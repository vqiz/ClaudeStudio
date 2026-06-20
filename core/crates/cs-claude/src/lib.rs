#![forbid(unsafe_code)]
//! # cs-claude
//!
//! Claude Code CLI process manager for ClaudeStudio.
//!
//! This crate owns the lifecycle of the `claude` binary. It:
//!
//! - builds the argument vector for a run (model, `--output-format
//!   stream-json`, prompt) via the pure, unit-testable [`build_args`];
//! - spawns the process with `tokio::process` and streams its stdout
//!   line-by-line through [`ClaudeSession::run`];
//! - parses each line of the `stream-json` output into a [`StreamEvent`] with a
//!   tolerant parser ([`parse_stream_line`]) that maps anything unrecognized to
//!   [`StreamEvent::Other`]; and
//! - chooses an appropriate [`cs_types::ModelTier`] for a [`TaskKind`] via the
//!   [`ModelRouter`], complete with a fallback chain.
//!
//! This crate never talks to the Anthropic API or the network directly and
//! never injects an API key: it only spawns the `claude` binary via
//! `tokio::process` (when [`ClaudeSession::run`] is called). A run therefore
//! uses whatever the `claude` CLI is authenticated with — e.g. your Claude
//! Pro/Max **subscription** via `claude /login` — exactly like running `claude`
//! in a terminal yourself.

use std::path::PathBuf;
use std::process::Stdio;

use cs_types::ModelTier;
use futures::stream::{Stream, StreamExt};
use serde::{Deserialize, Serialize};
use thiserror::Error;
use tokio::io::{AsyncBufReadExt, AsyncReadExt, BufReader};
use tokio::process::Command;

/// Errors produced by the Claude process manager.
#[derive(Debug, Error)]
pub enum Error {
    /// The `claude` process failed to spawn.
    #[error("failed to spawn claude process: {0}")]
    Spawn(String),
    /// The spawned process had no stdout pipe.
    #[error("claude process produced no stdout handle")]
    NoStdout,
    /// An I/O error occurred while reading the stream.
    #[error("io error: {0}")]
    Io(String),
}

/// Convenient result alias for this crate.
pub type Result<T> = std::result::Result<T, Error>;

/// Map a [`ModelTier`] to the CLI model identifier the `claude` binary expects.
pub fn model_flag(tier: ModelTier) -> &'static str {
    match tier {
        ModelTier::Haiku => "haiku",
        ModelTier::Sonnet => "sonnet",
        ModelTier::Opus => "opus",
    }
}

/// Build the argument vector for a `claude` invocation.
///
/// This is a pure function so it can be tested without spawning anything. The
/// produced arguments always request streaming JSON output. When
/// `append_system_prompt` is set (e.g. running a saved agent), it is passed
/// through `--append-system-prompt` so the agent's persona augments — rather
/// than replaces — Claude Code's own system prompt.
pub fn build_args(
    model: ModelTier,
    prompt: &str,
    append_system_prompt: Option<&str>,
) -> Vec<String> {
    let mut args = vec![
        "--model".to_string(),
        model_flag(model).to_string(),
        "--output-format".to_string(),
        "stream-json".to_string(),
        // `--print` + `stream-json` is rejected by the CLI without `--verbose`
        // ("requires --verbose"); without it the run errors out and emits no
        // stdout, so the UI would show an empty transcript.
        "--verbose".to_string(),
    ];
    if let Some(system) = append_system_prompt {
        let trimmed = system.trim();
        if !trimmed.is_empty() {
            args.push("--append-system-prompt".to_string());
            args.push(trimmed.to_string());
        }
    }
    args.push("--print".to_string());
    args.push(prompt.to_string());
    args
}

/// A single parsed event from the `claude --output-format stream-json` stream.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub enum StreamEvent {
    /// Assistant produced some text.
    AssistantText(String),
    /// The assistant requested a tool call.
    ToolUse {
        /// The name of the tool.
        name: String,
        /// The tool's input arguments.
        input: serde_json::Value,
    },
    /// A tool returned a result.
    ToolResult {
        /// The textual result content, if any.
        content: String,
    },
    /// The terminal result line, including cost accounting.
    Result {
        /// Total cost of the run in USD.
        cost_usd: f64,
        /// Whether the run ended in an error.
        is_error: bool,
    },
    /// The process failed (non-zero exit). Carries the captured stderr so the UI
    /// can show *why* a run produced no output (e.g. a CLI usage error).
    Failure(String),
    /// Any line we could parse as JSON but did not recognize.
    Other(serde_json::Value),
}

