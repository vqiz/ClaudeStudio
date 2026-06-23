//! The IPC method router.
//!
//! [`Router`] owns the shared application state — the loaded [`AppConfig`], the
//! [`SessionStore`], the [`EventBus`], and the filesystem locations of the state
//! and library directories — and dispatches each incoming [`IpcEnvelope`] request
//! to a handler keyed by its `method` string.
//!
//! Handlers return [`HandlerResult`] (a JSON payload or a typed [`IpcFailure`]);
//! [`Router::dispatch`] wraps a success into a response envelope and a failure
//! into a typed error envelope (`kind: error`, `{ code, message }`), so a bad
//! request never tears down the connection. It also enforces a per-handler
//! server-side deadline ([`HANDLER_TIMEOUT`]) and logs `{id, method}` with timing.
//!
//! TODO(A1 — Router decomposition): this type has grown into a "god object"
//! dispatching ~40 methods across config, sessions, git, libraries, MCP, skills,
//! plugins, and hooks. The safe incremental refactor is to keep `Router` as the
//! thin dispatcher + shared `Inner` state, and move each method *group* into its
//! own submodule (`handlers/config.rs`, `handlers/sessions.rs`, `handlers/git.rs`,
//! `handlers/mcp.rs`, `handlers/skills_plugins.rs`, `handlers/library.rs`) as
//! `impl Router` blocks or free functions taking `&Inner`. The `match` in
//! `handle` then becomes a small table delegating to those modules. This was
//! deferred here to avoid destabilizing the build in one pass; the error-taxonomy,
//! timeout, and logging seams added below are the prerequisites that make the
//! split mechanical.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, RwLock};

use cs_agentic_os::{EventBus, SystemEvent};
use cs_config::{estimate_tokens, AppConfig, ContextAssembler, LayerKind};
use cs_git::{GitBackend, SystemGit};
use cs_ipc::{ErrorCode, IpcEnvelope, IpcFailure};
use cs_sessions::{
    now_millis, HitSource, NewEvent, NewMessage, NewSession, NewToolCall, NewUsage, SessionStore,
};
use cs_vector::{Embedder, HashEmbedder};
use serde_json::{json, Value};

use crate::embedding::{self, HASH_TAG};

/// The active embedder plus the tag stored next to the vectors it produces.
/// Held behind an [`RwLock`] in [`Inner`] so the core can hot-swap a freshly
/// downloaded neural model in while the socket is already serving.
struct EmbedderState {
    embedder: Arc<dyn Embedder>,
    tag: String,
}

/// What every handler returns: a JSON payload or a typed [`IpcFailure`] (an
/// [`ErrorCode`] plus message). Handlers may still `?` a bare `String`/`&str`
/// error — those convert to [`ErrorCode::Internal`] via `From` — but should
/// prefer the typed `IpcFailure::invalid` / `::not_found` / … constructors so the
/// front-end can branch on the failure *kind* rather than parse its message.
type HandlerResult = std::result::Result<Value, IpcFailure>;

/// Default per-handler server-side deadline. A handler that wedges (e.g. a
/// shell-out to `claude` that never returns) is abandoned after this long and
/// answered with a typed timeout error, so a stuck handler can't pin the
/// connection forever. The Swift client's own request timeout is deliberately a
/// touch shorter; this is the backstop for handlers the client gave up on.
const HANDLER_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(15);

/// A longer deadline for handlers that are *legitimately* slow on big inputs —
/// git read commands on a huge monorepo, or full-archive session search /
/// embedding backfill. Applying the 15s default to these would regress large
/// repos (A13/A14). These are all read-only, so abandoning the future mid-flight
/// is cancellation-safe (see [`deadline_for`]).
const LONG_HANDLER_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(60);

/// The server-side deadline for a given method.
///
/// CANCELLATION SAFETY: when the deadline fires, the handler future is *dropped*.
/// Every method covered here is **read-only** (git status/diff/log, session
/// search) or runs its mutation in a detached `spawn_blocking` whose `JoinHandle`
/// outlives the drop — so dropping the dispatch future cannot leave a half-applied
/// write. If a *write-path* handler is ever wrapped by this timeout, dropping it
/// mid-write is NOT cancellation-safe and the deadline logic must be revisited
/// (e.g. run the write to completion in `spawn_blocking` and only race the await).
fn deadline_for(method: &str) -> std::time::Duration {
    match method {
        // Genuinely-long read-only handlers on large repos / archives.
        "git.status" | "git.diff" | "git.log" | "git.branch" | "git.worktrees"
        | "session.search" => LONG_HANDLER_TIMEOUT,
        _ => HANDLER_TIMEOUT,
    }
}

/// Map a [`cs_sessions::Error`] to a typed [`IpcFailure`]: a `NotFound` becomes
/// [`ErrorCode::NotFound`]; everything else (SQLite, serde) is a
/// [`ErrorCode::SessionError`].
fn session_failure(e: cs_sessions::Error) -> IpcFailure {
    match e {
        cs_sessions::Error::NotFound(_) => IpcFailure::not_found(e.to_string()),
        _ => IpcFailure::session(e.to_string()),
    }
}

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
    /// Per-running-session cancel signals, so `session.stop` can kill a live run.
    cancels: Mutex<HashMap<String, Arc<tokio::sync::Notify>>>,
    /// Active semantic embedder; swappable so a background download can upgrade
    /// the hash fallback to the neural model without restarting.
    embedder: RwLock<EmbedderState>,
}

