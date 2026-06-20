//! The IPC method router.
//!
//! [`Router`] owns the shared application state — the loaded [`AppConfig`], the
//! [`SessionStore`], the [`EventBus`], and the filesystem locations of the state
//! and library directories — and dispatches each incoming [`IpcEnvelope`] request
//! to a handler keyed by its `method` string.
//!
//! Handlers return `Result<serde_json::Value, String>`; [`Router::dispatch`]
//! wraps a success into a response envelope and a failure into an error envelope
//! (`kind: error`, `{ code, message }`), so a bad request never tears down the
//! connection.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use cs_agentic_os::{EventBus, SystemEvent};
use cs_config::{AppConfig, ContextAssembler, LayerKind};
use cs_git::{GitBackend, SystemGit};
use cs_ipc::IpcEnvelope;
use cs_sessions::{NewEvent, NewMessage, NewSession, NewToolCall, SessionStore};
use serde_json::{json, Value};

/// What every handler returns: a JSON payload or a human-readable error message.
type HandlerResult = std::result::Result<Value, String>;

/// Shared, cloneable application state plus method dispatch.
#[derive(Clone)]
pub struct Router {
    inner: Arc<Inner>,
}

struct Inner {
    config: Mutex<AppConfig>,
    state_dir: PathBuf,
    library_dir: PathBuf,
    sessions: Mutex<SessionStore>,
    event_bus: EventBus,
}

impl Router {
    /// Build a router from the loaded application components.
    pub fn new(
        config: AppConfig,
        sessions: SessionStore,
        event_bus: EventBus,
        state_dir: PathBuf,
        library_dir: PathBuf,
    ) -> Self {
        Self {
            inner: Arc::new(Inner {
                config: Mutex::new(config),
                state_dir,
                library_dir,
                sessions: Mutex::new(sessions),
                event_bus,
            }),
        }
    }

    /// The shared event bus, so the connection layer can stream `SystemEvent`s
    /// to a subscribed front-end.
    pub fn event_bus(&self) -> &EventBus {
        &self.inner.event_bus
    }

    /// Dispatch a request envelope, returning the response envelope to send back.
    pub async fn dispatch(&self, request: &IpcEnvelope) -> IpcEnvelope {
        match self.handle(request).await {
            Ok(payload) => request.response_to(payload),
            Err(message) => cs_ipc::error_response(request, message),
        }
    }

    async fn handle(&self, request: &IpcEnvelope) -> HandlerResult {
        let p = &request.payload;
        match request.method.as_str() {
            "ping" => Ok(json!({ "pong": true })),

            // --- Configuration & context ---
            "config.get" => Ok(self.config_payload()),
            "config.set" => self.config_set(p),
            "context.budget" => Ok(self.budget_payload()),

            // --- Session archive ---
            "session.list" => self.session_list(p),
            "session.get" => self.session_get(p),
            "session.search" => self.session_search(p),
            "session.create" => self.session_create(p),
            "session.stats" => self.session_stats(),

            // --- Git ---
            "git.status" => self.git_status(p).await,
            "git.branch" => self.git_branch(p).await,
            "git.worktrees" => self.git_worktrees(p).await,
            "git.diff" => self.git_diff(p).await,
            "git.log" => self.git_log(p).await,

            // --- Libraries & integrations ---
            "tasks.list" => self.tasks_list(),
            "tasks.create" => self.library_create(p, "tasks"),
            "tasks.delete" => self.library_delete(p, "tasks", ".task.json"),
            "definitions.list" => self.definitions_list(),
            "definitions.create" => self.library_create(p, "definitions"),
            "definitions.delete" => self.library_delete(p, "definitions", ".def.md"),
            "skills.list" => self.skills_list(p),
            "mcp.list" => self.mcp_list(p),
            "mcp.upsert" => self.mcp_upsert(p),
            "mcp.remove" => self.mcp_remove(p),
            "hooks.list" => self.hooks_list(p),

            // --- Editable files (CLAUDE.md, AGENTS.md, …) ---
            "file.read" => self.file_read(p),
            "file.write" => self.file_write(p),

            other => Err(format!("unknown method: {other}")),
        }
    }

    // MARK: Configuration

    fn config_payload(&self) -> Value {
        let cfg = self.inner.config.lock().unwrap();
        config_to_json(&cfg)
    }

    fn config_set(&self, p: &Value) -> HandlerResult {
        let payload = {
            let mut cfg = self.inner.config.lock().unwrap();
            // Apply changes to a copy first, persist it, and only commit the copy
            // to the shared state once the write succeeds — so a save failure
            // never leaves the in-memory config diverging from settings.json.
            let mut next = cfg.clone();
            if let Some(v) = p.get("trust_mode").and_then(Value::as_str) {
                next.trust_mode = serde_json::from_value(json!(v))
                    .map_err(|_| format!("invalid trust_mode: {v}"))?;
            }
            if let Some(v) = p.get("default_model").and_then(Value::as_str) {
                next.default_model = serde_json::from_value(json!(v))
                    .map_err(|_| format!("invalid default_model: {v}"))?;
            }
            if let Some(v) = p.get("daily_budget_usd").and_then(Value::as_f64) {
                next.daily_budget_usd = v.max(0.0);
            }
            if let Some(v) = p.get("context_token_budget").and_then(Value::as_u64) {
                next.context_token_budget = v as usize;
            }
            next.save(&self.inner.state_dir)
                .map_err(|e| format!("failed to save settings: {e}"))?;
            *cfg = next;
            config_to_json(&cfg)
        };
        // Best-effort: notify any event subscribers that config changed.
        let _ = self.inner.event_bus.publish(SystemEvent::TaskOneClick);
        Ok(payload)
    }