/// Tolerantly parse one line of `stream-json` output into a [`StreamEvent`].
///
/// Unknown or partially-formed lines never error: they collapse to
/// [`StreamEvent::Other`], keeping the stream resilient to CLI schema drift.
pub fn parse_stream_line(line: &str) -> StreamEvent {
    let trimmed = line.trim();
    if trimmed.is_empty() {
        return StreamEvent::Other(serde_json::Value::Null);
    }
    let value: serde_json::Value = match serde_json::from_str(trimmed) {
        Ok(v) => v,
        Err(_) => return StreamEvent::Other(serde_json::Value::String(trimmed.to_string())),
    };

    match value.get("type").and_then(|t| t.as_str()) {
        Some("assistant") => {
            // Newer CLI nests text under message.content[].text.
            if let Some(text) = extract_assistant_text(&value) {
                return StreamEvent::AssistantText(text);
            }
            StreamEvent::Other(value)
        }
        Some("text") => value
            .get("text")
            .and_then(|t| t.as_str())
            .map(|s| StreamEvent::AssistantText(s.to_string()))
            .unwrap_or(StreamEvent::Other(value)),
        Some("tool_use") => StreamEvent::ToolUse {
            name: value
                .get("name")
                .and_then(|n| n.as_str())
                .unwrap_or_default()
                .to_string(),
            input: value
                .get("input")
                .cloned()
                .unwrap_or(serde_json::Value::Null),
        },
        Some("tool_result") => StreamEvent::ToolResult {
            content: value
                .get("content")
                .and_then(|c| c.as_str())
                .unwrap_or_default()
                .to_string(),
        },
        Some("result") => StreamEvent::Result {
            cost_usd: value
                .get("cost_usd")
                .or_else(|| value.get("total_cost_usd"))
                .and_then(|c| c.as_f64())
                .unwrap_or(0.0),
            is_error: value
                .get("is_error")
                .and_then(|e| e.as_bool())
                .unwrap_or(false),
        },
        _ => StreamEvent::Other(value),
    }
}

/// Pull assistant text out of either a flat `text` field or a nested
/// `message.content[].text` structure.
fn extract_assistant_text(value: &serde_json::Value) -> Option<String> {
    if let Some(t) = value.get("text").and_then(|t| t.as_str()) {
        return Some(t.to_string());
    }
    let content = value.get("message")?.get("content")?.as_array()?;
    let mut buf = String::new();
    for block in content {
        if let Some(t) = block.get("text").and_then(|t| t.as_str()) {
            buf.push_str(t);
        }
    }
    if buf.is_empty() {
        None
    } else {
        Some(buf)
    }
}

/// A configured session against the `claude` CLI.
#[derive(Clone, Debug)]
pub struct ClaudeSession {
    /// The model tier this session runs at.
    pub model: ModelTier,
    /// The binary to invoke (defaults to `claude`).
    pub binary: String,
    /// Working directory to run in (defaults to the parent process's cwd).
    pub cwd: Option<PathBuf>,
    /// Extra system prompt appended via `--append-system-prompt` (e.g. a saved
    /// agent's persona). `None` runs Claude with its default system prompt.
    pub append_system_prompt: Option<String>,
}

impl ClaudeSession {
    /// Create a session that runs the default `claude` binary at the given
    /// model tier.
    pub fn new(model: ModelTier) -> Self {
        Self {
            model,
            binary: "claude".to_string(),
            cwd: None,
            append_system_prompt: None,
        }
    }

    /// Append a system prompt (a saved agent's persona) to this session.
    pub fn with_system_prompt(mut self, system: impl Into<String>) -> Self {
        let system = system.into();
        if !system.trim().is_empty() {
            self.append_system_prompt = Some(system);
        }
        self
    }

    /// Override the binary path (useful for tests or custom installs).
    pub fn with_binary(mut self, binary: impl Into<String>) -> Self {
        self.binary = binary.into();
        self
    }