impl Router {
    /// Build a router from the loaded application components.
    ///
    /// The semantic embedder starts as the dependency-free hash fallback;
    /// production swaps in the neural model via [`Router::set_embedder`] once a
    /// background task has loaded it.
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
                cancels: Mutex::new(HashMap::new()),
                embedder: RwLock::new(EmbedderState {
                    embedder: Arc::new(HashEmbedder::default()),
                    tag: HASH_TAG.to_string(),
                }),
            }),
        }
    }

    /// Replace the active embedder (and its vector tag). Used by the core to
    /// upgrade from the hash fallback to the neural model once it is ready.
    pub fn set_embedder(&self, embedder: Arc<dyn Embedder>, tag: String) {
        let mut slot = self.inner.embedder.write().unwrap();
        slot.embedder = embedder;
        slot.tag = tag;
    }

    /// Snapshot the current embedder and its tag (cheap `Arc`/`String` clone),
    /// taken without holding the lock across an embed call.
    fn current_embedder(&self) -> (Arc<dyn Embedder>, String) {
        let slot = self.inner.embedder.read().unwrap();
        (slot.embedder.clone(), slot.tag.clone())
    }

    /// Register a cancel signal for a running session and return it. The session
    /// forwarder awaits it; `session.stop` fires it.
    pub fn register_cancel(&self, session_id: &str) -> Arc<tokio::sync::Notify> {
        let notify = Arc::new(tokio::sync::Notify::new());
        self.inner
            .cancels
            .lock()
            .unwrap()
            .insert(session_id.to_string(), notify.clone());
        notify
    }

    /// Fire the cancel signal for `session_id`. Returns whether one was armed.
    pub fn trigger_cancel(&self, session_id: &str) -> bool {
        match self.inner.cancels.lock().unwrap().get(session_id) {
            Some(notify) => {
                notify.notify_one();
                true
            }
            None => false,
        }
    }

    /// Drop a session's cancel signal (called when the run ends).
    pub fn clear_cancel(&self, session_id: &str) {
        self.inner.cancels.lock().unwrap().remove(session_id);
    }

    /// Stop a running live session by id (kills the `claude` process).
    fn session_stop(&self, p: &Value) -> HandlerResult {
        let id = p
            .get("session_id")
            .and_then(Value::as_str)
            .ok_or_else(|| IpcFailure::invalid("missing 'session_id'"))?;
        Ok(json!({ "ok": true, "stopped": self.trigger_cancel(id) }))
    }

    /// The shared event bus, so the connection layer can stream `SystemEvent`s
    /// to a subscribed front-end.
    pub fn event_bus(&self) -> &EventBus {
        &self.inner.event_bus
    }

    /// The currently configured trust mode (used to choose the run's tool
    /// permission posture).
    pub fn trust_mode(&self) -> cs_types::TrustMode {
        self.inner.config.lock().unwrap().trust_mode
    }

    /// Dispatch a request envelope, returning the response envelope to send back.
    ///
    /// Logs `{id, method}` at start and the outcome (ok / error code / timeout)
    /// with elapsed time, so a request can be traced across the IPC boundary
    /// (A20). Enforces a per-handler [`HANDLER_TIMEOUT`] (A13): a wedged handler
    /// is abandoned and answered with a typed timeout error rather than pinning
    /// the connection forever. The handler future keeps running until it
    /// completes or is dropped; the dropped response is logged.
    pub async fn dispatch(&self, request: &IpcEnvelope) -> IpcEnvelope {
        let started = std::time::Instant::now();
        let id = request.id.as_str();
        let method = request.method.as_str();
        tracing::debug!(id, method, "ipc request");

        let deadline = deadline_for(method);
        let outcome = tokio::time::timeout(deadline, self.handle(request)).await;
        let elapsed_ms = started.elapsed().as_millis();

        match outcome {
            Ok(Ok(payload)) => {
                tracing::debug!(id, method, elapsed_ms, "ipc ok");
                request.response_to(payload)
            }
            Ok(Err(failure)) => {
                tracing::warn!(
                    id,
                    method,
                    elapsed_ms,
                    code = failure.code.as_i64(),
                    message = %failure.message,
                    "ipc error"
                );
                cs_ipc::error_response_typed(request, &failure)
            }
            Err(_elapsed) => {
                tracing::error!(
                    id,
                    method,
                    elapsed_ms,
                    timeout_ms = deadline.as_millis(),
                    "ipc handler timed out; response dropped"
                );
                let failure = IpcFailure::new(
                    ErrorCode::Internal,
                    format!(
                        "handler '{method}' exceeded the {}s server deadline",
                        deadline.as_secs()
                    ),
                );
                cs_ipc::error_response_typed(request, &failure)
            }
        }
    }

    /// Async dispatch front door.
    ///
    /// The git handlers are genuinely `async` (they shell out via `tokio::process`,
    /// which yields at every await), so they run inline where the dispatch timeout
    /// can race them directly. *Every other* handler is synchronous and may block
    /// the executor thread — most dangerously the MCP / skills / plugins arms,
    /// which call the blocking `run_claude*` shell-out. A blocking call never yields,
    /// so wrapping a blocking handler in `tokio::time::timeout` is useless: the
    /// timeout future is never polled until the block finally returns (A13/Finding 1).
    /// We therefore offload the synchronous arms to `spawn_blocking`, freeing the
    /// executor thread so the dispatch-level timeout can actually fire and unwedge
    /// the connection. If the timeout drops us, the blocking work detaches and runs
    /// to completion on its pool thread; every offloaded handler is read-only or a
    /// self-contained write, so a dropped future leaves no half-applied state.
    async fn handle(&self, request: &IpcEnvelope) -> HandlerResult {
        let p = &request.payload;
        match request.method.as_str() {
            // --- Git (already async / interruptible) ---
            "git.status" => self.git_status(p).await,
            "git.branch" => self.git_branch(p).await,
            "git.worktrees" => self.git_worktrees(p).await,
            "git.diff" => self.git_diff(p).await,
            "git.log" => self.git_log(p).await,
            "git.commit" => self.git_commit(p).await,
            "git.commit_message" => self.git_commit_message(p).await,
            "worktree.add" => self.worktree_add(p).await,
            "worktree.remove" => self.worktree_remove(p).await,
            "worktree.merge" => self.worktree_merge(p).await,

            // --- Everything else is synchronous: run on the blocking pool ---
            _ => {
                let router = self.clone();
                let request = request.clone();
                tokio::task::spawn_blocking(move || router.handle_blocking(&request))
                    .await
                    .map_err(|e| {
                        IpcFailure::internal(format!("handler task failed to complete: {e}"))
                    })?
            }
        }
    }

    /// Synchronous handler dispatch, run on the blocking thread pool by [`handle`].
    /// Contains every non-`async` method arm; never call this directly from the
    /// async path — go through [`handle`] so the executor thread stays free.
    fn handle_blocking(&self, request: &IpcEnvelope) -> HandlerResult {
        let p = &request.payload;
        match request.method.as_str() {
            "ping" => Ok(json!({ "pong": true })),

            // --- Configuration & context ---
            "config.get" => Ok(self.config_payload()),
            "config.set" => self.config_set(p),
            "context.budget" => Ok(self.budget_payload()),
            "context.assemble" => self.context_assemble(p),
            "memory.get" => self.memory_get(p),
            "memory.set" => self.memory_set(p),
            "memory.append" => self.memory_append(p),
            "claudemd.save" => self.claudemd_save(p),

            // --- Security & permissions ---
            "permissions.check" => self.permissions_check(p),
            "permissions.audit_log" => self.permissions_audit_log(p),
            "permissions.matrix_get" => self.permissions_matrix_get(),
            "permissions.matrix_set" => self.permissions_matrix_set(p),
            "security.scan_output" => Ok(scan_output_payload(p)),

            // --- Agentic OS: rules, routing, scheduler, monitors, supervisor ---
            "rules.add" => self.rules_add(p),
            "rules.list" => self.rules_list(),
            "rules.eval" => self.rules_eval(p),
            "routing.route" => Ok(routing_route_payload(p)),
            "queue.order" => Ok(queue_order_payload(p)),
            "scheduler.admit" => Ok(scheduler_admit_payload(p)),
            "monitor.health_check" => Ok(health_check_payload(p)),
            "monitor.cost_guard" => Ok(cost_guard_payload(p)),
            "supervisor.evaluate" => Ok(supervisor_evaluate_payload(p)),

            // --- Live session control ---
            "session.stop" => self.session_stop(p),

            // --- Session archive ---
            "session.list" => self.session_list(p),
            "session.get" => self.session_get(p),
            "session.messages" => self.session_messages(p),
            "session.search" => self.session_search(p),
            "session.create" => self.session_create(p),
            "session.stats" => self.session_stats(),
            "session.record_usage" => self.session_record_usage(p),
            "cost.summary" => self.cost_summary(p),
            "cost.cache_hit_rate" => self.cost_cache_hit_rate(),

            // --- Libraries & integrations ---
            "tasks.list" => self.tasks_list(),
            "tasks.create" => self.library_create(p, "tasks"),
            "tasks.delete" => self.library_delete(p, "tasks", ".task.json"),
            "library.load_defaults" => self.library_load_defaults(),
            "definitions.list" => self.definitions_list(),
            "definitions.suggest" => self.definitions_suggest(p),
            "definitions.create" => self.library_create(p, "definitions"),
            "definitions.delete" => self.library_delete(p, "definitions", ".def.md"),
            "prompts.templates" => Ok(prompt_templates_payload()),
            "prompts.record" => self.prompts_record(p),
            "prompts.history" => self.prompts_history(p),
            "prompts.favorite" => self.prompts_favorite(p),
            "prompts.chain_run" => Ok(chain_run_payload(p)),
            "skills.list" => self.skills_list(p),
            "skills.create" => self.skills_create(p),
            "skills.install" => self.skills_install(p),
            "skills.uninstall" => self.skills_uninstall(p),
            "plugins.list" => self.plugins_list(),
            "plugins.install" => self.plugins_install(p),
            "plugins.uninstall" => self.plugins_uninstall(p),
            "plugins.set_enabled" => self.plugins_set_enabled(p),
            "plugins.marketplace_list" => self.plugins_marketplace_list(),
            "plugins.marketplace_add" => self.plugins_marketplace_add(p),
            "mcp.list" => self.mcp_list(p),
            "mcp.list_all" => self.mcp_list_all(p),
            "mcp.upsert" => self.mcp_upsert(p),
            "mcp.remove" => self.mcp_remove(p),
            "mcp.cli_remove" => self.mcp_cli_remove(p),
            "hooks.list" => self.hooks_list(p),
            "hooks.add" => self.hooks_add(p),
            "hooks.remove" => self.hooks_remove(p),
            "git.secret_scan" => self.git_secret_scan(p),
            "deploy.risk" => self.deploy_risk(p),

            // --- Editable files (CLAUDE.md, AGENTS.md, …) ---
            "file.read" => self.file_read(p),
            "file.write" => self.file_write(p),
            "file.create" => self.file_create(p),
            "file.rename" => self.file_rename(p),
            "file.move" => self.file_rename(p),
            "file.delete" => self.file_delete(p),
            "file.duplicate" => self.file_duplicate(p),
            "file.list" => self.file_list(p),
            "file.search" => self.file_search(p),
            "file.attach" => self.file_attach(p),

            other => Err(IpcFailure::not_found(format!("unknown method: {other}"))),
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
                    .map_err(|_| IpcFailure::invalid(format!("invalid trust_mode: {v}")))?;
            }
            if let Some(v) = p.get("default_model").and_then(Value::as_str) {
                next.default_model = serde_json::from_value(json!(v))
                    .map_err(|_| IpcFailure::invalid(format!("invalid default_model: {v}")))?;
            }
            if let Some(v) = p.get("daily_budget_usd").and_then(Value::as_f64) {
                next.daily_budget_usd = v.max(0.0);
            }
            if let Some(v) = p.get("context_token_budget").and_then(Value::as_u64) {
                next.context_token_budget = v as usize;
            }
            next.save(&self.inner.state_dir)
                .map_err(|e| IpcFailure::config(format!("failed to save settings: {e}")))?;
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

    // MARK: Context assembly & memory

    /// The user HOME (parent of the state dir `~/.claudestudio`).
    fn home_dir(&self) -> PathBuf {
        self.inner
            .state_dir
            .parent()
            .map(Path::to_path_buf)
            .unwrap_or_else(|| PathBuf::from("."))
    }

    /// Path of a memory document for the given scope.
    fn memory_path(&self, scope: &str, project: Option<&str>) -> PathBuf {
        match (scope, project) {
            ("project", Some(name)) => self
                .inner
                .state_dir
                .join("memory/projects")
                .join(format!("{name}.md")),
            _ => self.inner.state_dir.join("memory/global.md"),
        }
    }

    fn memory_get(&self, p: &Value) -> HandlerResult {
        let scope = p.get("scope").and_then(Value::as_str).unwrap_or("global");
        let project = p.get("project").and_then(Value::as_str);
        let path = self.memory_path(scope, project);
        let content = std::fs::read_to_string(&path).unwrap_or_default();
        Ok(json!({
            "scope": scope, "project": project, "path": path.to_string_lossy(),
            "exists": path.exists(), "content": content, "tokens": estimate_tokens(&content),
        }))
    }

    fn memory_set(&self, p: &Value) -> HandlerResult {
        let scope = p.get("scope").and_then(Value::as_str).unwrap_or("global");
        let project = p.get("project").and_then(Value::as_str);
        let content = req_str(p, "content")?;
        let path = self.memory_path(scope, project);
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).ok();
        }
        std::fs::write(&path, content).map_err(|e| e.to_string())?;
        Ok(json!({ "ok": true, "path": path.to_string_lossy(), "bytes": content.len() }))
    }

    fn memory_append(&self, p: &Value) -> HandlerResult {
        let scope = p.get("scope").and_then(Value::as_str).unwrap_or("global");
        let project = p.get("project").and_then(Value::as_str);
        let text = req_str(p, "text")?;
        let path = self.memory_path(scope, project);
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).ok();
        }
        let mut existing = std::fs::read_to_string(&path).unwrap_or_default();
        if !existing.is_empty() && !existing.ends_with('\n') {
            existing.push('\n');
        }
        existing.push_str(text);
        existing.push('\n');
        std::fs::write(&path, &existing).map_err(|e| e.to_string())?;
        Ok(json!({ "ok": true, "path": path.to_string_lossy(), "tokens": estimate_tokens(&existing) }))
    }

    /// Save a CLAUDE.md (default: global `~/.claude/CLAUDE.md`), backing up the
    /// previous version under `<state_dir>/backups/` first.
    fn claudemd_save(&self, p: &Value) -> HandlerResult {
        let path = match p.get("path").and_then(Value::as_str) {
            Some(pp) => PathBuf::from(pp),
            None => self.home_dir().join(".claude/CLAUDE.md"),
        };
        let content = req_str(p, "content")?;
        let mut backup_path = Value::Null;
        if path.exists() {
            let backups = self.inner.state_dir.join("backups");
            std::fs::create_dir_all(&backups).ok();
            let stamp = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_millis())
                .unwrap_or(0);
            let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("file");
            let bp = backups.join(format!("{name}.{stamp}.bak"));
            std::fs::copy(&path, &bp).ok();
            backup_path = json!(bp.to_string_lossy());
        }
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).ok();
        }
        std::fs::write(&path, content).map_err(|e| e.to_string())?;
        Ok(json!({
            "ok": true, "path": path.to_string_lossy(),
            "backup": backup_path, "tokens": estimate_tokens(content),
        }))
    }

    /// Assemble the real six-layer context from the actual files on disk, in
    /// fixed priority order, honoring per-layer enable toggles (`layers` map).
    fn context_assemble(&self, p: &Value) -> HandlerResult {
        let cwd = p.get("cwd").and_then(Value::as_str);
        let project = p.get("project").and_then(Value::as_str);
        let worktree = p.get("worktree").and_then(Value::as_str);
        let toggles = p.get("layers");
        let enabled = |label: &str| -> bool {
            toggles
                .and_then(|t| t.get(label))
                .and_then(Value::as_bool)
                .unwrap_or(true)
        };

        // Layer 1: global ~/.claude/CLAUDE.md
        let global =
            std::fs::read_to_string(self.home_dir().join(".claude/CLAUDE.md")).unwrap_or_default();
        // Layer 2: cross-project memory + per-project memory (same band)
        let mut memory =
            std::fs::read_to_string(self.inner.state_dir.join("memory/global.md")).unwrap_or_default();
        if let Some(name) = project {
            let pm = std::fs::read_to_string(self.memory_path("project", Some(name))).unwrap_or_default();
            if !pm.trim().is_empty() {
                if !memory.is_empty() {
                    memory.push_str("\n\n");
                }
                memory.push_str(&pm);
            }
        }
        // Layer 3: project CLAUDE.md
        let project_md = cwd
            .map(|c| std::fs::read_to_string(Path::new(c).join(".claude/CLAUDE.md")).unwrap_or_default())
            .unwrap_or_default();
        // Layer 6: worktree CLAUDE.md override
        let worktree_md = worktree
            .map(|w| std::fs::read_to_string(Path::new(w).join("CLAUDE.md")).unwrap_or_default())
            .unwrap_or_default();

        let raw: Vec<(LayerKind, String)> = vec![
            (LayerKind::GlobalClaudeMd, global),
            (LayerKind::CrossProjectMemory, memory),
            (LayerKind::ProjectClaudeMd, project_md),
            (LayerKind::VectorRetrieval, String::new()),
            (LayerKind::ActiveDefinitions, self.load_active_definitions(p.get("definitions"))),
            (LayerKind::WorktreeOverride, worktree_md),
        ];

        let total = self.inner.config.lock().unwrap().context_token_budget;
        let mut assembler = ContextAssembler::new(total);
        for (kind, content) in &raw {
            let req = if enabled(kind.label()) {
                estimate_tokens(content)
            } else {
                0
            };
            assembler = assembler.with_layer(*kind, req);
        }
        let budget = assembler.assemble();

        let mut assembled_text = String::new();
        let layers: Vec<Value> = raw
            .iter()
            .map(|(kind, content)| {
                let on = enabled(kind.label());
                let granted = budget.layer(*kind).map(|l| l.granted_tokens).unwrap_or(0);
                if on && !content.trim().is_empty() {
                    if !assembled_text.is_empty() {
                        assembled_text.push_str("\n\n");
                    }
                    assembled_text.push_str(&format!("# [{}]\n{}", kind.label(), content));
                }
                json!({
                    "layer": kind.label(),
                    "enabled": on,
                    "tokens": estimate_tokens(content),
                    "granted_tokens": granted,
                    "content": if on { content.clone() } else { String::new() },
                })
            })
            .collect();

        Ok(json!({
            "total_budget": total,
            "granted_total": budget.granted_total(),
            "order": LayerKind::ALL.iter().map(|k| k.label()).collect::<Vec<_>>(),
            "layers": layers,
            "assembled_text": assembled_text,
        }))
    }

    /// Concatenate the bodies (frontmatter stripped) of the named definitions
    /// from the library, so `context.assemble` can inject them as the active-
    /// definitions layer. `names` is the request's `definitions` value (array).
    fn load_active_definitions(&self, names: Option<&Value>) -> String {
        let wanted: Vec<String> = names
            .and_then(Value::as_array)
            .map(|a| a.iter().filter_map(|v| v.as_str().map(str::to_string)).collect())
            .unwrap_or_default();
        if wanted.is_empty() {
            return String::new();
        }
        let mut out = String::new();
        for (_path, content, _w) in self.library_files("definitions", ".def.md") {
            let fm = parse_frontmatter(&content);
            let name = fm.get("name").cloned().unwrap_or_default();
            if wanted.iter().any(|w| w.eq_ignore_ascii_case(&name)) {
                if !out.is_empty() {
                    out.push_str("\n\n");
                }
                out.push_str(&format!("## {name}\n{}", strip_frontmatter(&content).trim()));
            }
        }
        out
    }

    /// Suggest definitions whose name/tags/category match keywords in `prompt`.
    fn definitions_suggest(&self, p: &Value) -> HandlerResult {
        let prompt = req_str(p, "prompt")?.to_lowercase();
        let words: HashSet<&str> = prompt
            .split(|c: char| !c.is_alphanumeric())
            .filter(|w| w.len() >= 3)
            .collect();
        let mut suggestions = Vec::new();
        for (path, content, _w) in self.library_files("definitions", ".def.md") {
            let fm = parse_frontmatter(&content);
            let name = fm.get("name").cloned().unwrap_or_default();
            let tags = fm.get("tags").cloned().unwrap_or_default().to_lowercase();
            let category = fm.get("category").cloned().unwrap_or_default().to_lowercase();
            let hay = format!("{} {tags} {category}", name.to_lowercase());
            let score = words.iter().filter(|w| hay.contains(**w)).count();
            if score > 0 {
                suggestions.push((score, json!({ "name": name, "category": fm.get("category"), "score": score, "path": path })));
            }
        }
        suggestions.sort_by(|a, b| b.0.cmp(&a.0));
        let out: Vec<Value> = suggestions.into_iter().map(|(_, v)| v).collect();
        Ok(json!({ "suggestions": out }))
    }

    // MARK: Agentic OS — event rules (WENN-DANN)

    fn rules_path(&self) -> PathBuf {
        self.inner.state_dir.join("rules.json")
    }
    fn read_rules(&self) -> Vec<Value> {
        std::fs::read_to_string(self.rules_path())
            .ok()
            .and_then(|s| serde_json::from_str::<Vec<Value>>(&s).ok())
            .unwrap_or_default()
    }

    /// Add a WENN-DANN rule: `{ when: { event, branch? }, then: [action,…] }`.
    fn rules_add(&self, p: &Value) -> HandlerResult {
        let when = p.get("when").cloned().ok_or_else(|| IpcFailure::invalid("missing 'when'"))?;
        let then = p.get("then").cloned().ok_or_else(|| IpcFailure::invalid("missing 'then'"))?;
        let id = unique_id("rule");
        let mut rules = self.read_rules();
        rules.push(json!({ "id": id, "when": when, "then": then }));
        std::fs::write(self.rules_path(), serde_json::to_string_pretty(&rules).unwrap_or_default())
            .map_err(|e| e.to_string())?;
        Ok(json!({ "ok": true, "id": id, "count": rules.len() }))
    }

    fn rules_list(&self) -> HandlerResult {
        Ok(json!({ "rules": self.read_rules() }))
    }

    /// Evaluate an incoming event against all rules; return the fired actions.
    fn rules_eval(&self, p: &Value) -> HandlerResult {
        let event = req_str(p, "event")?;
        let branch = p.get("branch").and_then(Value::as_str);
        let mut fired = Vec::new();
        for rule in self.read_rules() {
            let w = &rule["when"];
            if w.get("event").and_then(Value::as_str) != Some(event) {
                continue;
            }
            // Optional branch condition: only matches when equal (if specified).
            if let Some(rb) = w.get("branch").and_then(Value::as_str) {
                if Some(rb) != branch {
                    continue;
                }
            }
            fired.push(json!({ "rule_id": rule["id"], "actions": rule["then"] }));
        }
        Ok(json!({ "event": event, "branch": branch, "fired": fired }))
    }

    // MARK: Security & permissions

    fn audit_log_path(&self) -> PathBuf {
        self.inner.state_dir.join("audit.log")
    }
    fn matrix_path(&self) -> PathBuf {
        self.inner.state_dir.join("permissions.json")
    }

    fn read_matrix(&self) -> Value {
        std::fs::read_to_string(self.matrix_path())
            .ok()
            .and_then(|s| serde_json::from_str::<Value>(&s).ok())
            .unwrap_or_else(|| json!({}))
    }

    fn permissions_matrix_get(&self) -> HandlerResult {
        Ok(json!({ "matrix": self.read_matrix() }))
    }

    fn permissions_matrix_set(&self, p: &Value) -> HandlerResult {
        let tool = req_str(p, "tool")?;
        let decision = req_str(p, "decision")?;
        if !["allow", "ask", "deny", "default"].contains(&decision) {
            return Err(IpcFailure::invalid("decision must be allow|ask|deny|default"));
        }
        let mut m = self.read_matrix();
        if decision == "default" {
            // Remove the per-tool override, falling back to trust-mode logic.
            if let Some(obj) = m.as_object_mut() {
                obj.remove(tool);
            }
        } else {
            m[tool] = json!(decision);
        }
        std::fs::write(self.matrix_path(), serde_json::to_string_pretty(&m).unwrap_or_default())
            .map_err(|e| e.to_string())?;
        Ok(json!({ "ok": true, "matrix": m }))
    }

    fn permissions_audit_log(&self, p: &Value) -> HandlerResult {
        let limit = p.get("limit").and_then(Value::as_u64).unwrap_or(50) as usize;
        let content = std::fs::read_to_string(self.audit_log_path()).unwrap_or_default();
        let entries: Vec<Value> = content
            .lines()
            .rev()
            .take(limit)
            .filter_map(|l| serde_json::from_str::<Value>(l).ok())
            .collect();
        Ok(json!({ "entries": entries }))
    }

    /// Decide whether a tool action is allowed / needs confirmation / denied,
    /// then record the decision in the audit log (F287-F299).
    fn permissions_check(&self, p: &Value) -> HandlerResult {
        let mode = match p.get("trust_mode").and_then(Value::as_str) {
            Some(m) => m.to_lowercase(),
            None => format!("{:?}", self.trust_mode()).to_lowercase(),
        };
        let action = req_str(p, "action")?;
        let command = p.get("command").and_then(Value::as_str).unwrap_or("");
        let path = p.get("path").and_then(Value::as_str).unwrap_or("");
        let project_root = p.get("project_root").and_then(Value::as_str).unwrap_or("");
        let branch = p.get("branch").and_then(Value::as_str).unwrap_or("");
        let subagent = p.get("subagent").and_then(Value::as_bool).unwrap_or(false);

        let matrix = self.read_matrix();
        let matrix_decision = matrix.get(action).and_then(Value::as_str);

        let (mut decision, reason, gate) = classify_permission(
            &mode, action, command, path, project_root, branch, matrix_decision,
        );
        // Subagents can never ask interactively: an 'ask' becomes 'deny' (F295).
        if subagent && decision == "ask" {
            decision = "deny".to_string();
        }

        // Audit EVERY decision, independent of the trust mode (F298).
        let entry = json!({
            "timestamp": now_millis(), "action": action, "command": command,
            "path": path, "branch": branch, "mode": mode, "subagent": subagent,
            "decision": decision, "gate": gate, "reason": reason,
        });
        if let Ok(mut f) = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(self.audit_log_path())
        {
            use std::io::Write;
            let _ = writeln!(f, "{entry}");
        }

        Ok(json!({ "decision": decision, "reason": reason, "gate": gate, "mode": mode }))
    }

    // MARK: Sessions

    fn session_list(&self, p: &Value) -> HandlerResult {
        let limit = p.get("limit").and_then(Value::as_i64).unwrap_or(100);
        let offset = p.get("offset").and_then(Value::as_i64).unwrap_or(0);
        let project = p.get("project").and_then(Value::as_str);
        let model = p.get("model").and_then(Value::as_str);
        let since = p.get("since").and_then(Value::as_i64);
        let until = p.get("until").and_then(Value::as_i64);
        let has_filter = project.is_some() || model.is_some() || since.is_some() || until.is_some();

        let store = self.inner.sessions.lock().unwrap();
        // When filtering, fetch a wider window then narrow it in-process (F158).
        let fetch = if has_filter { limit.max(1000) } else { limit };
        let sessions = store.list_sessions(fetch, offset).map_err(session_failure)?;
        let mut arr = serde_json::to_value(sessions).unwrap_or_else(|_| json!([]));
        if let Some(list) = arr.as_array_mut() {
            list.retain(|s| {
                if let Some(pj) = project {
                    if !s.get("cwd").and_then(Value::as_str).unwrap_or("").contains(pj) {
                        return false;
                    }
                }
                if let Some(m) = model {
                    if s.get("model").and_then(Value::as_str) != Some(m) {
                        return false;
                    }
                }
                let ts = s.get("created_at").and_then(Value::as_i64).unwrap_or(0);
                if matches!(since, Some(sc) if ts < sc) {
                    return false;
                }
                if matches!(until, Some(uc) if ts > uc) {
                    return false;
                }
                true
            });
            list.truncate(limit.max(0) as usize);
        }
        Ok(json!({ "sessions": arr }))
    }

    fn session_get(&self, p: &Value) -> HandlerResult {
        let id = p.get("id").and_then(Value::as_str).ok_or_else(|| IpcFailure::invalid("missing 'id'"))?;
        let store = self.inner.sessions.lock().unwrap();
        let session = store.get_session(id).map_err(session_failure)?;
        let usage = store.session_usage(id).map_err(session_failure)?;
        let mut v = serde_json::to_value(session).unwrap_or(Value::Null);
        if let Some(obj) = v.as_object_mut() {
            obj.insert("usage".into(), serde_json::to_value(usage).unwrap_or(Value::Null));
        }
        Ok(v)
    }

    /// Record a token/USD usage line against a session (cost/telemetry).
    fn session_record_usage(&self, p: &Value) -> HandlerResult {
        let u = NewUsage {
            session_id: req_str(p, "session_id")?.to_string(),
            model: p.get("model").and_then(Value::as_str).map(str::to_string),
            agent: p.get("agent").and_then(Value::as_str).map(str::to_string),
            project: p.get("project").and_then(Value::as_str).map(str::to_string),
            input_tokens: p.get("input_tokens").and_then(Value::as_i64).unwrap_or(0),
            output_tokens: p.get("output_tokens").and_then(Value::as_i64).unwrap_or(0),
            cache_read_tokens: p.get("cache_read_tokens").and_then(Value::as_i64).unwrap_or(0),
            cache_creation_tokens: p.get("cache_creation_tokens").and_then(Value::as_i64).unwrap_or(0),
            cost_usd: p.get("cost_usd").and_then(Value::as_f64).unwrap_or(0.0),
        };
        let store = self.inner.sessions.lock().unwrap();
        let id = store.record_usage(&u).map_err(session_failure)?;
        Ok(json!({ "ok": true, "id": id }))
    }

    /// Cost dashboard: aggregate USD + token breakdown grouped by model/agent/project.
    fn cost_summary(&self, p: &Value) -> HandlerResult {
        let group_by = p.get("group_by").and_then(Value::as_str).unwrap_or("model");
        let store = self.inner.sessions.lock().unwrap();
        let groups = store.usage_summary(group_by).map_err(session_failure)?;
        let total: f64 = groups.iter().map(|g| g.cost_usd).sum();
        let most = store.most_expensive_session().map_err(session_failure)?;
        Ok(json!({
            "group_by": group_by,
            "groups": serde_json::to_value(&groups).unwrap_or(Value::Null),
            "total_cost_usd": total,
            "most_expensive_session": match most {
                Some((sid, c)) => json!({ "session_id": sid, "cost_usd": c }),
                None => Value::Null,
            },
        }))
    }

    fn cost_cache_hit_rate(&self) -> HandlerResult {
        let store = self.inner.sessions.lock().unwrap();
        let rate = store.cache_hit_rate().map_err(session_failure)?;
        Ok(json!({ "cache_hit_rate": rate }))
    }

    // MARK: Prompt Studio — history, favorites, chains

    fn prompt_history_path(&self) -> PathBuf {
        self.inner.state_dir.join("prompt_history.jsonl")
    }
    fn prompt_favs_path(&self) -> PathBuf {
        self.inner.state_dir.join("prompt_favorites.json")
    }
    fn read_favs(&self) -> HashSet<String> {
        std::fs::read_to_string(self.prompt_favs_path())
            .ok()
            .and_then(|s| serde_json::from_str::<Vec<String>>(&s).ok())
            .map(|v| v.into_iter().collect())
            .unwrap_or_default()
    }

    /// Append a prompt to the chronological history (F244).
    fn prompts_record(&self, p: &Value) -> HandlerResult {
        let prompt = req_str(p, "prompt")?;
        let id = unique_id("p");
        let entry = json!({
            "id": id, "timestamp": now_millis(), "prompt": prompt,
            "agent": p.get("agent").and_then(Value::as_str),
            "tokens": p.get("tokens").and_then(Value::as_i64),
            "result": p.get("result").and_then(Value::as_str),
        });
        if let Ok(mut f) = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(self.prompt_history_path())
        {
            use std::io::Write;
            let _ = writeln!(f, "{entry}");
        }
        Ok(json!({ "ok": true, "id": id }))
    }

    /// Prompt history newest-first; optional full-text `query` and `favorites_only` (F244/F245).
    fn prompts_history(&self, p: &Value) -> HandlerResult {
        let limit = p.get("limit").and_then(Value::as_u64).unwrap_or(50) as usize;
        let query = p.get("query").and_then(Value::as_str).map(|s| s.to_lowercase());
        let favs_only = p.get("favorites_only").and_then(Value::as_bool).unwrap_or(false);
        let favs = self.read_favs();
        let content = std::fs::read_to_string(self.prompt_history_path()).unwrap_or_default();
        let mut entries: Vec<Value> = content
            .lines()
            .rev()
            .filter_map(|l| serde_json::from_str::<Value>(l).ok())
            .filter(|e| {
                let id = e.get("id").and_then(Value::as_str).unwrap_or("");
                if favs_only && !favs.contains(id) {
                    return false;
                }
                if let Some(q) = &query {
                    let hay = format!(
                        "{} {}",
                        e.get("prompt").and_then(Value::as_str).unwrap_or(""),
                        e.get("result").and_then(Value::as_str).unwrap_or("")
                    )
                    .to_lowercase();
                    if !hay.contains(q) {
                        return false;
                    }
                }
                true
            })
            .collect();
        for e in entries.iter_mut() {
            let fav = e.get("id").and_then(Value::as_str).map(|id| favs.contains(id)).unwrap_or(false);
            if let Some(o) = e.as_object_mut() {
                o.insert("favorite".into(), json!(fav));
            }
        }
        entries.truncate(limit);
        Ok(json!({ "entries": entries }))
    }

    /// Mark/unmark a history entry as a favorite (F245).
    fn prompts_favorite(&self, p: &Value) -> HandlerResult {
        let id = req_str(p, "id")?.to_string();
        let fav = p.get("favorite").and_then(Value::as_bool).unwrap_or(true);
        let mut favs = self.read_favs();
        if fav {
            favs.insert(id.clone());
        } else {
            favs.remove(&id);
        }
        let v: Vec<&String> = favs.iter().collect();
        std::fs::write(self.prompt_favs_path(), serde_json::to_string(&v).unwrap_or_default())
            .map_err(|e| e.to_string())?;
        Ok(json!({ "ok": true, "id": id, "favorite": fav }))
    }

    fn session_messages(&self, p: &Value) -> HandlerResult {
        let id = p.get("id").and_then(Value::as_str).ok_or_else(|| IpcFailure::invalid("missing 'id'"))?;
        let store = self.inner.sessions.lock().unwrap();
        let messages = store.list_messages(id).map_err(session_failure)?;
        Ok(json!({ "messages": messages }))
    }

    fn session_search(&self, p: &Value) -> HandlerResult {
        let query = p
            .get("query")
            .and_then(Value::as_str)
            .ok_or_else(|| IpcFailure::invalid("missing 'query'"))?;
        // Small default keeps results token-frugal (title + short snippet each).
        let limit = p.get("limit").and_then(Value::as_i64).unwrap_or(8);
        // Semantic-first: rank by meaning via the neural index, falling back to
        // keyword FTS when the semantic index has nothing (e.g. before the
        // model has finished backfilling).
        let (embedder, tag) = self.current_embedder();
        let query_vec = embedder.embed(query);
        let store = self.inner.sessions.lock().unwrap();
        let mut hits = store
            .vector_search(&query_vec, &tag, limit)
            .map_err(session_failure)?;
        if hits.is_empty() {
            hits = store
                .full_text_search(query, limit)
                .map_err(session_failure)?;
        }
        Ok(json!({ "hits": hits }))
    }

    fn session_create(&self, p: &Value) -> HandlerResult {
        let title = p
            .get("title")
            .and_then(Value::as_str)
            .ok_or_else(|| IpcFailure::invalid("missing 'title'"))?;
        let cwd = p
            .get("cwd")
            .and_then(Value::as_str)
            .ok_or_else(|| IpcFailure::invalid("missing 'cwd'"))?;
        let mut ns = NewSession::new(title, cwd);
        ns.branch = p.get("branch").and_then(Value::as_str).map(str::to_string);
        ns.model = p.get("model").and_then(Value::as_str).map(str::to_string);
        let store = self.inner.sessions.lock().unwrap();
        let id = store.insert_session(&ns).map_err(session_failure)?;
        let _ = self.inner.event_bus.publish(SystemEvent::TaskOneClick);
        Ok(json!({ "id": id }))
    }

    fn session_stats(&self) -> HandlerResult {
        let store = self.inner.sessions.lock().unwrap();
        let stats = store.stats().map_err(session_failure)?;
        let cache = store.cache_hit_rate().map_err(session_failure)?;
        let most = store.most_expensive_session().map_err(session_failure)?;
        let total_cost: f64 = store
            .usage_summary("model")
            .map_err(session_failure)?
            .iter()
            .map(|g| g.cost_usd)
            .sum();
        let mut v = serde_json::to_value(stats).unwrap_or(Value::Null);
        if let Some(obj) = v.as_object_mut() {
            obj.insert("total_cost_usd".into(), json!(total_cost));
            obj.insert("cache_hit_rate".into(), json!(cache));
            obj.insert(
                "most_expensive_session".into(),
                match most {
                    Some((sid, c)) => json!({ "session_id": sid, "cost_usd": c }),
                    None => Value::Null,
                },
            );
        }
        Ok(v)
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
            .ok_or_else(|| IpcFailure::invalid("missing 'path'"))?;
        // Reject by size *before* reading, so a multi-GB path can't force the
        // whole file into memory before the cap is enforced. Symlinks/specials
        // that report a small (or zero) size fall through to read_to_string,
        // whose own buffering is then the only exposure.
        match std::fs::metadata(path) {
            Ok(meta) if meta.is_file() && meta.len() > MAX_BYTES => {
                return Err(IpcFailure::invalid("file too large to edit"));
            }
            _ => {}
        }
        match std::fs::read_to_string(path) {
            Ok(content) => {
                if content.len() as u64 > MAX_BYTES {
                    return Err(IpcFailure::invalid("file too large to edit"));
                }
                Ok(json!({ "path": path, "content": content, "exists": true }))
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                Ok(json!({ "path": path, "content": "", "exists": false }))
            }
            Err(e) => Err(IpcFailure::internal(e.to_string())),
        }
    }

    /// Write a UTF-8 text file, creating parent directories as needed.
    fn file_write(&self, p: &Value) -> HandlerResult {
        let path = p
            .get("path")
            .and_then(Value::as_str)
            .ok_or_else(|| IpcFailure::invalid("missing 'path'"))?;
        let content = p
            .get("content")
            .and_then(Value::as_str)
            .ok_or_else(|| IpcFailure::invalid("missing 'content'"))?;
        if let Some(parent) = std::path::Path::new(path).parent() {
            std::fs::create_dir_all(parent).ok();
        }
        std::fs::write(path, content).map_err(|e| e.to_string())?;
        Ok(json!({ "path": path, "ok": true, "bytes": content.len() }))
    }

    /// Create a new file (error if it already exists). Optional `content`.
    fn file_create(&self, p: &Value) -> HandlerResult {
        let path = req_str(p, "path")?;
        if Path::new(path).exists() {
            return Err(IpcFailure::invalid(format!("already exists: {path}")));
        }
        if let Some(parent) = Path::new(path).parent() {
            std::fs::create_dir_all(parent).ok();
        }
        let content = p.get("content").and_then(Value::as_str).unwrap_or("");
        std::fs::write(path, content).map_err(|e| e.to_string())?;
        Ok(json!({ "path": path, "ok": true, "created": true }))
    }

    /// Rename / move a file or directory from `from` to `to`.
    fn file_rename(&self, p: &Value) -> HandlerResult {
        let from = req_str(p, "from")?;
        let to = req_str(p, "to")?;
        if !Path::new(from).exists() {
            return Err(IpcFailure::not_found(format!("source not found: {from}")));
        }
        if let Some(parent) = Path::new(to).parent() {
            std::fs::create_dir_all(parent).ok();
        }
        std::fs::rename(from, to).map_err(|e| e.to_string())?;
        Ok(json!({ "from": from, "to": to, "ok": true }))
    }

    /// Delete a file or directory (recursively for directories).
    fn file_delete(&self, p: &Value) -> HandlerResult {
        let path = req_str(p, "path")?;
        let meta = std::fs::metadata(path)
            .map_err(|_| IpcFailure::not_found(format!("not found: {path}")))?;
        if meta.is_dir() {
            std::fs::remove_dir_all(path).map_err(|e| e.to_string())?;
        } else {
            std::fs::remove_file(path).map_err(|e| e.to_string())?;
        }
        Ok(json!({ "path": path, "ok": true, "deleted": true }))
    }

    /// Duplicate a file to `to` (defaults to "<name> copy<ext>").
    fn file_duplicate(&self, p: &Value) -> HandlerResult {
        let from = req_str(p, "from")?;
        let src = Path::new(from);
        if !src.is_file() {
            return Err(IpcFailure::invalid(format!("not a file: {from}")));
        }
        let to = match p.get("to").and_then(Value::as_str) {
            Some(t) => t.to_string(),
            None => {
                let stem = src.file_stem().and_then(|s| s.to_str()).unwrap_or("copy");
                let ext = src.extension().and_then(|s| s.to_str());
                let parent = src.parent().unwrap_or_else(|| Path::new("."));
                let name = match ext {
                    Some(e) => format!("{stem} copy.{e}"),
                    None => format!("{stem} copy"),
                };
                parent.join(name).to_string_lossy().to_string()
            }
        };
        std::fs::copy(from, &to).map_err(|e| e.to_string())?;
        Ok(json!({ "from": from, "to": to, "ok": true }))
    }

    /// List the immediate entries of a directory, flagging protected files.
    fn file_list(&self, p: &Value) -> HandlerResult {
        let path = req_str(p, "path")?;
        let rd = std::fs::read_dir(path)
            .map_err(|e| IpcFailure::invalid(format!("cannot list {path}: {e}")))?;
        let mut entries = Vec::new();
        for e in rd.flatten() {
            let name = e.file_name().to_string_lossy().to_string();
            let is_dir = e.file_type().map(|t| t.is_dir()).unwrap_or(false);
            let full = e.path().to_string_lossy().to_string();
            entries.push(json!({
                "name": name,
                "path": full,
                "is_dir": is_dir,
                "protected": is_protected_path(&full),
            }));
        }
        entries.sort_by(|a, b| {
            let ad = a["is_dir"].as_bool().unwrap_or(false);
            let bd = b["is_dir"].as_bool().unwrap_or(false);
            bd.cmp(&ad).then(a["name"].as_str().cmp(&b["name"].as_str()))
        });
        Ok(json!({ "path": path, "entries": entries }))
    }

    /// Full-text search over a project tree (ripgrep, falling back to grep).
    fn file_search(&self, p: &Value) -> HandlerResult {
        let cwd = req_str(p, "cwd")?;
        let query = req_str(p, "query")?;
        let (bin, args): (&str, Vec<String>) = if which_ok("rg") {
            ("rg", vec![
                "--line-number".into(), "--no-heading".into(), "--color".into(), "never".into(),
                "--max-count".into(), "50".into(), query.into(), ".".into(),
            ])
        } else {
            ("grep", vec!["-rniI".into(), "--line-number".into(), query.into(), ".".into()])
        };
        let out = std::process::Command::new(bin)
            .current_dir(cwd)
            .args(&args)
            .output()
            .map_err(|e| IpcFailure::internal(e.to_string()))?;
        let text = String::from_utf8_lossy(&out.stdout);
        let mut matches = Vec::new();
        for line in text.lines().take(200) {
            // rg/grep format: path:line:content
            let mut it = line.splitn(3, ':');
            if let (Some(fp), Some(ln), Some(content)) = (it.next(), it.next(), it.next()) {
                matches.push(json!({ "path": fp, "line": ln.parse::<u64>().unwrap_or(0), "text": content }));
            }
        }
        Ok(json!({ "cwd": cwd, "query": query, "tool": bin, "matches": matches }))
    }

    /// Attach a file's content to a session — but NEVER for protected paths
    /// (.env, secrets/, *.key/*.pem, credentials). This is the guard that keeps
    /// secrets from being sent to Claude.
    fn file_attach(&self, p: &Value) -> HandlerResult {
        let path = req_str(p, "path")?;
        if is_protected_path(path) {
            return Err(IpcFailure::new(
                ErrorCode::InvalidParameter,
                format!("protected path refused — not sent to Claude: {path}"),
            ));
        }
        let content = std::fs::read_to_string(path)
            .map_err(|e| IpcFailure::not_found(format!("cannot read {path}: {e}")))?;
        Ok(json!({ "path": path, "content": content, "protected": false, "attached": true }))
    }

    // MARK: Hooks (editor) & deployment helpers

    /// Add a hook to `<cwd>/.claude/settings.json` in the standard Claude Code
    /// format: `hooks[event] = [{ matcher, hooks: [{ type, command }] }]` (F257).
    fn hooks_add(&self, p: &Value) -> HandlerResult {
        let cwd = req_str(p, "cwd")?;
        let event = req_str(p, "event")?;
        let matcher = p.get("matcher").and_then(Value::as_str).unwrap_or("*").to_string();
        let command = req_str(p, "command")?;
        let valid = [
            "PreToolUse", "PostToolUse", "Notification", "Stop", "SubagentStop",
            "WorktreeCreate", "WorktreeRemove",
        ];
        if !valid.contains(&event) {
            return Err(IpcFailure::invalid(format!("unknown hook event: {event}")));
        }
        let path = Path::new(cwd).join(".claude/settings.json");
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).ok();
        }
        let mut root: Value = std::fs::read_to_string(&path)
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_else(|| json!({}));
        if !root.is_object() {
            root = json!({});
        }
        let hooks = root
            .as_object_mut()
            .unwrap()
            .entry("hooks")
            .or_insert_with(|| json!({}));
        let arr = hooks
            .as_object_mut()
            .unwrap()
            .entry(event.to_string())
            .or_insert_with(|| json!([]));
        arr.as_array_mut().unwrap().push(json!({
            "matcher": matcher,
            "hooks": [{ "type": "command", "command": command }],
        }));
        std::fs::write(&path, serde_json::to_string_pretty(&root).unwrap_or_default())
            .map_err(|e| e.to_string())?;
        Ok(json!({ "ok": true, "event": event, "matcher": matcher, "path": path.to_string_lossy() }))
    }

    /// Remove hooks for an event (optionally only those matching `matcher`).
    fn hooks_remove(&self, p: &Value) -> HandlerResult {
        let cwd = req_str(p, "cwd")?;
        let event = req_str(p, "event")?;
        let matcher = p.get("matcher").and_then(Value::as_str);
        let path = Path::new(cwd).join(".claude/settings.json");
        let mut root: Value = std::fs::read_to_string(&path)
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_else(|| json!({}));
        let mut removed = 0;
        if let Some(hooks) = root.get_mut("hooks").and_then(Value::as_object_mut) {
            if let Some(arr) = hooks.get_mut(event).and_then(Value::as_array_mut) {
                let before = arr.len();
                arr.retain(|e| matcher.is_some_and(|m| e.get("matcher").and_then(Value::as_str) != Some(m)));
                removed = before - arr.len();
            }
        }
        std::fs::write(&path, serde_json::to_string_pretty(&root).unwrap_or_default())
            .map_err(|e| e.to_string())?;
        Ok(json!({ "ok": true, "removed": removed }))
    }

    /// Scan a repo's full git history for committed secrets (F075).
    fn git_secret_scan(&self, p: &Value) -> HandlerResult {
        let cwd = req_str(p, "cwd")?;
        let out = std::process::Command::new("git")
            .current_dir(cwd)
            .args(["log", "-p", "--all", "--no-color"])
            .output()
            .map_err(|e| IpcFailure::internal(e.to_string()))?;
        let text = String::from_utf8_lossy(&out.stdout);
        let mut findings = Vec::new();
        for line in text.lines() {
            if !line.starts_with('+') {
                continue;
            }
            if let Some(kind) = secret_kind(&line[1..]) {
                findings.push(json!({ "kind": kind, "snippet": redact(&line[1..]) }));
            }
        }
        Ok(json!({ "cwd": cwd, "findings": findings, "found": !findings.is_empty() }))
    }

    /// Estimate deployment risk (Low/Medium/High) from the diff vs HEAD (F275).
    fn deploy_risk(&self, p: &Value) -> HandlerResult {
        let cwd = req_str(p, "cwd")?;
        let run = |args: &[&str]| -> String {
            std::process::Command::new("git")
                .current_dir(cwd)
                .args(args)
                .output()
                .map(|o| String::from_utf8_lossy(&o.stdout).to_string())
                .unwrap_or_default()
        };
        let mut numstat = run(&["diff", "--numstat", "HEAD"]);
        if numstat.trim().is_empty() {
            numstat = run(&["diff", "--numstat", "--cached"]);
        }
        let (mut files, mut add, mut del) = (0u64, 0i64, 0i64);
        let mut reasons: Vec<String> = Vec::new();
        for l in numstat.lines() {
            let mut it = l.split('\t');
            let a = it.next().unwrap_or("0").parse::<i64>().unwrap_or(0);
            let d = it.next().unwrap_or("0").parse::<i64>().unwrap_or(0);
            let f = it.next().unwrap_or("");
            files += 1;
            add += a;
            del += d;
            let fl = f.to_lowercase();
            if fl.contains("migration") || fl.contains("schema") {
                reasons.push(format!("DB-Migration: {f}"));
            }
            if fl.ends_with(".lock") || fl.contains("package.json") || fl.contains("cargo.toml") {
                reasons.push(format!("Dependency-Änderung: {f}"));
            }
            if fl.contains(".env") || fl.contains("/config") {
                reasons.push(format!("Konfig-Änderung: {f}"));
            }
        }
        let big = add + del > 500 || files > 20;
        let risk = if !reasons.is_empty() || big {
            "high"
        } else if add + del > 100 || files > 5 {
            "medium"
        } else {
            "low"
        };
        if big {
            reasons.push(format!("großer Diff: {files} Dateien, +{add}/-{del}"));
        }
        Ok(json!({ "risk": risk, "files_changed": files, "additions": add, "deletions": del, "reasons": reasons }))
    }

    // MARK: Git write operations (commit assistant, worktrees, merge)

    /// Commit staged+unstaged changes. With `message` uses it verbatim; without,
    /// generates a Conventional Commit message from the staged diff (assistant).
    async fn git_commit(&self, p: &Value) -> HandlerResult {
        let git = Self::git_for(p)?;
        let message = match p.get("message").and_then(Value::as_str) {
            Some(m) if !m.trim().is_empty() => m.to_string(),
            _ => {
                let mut diff = git.diff(true).await.map_err(|e| e.to_string())?;
                if diff.trim().is_empty() {
                    diff = git.diff(false).await.map_err(|e| e.to_string())?;
                }
                git.generate_conventional_commit_message(&diff)
            }
        };
        let hash = git.commit(&message).await.map_err(|e| e.to_string())?;
        Ok(json!({ "ok": true, "hash": hash, "message": message }))
    }

    /// Generate (but do not apply) a Conventional Commit message from the diff.
    async fn git_commit_message(&self, p: &Value) -> HandlerResult {
        let git = Self::git_for(p)?;
        let mut diff = git.diff(true).await.map_err(|e| e.to_string())?;
        if diff.trim().is_empty() {
            diff = git.diff(false).await.map_err(|e| e.to_string())?;
        }
        let message = git.generate_conventional_commit_message(&diff);
        Ok(json!({ "message": message }))
    }

    /// Create a worktree at `path` on a new `branch`.
    async fn worktree_add(&self, p: &Value) -> HandlerResult {
        let git = Self::git_for(p)?;
        let path = req_str(p, "path")?;
        let branch = req_str(p, "branch")?;
        git.create_worktree(Path::new(path), branch)
            .await
            .map_err(|e| e.to_string())?;
        Ok(json!({ "ok": true, "path": path, "branch": branch }))
    }

    /// Remove the worktree at `path`.
    async fn worktree_remove(&self, p: &Value) -> HandlerResult {
        let git = Self::git_for(p)?;
        let path = req_str(p, "path")?;
        git.remove_worktree(Path::new(path))
            .await
            .map_err(|e| e.to_string())?;
        Ok(json!({ "ok": true, "path": path }))
    }

    /// Merge `branch` into the current branch.
    async fn worktree_merge(&self, p: &Value) -> HandlerResult {
        let git = Self::git_for(p)?;
        let branch = req_str(p, "branch")?;
        let head = git.merge(branch).await.map_err(|e| e.to_string())?;
        Ok(json!({ "ok": true, "head": head, "merged": branch }))
    }

    // MARK: Live Claude session recording (called by the connection forwarder)

    /// Insert a session record for a live run and return its id (empty on error).
    pub fn create_run_session(&self, title: &str, cwd: &str, model: &str) -> String {
        let mut ns = NewSession::new(title, cwd);
        ns.model = Some(model.to_string());
        let store = self.inner.sessions.lock().unwrap();
        store.insert_session(&ns).unwrap_or_default()
    }

    /// Append a transcript message (best-effort) and add it to the semantic
    /// index so it is recallable by meaning, not just keywords.
    pub fn record_message(&self, session_id: &str, role: &str, content: &str) {
        // Embed BEFORE taking the lock — the neural forward pass is the slow part
        // and must not serialize other DB work. The embedding depends only on the
        // content, not on the row id, so it can be computed up front (A15).
        let (embedder, tag) = self.current_embedder();
        let vector = embedder.embed(content);
        let snippet = embedding::snippet(content);

        // Append + upsert under a SINGLE critical section: previously this took
        // the sessions lock twice (append, drop, re-acquire for upsert), which
        // both doubled lock churn and let another writer interleave between the
        // message insert and its embedding.
        let store = self.inner.sessions.lock().unwrap();
        let id = store
            .append_message(&NewMessage::new(session_id, role, content))
            .unwrap_or_default();
        if id.is_empty() {
            return;
        }
        let _ = store.upsert_embedding(
            &id,
            session_id,
            HitSource::Message,
            &snippet,
            &tag,
            &vector,
            now_millis(),
        );
    }

    /// Embed transcript items that still lack a vector for the active model —
    /// both messages (what was *said*) and tool calls (what Claude *did*, with
    /// their captured output). Returns how many were embedded. Idempotent.
    pub fn backfill_embeddings(&self, max_items: i64) -> usize {
        let (embedder, tag) = self.current_embedder();
        self.backfill_kind(&*embedder, &tag, HitSource::Message, max_items)
            + self.backfill_kind(&*embedder, &tag, HitSource::Tool, max_items)
    }

    /// Embed all un-embedded items of one kind in two phases, so the lock is
    /// taken exactly twice instead of once-per-item (A15). Phase 1 fetches the
    /// pending rows under the lock (held briefly); phase 2 runs every embed (the
    /// slow neural pass) with NO lock held; phase 3 upserts the whole batch under
    /// a single lock. Previously each item re-acquired the lock, so a backfill of
    /// N items took N+1 lock acquisitions and interleaved with every other writer.
    fn backfill_kind(
        &self,
        embedder: &dyn Embedder,
        tag: &str,
        source: HitSource,
        max_items: i64,
    ) -> usize {
        // Phase 1: snapshot the pending rows.
        let pending = {
            let store = self.inner.sessions.lock().unwrap();
            let res = match source {
                HitSource::Tool => store.unembedded_tool_calls(tag, max_items),
                _ => store.unembedded_messages(tag, max_items),
            };
            res.unwrap_or_default()
        };
        if pending.is_empty() {
            return 0;
        }

        // Phase 2: embed everything off-lock (the expensive part).
        let embedded: Vec<(String, String, String, Vec<f32>)> = pending
            .into_iter()
            .map(|item| {
                let vector = embedder.embed(&item.content);
                let snippet = embedding::snippet(&item.content);
                (item.owner_id, item.session_id, snippet, vector)
            })
            .collect();

        // Phase 3: upsert the batch under one critical section.
        let ts = now_millis();
        let store = self.inner.sessions.lock().unwrap();
        let mut count = 0;
        for (owner_id, session_id, snippet, vector) in &embedded {
            if store
                .upsert_embedding(owner_id, session_id, source, snippet, tag, vector, ts)
                .is_ok()
            {
                count += 1;
            }
        }
        count
    }

    /// Append a tool-call record (best-effort), tagged with the `claude`
    /// tool-use id so its output can be matched in when the result streams back.
    /// Embedding happens later in the post-run backfill, off the hot path.
    pub fn record_tool_call(
        &self,
        session_id: &str,
        tool_use_id: Option<&str>,
        tool: &str,
        input: Value,
    ) {
        let mut call = NewToolCall::new(session_id, tool, input);
        if let Some(tuid) = tool_use_id {
            call = call.with_tool_use_id(tuid);
        }
        let store = self.inner.sessions.lock().unwrap();
        let _ = store.append_tool_call(&call);
    }

    /// Capture a tool's output, matched to its call by the `claude` tool-use id.
    /// The full output is stored for the archive; it becomes semantically
    /// searchable (as a short snippet) via the backfill. Best-effort.
    pub fn record_tool_result(
        &self,
        session_id: &str,
        tool_use_id: Option<&str>,
        output: &str,
        success: bool,
    ) {
        let Some(tuid) = tool_use_id else { return };
        let store = self.inner.sessions.lock().unwrap();
        let _ = store.set_tool_output(session_id, tuid, output, success);
    }

    /// Append a lifecycle event (best-effort).
    pub fn record_run_event(&self, session_id: &str, kind: &str) {
        let store = self.inner.sessions.lock().unwrap();
        let _ = store.append_event(&NewEvent::new(session_id, kind));
    }

    /// Persist the `claude` CLI's session id for a run so the archive can resume
    /// it later (best-effort).
    pub fn set_claude_session_id(&self, session_id: &str, claude_session_id: &str) {
        let store = self.inner.sessions.lock().unwrap();
        let _ = store.set_claude_session_id(session_id, claude_session_id);
    }

    // MARK: Git (operates on the project directory named in `cwd`)

    fn git_for(p: &Value) -> std::result::Result<SystemGit, IpcFailure> {
        let cwd = p
            .get("cwd")
            .and_then(Value::as_str)
            .ok_or_else(|| IpcFailure::invalid("missing 'cwd'"))?;
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
            .ok_or_else(|| IpcFailure::invalid("missing 'name'"))?;
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
                    .ok_or_else(|| IpcFailure::invalid("missing 'url' for an sse/http server"))?;
                json!({ "type": transport, "url": url })
            }
            _ => {
                let command = p
                    .get("command")
                    .and_then(Value::as_str)
                    .map(str::trim)
                    .filter(|s| !s.is_empty())
                    .ok_or_else(|| IpcFailure::invalid("missing 'command' for a stdio server"))?;
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
            .ok_or_else(|| IpcFailure::invalid("missing 'name'"))?;
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

    /// List **every** MCP server the `claude` CLI knows about — across all scopes
    /// *and* plugin / claude.ai connector servers (which never appear in
    /// `~/.claude.json`) — including each server's live connection status.
    ///
    /// SHELL-OUT BOUNDARY (A16): this is implemented by *scraping the human text
    /// of `claude mcp list`*, not by speaking the MCP protocol. That is a
    /// deliberate fallback — the CLI is the only source that knows about plugin
    /// and connector servers — and it is brittle to CLI output changes. We
    /// therefore surface failure explicitly: a CLI error returns an empty list
    /// **plus a `warning`** so the UI can distinguish "the `claude` CLI is
    /// unavailable / changed its output" from "no servers are configured".
    ///
    /// TODO(A16): replace the text scrape with a real MCP-protocol query for the
    /// servers we can reach directly, keeping the CLI scrape only for the
    /// plugin/connector servers it uniquely reports.
    fn mcp_list_all(&self, p: &Value) -> HandlerResult {
        // Run in the project dir (if given) so its `.mcp.json` servers are
        // included alongside user, plugin, and connector servers.
        let cwd = p.get("cwd").and_then(Value::as_str);
        match run_claude_in(cwd, &["mcp", "list"]) {
            Ok(out) => Ok(json!({ "servers": parse_claude_mcp_list(&out) })),
            Err(e) => {
                // Best-effort, non-fatal: the file-based `mcp.list` still works,
                // so don't fail the whole request — but make the degradation
                // visible rather than masquerading as an empty config.
                tracing::warn!(error = %e, "mcp.list_all: `claude mcp list` shell-out failed");
                Ok(json!({ "servers": [], "warning": e }))
            }
        }
    }

    /// Remove an MCP server via `claude mcp remove <name>` (handles whichever
    /// scope the CLI manages it in). Used for servers not editable as a plain
    /// `.mcp.json` / `~/.claude.json` entry.
    fn mcp_cli_remove(&self, p: &Value) -> HandlerResult {
        let name = p
            .get("name")
            .and_then(Value::as_str)
            .ok_or_else(|| IpcFailure::invalid("missing 'name'"))?;
        let output = run_claude(&["mcp", "remove", name])?;
        Ok(json!({ "ok": true, "name": name, "output": output.trim() }))
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

    /// Collect library files of a kind from the user's library
    /// (`<state_dir>/<sub>`) only. The libraries start empty; the shipped
    /// defaults are *not* listed automatically — the user loads them on demand
    /// via `library.load_defaults` (Settings → Load default templates), which
    /// copies them in as editable items. Everything listed is therefore
    /// writable.
    fn library_files(&self, sub: &str, suffix: &str) -> Vec<(String, String, bool)> {
        read_files_with_suffix(&self.inner.state_dir.join(sub), suffix)
            .into_iter()
            .map(|(path, content)| (path, content, true))
            .collect()
    }

    /// Copy the shipped default tasks & definitions from the bundled library
    /// (`<library_dir>`) into the user's editable library (`<state_dir>`),
    /// preserving category subfolders and skipping files that already exist (so
    /// the user's own edits are never clobbered). Returns how many of each were
    /// newly added.
    fn library_load_defaults(&self) -> HandlerResult {
        let mut counts = serde_json::Map::new();
        for (sub, suffix) in [("tasks", ".task.json"), ("definitions", ".def.md")] {
            let src_root = self.inner.library_dir.join(sub);
            let dst_root = self.inner.state_dir.join(sub);
            let mut added = 0u64;
            // Same dir (dev with no separate bundle): nothing to copy.
            if src_root != dst_root {
                for (abs, _content) in read_files_with_suffix(&src_root, suffix) {
                    let src = Path::new(&abs);
                    let Ok(rel) = src.strip_prefix(&src_root) else {
                        continue;
                    };
                    let dst = dst_root.join(rel);
                    if dst.exists() {
                        continue;
                    }
                    if let Some(parent) = dst.parent() {
                        std::fs::create_dir_all(parent).ok();
                    }
                    if std::fs::copy(src, &dst).is_ok() {
                        added += 1;
                    }
                }
            }
            counts.insert(sub.to_string(), json!(added));
        }
        Ok(Value::Object(counts))
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
            .ok_or_else(|| IpcFailure::invalid("missing 'path'"))?;
        let target = Path::new(path);
        let user_root = self.inner.state_dir.join(sub);
        if !target.starts_with(&user_root) {
            return Err(IpcFailure::invalid("only items in your library can be deleted"));
        }
        if !path.ends_with(suffix) {
            return Err(IpcFailure::invalid("refusing to delete a non-library file"));
        }
        std::fs::remove_file(target).map_err(|e| match e.kind() {
            std::io::ErrorKind::NotFound => IpcFailure::not_found(format!("no such library item: {path}")),
            _ => IpcFailure::internal(e.to_string()),
        })?;
        Ok(json!({ "ok": true }))
    }

    /// List installed skills for the project at `cwd` (and the user's global
    /// skills), parsed from `<root>/.claude/skills/<name>/SKILL.md`. Each skill's
    /// invocation `command` is its directory name (typed as `/<command>`).
    /// Project skills shadow user skills of the same name.
    fn skills_list(&self, p: &Value) -> HandlerResult {
        let cwd = p.get("cwd").and_then(Value::as_str);

        // 1. Scan the local skill directories for rich metadata (description,
        //    scope, SKILL.md path), keyed by the skill's command token.
        let mut meta: HashMap<String, (String, String, String, &str)> = HashMap::new();
        let mut roots: Vec<(PathBuf, &str)> = Vec::new();
        if let Some(cwd) = cwd {
            roots.push((Path::new(cwd).join(".claude").join("skills"), "project"));
        }
        if let Ok(home) = std::env::var("HOME") {
            roots.push((Path::new(&home).join(".claude").join("skills"), "user"));
        }
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
                let fm = parse_frontmatter(&content);
                let name = fm
                    .get("name")
                    .cloned()
                    .filter(|s| !s.is_empty())
                    .unwrap_or_else(|| command.clone());
                // Use the frontmatter description; if there is none, generate one
                // from the SKILL.md body (first heading / prose line) so every
                // skill has a meaningful description.
                let description = {
                    let d = fm.get("description").cloned().unwrap_or_default();
                    if d.trim().is_empty() {
                        derive_skill_description(&content)
                    } else {
                        d
                    }
                };
                meta.entry(command).or_insert((
                    name,
                    description,
                    dir.join("SKILL.md").to_string_lossy().to_string(),
                    scope,
                ));
            }
        }

        // 2. The authoritative full list of skills the user can invoke with `/`
        //    (includes plugin and built-in skills) comes from the CLI itself.
        let cli_skills = claude_init_skills(cwd);

        // 3. Merge: every CLI skill, enriched with local metadata when available;
        //    plus any local skill the CLI didn't report (e.g. project-only).
        let mut skills = Vec::new();
        let mut emitted = HashSet::new();
        for command in &cli_skills {
            emitted.insert(command.clone());
            if let Some((name, desc, path, scope)) = meta.get(command) {
                skills.push(json!({ "command": command, "name": name, "description": desc, "path": path, "scope": scope }));
            } else {
                // Namespaced (`plugin:skill`) ones come from plugins.
                let scope = if command.contains(':') {
                    "plugin"
                } else {
                    "user"
                };
                // No local SKILL.md (plugin / built-in command): synthesize a
                // readable description from the command so it's never blank.
                skills.push(json!({ "command": command, "name": command, "description": humanize_skill_command(command), "path": "", "scope": scope }));
            }
        }
        for (command, (name, desc, path, scope)) in &meta {
            if emitted.insert(command.clone()) {
                skills.push(json!({ "command": command, "name": name, "description": desc, "path": path, "scope": scope }));
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

    /// Scaffold a new skill at `<root>/.claude/skills/<slug>/SKILL.md` (root =
    /// `cwd` for project scope, `$HOME` for user scope) with starter frontmatter.
    fn skills_create(&self, p: &Value) -> HandlerResult {
        let name = p
            .get("name")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .unwrap_or("New Skill");
        let command = slugify(name);
        let root = skills_root_for_scope(p)?;
        let dir = root.join(&command);
        if dir.exists() {
            return Err(IpcFailure::invalid(format!("a skill named '{command}' already exists")));
        }
        std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
        let body = format!(
            "---\nname: {command}\ndescription: {name} — describe when Claude should use this skill.\n---\n\n# {name}\n\nWrite the instructions Claude should follow when this skill runs.\n"
        );
        let skill_md = dir.join("SKILL.md");
        std::fs::write(&skill_md, body).map_err(|e| e.to_string())?;
        Ok(json!({ "ok": true, "path": skill_md.to_string_lossy(), "command": command }))
    }

    /// Install one or more skills from a `source` (a git URL or a local path)
    /// into the chosen scope's `.claude/skills/` directory. Git sources are
    /// shallow-cloned with the system `git`; never the network API.
    fn skills_install(&self, p: &Value) -> HandlerResult {
        let source = p
            .get("source")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .ok_or_else(|| IpcFailure::invalid("missing 'source'"))?;
        let dest_root = skills_root_for_scope(p)?;
        std::fs::create_dir_all(&dest_root).map_err(|e| e.to_string())?;

        // Resolve the source to a local directory to copy skill folders from.
        let temp_holder;
        let source_dir: PathBuf = if looks_like_git_url(source) {
            let tmp = std::env::temp_dir().join(format!("cs-skill-clone-{}", std::process::id()));
            let _ = std::fs::remove_dir_all(&tmp);
            let out = std::process::Command::new("git")
                .args(["clone", "--depth", "1", source])
                .arg(&tmp)
                .output()
                .map_err(|e| IpcFailure::config(format!("git not available: {e}")))?;
            if !out.status.success() {
                return Err(IpcFailure::internal(format!(
                    "git clone failed: {}",
                    String::from_utf8_lossy(&out.stderr).trim()
                )));
            }
            temp_holder = tmp;
            temp_holder.clone()
        } else {
            let dir = PathBuf::from(source);
            if !dir.is_dir() {
                return Err(IpcFailure::invalid("source is neither a git URL nor an existing directory"));
            }
            dir
        };

        // Collect skill directories: any folder containing SKILL.md (including
        // the source root itself).
        let mut skill_dirs: Vec<PathBuf> = Vec::new();
        if source_dir.join("SKILL.md").is_file() {
            skill_dirs.push(source_dir.clone());
        }
        if let Ok(entries) = std::fs::read_dir(&source_dir) {
            for entry in entries.flatten() {
                let d = entry.path();
                if d.is_dir() && d.join("SKILL.md").is_file() {
                    skill_dirs.push(d);
                }
            }
        }
        if skill_dirs.is_empty() {
            return Err(IpcFailure::invalid("no SKILL.md found in the source"));
        }

        let mut installed = Vec::new();
        for src in &skill_dirs {
            let Some(name) = src.file_name().and_then(|n| n.to_str()) else {
                continue;
            };
            let dest = dest_root.join(name);
            let _ = std::fs::remove_dir_all(&dest);
            copy_dir_recursive(src, &dest).map_err(|e| e.to_string())?;
            installed.push(name.to_string());
        }
        Ok(json!({ "ok": true, "installed": installed }))
    }

    /// Remove an installed skill directory. The path must live inside a
    /// `.claude/skills/` directory (guarding against deleting anything else).
    fn skills_uninstall(&self, p: &Value) -> HandlerResult {
        let path = p
            .get("path")
            .and_then(Value::as_str)
            .ok_or_else(|| IpcFailure::invalid("missing 'path'"))?;
        // The path may be the SKILL.md file or the skill directory.
        let mut dir = PathBuf::from(path);
        if dir.is_file() {
            dir = dir
                .parent()
                .map(Path::to_path_buf)
                .ok_or_else(|| IpcFailure::invalid("invalid skill path"))?;
        }
        let inside_skills = dir
            .components()
            .collect::<Vec<_>>()
            .windows(2)
            .any(|w| w[0].as_os_str() == ".claude" && w[1].as_os_str() == "skills");
        if !inside_skills {
            return Err(IpcFailure::invalid("only skills under a .claude/skills directory can be removed"));
        }
        std::fs::remove_dir_all(&dir).map_err(|e| e.to_string())?;
        Ok(json!({ "ok": true }))
    }

    // MARK: Plugins (delegated to the `claude plugin` CLI)

    /// List installed Claude Code plugins via `claude plugin list --json`.
    fn plugins_list(&self) -> HandlerResult {
        let out = run_claude(&["plugin", "list", "--json"])?;
        let parsed: Value = serde_json::from_str(out.trim()).unwrap_or(json!([]));
        let list: Vec<Value> = parsed
            .as_array()
            .map(|rows| {
                rows.iter()
                    .map(|r| {
                        let id = r.get("id").and_then(Value::as_str).unwrap_or_default();
                        let (name, marketplace) = id.split_once('@').unwrap_or((id, ""));
                        json!({
                            "id": id,
                            "name": name,
                            "marketplace": marketplace,
                            "version": r.get("version").and_then(Value::as_str).unwrap_or("unknown"),
                            "scope": r.get("scope").and_then(Value::as_str).unwrap_or("user"),
                            "enabled": r.get("enabled").and_then(Value::as_bool).unwrap_or(false),
                            "has_mcp": r.get("mcpServers").map(|m| m.is_object()).unwrap_or(false),
                        })
                    })
                    .collect()
            })
            .unwrap_or_default();
        Ok(json!({ "plugins": list }))
    }

    /// Install a plugin (`plugin@marketplace`) at the given scope.
    fn plugins_install(&self, p: &Value) -> HandlerResult {
        let source = p
            .get("source")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .ok_or_else(|| IpcFailure::invalid("missing 'source'"))?;
        let scope = p.get("scope").and_then(Value::as_str).unwrap_or("user");
        let out = run_claude(&["plugin", "install", source, "--scope", scope])?;
        Ok(json!({ "ok": true, "output": out.trim() }))
    }

    /// Uninstall a plugin by name (`plugin@marketplace` or `plugin`).
    fn plugins_uninstall(&self, p: &Value) -> HandlerResult {
        let name = p
            .get("name")
            .and_then(Value::as_str)
            .ok_or_else(|| IpcFailure::invalid("missing 'name'"))?;
        let scope = p.get("scope").and_then(Value::as_str).unwrap_or("user");
        let out = run_claude(&["plugin", "uninstall", name, "--scope", scope, "-y"])?;
        Ok(json!({ "ok": true, "output": out.trim() }))
    }

    /// Enable or disable an installed plugin.
    fn plugins_set_enabled(&self, p: &Value) -> HandlerResult {
        let name = p
            .get("name")
            .and_then(Value::as_str)
            .ok_or_else(|| IpcFailure::invalid("missing 'name'"))?;
        let enabled = p
            .get("enabled")
            .and_then(Value::as_bool)
            .ok_or_else(|| IpcFailure::invalid("missing 'enabled'"))?;
        let verb = if enabled { "enable" } else { "disable" };
        let out = run_claude(&["plugin", verb, name])?;
        Ok(json!({ "ok": true, "output": out.trim() }))
    }

    /// List configured marketplaces via `claude plugin marketplace list --json`.
    fn plugins_marketplace_list(&self) -> HandlerResult {
        let out = run_claude(&["plugin", "marketplace", "list", "--json"])?;
        let parsed: Value = serde_json::from_str(out.trim()).unwrap_or(json!([]));
        Ok(json!({ "marketplaces": parsed }))
    }

    /// Add a marketplace from a URL, path, or GitHub repo.
    fn plugins_marketplace_add(&self, p: &Value) -> HandlerResult {
        let source = p
            .get("source")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .ok_or_else(|| IpcFailure::invalid("missing 'source'"))?;
        let out = run_claude(&["plugin", "marketplace", "add", source])?;
        Ok(json!({ "ok": true, "output": out.trim() }))
    }
}

/// Monotonic counter so ids stay unique even within the same millisecond
/// (avoids collisions when several records are written back-to-back).
static ID_COUNTER: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

/// A process-unique id of the form `<prefix>-<millis>-<seq>`.
fn unique_id(prefix: &str) -> String {
    let n = ID_COUNTER.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    format!("{prefix}-{}-{}", now_millis(), n)
}

/// Read a required string field from a request payload, or fail with 400.
fn req_str<'a>(p: &'a Value, key: &str) -> std::result::Result<&'a str, IpcFailure> {
    p.get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| IpcFailure::invalid(format!("missing '{key}'")))
}