    fn budget_payload(&self) -> Value {
        let total = self.inner.config.lock().unwrap().context_token_budget;
        let budget = ContextAssembler::new(total)
            .with_layer(LayerKind::GlobalClaudeMd, 1_200)
            .with_layer(LayerKind::CrossProjectMemory, 3_000)
            .with_layer(LayerKind::ProjectClaudeMd, 2_400)
            .with_layer(LayerKind::VectorRetrieval, 6_000)
            .with_layer(LayerKind::ActiveDefinitions, 4_000)
            .with_layer(LayerKind::WorktreeOverride, 800)
            .assemble();

        let layers: Vec<Value> = budget
            .layers
            .iter()
            .map(|l| {
                json!({
                    "layer": l.kind.label(),
                    "requested_tokens": l.requested_tokens,
                    "granted_tokens": l.granted_tokens,
                    "truncated": l.was_truncated(),
                })
            })
            .collect();

        json!({
            "total_budget": budget.total_budget,
            "granted_total": budget.granted_total(),
            "remaining": budget.remaining(),
            "layers": layers,
        })
    }

    // MARK: Sessions

    fn session_list(&self, p: &Value) -> HandlerResult {
        let limit = p.get("limit").and_then(Value::as_i64).unwrap_or(100);
        let offset = p.get("offset").and_then(Value::as_i64).unwrap_or(0);
        let store = self.inner.sessions.lock().unwrap();
        let sessions = store
            .list_sessions(limit, offset)
            .map_err(|e| e.to_string())?;
        Ok(json!({ "sessions": sessions }))
    }

    fn session_get(&self, p: &Value) -> HandlerResult {
        let id = p.get("id").and_then(Value::as_str).ok_or("missing 'id'")?;
        let store = self.inner.sessions.lock().unwrap();
        let session = store.get_session(id).map_err(|e| e.to_string())?;
        Ok(serde_json::to_value(session).unwrap_or(Value::Null))
    }

    fn session_search(&self, p: &Value) -> HandlerResult {
        let query = p
            .get("query")
            .and_then(Value::as_str)
            .ok_or("missing 'query'")?;
        let store = self.inner.sessions.lock().unwrap();
        let hits = store.full_text_search(query).map_err(|e| e.to_string())?;
        Ok(json!({ "hits": hits }))
    }

    fn session_create(&self, p: &Value) -> HandlerResult {
        let title = p
            .get("title")
            .and_then(Value::as_str)
            .ok_or("missing 'title'")?;
        let cwd = p
            .get("cwd")
            .and_then(Value::as_str)
            .ok_or("missing 'cwd'")?;
        let mut ns = NewSession::new(title, cwd);
        ns.branch = p.get("branch").and_then(Value::as_str).map(str::to_string);
        ns.model = p.get("model").and_then(Value::as_str).map(str::to_string);
        let store = self.inner.sessions.lock().unwrap();
        let id = store.insert_session(&ns).map_err(|e| e.to_string())?;
        let _ = self.inner.event_bus.publish(SystemEvent::TaskOneClick);
        Ok(json!({ "id": id }))
    }

    fn session_stats(&self) -> HandlerResult {
        let store = self.inner.sessions.lock().unwrap();
        let stats = store.stats().map_err(|e| e.to_string())?;
        Ok(serde_json::to_value(stats).unwrap_or(Value::Null))
    }

    // MARK: Editable files

    /// Read a UTF-8 text file. A missing file returns `exists: false` with empty
    /// content (so the UI can create it on first save). Files over 4 MiB are
    /// rejected to keep the editor responsive.
    fn file_read(&self, p: &Value) -> HandlerResult {
        const MAX_BYTES: u64 = 4 * 1024 * 1024;
        let path = p
            .get("path")
            .and_then(Value::as_str)
            .ok_or("missing 'path'")?;
        // Reject by size *before* reading, so a multi-GB path can't force the
        // whole file into memory before the cap is enforced. Symlinks/specials
        // that report a small (or zero) size fall through to read_to_string,
        // whose own buffering is then the only exposure.
        match std::fs::metadata(path) {
            Ok(meta) if meta.is_file() && meta.len() > MAX_BYTES => {
                return Err("file too large to edit".to_string());
            }
            _ => {}
        }
        match std::fs::read_to_string(path) {
            Ok(content) => {
                if content.len() as u64 > MAX_BYTES {
                    return Err("file too large to edit".to_string());
                }
                Ok(json!({ "path": path, "content": content, "exists": true }))
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                Ok(json!({ "path": path, "content": "", "exists": false }))
            }
            Err(e) => Err(e.to_string()),
        }
    }

    /// Write a UTF-8 text file, creating parent directories as needed.
    fn file_write(&self, p: &Value) -> HandlerResult {
        let path = p
            .get("path")
            .and_then(Value::as_str)
            .ok_or("missing 'path'")?;
        let content = p
            .get("content")
            .and_then(Value::as_str)
            .ok_or("missing 'content'")?;
        if let Some(parent) = std::path::Path::new(path).parent() {
            std::fs::create_dir_all(parent).ok();
        }
        std::fs::write(path, content).map_err(|e| e.to_string())?;
        Ok(json!({ "path": path, "ok": true, "bytes": content.len() }))
    }

    // MARK: Live Claude session recording (called by the connection forwarder)

    /// Insert a session record for a live run and return its id (empty on error).
    pub fn create_run_session(&self, title: &str, cwd: &str, model: &str) -> String {
        let mut ns = NewSession::new(title, cwd);
        ns.model = Some(model.to_string());
        let store = self.inner.sessions.lock().unwrap();
        store.insert_session(&ns).unwrap_or_default()
    }

    /// Append a transcript message (best-effort).
    pub fn record_message(&self, session_id: &str, role: &str, content: &str) {
        let store = self.inner.sessions.lock().unwrap();
        let _ = store.append_message(&NewMessage::new(session_id, role, content));
    }

