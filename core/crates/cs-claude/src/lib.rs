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

/// Reasoning-effort levels the `claude` CLI accepts for `--effort`.
pub const EFFORT_LEVELS: [&str; 5] = ["low", "medium", "high", "xhigh", "max"];

/// Map a [`ModelTier`] to the CLI model identifier the `binary` expects.
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
#[allow(clippy::too_many_arguments)]
pub fn build_args(
    model: ModelTier,
    prompt: &str,
    append_system_prompt: Option<&str>,
    mcp_config: Option<&str>,
    allowed_tools: &[String],
    effort: Option<&str>,
    resume: Option<&str>,
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
        // Stream assistant text token-by-token (content_block_delta) so the UI
        // can render the reply incrementally, like the CLI.
        "--include-partial-messages".to_string(),
    ];
    // Continue a previous conversation with full context.
    if let Some(id) = resume {
        if !id.trim().is_empty() {
            args.push("--resume".to_string());
            args.push(id.to_string());
        }
    }
    // Reasoning effort for this run (`--effort`), if one of the levels the CLI
    // accepts. Anything else is ignored so we never pass garbage.
    if let Some(level) = effort {
        if EFFORT_LEVELS.contains(&level) {
            args.push("--effort".to_string());
            args.push(level.to_string());
        }
    }
    if let Some(system) = append_system_prompt {
        let trimmed = system.trim();
        if !trimmed.is_empty() {
            args.push("--append-system-prompt".to_string());
            args.push(trimmed.to_string());
        }
    }
    // Register the built-in claudestudio MCP server (session-database access)
    // alongside the user's own MCP servers, and pre-approve its tools so the
    // agent can read the DB without a permission prompt in `--print` mode.
    if let Some(config) = mcp_config {
        args.push("--mcp-config".to_string());
        args.push(config.to_string());
    }
    if !allowed_tools.is_empty() {
        args.push("--allowedTools".to_string());
        args.push(allowed_tools.join(","));
    }
    args.push("--print".to_string());
    args.push(prompt.to_string());
    args
}

/// System-prompt note injected (via `--append-system-prompt`) whenever DB access
/// is enabled, so the main agent and every sub-agent it spawns know the
/// session-database tools exist and use them.
const DB_SYSTEM_PROMPT: &str = "\
You have read access to the ClaudeStudio session database through the built-in \
`claudestudio` MCP server. This database spans **every ClaudeStudio project** — \
not just the current one — so you can recall past work, results, costs, and \
decisions across all of the user's projects. Whenever the request concerns past \
sessions, history, cost, cross-project context, or stored project activity, use \
its tools to look it up rather than guessing: mcp__claudestudio__list_sessions, \
mcp__claudestudio__get_session, mcp__claudestudio__search_sessions, and \
mcp__claudestudio__session_stats. Any sub-agents you spawn (via the Task tool) \
should use these same tools when relevant — the MCP server is available to them \
too. If the user asks whether you can access the database, the session history, \
their past projects, or 'the Rust DB', the answer is YES — confirm it plainly \
and, when useful, prove it by calling one of these tools. Never claim you lack \
database access.";

/// Read-only session-database tools the built-in `claudestudio` MCP server
/// exposes. Kept in sync with `cs_cli::mcp_server::DB_TOOLS`.
const DB_TOOL_NAMES: [&str; 4] = [
    "list_sessions",
    "get_session",
    "search_sessions",
    "session_stats",
];