/// Whether `path` is a protected secret that must never be sent to Claude.
/// Matches `.env*`, anything under a `secrets/` directory, and key material.
fn is_protected_path(path: &str) -> bool {
    let lower = path.to_lowercase();
    let name = Path::new(path)
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("")
        .to_lowercase();
    name == ".env"
        || name.starts_with(".env.")
        || name.ends_with(".pem")
        || name.ends_with(".key")
        || name.starts_with("id_rsa")
        || name == "credentials"
        || name == ".npmrc"
        || lower.contains("/secrets/")
        || lower.contains("/.ssh/")
}

/// Whether an executable `bin` is resolvable on PATH (best-effort, no failure).
fn which_ok(bin: &str) -> bool {
    std::process::Command::new(bin)
        .arg("--version")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

/// Pure permission classifier. Returns `(decision, reason, critical_gate)` where
/// decision ∈ {allow, ask, deny}. Critical gates fire in EVERY trust mode —
/// including YOLO. Deterministic and side-effect free (so it is unit-testable).
fn classify_permission(
    mode: &str,
    action: &str,
    command: &str,
    path: &str,
    project_root: &str,
    branch: &str,
    matrix: Option<&str>,
) -> (String, String, bool) {
    let cmd = command.to_lowercase();

    // --- Critical gates: hold in every mode, including yolo ---
    if is_recursive_delete(&cmd) {
        let target = if !path.is_empty() { path } else { last_path_token(command) };
        if target == "/"
            || target.starts_with("/etc")
            || target.starts_with("/usr")
            || target.starts_with("/System")
        {
            return ("deny".into(), "kritisches Gate: rm -rf auf Systempfad".into(), true);
        }
        if !target.is_empty() && !project_root.is_empty() && !is_within(target, project_root) {
            return (
                "deny".into(),
                format!("kritisches Gate: rekursives Löschen außerhalb des Projektpfads ({target})"),
                true,
            );
        }
    }
    if is_dangerous_command(&cmd) {
        return (
            "deny".into(),
            "Dangerous-Command-Filter: gefährlicher Befehl blockiert".into(),
            true,
        );
    }
    if action == "git.push" && is_protected_branch(branch) {
        return (
            "ask".into(),
            format!("kritisches Gate: Push auf geschützten Branch '{branch}'"),
            true,
        );
    }

    // --- Per-tool permission matrix (after gates) ---
    match matrix {
        Some("deny") => return ("deny".into(), format!("Permission-Matrix: {action}=deny"), false),
        Some("allow") => return ("allow".into(), format!("Permission-Matrix: {action}=allow"), false),
        Some("ask") => return ("ask".into(), format!("Permission-Matrix: {action}=ask"), false),
        _ => {}
    }

    // --- Bash blocklist ---
    if action == "bash" && matches_blocklist(&cmd) {
        return ("deny".into(), "Bash-Blocklist-Treffer".into(), false);
    }

    // --- Trust-mode logic ---
    match mode {
        "strict" => ("ask".into(), "Strict: jede Aktion einzeln bestätigen".into(), false),
        "standard" => {
            if is_risky_action(action, &cmd) {
                ("ask".into(), "Standard: gefährliche Aktion -> ask".into(), false)
            } else {
                ("allow".into(), "Standard: sichere Aktion -> auto".into(), false)
            }
        }
        "auto" => ("allow".into(), "Auto: automatisch (außer Gates)".into(), false),
        "yolo" => ("allow".into(), "YOLO: automatisch (außer kritische Gates)".into(), false),
        other => ("ask".into(), format!("unbekannter Modus '{other}' -> ask"), false),
    }
}

fn is_recursive_delete(cmd: &str) -> bool {
    cmd.contains("rm ")
        && (cmd.contains("-rf") || cmd.contains("-fr") || (cmd.contains("-r") && cmd.contains("-f")))
}
fn is_dangerous_command(cmd: &str) -> bool {
    let needles = [
        ":(){", "mkfs", "dd if=", "rm -rf /", "> /dev/sd", "shutdown ", "reboot", "chmod -r 777 /",
    ];
    needles.iter().any(|n| cmd.contains(n))
}
fn is_protected_branch(branch: &str) -> bool {
    matches!(branch, "main" | "master" | "production" | "prod" | "release")
}
fn is_risky_action(action: &str, cmd: &str) -> bool {
    action == "deploy"
        || action == "git.push"
        || cmd.contains("sudo")
        || cmd.contains("rm ")
        || cmd.contains("kill ")
        || cmd.contains("git push")
        || cmd.contains("npm publish")
}
fn matches_blocklist(cmd: &str) -> bool {
    ["curl ", "wget ", " nc ", "telnet ", "eval "].iter().any(|b| cmd.contains(b))
}
fn last_path_token(cmd: &str) -> &str {
    cmd.split_whitespace().last().unwrap_or("")
}
fn is_within(target: &str, root: &str) -> bool {
    if !target.starts_with('/') {
        return true; // relative target — treat as inside the project
    }
    let t = target.trim_end_matches('/');
    let r = root.trim_end_matches('/');
    t == r || t.starts_with(&format!("{r}/"))
}

/// Route a task type to the agent best suited to handle it (F304).
fn routing_route_payload(p: &Value) -> Value {
    let task_type = p.get("task_type").and_then(Value::as_str).unwrap_or("").to_lowercase();
    let agent = if task_type.contains("test") {
        "test-agent"
    } else if task_type.contains("doc") {
        "doc-writer"
    } else if task_type.contains("security") || task_type.contains("scan") {
        "security-scan"
    } else if task_type.contains("review") {
        "review-agent"
    } else if task_type.contains("fix") || task_type.contains("bug") {
        "fix-agent"
    } else if task_type.contains("feature") || task_type.contains("impl") {
        "feature-agent"
    } else {
        "general-agent"
    };
    json!({ "task_type": task_type, "agent": agent })
}

/// Rank a priority label (lower = more urgent).
fn priority_rank(label: &str) -> u8 {
    match label.to_lowercase().as_str() {
        "critical" => 0,
        "high" => 1,
        "normal" => 2,
        "background" => 3,
        _ => 2,
    }
}

/// Order queued tasks by priority (Critical → High → Normal → Background) (F309).
fn queue_order_payload(p: &Value) -> Value {
    let mut tasks: Vec<Value> = p
        .get("tasks")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    tasks.sort_by_key(|t| priority_rank(t.get("priority").and_then(Value::as_str).unwrap_or("normal")));
    let order: Vec<&Value> = tasks.iter().map(|t| &t["id"]).collect();
    json!({ "ordered": tasks, "order": order })
}

/// Hard resource limit: admit a new agent only if running < max_parallel (F310).
fn scheduler_admit_payload(p: &Value) -> Value {
    let running = p.get("running").and_then(Value::as_u64).unwrap_or(0);
    let max_parallel = p.get("max_parallel").and_then(Value::as_u64).unwrap_or(4);
    let admit = running < max_parallel;
    json!({
        "admit": admit, "running": running, "max_parallel": max_parallel,
        "reason": if admit { "Slot frei" } else { "Limit erreicht — überzähliger Agent abgewiesen" },
    })
}

/// Continuous health monitor: ping an endpoint, alert on HTTP != 200 (F313).
fn health_check_payload(p: &Value) -> Value {
    let url = p.get("url").and_then(Value::as_str).unwrap_or("");
    let out = std::process::Command::new("curl")
        .args(["-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "5", url])
        .output();
    let code: u32 = out
        .ok()
        .and_then(|o| String::from_utf8_lossy(&o.stdout).trim().parse().ok())
        .unwrap_or(0);
    let ok = code == 200;
    json!({ "url": url, "status_code": code, "ok": ok, "alert": !ok })
}

/// Cost guard: warn at ≥80 %, stop at ≥100 % of the budget (F314).
fn cost_guard_payload(p: &Value) -> Value {
    let spent = p.get("spent").and_then(Value::as_f64).unwrap_or(0.0);
    let budget = p.get("budget").and_then(Value::as_f64).unwrap_or(0.0);
    let ratio = if budget > 0.0 { spent / budget } else { 0.0 };
    let (status, action) = if ratio >= 1.0 {
        ("stop", "Agenten-Aktivität gestoppt")
    } else if ratio >= 0.8 {
        ("warn", "Warnung: 80 % des Budgets erreicht")
    } else {
        ("ok", "im Budget")
    };
    json!({ "spent": spent, "budget": budget, "ratio": ratio, "status": status, "action": action })
}

/// Supervisor decision for one agent: restart on idle, pause on budget,
/// escalate on a repeated error loop (F301/F302/F303).
fn supervisor_evaluate_payload(p: &Value) -> Value {
    let error_repeats = p.get("error_repeats").and_then(Value::as_u64).unwrap_or(0);
    let tokens_used = p.get("tokens_used").and_then(Value::as_u64);
    let token_budget = p.get("token_budget").and_then(Value::as_u64);
    let last_output_ms = p.get("last_output_ms").and_then(Value::as_u64);
    let now_ms = p.get("now_ms").and_then(Value::as_u64);
    let idle_threshold_ms = p.get("idle_threshold_ms").and_then(Value::as_u64).unwrap_or(900_000);

    let (action, reason) = if error_repeats > 3 {
        ("escalate", format!("gleicher Fehler {error_repeats}× — Eskalation an User"))
    } else if matches!((tokens_used, token_budget), (Some(u), Some(b)) if u >= b) {
        ("pause", "Token-Budget überschritten — Agent pausiert".to_string())
    } else if matches!((last_output_ms, now_ms), (Some(l), Some(n)) if n.saturating_sub(l) > idle_threshold_ms) {
        ("restart", "kein Output über Schwellwert — Restart".to_string())
    } else {
        ("ok", "Agent gesund".to_string())
    };
    json!({ "action": action, "reason": reason })
}

/// Prebuilt prompt templates for the prompt library (F243).
fn prompt_templates_payload() -> Value {
    json!({ "templates": [
        {"name":"Code Review","category":"review","template":"Review the following diff for correctness, security, and style:\n\n{{diff}}"},
        {"name":"Feature implementieren","category":"feature","template":"Implement {{feature}} in {{file}}. Follow existing patterns. Add tests."},
        {"name":"Bug fixen","category":"bugfix","template":"Diagnose and fix this bug: {{bug}}. Reproduce first, then a minimal fix."},
        {"name":"Tests schreiben","category":"tests","template":"Write unit tests for {{target}} covering the edge cases."},
        {"name":"Refactoring","category":"refactor","template":"Refactor {{target}} for clarity without changing behavior."},
    ]})
}

/// Run a deterministic prompt chain, piping each step's output into the next
/// (F246) and supporting conditional branches (F247). Real LLM `prompt` steps
/// would need a live `claude` session; the test ops here verify the chaining.
fn chain_run_payload(p: &Value) -> Value {
    let mut current = p.get("input").and_then(Value::as_str).unwrap_or("").to_string();
    let steps = p.get("steps").and_then(Value::as_array).cloned().unwrap_or_default();
    let mut trace = Vec::new();
    run_chain_steps(&steps, &mut current, &mut trace);
    json!({ "output": current, "trace": trace })
}

fn run_chain_steps(steps: &[Value], current: &mut String, trace: &mut Vec<Value>) {
    for step in steps {
        let op = step.get("op").and_then(Value::as_str).unwrap_or("");
        if op == "branch" {
            let needle = step.get("contains").and_then(Value::as_str).unwrap_or("");
            let taken = if current.contains(needle) { "then" } else { "else" };
            trace.push(json!({ "op": "branch", "contains": needle, "taken": taken }));
            if let Some(sub) = step.get(taken).and_then(Value::as_array) {
                run_chain_steps(sub, current, trace);
            }
            continue;
        }
        match op {
            "set" => *current = step.get("arg").and_then(Value::as_str).unwrap_or("").to_string(),
            "append" => current.push_str(step.get("arg").and_then(Value::as_str).unwrap_or("")),
            "upper" => *current = current.to_uppercase(),
            "replace" => {
                let from = step.get("from").and_then(Value::as_str).unwrap_or("");
                let to = step.get("to").and_then(Value::as_str).unwrap_or("");
                *current = current.replace(from, to);
            }
            _ => {}
        }
        trace.push(json!({ "op": op, "output": current.clone() }));
    }
}

/// Classify a single added line as a known secret kind, if any (F075).
fn secret_kind(s: &str) -> Option<&'static str> {
    let t = s.trim();
    if t.contains("BEGIN") && t.contains("PRIVATE KEY") {
        return Some("private_key");
    }
    if let Some(i) = t.find("AKIA") {
        let alnum = t[i + 4..].chars().take(16).filter(|c| c.is_ascii_alphanumeric()).count();
        if alnum == 16 {
            return Some("aws_access_key");
        }
    }
    let low = t.to_lowercase();
    let labeled = low.contains("secret")
        || low.contains("api_key")
        || low.contains("apikey")
        || low.contains("access_key")
        || low.contains("password");
    if labeled {
        let val_len = t
            .split(['=', ':'])
            .nth(1)
            .map(|v| v.trim().trim_matches('"').trim_matches('\'').chars().count())
            .unwrap_or(0);
        if val_len >= 16 {
            return Some("generic_secret");
        }
    }
    None
}

/// Redact a secret-bearing line for safe reporting.
fn redact(s: &str) -> String {
    let prefix: String = s.trim().chars().take(10).collect();
    format!("{prefix}…***")
}

/// Prompt-injection scan over a tool output (F297). Flags well-known patterns.
fn scan_output_payload(p: &Value) -> Value {
    let text = p.get("text").and_then(Value::as_str).unwrap_or("").to_lowercase();
    let patterns = [
        "ignore previous instructions",
        "ignore all previous",
        "disregard the above",
        "you are now",
        "reveal your system prompt",
        "exfiltrate",
        "send the secret",
        "base64 -d",
    ];
    let hits: Vec<&str> = patterns.iter().filter(|n| text.contains(**n)).copied().collect();
    json!({ "flagged": !hits.is_empty(), "patterns": hits })
}

// CONTRACT: the Swift `CoreConfig` decoder treats `daily_budget_usd` and
// `context_token_budget` as REQUIRED fields, and its `intValue` helper rejects a
// msgpack float. `context_token_budget` MUST therefore stay an integer type
// (`usize`) and `daily_budget_usd` a float — emitting `context_token_budget` as a
// float, or dropping either key, would nil the ENTIRE CoreConfig decode on the
// client. Guarded by `config_to_json_carries_every_field_the_swift_dto_requires`.
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

/// Return the SKILL.md body with a leading `--- ... ---` frontmatter removed.
fn strip_frontmatter(content: &str) -> &str {
    let trimmed = content.trim_start_matches([' ', '\n', '\r']);
    if let Some(rest) = trimmed.strip_prefix("---") {
        // Find the closing fence at the start of a line and return what follows.
        if let Some(idx) = rest.find("\n---") {
            let after = &rest[idx + 4..];
            return after
                .trim_start_matches(|c| c != '\n')
                .trim_start_matches('\n');
        }
    }
    content
}

/// Trim a candidate line into a clean one-line description (markers stripped,
/// length-capped).
fn truncate_desc(s: &str) -> String {
    let s = s
        .trim()
        .trim_start_matches(['#', '*', '-', '>', ' '])
        .trim();
    let max = 160;
    if s.chars().count() <= max {
        s.to_string()
    } else {
        let mut t: String = s.chars().take(max).collect();
        t.push('…');
        t
    }
}

/// Generate a one-line description from a SKILL.md body when its frontmatter has
/// none: prefer the first prose line; otherwise the first heading's text.
fn derive_skill_description(content: &str) -> String {
    let body = strip_frontmatter(content);
    let mut first_heading: Option<String> = None;
    for raw in body.lines() {
        let line = raw.trim();
        if line.is_empty() {
            continue;
        }
        if line.starts_with('#') {
            if first_heading.is_none() {
                let text = line.trim_start_matches('#').trim().to_string();
                if !text.is_empty() {
                    first_heading = Some(text);
                }
            }
            continue;
        }
        // First non-empty, non-heading line is the best summary.
        return truncate_desc(line);
    }
    first_heading.map(|h| truncate_desc(&h)).unwrap_or_default()
}

/// A readable description for a `/`-command skill we have no local SKILL.md for
/// (plugin or built-in), synthesized from the command token.
fn humanize_skill_command(command: &str) -> String {
    let prettify = |s: &str| -> String {
        s.split(['-', '_'])
            .filter(|w| !w.is_empty())
            .map(|w| {
                let mut chars = w.chars();
                match chars.next() {
                    Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
                    None => String::new(),
                }
            })
            .collect::<Vec<_>>()
            .join(" ")
    };
    if let Some((plugin, skill)) = command.split_once(':') {
        format!("{} skill (from the {plugin} plugin)", prettify(skill))
    } else {
        format!("The /{command} skill")
    }
}

/// The `claude` binary to invoke (honors `CLAUDESTUDIO_CLAUDE_BIN`).
fn claude_bin() -> String {
    std::env::var("CLAUDESTUDIO_CLAUDE_BIN").unwrap_or_else(|_| "claude".to_string())
}

/// Run the `claude` CLI with `args`, capturing stdout. Returns a useful error
/// (including stderr) on failure. Never touches the Anthropic API — this only
/// shells out to the locally-authenticated `claude` binary.
fn run_claude(args: &[&str]) -> std::result::Result<String, String> {
    run_claude_in(None, args)
}

/// The complete list of skills the user can invoke with `/` — including plugin
/// and built-in skills the local filesystem scan can't see. We read them from the
/// `claude` CLI's `system`/`init` line (which lists every available skill), then
/// stop the process as soon as we have it (the init line arrives in ~1s, well
/// before the model responds). Returns an empty list if the CLI is unavailable.
fn claude_init_skills(cwd: Option<&str>) -> Vec<String> {
    use std::io::{BufRead, BufReader};
    let mut cmd = std::process::Command::new(claude_bin());
    cmd.args(["-p", "ok", "--output-format", "stream-json", "--verbose"])
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null());
    if let Some(dir) = cwd {
        cmd.current_dir(dir);
    }
    let Ok(mut child) = cmd.spawn() else {
        return Vec::new();
    };
    let mut skills = Vec::new();
    if let Some(out) = child.stdout.take() {
        for line in BufReader::new(out)
            .lines()
            .map_while(std::result::Result::ok)
        {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            let Ok(v) = serde_json::from_str::<Value>(line) else {
                continue;
            };
            if v.get("type").and_then(Value::as_str) == Some("system")
                && v.get("subtype").and_then(Value::as_str) == Some("init")
            {
                if let Some(arr) = v.get("skills").and_then(Value::as_array) {
                    skills = arr
                        .iter()
                        .filter_map(|x| x.as_str().map(str::to_string))
                        .collect();
                }
                break;
            }
        }
    }
    let _ = child.kill();
    let _ = child.wait();
    skills
}

/// Like [`run_claude`] but runs in `cwd` when given (so directory-scoped config,
/// e.g. a project's `.mcp.json`, is picked up).
fn run_claude_in(cwd: Option<&str>, args: &[&str]) -> std::result::Result<String, String> {
    let mut cmd = std::process::Command::new(claude_bin());
    cmd.args(args).stdin(std::process::Stdio::null());
    if let Some(dir) = cwd {
        cmd.current_dir(dir);
    }
    let out = cmd
        .output()
        .map_err(|e| format!("could not run claude: {e}"))?;
    if out.status.success() {
        Ok(String::from_utf8_lossy(&out.stdout).to_string())
    } else {
        let stderr = String::from_utf8_lossy(&out.stderr);
        let stdout = String::from_utf8_lossy(&out.stdout);
        let msg = if stderr.trim().is_empty() {
            stdout.trim()
        } else {
            stderr.trim()
        };
        Err(format!("claude {} failed: {msg}", args.join(" ")))
    }
}

/// True if `source` looks like a git/remote URL rather than a local path.
fn looks_like_git_url(source: &str) -> bool {
    source.contains("://") || source.starts_with("git@") || source.ends_with(".git")
}

/// The `.claude/skills` directory for the requested scope: `project` → `<cwd>`,
/// anything else → `$HOME`.
fn skills_root_for_scope(p: &Value) -> std::result::Result<PathBuf, IpcFailure> {
    let scope = p.get("scope").and_then(Value::as_str).unwrap_or("user");
    let base = if scope == "project" {
        let cwd = p
            .get("cwd")
            .and_then(Value::as_str)
            .ok_or_else(|| IpcFailure::invalid("project scope requires a 'cwd'"))?;
        PathBuf::from(cwd)
    } else {
        PathBuf::from(std::env::var("HOME").map_err(|_| IpcFailure::config("HOME is not set"))?)
    };
    Ok(base.join(".claude").join("skills"))
}

/// Recursively copy a directory tree from `src` to `dst`.
fn copy_dir_recursive(src: &Path, dst: &Path) -> std::io::Result<()> {
    std::fs::create_dir_all(dst)?;
    for entry in std::fs::read_dir(src)? {
        let entry = entry?;
        let from = entry.path();
        let to = dst.join(entry.file_name());
        if from.is_dir() {
            // Skip VCS metadata so an installed skill isn't a nested repo.
            if entry.file_name() == ".git" {
                continue;
            }
            copy_dir_recursive(&from, &to)?;
        } else {
            std::fs::copy(&from, &to)?;
        }
    }
    Ok(())
}

/// Parse the human-readable output of `claude mcp list` into server entries.
/// Each row looks like `name: target [(HTTP)] - <icon> <status>` — names may
/// contain `:` (e.g. `plugin:github:github`) or spaces (`claude.ai Gmail`), so we
/// split on the first `": "` (which only precedes the target).
fn parse_claude_mcp_list(out: &str) -> Vec<Value> {
    let mut servers = Vec::new();
    for line in out.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with("Checking") {
            continue;
        }
        let Some((name, rest)) = line.split_once(": ") else {
            continue;
        };
        let (target_part, status_text) = match rest.rsplit_once(" - ") {
            Some((t, s)) => (t.trim(), s.trim()),
            None => (rest.trim(), ""),
        };
        let (target, transport) = if let Some(idx) = target_part.rfind(" (") {
            let t = target_part[..idx].trim();
            let marker = target_part[idx + 2..].trim_end_matches(')').to_lowercase();
            (t, marker)
        } else if target_part.starts_with("http") {
            (target_part, "http".to_string())
        } else {
            (target_part, "stdio".to_string())
        };
        let status = if status_text.contains("Connected") {
            "connected"
        } else if status_text.contains("Failed") {
            "failed"
        } else if status_text.to_lowercase().contains("auth") {
            "needs-auth"
        } else if status_text.contains("Pending") {
            "pending"
        } else {
            "unknown"
        };
        servers.push(json!({
            "name": name,
            "target": target,
            "transport": transport,
            "status": status,
            "scope": "",
        }));
    }
    servers
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
fn mcp_path_for_scope(scope: &str, cwd: Option<&str>) -> std::result::Result<String, IpcFailure> {
    match scope {
        "project" => {
            let cwd = cwd.ok_or_else(|| IpcFailure::invalid("project scope requires a 'cwd'"))?;
            Ok(format!("{cwd}/.mcp.json"))
        }
        _ => {
            let home = std::env::var("HOME").map_err(|_| IpcFailure::config("HOME is not set"))?;
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
    fn parse_claude_mcp_list_handles_all_shapes() {
        let out = "Checking MCP server health…\n\n\
            claude.ai Indeed: https://mcp.indeed.com/claude/mcp - ✔ Connected\n\
            claude.ai Google Drive: https://drivemcp.googleapis.com/mcp/v1 - ! Needs authentication\n\
            plugin:github:github: https://api.githubcopilot.com/mcp/ (HTTP) - ✘ Failed to connect\n\
            ae-mcp: node /Users/u/after-effects-mcp/dist/index.js - ✘ Failed to connect\n\
            fal-ai: https://mcp.fal.ai/mcp (HTTP) - ✔ Connected\n\
            blender: /opt/homebrew/bin/uvx blender-mcp - ✔ Connected\n";
        let servers = parse_claude_mcp_list(out);
        assert_eq!(servers.len(), 6);

        let by_name = |n: &str| {
            servers
                .iter()
                .find(|s| s["name"] == json!(n))
                .unwrap()
                .clone()
        };

        let gh = by_name("plugin:github:github");
        assert_eq!(gh["target"], json!("https://api.githubcopilot.com/mcp/"));
        assert_eq!(gh["transport"], json!("http"));
        assert_eq!(gh["status"], json!("failed"));

        let gd = by_name("claude.ai Google Drive");
        assert_eq!(gd["status"], json!("needs-auth"));

        let ae = by_name("ae-mcp");
        assert_eq!(ae["transport"], json!("stdio"));
        assert_eq!(
            ae["target"],
            json!("node /Users/u/after-effects-mcp/dist/index.js")
        );

        let fal = by_name("fal-ai");
        assert_eq!(fal["status"], json!("connected"));
        assert_eq!(fal["transport"], json!("http"));
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
        // Point the CLI at a no-op binary so the authoritative-list step returns
        // nothing and the test stays hermetic (no real `claude` spawn). The local
        // filesystem scan must still surface the skill.
        std::env::set_var("CLAUDESTUDIO_CLAUDE_BIN", "/usr/bin/true");
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
    async fn skill_create_then_list_and_uninstall() {
        let (r, base) = router_in("skill-crud");
        let cwd = base.join("proj");
        std::fs::create_dir_all(&cwd).unwrap();
        let cwd_str = cwd.to_string_lossy().to_string();

        let created = r
            .dispatch(&new_request(
                "skills.create",
                json!({ "scope": "project", "cwd": cwd_str, "name": "My Helper" }),
            ))
            .await;
        assert_eq!(created.payload["ok"], json!(true));
        assert_eq!(created.payload["command"], json!("my-helper"));
        let path = created.payload["path"].as_str().unwrap().to_string();
        assert!(path.ends_with("/.claude/skills/my-helper/SKILL.md"));

        let listed = r
            .dispatch(&new_request("skills.list", json!({ "cwd": cwd_str })))
            .await;
        assert!(listed.payload["skills"]
            .as_array()
            .unwrap()
            .iter()
            .any(|s| s["command"] == json!("my-helper")));

        let removed = r
            .dispatch(&new_request("skills.uninstall", json!({ "path": path })))
            .await;
        assert_eq!(removed.payload["ok"], json!(true));

        let after = r
            .dispatch(&new_request("skills.list", json!({ "cwd": cwd_str })))
            .await;
        assert!(!after.payload["skills"]
            .as_array()
            .unwrap()
            .iter()
            .any(|s| s["command"] == json!("my-helper")));
        let _ = std::fs::remove_dir_all(&base);
    }

    #[tokio::test]
    async fn skill_uninstall_rejects_paths_outside_skills_dir() {
        let (r, base) = router_in("skill-guard");
        let res = r
            .dispatch(&new_request(
                "skills.uninstall",
                json!({ "path": "/tmp/not-a-skill" }),
            ))
            .await;
        assert_eq!(res.kind, cs_types::IpcKind::Error);
        let _ = std::fs::remove_dir_all(&base);
    }

    #[tokio::test]
    async fn skill_install_from_local_directory() {
        let (r, base) = router_in("skill-install");
        // A local "source" containing one skill folder.
        let src = base.join("src");
        let pack = src.join("cool-skill");
        std::fs::create_dir_all(&pack).unwrap();
        std::fs::write(
            pack.join("SKILL.md"),
            "---\nname: cool-skill\ndescription: demo\n---\nbody\n",
        )
        .unwrap();
        let cwd = base.join("proj");
        std::fs::create_dir_all(&cwd).unwrap();

        let res = r
            .dispatch(&new_request(
                "skills.install",
                json!({ "scope": "project", "cwd": cwd.to_string_lossy(),
                        "source": src.to_string_lossy() }),
            ))
            .await;
        assert_eq!(res.payload["ok"], json!(true));
        assert!(cwd.join(".claude/skills/cool-skill/SKILL.md").is_file());
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

    #[test]
    fn config_to_json_carries_every_field_the_swift_dto_requires() {
        // Guards the A17 round-trip contract: the Swift `CoreConfig` decoder now
        // treats these as REQUIRED and fails to decode if any is absent (so it
        // can never substitute 0 and write that back, clobbering the real value).
        // If a future refactor drops one of these keys from `config_to_json`,
        // this test fails before the app silently corrupts settings.
        let cfg = AppConfig::default();
        let v = config_to_json(&cfg);
        for key in [
            "trust_mode",
            "default_model",
            "daily_budget_usd",
            "context_token_budget",
        ] {
            assert!(
                v.get(key).is_some_and(|x| !x.is_null()),
                "config_to_json must include '{key}' for the Swift DTO contract"
            );
        }
        // The numeric fields must be numbers (not strings), matching the Swift
        // decoder's `doubleValue` / `intValue` expectations.
        assert!(v["daily_budget_usd"].is_number());
        assert!(v["context_token_budget"].is_number());
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
    async fn libraries_start_empty_and_load_defaults_on_demand() {
        // A shipped library with one task and one definition…
        let base = std::env::temp_dir().join(format!("cs-defaults-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let lib = base.join("shipped");
        let state = base.join("state");
        std::fs::create_dir_all(lib.join("tasks/compliance")).unwrap();
        std::fs::create_dir_all(lib.join("definitions/loading")).unwrap();
        std::fs::create_dir_all(&state).unwrap();
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
            state.clone(),
            lib.clone(),
        );

        // …does NOT appear until loaded: the libraries start empty.
        let before = r.dispatch(&new_request("tasks.list", json!({}))).await;
        assert!(before.payload["tasks"].as_array().unwrap().is_empty());

        // Load the defaults into the user library.
        let loaded = r
            .dispatch(&new_request("library.load_defaults", json!({})))
            .await;
        assert_eq!(loaded.payload["tasks"], json!(1));
        assert_eq!(loaded.payload["definitions"], json!(1));

        // Now they're listed, writable, and copied under the state dir.
        let tasks = r.dispatch(&new_request("tasks.list", json!({}))).await;
        let arr = tasks.payload["tasks"].as_array().unwrap();
        assert_eq!(arr.len(), 1);
        assert_eq!(arr[0]["name"], json!("Kleinunternehmer-Check"));
        assert_eq!(arr[0]["writable"], json!(true));
        assert!(arr[0]["path"]
            .as_str()
            .unwrap()
            .contains(&*state.to_string_lossy()));

        let defs = r
            .dispatch(&new_request("definitions.list", json!({})))
            .await;
        assert_eq!(defs.payload["definitions"].as_array().unwrap().len(), 1);

        // Loading again is idempotent — nothing is added the second time.
        let again = r
            .dispatch(&new_request("library.load_defaults", json!({})))
            .await;
        assert_eq!(again.payload["tasks"], json!(0));

        let _ = std::fs::remove_dir_all(&base);
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