    /// Append a tool-call record (best-effort).
    pub fn record_tool_call(&self, session_id: &str, tool: &str, input: Value) {
        let store = self.inner.sessions.lock().unwrap();
        let _ = store.append_tool_call(&NewToolCall::new(session_id, tool, input));
    }

    /// Append a lifecycle event (best-effort).
    pub fn record_run_event(&self, session_id: &str, kind: &str) {
        let store = self.inner.sessions.lock().unwrap();
        let _ = store.append_event(&NewEvent::new(session_id, kind));
    }

    // MARK: Git (operates on the project directory named in `cwd`)

    fn git_for(p: &Value) -> std::result::Result<SystemGit, String> {
        let cwd = p
            .get("cwd")
            .and_then(Value::as_str)
            .ok_or("missing 'cwd'")?;
        Ok(SystemGit::new(cwd))
    }

    async fn git_status(&self, p: &Value) -> HandlerResult {
        let entries = Self::git_for(p)?
            .status()
            .await
            .map_err(|e| e.to_string())?;
        Ok(json!({ "entries": entries }))
    }

    async fn git_branch(&self, p: &Value) -> HandlerResult {
        let branch = Self::git_for(p)?
            .current_branch()
            .await
            .map_err(|e| e.to_string())?;
        Ok(json!({ "branch": branch }))
    }

    async fn git_worktrees(&self, p: &Value) -> HandlerResult {
        let worktrees = Self::git_for(p)?
            .list_worktrees()
            .await
            .map_err(|e| e.to_string())?;
        Ok(json!({ "worktrees": worktrees }))
    }

    async fn git_diff(&self, p: &Value) -> HandlerResult {
        let staged = p.get("staged").and_then(Value::as_bool).unwrap_or(false);
        let diff = Self::git_for(p)?
            .diff(staged)
            .await
            .map_err(|e| e.to_string())?;
        Ok(json!({ "diff": diff, "staged": staged }))
    }

    async fn git_log(&self, p: &Value) -> HandlerResult {
        let limit = p.get("limit").and_then(Value::as_u64).unwrap_or(20) as u32;
        let commits = Self::git_for(p)?
            .log(limit)
            .await
            .map_err(|e| e.to_string())?;
        Ok(json!({ "commits": commits }))
    }

    // MARK: Task & definition libraries (filesystem-backed)

    fn tasks_list(&self) -> HandlerResult {
        let mut tasks = Vec::new();
        for (path, content, writable) in self.library_files("tasks", ".task.json") {
            let Ok(v) = serde_json::from_str::<Value>(&content) else {
                continue;
            };
            tasks.push(json!({
                "path": path,
                "name": v.get("name").cloned().unwrap_or(Value::Null),
                "category": v.get("category").cloned().unwrap_or(Value::Null),
                "icon": v.get("icon").cloned().unwrap_or(Value::Null),
                "description": v.get("description").cloned().unwrap_or(Value::Null),
                "tags": v.get("tags").cloned().unwrap_or(json!([])),
                "writable": writable,
            }));
        }
        Ok(json!({ "tasks": tasks }))
    }

    /// List configured MCP servers. Reads the project file `<cwd>/.mcp.json`
    /// (scope `project`) when `cwd` is given, and the user file `~/.claude.json`
    /// (scope `user`, override with `path`). Project servers shadow user servers
    /// of the same name. Missing/unparseable files yield no entries.
    fn mcp_list(&self, p: &Value) -> HandlerResult {
        let mut sources: Vec<(String, &str)> = Vec::new();
        if let Some(cwd) = p.get("cwd").and_then(Value::as_str) {
            sources.push((format!("{cwd}/.mcp.json"), "project"));
        }
        if let Some(path) = p.get("path").and_then(Value::as_str) {
            sources.push((path.to_string(), "user"));
        } else if let Ok(home) = std::env::var("HOME") {
            sources.push((format!("{home}/.claude.json"), "user"));
        }

        let mut list = Vec::new();
        let mut seen = HashSet::new();
        for (path, scope) in sources {
            let content = std::fs::read_to_string(&path).unwrap_or_default();
            let servers = cs_mcp::parse_mcp_config(&content).unwrap_or_default();
            for s in servers {
                if !seen.insert(s.name.clone()) {
                    continue;
                }
                let mut entry = json!({
                    "name": s.name,
                    "scope": scope,
                    "source": path,
                    "args": [],
                    "env": {},
                    "url": "",
                });
                match &s.transport {
                    cs_mcp::Transport::Stdio { command, args, env } => {
                        entry["transport"] = json!("stdio");
                        entry["target"] = json!(command);
                        entry["args"] = json!(args);
                        entry["env"] = json!(env);
                    }
                    cs_mcp::Transport::Sse { url } => {
                        entry["transport"] = json!("sse");
                        entry["target"] = json!(url);
                        entry["url"] = json!(url);
                    }
                    cs_mcp::Transport::Http { url } => {
                        entry["transport"] = json!("http");
                        entry["target"] = json!(url);
                        entry["url"] = json!(url);
                    }
                }
                list.push(entry);
            }
        }
        Ok(json!({ "servers": list }))
    }