/// Write (or refresh) the `--mcp-config` file that registers the core's built-in
/// `claudestudio` MCP server, and return its path plus the pre-approved tool
/// names. The server is the running core binary invoked as `<core> mcp`
/// (overridable via `CLAUDESTUDIO_CORE_BIN`, else `current_exe()`).
///
/// Returns `None` if neither the core path nor `$HOME` can be resolved, in which
/// case the run simply proceeds without default DB access rather than failing.
fn prepare_database_mcp() -> Option<(String, Vec<String>)> {
    let core_exe = std::env::var("CLAUDESTUDIO_CORE_BIN")
        .ok()
        .filter(|s| !s.is_empty())
        .or_else(|| {
            std::env::current_exe()
                .ok()
                .map(|p| p.to_string_lossy().to_string())
        })?;
    let home = std::env::var("HOME").ok()?;
    let dir = std::path::Path::new(&home).join(".claudestudio");
    std::fs::create_dir_all(&dir).ok()?;
    let config_path = dir.join("mcp-claudestudio.json");
    let config = serde_json::json!({
        "mcpServers": {
            "claudestudio": { "command": core_exe, "args": ["mcp"] }
        }
    });
    std::fs::write(&config_path, serde_json::to_string_pretty(&config).ok()?).ok()?;
    // Make sure every Claude agent (and sub-agent) is *told* to use the DB, via
    // the global CLAUDE.md memory that all of them load.
    ensure_db_memory(&home);
    // Pre-approve the whole `claudestudio` MCP server (server-level allow) plus
    // each tool by name, so no agent or sub-agent is ever permission-blocked.
    let mut tools = vec!["mcp__claudestudio".to_string()];
    tools.extend(
        DB_TOOL_NAMES
            .iter()
            .map(|t| format!("mcp__claudestudio__{t}")),
    );
    Some((config_path.to_string_lossy().to_string(), tools))
}

/// Markers around the ClaudeStudio-managed block in `~/.claude/CLAUDE.md`.
const DB_MEMORY_START: &str = "<!-- ClaudeStudio:db-access START -->";
const DB_MEMORY_END: &str = "<!-- ClaudeStudio:db-access END -->";

/// Ensure the global user memory (`~/.claude/CLAUDE.md`, loaded by *every* Claude
/// agent and sub-agent) contains a managed block instructing them to use the
/// ClaudeStudio session-database MCP tools. Idempotent and non-destructive: it
/// only touches its own marked block, leaving the rest of the file untouched.
fn ensure_db_memory(home: &str) {
    let dir = std::path::Path::new(home).join(".claude");
    if std::fs::create_dir_all(&dir).is_err() {
        return;
    }
    let path = dir.join("CLAUDE.md");
    let block = format!(
        "{DB_MEMORY_START}\n## ClaudeStudio session database (always available)\n\n\
         You — and every sub-agent you spawn via the Task tool — have read access to \
         the ClaudeStudio session database through the `claudestudio` MCP server, which \
         spans ALL of the user's ClaudeStudio projects. Whenever a request touches past \
         sessions, history, cost, decisions, or cross-project context, use its tools \
         (mcp__claudestudio__list_sessions, mcp__claudestudio__get_session, \
         mcp__claudestudio__search_sessions, mcp__claudestudio__session_stats) instead of \
         guessing. If asked whether you can access the database, the answer is yes.\n\
         {DB_MEMORY_END}"
    );
    let existing = std::fs::read_to_string(&path).unwrap_or_default();
    let updated = match (existing.find(DB_MEMORY_START), existing.find(DB_MEMORY_END)) {
        (Some(s), Some(e)) if e >= s => {
            let mut c = existing.clone();
            c.replace_range(s..e + DB_MEMORY_END.len(), &block);
            c
        }
        _ if existing.trim().is_empty() => format!("{block}\n"),
        _ => format!("{}\n\n{block}\n", existing.trim_end()),
    };
    if updated != existing {
        let _ = std::fs::write(&path, updated);
    }
}

