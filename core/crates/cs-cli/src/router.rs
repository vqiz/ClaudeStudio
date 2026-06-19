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

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use cs_agentic_os::{EventBus, SystemEvent};
use cs_config::{AppConfig, ContextAssembler, LayerKind};
use cs_git::{GitBackend, SystemGit};
use cs_ipc::IpcEnvelope;
use cs_sessions::{NewSession, SessionStore};
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

            // --- Libraries ---
            "tasks.list" => self.tasks_list(),
            "definitions.list" => self.definitions_list(),

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
        let dir = self.inner.library_dir.join("tasks");
        let tasks: Vec<Value> = read_files_with_suffix(&dir, ".task.json")
            .into_iter()
            .filter_map(|(path, content)| {
                let v: Value = serde_json::from_str(&content).ok()?;
                Some(json!({
                    "path": path,
                    "name": v.get("name").cloned().unwrap_or(Value::Null),
                    "category": v.get("category").cloned().unwrap_or(Value::Null),
                    "icon": v.get("icon").cloned().unwrap_or(Value::Null),
                    "description": v.get("description").cloned().unwrap_or(Value::Null),
                    "tags": v.get("tags").cloned().unwrap_or(json!([])),
                }))
            })
            .collect();
        Ok(json!({ "tasks": tasks }))
    }

    fn definitions_list(&self) -> HandlerResult {
        let dir = self.inner.library_dir.join("definitions");
        let defs: Vec<Value> = read_files_with_suffix(&dir, ".def.md")
            .into_iter()
            .map(|(path, content)| {
                let fm = parse_frontmatter(&content);
                json!({
                    "path": path,
                    "name": fm.get("name").cloned().unwrap_or_default(),
                    "category": fm.get("category").cloned().unwrap_or_default(),
                    "scope": fm.get("scope").cloned().unwrap_or_default(),
                    "tags": fm.get("tags").cloned().unwrap_or_default(),
                    "version": fm.get("version").cloned().unwrap_or_default(),
                })
            })
            .collect();
        Ok(json!({ "definitions": defs }))
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

    #[tokio::test]
    async fn ping_responds_pong() {
        let r = router();
        let req = new_request("ping", json!({}));
        let res = r.dispatch(&req).await;
        assert_eq!(res.payload["pong"], json!(true));
        assert_eq!(res.id, req.id);
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