    /// Add or update an MCP server in the config for the requested `scope`
    /// (`project` → `<cwd>/.mcp.json`, else `user` → `~/.claude.json`). Only the
    /// `mcpServers` map is touched; every other key in the file is preserved, and
    /// the write is atomic (temp file + rename).
    fn mcp_upsert(&self, p: &Value) -> HandlerResult {
        let name = p
            .get("name")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .ok_or("missing 'name'")?;
        let transport = p
            .get("transport")
            .and_then(Value::as_str)
            .unwrap_or("stdio");
        let scope = p.get("scope").and_then(Value::as_str).unwrap_or("user");
        let path = mcp_path_for_scope(scope, p.get("cwd").and_then(Value::as_str))?;

        let entry = match transport {
            "sse" | "http" => {
                let url = p
                    .get("url")
                    .and_then(Value::as_str)
                    .map(str::trim)
                    .filter(|s| !s.is_empty())
                    .ok_or("missing 'url' for an sse/http server")?;
                json!({ "type": transport, "url": url })
            }
            _ => {
                let command = p
                    .get("command")
                    .and_then(Value::as_str)
                    .map(str::trim)
                    .filter(|s| !s.is_empty())
                    .ok_or("missing 'command' for a stdio server")?;
                let args = p.get("args").cloned().unwrap_or(json!([]));
                let env = p.get("env").cloned().unwrap_or(json!({}));
                json!({ "command": command, "args": args, "env": env })
            }
        };

        let mut root = read_json_object(&path);
        let servers = root
            .as_object_mut()
            .unwrap()
            .entry("mcpServers")
            .or_insert_with(|| json!({}));
        if !servers.is_object() {
            *servers = json!({});
        }
        servers
            .as_object_mut()
            .unwrap()
            .insert(name.to_string(), entry);

        let pretty = serde_json::to_string_pretty(&root).map_err(|e| e.to_string())?;
        atomic_write(Path::new(&path), &format!("{pretty}\n"))?;
        Ok(json!({ "ok": true, "path": path, "name": name }))
    }

    /// Remove an MCP server by name from the config for the requested `scope`.
    fn mcp_remove(&self, p: &Value) -> HandlerResult {
        let name = p
            .get("name")
            .and_then(Value::as_str)
            .ok_or("missing 'name'")?;
        let scope = p.get("scope").and_then(Value::as_str).unwrap_or("user");
        let path = mcp_path_for_scope(scope, p.get("cwd").and_then(Value::as_str))?;

        let mut root = read_json_object(&path);
        if let Some(servers) = root.get_mut("mcpServers").and_then(Value::as_object_mut) {
            servers.remove(name);
        }
        let pretty = serde_json::to_string_pretty(&root).map_err(|e| e.to_string())?;
        atomic_write(Path::new(&path), &format!("{pretty}\n"))?;
        Ok(json!({ "ok": true, "path": path, "name": name }))
    }

    /// List configured Claude hooks parsed from `settings.json` — the project's
    /// `<cwd>/.claude/settings.json` (if `cwd` is given) and the global
    /// `~/.claude/settings.json`. Missing/unparseable files are skipped.
    fn hooks_list(&self, p: &Value) -> HandlerResult {
        let mut sources: Vec<String> = Vec::new();
        if let Some(cwd) = p.get("cwd").and_then(Value::as_str) {
            sources.push(format!("{cwd}/.claude/settings.json"));
        }
        if let Ok(home) = std::env::var("HOME") {
            sources.push(format!("{home}/.claude/settings.json"));
        }

        let mut hooks = Vec::new();
        for path in sources {
            let Ok(content) = std::fs::read_to_string(&path) else {
                continue;
            };
            let Ok(value) = serde_json::from_str::<Value>(&content) else {
                continue;
            };
            let Some(by_event) = value.get("hooks").and_then(Value::as_object) else {
                continue;
            };
            for (event, entries) in by_event {
                let Some(entries) = entries.as_array() else {
                    continue;
                };
                for entry in entries {
                    let matcher = entry
                        .get("matcher")
                        .and_then(Value::as_str)
                        .unwrap_or("*")
                        .to_string();
                    let Some(commands) = entry.get("hooks").and_then(Value::as_array) else {
                        continue;
                    };
                    for command in commands {
                        hooks.push(json!({
                            "event": event,
                            "matcher": matcher,
                            "command": command.get("command").and_then(Value::as_str).unwrap_or(""),
                            "source": path,
                        }));
                    }
                }
            }
        }
        Ok(json!({ "hooks": hooks }))
    }

    fn definitions_list(&self) -> HandlerResult {
        let mut defs = Vec::new();
        for (path, content, writable) in self.library_files("definitions", ".def.md") {
            let fm = parse_frontmatter(&content);
            defs.push(json!({
                "path": path,
                "name": fm.get("name").cloned().unwrap_or_default(),
                "category": fm.get("category").cloned().unwrap_or_default(),
                "scope": fm.get("scope").cloned().unwrap_or_default(),
                "tags": fm.get("tags").cloned().unwrap_or_default(),
                "version": fm.get("version").cloned().unwrap_or_default(),
                "writable": writable,
            }));
        }
        Ok(json!({ "definitions": defs }))
    }

    /// Collect library files of a kind from both the writable user library
    /// (`<state_dir>/<sub>`, always editable) and the shipped reference library
    /// (`<library_dir>/<sub>`, read-only in the packaged app). Each entry is
    /// tagged `writable`. When the two roots coincide (dev / default), the
    /// shipped scan is skipped so nothing is listed twice.
    fn library_files(&self, sub: &str, suffix: &str) -> Vec<(String, String, bool)> {
        let user_dir = self.inner.state_dir.join(sub);
        let shipped_dir = self.inner.library_dir.join(sub);
        let mut out = Vec::new();
        let mut seen = HashSet::new();
        let same = user_dir == shipped_dir;
        for (dir, writable) in [(&user_dir, true), (&shipped_dir, false)] {
            if !writable && same {
                continue;
            }
            for (path, content) in read_files_with_suffix(dir, suffix) {
                if seen.insert(path.clone()) {
                    out.push((path, content, writable));
                }
            }
        }
        out
    }