/// A single parsed event from the `claude --output-format stream-json` stream.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub enum StreamEvent {
    /// Assistant produced some text (a complete message block).
    AssistantText(String),
    /// An incremental chunk of the assistant's text, streamed as it's produced
    /// (`--include-partial-messages`). The UI appends these to the current reply.
    AssistantDelta(String),
    /// The assistant requested a tool call.
    ToolUse {
        /// The tool-call id (matches a later `ToolResult.id`), if present.
        id: Option<String>,
        /// The name of the tool.
        name: String,
        /// The tool's input arguments.
        input: serde_json::Value,
    },
    /// A tool returned a result.
    ToolResult {
        /// The id of the `ToolUse` this result completes, if present.
        id: Option<String>,
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
    /// The run was stopped by the user (the process was killed).
    Stopped,
    /// The `claude` CLI's own session id for this run — used to continue the
    /// conversation later via `--resume`.
    ClaudeSessionId(String),
    /// Any line we could parse as JSON but did not recognize.
    Other(serde_json::Value),
}

/// Tolerantly parse one line of `stream-json` output into zero or more
/// [`StreamEvent`]s.
///
/// A single `assistant` line can carry several content blocks at once — plain
/// text **and** one or more `tool_use` calls (the Bash command, file edit, or
/// sub-agent launch). A `user` line carries the matching `tool_result`s. We emit
/// each block as its own event so the live transcript shows *everything* the
/// agent does, not just its prose. Unknown or partially-formed lines never
/// error: they collapse to [`StreamEvent::Other`], keeping the stream resilient
/// to CLI schema drift.
pub fn parse_stream_line(line: &str) -> Vec<StreamEvent> {
    let trimmed = line.trim();
    if trimmed.is_empty() {
        return Vec::new();
    }
    let value: serde_json::Value = match serde_json::from_str(trimmed) {
        Ok(v) => v,
        Err(_) => {
            return vec![StreamEvent::Other(serde_json::Value::String(
                trimmed.to_string(),
            ))]
        }
    };

    match value.get("type").and_then(|t| t.as_str()) {
        // Token-level streaming: a text delta of the assistant's current reply.
        // Only the top-level agent's deltas (no parent_tool_use_id) are streamed
        // into the main transcript; sub-agent internals are shown separately.
        Some("stream_event") => {
            let is_subagent = value
                .get("parent_tool_use_id")
                .map(|p| !p.is_null())
                .unwrap_or(false);
            if is_subagent {
                return Vec::new();
            }
            let delta_text = value
                .get("event")
                .filter(|e| e.get("type").and_then(|t| t.as_str()) == Some("content_block_delta"))
                .and_then(|e| e.get("delta"))
                .filter(|d| d.get("type").and_then(|t| t.as_str()) == Some("text_delta"))
                .and_then(|d| d.get("text"))
                .and_then(|t| t.as_str());
            match delta_text {
                Some(t) if !t.is_empty() => vec![StreamEvent::AssistantDelta(t.to_string())],
                _ => Vec::new(),
            }
        }
        // The `system`/`init` line carries the CLI's session id (for --resume).
        Some("system") => value
            .get("session_id")
            .and_then(|s| s.as_str())
            .map(|sid| vec![StreamEvent::ClaudeSessionId(sid.to_string())])
            .unwrap_or_else(|| vec![StreamEvent::Other(value)]),
        // Real CLI shape: blocks live under message.content[]. Assistant lines
        // hold text + tool_use; user lines hold tool_result.
        Some("assistant") | Some("user") => parse_message_blocks(&value),
        Some("text") => value
            .get("text")
            .and_then(|t| t.as_str())
            .map(|s| vec![StreamEvent::AssistantText(s.to_string())])
            .unwrap_or_else(|| vec![StreamEvent::Other(value)]),
        Some("tool_use") => vec![tool_use_event(&value)],
        Some("tool_result") => vec![tool_result_event(&value)],
        Some("result") => vec![StreamEvent::Result {
            cost_usd: value
                .get("cost_usd")
                .or_else(|| value.get("total_cost_usd"))
                .and_then(|c| c.as_f64())
                .unwrap_or(0.0),
            is_error: value
                .get("is_error")
                .and_then(|e| e.as_bool())
                .unwrap_or(false),
        }],
        _ => vec![StreamEvent::Other(value)],
    }
}

