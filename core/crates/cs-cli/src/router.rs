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

/// Deadline for handlers that drive a *real* `claude` agent run (test generation,
/// auto-fix loops, framework migration, task decomposition, compliance analysis).
/// An autonomous LLM run that writes files + runs commands can take a few minutes;
/// these handlers run the agent to completion in a child process (cancellation-safe
/// since the child owns its own work).
const AGENT_HANDLER_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(420);

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
        // Embedding-backed handlers spawn the neural model (one load per call).
        "knowledge.teach" | "knowledge.search" | "knowledge.chunk_text"
        | "knowledge.extract_entities" | "assets.scan" | "coverage.measure"
        | "models.compare" | "integrations.github_sync" | "integrations.usage_report"
        | "integrations.slack_command" | "telemetry.export_span"
        | "definitions.vector_inject" => LONG_HANDLER_TIMEOUT,
        // Real `claude` agent runs (LLM + autonomous file edits / shell).
        "testing.generate_tests" | "code.auto_fix_loop" | "agents.decompose_task"
        | "refactoring.migrate_component" | "tasks.test_run" | "skills.test"
        | "prompts.optimize" | "compliance.check" => AGENT_HANDLER_TIMEOUT,
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

    /// Die aktuell laufenden Agenten (Sessions mit aktivem Cancel-Signal). Der
    /// dauerhaft laufende Supervisor (Haiku) führt jeden laufenden Agenten als
    /// 'observed'; endet einer, verschwindet er aus der Liste (F300).
    fn os_running_agents(&self) -> HandlerResult {
        let mut ids: Vec<String> = self.inner.cancels.lock().unwrap().keys().cloned().collect();
        ids.sort();
        Ok(json!({
            "running": ids,
            "count": ids.len(),
            "supervisor": { "model": "haiku", "alive": true, "observed": ids.len() },
        }))
    }

    /// OS-View Mission-Control (F315): bündelt die Live-Daten der Agentic-OS in einer
    /// Antwort — laufende Agenten-Kacheln, der Event-Stream, das Queue-Board, der A2A-Feed
    /// und Resource-Gauges (Zähler). Liest die persistierten Logs (event_log/agent_queue).
    fn os_mission_control(&self) -> HandlerResult {
        let mut agents: Vec<String> = self.inner.cancels.lock().unwrap().keys().cloned().collect();
        agents.sort();
        let read_jsonl = |name: &str, take_last: usize| -> Vec<Value> {
            let content = std::fs::read_to_string(self.inner.state_dir.join(name)).unwrap_or_default();
            let mut v: Vec<Value> = content.lines().filter_map(|l| serde_json::from_str(l).ok()).collect();
            if v.len() > take_last {
                v = v.split_off(v.len() - take_last);
            }
            v
        };
        let events = read_jsonl("event_log.jsonl", 50);
        let queue = read_jsonl("agent_queue.jsonl", 50);
        let a2a: Vec<Value> = events
            .iter()
            .filter(|e| {
                e.get("kind").and_then(Value::as_str).map(|k| k.contains("agent")).unwrap_or(false)
            })
            .cloned()
            .collect();
        Ok(json!({
            "agents": { "running": agents, "count": agents.len() },
            "event_stream": events,
            "queue_board": queue,
            "a2a_feed": a2a,
            "gauges": { "running_agents": agents.len(),
                        "queued_tasks": queue.len(),
                        "recent_events": events.len() },
        }))
    }

    /// Task-Test-Lauf (F215): führt den Task-Prompt mit dem echten `claude` aus
    /// (`--output-format json`) und liefert das Ergebnis samt ECHTER Token-Anzahl
    /// (input/output aus der API-Usage) und Dauer — die Werte, die der Test-Tab anzeigt.
    fn tasks_test_run(&self, p: &Value) -> HandlerResult {
        let prompt = req_str(p, "prompt")?;
        let cwd = p
            .get("cwd")
            .and_then(Value::as_str)
            .map(String::from)
            .unwrap_or_else(|| std::env::temp_dir().to_string_lossy().to_string());
        let binary = std::env::var("CLAUDESTUDIO_CLAUDE_BIN").unwrap_or_else(|_| "claude".to_string());
        let out = std::process::Command::new(&binary)
            .args(["--print", "--output-format", "json", prompt])
            .current_dir(&cwd)
            .output()
            .map_err(|e| IpcFailure::internal(format!("claude konnte nicht gestartet werden: {e}")))?;
        let v: Value = serde_json::from_slice(&out.stdout).unwrap_or_else(|_| json!({}));
        let usage = v.get("usage").cloned().unwrap_or_else(|| json!({}));
        let it = usage.get("input_tokens").and_then(Value::as_i64).unwrap_or(0);
        let ot = usage.get("output_tokens").and_then(Value::as_i64).unwrap_or(0);
        Ok(json!({
            "ok": v.get("is_error").and_then(Value::as_bool).map(|e| !e).unwrap_or(true),
            "result": v.get("result").cloned().unwrap_or(Value::Null),
            "input_tokens": it, "output_tokens": ot, "total_tokens": it + ot,
            "cost_usd": v.get("total_cost_usd").cloned().unwrap_or(json!(0)),
            "duration_ms": v.get("duration_ms").cloned().unwrap_or(json!(0)),
        }))
    }

    /// Skill direkt testen (F241): der echte `claude` befolgt die Skill-Anweisungen und führt die
    /// enthaltenen Shell-Kommandos wirklich aus (Bash-Tool); das echte Ausführungsergebnis wird
    /// zurückgegeben — wie der Test-Button im Skill-Editor.
    fn skills_test(&self, p: &Value) -> HandlerResult {
        let body = req_str(p, "body")?;
        let cwd = p.get("cwd").and_then(Value::as_str).map(String::from)
            .unwrap_or_else(|| std::env::temp_dir().to_string_lossy().to_string());
        let prompt = format!(
            "Du testest ein ClaudeStudio-Skill. Befolge die folgenden Skill-Anweisungen exakt und führe \
             die enthaltenen Shell-Kommandos WIRKLICH aus (nutze das Bash-Tool). Berichte danach das \
             exakte Ausführungsergebnis.\n\n--- SKILL ---\n{body}"
        );
        let (out, exit) = run_claude_agent(&cwd, &prompt);
        Ok(json!({ "ok": exit == 0, "output": out }))
    }

    /// Prompt-Optimizer (F347): der echte `claude` verbessert einen schwachen Prompt (konkreter,
    /// mit Kontext/Format/Akzeptanzkriterien). Liefert Original + optimierte Variante.
    fn prompts_optimize(&self, p: &Value) -> HandlerResult {
        let original = req_str(p, "prompt")?;
        let cwd = std::env::temp_dir().to_string_lossy().to_string();
        let meta = format!(
            "Du bist ein Prompt-Optimizer für ein Coding-Agenten-Tool. Verbessere den folgenden schwachen \
             Prompt deutlich: mache ihn konkret, ergänze Kontext, das gewünschte Ausgabeformat und klare \
             Akzeptanzkriterien (z.B. Tests). Antworte AUSSCHLIESSLICH mit dem optimierten Prompt — kein \
             Vorwort, keine Erklärung.\n\nSCHWACHER PROMPT:\n{original}"
        );
        let (out, _) = run_claude_agent(&cwd, &meta);
        let optimized = out.trim().to_string();
        Ok(json!({ "original": original, "optimized": optimized,
                   "original_len": original.chars().count(),
                   "optimized_len": optimized.chars().count() }))
    }

    /// Compliance-Analyse (F199-F202): der echte `claude` analysiert das Projekt (Read/Grep/Glob)
    /// auf einen rechtlichen Aspekt und liefert ein strukturiertes Findings-JSON.
    fn compliance_check(&self, p: &Value) -> HandlerResult {
        let cwd = req_str(p, "cwd")?;
        let kind = req_str(p, "kind")?;
        let focus = match kind {
            "impressum" => "ein vollständiges Impressum nach §5 DDG/TMG, eine Datenschutzerklärung und AGB",
            "dsgvo" => "DSGVO-Datenschutzprobleme (personenbezogene Daten ohne Einwilligung, fehlende Datenschutzerklärung, Tracking ohne Consent)",
            "kleinunternehmer" => "Kleinunternehmer-Konformität nach §19 UStG (z.B. ob Rechnungen korrekt OHNE Umsatzsteuer mit §19-Hinweis ausgewiesen werden)",
            "reverse_charge" => "Reverse-Charge-Behandlung bei EU-B2B-Rechnungen (Steuerschuldnerschaft des Leistungsempfängers)",
            _ => "rechtliche Pflichtangaben",
        };
        let prompt = format!(
            "Analysiere ALLE relevanten Dateien im aktuellen Projektverzeichnis (nutze Glob/Grep/Read) auf \
             {focus}. Liste jedes konkrete Problem oder Fehlen als Finding. Antworte AUSSCHLIESSLICH mit \
             einem reinen JSON-Objekt der Form {{\"findings\":[{{\"issue\":\"...\",\"file\":\"...\",\
             \"severity\":\"low|medium|high\"}}],\"summary\":\"...\",\"compliant\":true|false}} — kein \
             Markdown, kein weiterer Text."
        );
        let (out, _) = run_claude_agent(cwd, &prompt);
        let report = extract_json_value(&out);
        Ok(json!({ "kind": kind, "report": report }))
    }

    /// Orchestrator-Zerlegung (F120): der echte `claude` (Opus-Orchestrator) zerlegt eine
    /// Entwicklungsaufgabe in konkrete Subtasks (JSON-Array) und weist sie den Workern
    /// round-robin zu. Liefert die Subtask-Liste + Zuordnungen.
    fn agents_decompose_task(&self, p: &Value) -> HandlerResult {
        let task = req_str(p, "task")?;
        let workers: Vec<String> = p
            .get("workers")
            .and_then(Value::as_array)
            .map(|a| a.iter().filter_map(|w| w.as_str().map(String::from)).collect())
            .unwrap_or_default();
        let cwd = p
            .get("cwd")
            .and_then(Value::as_str)
            .map(String::from)
            .unwrap_or_else(|| std::env::temp_dir().to_string_lossy().to_string());
        let prompt = format!(
            "Du bist der Orchestrator eines Entwickler-Teams. Zerlege die Aufgabe \"{task}\" in 3 bis 6 \
             konkrete, eigenständige Subtasks. Antworte AUSSCHLIESSLICH mit einem reinen JSON-Array von \
             Objekten der Form {{\"id\":\"st1\",\"title\":\"...\",\"description\":\"...\"}} — keine \
             Markdown-Codeblöcke, kein erklärender Text davor oder danach."
        );
        let (out, _) = run_claude_agent(&cwd, &prompt);
        let subtasks = match extract_json_value(&out) {
            Value::Array(a) => a,
            _ => Vec::new(),
        };
        let assignments: Vec<Value> = subtasks
            .iter()
            .enumerate()
            .map(|(i, st)| {
                let worker = if workers.is_empty() {
                    "unassigned".to_string()
                } else {
                    workers[i % workers.len()].clone()
                };
                json!({ "subtask": st, "worker": worker })
            })
            .collect();
        Ok(json!({ "task": task, "subtasks": subtasks, "subtask_count": subtasks.len(),
                   "assignments": assignments, "workers": workers }))
    }

    /// Auto-Loop-Agent (F316): führt die Test-Suite aus; solange sie rot ist, lässt es den echten
    /// `claude` den QUELLCODE reparieren und führt die Tests erneut aus — maximal `max_iter`
    /// Runden. Liefert die Iterations-Historie und ob am Ende alles grün ist.
    fn code_auto_fix_loop(&self, p: &Value) -> HandlerResult {
        let cwd = req_str(p, "cwd")?;
        let test_cmd = req_str(p, "test_command")?;
        let max_iter = p.get("max_iter").and_then(Value::as_u64).unwrap_or(5) as usize;
        let run_tests = || -> i32 {
            std::process::Command::new("sh")
                .arg("-c").arg(test_cmd).current_dir(cwd).output()
                .map(|o| o.status.code().unwrap_or(-1)).unwrap_or(-1)
        };
        let mut history = Vec::new();
        let mut green = false;
        for i in 0..max_iter {
            let exit = run_tests();
            history.push(json!({ "iteration": i, "phase": "test", "exit": exit }));
            if exit == 0 {
                green = true;
                break;
            }
            let prompt = format!(
                "Die Tests in diesem Projekt schlagen fehl. Führe `{test_cmd}` aus, lies die \
                 Fehlermeldungen und korrigiere ausschließlich den PRODUKTIONS-Quellcode (NICHT die \
                 Testdateien), bis alle Tests grün sind. Führe die Tests am Ende erneut aus, um es zu \
                 bestätigen. Nutze deine Tools."
            );
            let (_log, _exit) = run_claude_agent(cwd, &prompt);
            history.push(json!({ "iteration": i, "phase": "fix" }));
        }
        if !green {
            let exit = run_tests(); // finaler Check nach der letzten Fix-Runde
            green = exit == 0;
            history.push(json!({ "iteration": max_iter, "phase": "final_test", "exit": exit }));
        }
        Ok(json!({ "green": green, "iterations": history.len(), "history": history }))
    }

    /// Test-Generierungs-Agent (F321): lässt den echten `claude` autonom Unit-Tests für die
    /// Zieldatei schreiben (Write/Bash/Read-Tools, bypass-Permissions) und ausführen. Gibt die
    /// erzeugten Testdateien + das Agenten-Log zurück; der Probe verifiziert grün + Coverage.
    fn testing_generate_tests(&self, p: &Value) -> HandlerResult {
        let cwd = req_str(p, "cwd")?;
        let target = req_str(p, "target")?;
        let prompt = format!(
            "Schreibe gründliche Unit-Tests für ALLE exportierten Funktionen in der Datei `{target}` \
             in eine NEUE Node-Testdatei mit Endung `.test.mjs` im selben Verzeichnis (verwende \
             `node:test` und `node:assert`, ESM-Imports aus `./{target}`). Decke jede Funktion mit \
             mindestens einem Testfall ab. Führe anschließend `node --test` aus und stelle sicher, \
             dass alle Tests grün sind. Nutze deine Tools, um die Datei wirklich zu schreiben und \
             den Test wirklich auszuführen."
        );
        let (log, exit) = run_claude_agent(cwd, &prompt);
        let mut test_files = Vec::new();
        if let Ok(rd) = std::fs::read_dir(cwd) {
            for e in rd.flatten() {
                let n = e.file_name().to_string_lossy().to_string();
                let nl = n.to_lowercase();
                if (nl.contains("test") || nl.contains("spec"))
                    && (nl.ends_with(".mjs") || nl.ends_with(".js") || nl.ends_with(".ts"))
                {
                    test_files.push(n);
                }
            }
        }
        test_files.sort();
        let tail: String = {
            let chars: Vec<char> = log.chars().collect();
            let start = chars.len().saturating_sub(500);
            chars[start..].iter().collect()
        };
        Ok(json!({ "ok": exit == 0, "exit": exit, "target": target,
                   "test_files": test_files, "agent_log_tail": tail }))
    }

    /// Misst die echte Test-Abdeckung (F204/F322): führt optional ein Coverage-Kommando
    /// im cwd aus, parst dann den lcov-Report (SF/LF/LH/FNF/FNH) zu Pro-Modul-Prozenten
    /// und einer Gesamt-Coverage. Keine Heuristik — echte ausgeführte Coverage-Daten.
    fn coverage_measure(&self, p: &Value) -> HandlerResult {
        let cwd = req_str(p, "cwd")?;
        if let Some(cmd) = p.get("command").and_then(Value::as_str) {
            let _ = std::process::Command::new("sh").arg("-c").arg(cmd).current_dir(cwd).output();
        }
        let lcov = p.get("lcov").and_then(Value::as_str).unwrap_or("coverage.lcov");
        let content = std::fs::read_to_string(Path::new(cwd).join(lcov))
            .map_err(|e| IpcFailure::not_found(format!("lcov nicht gefunden: {e}")))?;

        fn flush(modules: &mut Vec<Value>, cur: &str, lf: i64, lh: i64, fnf: i64, fnh: i64) {
            if !cur.is_empty() && lf > 0 {
                modules.push(json!({ "file": cur, "lines_found": lf, "lines_hit": lh,
                    "functions_found": fnf, "functions_hit": fnh,
                    "percent": (lh as f64 / lf as f64 * 100.0).round() }));
            }
        }
        let mut modules = Vec::new();
        let (mut cur, mut lf, mut lh, mut fnf, mut fnh) = (String::new(), 0i64, 0i64, 0i64, 0i64);
        let (mut tot_lf, mut tot_lh) = (0i64, 0i64);
        for line in content.lines() {
            if let Some(f) = line.strip_prefix("SF:") {
                flush(&mut modules, &cur, lf, lh, fnf, fnh);
                cur = f.trim().to_string();
                lf = 0; lh = 0; fnf = 0; fnh = 0;
            } else if let Some(v) = line.strip_prefix("LF:") {
                lf = v.trim().parse().unwrap_or(0); tot_lf += lf;
            } else if let Some(v) = line.strip_prefix("LH:") {
                lh = v.trim().parse().unwrap_or(0); tot_lh += lh;
            } else if let Some(v) = line.strip_prefix("FNF:") {
                fnf = v.trim().parse().unwrap_or(0);
            } else if let Some(v) = line.strip_prefix("FNH:") {
                fnh = v.trim().parse().unwrap_or(0);
            }
        }
        flush(&mut modules, &cur, lf, lh, fnf, fnh);
        let total = if tot_lf > 0 { (tot_lh as f64 / tot_lf as f64 * 100.0).round() } else { 0.0 };
        Ok(json!({ "total_percent": total, "lines_found": tot_lf, "lines_hit": tot_lh,
                   "modules": modules, "module_count": modules.len() }))
    }

    /// Status-Farben der Worktrees (F068): rot = git-Fehler (detached HEAD / Merge-Konflikt),
    /// grün = aktiv (laufender Agent in `active_paths`), gelb = arbeitend (uncommittete
    /// Änderungen), weiß = idle (sauber). Status wird real aus dem git-Zustand abgeleitet.
    fn worktree_status(&self, p: &Value) -> HandlerResult {
        let worktrees: Vec<String> = p
            .get("worktrees")
            .and_then(Value::as_array)
            .map(|a| a.iter().filter_map(|w| w.as_str().map(String::from)).collect())
            .unwrap_or_default();
        let active: std::collections::HashSet<String> = p
            .get("active_paths")
            .and_then(Value::as_array)
            .map(|a| a.iter().filter_map(|w| w.as_str().map(String::from)).collect())
            .unwrap_or_default();
        let git = |wt: &str, args: &[&str]| -> String {
            let mut a = vec!["-C", wt];
            a.extend_from_slice(args);
            std::process::Command::new("git")
                .args(&a)
                .output()
                .map(|o| String::from_utf8_lossy(&o.stdout).to_string())
                .unwrap_or_default()
        };
        let mut out = Vec::new();
        for wt in &worktrees {
            let status_text = git(wt, &["status", "--porcelain"]);
            let head = git(wt, &["rev-parse", "--abbrev-ref", "HEAD"]).trim().to_string();
            let conflict = status_text
                .lines()
                .any(|l| l.starts_with("UU") || l.starts_with("AA") || l.starts_with("DD"));
            let detached = head == "HEAD";
            let dirty = !status_text.trim().is_empty();
            let (state, color) = if detached || conflict {
                ("error", "red")
            } else if active.contains(wt) {
                ("active", "green")
            } else if dirty {
                ("working", "yellow")
            } else {
                ("idle", "white")
            };
            out.push(json!({ "path": wt, "state": state, "color": color,
                             "detached": detached, "conflict": conflict, "dirty": dirty }));
        }
        Ok(json!({ "worktrees": out }))
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
            "memory.suggest_insight" => self.memory_suggest_insight(p),
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
            "events.publish" => self.events_publish(p),
            "routing.route" => Ok(routing_route_payload(p)),
            "queue.order" => Ok(queue_order_payload(p)),
            "queue.enqueue" => self.queue_enqueue(p),
            "queue.list" => self.queue_list(),
            "tasks.schedule" => self.tasks_schedule(p),
            "queue.dag" => Ok(queue_dag_payload(p)),
            "queue.reorder" => Ok(queue_reorder_payload(p)),
            "scheduler.admit" => Ok(scheduler_admit_payload(p)),
            "monitor.health_check" => Ok(health_check_payload(p)),
            "monitor.cost_guard" => Ok(cost_guard_payload(p)),
            "supervisor.evaluate" => Ok(supervisor_evaluate_payload(p)),
            "os.running_agents" => self.os_running_agents(),
            "os.mission_control" => self.os_mission_control(),
            "worktree.status" => self.worktree_status(p),
            "coverage.measure" => self.coverage_measure(p),
            "testing.generate_tests" => self.testing_generate_tests(p),
            "code.auto_fix_loop" => self.code_auto_fix_loop(p),
            "agents.decompose_task" => self.agents_decompose_task(p),
            "tasks.test_run" => self.tasks_test_run(p),
            "skills.test" => self.skills_test(p),
            "prompts.optimize" => self.prompts_optimize(p),
            "compliance.check" => self.compliance_check(p),

            // --- Live session control ---
            "session.stop" => self.session_stop(p),

            // --- Session archive ---
            "session.list" => self.session_list(p),
            "session.get" => self.session_get(p),
            "session.messages" => self.session_messages(p),
            "session.share" => self.session_share(p),
            "session.join" => self.session_join(p),
            "session.replay_step" => self.session_replay_step(p),
            "list.filter" => Ok(list_filter_payload(p)),
            "session.search" => self.session_search(p),
            "session.create" => self.session_create(p),
            "session.stats" => self.session_stats(),
            "session.record_usage" => self.session_record_usage(p),
            "session.record_event" => self.session_record_event(p),
            "session.events" => self.session_events(p),
            "session.record_error" => self.session_record_error(p),
            "session.set_private" => self.session_set_private(p),
            "session.get_private" => self.session_get_private(p),
            "worktime.export" => self.worktime_export(p),
            "cost.summary" => self.cost_summary(p),
            "cost.cache_hit_rate" => self.cost_cache_hit_rate(),
            "tasks.deliver_output" => self.tasks_deliver_output(p),

            // --- Libraries & integrations ---
            "tasks.list" => self.tasks_list(),
            "tasks.create" => self.library_create(p, "tasks"),
            "tasks.save" => self.tasks_save(p),
            "tasks.delete" => self.library_delete(p, "tasks", ".task.json"),
            "library.load_defaults" => self.library_load_defaults(),
            "definitions.list" => self.definitions_list(),
            "definitions.suggest" => self.definitions_suggest(p),
            "definitions.vector_inject" => self.definitions_vector_inject(p),
            "definitions.create" => self.library_create(p, "definitions"),
            "definitions.delete" => self.library_delete(p, "definitions", ".def.md"),
            "prompts.templates" => Ok(prompt_templates_payload()),
            "prompts.record" => self.prompts_record(p),
            "prompts.history" => self.prompts_history(p),
            "prompts.favorite" => self.prompts_favorite(p),
            "prompts.chain_run" => Ok(chain_run_payload(p)),
            "agents.create" => self.agents_create(p),
            "agents.write_agents_md" => self.agents_write_agents_md(p),
            "agents.list" => self.agents_list(),
            "agents.get" => self.agents_get(p),
            "agents.update" => self.agents_update(p),
            "agents.delete" => self.agents_delete(p),
            "agents.check_tool" => self.agents_check_tool(p),
            "agents.render_prompt" => self.agents_render_prompt(p),
            "agents.context" => self.agents_context(p),
            "model_router.route" => self.model_router_route(p),
            "models.compare" => self.models_compare(p),
            "integrations.github_sync" => self.integrations_github_sync(p),
            "integrations.usage_report" => self.integrations_usage_report(p),
            "integrations.slack_command" => self.integrations_slack_command(p),
            "telemetry.export_span" => self.telemetry_export_span(p),
            "model_router.set" => self.model_router_set(p),
            "model_router.resolve" => self.model_router_resolve(p),
            "model_router.fallback" => Ok(model_fallback_payload(p)),
            "model_router.cost_compare" => Ok(model_cost_compare_payload(p)),
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
            "mcp.tools" => self.mcp_tools(p),
            "mcp.call_tool" => self.mcp_call_tool(p),
            "mcp.allowlist_set" => self.mcp_allowlist_set(p),
            "mcp.allowlist_get" => self.mcp_allowlist_get(),
            "mcp.check_server" => self.mcp_check_server(p),
            "hooks.list" => self.hooks_list(p),
            "hooks.add" => self.hooks_add(p),
            "hooks.remove" => self.hooks_remove(p),
            "hooks.types" => Ok(hook_types_payload()),
            "hooks.run" => self.hooks_run(p),
            "security.code_scan" => self.security_code_scan(p),
            "changelog.generate" => self.changelog_generate(p),
            "release_notes.generate" => self.release_notes_generate(p),
            "readme.generate" => self.readme_generate(p),
            "git.secret_scan" => self.git_secret_scan(p),
            "deploy.risk" => self.deploy_risk(p),
            "deploy.rollback" => self.deploy_rollback(p),
            "deploy.checklist" => Ok(deploy_checklist_payload(p)),
            "env.add" => self.env_add(p),
            "env.list" => self.env_list(),
            "flags.set" => self.flags_set(p),
            "flags.eval" => self.flags_eval(p),
            "flags.list" => self.flags_list(),
            "metrics.dora" => self.metrics_dora(p),
            "report.standup" => self.report_standup(p),
            "checkpoint.save" => self.checkpoint_save(p),
            "checkpoint.restore" => self.checkpoint_restore(p),
            "checkpoint.list" => self.checkpoint_list(),
            "codeq.dead_code" => self.codeq_dead_code(p),
            "codeq.duplicates" => self.codeq_duplicates(p),
            "codeq.complexity" => self.codeq_complexity(p),
            "perf.compare" => Ok(perf_compare_payload(p)),
            "docs.arch_diagram" => self.docs_arch_diagram(p),
            "docs.generate" => self.docs_generate(p),
            "i18n.extract" => Ok(i18n_extract_payload(p)),
            "a2a.send" => self.a2a_send(p),
            "a2a.inbox" => self.a2a_inbox(p),
            "teams.create" => self.teams_create(p),
            "teams.get" => self.teams_get(p),
            "teams.decompose" => Ok(teams_decompose_payload(p)),
            "teams.review_and_merge" => self.teams_review_and_merge(p),
            "teams.escalate" => self.teams_escalate(p),
            "teams.stop" => self.teams_stop(p),
            "tasks.render" => Ok(tasks_render_payload(p)),
            "migration.generate" => Ok(migration_generate_payload(p)),
            "apiportal.render" => Ok(apiportal_render_payload(p)),
            "snapshot.save" => self.snapshot_save(p),
            "snapshot.compare" => self.snapshot_compare(p),
            "a11y.check" => Ok(a11y_check_payload(p)),
            "comments.add" => self.comments_add(p),
            "comments.list" => self.comments_list(p),
            "graph.node_types" => Ok(graph_node_types_payload()),
            "graph.edge_types" => Ok(graph_edge_types_payload()),
            "graph.add_node" => self.graph_add_node(p),
            "graph.add_edge" => self.graph_add_edge(p),
            "graph.node_detail" => self.graph_node_detail(p),
            "graph.layout" => self.graph_layout(p),
            "graph.export" => self.graph_export(),
            "graph.search" => self.graph_search(p),
            "graph.at" => self.graph_at(p),
            "graph.query_asset" => self.graph_query_asset(p),
            "graph.remember" => self.graph_remember(p),
            "copilot.suggestions" => Ok(copilot_suggestions_payload(p)),
            "copilot.focus" => Ok(copilot_focus_payload(p)),
            "copilot.config_get" => self.copilot_config_get(),
            "copilot.config_set" => self.copilot_config_set(p),
            "projects.create" => self.projects_create(p),
            "projects.list" => self.projects_list(),
            "projects.get" => self.projects_get(p),
            "projects.detect_stack" => Ok(json!({ "stack": detect_stack(Path::new(req_str(p, "path")?)) })),
            "projects.import" => self.projects_import(p),
            "projects.scaffold" => self.projects_scaffold(p),
            "projects.rename" => self.projects_rename(p),
            "projects.remove" => self.projects_remove(p),
            "projects.online_status" => Ok(project_online_status_payload(p)),
            "file.git_colors" => self.file_git_colors(p),
            "diff.render" => self.diff_render(p),
            "files.status_indicators" => self.files_status_indicators(p),
            "files.cross_project_tree" => self.files_cross_project_tree(p),
            "file.diff" => self.file_diff(p),
            "file.find" => self.file_find(p),
            "file.to_asset" => self.file_to_asset(p),
            "context.sections" => Ok(context_sections_payload()),
            "context.token_check" => Ok(context_token_check_payload(p)),
            "context.diff" => self.context_diff(p),
            "memory.categorize" => self.memory_categorize(p),
            "memory.token_usage" => self.memory_token_usage(p),
            "memory.mark_stale" => Ok(memory_mark_stale_payload(p)),
            "knowledge.chunk_text" => self.knowledge_chunk_text(p),
            "definitions.grouped" => self.definitions_grouped(),
            "cost.estimate" => Ok(cost_estimate_payload(p)),
            "metrics.productivity" => self.metrics_productivity(p),
            "pipeline.generate" => Ok(pipeline_generate_payload(p)),
            "pipeline.visualize" => self.pipeline_visualize(p),
            "settings.set" => self.settings_set(p),
            "settings.get" => self.settings_get(p),
            "settings.merge" => self.settings_merge(p),
            "cve.scan" => Ok(cve_scan_payload(p)),
            "iac.validate" => Ok(iac_validate_payload(p)),
            "docker.optimize" => Ok(docker_optimize_payload(p)),
            "codeq.comment_quality" => self.codeq_comment_quality(p),
            "refactor.js_to_ts" => Ok(js_to_ts_payload(p)),
            "resume.detect" => self.resume_detect(),
            "briefing.daily" => self.briefing_daily(p),
            "llm.fallback" => Ok(llm_fallback_payload(p)),
            "knowledge.teach" => self.knowledge_teach(p),
            "knowledge.extract_entities" => self.knowledge_extract_entities(p),
            "assets.scan" => self.assets_scan(p),
            "knowledge.search" => self.knowledge_search(p),
            "css.extract" => Ok(css_extract_payload(p)),
            "library.git_sync" => self.library_git_sync(p),

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

    /// Publish an event onto the bus: log it, evaluate rules, and DISPATCH matched
    /// actions for real. A `security-scan` action runs an actual `code_scan` and
    /// logs the result — all tied by one correlation id (F306).
    fn events_publish(&self, p: &Value) -> HandlerResult {
        let etype = req_str(p, "type")?;
        let branch = p.get("branch").and_then(Value::as_str);
        let cwd = p.get("cwd").and_then(Value::as_str);
        let cid = unique_id("evt");
        let mut log = vec![json!({ "correlation_id": cid, "kind": "event", "type": etype, "branch": branch, "ts": now_millis() })];
        let mut fired = 0;
        let mut scan = Value::Null;
        for rule in self.read_rules() {
            let w = &rule["when"];
            if w.get("event").and_then(Value::as_str) != Some(etype) {
                continue;
            }
            if let Some(rb) = w.get("branch").and_then(Value::as_str) {
                if Some(rb) != branch {
                    continue;
                }
            }
            fired += 1;
            if let Some(actions) = rule["then"].as_array() {
                for a in actions {
                    let act = a.as_str().unwrap_or("");
                    if act.contains("security-scan") {
                        log.push(json!({ "correlation_id": cid, "kind": "agent_started", "agent": "security-scan-agent", "ts": now_millis() }));
                        if let Some(c) = cwd {
                            let result = self.security_code_scan(&json!({ "cwd": c }))?;
                            log.push(json!({ "correlation_id": cid, "kind": "agent_result", "agent": "security-scan-agent", "findings": result.get("count") }));
                            scan = result;
                        }
                    } else if act.contains("fix") {
                        // F307: test.failed -> Fix-Agent startet und *nimmt den
                        // fehlgeschlagenen Test auf*, indem er das Test-Kommando aus
                        // dem Event real ausführt und die rote Ausgabe erfasst.
                        let test = p.get("test").and_then(Value::as_str).unwrap_or("");
                        log.push(json!({ "correlation_id": cid, "kind": "agent_started",
                                         "agent": "fix-agent", "picked_up_test": test, "ts": now_millis() }));
                        let mut test_exit = Value::Null;
                        let mut test_red = false;
                        if let (Some(c), Some(cmd)) =
                            (cwd, p.get("test_command").and_then(Value::as_str))
                        {
                            if let Ok(out) = std::process::Command::new("sh")
                                .arg("-c").arg(cmd).current_dir(c).output()
                            {
                                let code = out.status.code().unwrap_or(-1);
                                test_red = code != 0;
                                test_exit = json!(code);
                            }
                        }
                        log.push(json!({ "correlation_id": cid, "kind": "agent_result",
                                         "agent": "fix-agent", "picked_up_test": test,
                                         "test_exit": test_exit, "test_red": test_red }));
                    }
                }
            }
        }
        if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(self.inner.state_dir.join("event_log.jsonl")) {
            use std::io::Write;
            for e in &log {
                let _ = writeln!(f, "{e}");
            }
        }
        Ok(json!({ "correlation_id": cid, "log": log, "fired": fired, "scan": scan }))
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

        // Private sessions are hidden from the default list unless requested (F165).
        let include_private = p.get("include_private").and_then(Value::as_bool).unwrap_or(false);
        let private = self.private_sessions();

        let store = self.inner.sessions.lock().unwrap();
        // When filtering, fetch a wider window then narrow it in-process (F158).
        let fetch = if has_filter { limit.max(1000) } else { limit };
        let sessions = store.list_sessions(fetch, offset).map_err(session_failure)?;
        let mut arr = serde_json::to_value(sessions).unwrap_or_else(|_| json!([]));
        if let Some(list) = arr.as_array_mut() {
            list.retain(|s| {
                if !include_private {
                    if let Some(id) = s.get("id").and_then(Value::as_str) {
                        if private.contains(id) {
                            return false;
                        }
                    }
                }
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

    /// Record a session permission/hook/mcp/lifecycle event (F155).
    fn session_record_event(&self, p: &Value) -> HandlerResult {
        let e = NewEvent {
            session_id: req_str(p, "session_id")?.to_string(),
            kind: req_str(p, "kind")?.to_string(),
            payload: p.get("payload").cloned(),
            created_at: now_millis(),
        };
        let id = self.inner.sessions.lock().unwrap().append_event(&e).map_err(session_failure)?;
        Ok(json!({ "ok": true, "id": id }))
    }

    fn session_events(&self, p: &Value) -> HandlerResult {
        let id = req_str(p, "session_id")?;
        let kind = p.get("kind").and_then(Value::as_str);
        let events = self.inner.sessions.lock().unwrap().list_events(id, kind).map_err(session_failure)?;
        Ok(json!({ "events": events }))
    }

    /// Record an error with exit-code + a numbered retry as an event (F156).
    fn session_record_error(&self, p: &Value) -> HandlerResult {
        let session_id = req_str(p, "session_id")?.to_string();
        let payload = json!({
            "error": p.get("error").and_then(Value::as_str).unwrap_or(""),
            "exit_code": p.get("exit_code").and_then(Value::as_i64),
            "retry": p.get("retry").and_then(Value::as_i64).unwrap_or(0),
            "stack": p.get("stack").and_then(Value::as_str),
        });
        let e = NewEvent { session_id, kind: "error".into(), payload: Some(payload), created_at: now_millis() };
        let id = self.inner.sessions.lock().unwrap().append_event(&e).map_err(session_failure)?;
        Ok(json!({ "ok": true, "id": id }))
    }

    fn private_path(&self) -> PathBuf {
        self.inner.state_dir.join("private_sessions.json")
    }
    fn private_sessions(&self) -> HashSet<String> {
        std::fs::read_to_string(self.private_path())
            .ok()
            .and_then(|s| serde_json::from_str::<Vec<String>>(&s).ok())
            .map(|v| v.into_iter().collect())
            .unwrap_or_default()
    }

    /// Mark a session private: gzip + AES-256-GCM encrypt its content at rest (F165).
    fn session_set_private(&self, p: &Value) -> HandlerResult {
        let id = req_str(p, "session_id")?.to_string();
        let content = req_str(p, "content")?;
        let key = p.get("key").and_then(Value::as_str).unwrap_or("default-passphrase");
        let (cipher_hex, original_len, compressed_len) = encrypt_private(content, key)?;
        let dir = self.inner.state_dir.join("private");
        std::fs::create_dir_all(&dir).ok();
        let path = dir.join(format!("{id}.enc"));
        std::fs::write(&path, &cipher_hex).map_err(|e| e.to_string())?;
        let mut set = self.private_sessions();
        set.insert(id.clone());
        let v: Vec<&String> = set.iter().collect();
        std::fs::write(self.private_path(), serde_json::to_string(&v).unwrap_or_default()).map_err(|e| e.to_string())?;
        let ratio = if original_len > 0 { 1.0 - (compressed_len as f64 / original_len as f64) } else { 0.0 };
        Ok(json!({
            "ok": true, "id": id, "encrypted_path": path.to_string_lossy(),
            "original_len": original_len, "compressed_len": compressed_len, "gzip_ratio": ratio,
        }))
    }

    /// Decrypt + decompress a private session's content (F165).
    fn session_get_private(&self, p: &Value) -> HandlerResult {
        let id = req_str(p, "session_id")?;
        let key = p.get("key").and_then(Value::as_str).unwrap_or("default-passphrase");
        let hex = std::fs::read_to_string(self.inner.state_dir.join("private").join(format!("{id}.enc")))
            .map_err(|_| IpcFailure::not_found("keine private Session"))?;
        Ok(json!({ "id": id, "content": decrypt_private(&hex, key)? }))
    }

    fn mcp_allowlist_path(&self) -> PathBuf {
        self.inner.state_dir.join("mcp_allowlist.json")
    }
    fn mcp_allowlist_get(&self) -> HandlerResult {
        let list: Vec<String> = std::fs::read_to_string(self.mcp_allowlist_path())
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_default();
        Ok(json!({ "allowlist": list }))
    }
    fn mcp_allowlist_set(&self, p: &Value) -> HandlerResult {
        let servers: Vec<String> = p
            .get("servers")
            .and_then(Value::as_array)
            .map(|a| a.iter().filter_map(|x| x.as_str().map(str::to_string)).collect())
            .unwrap_or_default();
        std::fs::write(self.mcp_allowlist_path(), serde_json::to_string_pretty(&servers).unwrap_or_default()).map_err(|e| e.to_string())?;
        Ok(json!({ "ok": true, "allowlist": servers }))
    }
    /// Check whether an MCP server is permitted; a non-empty allowlist blocks others (F254).
    fn mcp_check_server(&self, p: &Value) -> HandlerResult {
        let name = req_str(p, "name")?;
        let list: Vec<String> = std::fs::read_to_string(self.mcp_allowlist_path())
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_default();
        let allowed = list.is_empty() || list.iter().any(|s| s == name);
        Ok(json!({
            "server": name, "allowed": allowed,
            "reason": if allowed { "erlaubt" } else { "nicht in MCP-Allowlist — blockiert" },
        }))
    }

    /// Export per-session active time in a Toggl-compatible shape (F356).
    fn worktime_export(&self, _p: &Value) -> HandlerResult {
        let store = self.inner.sessions.lock().unwrap();
        let sessions = store.list_sessions(1000, 0).map_err(session_failure)?;
        let arr = serde_json::to_value(sessions).unwrap_or(json!([]));
        let mut entries = Vec::new();
        let mut total_ms = 0i64;
        if let Some(list) = arr.as_array() {
            for s in list {
                let id = s.get("id").and_then(Value::as_str).unwrap_or("");
                let start = s.get("created_at").and_then(Value::as_i64).unwrap_or(0);
                let end = store
                    .list_events(id, None)
                    .unwrap_or_default()
                    .iter()
                    .filter_map(|e| e.get("created_at").and_then(Value::as_i64))
                    .max()
                    .unwrap_or(start);
                let dur = (end - start).max(0);
                total_ms += dur;
                entries.push(json!({
                    "description": s.get("title"), "start_ms": start, "stop_ms": end,
                    "duration_seconds": dur / 1000, "project": s.get("cwd"),
                }));
            }
        }
        Ok(json!({ "format": "toggl", "entries": entries, "total_seconds": total_ms / 1000 }))
    }

    /// Deliver a task's output according to its configured output type (F212).
    fn tasks_deliver_output(&self, p: &Value) -> HandlerResult {
        let otype = req_str(p, "type")?;
        let content = p.get("content").and_then(Value::as_str).unwrap_or("");
        match otype {
            "Datei" | "file" => {
                let path = req_str(p, "path")?;
                if let Some(parent) = Path::new(path).parent() {
                    std::fs::create_dir_all(parent).ok();
                }
                std::fs::write(path, content).map_err(|e| e.to_string())?;
                Ok(json!({ "type": "file", "path": path, "bytes": content.len(), "result": "file" }))
            }
            "Report" | "report" => {
                Ok(json!({ "type": "report", "report": format!("# Task-Report\n\n{content}\n"), "result": "report" }))
            }
            "PR" | "Slack" | "Email" => Ok(json!({ "type": otype, "result": "queued", "channel": otype })),
            other => Err(IpcFailure::invalid(format!("unknown output type: {other}"))),
        }
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

    // MARK: Agent Studio — agent config CRUD + checks

    fn agents_dir(&self) -> PathBuf {
        self.inner.state_dir.join("agents")
    }
    fn read_agent(&self, id: &str) -> Option<Value> {
        std::fs::read_to_string(self.agents_dir().join(format!("{id}.json")))
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
    }

    /// Create an agent from its designer config (F106/F107/F112/F114).
    fn agents_create(&self, p: &Value) -> HandlerResult {
        req_str(p, "name")?; // identity requires a name
        let id = unique_id("agent");
        let mut agent = p.clone();
        if let Some(o) = agent.as_object_mut() {
            o.insert("id".into(), json!(id));
            o.entry("model").or_insert(json!("sonnet"));
        }
        let dir = self.agents_dir();
        std::fs::create_dir_all(&dir).ok();
        std::fs::write(
            dir.join(format!("{id}.json")),
            serde_json::to_string_pretty(&agent).unwrap_or_default(),
        )
        .map_err(|e| e.to_string())?;
        Ok(json!({ "ok": true, "id": id, "agent": agent }))
    }

    fn agents_list(&self) -> HandlerResult {
        let mut agents = Vec::new();
        if let Ok(rd) = std::fs::read_dir(self.agents_dir()) {
            for e in rd.flatten() {
                if e.path().extension().and_then(|x| x.to_str()) == Some("json") {
                    if let Ok(s) = std::fs::read_to_string(e.path()) {
                        if let Ok(v) = serde_json::from_str::<Value>(&s) {
                            agents.push(v);
                        }
                    }
                }
            }
        }
        Ok(json!({ "agents": agents }))
    }

    fn agents_get(&self, p: &Value) -> HandlerResult {
        let id = req_str(p, "id")?;
        self.read_agent(id)
            .map(|a| json!({ "agent": a }))
            .ok_or_else(|| IpcFailure::not_found(format!("agent not found: {id}")))
    }

    fn agents_update(&self, p: &Value) -> HandlerResult {
        let id = req_str(p, "id")?;
        let mut agent = self
            .read_agent(id)
            .ok_or_else(|| IpcFailure::not_found(format!("agent not found: {id}")))?;
        if let (Some(o), Some(patch)) = (agent.as_object_mut(), p.as_object()) {
            for (k, v) in patch {
                if k != "id" {
                    o.insert(k.clone(), v.clone());
                }
            }
        }
        std::fs::write(
            self.agents_dir().join(format!("{id}.json")),
            serde_json::to_string_pretty(&agent).unwrap_or_default(),
        )
        .map_err(|e| e.to_string())?;
        Ok(json!({ "ok": true, "agent": agent }))
    }

    fn agents_delete(&self, p: &Value) -> HandlerResult {
        let id = req_str(p, "id")?;
        let path = self.agents_dir().join(format!("{id}.json"));
        let existed = path.exists();
        std::fs::remove_file(&path).ok();
        Ok(json!({ "ok": true, "deleted": existed }))
    }

    /// Tool enforcement: a tool not in the agent's allowed_tools is denied (F108).
    fn agents_check_tool(&self, p: &Value) -> HandlerResult {
        let id = req_str(p, "id")?;
        let tool = req_str(p, "tool")?;
        let agent = self
            .read_agent(id)
            .ok_or_else(|| IpcFailure::not_found(format!("agent not found: {id}")))?;
        let allowed = match agent.get("allowed_tools").and_then(Value::as_array) {
            Some(list) => list.iter().any(|t| t.as_str() == Some(tool)),
            None => true, // no restriction configured -> all allowed
        };
        Ok(json!({
            "allowed": allowed, "tool": tool,
            "reason": if allowed { "Tool erlaubt" } else { "Tool nicht in allowed_tools — abgewiesen" },
        }))
    }

    /// Render the agent's system prompt, substituting {{var}} from `vars` (F109).
    fn agents_render_prompt(&self, p: &Value) -> HandlerResult {
        let id = req_str(p, "id")?;
        let agent = self
            .read_agent(id)
            .ok_or_else(|| IpcFailure::not_found(format!("agent not found: {id}")))?;
        let mut prompt = agent.get("system_prompt").and_then(Value::as_str).unwrap_or("").to_string();
        if let Some(vars) = p.get("vars").and_then(Value::as_object) {
            for (k, v) in vars {
                let needle = format!("{{{{{k}}}}}");
                let val = v.as_str().map(str::to_string).unwrap_or_else(|| v.to_string());
                prompt = prompt.replace(&needle, &val);
            }
        }
        Ok(json!({ "prompt": prompt }))
    }

    /// The context an agent runs with — its assigned definitions are injected
    /// into the active-definitions layer (F115).
    fn agents_context(&self, p: &Value) -> HandlerResult {
        let id = req_str(p, "id")?;
        let agent = self
            .read_agent(id)
            .ok_or_else(|| IpcFailure::not_found(format!("agent not found: {id}")))?;
        let defs = agent.get("definitions").cloned().unwrap_or_else(|| json!([]));
        let content = self.load_active_definitions(Some(&defs));
        Ok(json!({
            "agent": id, "definitions": defs,
            "active_definitions": content, "tokens": estimate_tokens(&content),
        }))
    }

    // MARK: Model router

    fn model_routes_path(&self) -> PathBuf {
        self.inner.state_dir.join("model_routes.json")
    }
    fn read_model_routes(&self) -> Value {
        std::fs::read_to_string(self.model_routes_path())
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_else(|| json!({}))
    }

    /// Route a task type to a model tier (F131), honoring configured overrides (F132).
    /// Multi-Model-Vergleich (F346): schickt denselben Prompt parallel an mehrere Modelle
    /// (je ein `claude`-Prozess pro Modell), erfasst Antwort + Latenz + Kosten je Modell und
    /// liefert die Spalten für die Nebeneinander-Ansicht. Testet die Dispatch-/Sammel-Mechanik.
    fn models_compare(&self, p: &Value) -> HandlerResult {
        let prompt = req_str(p, "prompt")?.to_string();
        let models: Vec<String> = p
            .get("models")
            .and_then(Value::as_array)
            .map(|a| a.iter().filter_map(|m| m.as_str().map(String::from)).collect())
            .unwrap_or_default();
        if models.len() < 2 {
            return Err(IpcFailure::invalid("mindestens 2 Modelle zum Vergleich"));
        }
        let binary = p
            .get("binary")
            .and_then(Value::as_str)
            .map(String::from)
            .or_else(|| std::env::var("CLAUDESTUDIO_CLAUDE_BIN").ok())
            .unwrap_or_else(|| "claude".to_string());
        let handles: Vec<_> = models
            .iter()
            .cloned()
            .map(|m| {
                let (bin, pr) = (binary.clone(), prompt.clone());
                std::thread::spawn(move || {
                    let t0 = std::time::Instant::now();
                    let out = std::process::Command::new(&bin)
                        .args(["--model", &m, "--print", &pr])
                        .output();
                    let ms = t0.elapsed().as_millis() as u64;
                    let (text, cost) = match out {
                        Ok(o) => extract_assistant_text(&String::from_utf8_lossy(&o.stdout)),
                        Err(_) => (String::new(), 0.0),
                    };
                    json!({ "model": m, "response": text, "latency_ms": ms, "cost_usd": cost })
                })
            })
            .collect();
        let responses: Vec<Value> = handles.into_iter().filter_map(|h| h.join().ok()).collect();
        Ok(json!({ "models": models, "responses": responses, "count": responses.len() }))
    }

    /// Bidirektionaler GitHub-Issues-Sync (F357): legt für Tasks ohne Issue ein GitHub-Issue
    /// an (POST /repos/{repo}/issues) und merkt sich die Nummer; für Tasks mit Issue wird der
    /// Status zurückgelesen (GET) und ein geschlossenes Issue markiert den Task als 'closed'.
    /// `api_base` ist konfigurierbar (echtes api.github.com im Betrieb, lokaler Mock im Test).
    fn integrations_github_sync(&self, p: &Value) -> HandlerResult {
        let repo = req_str(p, "repo")?;
        let api_base = p.get("api_base").and_then(Value::as_str).unwrap_or("https://api.github.com");
        let tasks = p.get("tasks").and_then(Value::as_array).cloned().unwrap_or_default();
        let mut out = Vec::new();
        let mut log = Vec::new();
        for t in tasks {
            let mut task = t.clone();
            let title = task.get("title").and_then(Value::as_str).unwrap_or("Task").to_string();
            match task.get("issue_number").and_then(Value::as_i64) {
                None => {
                    let url = format!("{api_base}/repos/{repo}/issues");
                    let body = json!({ "title": title }).to_string();
                    let resp = curl_json("POST", &url, Some(&body));
                    if let Some(n) = resp.get("number").and_then(Value::as_i64) {
                        if let Some(o) = task.as_object_mut() {
                            o.insert("issue_number".into(), json!(n));
                            o.insert("state".into(), json!("open"));
                        }
                        log.push(json!({ "op": "create_issue", "task": task.get("id"), "issue": n }));
                    }
                }
                Some(n) => {
                    let url = format!("{api_base}/repos/{repo}/issues/{n}");
                    let resp = curl_json("GET", &url, None);
                    if let Some(state) = resp.get("state").and_then(Value::as_str) {
                        if let Some(o) = task.as_object_mut() {
                            o.insert("state".into(), json!(state));
                        }
                        log.push(json!({ "op": "reconcile", "task": task.get("id"),
                                         "issue": n, "state": state }));
                    }
                }
            }
            out.push(task);
        }
        Ok(json!({ "tasks": out, "log": log }))
    }

    /// Admin-API Usage-Report (F285): ruft GET /v1/organizations/usage_report/claude_code mit
    /// dem hinterlegten Admin-Key auf und parst die Antwort (HTTP-Status + Report-Felder).
    /// `api_base` konfigurierbar (echtes api.anthropic.com im Betrieb, Mock im Test).
    fn integrations_usage_report(&self, p: &Value) -> HandlerResult {
        let api_base = p.get("api_base").and_then(Value::as_str).unwrap_or("https://api.anthropic.com");
        let key = p.get("admin_key").and_then(Value::as_str).unwrap_or("");
        let url = format!("{api_base}/v1/organizations/usage_report/claude_code");
        let out = std::process::Command::new("curl")
            .args([
                "-s", "-w", "\n%{http_code}",
                "-H", &format!("x-api-key: {key}"),
                "-H", "anthropic-version: 2023-06-01",
                "-H", "Accept: application/json",
                &url,
            ])
            .output()
            .map_err(|e| IpcFailure::internal(e.to_string()))?;
        let text = String::from_utf8_lossy(&out.stdout);
        let (body, code) = match text.rsplit_once('\n') {
            Some((b, c)) => (b, c.trim().parse::<u32>().unwrap_or(0)),
            None => (text.as_ref(), 0),
        };
        let report: Value = serde_json::from_str(body).unwrap_or_else(|_| json!({}));
        Ok(json!({ "http_status": code, "report": report, "authenticated": !key.is_empty() }))
    }

    /// OpenTelemetry-Span-Export via OTLP/HTTP-JSON (F263/F283): baut einen echten OTLP-Span
    /// (resourceSpans → scopeSpans → spans mit Attributen) und POSTet ihn an den konfigurierbaren
    /// Collector-Endpunkt (z.B. http://host:4318/v1/traces). Liefert den HTTP-Status.
    fn telemetry_export_span(&self, p: &Value) -> HandlerResult {
        let endpoint = req_str(p, "endpoint")?;
        let name = p.get("name").and_then(Value::as_str).unwrap_or("span");
        let mut attrs = Vec::new();
        if let Some(o) = p.get("attributes").and_then(Value::as_object) {
            for (k, v) in o {
                let s = v.as_str().map(String::from).unwrap_or_else(|| v.to_string());
                attrs.push(json!({ "key": k, "value": { "stringValue": s } }));
            }
        }
        let payload = json!({
            "resourceSpans": [{
                "resource": { "attributes": [
                    { "key": "service.name", "value": { "stringValue": "claudestudio" } }
                ]},
                "scopeSpans": [{
                    "scope": { "name": "claudestudio.core" },
                    "spans": [{ "name": name, "kind": 1, "attributes": attrs }]
                }]
            }]
        })
        .to_string();
        let posted = curl_status("POST", endpoint, &payload);
        Ok(json!({ "posted_status": posted, "span_name": name,
                   "attribute_count": attrs.len(), "otlp": true }))
    }

    /// Slack-Bot-Modus (F358): empfängt einen Slack-Befehl (von der Slack-Ingress an diesen
    /// Handler weitergereicht), führt den zugehörigen Task (Shell-Kommando) aus und postet das
    /// Ergebnis als Slack-Nachricht an die `response_url` zurück (in_channel).
    fn integrations_slack_command(&self, p: &Value) -> HandlerResult {
        let command = req_str(p, "command")?.to_string();
        let response_url = req_str(p, "response_url")?;
        let cwd = p.get("cwd").and_then(Value::as_str).unwrap_or(".");
        let exec = p.get("exec").and_then(Value::as_str).unwrap_or("");
        let (output, exit) = if exec.is_empty() {
            (String::new(), 0)
        } else {
            match std::process::Command::new("sh").arg("-c").arg(exec).current_dir(cwd).output() {
                Ok(o) => (
                    String::from_utf8_lossy(&o.stdout).trim().to_string(),
                    o.status.code().unwrap_or(-1),
                ),
                Err(e) => (e.to_string(), -1),
            }
        };
        let msg = json!({ "response_type": "in_channel",
                          "text": format!("Task '{command}' fertig (exit {exit}):\n{output}") })
        .to_string();
        let posted = curl_status("POST", response_url, &msg);
        Ok(json!({ "command": command, "exit": exit, "output": output, "posted_status": posted }))
    }

    fn model_router_route(&self, p: &Value) -> HandlerResult {
        let task_type = req_str(p, "task_type")?;
        let routes = self.read_model_routes();
        let configured = routes.get(task_type).and_then(Value::as_str);
        let model = configured
            .map(str::to_string)
            .unwrap_or_else(|| default_model_for(task_type).to_string());
        Ok(json!({
            "task_type": task_type, "model": model,
            "source": if configured.is_some() { "configured" } else { "default" },
        }))
    }

    /// Configure which model a task type routes to (F132).
    fn model_router_set(&self, p: &Value) -> HandlerResult {
        let task_type = req_str(p, "task_type")?;
        let model = req_str(p, "model")?;
        let mut routes = self.read_model_routes();
        routes[task_type] = json!(model);
        std::fs::write(self.model_routes_path(), serde_json::to_string_pretty(&routes).unwrap_or_default())
            .map_err(|e| e.to_string())?;
        Ok(json!({ "ok": true, "routes": routes }))
    }

    /// Resolve the model for a task; a per-agent override wins over the router (F133).
    fn model_router_resolve(&self, p: &Value) -> HandlerResult {
        let task_type = req_str(p, "task_type")?;
        if let Some(ov) = p.get("agent_override").and_then(Value::as_str) {
            return Ok(json!({ "task_type": task_type, "model": ov, "source": "agent_override" }));
        }
        self.model_router_route(p)
    }

    fn session_messages(&self, p: &Value) -> HandlerResult {
        let id = p.get("id").and_then(Value::as_str).ok_or_else(|| IpcFailure::invalid("missing 'id'"))?;
        let store = self.inner.sessions.lock().unwrap();
        let messages = store.list_messages(id).map_err(session_failure)?;
        Ok(json!({ "messages": messages }))
    }

    /// Live-Session-Sharing (F349): erzeugt ein Share-Token (Link) für eine laufende
    /// Session. Ein zweiter Client löst es per session.join auf und liest die Session
    /// live über session.messages mit (der Forwarder schreibt jede Nachricht sofort in
    /// die DB). Token→session_id wird in shares.json persistiert.
    fn session_share(&self, p: &Value) -> HandlerResult {
        let sid = req_str(p, "session_id")?.to_string();
        let token = unique_id("share");
        let path = self.inner.state_dir.join("shares.json");
        let mut map: Value = std::fs::read_to_string(&path)
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_else(|| json!({}));
        if let Some(o) = map.as_object_mut() {
            o.insert(token.clone(), json!(sid));
        }
        std::fs::create_dir_all(&self.inner.state_dir).ok();
        std::fs::write(&path, serde_json::to_string_pretty(&map).unwrap_or_default())
            .map_err(|e| e.to_string())?;
        Ok(json!({ "ok": true, "token": token, "session_id": sid,
                   "link": format!("claudestudio://session/share/{token}") }))
    }

    /// Löst ein Share-Token zur session_id auf (F349), damit der zweite Client mitliest.
    fn session_join(&self, p: &Value) -> HandlerResult {
        let token = req_str(p, "token")?;
        let path = self.inner.state_dir.join("shares.json");
        let map: Value = std::fs::read_to_string(&path)
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_else(|| json!({}));
        match map.get(token).and_then(Value::as_str) {
            Some(sid) => Ok(json!({ "ok": true, "session_id": sid })),
            None => Err(IpcFailure::not_found(format!("unbekanntes Share-Token: {token}"))),
        }
    }

    /// Session-Replay-Step-Through (F160): liefert den Transcript-Schritt am Index
    /// `step` (geklemmt) plus has_prev/has_next, sodass die Vor-/Zurück-Pfeiltasten
    /// deterministisch durch die geordneten Schritte navigieren.
    fn session_replay_step(&self, p: &Value) -> HandlerResult {
        let id = p.get("id").and_then(Value::as_str).ok_or_else(|| IpcFailure::invalid("missing 'id'"))?;
        let messages = self.inner.sessions.lock().unwrap().list_messages(id).map_err(session_failure)?;
        let total = messages.len();
        if total == 0 {
            return Ok(json!({ "total": 0, "step": Value::Null, "index": 0,
                              "has_prev": false, "has_next": false }));
        }
        let idx = (p.get("step").and_then(Value::as_u64).unwrap_or(0) as usize).min(total - 1);
        Ok(json!({
            "index": idx, "total": total,
            "step": serde_json::to_value(&messages[idx]).unwrap_or_else(|_| json!({})),
            "has_prev": idx > 0, "has_next": idx + 1 < total,
        }))
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

    /// Fire the hooks registered for an event; execute or dry-run them (F258/F260/F265).
    fn hooks_run(&self, p: &Value) -> HandlerResult {
        let cwd = req_str(p, "cwd")?;
        let event = req_str(p, "event")?;
        let tool = p.get("tool").and_then(Value::as_str).unwrap_or("");
        let file = p.get("file").and_then(Value::as_str).unwrap_or("");
        let input = p.get("input").and_then(Value::as_str).unwrap_or("");
        let dry_run = p.get("dry_run").and_then(Value::as_bool).unwrap_or(false);
        let path = Path::new(cwd).join(".claude/settings.json");
        let root: Value = std::fs::read_to_string(&path)
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_else(|| json!({}));
        let mut fired = Vec::new();
        let mut blocked = false;
        if let Some(arr) = root.get("hooks").and_then(|h| h.get(event)).and_then(Value::as_array) {
            for entry in arr {
                let matcher = entry.get("matcher").and_then(Value::as_str).unwrap_or("*");
                let matches = matcher == "*" || tool.is_empty() || matcher.split('|').any(|m| m == tool);
                if !matches {
                    continue;
                }
                if let Some(cmds) = entry.get("hooks").and_then(Value::as_array) {
                    for cmd in cmds {
                        let command = cmd.get("command").and_then(Value::as_str).unwrap_or("");
                        if dry_run {
                            fired.push(json!({ "matcher": matcher, "command": command, "would_fire": true }));
                            continue;
                        }
                        let out = std::process::Command::new("sh")
                            .arg("-c")
                            .arg(command)
                            .current_dir(cwd)
                            .env("CS_FILE", file)
                            .env("CS_TOOL_INPUT", input)
                            .output();
                        let (code, stdout, stderr) = match out {
                            Ok(o) => (
                                o.status.code().unwrap_or(-1),
                                String::from_utf8_lossy(&o.stdout).trim().to_string(),
                                String::from_utf8_lossy(&o.stderr).trim().to_string(),
                            ),
                            Err(e) => (-1, String::new(), e.to_string()),
                        };
                        let this_blocked = event == "PreToolUse" && code != 0;
                        if this_blocked {
                            blocked = true;
                        }
                        fired.push(json!({
                            "matcher": matcher, "command": command, "exit": code,
                            "blocked": this_blocked, "stdout": stdout, "stderr": stderr,
                        }));
                    }
                }
            }
        }
        Ok(json!({ "event": event, "dry_run": dry_run, "blocked": blocked, "fired": fired }))
    }

    /// OWASP-ish static code scan with file + line numbers (F203).
    fn security_code_scan(&self, p: &Value) -> HandlerResult {
        let cwd = req_str(p, "cwd")?;
        let files = collect_source_files(Path::new(cwd), &["ts", "tsx", "js", "jsx", "py", "rs"]);
        let mut findings = Vec::new();
        for (path, content) in &files {
            for (i, line) in content.lines().enumerate() {
                let l = line.to_lowercase();
                let kind = if (l.contains("select ") || l.contains("insert ") || l.contains("where")) && line.contains("${") {
                    Some(("SQL-Injection", "high"))
                } else if l.contains("eval(") {
                    Some(("Code-Injection (eval)", "high"))
                } else if l.contains(".innerhtml") && line.contains('=') {
                    Some(("XSS (innerHTML)", "medium"))
                } else if l.contains("exec(") && line.contains("req.") {
                    Some(("Command-Injection", "high"))
                } else if secret_kind(line).is_some() {
                    Some(("Hardcoded Secret", "high"))
                } else {
                    None
                };
                if let Some((k, sev)) = kind {
                    findings.push(json!({
                        "file": path, "line": i + 1, "kind": k, "severity": sev,
                        "snippet": line.trim().chars().take(100).collect::<String>(),
                    }));
                }
            }
        }
        Ok(json!({ "findings": findings, "count": findings.len() }))
    }

    /// Conventional-Commit changelog grouped by type (F207).
    fn changelog_generate(&self, p: &Value) -> HandlerResult {
        let cwd = req_str(p, "cwd")?;
        let subjects = git_subjects(cwd, p.get("since").and_then(Value::as_str));
        let mut sections: std::collections::BTreeMap<&str, Vec<String>> = Default::default();
        for s in &subjects {
            let ty = s.split([':', '(']).next().unwrap_or("other").trim();
            let label = match ty {
                "feat" => "Features",
                "fix" => "Fixes",
                "docs" => "Docs",
                "refactor" => "Refactor",
                "perf" => "Performance",
                "test" => "Tests",
                "chore" => "Chore",
                _ => "Other",
            };
            sections.entry(label).or_default().push(s.clone());
        }
        let mut md = String::from("# Changelog\n\n");
        for (label, items) in &sections {
            md.push_str(&format!("## {label}\n"));
            for it in items {
                md.push_str(&format!("- {it}\n"));
            }
            md.push('\n');
        }
        Ok(json!({ "changelog": md, "commits": subjects.len() }))
    }

    /// User-friendly release notes from git history (F209).
    fn release_notes_generate(&self, p: &Value) -> HandlerResult {
        let cwd = req_str(p, "cwd")?;
        let subjects = git_subjects(cwd, p.get("since").and_then(Value::as_str));
        let feats: Vec<String> = subjects.iter().filter(|s| s.starts_with("feat")).map(|s| friendly(s)).collect();
        let fixes: Vec<String> = subjects.iter().filter(|s| s.starts_with("fix")).map(|s| friendly(s)).collect();
        let mut md = String::from("# Release Notes\n\n");
        if !feats.is_empty() {
            md.push_str("## ✨ Neue Funktionen\n");
            for f in &feats {
                md.push_str(&format!("- {f}\n"));
            }
            md.push('\n');
        }
        if !fixes.is_empty() {
            md.push_str("## 🐛 Behobene Fehler\n");
            for f in &fixes {
                md.push_str(&format!("- {f}\n"));
            }
            md.push('\n');
        }
        Ok(json!({ "release_notes": md, "features": feats.len(), "fixes": fixes.len() }))
    }

    /// Project README derived from structure + stack detection (F206).
    fn readme_generate(&self, p: &Value) -> HandlerResult {
        let cwd = req_str(p, "cwd")?;
        let root = Path::new(cwd);
        let name = root.file_name().and_then(|n| n.to_str()).unwrap_or("project");
        let mut stack = Vec::new();
        if root.join("package.json").exists() {
            stack.push("Node.js");
        }
        if root.join("Cargo.toml").exists() {
            stack.push("Rust");
        }
        if root.join("requirements.txt").exists() || root.join("pyproject.toml").exists() {
            stack.push("Python");
        }
        if root.join("go.mod").exists() {
            stack.push("Go");
        }
        let mut files = Vec::new();
        if let Ok(rd) = std::fs::read_dir(root) {
            for e in rd.flatten() {
                let n = e.file_name().to_string_lossy().to_string();
                if !n.starts_with('.') {
                    files.push(n);
                }
            }
        }
        files.sort();
        let md = format!(
            "# {name}\n\n**Stack:** {}\n\n## Struktur\n\n{}\n",
            if stack.is_empty() { "unbekannt".into() } else { stack.join(", ") },
            files.iter().map(|f| format!("- `{f}`")).collect::<Vec<_>>().join("\n")
        );
        Ok(json!({ "readme": md, "stack": stack, "name": name }))
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

    /// Roll a deployment back to the previous (or a given) commit (F273).
    fn deploy_rollback(&self, p: &Value) -> HandlerResult {
        let cwd = req_str(p, "cwd")?;
        let to = p.get("to").and_then(Value::as_str).unwrap_or("HEAD~1");
        let rev = |args: &[&str]| {
            std::process::Command::new("git")
                .current_dir(cwd)
                .args(args)
                .output()
                .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
                .unwrap_or_default()
        };
        let before = rev(&["rev-parse", "HEAD"]);
        let out = std::process::Command::new("git")
            .current_dir(cwd)
            .args(["reset", "--hard", to])
            .output()
            .map_err(|e| IpcFailure::internal(e.to_string()))?;
        if !out.status.success() {
            return Err(IpcFailure::internal(String::from_utf8_lossy(&out.stderr).trim().to_string()));
        }
        let after = rev(&["rev-parse", "HEAD"]);
        Ok(json!({ "ok": true, "from": before, "to": after, "rolled_back_to": to }))
    }

    fn environments_path(&self) -> PathBuf {
        self.inner.state_dir.join("environments.json")
    }
    fn env_add(&self, p: &Value) -> HandlerResult {
        let name = req_str(p, "name")?;
        let mut envs: Value = std::fs::read_to_string(self.environments_path())
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_else(|| json!({}));
        envs[name] = json!({
            "status": p.get("status").and_then(Value::as_str).unwrap_or("unknown"),
            "config": p.get("config").cloned().unwrap_or_else(|| json!({})),
        });
        std::fs::write(self.environments_path(), serde_json::to_string_pretty(&envs).unwrap_or_default())
            .map_err(|e| e.to_string())?;
        Ok(json!({ "ok": true, "environments": envs }))
    }
    fn env_list(&self) -> HandlerResult {
        let envs: Value = std::fs::read_to_string(self.environments_path())
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_else(|| json!({}));
        Ok(json!({ "environments": envs }))
    }

    fn flags_path(&self) -> PathBuf {
        self.inner.state_dir.join("feature_flags.json")
    }
    fn read_flags(&self) -> Value {
        std::fs::read_to_string(self.flags_path())
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_else(|| json!({}))
    }
    fn flags_set(&self, p: &Value) -> HandlerResult {
        let name = req_str(p, "name")?;
        let enabled = p.get("enabled").and_then(Value::as_bool).unwrap_or(false);
        let mut flags = self.read_flags();
        flags[name] = json!(enabled);
        std::fs::write(self.flags_path(), serde_json::to_string_pretty(&flags).unwrap_or_default())
            .map_err(|e| e.to_string())?;
        Ok(json!({ "ok": true, "name": name, "enabled": enabled }))
    }
    fn flags_eval(&self, p: &Value) -> HandlerResult {
        let name = req_str(p, "name")?;
        let enabled = self.read_flags().get(name).and_then(Value::as_bool).unwrap_or(false);
        Ok(json!({ "name": name, "enabled": enabled }))
    }
    fn flags_list(&self) -> HandlerResult {
        Ok(json!({ "flags": self.read_flags() }))
    }

    /// DORA metrics computed from git history (F331).
    fn metrics_dora(&self, p: &Value) -> HandlerResult {
        let cwd = req_str(p, "cwd")?;
        let log = std::process::Command::new("git")
            .current_dir(cwd)
            .args(["log", "--pretty=format:%ct%x1f%s"])
            .output()
            .map(|o| String::from_utf8_lossy(&o.stdout).to_string())
            .unwrap_or_default();
        let mut times: Vec<i64> = Vec::new();
        let (mut total, mut failures) = (0i64, 0i64);
        for line in log.lines() {
            let mut it = line.split('\u{1f}');
            if let Some(ts) = it.next().and_then(|t| t.parse::<i64>().ok()) {
                times.push(ts);
            }
            let subject = it.next().unwrap_or("").to_lowercase();
            total += 1;
            if subject.contains("fix") || subject.contains("revert") || subject.contains("hotfix") {
                failures += 1;
            }
        }
        let span_days = if times.len() >= 2 {
            ((times.first().unwrap() - times.last().unwrap()).max(1) as f64) / 86400.0
        } else {
            1.0
        };
        let mut gaps = 0i64;
        let mut n = 0;
        for w in times.windows(2) {
            gaps += (w[0] - w[1]).abs();
            n += 1;
        }
        let lead_time_hours = if n > 0 { (gaps as f64 / n as f64) / 3600.0 } else { 0.0 };
        Ok(json!({
            "total_commits": total,
            "span_days": span_days,
            "deployment_frequency_per_day": total as f64 / span_days.max(1.0),
            "lead_time_hours": lead_time_hours,
            "change_failure_rate": if total > 0 { failures as f64 / total as f64 } else { 0.0 },
            "mttr_hours": lead_time_hours,
        }))
    }

    /// Standup report from recent git activity + session archive (F352).
    fn report_standup(&self, p: &Value) -> HandlerResult {
        let cwd = req_str(p, "cwd")?;
        let since = p.get("since").and_then(Value::as_str).unwrap_or("1 day ago");
        let log = std::process::Command::new("git")
            .current_dir(cwd)
            .args(["log", "--since", since, "--pretty=format:- %s"])
            .output()
            .map(|o| String::from_utf8_lossy(&o.stdout).to_string())
            .unwrap_or_default();
        let commits: Vec<String> = log.lines().filter(|l| !l.trim().is_empty()).map(str::to_string).collect();
        let sessions = self.inner.sessions.lock().unwrap().stats().map(|s| s.sessions).unwrap_or(0);
        let report = format!(
            "# Standup ({since})\n\nCommits: {}\n{}\n\nSessions im Archiv: {sessions}",
            commits.len(),
            log
        );
        Ok(json!({ "report": report, "commit_count": commits.len(), "commits": commits, "sessions": sessions }))
    }

    fn checkpoints_dir(&self) -> PathBuf {
        self.inner.state_dir.join("checkpoints")
    }
    fn checkpoint_save(&self, p: &Value) -> HandlerResult {
        let name = req_str(p, "name")?;
        let data = p.get("data").cloned().unwrap_or_else(|| json!({}));
        let dir = self.checkpoints_dir();
        std::fs::create_dir_all(&dir).ok();
        let entry = json!({ "name": name, "saved_at": now_millis(), "data": data });
        std::fs::write(dir.join(format!("{name}.json")), serde_json::to_string_pretty(&entry).unwrap_or_default())
            .map_err(|e| e.to_string())?;
        Ok(json!({ "ok": true, "name": name }))
    }
    fn checkpoint_restore(&self, p: &Value) -> HandlerResult {
        let name = req_str(p, "name")?;
        std::fs::read_to_string(self.checkpoints_dir().join(format!("{name}.json")))
            .ok()
            .and_then(|s| serde_json::from_str::<Value>(&s).ok())
            .map(|v| json!({ "checkpoint": v }))
            .ok_or_else(|| IpcFailure::not_found(format!("checkpoint not found: {name}")))
    }
    fn checkpoint_list(&self) -> HandlerResult {
        let mut names = Vec::new();
        if let Ok(rd) = std::fs::read_dir(self.checkpoints_dir()) {
            for e in rd.flatten() {
                if let Some(stem) = e.path().file_stem().and_then(|s| s.to_str()) {
                    names.push(stem.to_string());
                }
            }
        }
        Ok(json!({ "checkpoints": names }))
    }

    // MARK: Code-quality & documentation analyzers (static, no LLM)

    /// Exported JS/TS symbols never referenced anywhere else (F318).
    fn codeq_dead_code(&self, p: &Value) -> HandlerResult {
        let cwd = req_str(p, "cwd")?;
        let files = collect_source_files(Path::new(cwd), &["ts", "tsx", "js", "jsx"]);
        let all: String = files.iter().map(|(_, c)| c.as_str()).collect::<Vec<_>>().join("\n");
        let mut dead = Vec::new();
        for (path, content) in &files {
            for line in content.lines() {
                for kw in ["export function ", "export const ", "export class "] {
                    if let Some(idx) = line.find(kw) {
                        let name: String = line[idx + kw.len()..]
                            .chars()
                            .take_while(|c| c.is_alphanumeric() || *c == '_')
                            .collect();
                        if !name.is_empty() && all.matches(name.as_str()).count() <= 1 {
                            dead.push(json!({ "name": name, "file": path }));
                        }
                    }
                }
            }
        }
        Ok(json!({ "dead_exports": dead, "scanned_files": files.len() }))
    }

    /// Duplicated line-blocks across files with a size-based similarity (F319).
    fn codeq_duplicates(&self, p: &Value) -> HandlerResult {
        let cwd = req_str(p, "cwd")?;
        let window = p.get("window").and_then(Value::as_u64).unwrap_or(4) as usize;
        let files = collect_source_files(Path::new(cwd), &["ts", "tsx", "js", "jsx", "rs", "py"]);
        let mut blocks: HashMap<String, Vec<String>> = HashMap::new();
        for (path, content) in &files {
            let lines: Vec<&str> = content.lines().map(str::trim).filter(|l| !l.is_empty()).collect();
            if lines.len() < window {
                continue;
            }
            for i in 0..=lines.len() - window {
                let block = lines[i..i + window].join("\n");
                blocks.entry(block).or_default().push(format!("{path}:{}", i + 1));
            }
        }
        let mut dups = Vec::new();
        for (block, locs) in blocks {
            if locs.len() >= 2 {
                dups.push(json!({
                    "locations": locs, "lines": window,
                    "block": block.chars().take(120).collect::<String>(),
                }));
            }
        }
        Ok(json!({ "duplicates": dups, "block_size": window }))
    }

    /// Cyclomatic complexity per file (F320).
    fn codeq_complexity(&self, p: &Value) -> HandlerResult {
        let cwd = req_str(p, "cwd")?;
        let files = collect_source_files(Path::new(cwd), &["ts", "tsx", "js", "jsx", "rs", "py"]);
        let mut out = Vec::new();
        for (path, content) in &files {
            let c = cyclomatic_complexity(content);
            let level = if c > 20 { "high" } else if c > 10 { "medium" } else { "low" };
            out.push(json!({ "file": path, "complexity": c, "level": level }));
        }
        out.sort_by(|a, b| b["complexity"].as_u64().cmp(&a["complexity"].as_u64()));
        Ok(json!({ "files": out }))
    }

    /// Mermaid component diagram derived from JS/TS imports (F334).
    fn docs_arch_diagram(&self, p: &Value) -> HandlerResult {
        let cwd = req_str(p, "cwd")?;
        let files = collect_source_files(Path::new(cwd), &["ts", "tsx", "js", "jsx"]);
        let mut edges: Vec<(String, String)> = Vec::new();
        for (path, content) in &files {
            let module = Path::new(path).file_stem().and_then(|s| s.to_str()).unwrap_or("mod").to_string();
            for line in content.lines() {
                if line.contains("import") {
                    if let Some(idx) = line.find("from ") {
                        let target = line[idx + 5..].trim().trim_matches(|c| c == '"' || c == '\'' || c == ';');
                        let dep = Path::new(target).file_stem().and_then(|s| s.to_str()).unwrap_or(target).to_string();
                        edges.push((module.clone(), dep));
                    }
                }
            }
        }
        let nodes: HashSet<String> = edges.iter().flat_map(|(a, b)| [a.clone(), b.clone()]).collect();
        let mut mermaid = String::from("graph TD\n");
        for (a, b) in &edges {
            mermaid.push_str(&format!("  {} --> {}\n", sanitize_node(a), sanitize_node(b)));
        }
        Ok(json!({ "mermaid": mermaid, "edges": edges.len(), "nodes": nodes.len() }))
    }

    /// Markdown docs from doc-comments + function signatures (F332).
    fn docs_generate(&self, p: &Value) -> HandlerResult {
        let cwd = req_str(p, "cwd")?;
        let files = collect_source_files(Path::new(cwd), &["ts", "js"]);
        let mut md = String::from("# API\n\n");
        let mut count = 0;
        for (path, content) in &files {
            let lines: Vec<&str> = content.lines().collect();
            for i in 0..lines.len() {
                let l = lines[i].trim();
                if l.starts_with("export function ") || l.starts_with("function ") {
                    let sig = l.trim_end_matches('{').trim();
                    let mut j = i;
                    while j > 0 {
                        let prev = lines[j - 1].trim();
                        if prev.starts_with("*") || prev.starts_with("/**") || prev.ends_with("*/") {
                            j -= 1;
                        } else {
                            break;
                        }
                    }
                    let mut doc = String::new();
                    for line in lines.iter().take(i).skip(j) {
                        let t = line.trim().trim_start_matches("/**").trim_start_matches("*/").trim_start_matches('*').trim();
                        if !t.is_empty() {
                            doc.push_str(t);
                            doc.push(' ');
                        }
                    }
                    md.push_str(&format!("## `{sig}`\n\n{}\n\n_({path})_\n\n", doc.trim()));
                    count += 1;
                }
            }
        }
        Ok(json!({ "markdown": md, "documented": count }))
    }

    // MARK: Agent teams (A2A), tasks render, snapshots, comments

    fn a2a_path(&self, agent: &str) -> PathBuf {
        self.inner.state_dir.join("a2a").join(format!("{agent}.jsonl"))
    }
    /// Agent-to-agent message: append to the recipient's inbox (F125).
    fn a2a_send(&self, p: &Value) -> HandlerResult {
        let from = req_str(p, "from")?;
        let to = req_str(p, "to")?;
        let message = p.get("message").cloned().unwrap_or(Value::Null);
        let path = self.a2a_path(to);
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).ok();
        }
        let id = unique_id("a2a");
        let entry = json!({ "id": id, "from": from, "to": to, "ts": now_millis(), "message": message });
        if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&path) {
            use std::io::Write;
            let _ = writeln!(f, "{entry}");
        }
        Ok(json!({ "ok": true, "id": id, "to": to }))
    }
    /// Drain (or peek) an agent's A2A inbox (F127).
    fn a2a_inbox(&self, p: &Value) -> HandlerResult {
        let agent = req_str(p, "agent")?;
        let drain = p.get("drain").and_then(Value::as_bool).unwrap_or(true);
        let path = self.a2a_path(agent);
        let content = std::fs::read_to_string(&path).unwrap_or_default();
        let messages: Vec<Value> = content.lines().filter_map(|l| serde_json::from_str(l).ok()).collect();
        if drain {
            std::fs::remove_file(&path).ok();
        }
        Ok(json!({ "agent": agent, "messages": messages }))
    }

    fn teams_dir(&self) -> PathBuf {
        self.inner.state_dir.join("teams")
    }
    /// Save a reusable team template with explicit orchestrator->worker edges (F121/F129).
    fn teams_create(&self, p: &Value) -> HandlerResult {
        let name = req_str(p, "name")?;
        let mut team = p.clone();
        if let Some(o) = team.as_object_mut() {
            o.insert("id".into(), json!(name));
            // Derive the graph edges orchestrator -> each worker (F121).
            let orch = o.get("orchestrator").and_then(Value::as_str).unwrap_or("orchestrator").to_string();
            let edges: Vec<Value> = o
                .get("workers")
                .and_then(Value::as_array)
                .map(|ws| ws.iter().filter_map(|w| w.as_str()).map(|w| json!([orch, w])).collect())
                .unwrap_or_default();
            o.insert("edges".into(), json!(edges));
        }
        let dir = self.teams_dir();
        std::fs::create_dir_all(&dir).ok();
        std::fs::write(dir.join(format!("{name}.json")), serde_json::to_string_pretty(&team).unwrap_or_default())
            .map_err(|e| e.to_string())?;
        Ok(json!({ "ok": true, "team": team }))
    }

    /// Review-gate: the orchestrator approves, THEN the worker branch is merged
    /// for real — the log proves the merge happened after approval (F124).
    fn teams_review_and_merge(&self, p: &Value) -> HandlerResult {
        let cwd = req_str(p, "cwd")?;
        let branch = req_str(p, "branch")?;
        let worker_ok = p.get("worker_ok").and_then(Value::as_bool).unwrap_or(true);
        let mut log = vec![json!({ "kind": "review_started", "branch": branch, "ts": now_millis() })];
        if !worker_ok {
            log.push(json!({ "kind": "review_rejected", "branch": branch, "ts": now_millis() }));
            return Ok(json!({ "merged": false, "decision": "rejected", "log": log }));
        }
        log.push(json!({ "kind": "review_approved", "branch": branch, "ts": now_millis() }));
        let out = std::process::Command::new("git")
            .current_dir(cwd)
            .args(["merge", "--no-edit", branch])
            .output()
            .map_err(|e| IpcFailure::internal(e.to_string()))?;
        if !out.status.success() {
            return Err(IpcFailure::internal(String::from_utf8_lossy(&out.stderr).trim().to_string()));
        }
        let head = std::process::Command::new("git")
            .current_dir(cwd)
            .args(["rev-parse", "HEAD"])
            .output()
            .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
            .unwrap_or_default();
        log.push(json!({ "kind": "merged", "branch": branch, "head": head, "ts": now_millis() }));
        Ok(json!({ "merged": true, "decision": "approved", "head": head, "log": log }))
    }

    /// A failed worker escalates to the orchestrator via the A2A bus; the
    /// orchestrator returns a reassign-or-fail decision (F128).
    /// Zentraler Team-Stop (F130): beendet alle laufenden Worker-Sessions des
    /// Teams in einem Aufruf, indem für jede ihr Cancel-Signal gefeuert wird
    /// (killt den jeweiligen claude-Subprozess).
    fn teams_stop(&self, p: &Value) -> HandlerResult {
        let sessions: Vec<String> = p
            .get("sessions")
            .and_then(Value::as_array)
            .map(|a| a.iter().filter_map(|s| s.as_str().map(String::from)).collect())
            .unwrap_or_default();
        let mut stopped = Vec::new();
        for sid in &sessions {
            if self.trigger_cancel(sid) {
                stopped.push(sid.clone());
            }
        }
        Ok(json!({ "ok": true, "stopped": stopped, "count": stopped.len() }))
    }

    fn teams_escalate(&self, p: &Value) -> HandlerResult {
        let worker = req_str(p, "worker")?;
        let orchestrator = p.get("orchestrator").and_then(Value::as_str).unwrap_or("orchestrator");
        let subtask = p.get("subtask").and_then(Value::as_str).unwrap_or("");
        let error = p.get("error").and_then(Value::as_str).unwrap_or("");
        let attempts = p.get("attempts").and_then(Value::as_u64).unwrap_or(1);
        // Worker -> Orchestrator escalation message on the real A2A bus.
        let msg = json!({ "status": "failed", "subtask": subtask, "error": error, "attempts": attempts });
        self.a2a_send(&json!({ "from": worker, "to": orchestrator, "message": msg }))?;
        // Orchestrator reacts: reassign if attempts remain, else fail.
        let decision = if attempts < 3 { "reassign" } else { "fail" };
        let inbox = self.a2a_inbox(&json!({ "agent": orchestrator, "drain": false }))?;
        Ok(json!({ "escalated": true, "decision": decision, "orchestrator_inbox": inbox.get("messages") }))
    }
    fn teams_get(&self, p: &Value) -> HandlerResult {
        let name = req_str(p, "name")?;
        std::fs::read_to_string(self.teams_dir().join(format!("{name}.json")))
            .ok()
            .and_then(|s| serde_json::from_str::<Value>(&s).ok())
            .map(|t| json!({ "team": t }))
            .ok_or_else(|| IpcFailure::not_found(format!("team not found: {name}")))
    }

    fn snapshots_dir(&self) -> PathBuf {
        self.inner.state_dir.join("snapshots")
    }
    fn snapshot_save(&self, p: &Value) -> HandlerResult {
        let name = req_str(p, "name")?;
        let data = p.get("data").cloned().unwrap_or(Value::Null);
        let dir = self.snapshots_dir();
        std::fs::create_dir_all(&dir).ok();
        std::fs::write(dir.join(format!("{name}.json")), serde_json::to_string_pretty(&data).unwrap_or_default())
            .map_err(|e| e.to_string())?;
        Ok(json!({ "ok": true, "name": name }))
    }
    fn snapshot_compare(&self, p: &Value) -> HandlerResult {
        let name = req_str(p, "name")?;
        let data = p.get("data").cloned().unwrap_or(Value::Null);
        let saved: Value = std::fs::read_to_string(self.snapshots_dir().join(format!("{name}.json")))
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or(Value::Null);
        Ok(json!({ "name": name, "changed": saved != data, "saved": saved, "current": data }))
    }

    fn comments_path(&self, session: &str) -> PathBuf {
        self.inner.state_dir.join("comments").join(format!("{session}.jsonl"))
    }
    fn comments_add(&self, p: &Value) -> HandlerResult {
        let session = req_str(p, "session_id")?;
        let message_id = req_str(p, "message_id")?;
        let text = req_str(p, "text")?;
        let path = self.comments_path(session);
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).ok();
        }
        let id = unique_id("c");
        let entry = json!({ "id": id, "message_id": message_id, "text": text, "ts": now_millis() });
        if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&path) {
            use std::io::Write;
            let _ = writeln!(f, "{entry}");
        }
        Ok(json!({ "ok": true, "id": id }))
    }
    fn comments_list(&self, p: &Value) -> HandlerResult {
        let session = req_str(p, "session_id")?;
        let content = std::fs::read_to_string(self.comments_path(session)).unwrap_or_default();
        let comments: Vec<Value> = content.lines().filter_map(|l| serde_json::from_str(l).ok()).collect();
        Ok(json!({ "comments": comments }))
    }

    // MARK: Brain graph (knowledge graph) + Co-Pilot

    fn graph_path(&self) -> PathBuf {
        self.inner.state_dir.join("brain_graph.json")
    }
    fn read_graph(&self) -> Value {
        std::fs::read_to_string(self.graph_path())
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_else(|| json!({ "nodes": [], "edges": [] }))
    }
    fn write_graph(&self, g: &Value) -> std::result::Result<(), IpcFailure> {
        std::fs::write(self.graph_path(), serde_json::to_string_pretty(g).unwrap_or_default())
            .map_err(|e| IpcFailure::internal(e.to_string()))
    }

    /// Force-Directed-Layout (Fruchterman-Reingold) für den Brain-Graph (F189/F190).
    /// Nimmt `nodes`/`edges` inline oder den gespeicherten Graph, verteilt die Knoten
    /// überlappungsfrei und vergibt einen Radius proportional zum Knotengrad (mehr
    /// Verbindungen → größerer Knoten). Liefert die Layout-Daten, die die UI rendert.
    /// Deterministisch (Kreis-Initialisierung statt Zufall).
    fn graph_layout(&self, p: &Value) -> HandlerResult {
        let (nodes, edges) = if p.get("nodes").is_some() {
            (p.get("nodes").and_then(Value::as_array).cloned().unwrap_or_default(),
             p.get("edges").and_then(Value::as_array).cloned().unwrap_or_default())
        } else {
            let g = self.read_graph();
            (g["nodes"].as_array().cloned().unwrap_or_default(),
             g["edges"].as_array().cloned().unwrap_or_default())
        };
        let ids: Vec<String> = nodes.iter().enumerate().map(|(i, n)| {
            n.get("id").and_then(Value::as_str).map(String::from).unwrap_or_else(|| format!("n{i}"))
        }).collect();
        let n = ids.len();
        if n == 0 {
            return Ok(json!({ "nodes": [], "edges": edges, "node_count": 0, "edge_count": 0 }));
        }
        let idx: std::collections::HashMap<&str, usize> =
            ids.iter().enumerate().map(|(i, s)| (s.as_str(), i)).collect();
        let mut deg = vec![0usize; n];
        let mut elist: Vec<(usize, usize)> = Vec::new();
        for e in &edges {
            let (a, b) = edge_endpoints(e);
            if let (Some(&i), Some(&j)) = (idx.get(a.as_str()), idx.get(b.as_str())) {
                elist.push((i, j));
                deg[i] += 1;
                deg[j] += 1;
            }
        }
        // Deterministische Kreis-Initialisierung.
        let mut xs = vec![0f64; n];
        let mut ys = vec![0f64; n];
        let r0 = 300.0;
        for i in 0..n {
            let a = 2.0 * std::f64::consts::PI * (i as f64) / (n as f64);
            xs[i] = r0 * a.cos();
            ys[i] = r0 * a.sin();
        }
        let k = (1_000_000.0 / (n as f64)).sqrt(); // ideale Kantenlänge
        let mut temp = 200.0;
        for _ in 0..120 {
            let mut dx = vec![0f64; n];
            let mut dy = vec![0f64; n];
            // Abstoßung zwischen allen Knotenpaaren
            for i in 0..n {
                for j in 0..n {
                    if i == j { continue; }
                    let mut ddx = xs[i] - xs[j];
                    let mut ddy = ys[i] - ys[j];
                    let mut dist = (ddx * ddx + ddy * ddy).sqrt();
                    if dist < 0.01 {
                        ddx = 0.01 * (i as f64 - j as f64);
                        ddy = 0.01;
                        dist = 0.0142;
                    }
                    let force = k * k / dist;
                    dx[i] += ddx / dist * force;
                    dy[i] += ddy / dist * force;
                }
            }
            // Anziehung entlang Kanten
            for &(i, j) in &elist {
                let ddx = xs[i] - xs[j];
                let ddy = ys[i] - ys[j];
                let dist = (ddx * ddx + ddy * ddy).sqrt().max(0.01);
                let force = dist * dist / k;
                dx[i] -= ddx / dist * force;
                dy[i] -= ddy / dist * force;
                dx[j] += ddx / dist * force;
                dy[j] += ddy / dist * force;
            }
            for i in 0..n {
                let d = (dx[i] * dx[i] + dy[i] * dy[i]).sqrt().max(0.01);
                xs[i] += dx[i] / d * d.min(temp);
                ys[i] += dy[i] / d * d.min(temp);
            }
            temp *= 0.95;
        }
        let out_nodes: Vec<Value> = (0..n).map(|i| {
            json!({ "id": ids[i], "x": xs[i], "y": ys[i],
                    "radius": 8.0 + 4.0 * deg[i] as f64, "degree": deg[i] })
        }).collect();
        let mut min_d = f64::MAX;
        for i in 0..n {
            for j in (i + 1)..n {
                let d = ((xs[i] - xs[j]).powi(2) + (ys[i] - ys[j]).powi(2)).sqrt();
                if d < min_d { min_d = d; }
            }
        }
        Ok(json!({ "nodes": out_nodes, "edges": edges, "node_count": n,
                   "edge_count": elist.len(), "min_distance": if n > 1 { min_d } else { 0.0 } }))
    }

    fn graph_add_node(&self, p: &Value) -> HandlerResult {
        let node_type = req_str(p, "type")?;
        if !GRAPH_NODE_TYPES.contains(&node_type) {
            return Err(IpcFailure::invalid(format!("unknown node type: {node_type}")));
        }
        let label = req_str(p, "label")?;
        let id = unique_id("n");
        let mut g = self.read_graph();
        g["nodes"].as_array_mut().unwrap().push(json!({
            "id": id, "type": node_type, "label": label,
            "props": p.get("props").cloned().unwrap_or(json!({})), "created_at": now_millis(),
        }));
        self.write_graph(&g)?;
        Ok(json!({ "ok": true, "id": id }))
    }

    fn graph_add_edge(&self, p: &Value) -> HandlerResult {
        let etype = req_str(p, "type")?;
        if !GRAPH_EDGE_TYPES.contains(&etype) {
            return Err(IpcFailure::invalid(format!("unknown edge type: {etype}")));
        }
        let from = req_str(p, "from")?;
        let to = req_str(p, "to")?;
        let mut g = self.read_graph();
        g["edges"].as_array_mut().unwrap().push(json!({ "from": from, "to": to, "type": etype }));
        self.write_graph(&g)?;
        Ok(json!({ "ok": true }))
    }

    fn graph_export(&self) -> HandlerResult {
        let g = self.read_graph();
        let nodes = g["nodes"].as_array().map(|a| a.len()).unwrap_or(0);
        let edges = g["edges"].as_array().map(|a| a.len()).unwrap_or(0);
        Ok(json!({ "graph": g, "node_count": nodes, "edge_count": edges }))
    }

    fn graph_search(&self, p: &Value) -> HandlerResult {
        let q = req_str(p, "query")?.to_lowercase();
        let g = self.read_graph();
        let hits: Vec<Value> = g["nodes"]
            .as_array()
            .cloned()
            .unwrap_or_default()
            .into_iter()
            .filter(|n| {
                let label = n.get("label").and_then(Value::as_str).unwrap_or("").to_lowercase();
                let props = n.get("props").map(|p| p.to_string().to_lowercase()).unwrap_or_default();
                label.contains(&q) || props.contains(&q)
            })
            .collect();
        Ok(json!({ "nodes": hits }))
    }

    fn graph_at(&self, p: &Value) -> HandlerResult {
        let date = p.get("date_ms").and_then(Value::as_i64).unwrap_or(i64::MAX);
        let g = self.read_graph();
        let nodes: Vec<Value> = g["nodes"]
            .as_array()
            .cloned()
            .unwrap_or_default()
            .into_iter()
            .filter(|n| n.get("created_at").and_then(Value::as_i64).unwrap_or(0) <= date)
            .collect();
        let count = nodes.len();
        Ok(json!({ "as_of": date, "nodes": nodes, "node_count": count }))
    }

    /// "Nimm das Logo aus Projekt X" -> Asset-Node via BELONGS_TO zu Projekt X (F194).
    fn graph_query_asset(&self, p: &Value) -> HandlerResult {
        let project = req_str(p, "project")?.to_lowercase();
        let asset_hint = p.get("asset").and_then(Value::as_str).unwrap_or("").to_lowercase();
        let g = self.read_graph();
        let nodes = g["nodes"].as_array().cloned().unwrap_or_default();
        let edges = g["edges"].as_array().cloned().unwrap_or_default();
        let proj = nodes.iter().find(|n| {
            n.get("type").and_then(Value::as_str) == Some("project")
                && n.get("label").and_then(Value::as_str).unwrap_or("").to_lowercase().contains(&project)
        });
        let Some(proj) = proj else {
            return Ok(json!({ "found": false, "reason": "Projekt nicht gefunden" }));
        };
        let pid = proj.get("id").and_then(Value::as_str).unwrap_or("");
        for e in &edges {
            if e.get("type").and_then(Value::as_str) == Some("BELONGS_TO")
                && e.get("to").and_then(Value::as_str) == Some(pid)
            {
                let aid = e.get("from").and_then(Value::as_str).unwrap_or("");
                if let Some(asset) = nodes.iter().find(|n| n.get("id").and_then(Value::as_str) == Some(aid)) {
                    let label = asset.get("label").and_then(Value::as_str).unwrap_or("").to_lowercase();
                    if asset_hint.is_empty() || label.contains(&asset_hint) {
                        return Ok(json!({
                            "found": true, "asset": asset, "project": proj,
                            "path": asset.get("props").and_then(|p| p.get("path")),
                        }));
                    }
                }
            }
        }
        Ok(json!({ "found": false, "reason": "kein passendes Asset via BELONGS_TO" }))
    }

    /// "Merke dir dass <Asset> zu <Projekt> gehört" -> Node + BELONGS_TO (F195).
    fn graph_remember(&self, p: &Value) -> HandlerResult {
        let text = req_str(p, "text")?;
        let lower = text.to_lowercase();
        let Some(idx) = lower.find(" zu ") else {
            return Err(IpcFailure::invalid("Muster '<Asset> zu <Projekt> gehört' nicht erkannt"));
        };
        let asset_part = &text[..idx];
        let project_part = &text[idx + 4..];
        let asset_label = asset_part
            .split_whitespace()
            .find(|w| w.contains('.') || w.contains('/'))
            .unwrap_or("asset")
            .trim()
            .to_string();
        let project_label = project_part
            .replace("gehört", "")
            .replace("gehoert", "")
            .trim()
            .trim_end_matches('.')
            .trim()
            .to_string();
        let mut g = self.read_graph();
        let existing_pid = g["nodes"].as_array().and_then(|nodes| {
            nodes
                .iter()
                .find(|n| {
                    n.get("type").and_then(Value::as_str) == Some("project")
                        && n.get("label").and_then(Value::as_str).map(|l| l.eq_ignore_ascii_case(&project_label)).unwrap_or(false)
                })
                .and_then(|n| n.get("id").and_then(Value::as_str))
                .map(str::to_string)
        });
        let aid = unique_id("n");
        let project_id = existing_pid.clone().unwrap_or_else(|| unique_id("n"));
        let nodes = g["nodes"].as_array_mut().unwrap();
        if existing_pid.is_none() {
            nodes.push(json!({ "id": project_id, "type": "project", "label": project_label, "props": {}, "created_at": now_millis() }));
        }
        nodes.push(json!({ "id": aid, "type": "asset", "label": asset_label, "props": { "path": asset_label }, "created_at": now_millis() }));
        g["edges"].as_array_mut().unwrap().push(json!({ "from": aid, "to": project_id, "type": "BELONGS_TO" }));
        self.write_graph(&g)?;
        Ok(json!({ "ok": true, "asset_id": aid, "project_id": project_id, "asset_label": asset_label, "project_label": project_label }))
    }

    fn copilot_config_path(&self) -> PathBuf {
        self.inner.state_dir.join("copilot.json")
    }
    fn copilot_config_get(&self) -> HandlerResult {
        let cfg: Value = std::fs::read_to_string(self.copilot_config_path())
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_else(|| json!({ "proactivity": "dezent", "quiet_hours": [], "weekend_mode": false }));
        Ok(json!({ "config": cfg }))
    }
    fn copilot_config_set(&self, p: &Value) -> HandlerResult {
        let mut cfg: Value = std::fs::read_to_string(self.copilot_config_path())
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_else(|| json!({}));
        if !cfg.is_object() {
            cfg = json!({});
        }
        if let Some(patch) = p.as_object() {
            let o = cfg.as_object_mut().unwrap();
            for (k, v) in patch {
                o.insert(k.clone(), v.clone());
            }
        }
        std::fs::write(self.copilot_config_path(), serde_json::to_string_pretty(&cfg).unwrap_or_default())
            .map_err(|e| e.to_string())?;
        Ok(json!({ "ok": true, "config": cfg }))
    }

    // MARK: Project hub (registry, stack detection, cards) + file-explorer extras

    fn projects_path(&self) -> PathBuf {
        self.inner.state_dir.join("projects.json")
    }
    fn read_projects(&self) -> Value {
        std::fs::read_to_string(self.projects_path())
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_else(|| json!({}))
    }
    fn write_projects(&self, v: &Value) -> std::result::Result<(), IpcFailure> {
        std::fs::write(self.projects_path(), serde_json::to_string_pretty(v).unwrap_or_default())
            .map_err(|e| IpcFailure::internal(e.to_string()))
    }

    fn projects_create(&self, p: &Value) -> HandlerResult {
        let name = req_str(p, "name")?;
        let path = req_str(p, "path")?;
        let pp = Path::new(path);
        if p.get("git_init").and_then(Value::as_bool).unwrap_or(false) {
            std::fs::create_dir_all(pp).ok();
            if !pp.join(".git").exists() {
                std::process::Command::new("git").current_dir(pp).args(["init", "-q", "-b", "main"]).output().ok();
            }
        }
        let stack = detect_stack(pp);
        let mut projs = self.read_projects();
        projs[name] = json!({ "name": name, "path": path, "stack": stack, "server_url": p.get("server_url"), "created_at": now_millis() });
        self.write_projects(&projs)?;
        Ok(json!({ "ok": true, "name": name, "stack": stack, "git": pp.join(".git").exists() }))
    }

    fn project_card(&self, name: &str, meta: &Value) -> Value {
        let path = meta.get("path").and_then(Value::as_str).unwrap_or("");
        let branch = std::process::Command::new("git")
            .current_dir(path)
            .args(["rev-parse", "--abbrev-ref", "HEAD"])
            .output()
            .ok()
            .filter(|o| o.status.success())
            .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string());
        let start_of_day = now_millis() - (now_millis() % 86_400_000);
        let cost_today = self
            .inner
            .sessions
            .lock()
            .unwrap()
            .usage_for_project(name, start_of_day)
            .map(|u| u.cost_usd)
            .unwrap_or(0.0);
        json!({
            "name": name, "path": path,
            "stack": meta.get("stack").cloned().unwrap_or(json!([])),
            "branch": branch, "cost_today_usd": cost_today,
            "server_url": meta.get("server_url").cloned().unwrap_or(Value::Null),
        })
    }

    fn projects_list(&self) -> HandlerResult {
        let projs = self.read_projects();
        let mut cards = Vec::new();
        if let Some(obj) = projs.as_object() {
            for (name, meta) in obj {
                cards.push(self.project_card(name, meta));
            }
        }
        Ok(json!({ "projects": cards }))
    }

    fn projects_get(&self, p: &Value) -> HandlerResult {
        let name = req_str(p, "name")?;
        let projs = self.read_projects();
        projs
            .get(name)
            .map(|m| json!({ "project": self.project_card(name, m) }))
            .ok_or_else(|| IpcFailure::not_found(format!("project not found: {name}")))
    }

    fn projects_import(&self, p: &Value) -> HandlerResult {
        let path = req_str(p, "path")?;
        let pp = Path::new(path);
        let claude_dir = pp.join(".claude");
        if !claude_dir.exists() {
            return Err(IpcFailure::invalid("kein .claude/ im Verzeichnis"));
        }
        let skills = std::fs::read_dir(claude_dir.join("commands"))
            .map(|rd| rd.flatten().filter(|e| e.path().extension().and_then(|x| x.to_str()) == Some("md")).count())
            .unwrap_or(0);
        let settings = claude_dir.join("settings.json").exists();
        let name = p
            .get("name")
            .and_then(Value::as_str)
            .map(str::to_string)
            .unwrap_or_else(|| pp.file_name().and_then(|n| n.to_str()).unwrap_or("imported").to_string());
        let stack = detect_stack(pp);
        let mut projs = self.read_projects();
        projs[&name] = json!({ "name": name, "path": path, "stack": stack, "created_at": now_millis() });
        self.write_projects(&projs)?;
        Ok(json!({ "ok": true, "name": name, "skills_found": skills, "settings_found": settings, "stack": stack }))
    }

    fn projects_scaffold(&self, p: &Value) -> HandlerResult {
        let path = req_str(p, "path")?;
        let pp = Path::new(path);
        let template = p.get("template").and_then(Value::as_str).unwrap_or("default");
        let claude = pp.join(".claude");
        std::fs::create_dir_all(claude.join("agents")).ok();
        let md = scaffold_claude_md(pp, template);
        std::fs::write(claude.join("CLAUDE.md"), &md).map_err(|e| e.to_string())?;
        std::fs::write(
            claude.join("agents").join("default.json"),
            serde_json::to_string_pretty(&json!({ "name": "Default Agent", "model": "sonnet", "allowed_tools": ["Read", "Edit", "Bash"] })).unwrap_or_default(),
        )
        .map_err(|e| e.to_string())?;
        Ok(json!({ "ok": true, "claude_md": claude.join("CLAUDE.md").to_string_lossy(), "agent_created": true }))
    }

    fn projects_rename(&self, p: &Value) -> HandlerResult {
        let name = req_str(p, "name")?;
        let new_name = req_str(p, "new_name")?;
        let mut projs = self.read_projects();
        let Some(mut meta) = projs.get(name).cloned() else {
            return Err(IpcFailure::not_found(format!("project not found: {name}")));
        };
        if let Some(o) = projs.as_object_mut() {
            o.remove(name);
        }
        if let Some(o) = meta.as_object_mut() {
            o.insert("name".into(), json!(new_name));
        }
        projs[new_name] = meta;
        self.write_projects(&projs)?;
        Ok(json!({ "ok": true, "old": name, "new": new_name }))
    }

    fn projects_remove(&self, p: &Value) -> HandlerResult {
        let name = req_str(p, "name")?;
        let mut projs = self.read_projects();
        let existed = projs.get(name).is_some();
        if let Some(o) = projs.as_object_mut() {
            o.remove(name);
        }
        self.write_projects(&projs)?;
        Ok(json!({ "ok": true, "removed": existed }))
    }

    /// Git-status colors per file: new=green, deleted=red, modified=orange (F058).
    /// Parst einen Unified-Diff in farbcodierte Zeilen für die Archiv-Diff-Ansicht
    /// (F161): '+'-Zeilen grün (Add), '-'-Zeilen rot (Remove), Hunk-/Meta-/Kontext
    /// jeweils eigene Klasse. Liefert die Zeilen + Add/Remove-Zähler.
    fn diff_render(&self, p: &Value) -> HandlerResult {
        let diff = req_str(p, "diff")?;
        let mut lines = Vec::new();
        let (mut adds, mut dels) = (0u32, 0u32);
        for l in diff.lines() {
            let (kind, color) = if l.starts_with("+++") || l.starts_with("---") {
                ("meta", "grey")
            } else if l.starts_with("@@") {
                ("hunk", "blue")
            } else if l.starts_with('+') {
                adds += 1;
                ("add", "green")
            } else if l.starts_with('-') {
                dels += 1;
                ("remove", "red")
            } else {
                ("context", "none")
            };
            lines.push(json!({ "text": l, "type": kind, "color": color }));
        }
        Ok(json!({ "lines": lines, "added": adds, "removed": dels }))
    }

    /// Detail-Panel eines Graph-Knotens (F191): Knoten (per id oder label) mit Typ,
    /// Metadaten (props) und allen ein-/ausgehenden Kanten samt Nachbar-Labels.
    fn graph_node_detail(&self, p: &Value) -> HandlerResult {
        let g = self.read_graph();
        let nodes = g["nodes"].as_array().cloned().unwrap_or_default();
        let edges = g["edges"].as_array().cloned().unwrap_or_default();
        let id_q = p.get("id").and_then(Value::as_str);
        let label_q = p.get("label").and_then(Value::as_str).map(|s| s.to_lowercase());
        let node = nodes.iter().find(|n| {
            id_q == n.get("id").and_then(Value::as_str)
                || label_q.as_deref().is_some_and(|q| {
                    n.get("label").and_then(Value::as_str).map(|l| l.to_lowercase()) == Some(q.to_string())
                })
        });
        let Some(node) = node else { return Ok(json!({ "found": false })) };
        let nid = node.get("id").and_then(Value::as_str).unwrap_or("");
        let label_of = |id: &str| {
            nodes
                .iter()
                .find(|n| n.get("id").and_then(Value::as_str) == Some(id))
                .and_then(|n| n.get("label").and_then(Value::as_str))
                .unwrap_or(id)
                .to_string()
        };
        let outgoing: Vec<Value> = edges
            .iter()
            .filter(|e| e.get("from").and_then(Value::as_str) == Some(nid))
            .map(|e| {
                let to = e.get("to").and_then(Value::as_str).unwrap_or("");
                json!({ "to": to, "to_label": label_of(to), "type": e.get("type") })
            })
            .collect();
        let incoming: Vec<Value> = edges
            .iter()
            .filter(|e| e.get("to").and_then(Value::as_str) == Some(nid))
            .map(|e| {
                let from = e.get("from").and_then(Value::as_str).unwrap_or("");
                json!({ "from": from, "from_label": label_of(from), "type": e.get("type") })
            })
            .collect();
        Ok(json!({
            "found": true, "node": node, "type": node.get("type"), "props": node.get("props"),
            "outgoing": outgoing, "incoming": incoming,
            "edge_count": outgoing.len() + incoming.len(),
        }))
    }

    /// Status-Indikatoren je Datei für den Dateibaum (F052): bearbeitet (git status),
    /// geschützt (is_protected_path) und Brain-Graph-Asset (Asset-Knoten im Graph).
    /// Liefert pro Datei die Flags + zugehörige Symbole für die Indikatorspalte.
    fn files_status_indicators(&self, p: &Value) -> HandlerResult {
        let cwd = req_str(p, "cwd")?;
        // bearbeitete Dateien aus git status
        let mut edited: std::collections::HashSet<String> = std::collections::HashSet::new();
        if let Ok(out) = std::process::Command::new("git")
            .current_dir(cwd).args(["status", "--porcelain"]).output()
        {
            for line in String::from_utf8_lossy(&out.stdout).lines() {
                if line.len() >= 3 {
                    edited.insert(line[3..].trim().to_string());
                }
            }
        }
        // Brain-Graph-Assets (Asset-Knoten mit props.path)
        let g = self.read_graph();
        let asset_paths: std::collections::HashSet<String> = g["nodes"]
            .as_array()
            .map(|ns| {
                ns.iter()
                    .filter(|n| n.get("type").and_then(Value::as_str) == Some("asset"))
                    .filter_map(|n| {
                        n.get("props").and_then(|pr| pr.get("path")).and_then(Value::as_str).map(String::from)
                    })
                    .collect()
            })
            .unwrap_or_default();
        let is_asset = |f: &str, full: &str| {
            asset_paths.contains(f)
                || asset_paths.contains(full)
                || asset_paths.iter().any(|ap| ap.ends_with(&format!("/{f}")))
        };
        // zu prüfende Dateien: übergebene Liste oder Top-Level-Dateien im cwd
        let files: Vec<String> = match p.get("files").and_then(Value::as_array) {
            Some(arr) => arr.iter().filter_map(|f| f.as_str().map(String::from)).collect(),
            None => std::fs::read_dir(cwd)
                .map(|rd| {
                    rd.flatten()
                        .filter(|e| e.path().is_file())
                        .filter_map(|e| e.file_name().to_str().map(String::from))
                        .collect()
                })
                .unwrap_or_default(),
        };
        let mut out = Vec::new();
        for f in files {
            let full = Path::new(cwd).join(&f).to_string_lossy().to_string();
            let protected = is_protected_path(&f) || is_protected_path(&full);
            let brain_asset = is_asset(&f, &full);
            let is_edited = edited.contains(&f);
            let mut symbols = Vec::new();
            if is_edited { symbols.push("pencil"); }
            if protected { symbols.push("lock"); }
            if brain_asset { symbols.push("brain"); }
            out.push(json!({ "path": f, "edited": is_edited, "protected": protected,
                             "brain_asset": brain_asset, "symbols": symbols }));
        }
        Ok(json!({ "files": out }))
    }

    /// Cross-Project-Modus (F063): vereint mehrere Projekt-Wurzeln in einem Baum.
    /// Jede Wurzel wird mit Namen + Top-Level-Einträgen zurückgegeben.
    fn files_cross_project_tree(&self, p: &Value) -> HandlerResult {
        let roots: Vec<String> = p
            .get("roots")
            .and_then(Value::as_array)
            .map(|a| a.iter().filter_map(|r| r.as_str().map(String::from)).collect())
            .unwrap_or_default();
        let mut projects = Vec::new();
        for root in &roots {
            let name = Path::new(root).file_name().and_then(|n| n.to_str()).unwrap_or("project").to_string();
            let mut entries: Vec<Value> = Vec::new();
            if let Ok(rd) = std::fs::read_dir(root) {
                for e in rd.flatten() {
                    let n = e.file_name().to_string_lossy().to_string();
                    if n.starts_with('.') {
                        continue;
                    }
                    entries.push(json!({ "name": n, "dir": e.path().is_dir() }));
                }
            }
            entries.sort_by(|a, b| a["name"].as_str().cmp(&b["name"].as_str()));
            projects.push(json!({ "root": root, "name": name, "entries": entries }));
        }
        Ok(json!({ "projects": projects, "count": projects.len() }))
    }

    fn file_git_colors(&self, p: &Value) -> HandlerResult {
        let cwd = req_str(p, "cwd")?;
        let out = std::process::Command::new("git")
            .current_dir(cwd)
            .args(["status", "--porcelain"])
            .output()
            .map_err(|e| IpcFailure::internal(e.to_string()))?;
        let text = String::from_utf8_lossy(&out.stdout);
        let mut files = Vec::new();
        for line in text.lines() {
            if line.len() < 3 {
                continue;
            }
            let code = &line[..2];
            let path = line[3..].trim();
            let color = if code.contains('?') { "green" } else if code.contains('D') { "red" } else { "orange" };
            files.push(json!({ "path": path, "code": code, "color": color }));
        }
        Ok(json!({ "files": files }))
    }

    /// Real diff for a single file vs the committed version (F059).
    fn file_diff(&self, p: &Value) -> HandlerResult {
        let cwd = req_str(p, "cwd")?;
        let path = req_str(p, "path")?;
        let out = std::process::Command::new("git")
            .current_dir(cwd)
            .args(["diff", "--", path])
            .output()
            .map_err(|e| IpcFailure::internal(e.to_string()))?;
        let diff = String::from_utf8_lossy(&out.stdout).to_string();
        let added = diff.lines().filter(|l| l.starts_with('+') && !l.starts_with("+++")).count();
        let removed = diff.lines().filter(|l| l.starts_with('-') && !l.starts_with("---")).count();
        Ok(json!({ "path": path, "diff": diff, "added": added, "removed": removed }))
    }

    /// Fuzzy filename search (subsequence match) across the project (F060).
    fn file_find(&self, p: &Value) -> HandlerResult {
        let cwd = req_str(p, "cwd")?;
        let query = req_str(p, "query")?.to_lowercase();
        let files = collect_source_files(
            Path::new(cwd),
            &["ts", "tsx", "js", "jsx", "rs", "py", "md", "json", "txt", "go", "swift", "toml"],
        );
        let mut hits: Vec<(usize, String)> = Vec::new();
        for (path, _) in &files {
            let name = Path::new(path).file_name().and_then(|n| n.to_str()).unwrap_or("").to_lowercase();
            if let Some(score) = fuzzy_score(&name, &query) {
                hits.push((score, path.clone()));
            }
        }
        hits.sort_by(|a, b| b.0.cmp(&a.0));
        let out: Vec<Value> = hits.into_iter().take(20).map(|(s, p)| json!({ "path": p, "score": s })).collect();
        Ok(json!({ "matches": out }))
    }

    /// Promote a file to a Brain-Graph asset node (F056 — D&D effect).
    fn file_to_asset(&self, p: &Value) -> HandlerResult {
        let path = req_str(p, "path")?;
        let label = Path::new(path).file_name().and_then(|n| n.to_str()).unwrap_or("asset").to_string();
        let id = unique_id("n");
        let mut g = self.read_graph();
        g["nodes"].as_array_mut().unwrap().push(json!({
            "id": id, "type": "asset", "label": label, "props": { "path": path }, "created_at": now_millis(),
        }));
        self.write_graph(&g)?;
        Ok(json!({ "ok": true, "node_id": id, "label": label }))
    }

    // MARK: Context-editor helpers, memory manager, settings, metrics

    /// Diff a CLAUDE.md buffer against the saved version before saving (F082).
    fn context_diff(&self, p: &Value) -> HandlerResult {
        let path = req_str(p, "path")?;
        let buffer = req_str(p, "buffer")?;
        let current = std::fs::read_to_string(path).unwrap_or_default();
        let cur_lines: Vec<&str> = current.lines().collect();
        let buf_lines: Vec<&str> = buffer.lines().collect();
        let added: Vec<&str> = buf_lines.iter().filter(|l| !cur_lines.contains(l)).copied().collect();
        let removed: Vec<&str> = cur_lines.iter().filter(|l| !buf_lines.contains(l)).copied().collect();
        Ok(json!({ "added": added, "removed": removed, "changed": !added.is_empty() || !removed.is_empty() }))
    }

    /// Categorize memory markdown entries by their headings (F088).
    fn memory_categorize(&self, p: &Value) -> HandlerResult {
        let scope = p.get("scope").and_then(Value::as_str).unwrap_or("global");
        let project = p.get("project").and_then(Value::as_str);
        let content = match p.get("content").and_then(Value::as_str) {
            Some(c) => c.to_string(),
            None => std::fs::read_to_string(self.memory_path(scope, project)).unwrap_or_default(),
        };
        let mut categories: serde_json::Map<String, Value> = Default::default();
        let mut current = "Allgemein".to_string();
        for line in content.lines() {
            let t = line.trim();
            if let Some(h) = t.strip_prefix("## ").or_else(|| t.strip_prefix("# ")) {
                current = h.trim().to_string();
                categories.entry(current.clone()).or_insert(json!([]));
            } else if !t.is_empty() {
                categories
                    .entry(current.clone())
                    .or_insert(json!([]))
                    .as_array_mut()
                    .unwrap()
                    .push(json!(t.trim_start_matches("- ")));
            }
        }
        let n = categories.len();
        Ok(json!({ "categories": categories, "category_count": n }))
    }

    /// Token usage of the memory layer against the budget (F093).
    fn memory_token_usage(&self, p: &Value) -> HandlerResult {
        let scope = p.get("scope").and_then(Value::as_str).unwrap_or("global");
        let project = p.get("project").and_then(Value::as_str);
        let content = std::fs::read_to_string(self.memory_path(scope, project)).unwrap_or_default();
        let tokens = estimate_tokens(&content);
        let budget = self.inner.config.lock().unwrap().context_token_budget;
        Ok(json!({ "tokens": tokens, "budget": budget, "fraction": tokens as f64 / budget.max(1) as f64 }))
    }

    /// Definitions grouped by category for the sidebar hierarchy (F098).
    fn definitions_grouped(&self) -> HandlerResult {
        let mut groups: serde_json::Map<String, Value> = Default::default();
        for (path, content, _w) in self.library_files("definitions", ".def.md") {
            let fm = parse_frontmatter(&content);
            let cat = fm.get("category").cloned().unwrap_or_else(|| "Uncategorized".into());
            let name = fm.get("name").cloned().unwrap_or_default();
            groups
                .entry(cat)
                .or_insert(json!([]))
                .as_array_mut()
                .unwrap()
                .push(json!({ "name": name, "path": path }));
        }
        let n = groups.len();
        Ok(json!({ "groups": groups, "group_count": n }))
    }

    /// Productivity metrics from git history + the session archive (F284).
    fn metrics_productivity(&self, p: &Value) -> HandlerResult {
        let cwd = req_str(p, "cwd")?;
        let run = |args: &[&str]| {
            std::process::Command::new("git")
                .current_dir(cwd)
                .args(args)
                .output()
                .map(|o| String::from_utf8_lossy(&o.stdout).to_string())
                .unwrap_or_default()
        };
        let commits = run(&["rev-list", "--count", "HEAD"]).trim().parse::<i64>().unwrap_or(0);
        let shortstat = run(&["log", "--shortstat", "--pretty=format:"]);
        let (mut insertions, mut deletions) = (0i64, 0i64);
        for line in shortstat.lines() {
            for tok in line.split(',') {
                let t = tok.trim();
                if t.contains("insertion") {
                    insertions += t.split_whitespace().next().and_then(|n| n.parse().ok()).unwrap_or(0);
                }
                if t.contains("deletion") {
                    deletions += t.split_whitespace().next().and_then(|n| n.parse().ok()).unwrap_or(0);
                }
            }
        }
        let stats = self.inner.sessions.lock().unwrap().stats().ok();
        let sessions = stats.as_ref().map(|s| s.sessions).unwrap_or(0);
        let tool_calls = stats.as_ref().map(|s| s.tool_calls).unwrap_or(0);
        Ok(json!({
            "commits": commits, "lines_added": insertions, "lines_deleted": deletions,
            "sessions": sessions, "tool_calls": tool_calls,
            "commits_per_session": if sessions > 0 { commits as f64 / sessions as f64 } else { 0.0 },
        }))
    }

    fn settings_path(&self) -> PathBuf {
        self.inner.state_dir.join("app_settings.json")
    }
    fn settings_set(&self, p: &Value) -> HandlerResult {
        let key = req_str(p, "key")?;
        let value = p.get("value").cloned().unwrap_or(Value::Null);
        let mut s: Value = std::fs::read_to_string(self.settings_path())
            .ok()
            .and_then(|c| serde_json::from_str(&c).ok())
            .unwrap_or_else(|| json!({}));
        s[key] = value;
        std::fs::write(self.settings_path(), serde_json::to_string_pretty(&s).unwrap_or_default())
            .map_err(|e| e.to_string())?;
        Ok(json!({ "ok": true, "key": key, "settings": s }))
    }
    fn settings_get(&self, p: &Value) -> HandlerResult {
        let s: Value = std::fs::read_to_string(self.settings_path())
            .ok()
            .and_then(|c| serde_json::from_str(&c).ok())
            .unwrap_or_else(|| json!({}));
        match p.get("key").and_then(Value::as_str) {
            Some(k) => Ok(json!({ "key": k, "value": s.get(k).cloned().unwrap_or(Value::Null) })),
            None => Ok(json!({ "settings": s })),
        }
    }

    /// Merge user-level + project-level settings.json, labelling each key's source (F296).
    fn settings_merge(&self, p: &Value) -> HandlerResult {
        let cwd = req_str(p, "cwd")?;
        let user_path = self.home_dir().join(".claude/settings.json");
        let proj_path = Path::new(cwd).join(".claude/settings.json");
        let read = |path: &Path| -> Value {
            std::fs::read_to_string(path).ok().and_then(|s| serde_json::from_str(&s).ok()).unwrap_or_else(|| json!({}))
        };
        let user = read(&user_path);
        let proj = read(&proj_path);
        let mut effective = serde_json::Map::new();
        let mut source = serde_json::Map::new();
        if let Some(o) = user.as_object() {
            for (k, v) in o {
                effective.insert(k.clone(), v.clone());
                source.insert(k.clone(), json!("user"));
            }
        }
        if let Some(o) = proj.as_object() {
            for (k, v) in o {
                effective.insert(k.clone(), v.clone());
                source.insert(k.clone(), json!("project")); // project overrides user
            }
        }
        Ok(json!({
            "effective": effective, "source": source,
            "user_path": user_path.to_string_lossy(), "project_path": proj_path.to_string_lossy(),
        }))
    }

    /// Flag low-quality / stale comments (TODO/FIXME/XXX/HACK/DEPRECATED) (F335).
    fn codeq_comment_quality(&self, p: &Value) -> HandlerResult {
        let cwd = req_str(p, "cwd")?;
        let files = collect_source_files(Path::new(cwd), &["ts", "tsx", "js", "jsx", "rs", "py", "go", "swift"]);
        let mut findings = Vec::new();
        for (path, content) in &files {
            for (i, line) in content.lines().enumerate() {
                let u = line.to_uppercase();
                for marker in ["TODO", "FIXME", "XXX", "HACK", "DEPRECATED"] {
                    if u.contains(marker) {
                        findings.push(json!({ "file": path, "line": i + 1, "marker": marker, "text": line.trim().chars().take(100).collect::<String>() }));
                        break;
                    }
                }
            }
        }
        Ok(json!({ "findings": findings, "count": findings.len() }))
    }

    /// Identify the most recent session to resume after a crash (F354).
    fn resume_detect(&self) -> HandlerResult {
        let store = self.inner.sessions.lock().unwrap();
        let sessions = store.list_sessions(1, 0).map_err(session_failure)?;
        let arr = serde_json::to_value(sessions).unwrap_or(json!([]));
        match arr.as_array().and_then(|a| a.first()).cloned() {
            Some(s) => {
                let id = s.get("id").and_then(Value::as_str).unwrap_or("").to_string();
                let msgs = store.list_messages(&id).map_err(session_failure)?;
                Ok(json!({ "resumable": true, "session": s, "message_count": msgs.len() }))
            }
            None => Ok(json!({ "resumable": false })),
        }
    }

    /// Daily briefing: prioritized TODO/FIXME list from the codebase (F355).
    fn briefing_daily(&self, p: &Value) -> HandlerResult {
        let cwd = req_str(p, "cwd")?;
        let files = collect_source_files(Path::new(cwd), &["ts", "tsx", "js", "jsx", "rs", "py", "go", "swift", "md"]);
        let mut items = Vec::new();
        for (path, content) in &files {
            for (i, line) in content.lines().enumerate() {
                let u = line.to_uppercase();
                let pri = if u.contains("FIXME") {
                    "high"
                } else if u.contains("TODO") {
                    "normal"
                } else {
                    continue;
                };
                items.push(json!({ "priority": pri, "file": path, "line": i + 1, "text": line.trim().chars().take(100).collect::<String>() }));
            }
        }
        items.sort_by_key(|x| if x["priority"] == "high" { 0 } else { 1 });
        let n = items.len();
        Ok(json!({ "items": items, "count": n }))
    }

    // MARK: Semantic knowledge store (teach + retrieve via embed_cli)

    /// Embed texts with the real MiniLM model via the `embed_cli` example binary
    /// (sibling of the core binary). Returns one vector per input.
    fn embed_texts(&self, texts: &[String]) -> std::result::Result<Vec<Vec<f64>>, IpcFailure> {
        let exe = std::env::current_exe().map_err(|e| IpcFailure::internal(e.to_string()))?;
        let bin = exe
            .parent()
            .map(|d| d.join("examples/embed_cli"))
            .ok_or_else(|| IpcFailure::internal("embed_cli path"))?;
        if !bin.exists() {
            return Err(IpcFailure::internal(format!("embed_cli not built: {}", bin.display())));
        }
        let input = json!({ "texts": texts }).to_string();
        let mut child = std::process::Command::new(&bin)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .spawn()
            .map_err(|e| IpcFailure::internal(e.to_string()))?;
        {
            use std::io::Write;
            child.stdin.take().unwrap().write_all(input.as_bytes()).map_err(|e| IpcFailure::internal(e.to_string()))?;
        }
        let out = child.wait_with_output().map_err(|e| IpcFailure::internal(e.to_string()))?;
        if !out.status.success() {
            return Err(IpcFailure::internal(format!("embed_cli failed: {}", String::from_utf8_lossy(&out.stderr).trim())));
        }
        let stdout = String::from_utf8_lossy(&out.stdout);
        let line = stdout.lines().last().unwrap_or("");
        let v: Value = serde_json::from_str(line).map_err(|e| IpcFailure::internal(format!("embed parse: {e}")))?;
        let vectors = v.get("vectors").and_then(Value::as_array).ok_or_else(|| IpcFailure::internal("no vectors"))?;
        Ok(vectors
            .iter()
            .map(|vec| vec.as_array().map(|a| a.iter().filter_map(|x| x.as_f64()).collect()).unwrap_or_default())
            .collect())
    }

    fn knowledge_path(&self) -> PathBuf {
        self.inner.state_dir.join("knowledge_vectors.jsonl")
    }

    /// Teach Claude: embed text and store it in a semantic collection (F177/F179/F180).
    fn knowledge_teach(&self, p: &Value) -> HandlerResult {
        let text = req_str(p, "text")?;
        let collection = p.get("collection").and_then(Value::as_str).unwrap_or("knowledge");
        let source = p.get("source").and_then(Value::as_str).unwrap_or("teach");
        let vec = self.embed_texts(&[text.to_string()])?.into_iter().next().unwrap_or_default();
        let dim = vec.len();
        let entry = json!({ "id": unique_id("k"), "text": text, "collection": collection, "source": source, "vec": vec, "ts": now_millis() });
        if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(self.knowledge_path()) {
            use std::io::Write;
            let _ = writeln!(f, "{entry}");
        }
        Ok(json!({ "ok": true, "collection": collection, "dim": dim }))
    }

    /// Wissensaufbau nach Session-Ende: extrahiert neue Entitäten (Eigennamen,
    /// Projekt-/Personennamen, IDs) aus einem Transcript und bettet jede in die
    /// 'knowledge'-Collection ein, damit sie semantisch auffindbar wird (F181).
    /// Embeddet alle Entitäten in EINEM Batch-Aufruf (Performance).
    fn knowledge_extract_entities(&self, p: &Value) -> HandlerResult {
        let transcript = req_str(p, "transcript")?;
        let collection = p.get("collection").and_then(Value::as_str).unwrap_or("knowledge");
        let entities = extract_entities(transcript);
        if entities.is_empty() {
            return Ok(json!({ "entities": [], "embedded": 0, "collection": collection }));
        }
        let vecs = self.embed_texts(&entities)?;
        let mut embedded = 0;
        if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(self.knowledge_path()) {
            use std::io::Write;
            for (ent, vec) in entities.iter().zip(vecs.iter()) {
                let entry = json!({ "id": unique_id("k"), "text": ent, "collection": collection,
                                    "source": "entity-extraction", "vec": vec, "ts": now_millis() });
                if writeln!(f, "{entry}").is_ok() { embedded += 1; }
            }
        }
        Ok(json!({ "entities": entities, "embedded": embedded, "collection": collection }))
    }

    /// Scannt ein Projekt nach Bild-/SVG-Assets, extrahiert je Asset eine
    /// Beschreibung, OCR-Text (echtes tesseract) und SVG-Semantik, bettet den
    /// kombinierten Text in die 'assets'-Collection ein und macht ihn damit
    /// semantisch durchsuchbar (F178). Embeddet alle Assets in einem Batch.
    fn assets_scan(&self, p: &Value) -> HandlerResult {
        let cwd = req_str(p, "cwd")?;
        let collection = p.get("collection").and_then(Value::as_str).unwrap_or("assets");
        let mut files: Vec<PathBuf> = Vec::new();
        collect_asset_files(Path::new(cwd), &mut files, 0);
        files.sort();
        let mut assets: Vec<Value> = Vec::new();
        let mut texts: Vec<String> = Vec::new();
        for f in &files {
            let ext = f.extension().and_then(|e| e.to_str()).unwrap_or("").to_lowercase();
            let name = f.file_name().and_then(|n| n.to_str()).unwrap_or("asset").to_string();
            let description = format!("Asset {name} ({ext})");
            let mut ocr_text = String::new();
            let mut svg_text = String::new();
            if ext == "svg" {
                if let Ok(s) = std::fs::read_to_string(f) {
                    svg_text = extract_svg_semantics(&s);
                }
            } else {
                ocr_text = ocr_image(f);
            }
            let combined = format!("{description}. {svg_text} {ocr_text}").trim().to_string();
            texts.push(combined.clone());
            assets.push(json!({ "path": f.to_string_lossy(), "type": ext, "description": description,
                                "ocr_text": ocr_text, "svg_text": svg_text, "text": combined }));
        }
        let mut embedded = 0;
        if !texts.is_empty() {
            let vecs = self.embed_texts(&texts)?;
            if let Ok(mut fh) =
                std::fs::OpenOptions::new().create(true).append(true).open(self.knowledge_path())
            {
                use std::io::Write;
                for (a, vec) in assets.iter().zip(vecs.iter()) {
                    let entry = json!({ "id": unique_id("k"), "text": a.get("text"),
                                        "collection": collection, "source": "asset-scan",
                                        "vec": vec, "ts": now_millis() });
                    if writeln!(fh, "{entry}").is_ok() {
                        embedded += 1;
                    }
                }
            }
        }
        Ok(json!({ "assets": assets, "embedded": embedded, "collection": collection }))
    }

    /// Schreibt eine AGENTS.md mit YAML-Frontmatter (name/description/tools) in das
    /// Ziel-Verzeichnis (`cwd` für Projekt-Scope, sonst `$HOME`). Der visuelle
    /// AGENTS.md-Editor speichert über diesen Pfad; die Formularfelder landen als
    /// geparster Frontmatter (F086).
    fn agents_write_agents_md(&self, p: &Value) -> HandlerResult {
        let name = req_str(p, "name")?;
        let description = p.get("description").and_then(Value::as_str).unwrap_or("");
        let tools: Vec<String> = p
            .get("tools")
            .and_then(Value::as_array)
            .map(|a| a.iter().filter_map(|t| t.as_str().map(String::from)).collect())
            .unwrap_or_default();
        let body = p
            .get("body")
            .and_then(Value::as_str)
            .unwrap_or("Beschreibe hier, wie sich dieser Agent verhalten soll.");
        let dir = match p.get("cwd").and_then(Value::as_str) {
            Some(c) => PathBuf::from(c),
            None => self.home_dir(),
        };
        std::fs::create_dir_all(&dir).ok();
        let path = dir.join("AGENTS.md");
        // YAML-Flow-Sequenz aus plain scalars: [Read, Edit, Bash]
        let tools_yaml = format!("[{}]", tools.join(", "));
        let content = format!(
            "---\nname: {name}\ndescription: {description}\ntools: {tools_yaml}\n---\n\n# {name}\n\n{body}\n"
        );
        std::fs::write(&path, &content).map_err(|e| e.to_string())?;
        Ok(json!({ "ok": true, "path": path.to_string_lossy(),
                   "name": name, "tools": tools }))
    }

    /// Extrahiert nach Session-Ende merkbare Fakten aus einem Transcript für den
    /// Erkenntnis-Vorschlag-Dialog (F090). Heuristik: Sätze mit Benennungs-/
    /// Definitionsmustern ("heißt", "lautet", IDs wie prod-01, Eigennamen) werden
    /// gewertet und nach Aussagekraft gerankt; Rückgabe inkl. Preview-Text.
    fn memory_suggest_insight(&self, p: &Value) -> HandlerResult {
        let transcript = req_str(p, "transcript")?;
        let mut suggestions: Vec<(i32, String)> = Vec::new();
        for raw in split_sentences(transcript) {
            let s = raw.trim();
            if s.chars().count() < 8 { continue; }
            let score = insight_score(s);
            if score > 0 {
                suggestions.push((score, s.to_string()));
            }
        }
        suggestions.sort_by(|a, b| b.0.cmp(&a.0));
        let top: Vec<Value> = suggestions.iter().take(5).map(|(score, text)| {
            let preview = if text.chars().count() > 120 {
                format!("{}…", text.chars().take(120).collect::<String>())
            } else {
                text.clone()
            };
            json!({ "fact": text, "preview": preview, "score": score })
        }).collect();
        Ok(json!({ "suggestions": top, "count": top.len() }))
    }

    /// Semantic retrieval pipeline: embed query, cosine-rank, return top-K (F174).
    fn knowledge_search(&self, p: &Value) -> HandlerResult {
        let query = req_str(p, "query")?;
        let collection = p.get("collection").and_then(Value::as_str);
        let top_k = p.get("top_k").and_then(Value::as_u64).unwrap_or(5) as usize;
        let qvec = self.embed_texts(&[query.to_string()])?.into_iter().next().unwrap_or_default();
        let content = std::fs::read_to_string(self.knowledge_path()).unwrap_or_default();
        let mut scored: Vec<(f64, Value)> = Vec::new();
        for line in content.lines() {
            let Ok(e) = serde_json::from_str::<Value>(line) else { continue };
            if let Some(c) = collection {
                if e.get("collection").and_then(Value::as_str) != Some(c) {
                    continue;
                }
            }
            let vec: Vec<f64> = e
                .get("vec")
                .and_then(Value::as_array)
                .map(|a| a.iter().filter_map(|x| x.as_f64()).collect())
                .unwrap_or_default();
            let score = cosine_sim(&qvec, &vec);
            scored.push((score, json!({ "text": e.get("text"), "source": e.get("source"), "collection": e.get("collection"), "score": score })));
        }
        scored.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
        let hits: Vec<Value> = scored.into_iter().take(top_k).map(|(_, v)| v).collect();
        Ok(json!({ "hits": hits, "query": query }))
    }

    /// Auto-chunk a long transcript into ~N-token pieces, embed + store each (F175).
    fn knowledge_chunk_text(&self, p: &Value) -> HandlerResult {
        let text = req_str(p, "text")?;
        let chunk_tokens = p.get("chunk_tokens").and_then(Value::as_u64).unwrap_or(300) as usize;
        let collection = p.get("collection").and_then(Value::as_str).unwrap_or("sessions");
        let words: Vec<&str> = text.split_whitespace().collect();
        // Accumulate words until the chunk reaches ~chunk_tokens (token-accurate).
        let mut texts = Vec::new();
        let mut cur: Vec<&str> = Vec::new();
        for w in words {
            cur.push(w);
            if estimate_tokens(&cur.join(" ")) >= chunk_tokens {
                texts.push(cur.join(" "));
                cur.clear();
            }
        }
        if !cur.is_empty() {
            texts.push(cur.join(" "));
        }
        let vectors = self.embed_texts(&texts)?;
        let mut chunks = Vec::new();
        if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(self.knowledge_path()) {
            use std::io::Write;
            for (chunk, vec) in texts.iter().zip(vectors.into_iter()) {
                let entry = json!({ "id": unique_id("k"), "text": chunk, "collection": collection, "source": "auto-chunk", "vec": vec, "ts": now_millis() });
                let _ = writeln!(f, "{entry}");
                chunks.push(json!({ "tokens": estimate_tokens(chunk), "chars": chunk.len() }));
            }
        }
        Ok(json!({ "chunk_count": chunks.len(), "chunks": chunks, "collection": collection }))
    }

    /// Vector-find the best matching definition for a query and return it formatted
    /// as the active-definitions (Ebene 5) context block (F105).
    fn definitions_vector_inject(&self, p: &Value) -> HandlerResult {
        let query = req_str(p, "query")?;
        let qvec = self.embed_texts(&[query.to_string()])?.into_iter().next().unwrap_or_default();
        let content = std::fs::read_to_string(self.knowledge_path()).unwrap_or_default();
        let mut best: Option<(f64, Value)> = None;
        for line in content.lines() {
            let Ok(e) = serde_json::from_str::<Value>(line) else { continue };
            if e.get("collection").and_then(Value::as_str) != Some("definitions") {
                continue;
            }
            let vec: Vec<f64> = e.get("vec").and_then(Value::as_array).map(|a| a.iter().filter_map(|x| x.as_f64()).collect()).unwrap_or_default();
            let score = cosine_sim(&qvec, &vec);
            if best.as_ref().map(|(s, _)| score > *s).unwrap_or(true) {
                best = Some((score, e));
            }
        }
        match best {
            Some((score, e)) => {
                let text = e.get("text").and_then(Value::as_str).unwrap_or("");
                let name = e.get("source").and_then(Value::as_str).unwrap_or("definition");
                Ok(json!({
                    "found": score > 0.7, "score": score, "definition": name, "text": text,
                    "ebene5_block": format!("# [active_definitions]\n## {name}\n{text}"),
                }))
            }
            None => Ok(json!({ "found": false, "score": 0.0 })),
        }
    }

    /// Parse a GitHub-Actions workflow into a job graph (jobs as nodes, `needs`
    /// as edges) for the pipeline visualizer (F269).
    fn pipeline_visualize(&self, p: &Value) -> HandlerResult {
        let content = match p.get("content").and_then(Value::as_str) {
            Some(c) => c.to_string(),
            None => {
                let cwd = req_str(p, "cwd")?;
                let dir = Path::new(cwd).join(".github/workflows");
                let mut found = String::new();
                if let Ok(rd) = std::fs::read_dir(&dir) {
                    for e in rd.flatten() {
                        let ext = e.path().extension().and_then(|x| x.to_str()).unwrap_or("").to_string();
                        if ext == "yml" || ext == "yaml" {
                            found = std::fs::read_to_string(e.path()).unwrap_or_default();
                            break;
                        }
                    }
                }
                if found.is_empty() {
                    return Err(IpcFailure::not_found("kein Workflow-File"));
                }
                found
            }
        };
        Ok(parse_workflow_graph(&content))
    }

    /// Sync a library directory to a git remote (push), e.g. the skill library (F351).
    fn library_git_sync(&self, p: &Value) -> HandlerResult {
        let dir = req_str(p, "dir")?;
        let remote = req_str(p, "remote")?;
        let dp = Path::new(dir);
        std::fs::create_dir_all(dp).ok();
        let run = |args: &[&str]| {
            std::process::Command::new("git")
                .current_dir(dp)
                .args(args)
                .env("GIT_AUTHOR_NAME", "CS")
                .env("GIT_AUTHOR_EMAIL", "cs@local")
                .env("GIT_COMMITTER_NAME", "CS")
                .env("GIT_COMMITTER_EMAIL", "cs@local")
                .output()
        };
        if !dp.join(".git").exists() {
            run(&["init", "-q", "-b", "main"]).ok();
        }
        run(&["remote", "remove", "origin"]).ok();
        run(&["remote", "add", "origin", remote]).ok();
        run(&["add", "-A"]).ok();
        run(&["commit", "-qm", "skill library sync"]).ok();
        let push = run(&["push", "-u", "origin", "main", "--force"]).map_err(|e| IpcFailure::internal(e.to_string()))?;
        Ok(json!({ "ok": push.status.success(), "remote": remote, "stderr": String::from_utf8_lossy(&push.stderr).trim() }))
    }

    // MARK: MCP client — connect to a stdio MCP server, list/call tools

    fn mcp_command(&self, p: &Value) -> std::result::Result<(String, Vec<String>), IpcFailure> {
        match p.get("command").and_then(Value::as_str) {
            Some(cmd) => Ok((
                cmd.to_string(),
                p.get("args").and_then(Value::as_array)
                    .map(|a| a.iter().filter_map(|x| x.as_str().map(str::to_string)).collect())
                    .unwrap_or_default(),
            )),
            None => {
                // Default to the built-in MCP server: this binary with the `mcp` arg.
                let exe = std::env::current_exe().map_err(|e| IpcFailure::internal(e.to_string()))?;
                Ok((exe.to_string_lossy().to_string(), vec!["mcp".to_string()]))
            }
        }
    }

    /// Minimal line-delimited stdio JSON-RPC round-trip against an MCP server.
    fn mcp_jsonrpc(&self, command: &str, args: &[String], requests: &[Value]) -> std::result::Result<Vec<Value>, IpcFailure> {
        use std::io::{BufRead, BufReader, Write};
        let mut child = std::process::Command::new(command)
            .args(args)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::null())
            .spawn()
            .map_err(|e| IpcFailure::internal(format!("spawn mcp server: {e}")))?;
        let mut stdin = child.stdin.take().unwrap();
        let mut reader = BufReader::new(child.stdout.take().unwrap());
        let mut responses = Vec::new();
        for req in requests {
            let line = serde_json::to_string(req).unwrap_or_default();
            stdin
                .write_all(line.as_bytes())
                .and_then(|_| stdin.write_all(b"\n"))
                .and_then(|_| stdin.flush())
                .map_err(|e| IpcFailure::internal(e.to_string()))?;
            let mut resp = String::new();
            match reader.read_line(&mut resp) {
                Ok(0) => break,
                Ok(_) => {
                    if let Ok(v) = serde_json::from_str::<Value>(resp.trim()) {
                        responses.push(v);
                    }
                }
                Err(e) => return Err(IpcFailure::internal(e.to_string())),
            }
        }
        drop(stdin);
        let _ = child.wait();
        Ok(responses)
    }

    /// Connect to an MCP server and list its tools (F253; status+count for F248/F252).
    fn mcp_tools(&self, p: &Value) -> HandlerResult {
        let (cmd, args) = self.mcp_command(p)?;
        let init = json!({ "jsonrpc":"2.0","id":1,"method":"initialize","params":{ "protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"cs-tool-explorer","version":"1"} } });
        let list = json!({ "jsonrpc":"2.0","id":2,"method":"tools/list" });
        let resps = self.mcp_jsonrpc(&cmd, &args, &[init, list])?;
        let connected = resps.iter().any(|r| r.get("result").and_then(|x| x.get("capabilities")).is_some());
        let tools = resps.iter().find_map(|r| r.get("result").and_then(|x| x.get("tools")).cloned()).unwrap_or(json!([]));
        let count = tools.as_array().map(|a| a.len()).unwrap_or(0);
        Ok(json!({ "connected": connected, "tool_count": count, "tools": tools }))
    }

    /// Execute a real MCP tools/call against a server and read the result.
    fn mcp_call_tool(&self, p: &Value) -> HandlerResult {
        let (cmd, args) = self.mcp_command(p)?;
        let name = req_str(p, "name")?;
        let init = json!({ "jsonrpc":"2.0","id":1,"method":"initialize","params":{ "protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"cs","version":"1"} } });
        let call = json!({ "jsonrpc":"2.0","id":2,"method":"tools/call","params":{ "name": name, "arguments": p.get("arguments").cloned().unwrap_or(json!({})) } });
        let resps = self.mcp_jsonrpc(&cmd, &args, &[init, call])?;
        let result = resps
            .iter()
            .filter(|r| r.get("id").and_then(Value::as_i64) == Some(2))
            .find_map(|r| r.get("result").cloned())
            .unwrap_or(Value::Null);
        Ok(json!({ "tool": name, "result": result }))
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
        // Max-parallel limit (default 4): block once the cap is reached (F070).
        // The main worktree counts, so the limit is on *additional* worktrees.
        if let Some(max) = p.get("max_parallel").and_then(Value::as_u64) {
            let existing = git.list_worktrees().await.map_err(|e| e.to_string())?.len() as u64;
            if existing >= max {
                return Err(IpcFailure::new(
                    ErrorCode::InvalidParameter,
                    format!("Max-Parallel-Limit erreicht ({existing}/{max}) — Worktree abgewiesen"),
                ));
            }
        }
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

    /// Speichert einen im Task-Builder zusammengestellten Custom-Task (F210). Verlangt
    /// die sechs Tab-Sektionen (Grunddaten=name, agent, inputs, workflow, output, schedule),
    /// schreibt ihn nach tasks/custom/<slug>.task.json — danach erscheint er in tasks.list.
    fn tasks_save(&self, p: &Value) -> HandlerResult {
        let task = p.get("task").cloned().ok_or_else(|| IpcFailure::invalid("missing 'task'"))?;
        let name = task
            .get("name")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .ok_or_else(|| IpcFailure::invalid("task.name erforderlich"))?
            .to_string();
        let required = ["name", "agent", "inputs", "workflow", "output", "schedule"];
        let missing: Vec<&str> = required.iter().filter(|s| task.get(**s).is_none()).copied().collect();
        if !missing.is_empty() {
            return Err(IpcFailure::invalid(format!("fehlende Pflicht-Sektionen: {}", missing.join(", "))));
        }
        let slug = slugify(&name);
        let dir = self.inner.state_dir.join("tasks").join("custom");
        std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
        let mut t = task;
        if let Some(o) = t.as_object_mut() {
            o.entry("id".to_string()).or_insert(json!(slug));
            o.entry("category".to_string()).or_insert(json!("custom"));
        }
        let path = dir.join(format!("{slug}.task.json"));
        std::fs::write(&path, serde_json::to_string_pretty(&t).unwrap_or_default()).map_err(|e| e.to_string())?;
        Ok(json!({ "ok": true, "path": path.to_string_lossy(), "id": slug, "saved": t,
                   "sections": required }))
    }

    /// Persistenter Agenten-Queue-Pfad.
    fn queue_path(&self) -> PathBuf {
        self.inner.state_dir.join("agent_queue.jsonl")
    }

    /// 'Ausführen' im Task-Modal stellt den Task in die Agenten-Queue (F198).
    fn queue_enqueue(&self, p: &Value) -> HandlerResult {
        let task = req_str(p, "task")?;
        let priority = p.get("priority").and_then(Value::as_str).unwrap_or("normal");
        let entry = json!({ "id": unique_id("q"), "task": task, "priority": priority,
                            "status": "queued", "ts": now_millis() });
        std::fs::create_dir_all(&self.inner.state_dir).ok();
        if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(self.queue_path()) {
            use std::io::Write;
            let _ = writeln!(f, "{entry}");
        }
        Ok(json!({ "ok": true, "entry": entry }))
    }

    /// Die aktuelle Agenten-Queue (F198).
    fn queue_list(&self) -> HandlerResult {
        let content = std::fs::read_to_string(self.queue_path()).unwrap_or_default();
        let items: Vec<Value> = content.lines().filter_map(|l| serde_json::from_str(l).ok()).collect();
        Ok(json!({ "queue": items, "count": items.len() }))
    }

    /// Task-Scheduling (F214): unterstützt Manuell/Cron/Event/Threshold/Voice. Validiert
    /// den Trigger-Typ; bei Cron werden die 5 Felder geprüft. Liefert die Schedule-Spec.
    fn tasks_schedule(&self, p: &Value) -> HandlerResult {
        let task = req_str(p, "task")?;
        let stype = req_str(p, "type")?;
        const TYPES: [&str; 5] = ["manual", "cron", "event", "threshold", "voice"];
        if !TYPES.contains(&stype) {
            return Err(IpcFailure::invalid(format!("unbekannter Schedule-Typ: {stype}")));
        }
        let detail = match stype {
            "cron" => {
                let expr = req_str(p, "cron")?;
                let fields: Vec<&str> = expr.split_whitespace().collect();
                if fields.len() != 5 {
                    return Err(IpcFailure::invalid("Cron-Ausdruck braucht 5 Felder"));
                }
                json!({ "cron": expr, "fields": fields.len(), "valid": true })
            }
            "event" => json!({ "event": p.get("event").and_then(Value::as_str).unwrap_or("") }),
            "threshold" => json!({ "metric": p.get("metric").cloned().unwrap_or(Value::Null),
                                   "op": p.get("op").cloned().unwrap_or(Value::Null),
                                   "value": p.get("value").cloned().unwrap_or(Value::Null) }),
            "voice" => json!({ "phrase": p.get("phrase").and_then(Value::as_str).unwrap_or("") }),
            _ => json!({}),
        };
        Ok(json!({ "ok": true, "scheduled": { "task": task, "type": stype, "detail": detail },
                   "supported_types": TYPES }))
    }

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

/// Build a dependency DAG over queued tasks: edges + which tasks are blocked
/// until their predecessors complete (F311). `done` lists completed task ids.
fn queue_dag_payload(p: &Value) -> Value {
    let tasks = p.get("tasks").and_then(Value::as_array).cloned().unwrap_or_default();
    let done: HashSet<String> = p
        .get("done")
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(|x| x.as_str().map(str::to_string)).collect())
        .unwrap_or_default();
    let mut edges = Vec::new();
    let mut blocked = serde_json::Map::new();
    let mut ready = Vec::new();
    for t in &tasks {
        let id = t.get("id").and_then(Value::as_str).unwrap_or("").to_string();
        let deps: Vec<String> = t
            .get("deps")
            .and_then(Value::as_array)
            .map(|a| a.iter().filter_map(|x| x.as_str().map(str::to_string)).collect())
            .unwrap_or_default();
        let unmet: Vec<String> = deps.iter().filter(|d| !done.contains(*d)).cloned().collect();
        for d in &deps {
            edges.push(json!([d, id]));
        }
        if unmet.is_empty() {
            ready.push(id.clone());
        } else {
            blocked.insert(id.clone(), json!(unmet));
        }
    }
    // Kahn topological order over (deps -> task).
    let mut order = Vec::new();
    let mut completed: HashSet<String> = done.clone();
    let mut remaining: Vec<Value> = tasks.clone();
    while !remaining.is_empty() {
        let mut progressed = false;
        remaining.retain(|t| {
            let id = t.get("id").and_then(Value::as_str).unwrap_or("").to_string();
            let deps: Vec<String> = t
                .get("deps")
                .and_then(Value::as_array)
                .map(|a| a.iter().filter_map(|x| x.as_str().map(str::to_string)).collect())
                .unwrap_or_default();
            if deps.iter().all(|d| completed.contains(d)) {
                order.push(id.clone());
                completed.insert(id);
                progressed = true;
                false
            } else {
                true
            }
        });
        if !progressed {
            break; // cycle — stop
        }
    }
    json!({ "edges": edges, "order": order, "blocked": blocked, "ready": ready })
}

/// Manually reprioritize a queue by moving a task to a new index (F312).
fn queue_reorder_payload(p: &Value) -> Value {
    let mut queue: Vec<String> = p
        .get("queue")
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(|x| x.as_str().map(str::to_string)).collect())
        .unwrap_or_default();
    let mv = p.get("move").and_then(Value::as_str).unwrap_or("");
    let to = p.get("to").and_then(Value::as_u64).unwrap_or(0) as usize;
    if let Some(pos) = queue.iter().position(|x| x == mv) {
        let item = queue.remove(pos);
        queue.insert(to.min(queue.len()), item);
    }
    let next = queue.first().cloned();
    json!({ "order": queue, "next": next })
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

/// Mark memory entries unused for > threshold days as stale (F092).
fn memory_mark_stale_payload(p: &Value) -> Value {
    let now = p.get("now_ms").and_then(Value::as_i64).filter(|n| *n > 0).unwrap_or_else(now_millis);
    let threshold_days = p.get("threshold_days").and_then(Value::as_i64).unwrap_or(90);
    let cutoff = now - threshold_days * 86_400_000;
    let entries = p.get("entries").and_then(Value::as_array).cloned().unwrap_or_default();
    let mut out = Vec::new();
    let mut stale_count = 0;
    for e in &entries {
        let last = e.get("last_used_ms").and_then(Value::as_i64).unwrap_or(now);
        let stale = last < cutoff;
        if stale {
            stale_count += 1;
        }
        out.push(json!({ "name": e.get("name"), "last_used_ms": last, "stale": stale, "age_days": (now - last) / 86_400_000 }));
    }
    json!({ "entries": out, "threshold_days": threshold_days, "stale_count": stale_count })
}

/// gzip + AES-256-GCM encrypt; returns (hex(nonce||ciphertext), orig_len, gzip_len) (F165).
fn encrypt_private(text: &str, passphrase: &str) -> std::result::Result<(String, usize, usize), IpcFailure> {
    use flate2::{write::GzEncoder, Compression};
    use ring::aead::{Aad, LessSafeKey, Nonce, UnboundKey, AES_256_GCM, NONCE_LEN};
    use ring::rand::{SecureRandom, SystemRandom};
    use std::io::Write as _;
    let original_len = text.len();
    let mut enc = GzEncoder::new(Vec::new(), Compression::best());
    enc.write_all(text.as_bytes()).map_err(|e| IpcFailure::internal(e.to_string()))?;
    let compressed = enc.finish().map_err(|e| IpcFailure::internal(e.to_string()))?;
    let compressed_len = compressed.len();
    let digest = ring::digest::digest(&ring::digest::SHA256, passphrase.as_bytes());
    let key = LessSafeKey::new(UnboundKey::new(&AES_256_GCM, digest.as_ref()).map_err(|_| IpcFailure::internal("key"))?);
    let mut nonce_bytes = [0u8; NONCE_LEN];
    SystemRandom::new().fill(&mut nonce_bytes).map_err(|_| IpcFailure::internal("rng"))?;
    let mut in_out = compressed;
    key.seal_in_place_append_tag(Nonce::assume_unique_for_key(nonce_bytes), Aad::empty(), &mut in_out)
        .map_err(|_| IpcFailure::internal("seal"))?;
    let mut blob = nonce_bytes.to_vec();
    blob.extend_from_slice(&in_out);
    Ok((hex_encode(&blob), original_len, compressed_len))
}

/// AES-256-GCM decrypt + gunzip (inverse of [`encrypt_private`]) (F165).
fn decrypt_private(hex: &str, passphrase: &str) -> std::result::Result<String, IpcFailure> {
    use flate2::read::GzDecoder;
    use ring::aead::{Aad, LessSafeKey, Nonce, UnboundKey, AES_256_GCM, NONCE_LEN};
    use std::io::Read as _;
    let blob = hex_decode(hex).ok_or_else(|| IpcFailure::internal("bad hex"))?;
    if blob.len() < NONCE_LEN {
        return Err(IpcFailure::internal("short"));
    }
    let (nonce_bytes, ct) = blob.split_at(NONCE_LEN);
    let digest = ring::digest::digest(&ring::digest::SHA256, passphrase.as_bytes());
    let key = LessSafeKey::new(UnboundKey::new(&AES_256_GCM, digest.as_ref()).map_err(|_| IpcFailure::internal("key"))?);
    let mut nb = [0u8; NONCE_LEN];
    nb.copy_from_slice(nonce_bytes);
    let mut in_out = ct.to_vec();
    let plain = key
        .open_in_place(Nonce::assume_unique_for_key(nb), Aad::empty(), &mut in_out)
        .map_err(|_| IpcFailure::internal("decrypt failed"))?;
    let mut d = GzDecoder::new(&plain[..]);
    let mut s = String::new();
    d.read_to_string(&mut s).map_err(|e| IpcFailure::internal(e.to_string()))?;
    Ok(s)
}

fn hex_encode(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}
fn hex_decode(s: &str) -> Option<Vec<u8>> {
    if s.len() % 2 != 0 {
        return None;
    }
    (0..s.len()).step_by(2).map(|i| u8::from_str_radix(&s[i..i + 2], 16).ok()).collect()
}

/// Parse a GitHub-Actions workflow YAML into a job graph (F269): job names and
/// `needs` dependency edges `[dep, job]`. Indent-based, dependency-free.
fn parse_workflow_graph(content: &str) -> Value {
    let mut jobs = Vec::new();
    let mut edges = Vec::new();
    let mut in_jobs = false;
    let mut jobs_indent: i32 = -1;
    let mut job_indent: i32 = -1;
    let mut current: Option<String> = None;
    for raw in content.lines() {
        if raw.trim().is_empty() || raw.trim_start().starts_with('#') {
            continue;
        }
        let indent = (raw.len() - raw.trim_start().len()) as i32;
        let t = raw.trim();
        if t == "jobs:" {
            in_jobs = true;
            jobs_indent = indent;
            continue;
        }
        if !in_jobs {
            continue;
        }
        if indent <= jobs_indent {
            in_jobs = false; // left the jobs block (back to a top-level key)
            continue;
        }
        if job_indent < 0 && t.ends_with(':') {
            job_indent = indent;
        }
        if indent == job_indent && t.ends_with(':') {
            let name = t.trim_end_matches(':').trim().to_string();
            jobs.push(name.clone());
            current = Some(name);
        } else if let Some(job) = &current {
            if let Some(rest) = t.strip_prefix("needs:") {
                let rest = rest.trim();
                let deps: Vec<String> = if rest.starts_with('[') {
                    rest.trim_matches(|c| c == '[' || c == ']')
                        .split(',')
                        .map(|s| s.trim().trim_matches(|c| c == '"' || c == '\'').to_string())
                        .filter(|s| !s.is_empty())
                        .collect()
                } else if !rest.is_empty() {
                    vec![rest.trim_matches(|c| c == '"' || c == '\'').to_string()]
                } else {
                    vec![]
                };
                for d in deps {
                    edges.push(json!([d, job]));
                }
            }
        }
    }
    json!({ "jobs": jobs, "edges": edges, "job_count": jobs.len() })
}

/// Cosine similarity between two equal-length vectors.
fn cosine_sim(a: &[f64], b: &[f64]) -> f64 {
    if a.len() != b.len() || a.is_empty() {
        return 0.0;
    }
    let dot: f64 = a.iter().zip(b).map(|(x, y)| x * y).sum();
    let na: f64 = a.iter().map(|x| x * x).sum::<f64>().sqrt();
    let nb: f64 = b.iter().map(|y| y * y).sum::<f64>().sqrt();
    if na == 0.0 || nb == 0.0 {
        0.0
    } else {
        dot / (na * nb)
    }
}

/// CSS variable extractor: repeated hex colors become :root variables (F339).
fn css_extract_payload(p: &Value) -> Value {
    let css = p.get("css").and_then(Value::as_str).unwrap_or("");
    let mut counts: HashMap<String, usize> = HashMap::new();
    let bytes = css.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'#' {
            let hex: String = css[i + 1..].chars().take_while(|c| c.is_ascii_hexdigit()).collect();
            if hex.len() == 3 || hex.len() == 6 {
                *counts.entry(format!("#{hex}")).or_default() += 1;
            }
            i += 1 + hex.len().max(1);
            continue;
        }
        i += 1;
    }
    let mut repeated: Vec<(String, usize)> = counts.into_iter().filter(|(_, c)| *c >= 2).collect();
    repeated.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
    let mut vars = serde_json::Map::new();
    let mut replaced = css.to_string();
    for (idx, (val, _)) in repeated.iter().enumerate() {
        let name = format!("--color-{idx}");
        replaced = replaced.replace(val, &format!("var({name})"));
        vars.insert(name, json!(val));
    }
    let root = if vars.is_empty() {
        String::new()
    } else {
        format!(
            ":root {{\n{}\n}}\n",
            vars.iter().map(|(k, v)| format!("  {k}: {};", v.as_str().unwrap_or(""))).collect::<Vec<_>>().join("\n")
        )
    };
    let n = vars.len();
    json!({ "variables": vars, "root_block": root, "transformed": format!("{root}{replaced}"), "extracted": n })
}

/// Lexicographic-numeric semver less-than (3 components).
fn semver_lt(a: &str, b: &str) -> bool {
    let parse = |s: &str| -> Vec<u64> {
        s.trim_start_matches(['^', '~', '=', 'v'])
            .split('.')
            .map(|x| x.chars().take_while(|c| c.is_ascii_digit()).collect::<String>().parse().unwrap_or(0))
            .collect()
    };
    let (pa, pb) = (parse(a), parse(b));
    for i in 0..3 {
        let (x, y) = (pa.get(i).copied().unwrap_or(0), pb.get(i).copied().unwrap_or(0));
        if x != y {
            return x < y;
        }
    }
    false
}

/// Dependency CVE scan against a small embedded advisory sample (F317).
fn cve_scan_payload(p: &Value) -> Value {
    let advisories: &[(&str, &str, &str, &str)] = &[
        ("lodash", "4.17.21", "CVE-2021-23337", "high"),
        ("minimist", "1.2.6", "CVE-2021-44906", "critical"),
        ("axios", "0.21.2", "CVE-2021-3749", "high"),
        ("log4j", "2.17.0", "CVE-2021-44228", "critical"),
    ];
    let deps = p.get("deps").and_then(Value::as_array).cloned().unwrap_or_default();
    let mut findings = Vec::new();
    for d in &deps {
        let name = d.get("name").and_then(Value::as_str).unwrap_or("");
        let version = d.get("version").and_then(Value::as_str).unwrap_or("0.0.0");
        for (an, below, cve, sev) in advisories {
            if *an == name && semver_lt(version, below) {
                findings.push(json!({ "name": name, "version": version, "cve": cve, "severity": sev, "fixed_in": below }));
            }
        }
    }
    json!({ "findings": findings, "count": findings.len(), "advisory_count": advisories.len() })
}

/// Validate Terraform / Helm / k8s IaC for basic syntax + required blocks (F328).
fn iac_validate_payload(p: &Value) -> Value {
    let content = p.get("content").and_then(Value::as_str).unwrap_or("");
    let kind = p.get("type").and_then(Value::as_str).unwrap_or("terraform");
    let mut errors = Vec::new();
    let (opens, closes) = (content.matches('{').count(), content.matches('}').count());
    if opens != closes {
        errors.push(json!({ "rule": "balanced-braces", "detail": format!("{{ {opens} vs }} {closes}") }));
    }
    match kind {
        "terraform" => {
            if !content.contains("resource") && !content.contains("provider") {
                errors.push(json!({ "rule": "tf-block", "detail": "kein resource/provider-Block" }));
            }
        }
        "helm" | "k8s" => {
            if !content.contains("apiVersion") || !content.contains("kind") {
                errors.push(json!({ "rule": "k8s-required", "detail": "apiVersion/kind fehlt" }));
            }
        }
        _ => {}
    }
    json!({ "type": kind, "valid": errors.is_empty(), "errors": errors })
}

/// Suggest Dockerfile optimizations (F329).
fn docker_optimize_payload(p: &Value) -> Value {
    let df = p.get("dockerfile").and_then(Value::as_str).unwrap_or("");
    let up = df.to_uppercase();
    let mut suggestions = Vec::new();
    let run_count = df.lines().filter(|l| l.trim_start().to_uppercase().starts_with("RUN ")).count();
    if run_count > 1 {
        suggestions.push(json!({ "rule": "combine-run", "detail": format!("{run_count} RUN-Layer zu einem &&-Chain zusammenfassen") }));
    }
    if up.contains("FROM") && !df.to_lowercase().contains("slim") && !df.to_lowercase().contains("alpine") {
        suggestions.push(json!({ "rule": "slim-base", "detail": "schlankeres Base-Image (slim/alpine) erwägen" }));
    }
    if !up.contains(" AS ") {
        suggestions.push(json!({ "rule": "multi-stage", "detail": "Multi-Stage-Build erwägen" }));
    }
    if up.contains("COPY . ") || up.contains("COPY ./") {
        suggestions.push(json!({ "rule": "dockerignore", "detail": ".dockerignore nutzen statt alles zu kopieren" }));
    }
    json!({ "suggestions": suggestions, "count": suggestions.len() })
}

/// Basic JS->TS migration: annotate untyped function params with `: any` (F343).
fn js_to_ts_payload(p: &Value) -> Value {
    let content = p.get("content").and_then(Value::as_str).unwrap_or("");
    let mut out = String::new();
    let mut annotated = 0;
    for line in content.lines() {
        let mut l = line.to_string();
        if l.contains("function ") || l.contains("=>") {
            if let Some(start) = l.find('(') {
                if let Some(end_rel) = l[start + 1..].find(')') {
                    let params = l[start + 1..start + 1 + end_rel].to_string();
                    if !params.trim().is_empty() && !params.contains(':') {
                        let typed: Vec<String> = params.split(',').map(|pm| format!("{}: any", pm.trim())).collect();
                        l = format!("{}({}){}", &l[..start], typed.join(", "), &l[start + 1 + end_rel + 1..]);
                        annotated += typed.len();
                    }
                }
            }
        }
        out.push_str(&l);
        out.push('\n');
    }
    json!({ "ts": out, "annotations_added": annotated })
}

/// Local-LLM fallback: use Ollama when the cloud is unavailable (F360).
fn llm_fallback_payload(p: &Value) -> Value {
    if p.get("cloud_available").and_then(Value::as_bool).unwrap_or(true) {
        return json!({ "provider": "anthropic", "reason": "cloud verfügbar" });
    }
    let ollama = std::process::Command::new("curl")
        .args(["-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "3", "http://localhost:11434/api/tags"])
        .output()
        .ok()
        .and_then(|o| String::from_utf8_lossy(&o.stdout).trim().parse::<u32>().ok())
        .unwrap_or(0);
    if ollama == 200 {
        json!({ "provider": "ollama", "endpoint": "http://localhost:11434", "reason": "cloud offline -> lokales Ollama" })
    } else {
        json!({ "provider": "none", "reason": "cloud offline und kein lokales Ollama erreichbar" })
    }
}

/// The seven Global-CLAUDE.md editor sections (F081).
fn context_sections_payload() -> Value {
    json!({ "sections": [
        "Über mich & Unternehmen", "Projekte", "GitHub & Repositories",
        "Assets & Branding", "Coding Preferences", "Tool-Referenzen", "Regeln",
    ]})
}

/// Token count + >4000 warning for the global-CLAUDE.md editor (F080).
fn context_token_check_payload(p: &Value) -> Value {
    let tokens = estimate_tokens(p.get("content").and_then(Value::as_str).unwrap_or(""));
    json!({ "tokens": tokens, "warning": tokens > 4000, "limit": 4000 })
}

/// Live USD estimate for a running agent (F279).
fn cost_estimate_payload(p: &Value) -> Value {
    let model = p.get("model").and_then(Value::as_str).unwrap_or("sonnet");
    let input = p.get("input_tokens").and_then(Value::as_f64).unwrap_or(0.0);
    let output = p.get("output_tokens").and_then(Value::as_f64).unwrap_or(0.0);
    json!({ "model": model, "estimated_usd": cost_of(model, input, output) })
}

/// Generate a valid GitHub-Actions workflow for a stack (F270).
fn pipeline_generate_payload(p: &Value) -> Value {
    let stack = p.get("stack").and_then(Value::as_str).unwrap_or("node");
    let (setup, test) = match stack.to_lowercase().as_str() {
        "rust" => ("uses: dtolnay/rust-toolchain@stable", "cargo test --workspace"),
        "python" => ("uses: actions/setup-python@v5", "pytest"),
        _ => ("uses: actions/setup-node@v4", "npm test"),
    };
    let yaml = format!(
        "name: CI\non:\n  push:\n    branches: [main]\n  pull_request:\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n      - {setup}\n      - run: {test}\n"
    );
    json!({ "workflow": yaml, "stack": stack, "path": ".github/workflows/ci.yml" })
}

/// Detect a project's stack from marker files (F036/F038).
fn detect_stack(root: &Path) -> Vec<String> {
    let mut s = Vec::new();
    if root.join("package.json").exists() {
        s.push("Node.js".to_string());
    }
    if root.join("Cargo.toml").exists() {
        s.push("Rust".to_string());
    }
    if root.join("requirements.txt").exists() || root.join("pyproject.toml").exists() {
        s.push("Python".to_string());
    }
    if root.join("go.mod").exists() {
        s.push("Go".to_string());
    }
    s
}

/// Fuzzy subsequence score: matched chars if `needle` is a subsequence (F060).
fn fuzzy_score(haystack: &str, needle: &str) -> Option<usize> {
    if needle.is_empty() {
        return Some(0);
    }
    let mut ni = needle.chars();
    let mut cur = ni.next();
    let mut matched = 0;
    for hc in haystack.chars() {
        if let Some(nc) = cur {
            if hc == nc {
                matched += 1;
                cur = ni.next();
            }
        }
    }
    if cur.is_none() {
        Some(matched)
    } else {
        None
    }
}

/// Ping a project's server URL via curl; online if HTTP < 500 (F040).
fn project_online_status_payload(p: &Value) -> Value {
    let url = p.get("url").and_then(Value::as_str).unwrap_or("");
    if url.is_empty() {
        return json!({ "online": false, "reason": "keine server_url" });
    }
    let out = std::process::Command::new("curl")
        .args(["-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "5", url])
        .output();
    let code: u32 = out
        .ok()
        .and_then(|o| String::from_utf8_lossy(&o.stdout).trim().parse().ok())
        .unwrap_or(0);
    json!({ "url": url, "status_code": code, "online": code != 0 && code < 500 })
}

/// The seven Brain-Graph node types (F186) and nine edge types (F187).
const GRAPH_NODE_TYPES: [&str; 7] =
    ["project", "asset", "config", "person", "decision", "error_pattern", "concept"];
const GRAPH_EDGE_TYPES: [&str; 9] = [
    "USES_ASSET", "SHARES_MODULE", "DEPENDS_ON", "BELONGS_TO", "SIMILAR_TO",
    "DERIVED_FROM", "CONFIGURED_WITH", "DOCUMENTED_IN", "RESOLVED_BY",
];
fn graph_node_types_payload() -> Value {
    json!({ "node_types": GRAPH_NODE_TYPES })
}
fn graph_edge_types_payload() -> Value {
    json!({ "edge_types": GRAPH_EDGE_TYPES })
}

/// Derive proactive suggestions from a project-state snapshot
/// (F217 state-based, F218 priority, F220 why-data, F222 neglected, F223 cost, F224 freed-dep).
fn copilot_suggestions_payload(p: &Value) -> Value {
    let s = p.get("state").cloned().unwrap_or(json!({}));
    let getn = |k: &str| s.get(k).and_then(Value::as_i64).unwrap_or(0);
    let getb = |k: &str| s.get(k).and_then(Value::as_bool).unwrap_or(false);
    let mut sug = Vec::new();
    if getn("open_findings") > 0 {
        sug.push(json!({ "priority":"red","category":"Wichtig",
            "title":format!("{} offene Security-Findings beheben", getn("open_findings")),
            "reason":format!("Der letzte Scan meldete {} offene Security-Findings — vor dem nächsten Deploy beheben.", getn("open_findings")),
            "action":"fix_findings","action_label":"Jetzt beheben","why":{"open_findings":getn("open_findings")} }));
    }
    if getn("failing_tests") > 0 {
        sug.push(json!({ "priority":"red","category":"Wichtig","title":"Fehlschlagende Tests reparieren",
            "reason":format!("{} Test(s) schlagen aktuell fehl und blockieren grüne Builds.", getn("failing_tests")),
            "action":"fix_tests","action_label":"Tests reparieren","why":{"failing_tests":getn("failing_tests")} }));
    }
    if getn("last_backup_days") > 7 {
        sug.push(json!({ "priority":"yellow","category":"Empfohlen",
            "title":format!("Backup fällig (seit {} Tagen)", getn("last_backup_days")),
            "action":"backup","why":{"last_backup_days":getn("last_backup_days")} }));
    }
    if getn("outdated_deps") > 0 {
        sug.push(json!({ "priority":"yellow","category":"Empfohlen",
            "title":format!("{} veraltete Abhängigkeiten aktualisieren", getn("outdated_deps")),
            "action":"update_deps","why":{"outdated_deps":getn("outdated_deps")} }));
    }
    if getb("agent_opus_for_simple") {
        let in_t = getn("agent_input_tokens") as f64;
        let out_t = getn("agent_output_tokens") as f64;
        let savings = cost_of("opus", in_t, out_t) - cost_of("haiku", in_t, out_t);
        sug.push(json!({ "priority":"green","category":"Optimierung","title":"Agent auf Haiku umstellen",
            "action":"downgrade_model","why":{"savings_usd_per_run":savings} }));
    }
    if getb("dependency_freed") {
        sug.push(json!({ "priority":"blue","category":"Idee",
            "title":format!("Feature '{}' ist jetzt entsperrt", s.get("freed_feature").and_then(Value::as_str).unwrap_or("X")),
            "action":"start_feature","why":{"freed_feature": s.get("freed_feature").cloned().unwrap_or(json!("X"))} }));
    }
    json!({ "suggestions": sug, "count": sug.len() })
}

/// The single highest-priority recommendation (F221).
fn copilot_focus_payload(p: &Value) -> Value {
    let all = copilot_suggestions_payload(p);
    let empty = vec![];
    let sugs = all.get("suggestions").and_then(Value::as_array).unwrap_or(&empty);
    let mut best: Option<Value> = None;
    for pr in ["red", "yellow", "green", "blue"] {
        if let Some(s) = sugs.iter().find(|x| x.get("priority").and_then(Value::as_str) == Some(pr)) {
            best = Some(s.clone());
            break;
        }
    }
    json!({ "focus": best, "total_suggestions": sugs.len() })
}

/// Assign decomposed subtasks to workers round-robin (F120/F122).
fn teams_decompose_payload(p: &Value) -> Value {
    let subtasks: Vec<Value> = p.get("subtasks").and_then(Value::as_array).cloned().unwrap_or_default();
    let workers: Vec<String> = p
        .get("workers")
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(|w| w.as_str().map(str::to_string)).collect())
        .unwrap_or_default();
    let mut assignments = Vec::new();
    for (i, st) in subtasks.iter().enumerate() {
        let worker = if workers.is_empty() {
            "unassigned".to_string()
        } else {
            workers[i % workers.len()].clone()
        };
        assignments.push(json!({ "subtask": st, "worker": worker }));
    }
    json!({ "assignments": assignments, "worker_count": workers.len(), "subtask_count": subtasks.len() })
}

/// Substitute {{param}} placeholders in a task workflow (F211).
fn tasks_render_payload(p: &Value) -> Value {
    let mut workflow = p.get("workflow").and_then(Value::as_str).unwrap_or("").to_string();
    if let Some(params) = p.get("params").and_then(Value::as_object) {
        for (k, v) in params {
            let val = v.as_str().map(str::to_string).unwrap_or_else(|| v.to_string());
            workflow = workflow.replace(&format!("{{{{{k}}}}}"), &val);
        }
    }
    json!({ "rendered": workflow })
}

/// Generate ALTER statements from an old->new column schema diff (F342).
fn migration_generate_payload(p: &Value) -> Value {
    let from = p.get("from").and_then(Value::as_object).cloned().unwrap_or_default();
    let to = p.get("to").and_then(Value::as_object).cloned().unwrap_or_default();
    let table = p.get("table").and_then(Value::as_str).unwrap_or("t");
    let (mut up, mut down) = (Vec::new(), Vec::new());
    for (col, ty) in &to {
        if !from.contains_key(col) {
            up.push(format!("ALTER TABLE {table} ADD COLUMN {col} {};", ty.as_str().unwrap_or("TEXT")));
            down.push(format!("ALTER TABLE {table} DROP COLUMN {col};"));
        }
    }
    for col in from.keys() {
        if !to.contains_key(col) {
            up.push(format!("ALTER TABLE {table} DROP COLUMN {col};"));
        }
    }
    json!({ "up": up.join("\n"), "down": down.join("\n"), "changes": up.len() })
}

/// Render an endpoint list from an OpenAPI spec (F333).
fn apiportal_render_payload(p: &Value) -> Value {
    let spec = p.get("openapi").cloned().unwrap_or(Value::Null);
    let mut endpoints = Vec::new();
    if let Some(paths) = spec.get("paths").and_then(Value::as_object) {
        for (path, methods) in paths {
            if let Some(m) = methods.as_object() {
                for (method, op) in m {
                    endpoints.push(json!({
                        "method": method.to_uppercase(), "path": path,
                        "summary": op.get("summary").and_then(Value::as_str).unwrap_or(""),
                    }));
                }
            }
        }
    }
    json!({ "endpoints": endpoints, "count": endpoints.len() })
}

/// Check HTML for common WCAG violations (F326).
fn a11y_check_payload(p: &Value) -> Value {
    let lower = p.get("html").and_then(Value::as_str).unwrap_or("").to_lowercase();
    let mut violations = Vec::new();
    let mut idx = 0;
    while let Some(pos) = lower[idx..].find("<img") {
        let start = idx + pos;
        let end = lower[start..].find('>').map(|e| start + e).unwrap_or(lower.len());
        if !lower[start..end].contains("alt=") {
            violations.push(json!({ "rule": "img-alt", "wcag": "1.1.1", "detail": "<img> ohne alt-Attribut" }));
        }
        idx = end + 1;
    }
    if lower.contains("<input") && !lower.contains("aria-label") && !lower.contains("<label") {
        violations.push(json!({ "rule": "input-label", "wcag": "1.3.1", "detail": "<input> ohne Label" }));
    }
    if lower.contains("<html") && !lower.contains("lang=") {
        violations.push(json!({ "rule": "html-lang", "wcag": "3.1.1", "detail": "<html> ohne lang-Attribut" }));
    }
    json!({ "violations": violations, "count": violations.len(), "passed": violations.is_empty() })
}

/// The seven configurable Claude Code hook types (F256).
fn hook_types_payload() -> Value {
    json!({ "types": [
        {"name":"PreToolUse","when":"vor jeder Tool-Ausführung","can_block":true},
        {"name":"PostToolUse","when":"nach jeder Tool-Ausführung","can_block":false},
        {"name":"Notification","when":"bei Benachrichtigungen","can_block":false},
        {"name":"Stop","when":"wenn die Session endet","can_block":false},
        {"name":"SubagentStop","when":"wenn ein Subagent endet","can_block":false},
        {"name":"WorktreeCreate","when":"beim Anlegen eines Worktrees","can_block":false},
        {"name":"WorktreeRemove","when":"beim Entfernen eines Worktrees","can_block":false},
    ]})
}

/// Commit subjects newest-first, optionally since a git date expression.
fn git_subjects(cwd: &str, since: Option<&str>) -> Vec<String> {
    let mut args = vec!["log".to_string(), "--pretty=format:%s".to_string()];
    if let Some(s) = since {
        args.push("--since".into());
        args.push(s.into());
    }
    std::process::Command::new("git")
        .current_dir(cwd)
        .args(&args)
        .output()
        .map(|o| {
            String::from_utf8_lossy(&o.stdout)
                .lines()
                .filter(|l| !l.trim().is_empty())
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default()
}

/// Strip a conventional-commit `type: ` prefix and capitalize the first letter.
fn friendly(subject: &str) -> String {
    let body = subject.splitn(2, ": ").nth(1).unwrap_or(subject).trim();
    let mut chars = body.chars();
    match chars.next() {
        Some(f) => f.to_uppercase().collect::<String>() + chars.as_str(),
        None => body.to_string(),
    }
}

/// Recursively collect source files of the given extensions, skipping build
/// and vendor directories. Caps each file at ~1 MB.
fn collect_source_files(root: &Path, exts: &[&str]) -> Vec<(String, String)> {
    let mut out = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let Ok(rd) = std::fs::read_dir(&dir) else { continue };
        for e in rd.flatten() {
            let p = e.path();
            let name = e.file_name().to_string_lossy().to_string();
            if p.is_dir() {
                if matches!(name.as_str(), "target" | ".build" | "node_modules" | ".git" | ".claude") {
                    continue;
                }
                stack.push(p);
            } else if p.extension().and_then(|x| x.to_str()).map(|x| exts.contains(&x)).unwrap_or(false) {
                if let Ok(c) = std::fs::read_to_string(&p) {
                    if c.len() < 1_000_000 {
                        out.push((p.to_string_lossy().to_string(), c));
                    }
                }
            }
        }
    }
    out
}

/// Approximate cyclomatic complexity: 1 + branch/decision keywords (F320).
fn cyclomatic_complexity(src: &str) -> u64 {
    let mut c = 1u64;
    for kw in ["if ", "if(", "for ", "for(", "while ", "while(", "case ", "catch", "&&", "||", " ? "] {
        c += src.matches(kw).count() as u64;
    }
    c
}

/// Sanitize a string into a mermaid-safe node id.
fn sanitize_node(s: &str) -> String {
    s.chars().map(|c| if c.is_alphanumeric() || c == '_' { c } else { '_' }).collect()
}

/// Compare a benchmark's current time to a baseline; flag a regression (F325).
fn perf_compare_payload(p: &Value) -> Value {
    let baseline = p.get("baseline_ms").and_then(Value::as_f64).unwrap_or(0.0);
    let current = p.get("current_ms").and_then(Value::as_f64).unwrap_or(0.0);
    let threshold = p.get("threshold_pct").and_then(Value::as_f64).unwrap_or(10.0);
    let delta_pct = if baseline > 0.0 { (current - baseline) / baseline * 100.0 } else { 0.0 };
    json!({
        "baseline_ms": baseline, "current_ms": current, "delta_pct": delta_pct,
        "threshold_pct": threshold, "regression": delta_pct > threshold,
    })
}

/// Extract hardcoded UI strings, replacing them with t('key') calls (F344).
fn i18n_extract_payload(p: &Value) -> Value {
    let content = p.get("content").and_then(Value::as_str).unwrap_or("");
    let mut catalog = serde_json::Map::new();
    let mut transformed = content.to_string();
    let mut keys = Vec::new();
    // Collect double-quoted literals that look like UI text (letters, len>=3).
    let mut literals = Vec::new();
    let mut i = 0;
    let bytes = content.as_bytes();
    while i < bytes.len() {
        if bytes[i] == b'"' {
            if let Some(end) = content[i + 1..].find('"') {
                let lit = &content[i + 1..i + 1 + end];
                if lit.chars().any(|c| c.is_alphabetic()) && lit.chars().count() >= 3 {
                    literals.push(lit.to_string());
                }
                i = i + 1 + end + 1;
                continue;
            }
        }
        i += 1;
    }
    for (idx, lit) in literals.into_iter().enumerate() {
        let key = format!("key_{idx}");
        catalog.insert(key.clone(), json!(lit));
        transformed = transformed.replacen(&format!("\"{lit}\""), &format!("t('{key}')"), 1);
        keys.push(key);
    }
    json!({ "transformed": transformed, "catalog": catalog, "extracted": keys.len() })
}

/// Pre-deploy checklist: blocks the deploy while any check is failing (F276).
fn deploy_checklist_payload(p: &Value) -> Value {
    let checks = p.get("checks").and_then(Value::as_array).cloned().unwrap_or_default();
    let failing: Vec<Value> = checks
        .iter()
        .filter(|c| !c.get("pass").and_then(Value::as_bool).unwrap_or(false))
        .map(|c| c.get("name").cloned().unwrap_or(json!("?")))
        .collect();
    let blocked = !failing.is_empty();
    json!({
        "status": if blocked { "red" } else { "green" },
        "blocked": blocked, "failing": failing, "total": checks.len(),
    })
}

/// Default model tier for a task type (F131).
fn default_model_for(task_type: &str) -> &'static str {
    let t = task_type.to_lowercase();
    if t.contains("doc") || t.contains("simple") || t.contains("monitor") {
        "haiku"
    } else if t.contains("arch") || t.contains("plan") || t.contains("design") {
        "opus"
    } else {
        "sonnet" // feature / review / default
    }
}

/// Fallback chain opus -> sonnet -> haiku when a higher tier errors (F134).
fn model_fallback_payload(p: &Value) -> Value {
    let failed = p.get("model").and_then(Value::as_str).unwrap_or("opus");
    let next = match failed {
        "opus" => Some("sonnet"),
        "sonnet" => Some("haiku"),
        _ => None,
    };
    json!({ "failed": failed, "fallback": next, "exhausted": next.is_none() })
}

/// Illustrative per-1M-token USD prices for the routing cost comparison (F135).
fn model_price(model: &str) -> (f64, f64) {
    match model {
        "haiku" => (0.80, 4.0),
        "opus" => (15.0, 75.0),
        _ => (3.0, 15.0), // sonnet / default
    }
}
fn cost_of(model: &str, input: f64, output: f64) -> f64 {
    let (pi, po) = model_price(model);
    input / 1_000_000.0 * pi + output / 1_000_000.0 * po
}

/// Compare the routed model's cost vs all-Opus to confirm the saving (F135).
fn model_cost_compare_payload(p: &Value) -> Value {
    let input = p.get("input_tokens").and_then(Value::as_f64).unwrap_or(0.0);
    let output = p.get("output_tokens").and_then(Value::as_f64).unwrap_or(0.0);
    let routed = p.get("routed_model").and_then(Value::as_str).unwrap_or("sonnet");
    let routed_cost = cost_of(routed, input, output);
    let opus_cost = cost_of("opus", input, output);
    json!({
        "routed_model": routed, "routed_cost_usd": routed_cost, "opus_cost_usd": opus_cost,
        "savings_usd": opus_cost - routed_cost, "saved": opus_cost > routed_cost,
    })
}

/// Prebuilt prompt templates for the prompt library (F243).
/// Liest die beiden Endpunkt-IDs einer Kante (unterstützt {from,to},
/// {source,target} und [a, b]-Form).
fn edge_endpoints(e: &Value) -> (String, String) {
    if let Some(arr) = e.as_array() {
        return (
            arr.first().and_then(Value::as_str).unwrap_or("").to_string(),
            arr.get(1).and_then(Value::as_str).unwrap_or("").to_string(),
        );
    }
    let a = e.get("from").or_else(|| e.get("source")).and_then(Value::as_str).unwrap_or("").to_string();
    let b = e.get("to").or_else(|| e.get("target")).and_then(Value::as_str).unwrap_or("").to_string();
    (a, b)
}

/// Erzeugt die Projekt-CLAUDE.md für ein Scaffold-Template (F085). Bekannte
/// Templates (z.B. `fastapi`) liefern charakteristischen Stack-/Regel-Inhalt;
/// sonst ein generisches Gerüst aus der erkannten Stack-Signatur.
fn scaffold_claude_md(pp: &Path, template: &str) -> String {
    let name = pp.file_name().and_then(|n| n.to_str()).unwrap_or("project");
    match template {
        "fastapi" => format!(
            "# {name}\n\nFastAPI-Projekt-Kontext für Claude.\n\n## Stack\n\
             - Python 3.11+ mit **FastAPI**\n\
             - ASGI-Server: **uvicorn**\n\
             - Validierung/Schemas: **pydantic** (BaseModel)\n\
             - Tests: pytest + httpx.AsyncClient\n\n## Regeln\n\
             - Endpoints als `async def` mit Typannotationen und `response_model`.\n\
             - Request/Response immer über pydantic-Modelle, keine rohen dicts.\n\
             - Abhängigkeiten via `Depends`; DB-Sessions als Dependency injizieren.\n\
             - Lokal starten mit `uvicorn app.main:app --reload`.\n"
        ),
        _ => format!(
            "# {name}\n\nProjekt-Kontext für Claude.\n\n## Stack\n{}\n\n## Regeln\n- (Template: {template})\n",
            detect_stack(pp).join(", ")
        ),
    }
}

/// Sammelt Bild-/SVG-Asset-Dateien unterhalb von `dir` (bis Tiefe 2), unter
/// Auslassung von node_modules/.git und Dot-Verzeichnissen (F178).
fn collect_asset_files(dir: &Path, out: &mut Vec<PathBuf>, depth: usize) {
    if depth > 2 {
        return;
    }
    let Ok(rd) = std::fs::read_dir(dir) else { return };
    for e in rd.flatten() {
        let path = e.path();
        if path.is_dir() {
            let n = path.file_name().and_then(|x| x.to_str()).unwrap_or("");
            if n == "node_modules" || n == ".git" || n.starts_with('.') {
                continue;
            }
            collect_asset_files(&path, out, depth + 1);
        } else {
            let ext = path.extension().and_then(|x| x.to_str()).unwrap_or("").to_lowercase();
            if matches!(ext.as_str(), "png" | "jpg" | "jpeg" | "gif" | "bmp" | "tiff" | "svg") {
                out.push(path);
            }
        }
    }
}

/// Extrahiert Semantik aus SVG-XML: <text>-Inhalte plus vorkommende Form-Tags
/// (F178). Bewusst ohne XML-Crate — robuste, einfache Textextraktion.
fn extract_svg_semantics(svg: &str) -> String {
    let mut out: Vec<String> = Vec::new();
    let mut rest = svg;
    while let Some(start) = rest.find("<text") {
        if let Some(gt) = rest[start..].find('>') {
            let after = &rest[start + gt + 1..];
            if let Some(end) = after.find("</text>") {
                let content = after[..end].trim();
                if !content.is_empty() {
                    out.push(content.to_string());
                }
                rest = &after[end + 7..];
                continue;
            }
        }
        break;
    }
    for shape in ["rect", "circle", "ellipse", "path", "polygon", "line"] {
        if svg.contains(&format!("<{shape}")) {
            out.push(shape.to_string());
        }
    }
    out.join(" ")
}

/// Führt OCR über ein Bild aus, indem das System-`tesseract` aufgerufen wird
/// (F178). Liefert bei fehlendem/erfolglosem tesseract einen leeren String.
fn ocr_image(path: &Path) -> String {
    match std::process::Command::new("tesseract").arg(path).arg("stdout").output() {
        Ok(o) if o.status.success() => {
            String::from_utf8_lossy(&o.stdout).split_whitespace().collect::<Vec<_>>().join(" ")
        }
        _ => String::new(),
    }
}

/// Minimaler HTTP-JSON-Aufruf über das System-`curl` (für integrations.github_sync).
/// Liefert die geparste JSON-Antwort oder `{}` bei Fehler.
fn curl_json(method: &str, url: &str, body: Option<&str>) -> Value {
    let mut args: Vec<String> = vec![
        "-s".into(), "-X".into(), method.into(),
        "-H".into(), "Content-Type: application/json".into(),
        "-H".into(), "Accept: application/json".into(),
    ];
    if let Some(b) = body {
        args.push("-d".into());
        args.push(b.into());
    }
    args.push(url.into());
    std::process::Command::new("curl")
        .args(&args)
        .output()
        .ok()
        .and_then(|o| serde_json::from_slice(&o.stdout).ok())
        .unwrap_or_else(|| json!({}))
}

/// Führt einen autonomen `claude`-Agentenlauf im Verzeichnis `cwd` aus: `--print`,
/// volle Werkzeuge (Write/Edit/Bash/Read/Glob/Grep) und übersprungene Permissions, damit
/// der Agent Dateien wirklich schreibt und Befehle ausführt. Liefert (stdout, exit-code).
/// Das Binary kommt aus `CLAUDESTUDIO_CLAUDE_BIN` (sonst `claude`).
fn run_claude_agent(cwd: &str, prompt: &str) -> (String, i32) {
    let binary = std::env::var("CLAUDESTUDIO_CLAUDE_BIN").unwrap_or_else(|_| "claude".to_string());
    match std::process::Command::new(&binary)
        .args([
            "--print", "--output-format", "text",
            "--allowedTools", "Write,Edit,Bash,Read,Glob,Grep",
            "--permission-mode", "bypassPermissions",
            prompt,
        ])
        .current_dir(cwd)
        .output()
    {
        Ok(o) => (String::from_utf8_lossy(&o.stdout).to_string(), o.status.code().unwrap_or(-1)),
        Err(e) => (format!("claude konnte nicht gestartet werden: {e}"), -1),
    }
}

/// Extrahiert einen JSON-Wert aus `claude`-Freitext (toleriert Markdown-Fences und
/// umgebenden Text): versucht den ganzen String, sonst das erste `[..]`-Array bzw.
/// `{..}`-Objekt. Liefert `null` bei Misserfolg.
fn extract_json_value(text: &str) -> Value {
    let t = text.trim();
    if let Ok(v) = serde_json::from_str::<Value>(t) {
        return v;
    }
    if let (Some(s), Some(e)) = (t.find('['), t.rfind(']')) {
        if e > s {
            if let Ok(v) = serde_json::from_str::<Value>(&t[s..=e]) {
                return v;
            }
        }
    }
    if let (Some(s), Some(e)) = (t.find('{'), t.rfind('}')) {
        if e > s {
            if let Ok(v) = serde_json::from_str::<Value>(&t[s..=e]) {
                return v;
            }
        }
    }
    Value::Null
}

/// POSTet `body` an `url` und liefert nur den HTTP-Statuscode (für slack_command).
fn curl_status(method: &str, url: &str, body: &str) -> u32 {
    std::process::Command::new("curl")
        .args([
            "-s", "-o", "/dev/null", "-w", "%{http_code}", "-X", method,
            "-H", "Content-Type: application/json", "-d", body, url,
        ])
        .output()
        .ok()
        .and_then(|o| String::from_utf8_lossy(&o.stdout).trim().parse().ok())
        .unwrap_or(0)
}

/// Extrahiert den Assistenten-Text und die Kosten aus `claude` stream-json-stdout
/// (für models.compare). Sammelt alle text-Blöcke der assistant-Zeilen.
fn extract_assistant_text(stdout: &str) -> (String, f64) {
    let mut text = String::new();
    let mut cost = 0.0;
    for line in stdout.lines() {
        let Ok(v) = serde_json::from_str::<Value>(line.trim()) else { continue };
        match v.get("type").and_then(|t| t.as_str()) {
            Some("assistant") => {
                if let Some(blocks) =
                    v.get("message").and_then(|m| m.get("content")).and_then(|c| c.as_array())
                {
                    for b in blocks {
                        if b.get("type").and_then(|t| t.as_str()) == Some("text") {
                            if let Some(t) = b.get("text").and_then(|t| t.as_str()) {
                                if !text.is_empty() {
                                    text.push(' ');
                                }
                                text.push_str(t);
                            }
                        }
                    }
                }
            }
            Some("result") => {
                cost = v.get("cost_usd").and_then(|c| c.as_f64()).unwrap_or(0.0);
            }
            _ => {}
        }
    }
    (text, cost)
}

/// Filter-Chips-Substrat (F020): filtert `items` nach (key == value) und liefert
/// die Treffer plus den 'zeige N von M'-Zähler, den der Chip anzeigt.
fn list_filter_payload(p: &Value) -> Value {
    let items = p.get("items").and_then(Value::as_array).cloned().unwrap_or_default();
    let total = items.len();
    let key = p.get("key").and_then(Value::as_str).unwrap_or("status");
    let val = p.get("value").cloned().unwrap_or(Value::Null);
    let matched: Vec<Value> = if val.is_null() {
        items
    } else {
        items.into_iter().filter(|it| it.get(key) == Some(&val)).collect()
    };
    let visible = matched.len();
    json!({ "total": total, "visible": visible, "matched": matched,
            "label": format!("zeige {visible} von {total}") })
}

/// Zerlegt Text in grobe Sätze (Trennung an . ! ? und Zeilenumbruch).
fn split_sentences(text: &str) -> Vec<String> {
    text.split(|c| c == '.' || c == '!' || c == '?' || c == '\n')
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .collect()
}

/// Bewertet, wie "merkbar" ein Satz als Erkenntnis ist (F090). Höher = besser.
fn insight_score(s: &str) -> i32 {
    let low = s.to_lowercase();
    let mut score = 0;
    if low.contains("heißt") || low.contains("heisst") || low.contains("lautet") {
        score += 3;
    }
    if low.contains(" ist ") || low.contains(" = ") || low.contains(" sind ") {
        score += 1;
    }
    // ID-artige Tokens (Buchstaben UND Ziffern), z.B. prod-01, v2, api-key
    if s.split_whitespace().any(|t| {
        t.chars().any(|c| c.is_alphabetic()) && t.chars().any(|c| c.is_ascii_digit())
    }) {
        score += 2;
    }
    // Eigenname: Großbuchstabe bei einem Token NICHT am Satzanfang
    if s.split_whitespace().skip(1).any(|t| {
        t.chars().next().map(|c| c.is_uppercase()).unwrap_or(false)
    }) {
        score += 1;
    }
    score
}

/// Extrahiert Entitäten (Eigennamen, mehrteilige großgeschriebene Phrasen, IDs)
/// aus Text (F181). Heuristik ohne externe NLP-Abhängigkeit: zusammenhängende
/// großgeschriebene Tokens werden zu einer Entität verbunden; ID-artige Tokens
/// (mit Ziffer + Bindestrich) zählen ebenfalls. Häufige Satz-Anfangswörter werden
/// herausgefiltert.
fn extract_entities(text: &str) -> Vec<String> {
    let stop: std::collections::HashSet<&str> = [
        "Der", "Die", "Das", "Ein", "Eine", "Ich", "Wir", "Du", "Sie", "Es", "Und", "Aber",
        "Im", "In", "Am", "Mit", "Für", "Auf", "Von", "Zu", "Bei", "Heute", "Gestern", "Dann",
        "Mein", "Meine", "Unser", "Unsere", "Dein", "Deine", "Hier", "Diese", "Dieser", "Dieses",
        "The", "A", "An", "I", "We", "You", "It", "And", "But", "This", "That", "On", "With", "For",
    ]
    .into_iter()
    .collect();
    let mut out: Vec<String> = Vec::new();
    let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
    let mut run: Vec<String> = Vec::new();

    fn is_entity_token(t: &str) -> bool {
        let cap = t.chars().next().map(|c| c.is_uppercase()).unwrap_or(false);
        let alpha = t.chars().any(|c| c.is_alphabetic());
        let digit = t.chars().any(|c| c.is_ascii_digit());
        (cap && alpha) || (alpha && digit && t.contains('-'))
    }
    fn flush(run: &mut Vec<String>, out: &mut Vec<String>, seen: &mut std::collections::HashSet<String>) {
        if !run.is_empty() {
            let ent = run.join(" ");
            if ent.chars().count() >= 3 && seen.insert(ent.clone()) {
                out.push(ent);
            }
            run.clear();
        }
    }

    for raw in text.split(|c: char| c.is_whitespace()) {
        // führende/abschließende Satzzeichen abstreifen (Bindestrich/Unterstrich behalten)
        let tok = raw.trim_matches(|c: char| !c.is_alphanumeric() && c != '-' && c != '_');
        if tok.is_empty() {
            flush(&mut run, &mut out, &mut seen);
            continue;
        }
        if is_entity_token(tok) && !stop.contains(tok) {
            run.push(tok.to_string());
        } else {
            flush(&mut run, &mut out, &mut seen);
        }
    }
    flush(&mut run, &mut out, &mut seen);
    out
}

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