    /// Create a new, editable library item (task or definition) in the user
    /// library under `<state_dir>/<sub>/custom/`. Returns the new file path.
    fn library_create(&self, p: &Value, sub: &str) -> HandlerResult {
        let name = p
            .get("name")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .unwrap_or(if sub == "tasks" {
                "New Task"
            } else {
                "New Definition"
            });
        let slug = slugify(name);
        let dir = self.inner.state_dir.join(sub).join("custom");
        std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;

        let (suffix, content) = if sub == "tasks" {
            let body = serde_json::to_string_pretty(&json!({
                "id": slug,
                "name": name,
                "description": "Describe what this task does.",
                "icon": "wand.and.stars",
                "category": "custom",
                "tags": [],
                "scope": "user",
                "version": "1.0.0",
                "agent": {
                    "model": "claude-sonnet-4-5",
                    "allowed_tools": ["Read", "Grep", "Glob"]
                },
                "workflow": { "steps": [] }
            }))
            .map_err(|e| e.to_string())?;
            (".task.json", format!("{body}\n"))
        } else {
            (
                ".def.md",
                format!(
                    "---\nname: {name}\ncategory: custom\ntags: []\nscope: user\nversion: 1.0.0\n---\n\n# {name}\n\nDescribe this definition — the guidance Claude should follow.\n"
                ),
            )
        };

        let path = unique_path(&dir, &slug, suffix);
        std::fs::write(&path, content).map_err(|e| e.to_string())?;
        Ok(json!({ "ok": true, "path": path.to_string_lossy() }))
    }

    /// Delete a user-library item. Only files inside the writable user library
    /// (`<state_dir>/<sub>/`) may be removed; shipped files are protected.
    fn library_delete(&self, p: &Value, sub: &str, suffix: &str) -> HandlerResult {
        let path = p
            .get("path")
            .and_then(Value::as_str)
            .ok_or("missing 'path'")?;
        let target = Path::new(path);
        let user_root = self.inner.state_dir.join(sub);
        if !target.starts_with(&user_root) {
            return Err("only items in your library can be deleted".to_string());
        }
        if !path.ends_with(suffix) {
            return Err("refusing to delete a non-library file".to_string());
        }
        std::fs::remove_file(target).map_err(|e| e.to_string())?;
        Ok(json!({ "ok": true }))
    }

    /// List installed skills for the project at `cwd` (and the user's global
    /// skills), parsed from `<root>/.claude/skills/<name>/SKILL.md`. Each skill's
    /// invocation `command` is its directory name (typed as `/<command>`).
    /// Project skills shadow user skills of the same name.
    fn skills_list(&self, p: &Value) -> HandlerResult {
        let mut roots: Vec<(PathBuf, &str)> = Vec::new();
        if let Some(cwd) = p.get("cwd").and_then(Value::as_str) {
            roots.push((Path::new(cwd).join(".claude").join("skills"), "project"));
        }
        if let Ok(home) = std::env::var("HOME") {
            roots.push((Path::new(&home).join(".claude").join("skills"), "user"));
        }

        let mut skills = Vec::new();
        let mut seen = HashSet::new();
        for (root, scope) in roots {
            let Ok(entries) = std::fs::read_dir(&root) else {
                continue;
            };
            for entry in entries.flatten() {
                let dir = entry.path();
                if !dir.is_dir() {
                    continue;
                }
                let Ok(content) = std::fs::read_to_string(dir.join("SKILL.md")) else {
                    continue;
                };
                let Some(command) = dir.file_name().and_then(|n| n.to_str()).map(str::to_string)
                else {
                    continue;
                };
                if !seen.insert(command.clone()) {
                    continue; // project scope already provided this skill
                }
                let fm = parse_frontmatter(&content);
                let name = fm
                    .get("name")
                    .cloned()
                    .filter(|s| !s.is_empty())
                    .unwrap_or_else(|| command.clone());
                skills.push(json!({
                    "command": command,
                    "name": name,
                    "description": fm.get("description").cloned().unwrap_or_default(),
                    "path": dir.join("SKILL.md").to_string_lossy(),
                    "scope": scope,
                }));
            }
        }
        skills.sort_by(|a, b| {
            a["command"]
                .as_str()
                .unwrap_or("")
                .cmp(b["command"].as_str().unwrap_or(""))
        });
        Ok(json!({ "skills": skills }))
    }
}

fn config_to_json(cfg: &AppConfig) -> Value {
    json!({
        "trust_mode": cfg.trust_mode,
        "default_model": cfg.default_model,
        "daily_budget_usd": cfg.daily_budget_usd,
        "context_token_budget": cfg.context_token_budget,
        "voice": cfg.voice,
        "vector": cfg.vector,
    })
}

/// Recursively read every file under `dir` whose name ends with `suffix`,
/// returning `(path, contents)` pairs sorted by path. Missing directories yield
/// an empty list rather than an error.
fn read_files_with_suffix(dir: &Path, suffix: &str) -> Vec<(String, String)> {
    let mut out = Vec::new();
    let mut stack = vec![dir.to_path_buf()];
    while let Some(d) = stack.pop() {
        let Ok(entries) = std::fs::read_dir(&d) else {
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                stack.push(path);
            } else if path
                .file_name()
                .and_then(|n| n.to_str())
                .is_some_and(|n| n.ends_with(suffix))
            {
                if let Ok(content) = std::fs::read_to_string(&path) {
                    out.push((path.to_string_lossy().to_string(), content));
                }
            }
        }
    }
    out.sort_by(|a, b| a.0.cmp(&b.0));
    out
}