/// Expand a `message.content[]` array into one event per block (text, tool_use,
/// tool_result). Falls back to a flat `text` field for older shapes.
fn parse_message_blocks(value: &serde_json::Value) -> Vec<StreamEvent> {
    let Some(content) = value
        .get("message")
        .and_then(|m| m.get("content"))
        .and_then(|c| c.as_array())
    else {
        if let Some(t) = value.get("text").and_then(|t| t.as_str()) {
            if !t.trim().is_empty() {
                return vec![StreamEvent::AssistantText(t.to_string())];
            }
        }
        return Vec::new();
    };

    let mut out = Vec::new();
    for block in content {
        match block.get("type").and_then(|t| t.as_str()) {
            Some("text") => {
                if let Some(t) = block.get("text").and_then(|t| t.as_str()) {
                    if !t.trim().is_empty() {
                        out.push(StreamEvent::AssistantText(t.to_string()));
                    }
                }
            }
            Some("tool_use") => out.push(tool_use_event(block)),
            Some("tool_result") => out.push(tool_result_event(block)),
            _ => {}
        }
    }
    out
}

fn tool_use_event(block: &serde_json::Value) -> StreamEvent {
    StreamEvent::ToolUse {
        id: block.get("id").and_then(|v| v.as_str()).map(str::to_string),
        name: block
            .get("name")
            .and_then(|n| n.as_str())
            .unwrap_or_default()
            .to_string(),
        input: block
            .get("input")
            .cloned()
            .unwrap_or(serde_json::Value::Null),
    }
}