    /// Run the session in `dir` (typically the project root) instead of the
    /// parent process's working directory.
    pub fn with_cwd(mut self, dir: impl Into<PathBuf>) -> Self {
        self.cwd = Some(dir.into());
        self
    }

    /// Run a prompt, returning an asynchronous stream of [`StreamEvent`]s
    /// parsed from the CLI's stdout.
    pub async fn run(
        &self,
        prompt: &str,
    ) -> Result<impl Stream<Item = StreamEvent> + Send + Unpin> {
        let args = build_args(self.model, prompt, self.append_system_prompt.as_deref());
        let mut command = Command::new(&self.binary);
        command
            .args(&args)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .stdin(Stdio::null());
        if let Some(dir) = &self.cwd {
            command.current_dir(dir);
        }
        let mut child = command.spawn().map_err(|e| Error::Spawn(e.to_string()))?;

        let stdout = child.stdout.take().ok_or(Error::NoStdout)?;
        let stderr = child.stderr.take();
        let reader = BufReader::new(stdout);
        let lines = tokio_stream_lines(reader);

        // After stdout closes, wait for the child: if it failed, surface the
        // captured stderr as a terminal `Failure` event so the UI explains the
        // empty output instead of silently showing nothing.
        let tail = futures::stream::once(async move {
            let status = child.wait().await;
            let failed = status.map(|s| !s.success()).unwrap_or(true);
            if failed {
                let mut buf = String::new();
                if let Some(mut e) = stderr {
                    let _ = e.read_to_string(&mut buf).await;
                }
                let message = buf.trim();
                let message = if message.is_empty() {
                    "the claude CLI exited with an error".to_string()
                } else {
                    message.to_string()
                };
                Some(StreamEvent::Failure(message))
            } else {
                None
            }
        })
        .filter_map(|event| async move { event });

        Ok(Box::pin(
            lines.map(|line| parse_stream_line(&line)).chain(tail),
        ))
    }
}

/// Adapt an async buffered reader into a `Stream<Item = String>` of lines.
fn tokio_stream_lines<R>(reader: BufReader<R>) -> impl Stream<Item = String> + Send + Unpin
where
    R: tokio::io::AsyncRead + Unpin + Send + 'static,
{
    let lines = reader.lines();
    Box::pin(futures::stream::unfold(lines, |mut lines| async move {
        match lines.next_line().await {
            Ok(Some(line)) => Some((line, lines)),
            _ => None,
        }
    }))
}

/// The category of work a task represents, used by the [`ModelRouter`].
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum TaskKind {
    /// Documentation work — cheap, fast model.
    Docs,
    /// Feature implementation — balanced model.
    Feature,
    /// Architecture and deep reasoning — strongest model.
    Architecture,
}

/// Selects a [`ModelTier`] for a [`TaskKind`] and provides a fallback chain.
#[derive(Clone, Copy, Debug, Default)]
pub struct ModelRouter;

impl ModelRouter {
    /// Create a router.
    pub fn new() -> Self {
        Self
    }

    /// Primary routing table: pick the preferred tier for a task kind.
    pub fn route(&self, kind: TaskKind) -> ModelTier {
        match kind {
            TaskKind::Docs => ModelTier::Haiku,
            TaskKind::Feature => ModelTier::Sonnet,
            TaskKind::Architecture => ModelTier::Opus,
        }
    }