/// Parse a leading `--- ... ---` YAML-ish frontmatter block into key/value
/// strings. Only the simple `key: value` shape used by `.def.md` files is
/// supported; values keep their raw text (e.g. `[a, b]` stays a string).
fn parse_frontmatter(content: &str) -> HashMap<String, String> {
    let mut map = HashMap::new();
    let mut lines = content.lines();
    if lines.next().map(str::trim) != Some("---") {
        return map;
    }
    for line in lines {
        let trimmed = line.trim();
        if trimmed == "---" {
            break;
        }
        if let Some((k, v)) = trimmed.split_once(':') {
            map.insert(k.trim().to_string(), v.trim().trim_matches('"').to_string());
        }
    }
    map
}

/// Turn a human name into a filesystem-safe kebab slug for a library filename.
fn slugify(name: &str) -> String {
    let mut out = String::new();
    let mut prev_dash = false;
    for ch in name.trim().to_lowercase().chars() {
        if ch.is_alphanumeric() {
            out.push(ch);
            prev_dash = false;
        } else if !prev_dash {
            out.push('-');
            prev_dash = true;
        }
    }
    let trimmed = out.trim_matches('-').to_string();
    if trimmed.is_empty() {
        "untitled".to_string()
    } else {
        trimmed
    }
}

/// First `<dir>/<slug><suffix>` that doesn't already exist (appends `-2`, `-3`…).
fn unique_path(dir: &Path, slug: &str, suffix: &str) -> PathBuf {
    let mut candidate = dir.join(format!("{slug}{suffix}"));
    let mut n = 2;
    while candidate.exists() {
        candidate = dir.join(format!("{slug}-{n}{suffix}"));
        n += 1;
    }
    candidate
}

/// The MCP config file for a scope: `project` → `<cwd>/.mcp.json` (requires a
/// cwd), anything else → the user's `~/.claude.json`.
fn mcp_path_for_scope(scope: &str, cwd: Option<&str>) -> std::result::Result<String, String> {
    match scope {
        "project" => {
            let cwd = cwd.ok_or("project scope requires a 'cwd'")?;
            Ok(format!("{cwd}/.mcp.json"))
        }
        _ => {
            let home = std::env::var("HOME").map_err(|_| "HOME is not set".to_string())?;
            Ok(format!("{home}/.claude.json"))
        }
    }
}

/// Read a JSON file as an object, returning an empty object when the file is
/// missing, empty, or not a JSON object (so callers can safely mutate it).
fn read_json_object(path: &str) -> Value {
    let content = std::fs::read_to_string(path).unwrap_or_default();
    match serde_json::from_str::<Value>(&content) {
        Ok(v @ Value::Object(_)) => v,
        _ => json!({}),
    }
}