/// A `tool_result`'s `content` may be a plain string or an array of `{type:text}`
/// blocks; normalize both to a single string.
fn tool_result_event(block: &serde_json::Value) -> StreamEvent {
    let content = match block.get("content") {
        Some(serde_json::Value::String(s)) => s.clone(),
        Some(serde_json::Value::Array(arr)) => {
            let mut buf = String::new();
            for b in arr {
                if let Some(t) = b.get("text").and_then(|t| t.as_str()) {
                    buf.push_str(t);
                }
            }
            buf
        }
        Some(other) => other.to_string(),
        None => String::new(),
    };
    StreamEvent::ToolResult {
        id: block
            .get("tool_use_id")
            .and_then(|v| v.as_str())
            .map(str::to_string),
        content,
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
    /// When true (the default), the run is given default access to the core's
    /// session database via the built-in `claudestudio` MCP server.
    pub database_access: bool,
    /// Reasoning effort (`--effort`): one of [`EFFORT_LEVELS`], or `None` for the
    /// CLI default.
    pub effort: Option<String>,
    /// The `claude` session id to continue via `--resume`, or `None` for a new
    /// conversation.
    pub resume: Option<String>,
}

impl ClaudeSession {
    /// Create a session that runs the default `claude` binary at the given
    /// model tier. Database access is on by default.
    pub fn new(model: ModelTier) -> Self {
        Self {
            model,
            binary: "claude".to_string(),
            cwd: None,
            append_system_prompt: None,
            database_access: true,
            effort: None,
            resume: None,
        }
    }

    /// Continue a previous conversation by its `claude` session id.
    pub fn with_resume(mut self, id: impl Into<String>) -> Self {
        let id = id.into();
        if !id.trim().is_empty() {
            self.resume = Some(id);
        }
        self
    }

    /// Set the reasoning effort for this run (ignored unless it's a valid level).
    pub fn with_effort(mut self, effort: impl Into<String>) -> Self {
        let effort = effort.into();
        if EFFORT_LEVELS.contains(&effort.as_str()) {
            self.effort = Some(effort);
        }
        self
    }

    /// Disable the built-in session-database MCP server for this run.
    pub fn without_database_access(mut self) -> Self {
        self.database_access = false;
        self
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

    /// Run a prompt, returning an asynchronous stream of [`StreamEvent`]s parsed
    /// from the CLI's stdout. When `cancel` resolves, the `claude` process is
    /// killed and the stream ends with [`StreamEvent::Stopped`]. Pass
    /// `std::future::pending()` for a run that can't be cancelled.
    pub async fn run(
        &self,
        prompt: &str,
        cancel: impl std::future::Future<Output = ()> + Send + 'static,
    ) -> Result<impl Stream<Item = StreamEvent> + Send + Unpin> {
        // Give the run default access to the session database via the built-in
        // claudestudio MCP server (registered + pre-approved), unless disabled.
        let db_mcp = if self.database_access {
            prepare_database_mcp()
        } else {
            None
        };
        let (mcp_config, allowed_tools) = match &db_mcp {
            Some((path, tools)) => (Some(path.as_str()), tools.as_slice()),
            None => (None, [].as_slice()),
        };
        // When DB access is on, prepend a system-prompt note so the main agent
        // *and any sub-agents it spawns* know the session-database tools exist
        // and should use them. Combined with any agent persona.
        let system_prompt: Option<String> =
            match (db_mcp.is_some(), self.append_system_prompt.as_deref()) {
                (true, Some(persona)) => Some(format!("{DB_SYSTEM_PROMPT}\n\n{persona}")),
                (true, None) => Some(DB_SYSTEM_PROMPT.to_string()),
                (false, persona) => persona.map(str::to_string),
            };
        let args = build_args(
            self.model,
            prompt,
            system_prompt.as_deref(),
            mcp_config,
            allowed_tools,
            self.effort.as_deref(),
            self.resume.as_deref(),
        );
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

        // A dedicated task owns the child so it can be killed *concurrently* with
        // stdout streaming: on `cancel` it kills the process (which closes stdout
        // and ends the line stream); otherwise it waits for normal exit. It
        // reports the outcome to the tail via a oneshot.
        #[derive(PartialEq)]
        enum Outcome {
            Ok,
            Stopped,
            Failed,
        }
        let (tx, rx) = futures::channel::oneshot::channel::<Outcome>();
        tokio::spawn(async move {
            let outcome = tokio::select! {
                _ = cancel => {
                    let _ = child.start_kill();
                    let _ = child.wait().await;
                    Outcome::Stopped
                }
                status = child.wait() => match status {
                    Ok(s) if s.success() => Outcome::Ok,
                    _ => Outcome::Failed,
                },
            };
            let _ = tx.send(outcome);
        });

        // After stdout closes, surface the terminal event: nothing on success,
        // `Stopped` when cancelled, or `Failure(stderr)` on a real error.
        let tail = futures::stream::once(async move {
            match rx.await.unwrap_or(Outcome::Failed) {
                Outcome::Ok => None,
                Outcome::Stopped => Some(StreamEvent::Stopped),
                Outcome::Failed => {
                    let mut buf = String::new();
                    if let Some(mut e) = stderr {
                        let _ = e.read_to_string(&mut buf).await;
                    }
                    let message = buf.trim();
                    Some(StreamEvent::Failure(if message.is_empty() {
                        "the claude CLI exited with an error".to_string()
                    } else {
                        message.to_string()
                    }))
                }
            }
        })
        .filter_map(|event| async move { event });

        Ok(Box::pin(
            lines
                .flat_map(|line| futures::stream::iter(parse_stream_line(&line)))
                .chain(tail),
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
        let args = build_args(ModelTier::Sonnet, "hello", None, None, &[], None, None);
        assert!(args.contains(&"stream-json".to_string()));
        // --print + stream-json requires --verbose, or the CLI errors out.
        assert!(args.contains(&"--verbose".to_string()));
        assert!(args.contains(&"sonnet".to_string()));
        assert!(args.contains(&"hello".to_string()));
        assert!(!args.contains(&"--append-system-prompt".to_string()));
        // No DB access requested → no MCP/allowlist flags.
        assert!(!args.contains(&"--mcp-config".to_string()));
        assert!(!args.contains(&"--allowedTools".to_string()));
        assert!(!args.contains(&"--effort".to_string()));
    }

    #[test]
    fn build_args_passes_valid_effort_and_ignores_invalid() {
        let args = build_args(ModelTier::Opus, "go", None, None, &[], Some("high"), None);
        let i = args
            .iter()
            .position(|a| a == "--effort")
            .expect("--effort present");
        assert_eq!(args[i + 1], "high");
        // Unknown levels are dropped rather than passed through.
        let bad = build_args(ModelTier::Opus, "go", None, None, &[], Some("turbo"), None);
        assert!(!bad.contains(&"--effort".to_string()));
    }

    #[test]
    fn build_args_resumes_when_id_given() {
        let args = build_args(
            ModelTier::Sonnet,
            "go on",
            None,
            None,
            &[],
            None,
            Some("sess-123"),
        );
        let i = args
            .iter()
            .position(|a| a == "--resume")
            .expect("--resume present");
        assert_eq!(args[i + 1], "sess-123");
        let none = build_args(
            ModelTier::Sonnet,
            "go on",
            None,
            None,
            &[],
            None,
            Some("  "),
        );
        assert!(!none.contains(&"--resume".to_string()));
    }

    #[test]
    fn parse_stream_event_yields_text_delta() {
        let line = r#"{"type":"stream_event","parent_tool_use_id":null,"event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"Hel"}}}"#;
        assert_eq!(
            parse_stream_line(line),
            vec![StreamEvent::AssistantDelta("Hel".to_string())]
        );
        // Sub-agent deltas (with a parent tool-use id) are not streamed to the main view.
        let sub = r#"{"type":"stream_event","parent_tool_use_id":"toolu_1","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"x"}}}"#;
        assert!(parse_stream_line(sub).is_empty());
    }

    #[test]
    fn build_args_includes_partial_messages() {
        let args = build_args(ModelTier::Sonnet, "hi", None, None, &[], None, None);
        assert!(args.contains(&"--include-partial-messages".to_string()));
    }

    #[test]
    fn parse_system_init_yields_claude_session_id() {
        let line = r#"{"type":"system","subtype":"init","session_id":"abc-123","model":"x"}"#;
        assert_eq!(
            parse_stream_line(line),
            vec![StreamEvent::ClaudeSessionId("abc-123".to_string())]
        );
    }

    #[test]
    fn build_args_appends_system_prompt_when_present() {
        let args = build_args(
            ModelTier::Opus,
            "do it",
            Some("You are a careful reviewer."),
            None,
            &[],
            None,
            None,
        );
        let i = args
            .iter()
            .position(|a| a == "--append-system-prompt")
            .expect("flag present");
        assert_eq!(args[i + 1], "You are a careful reviewer.");
        // Blank/whitespace system prompts are omitted.
        let none = build_args(ModelTier::Opus, "do it", Some("   "), None, &[], None, None);
        assert!(!none.contains(&"--append-system-prompt".to_string()));
    }

    #[test]
    fn build_args_registers_mcp_and_allowlist_when_given() {
        let tools = vec![
            "mcp__claudestudio__list_sessions".to_string(),
            "mcp__claudestudio__get_session".to_string(),
        ];
        let args = build_args(
            ModelTier::Sonnet,
            "show my sessions",
            None,
            Some("/home/u/.claudestudio/mcp-claudestudio.json"),
            &tools,
            None,
            None,
        );
        let ci = args
            .iter()
            .position(|a| a == "--mcp-config")
            .expect("--mcp-config present");
        assert_eq!(args[ci + 1], "/home/u/.claudestudio/mcp-claudestudio.json");
        let ai = args
            .iter()
            .position(|a| a == "--allowedTools")
            .expect("--allowedTools present");
        assert_eq!(
            args[ai + 1],
            "mcp__claudestudio__list_sessions,mcp__claudestudio__get_session"
        );
    }

    #[test]
    fn parse_assistant_text_line() {
        let line = r#"{"type":"text","text":"Hello, world"}"#;
        assert_eq!(
            parse_stream_line(line),
            vec![StreamEvent::AssistantText("Hello, world".to_string())]
        );
    }

    #[test]
    fn parse_nested_assistant_message() {
        let line = r#"{"type":"assistant","message":{"content":[{"type":"text","text":"hi"}]}}"#;
        assert_eq!(
            parse_stream_line(line),
            vec![StreamEvent::AssistantText("hi".to_string())]
        );
    }

    #[test]
    fn parse_assistant_message_emits_text_and_tool_use() {
        // A real assistant line carries both prose and a tool call; both must surface.
        let line = r#"{"type":"assistant","message":{"content":[
            {"type":"text","text":"Let me check."},
            {"type":"tool_use","name":"Bash","input":{"command":"ls -la"}}
        ]}}"#;
        let events = parse_stream_line(line);
        assert_eq!(events.len(), 2);
        assert_eq!(
            events[0],
            StreamEvent::AssistantText("Let me check.".to_string())
        );
        match &events[1] {
            StreamEvent::ToolUse { name, input, .. } => {
                assert_eq!(name, "Bash");
                assert_eq!(input["command"], "ls -la");
            }
            other => panic!("expected ToolUse, got {other:?}"),
        }
    }

    #[test]
    fn parse_user_message_emits_tool_result() {
        let line = r#"{"type":"user","message":{"content":[
            {"type":"tool_result","tool_use_id":"toolu_9","content":[{"type":"text","text":"file1\nfile2"}]}
        ]}}"#;
        let events = parse_stream_line(line);
        assert_eq!(
            events,
            vec![StreamEvent::ToolResult {
                id: Some("toolu_9".to_string()),
                content: "file1\nfile2".to_string()
            }]
        );
    }

    #[test]
    fn parse_tool_use_carries_id_from_assistant_block() {
        let line = r#"{"type":"assistant","message":{"content":[
            {"type":"tool_use","id":"toolu_42","name":"Task","input":{"subagent_type":"Explore"}}
        ]}}"#;
        match parse_stream_line(line).as_slice() {
            [StreamEvent::ToolUse { id, name, .. }] => {
                assert_eq!(id.as_deref(), Some("toolu_42"));
                assert_eq!(name, "Task");
            }
            other => panic!("expected single ToolUse, got {other:?}"),
        }
    }

    #[test]
    fn parse_tool_use_line() {
        let line = r#"{"type":"tool_use","name":"Bash","input":{"command":"ls"}}"#;
        match parse_stream_line(line).as_slice() {
            [StreamEvent::ToolUse { name, input, .. }] => {
                assert_eq!(name, "Bash");
                assert_eq!(input["command"], "ls");
            }
            other => panic!("expected single ToolUse, got {other:?}"),
        }
    }

    #[test]
    fn parse_result_line_extracts_cost() {
        let line = r#"{"type":"result","cost_usd":0.0123,"is_error":false}"#;
        assert_eq!(
            parse_stream_line(line),
            vec![StreamEvent::Result {
                cost_usd: 0.0123,
                is_error: false
            }]
        );
    }

    #[test]
    fn unknown_line_becomes_other() {
        assert!(matches!(
            parse_stream_line(r#"{"type":"mystery"}"#).as_slice(),
            [StreamEvent::Other(_)]
        ));
        assert!(matches!(
            parse_stream_line("not json at all").as_slice(),
            [StreamEvent::Other(_)]
        ));
        assert!(parse_stream_line("").is_empty());
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
        let mut stream = session
            .run("test prompt", std::future::pending())
            .await
            .expect("spawn fake claude");

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