    /// The fallback chain for a task kind, most-preferred first. If the primary
    /// model is unavailable, callers walk this list.
    pub fn fallback_chain(&self, kind: TaskKind) -> Vec<ModelTier> {
        match kind {
            TaskKind::Docs => vec![ModelTier::Haiku, ModelTier::Sonnet, ModelTier::Opus],
            TaskKind::Feature => vec![ModelTier::Sonnet, ModelTier::Opus, ModelTier::Haiku],
            TaskKind::Architecture => vec![ModelTier::Opus, ModelTier::Sonnet],
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn build_args_requests_stream_json() {
        let args = build_args(ModelTier::Sonnet, "hello", None);
        assert!(args.contains(&"stream-json".to_string()));
        // --print + stream-json requires --verbose, or the CLI errors out.
        assert!(args.contains(&"--verbose".to_string()));
        assert!(args.contains(&"sonnet".to_string()));
        assert!(args.contains(&"hello".to_string()));
        assert!(!args.contains(&"--append-system-prompt".to_string()));
    }

    #[test]
    fn build_args_appends_system_prompt_when_present() {
        let args = build_args(
            ModelTier::Opus,
            "do it",
            Some("You are a careful reviewer."),
        );
        let i = args
            .iter()
            .position(|a| a == "--append-system-prompt")
            .expect("flag present");
        assert_eq!(args[i + 1], "You are a careful reviewer.");
        // Blank/whitespace system prompts are omitted.
        let none = build_args(ModelTier::Opus, "do it", Some("   "));
        assert!(!none.contains(&"--append-system-prompt".to_string()));
    }

    #[test]
    fn parse_assistant_text_line() {
        let line = r#"{"type":"text","text":"Hello, world"}"#;
        assert_eq!(
            parse_stream_line(line),
            StreamEvent::AssistantText("Hello, world".to_string())
        );
    }

    #[test]
    fn parse_nested_assistant_message() {
        let line = r#"{"type":"assistant","message":{"content":[{"type":"text","text":"hi"}]}}"#;
        assert_eq!(
            parse_stream_line(line),
            StreamEvent::AssistantText("hi".to_string())
        );
    }

    #[test]
    fn parse_tool_use_line() {
        let line = r#"{"type":"tool_use","name":"Bash","input":{"command":"ls"}}"#;
        match parse_stream_line(line) {
            StreamEvent::ToolUse { name, input } => {
                assert_eq!(name, "Bash");
                assert_eq!(input["command"], "ls");
            }
            other => panic!("expected ToolUse, got {other:?}"),
        }
    }

    #[test]
    fn parse_result_line_extracts_cost() {
        let line = r#"{"type":"result","cost_usd":0.0123,"is_error":false}"#;
        assert_eq!(
            parse_stream_line(line),
            StreamEvent::Result {
                cost_usd: 0.0123,
                is_error: false
            }
        );
    }

    #[test]
    fn unknown_line_becomes_other() {
        assert!(matches!(
            parse_stream_line(r#"{"type":"mystery"}"#),
            StreamEvent::Other(_)
        ));
        assert!(matches!(
            parse_stream_line("not json at all"),
            StreamEvent::Other(_)
        ));
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn run_streams_parsed_events_from_fake_binary_in_cwd() {
        use std::os::unix::fs::PermissionsExt;

        let dir = std::env::temp_dir().join(format!("cs-claude-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let fake = dir.join("fake-claude");
        std::fs::write(
            &fake,
            "#!/bin/sh\n\
             echo '{\"type\":\"text\",\"text\":\"hi there\"}'\n\
             echo '{\"type\":\"result\",\"cost_usd\":0.01,\"is_error\":false}'\n",
        )
        .unwrap();
        std::fs::set_permissions(&fake, std::fs::Permissions::from_mode(0o755)).unwrap();

        let session = ClaudeSession::new(ModelTier::Haiku)
            .with_binary(fake.to_string_lossy())
            .with_cwd(dir.clone());
        let mut stream = session.run("test prompt").await.expect("spawn fake claude");

        let mut events = Vec::new();
        while let Some(ev) = stream.next().await {
            events.push(ev);
        }
        assert!(events
            .iter()
            .any(|e| matches!(e, StreamEvent::AssistantText(t) if t == "hi there")));
        assert!(events
            .iter()
            .any(|e| matches!(e, StreamEvent::Result { .. })));

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn model_router_routing_table() {
        let r = ModelRouter::new();
        assert_eq!(r.route(TaskKind::Docs), ModelTier::Haiku);
        assert_eq!(r.route(TaskKind::Feature), ModelTier::Sonnet);
        assert_eq!(r.route(TaskKind::Architecture), ModelTier::Opus);
    }

    #[test]
    fn model_router_fallback_starts_with_primary() {
        let r = ModelRouter::new();
        for kind in [TaskKind::Docs, TaskKind::Feature, TaskKind::Architecture] {
            assert_eq!(r.fallback_chain(kind)[0], r.route(kind));
        }
    }
}