/// Write `content` atomically: write a sibling temp file, then rename over the
/// target so a crash mid-write can't corrupt an existing config.
fn atomic_write(path: &Path, content: &str) -> std::result::Result<(), String> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let tmp = path.with_extension("claudestudio-tmp");
    std::fs::write(&tmp, content).map_err(|e| e.to_string())?;
    std::fs::rename(&tmp, path).map_err(|e| e.to_string())?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use cs_ipc::new_request;

    fn router() -> Router {
        Router::new(
            AppConfig::default(),
            SessionStore::open_in_memory().expect("in-memory store"),
            EventBus::new(),
            std::env::temp_dir().join(format!("cs-router-state-{}", std::process::id())),
            std::env::temp_dir().join(format!("cs-router-lib-{}", std::process::id())),
        )
    }

    /// A router whose state & library dirs are a fresh, unique temp directory,
    /// so filesystem-mutating tests don't collide.
    fn router_in(tag: &str) -> (Router, PathBuf) {
        let base =
            std::env::temp_dir().join(format!("cs-rt-{tag}-{}-{:?}", std::process::id(), tag));
        let _ = std::fs::remove_dir_all(&base);
        std::fs::create_dir_all(&base).unwrap();
        let r = Router::new(
            AppConfig::default(),
            SessionStore::open_in_memory().expect("in-memory store"),
            EventBus::new(),
            base.clone(),
            base.join("shipped"),
        );
        (r, base)
    }

    #[tokio::test]
    async fn ping_responds_pong() {
        let r = router();
        let req = new_request("ping", json!({}));
        let res = r.dispatch(&req).await;
        assert_eq!(res.payload["pong"], json!(true));
        assert_eq!(res.id, req.id);
    }

    #[test]
    fn slugify_makes_safe_names() {
        assert_eq!(slugify("My Cool Task!"), "my-cool-task");
        assert_eq!(slugify("  spaces  "), "spaces");
        assert_eq!(slugify("***"), "untitled");
        assert_eq!(slugify("Already-Kebab"), "already-kebab");
    }

    #[tokio::test]
    async fn task_create_list_delete_roundtrip() {
        let (r, base) = router_in("task-crud");

        let created = r
            .dispatch(&new_request("tasks.create", json!({ "name": "My Audit" })))
            .await;
        assert_eq!(created.payload["ok"], json!(true));
        let path = created.payload["path"].as_str().unwrap().to_string();
        assert!(path.ends_with(".task.json"));

        let listed = r.dispatch(&new_request("tasks.list", json!({}))).await;
        let tasks = listed.payload["tasks"].as_array().unwrap();
        let mine = tasks
            .iter()
            .find(|t| t["path"] == json!(path))
            .expect("created task is listed");
        assert_eq!(mine["name"], json!("My Audit"));
        assert_eq!(mine["writable"], json!(true));

        let deleted = r
            .dispatch(&new_request("tasks.delete", json!({ "path": path })))
            .await;
        assert_eq!(deleted.payload["ok"], json!(true));

        let after = r.dispatch(&new_request("tasks.list", json!({}))).await;
        assert!(after.payload["tasks"].as_array().unwrap().is_empty());
        let _ = std::fs::remove_dir_all(&base);
    }

    #[tokio::test]
    async fn definition_delete_rejects_paths_outside_user_library() {
        let (r, base) = router_in("def-guard");
        let res = r
            .dispatch(&new_request(
                "definitions.delete",
                json!({ "path": "/etc/hosts" }),
            ))
            .await;
        assert_eq!(res.kind, cs_types::IpcKind::Error);
        let _ = std::fs::remove_dir_all(&base);
    }

    #[tokio::test]
    async fn skills_list_reads_skill_md() {
        let (r, base) = router_in("skills");
        let cwd = base.join("proj");
        let skill_dir = cwd.join(".claude").join("skills").join("graphify");
        std::fs::create_dir_all(&skill_dir).unwrap();
        std::fs::write(
            skill_dir.join("SKILL.md"),
            "---\nname: graphify\ndescription: Turn input into a knowledge graph\n---\n\nbody\n",
        )
        .unwrap();

        let res = r
            .dispatch(&new_request(
                "skills.list",
                json!({ "cwd": cwd.to_string_lossy() }),
            ))
            .await;
        let skills = res.payload["skills"].as_array().unwrap();
        let s = skills
            .iter()
            .find(|s| s["command"] == json!("graphify"))
            .expect("graphify skill found");
        assert_eq!(s["scope"], json!("project"));
        assert_eq!(s["description"], json!("Turn input into a knowledge graph"));
        let _ = std::fs::remove_dir_all(&base);
    }

    #[tokio::test]
    async fn mcp_upsert_list_remove_project_scope() {
        let (r, base) = router_in("mcp");
        let cwd = base.join("proj");
        std::fs::create_dir_all(&cwd).unwrap();
        let cwd_str = cwd.to_string_lossy().to_string();

        let up = r
            .dispatch(&new_request(
                "mcp.upsert",
                json!({
                    "scope": "project", "cwd": cwd_str,
                    "name": "filesystem", "transport": "stdio",
                    "command": "npx", "args": ["-y", "server-filesystem"]
                }),
            ))
            .await;
        assert_eq!(up.payload["ok"], json!(true));

        // Point the user source at an empty temp file so the test is isolated
        // from the developer's real ~/.claude.json.
        let empty_user = base.join("empty-user.json").to_string_lossy().to_string();
        let listed = r
            .dispatch(&new_request(
                "mcp.list",
                json!({ "cwd": cwd_str, "path": empty_user }),
            ))
            .await;
        let servers = listed.payload["servers"].as_array().unwrap();
        let s = servers
            .iter()
            .find(|s| s["name"] == json!("filesystem"))
            .expect("server listed");
        assert_eq!(s["transport"], json!("stdio"));
        assert_eq!(s["target"], json!("npx"));
        assert_eq!(s["scope"], json!("project"));
        assert_eq!(s["args"], json!(["-y", "server-filesystem"]));

        let rm = r
            .dispatch(&new_request(
                "mcp.remove",
                json!({ "scope": "project", "cwd": cwd_str, "name": "filesystem" }),
            ))
            .await;
        assert_eq!(rm.payload["ok"], json!(true));

        let after = r
            .dispatch(&new_request(
                "mcp.list",
                json!({ "cwd": cwd_str, "path": empty_user }),
            ))
            .await;
        assert!(after.payload["servers"].as_array().unwrap().is_empty());
        let _ = std::fs::remove_dir_all(&base);
    }

    #[tokio::test]
    async fn unknown_method_is_error_response() {
        use cs_types::IpcKind;
        let r = router();
        let req = new_request("does.not.exist", json!({}));
        let res = r.dispatch(&req).await;
        assert_eq!(res.kind, IpcKind::Error);
        assert!(res.payload.get("message").is_some());
        assert_eq!(res.id, req.id);
    }

    #[tokio::test]
    async fn context_budget_reports_six_layers() {
        let r = router();
        let req = new_request("context.budget", json!({}));
        let res = r.dispatch(&req).await;
        let layers = res.payload["layers"].as_array().expect("layers array");
        assert_eq!(layers.len(), 6);
    }

    #[tokio::test]
    async fn create_then_list_and_stats() {
        let r = router();
        let create = r
            .dispatch(&new_request(
                "session.create",
                json!({ "title": "Refactor", "cwd": "/repo", "branch": "main" }),
            ))
            .await;
        let id = create.payload["id"].as_str().expect("id").to_string();
        assert!(!id.is_empty());

        let list = r.dispatch(&new_request("session.list", json!({}))).await;
        let sessions = list.payload["sessions"].as_array().expect("sessions");
        assert_eq!(sessions.len(), 1);
        assert_eq!(sessions[0]["title"], json!("Refactor"));

        let get = r
            .dispatch(&new_request("session.get", json!({ "id": id })))
            .await;
        assert_eq!(get.payload["branch"], json!("main"));

        let stats = r.dispatch(&new_request("session.stats", json!({}))).await;
        assert_eq!(stats.payload["sessions"], json!(1));
    }

    #[tokio::test]
    async fn config_set_persists_and_roundtrips() {
        let r = router();
        let res = r
            .dispatch(&new_request(
                "config.set",
                json!({ "trust_mode": "yolo", "daily_budget_usd": 42.5 }),
            ))
            .await;
        assert_eq!(res.payload["trust_mode"], json!("yolo"));
        assert_eq!(res.payload["daily_budget_usd"], json!(42.5));

        let get = r.dispatch(&new_request("config.get", json!({}))).await;
        assert_eq!(get.payload["trust_mode"], json!("yolo"));
    }

    #[tokio::test]
    async fn config_set_rejects_bad_trust_mode() {
        let r = router();
        let res = r
            .dispatch(&new_request("config.set", json!({ "trust_mode": "bogus" })))
            .await;
        use cs_types::IpcKind;
        assert_eq!(res.kind, IpcKind::Error);
    }

    #[tokio::test]
    async fn tasks_and_definitions_read_from_library() {
        // Point the library at a temp dir with one task and one definition.
        let lib = std::env::temp_dir().join(format!("cs-lib-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&lib);
        std::fs::create_dir_all(lib.join("tasks/compliance")).unwrap();
        std::fs::create_dir_all(lib.join("definitions/loading")).unwrap();
        std::fs::write(
            lib.join("tasks/compliance/check.task.json"),
            r#"{"name":"Kleinunternehmer-Check","category":"Compliance","tags":["de"]}"#,
        )
        .unwrap();
        std::fs::write(
            lib.join("definitions/loading/video.def.md"),
            "---\nname: Video Frame Loading\ncategory: Loading Systems\nscope: global\n---\nbody",
        )
        .unwrap();

        let r = Router::new(
            AppConfig::default(),
            SessionStore::open_in_memory().unwrap(),
            EventBus::new(),
            std::env::temp_dir(),
            lib.clone(),
        );

        let tasks = r.dispatch(&new_request("tasks.list", json!({}))).await;
        let arr = tasks.payload["tasks"].as_array().unwrap();
        assert_eq!(arr.len(), 1);
        assert_eq!(arr[0]["name"], json!("Kleinunternehmer-Check"));

        let defs = r
            .dispatch(&new_request("definitions.list", json!({})))
            .await;
        let darr = defs.payload["definitions"].as_array().unwrap();
        assert_eq!(darr.len(), 1);
        assert_eq!(darr[0]["name"], json!("Video Frame Loading"));
        assert_eq!(darr[0]["scope"], json!("global"));

        let _ = std::fs::remove_dir_all(&lib);
    }

    #[tokio::test]
    async fn mcp_list_parses_config_file() {
        let dir = std::env::temp_dir().join(format!("cs-mcp-{}", std::process::id()));
        let _ = std::fs::create_dir_all(&dir);
        let cfg = dir.join("claude.json");
        std::fs::write(
            &cfg,
            r#"{"mcpServers":{"fs":{"command":"npx","args":["-y","server-fs"]}}}"#,
        )
        .unwrap();

        let r = router();
        let res = r
            .dispatch(&new_request(
                "mcp.list",
                json!({ "path": cfg.to_string_lossy() }),
            ))
            .await;
        let servers = res.payload["servers"].as_array().expect("servers");
        assert_eq!(servers.len(), 1);
        assert_eq!(servers[0]["name"], json!("fs"));
        assert_eq!(servers[0]["transport"], json!("stdio"));
        assert_eq!(servers[0]["target"], json!("npx"));

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn hooks_list_parses_settings() {
        let dir = std::env::temp_dir().join(format!("cs-hooks-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(dir.join(".claude")).unwrap();
        std::fs::write(
            dir.join(".claude/settings.json"),
            r#"{"hooks":{"PostToolUse":[{"matcher":"Edit","hooks":[{"type":"command","command":"cargo fmt"}]}]}}"#,
        )
        .unwrap();

        let r = router();
        let res = r
            .dispatch(&new_request(
                "hooks.list",
                json!({ "cwd": dir.to_string_lossy() }),
            ))
            .await;
        let hooks = res.payload["hooks"].as_array().expect("hooks");
        assert!(hooks.iter().any(|h| h["event"] == "PostToolUse"
            && h["matcher"] == "Edit"
            && h["command"] == "cargo fmt"));

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn file_write_then_read_roundtrips() {
        let dir = std::env::temp_dir().join(format!("cs-file-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let path = dir.join("nested/CLAUDE.md");
        let path_str = path.to_string_lossy().to_string();
        let r = router();

        let miss = r
            .dispatch(&new_request("file.read", json!({ "path": path_str })))
            .await;
        assert_eq!(miss.payload["exists"], json!(false));
        assert_eq!(miss.payload["content"], json!(""));

        let w = r
            .dispatch(&new_request(
                "file.write",
                json!({ "path": path_str, "content": "# Project\n" }),
            ))
            .await;
        assert_eq!(w.payload["ok"], json!(true));

        let rd = r
            .dispatch(&new_request("file.read", json!({ "path": path_str })))
            .await;
        assert_eq!(rd.payload["exists"], json!(true));
        assert_eq!(rd.payload["content"], json!("# Project\n"));

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn file_read_rejects_oversized_file_by_metadata() {
        // A file over the 4 MiB cap is rejected via metadata, before the whole
        // file is read into memory.
        let dir = std::env::temp_dir().join(format!("cs-file-big-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("big.bin");
        std::fs::write(&path, vec![b'a'; 4 * 1024 * 1024 + 1]).unwrap();
        let r = router();

        let res = r
            .dispatch(&new_request(
                "file.read",
                json!({ "path": path.to_string_lossy() }),
            ))
            .await;
        assert_eq!(res.kind, cs_types::IpcKind::Error);
        assert!(res.payload["message"]
            .as_str()
            .unwrap_or_default()
            .contains("too large"));

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn git_status_in_temp_repo() {
        // Initialise a tiny repo and confirm git.status sees an untracked file.
        let repo = std::env::temp_dir().join(format!("cs-git-rpc-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&repo);
        std::fs::create_dir_all(&repo).unwrap();
        let run = |args: &[&str]| {
            std::process::Command::new("git")
                .arg("-C")
                .arg(&repo)
                .args(args)
                .output()
                .unwrap();
        };
        run(&["init", "-q"]);
        std::fs::write(repo.join("a.txt"), "hi").unwrap();

        let r = router();
        let res = r
            .dispatch(&new_request(
                "git.status",
                json!({ "cwd": repo.to_string_lossy() }),
            ))
            .await;
        let entries = res.payload["entries"].as_array().expect("entries");
        assert!(entries.iter().any(|e| e["path"] == json!("a.txt")));

        let _ = std::fs::remove_dir_all(&repo);
    }
}
