#![forbid(unsafe_code)]
//! `cs-sessions` — the permanent session archive for ClaudeStudio.
//!
//! Every Claude Code session, message, tool invocation, file diff and lifecycle
//! event is durably recorded in a local SQLite database (built with the bundled
//! SQLite amalgamation, so no system library is required). A full-text search
//! index (FTS5) is maintained over all transcript text so past work can be
//! recalled instantly.
//!
//! The public entry point is [`SessionStore`]. Open an on-disk archive with
//! [`SessionStore::open`] or an ephemeral one with [`SessionStore::open_in_memory`].
//!
//! All timestamps are stored as `i64` Unix epoch **milliseconds** to avoid a
//! `chrono` dependency; callers may pass [`now_millis`] for "now".
//!
//! ```
//! use cs_sessions::{SessionStore, NewSession, NewMessage};
//!
//! let store = SessionStore::open_in_memory().unwrap();
//! let sid = store
//!     .insert_session(&NewSession::new("Refactor the parser", "/repo"))
//!     .unwrap();
//! store
//!     .append_message(&NewMessage::new(&sid, "user", "please fix the parser bug"))
//!     .unwrap();
//! let hits = store.full_text_search("parser bug", 10).unwrap();
//! assert_eq!(hits.len(), 1);
//! ```

use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};

/// Errors produced by the session archive.
#[derive(Debug, thiserror::Error)]
pub enum Error {
    /// A failure originating from the underlying SQLite engine.
    #[error("sqlite error: {0}")]
    Sqlite(#[from] rusqlite::Error),
    /// A (de)serialization failure for a JSON-encoded column.
    #[error("serialization error: {0}")]
    Serde(#[from] serde_json::Error),
    /// The requested entity could not be found.
    #[error("not found: {0}")]
    NotFound(String),
}

/// Convenience result alias used throughout this crate.
pub type Result<T> = std::result::Result<T, Error>;

/// Returns the current time as Unix epoch milliseconds.
#[must_use]
pub fn now_millis() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as i64)
        .unwrap_or(0)
}

fn new_id() -> String {
    uuid::Uuid::new_v4().to_string()
}

/// Upper bound on a stored tool output. Tool results (file reads, command logs)
/// can be huge; we keep the archive useful without letting one result bloat the
/// DB. This caps *storage* only — search never returns raw output regardless.
const MAX_TOOL_OUTPUT: usize = 200_000;

/// Clamp a possibly-huge string to [`MAX_TOOL_OUTPUT`] chars, marking truncation.
fn clamp_output(s: &str) -> String {
    if s.len() <= MAX_TOOL_OUTPUT {
        return s.to_string();
    }
    let mut out: String = s.chars().take(MAX_TOOL_OUTPUT).collect();
    out.push_str("\n…[truncated]");
    out
}

/// Build the text that represents a tool invocation for semantic embedding:
/// the tool name, its input, and (if known) its output. Identical input is fed
/// to the embedder for both the inline and backfill paths.
fn tool_embed_text(tool_name: &str, input: &str, output: Option<&str>) -> String {
    match output {
        Some(o) => format!("{tool_name}\n{input}\n{o}"),
        None => format!("{tool_name}\n{input}"),
    }
}

// TODO(A14 — keep cs-sessions transcript-only): this crate currently owns the
// vector layer too — the `encode_vec`/`decode_vec`/`cosine` helpers below plus
// `upsert_embedding`, `unembedded_messages`, `unembedded_tool_calls`,
// `vector_search`, and the `embeddings` table. Per ARCHITECTURE.md, semantic
// memory belongs in `cs-vector`. The intended extraction is a `VectorIndex`
// trait in `cs-vector` (open/upsert/search/pending), with a SQLite-backed impl,
// that `cs-sessions` *composes* rather than reimplements — leaving this crate
// responsible only for the durable transcript (sessions/messages/tool_calls/
// events/FTS). Deferred here because the embeddings table is currently joined
// to the transcript tables in the same SQLite connection, so the split needs a
// schema/ownership migration that is too large to land safely in this pass.

/// Encode an embedding vector as little-endian f32 bytes for BLOB storage.
fn encode_vec(v: &[f32]) -> Vec<u8> {
    let mut out = Vec::with_capacity(v.len() * 4);
    for x in v {
        out.extend_from_slice(&x.to_le_bytes());
    }
    out
}

/// Decode a little-endian f32 BLOB back into an embedding vector.
fn decode_vec(bytes: &[u8]) -> Vec<f32> {
    bytes
        .chunks_exact(4)
        .map(|c| f32::from_le_bytes([c[0], c[1], c[2], c[3]]))
        .collect()
}

/// Cosine similarity of two equal-length vectors; `0.0` on length mismatch or a
/// zero vector. Stored embeddings are pre-normalized, but we divide by the
/// norms anyway so this stays correct regardless.
fn cosine(a: &[f32], b: &[f32]) -> f32 {
    if a.len() != b.len() {
        return 0.0;
    }
    let mut dot = 0f32;
    let mut na = 0f32;
    let mut nb = 0f32;
    for i in 0..a.len() {
        dot += a[i] * b[i];
        na += a[i] * a[i];
        nb += b[i] * b[i];
    }
    let denom = na.sqrt() * nb.sqrt();
    if denom == 0.0 {
        0.0
    } else {
        dot / denom
    }
}

/// Data required to create a new session row.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NewSession {
    /// Human-readable title / first prompt summary.
    pub title: String,
    /// Working directory / project root for the session.
    pub cwd: String,
    /// Optional git branch the session ran against.
    pub branch: Option<String>,
    /// Model tier label (e.g. "opus", "sonnet"). Stored as a free-form string
    /// so this crate need not depend on the exact enum representation.
    pub model: Option<String>,
    /// Creation timestamp in Unix epoch milliseconds.
    pub created_at: i64,
}

impl NewSession {
    /// Build a new session with `title` and `cwd`, defaulting the rest.
    #[must_use]
    pub fn new(title: impl Into<String>, cwd: impl Into<String>) -> Self {
        Self {
            title: title.into(),
            cwd: cwd.into(),
            branch: None,
            model: None,
            created_at: now_millis(),
        }
    }

    /// Attach a git branch to the session being created.
    #[must_use]
    pub fn with_branch(mut self, branch: impl Into<String>) -> Self {
        self.branch = Some(branch.into());
        self
    }

    /// Attach a model label to the session being created.
    #[must_use]
    pub fn with_model(mut self, model: impl Into<String>) -> Self {
        self.model = Some(model.into());
        self
    }
}

/// A persisted session, as read back from the archive.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Session {
    /// Stable unique identifier (UUID v4).
    pub id: String,
    /// Human-readable title.
    pub title: String,
    /// Working directory / project root.
    pub cwd: String,
    /// Git branch, if recorded.
    pub branch: Option<String>,
    /// Model label, if recorded.
    pub model: Option<String>,
    /// Creation timestamp (Unix millis).
    pub created_at: i64,
    /// Timestamp of the most recent activity (Unix millis).
    pub updated_at: i64,
    /// The `claude` CLI's own session id for this run, if captured — lets the
    /// archive continue the conversation via `--resume`.
    pub claude_session_id: Option<String>,
}

/// One stored transcript message (for replaying an archived conversation).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TranscriptMessage {
    /// `user` or `assistant`.
    pub role: String,
    pub content: String,
    pub created_at: i64,
}

/// Data required to append a message to a session transcript.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NewMessage {
    /// Owning session id.
    pub session_id: String,
    /// Role of the speaker, e.g. "user", "assistant", "system".
    pub role: String,
    /// Message body (indexed for full-text search).
    pub content: String,
    /// Timestamp (Unix millis).
    pub created_at: i64,
}

impl NewMessage {
    /// Build a message for `session_id` from `role` and `content`.
    #[must_use]
    pub fn new(
        session_id: impl Into<String>,
        role: impl Into<String>,
        content: impl Into<String>,
    ) -> Self {
        Self {
            session_id: session_id.into(),
            role: role.into(),
            content: content.into(),
            created_at: now_millis(),
        }
    }
}

/// Data required to record a tool invocation within a session.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NewToolCall {
    /// Owning session id.
    pub session_id: String,
    /// Optional message id this tool call is associated with.
    pub message_id: Option<String>,
    /// The `claude` CLI's tool-use id, used to correlate a later tool result
    /// (`ToolResult.id`) back to this row so its output can be filled in.
    pub tool_use_id: Option<String>,
    /// Tool name, e.g. "Bash", "Edit".
    pub tool_name: String,
    /// Arbitrary JSON input passed to the tool.
    pub input: serde_json::Value,
    /// Arbitrary JSON output returned by the tool (if any).
    pub output: Option<serde_json::Value>,
    /// Whether the tool reported success.
    pub success: bool,
    /// Timestamp (Unix millis).
    pub created_at: i64,
}

impl NewToolCall {
    /// Build a tool-call record for `session_id` / `tool_name` with `input`.
    #[must_use]
    pub fn new(
        session_id: impl Into<String>,
        tool_name: impl Into<String>,
        input: serde_json::Value,
    ) -> Self {
        Self {
            session_id: session_id.into(),
            message_id: None,
            tool_use_id: None,
            tool_name: tool_name.into(),
            input,
            output: None,
            success: true,
            created_at: now_millis(),
        }
    }

    /// Attach the tool output and success flag.
    #[must_use]
    pub fn with_output(mut self, output: serde_json::Value, success: bool) -> Self {
        self.output = Some(output);
        self.success = success;
        self
    }

    /// Attach the `claude` tool-use id so a later result can be matched to it.
    #[must_use]
    pub fn with_tool_use_id(mut self, tool_use_id: impl Into<String>) -> Self {
        self.tool_use_id = Some(tool_use_id.into());
        self
    }
}

/// Data required to record a file diff produced during a session.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NewFileDiff {
    /// Owning session id.
    pub session_id: String,
    /// Path of the file that changed.
    pub path: String,
    /// Unified diff text (indexed for full-text search).
    pub diff: String,
    /// Lines added.
    pub additions: i64,
    /// Lines removed.
    pub deletions: i64,
    /// Timestamp (Unix millis).
    pub created_at: i64,
}

impl NewFileDiff {
    /// Build a file-diff record.
    #[must_use]
    pub fn new(
        session_id: impl Into<String>,
        path: impl Into<String>,
        diff: impl Into<String>,
    ) -> Self {
        Self {
            session_id: session_id.into(),
            path: path.into(),
            diff: diff.into(),
            additions: 0,
            deletions: 0,
            created_at: now_millis(),
        }
    }

    /// Set the additions / deletions counts.
    #[must_use]
    pub fn with_counts(mut self, additions: i64, deletions: i64) -> Self {
        self.additions = additions;
        self.deletions = deletions;
        self
    }
}

/// Data required to record a lifecycle event for a session.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NewEvent {
    /// Owning session id.
    pub session_id: String,
    /// Event kind, e.g. "started", "paused", "tool_denied".
    pub kind: String,
    /// Optional JSON payload describing the event.
    pub payload: Option<serde_json::Value>,
    /// Timestamp (Unix millis).
    pub created_at: i64,
}

impl NewEvent {
    /// Build an event record for `session_id` of `kind`.
    #[must_use]
    pub fn new(session_id: impl Into<String>, kind: impl Into<String>) -> Self {
        Self {
            session_id: session_id.into(),
            kind: kind.into(),
            payload: None,
            created_at: now_millis(),
        }
    }

    /// Attach a JSON payload to the event.
    #[must_use]
    pub fn with_payload(mut self, payload: serde_json::Value) -> Self {
        self.payload = Some(payload);
        self
    }
}

/// The kind of transcript text a [`SearchHit`] originated from.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum HitSource {
    /// The hit came from a transcript message.
    Message,
    /// The hit came from a file diff.
    FileDiff,
    /// The hit came from a tool invocation (what Claude *did* — command run,
    /// file edited, sub-agent launched — together with the tool's output).
    Tool,
}

/// A single full-text search result.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SearchHit {
    /// Session the hit belongs to.
    pub session_id: String,
    /// The matching session's title — so a caller can judge relevance without a
    /// follow-up `get_session` (saves tokens for the model).
    pub title: String,
    /// What kind of record matched.
    pub source: HitSource,
    /// A snippet of matching text with `[` / `]` marking the matched terms.
    pub snippet: String,
    /// BM25 relevance score (lower is a better match in SQLite's ranking).
    pub score: f64,
}

/// Aggregate counts describing the archive.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Stats {
    /// Total number of sessions.
    pub sessions: i64,
    /// Total number of transcript messages.
    pub messages: i64,
    /// Total number of recorded tool calls.
    pub tool_calls: i64,
    /// Total number of recorded file diffs.
    pub file_diffs: i64,
    /// Total number of recorded events.
    pub events: i64,
}

/// A token/USD usage line to record for a session.
#[derive(Debug, Clone, Default)]
pub struct NewUsage {
    /// Session this usage belongs to.
    pub session_id: String,
    /// Model tier (e.g. "opus", "sonnet", "haiku").
    pub model: Option<String>,
    /// Agent that incurred the cost.
    pub agent: Option<String>,
    /// Project the work belongs to.
    pub project: Option<String>,
    /// Input (prompt) tokens.
    pub input_tokens: i64,
    /// Output (completion) tokens.
    pub output_tokens: i64,
    /// Cache-read tokens.
    pub cache_read_tokens: i64,
    /// Cache-creation tokens.
    pub cache_creation_tokens: i64,
    /// USD cost for this call.
    pub cost_usd: f64,
}

/// Aggregated token + cost totals.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct UsageTotals {
    /// Total input tokens.
    pub input_tokens: i64,
    /// Total output tokens.
    pub output_tokens: i64,
    /// Total cache-read tokens.
    pub cache_read_tokens: i64,
    /// Total cache-creation tokens.
    pub cache_creation_tokens: i64,
    /// Total USD cost.
    pub cost_usd: f64,
}

/// A usage aggregate for one group key (model / agent / project).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UsageGroup {
    /// The group key value.
    pub key: String,
    /// Total input tokens.
    pub input_tokens: i64,
    /// Total output tokens.
    pub output_tokens: i64,
    /// Total cache-read tokens.
    pub cache_read_tokens: i64,
    /// Total cache-creation tokens.
    pub cache_creation_tokens: i64,
    /// Total USD cost.
    pub cost_usd: f64,
}

/// A transcript message still missing a semantic embedding, returned by
/// [`SessionStore::unembedded_messages`] for backfilling the vector index.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UnembeddedItem {
    /// The source message id (used as the embedding's `owner_id`).
    pub owner_id: String,
    /// Session the message belongs to.
    pub session_id: String,
    /// The message body to embed.
    pub content: String,
}

/// The permanent SQLite-backed session archive.
pub struct SessionStore {
    conn: Connection,
}

impl SessionStore {
    /// Open an in-memory archive (useful for tests and ephemeral runs).
    pub fn open_in_memory() -> Result<Self> {
        let conn = Connection::open_in_memory()?;
        let store = Self { conn };
        store.init_schema()?;
        Ok(store)
    }

    /// Open (creating if necessary) an on-disk archive at `path`.
    pub fn open(path: &Path) -> Result<Self> {
        let conn = Connection::open(path)?;
        let store = Self { conn };
        store.init_schema()?;
        Ok(store)
    }

    fn init_schema(&self) -> Result<()> {
        self.conn.execute_batch(
            r#"
            PRAGMA journal_mode = WAL;
            PRAGMA foreign_keys = ON;

            CREATE TABLE IF NOT EXISTS sessions (
                id          TEXT PRIMARY KEY,
                title       TEXT NOT NULL,
                cwd         TEXT NOT NULL,
                branch      TEXT,
                model       TEXT,
                created_at  INTEGER NOT NULL,
                updated_at  INTEGER NOT NULL,
                claude_session_id TEXT
            );

            CREATE TABLE IF NOT EXISTS messages (
                id          TEXT PRIMARY KEY,
                session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                role        TEXT NOT NULL,
                content     TEXT NOT NULL,
                created_at  INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);

            CREATE TABLE IF NOT EXISTS tool_calls (
                id          TEXT PRIMARY KEY,
                session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                message_id  TEXT,
                tool_use_id TEXT,
                tool_name   TEXT NOT NULL,
                input       TEXT NOT NULL,
                output      TEXT,
                success     INTEGER NOT NULL,
                created_at  INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tool_calls_session ON tool_calls(session_id);

            CREATE TABLE IF NOT EXISTS file_diffs (
                id          TEXT PRIMARY KEY,
                session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                path        TEXT NOT NULL,
                diff        TEXT NOT NULL,
                additions   INTEGER NOT NULL,
                deletions   INTEGER NOT NULL,
                created_at  INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_file_diffs_session ON file_diffs(session_id);

            CREATE TABLE IF NOT EXISTS events (
                id          TEXT PRIMARY KEY,
                session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                kind        TEXT NOT NULL,
                payload     TEXT,
                created_at  INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);

            -- Full-text index over all transcript text (messages + diffs).
            -- `source` distinguishes the origin; `session_id` lets us join back.
            CREATE VIRTUAL TABLE IF NOT EXISTS transcript_fts USING fts5(
                session_id UNINDEXED,
                source     UNINDEXED,
                body
            );

            -- Semantic-search index: one neural embedding per transcript item.
            -- `vec` is the raw f32 vector (little-endian bytes); `model` tags
            -- which embedder produced it so vectors from different models are
            -- never compared. `owner_id` (the message/diff id) + `model` is the
            -- key, so re-embedding an item replaces rather than duplicates.
            CREATE TABLE IF NOT EXISTS embeddings (
                owner_id    TEXT NOT NULL,
                model       TEXT NOT NULL,
                session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                source      TEXT NOT NULL,
                snippet     TEXT NOT NULL,
                dim         INTEGER NOT NULL,
                vec         BLOB NOT NULL,
                created_at  INTEGER NOT NULL,
                PRIMARY KEY (owner_id, model)
            );
            CREATE INDEX IF NOT EXISTS idx_embeddings_session ON embeddings(session_id);
            CREATE INDEX IF NOT EXISTS idx_embeddings_model ON embeddings(model);

            -- Per-call token + USD usage, for cost dashboards and per-session totals.
            CREATE TABLE IF NOT EXISTS usage (
                id                    TEXT PRIMARY KEY,
                session_id            TEXT NOT NULL,
                model                 TEXT,
                agent                 TEXT,
                project               TEXT,
                input_tokens          INTEGER NOT NULL DEFAULT 0,
                output_tokens         INTEGER NOT NULL DEFAULT 0,
                cache_read_tokens     INTEGER NOT NULL DEFAULT 0,
                cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
                cost_usd              REAL    NOT NULL DEFAULT 0,
                created_at            INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_usage_session ON usage(session_id);
            "#,
        )?;
        // Migration: add `claude_session_id` to databases created before it
        // existed (ALTER fails if the column is already there, so guard it).
        let has_col = self
            .conn
            .prepare(
                "SELECT 1 FROM pragma_table_info('sessions') WHERE name = 'claude_session_id'",
            )?
            .exists([])?;
        if !has_col {
            self.conn
                .execute("ALTER TABLE sessions ADD COLUMN claude_session_id TEXT", [])?;
        }
        // Migration: add `tool_use_id` to tool_calls created before output
        // capture existed, so a streamed tool result can be matched to its row.
        let has_tuid = self
            .conn
            .prepare("SELECT 1 FROM pragma_table_info('tool_calls') WHERE name = 'tool_use_id'")?
            .exists([])?;
        if !has_tuid {
            self.conn
                .execute("ALTER TABLE tool_calls ADD COLUMN tool_use_id TEXT", [])?;
        }
        Ok(())
    }

    /// Record the `claude` CLI's own session id for a session, so it can later
    /// be continued with `--resume`.
    pub fn set_claude_session_id(&self, id: &str, claude_session_id: &str) -> Result<()> {
        self.conn.execute(
            "UPDATE sessions SET claude_session_id = ?2 WHERE id = ?1",
            params![id, claude_session_id],
        )?;
        Ok(())
    }

    /// Insert a new session, returning its generated id.
    pub fn insert_session(&self, s: &NewSession) -> Result<String> {
        let id = new_id();
        self.conn.execute(
            "INSERT INTO sessions (id, title, cwd, branch, model, created_at, updated_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?6)",
            params![id, s.title, s.cwd, s.branch, s.model, s.created_at],
        )?;
        Ok(id)
    }

    /// Fetch a session by id.
    pub fn get_session(&self, id: &str) -> Result<Session> {
        self.conn
            .query_row(
                "SELECT id, title, cwd, branch, model, created_at, updated_at, claude_session_id
                 FROM sessions WHERE id = ?1",
                params![id],
                |row| {
                    Ok(Session {
                        id: row.get(0)?,
                        title: row.get(1)?,
                        cwd: row.get(2)?,
                        branch: row.get(3)?,
                        model: row.get(4)?,
                        created_at: row.get(5)?,
                        updated_at: row.get(6)?,
                        claude_session_id: row.get(7)?,
                    })
                },
            )
            .optional()?
            .ok_or_else(|| Error::NotFound(format!("session {id}")))
    }

    /// List sessions newest-first, paginated.
    ///
    /// `limit` is clamped to the range `1..=500`; `offset` skips that many rows.
    pub fn list_sessions(&self, limit: i64, offset: i64) -> Result<Vec<Session>> {
        let limit = limit.clamp(1, 500);
        let offset = offset.max(0);
        let mut stmt = self.conn.prepare(
            "SELECT id, title, cwd, branch, model, created_at, updated_at, claude_session_id
             FROM sessions ORDER BY created_at DESC LIMIT ?1 OFFSET ?2",
        )?;
        let rows = stmt.query_map(params![limit, offset], |row| {
            Ok(Session {
                id: row.get(0)?,
                title: row.get(1)?,
                cwd: row.get(2)?,
                branch: row.get(3)?,
                model: row.get(4)?,
                created_at: row.get(5)?,
                updated_at: row.get(6)?,
                claude_session_id: row.get(7)?,
            })
        })?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row?);
        }
        Ok(out)
    }

    fn touch_session(&self, session_id: &str, at: i64) -> Result<()> {
        self.conn.execute(
            "UPDATE sessions SET updated_at = ?2 WHERE id = ?1",
            params![session_id, at],
        )?;
        Ok(())
    }

    /// Append a message to a session transcript, returning its id.
    ///
    /// The message body is also inserted into the FTS index so it becomes
    /// searchable via [`SessionStore::full_text_search`].
    /// All messages of a session, oldest first (for replaying a transcript).
    pub fn list_messages(&self, session_id: &str) -> Result<Vec<TranscriptMessage>> {
        let mut stmt = self.conn.prepare(
            "SELECT role, content, created_at FROM messages
             WHERE session_id = ?1 ORDER BY created_at ASC, rowid ASC",
        )?;
        let rows = stmt.query_map(params![session_id], |row| {
            Ok(TranscriptMessage {
                role: row.get(0)?,
                content: row.get(1)?,
                created_at: row.get(2)?,
            })
        })?;
        let mut out = Vec::new();
        for r in rows {
            out.push(r?);
        }
        Ok(out)
    }

    pub fn append_message(&self, m: &NewMessage) -> Result<String> {
        let id = new_id();
        self.conn.execute(
            "INSERT INTO messages (id, session_id, role, content, created_at)
             VALUES (?1, ?2, ?3, ?4, ?5)",
            params![id, m.session_id, m.role, m.content, m.created_at],
        )?;
        self.conn.execute(
            "INSERT INTO transcript_fts (session_id, source, body) VALUES (?1, 'message', ?2)",
            params![m.session_id, m.content],
        )?;
        self.touch_session(&m.session_id, m.created_at)?;
        Ok(id)
    }

    /// Record a tool invocation, returning its id.
    pub fn append_tool_call(&self, t: &NewToolCall) -> Result<String> {
        let id = new_id();
        let input = serde_json::to_string(&t.input)?;
        let output = match &t.output {
            Some(v) => Some(serde_json::to_string(v)?),
            None => None,
        };
        self.conn.execute(
            "INSERT INTO tool_calls
                (id, session_id, message_id, tool_use_id, tool_name, input, output, success, created_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![
                id,
                t.session_id,
                t.message_id,
                t.tool_use_id,
                t.tool_name,
                input,
                output,
                t.success as i64,
                t.created_at
            ],
        )?;
        self.touch_session(&t.session_id, t.created_at)?;
        Ok(id)
    }

    /// Record a file diff, returning its id. The diff text is indexed for FTS.
    pub fn append_file_diff(&self, d: &NewFileDiff) -> Result<String> {
        let id = new_id();
        self.conn.execute(
            "INSERT INTO file_diffs
                (id, session_id, path, diff, additions, deletions, created_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![
                id,
                d.session_id,
                d.path,
                d.diff,
                d.additions,
                d.deletions,
                d.created_at
            ],
        )?;
        self.conn.execute(
            "INSERT INTO transcript_fts (session_id, source, body) VALUES (?1, 'file_diff', ?2)",
            params![d.session_id, d.diff],
        )?;
        self.touch_session(&d.session_id, d.created_at)?;
        Ok(id)
    }

    /// Record a lifecycle event, returning its id.
    pub fn append_event(&self, e: &NewEvent) -> Result<String> {
        let id = new_id();
        let payload = match &e.payload {
            Some(v) => Some(serde_json::to_string(v)?),
            None => None,
        };
        self.conn.execute(
            "INSERT INTO events (id, session_id, kind, payload, created_at)
             VALUES (?1, ?2, ?3, ?4, ?5)",
            params![id, e.session_id, e.kind, payload, e.created_at],
        )?;
        self.touch_session(&e.session_id, e.created_at)?;
        Ok(id)
    }

    /// Run a full-text search over all indexed transcript text.
    ///
    /// `query` uses SQLite's FTS5 MATCH syntax. Results are ordered by relevance
    /// (best match first). Returns at most 50 hits.
    pub fn full_text_search(&self, query: &str, limit: i64) -> Result<Vec<SearchHit>> {
        // Keep results small: each hit is a session id + title + a short snippet
        // (not the full message), so callers — including the Claude MCP tool —
        // can't accidentally pull large amounts of irrelevant transcript.
        let limit = limit.clamp(1, 50);
        // Treat the caller's text as plain search terms, NOT raw FTS5 syntax.
        // Without this, a query like "Healthcheck-Endpoint" makes FTS5 parse
        // `-Endpoint` as a column filter and fail with "no such column". We quote
        // each whitespace token (doubling embedded quotes) so arbitrary user
        // input becomes a safe set of phrase terms.
        let safe_query = sanitize_fts_query(query);
        if safe_query.is_empty() {
            return Ok(Vec::new());
        }
        let mut stmt = self.conn.prepare(
            "SELECT transcript_fts.session_id, s.title, transcript_fts.source,
                    snippet(transcript_fts, 2, '[', ']', '…', 12), rank
             FROM transcript_fts
             JOIN sessions s ON s.id = transcript_fts.session_id
             WHERE transcript_fts MATCH ?1
             ORDER BY rank
             LIMIT ?2",
        )?;
        let rows = stmt.query_map(params![safe_query, limit], |row| {
            let source: String = row.get(2)?;
            let source = match source.as_str() {
                "file_diff" => HitSource::FileDiff,
                "tool" => HitSource::Tool,
                _ => HitSource::Message,
            };
            Ok(SearchHit {
                session_id: row.get(0)?,
                title: row.get(1)?,
                source,
                snippet: row.get(3)?,
                score: row.get(4)?,
            })
        })?;
        let mut hits = Vec::new();
        for r in rows {
            hits.push(r?);
        }
        Ok(hits)
    }

    /// Store (or replace) the semantic embedding for one transcript item.
    ///
    /// `owner_id` is the source message/diff id, `model` the embedder tag (so
    /// vectors from different models never mix), and `snippet` a short excerpt
    /// returned with search hits. Re-embedding the same `(owner_id, model)`
    /// overwrites the previous vector.
    #[allow(clippy::too_many_arguments)]
    pub fn upsert_embedding(
        &self,
        owner_id: &str,
        session_id: &str,
        source: HitSource,
        snippet: &str,
        model: &str,
        vector: &[f32],
        created_at: i64,
    ) -> Result<()> {
        let source = match source {
            HitSource::FileDiff => "file_diff",
            HitSource::Tool => "tool",
            HitSource::Message => "message",
        };
        let blob = encode_vec(vector);
        self.conn.execute(
            "INSERT OR REPLACE INTO embeddings
                (owner_id, model, session_id, source, snippet, dim, vec, created_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![
                owner_id,
                model,
                session_id,
                source,
                snippet,
                vector.len() as i64,
                blob,
                created_at
            ],
        )?;
        Ok(())
    }

    /// Semantic nearest-neighbour search: rank stored embeddings (for `model`)
    /// by cosine similarity to `query`, returning the top `limit` as
    /// [`SearchHit`]s. `score` here is cosine similarity (higher is closer).
    ///
    /// Brute-force cosine over the archive — trivial for thousands of vectors
    /// and entirely local. Each hit carries only a short snippet, so this is as
    /// token-frugal as the FTS path.
    pub fn vector_search(&self, query: &[f32], model: &str, limit: i64) -> Result<Vec<SearchHit>> {
        let limit = limit.clamp(1, 50) as usize;
        let mut stmt = self.conn.prepare(
            "SELECT e.session_id, s.title, e.source, e.snippet, e.vec
             FROM embeddings e
             JOIN sessions s ON s.id = e.session_id
             WHERE e.model = ?1",
        )?;
        let rows = stmt.query_map(params![model], |row| {
            let source: String = row.get(2)?;
            let source = match source.as_str() {
                "file_diff" => HitSource::FileDiff,
                "tool" => HitSource::Tool,
                _ => HitSource::Message,
            };
            let blob: Vec<u8> = row.get(4)?;
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                source,
                row.get::<_, String>(3)?,
                decode_vec(&blob),
            ))
        })?;
        let mut scored: Vec<SearchHit> = Vec::new();
        for r in rows {
            let (session_id, title, source, snippet, vec) = r?;
            scored.push(SearchHit {
                session_id,
                title,
                source,
                snippet,
                score: f64::from(cosine(query, &vec)),
            });
        }
        // Highest cosine first; drop anything essentially orthogonal so the
        // model never sees padding of irrelevant sessions.
        scored.sort_by(|a, b| {
            b.score
                .partial_cmp(&a.score)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        scored.retain(|h| h.score > 0.15);
        scored.truncate(limit);
        Ok(scored)
    }

    /// Transcript messages that have no embedding yet for `model` (used to
    /// backfill the semantic index for sessions recorded before embeddings
    /// existed, or with a different model). Oldest first, at most `limit`.
    pub fn unembedded_messages(&self, model: &str, limit: i64) -> Result<Vec<UnembeddedItem>> {
        let limit = limit.clamp(1, 10_000);
        let mut stmt = self.conn.prepare(
            "SELECT m.id, m.session_id, m.content
             FROM messages m
             LEFT JOIN embeddings e
               ON e.owner_id = m.id AND e.model = ?1
             WHERE e.owner_id IS NULL
             ORDER BY m.created_at ASC, m.rowid ASC
             LIMIT ?2",
        )?;
        let rows = stmt.query_map(params![model, limit], |row| {
            Ok(UnembeddedItem {
                owner_id: row.get(0)?,
                session_id: row.get(1)?,
                content: row.get(2)?,
            })
        })?;
        let mut out = Vec::new();
        for r in rows {
            out.push(r?);
        }
        Ok(out)
    }

    /// Fill in a tool call's output (and success flag) by matching the
    /// `claude` tool-use id, capturing what the tool actually returned. Returns
    /// the item to (re)embed for semantic search — the tool name, input and
    /// output as one text — or `None` if no row matched (e.g. unknown id).
    pub fn set_tool_output(
        &self,
        session_id: &str,
        tool_use_id: &str,
        output: &str,
        success: bool,
    ) -> Result<Option<UnembeddedItem>> {
        let stored = clamp_output(output);
        let changed = self.conn.execute(
            "UPDATE tool_calls SET output = ?3, success = ?4
             WHERE session_id = ?1 AND tool_use_id = ?2",
            params![session_id, tool_use_id, stored, success as i64],
        )?;
        if changed == 0 {
            return Ok(None);
        }
        // Re-read the row to assemble the embedding text (input + output).
        let row = self
            .conn
            .query_row(
                "SELECT id, tool_name, input, output FROM tool_calls
                 WHERE session_id = ?1 AND tool_use_id = ?2",
                params![session_id, tool_use_id],
                |row| {
                    let owner_id: String = row.get(0)?;
                    let tool_name: String = row.get(1)?;
                    let input: String = row.get(2)?;
                    let output: Option<String> = row.get(3)?;
                    Ok((owner_id, tool_name, input, output))
                },
            )
            .optional()?;
        Ok(
            row.map(|(owner_id, tool_name, input, output)| UnembeddedItem {
                owner_id,
                session_id: session_id.to_string(),
                content: tool_embed_text(&tool_name, &input, output.as_deref()),
            }),
        )
    }

    /// Tool calls that have no embedding yet for `model` (used to backfill the
    /// semantic index — e.g. for runs recorded before tool-output capture, or
    /// tool calls that never produced a result). Oldest first, at most `limit`.
    pub fn unembedded_tool_calls(&self, model: &str, limit: i64) -> Result<Vec<UnembeddedItem>> {
        let limit = limit.clamp(1, 10_000);
        let mut stmt = self.conn.prepare(
            "SELECT t.id, t.session_id, t.tool_name, t.input, t.output
             FROM tool_calls t
             LEFT JOIN embeddings e ON e.owner_id = t.id AND e.model = ?1
             WHERE e.owner_id IS NULL
             ORDER BY t.created_at ASC, t.rowid ASC
             LIMIT ?2",
        )?;
        let rows = stmt.query_map(params![model, limit], |row| {
            let owner_id: String = row.get(0)?;
            let session_id: String = row.get(1)?;
            let tool_name: String = row.get(2)?;
            let input: String = row.get(3)?;
            let output: Option<String> = row.get(4)?;
            Ok(UnembeddedItem {
                owner_id,
                session_id,
                content: tool_embed_text(&tool_name, &input, output.as_deref()),
            })
        })?;
        let mut out = Vec::new();
        for r in rows {
            out.push(r?);
        }
        Ok(out)
    }

    /// Return aggregate counts describing the archive.
    pub fn stats(&self) -> Result<Stats> {
        let count = |table: &str| -> Result<i64> {
            // Table names are internal constants, never user input.
            let sql = format!("SELECT COUNT(*) FROM {table}");
            Ok(self.conn.query_row(&sql, [], |row| row.get(0))?)
        };
        Ok(Stats {
            sessions: count("sessions")?,
            messages: count("messages")?,
            tool_calls: count("tool_calls")?,
            file_diffs: count("file_diffs")?,
            events: count("events")?,
        })
    }

    /// Record a token/USD usage line for a session.
    pub fn record_usage(&self, u: &NewUsage) -> Result<String> {
        let id = uuid::Uuid::new_v4().to_string();
        self.conn.execute(
            "INSERT INTO usage (id, session_id, model, agent, project,
                input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, cost_usd, created_at)
             VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11)",
            params![
                id, u.session_id, u.model, u.agent, u.project,
                u.input_tokens, u.output_tokens, u.cache_read_tokens,
                u.cache_creation_tokens, u.cost_usd, now_millis()
            ],
        )?;
        Ok(id)
    }

    /// Aggregate token + cost totals for one session.
    pub fn session_usage(&self, session_id: &str) -> Result<UsageTotals> {
        Ok(self.conn.query_row(
            "SELECT COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0),
                    COALESCE(SUM(cache_read_tokens),0), COALESCE(SUM(cache_creation_tokens),0),
                    COALESCE(SUM(cost_usd),0)
             FROM usage WHERE session_id = ?1",
            params![session_id],
            |r| {
                Ok(UsageTotals {
                    input_tokens: r.get(0)?,
                    output_tokens: r.get(1)?,
                    cache_read_tokens: r.get(2)?,
                    cache_creation_tokens: r.get(3)?,
                    cost_usd: r.get(4)?,
                })
            },
        )?)
    }

    /// Aggregate cost + tokens grouped by a column (model / agent / project).
    pub fn usage_summary(&self, group_by: &str) -> Result<Vec<UsageGroup>> {
        let col = match group_by {
            "agent" => "agent",
            "project" => "project",
            _ => "model",
        };
        let sql = format!(
            "SELECT COALESCE({col},'(none)'), COALESCE(SUM(input_tokens),0),
                    COALESCE(SUM(output_tokens),0), COALESCE(SUM(cache_read_tokens),0),
                    COALESCE(SUM(cache_creation_tokens),0), COALESCE(SUM(cost_usd),0)
             FROM usage GROUP BY {col} ORDER BY SUM(cost_usd) DESC"
        );
        let mut stmt = self.conn.prepare(&sql)?;
        let rows = stmt.query_map([], |r| {
            Ok(UsageGroup {
                key: r.get(0)?,
                input_tokens: r.get(1)?,
                output_tokens: r.get(2)?,
                cache_read_tokens: r.get(3)?,
                cache_creation_tokens: r.get(4)?,
                cost_usd: r.get(5)?,
            })
        })?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row?);
        }
        Ok(out)
    }

    /// Cache hit rate = cache_read / (cache_read + input). 0.0 when no usage.
    pub fn cache_hit_rate(&self) -> Result<f64> {
        let (read, input): (i64, i64) = self.conn.query_row(
            "SELECT COALESCE(SUM(cache_read_tokens),0), COALESCE(SUM(input_tokens),0) FROM usage",
            [],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )?;
        let denom = (read + input) as f64;
        Ok(if denom > 0.0 { read as f64 / denom } else { 0.0 })
    }

    /// The session id with the highest total cost (and that cost), if any.
    pub fn most_expensive_session(&self) -> Result<Option<(String, f64)>> {
        Ok(self
            .conn
            .query_row(
                "SELECT session_id, SUM(cost_usd) c FROM usage
                 GROUP BY session_id ORDER BY c DESC LIMIT 1",
                [],
                |r| Ok((r.get::<_, String>(0)?, r.get::<_, f64>(1)?)),
            )
            .optional()?)
    }
}

/// Turn arbitrary user search text into a safe FTS5 MATCH expression.
///
/// Each whitespace-separated token is wrapped in double quotes (with embedded
/// quotes doubled), so characters FTS5 treats specially — `-` (NOT), `:`
/// (column filter), `*`, `(`, `"` — are taken literally instead of crashing the
/// query (e.g. `Healthcheck-Endpoint` → `"Healthcheck-Endpoint"`). Tokens are
/// joined by spaces, which FTS5 reads as an implicit AND. Returns an empty
/// string when there is nothing to search for.
#[must_use]
pub fn sanitize_fts_query(query: &str) -> String {
    query
        .split_whitespace()
        .filter(|t| !t.is_empty())
        .map(|t| format!("\"{}\"", t.replace('"', "\"\"")))
        .collect::<Vec<_>>()
        .join(" ")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn in_memory_open_insert_and_fts_search() {
        let store = SessionStore::open_in_memory().unwrap();
        let sid = store
            .insert_session(&NewSession::new("Fix the parser", "/repo").with_branch("main"))
            .unwrap();

        store
            .append_message(&NewMessage::new(
                &sid,
                "user",
                "the parser crashes on empty input",
            ))
            .unwrap();
        store
            .append_message(&NewMessage::new(
                &sid,
                "assistant",
                "I will add a guard clause",
            ))
            .unwrap();

        let hits = store.full_text_search("parser", 10).unwrap();
        assert_eq!(hits.len(), 1, "exactly one message mentions 'parser'");
        assert_eq!(hits[0].session_id, sid);
        assert_eq!(hits[0].source, HitSource::Message);
        assert!(
            hits[0].snippet.contains('['),
            "snippet should mark the term"
        );
    }

    #[test]
    fn stats_reflect_inserts() {
        let store = SessionStore::open_in_memory().unwrap();
        let sid = store
            .insert_session(&NewSession::new("Session", "/work"))
            .unwrap();
        store
            .append_message(&NewMessage::new(&sid, "user", "hello world"))
            .unwrap();
        store
            .append_tool_call(&NewToolCall::new(
                &sid,
                "Bash",
                serde_json::json!({"cmd": "ls"}),
            ))
            .unwrap();
        store
            .append_file_diff(
                &NewFileDiff::new(&sid, "src/lib.rs", "+added line").with_counts(1, 0),
            )
            .unwrap();
        store.append_event(&NewEvent::new(&sid, "started")).unwrap();

        let stats = store.stats().unwrap();
        assert_eq!(stats.sessions, 1);
        assert_eq!(stats.messages, 1);
        assert_eq!(stats.tool_calls, 1);
        assert_eq!(stats.file_diffs, 1);
        assert_eq!(stats.events, 1);
    }

    #[test]
    fn file_diff_text_is_searchable() {
        let store = SessionStore::open_in_memory().unwrap();
        let sid = store
            .insert_session(&NewSession::new("Diff session", "/repo"))
            .unwrap();
        store
            .append_file_diff(&NewFileDiff::new(
                &sid,
                "src/main.rs",
                "-let x = compute();\n+let x = compute_fast();",
            ))
            .unwrap();

        let hits = store.full_text_search("compute_fast", 10).unwrap();
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].source, HitSource::FileDiff);
    }

    #[test]
    fn get_missing_session_is_not_found() {
        let store = SessionStore::open_in_memory().unwrap();
        let err = store.get_session("nope").unwrap_err();
        assert!(matches!(err, Error::NotFound(_)));
    }

    #[test]
    fn list_sessions_is_newest_first_and_paginates() {
        let store = SessionStore::open_in_memory().unwrap();
        for (i, title) in ["first", "second", "third"].iter().enumerate() {
            let mut s = NewSession::new(*title, "/repo");
            s.created_at = 1_000 + i as i64; // strictly increasing
            store.insert_session(&s).unwrap();
        }
        let all = store.list_sessions(10, 0).unwrap();
        assert_eq!(all.len(), 3);
        assert_eq!(all[0].title, "third", "newest first");
        assert_eq!(all[2].title, "first");

        let page = store.list_sessions(1, 1).unwrap();
        assert_eq!(page.len(), 1);
        assert_eq!(page[0].title, "second", "offset skips the newest");
    }

    #[test]
    fn vector_search_ranks_by_cosine_and_respects_model() {
        let store = SessionStore::open_in_memory().unwrap();
        let sid = store
            .insert_session(&NewSession::new("Vec session", "/repo"))
            .unwrap();
        let m1 = store
            .append_message(&NewMessage::new(&sid, "user", "alpha"))
            .unwrap();
        let m2 = store
            .append_message(&NewMessage::new(&sid, "user", "beta"))
            .unwrap();
        store
            .upsert_embedding(
                &m1,
                &sid,
                HitSource::Message,
                "alpha",
                "m",
                &[1.0, 0.0, 0.0],
                1,
            )
            .unwrap();
        store
            .upsert_embedding(
                &m2,
                &sid,
                HitSource::Message,
                "beta",
                "m",
                &[0.0, 1.0, 0.0],
                2,
            )
            .unwrap();

        // Query aligned with the first vector: only it clears the 0.15 floor.
        let hits = store.vector_search(&[0.9, 0.1, 0.0], "m", 5).unwrap();
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].snippet, "alpha");
        assert!(hits[0].score > 0.9);

        // A different model tag must not surface these vectors.
        assert!(store
            .vector_search(&[1.0, 0.0, 0.0], "other", 5)
            .unwrap()
            .is_empty());

        // Re-embedding the same (owner, model) replaces rather than duplicates.
        store
            .upsert_embedding(
                &m1,
                &sid,
                HitSource::Message,
                "alpha-v2",
                "m",
                &[1.0, 0.0, 0.0],
                3,
            )
            .unwrap();
        let hits = store.vector_search(&[1.0, 0.0, 0.0], "m", 5).unwrap();
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].snippet, "alpha-v2");
    }

    #[test]
    fn unembedded_messages_tracks_backfill_state() {
        let store = SessionStore::open_in_memory().unwrap();
        let sid = store
            .insert_session(&NewSession::new("Backfill", "/repo"))
            .unwrap();
        let m1 = store
            .append_message(&NewMessage::new(&sid, "user", "one"))
            .unwrap();
        store
            .append_message(&NewMessage::new(&sid, "user", "two"))
            .unwrap();

        assert_eq!(store.unembedded_messages("m", 10).unwrap().len(), 2);
        store
            .upsert_embedding(&m1, &sid, HitSource::Message, "one", "m", &[1.0, 0.0], 1)
            .unwrap();
        let pending = store.unembedded_messages("m", 10).unwrap();
        assert_eq!(pending.len(), 1, "only the un-embedded message remains");
        assert_eq!(pending[0].content, "two");
    }

    #[test]
    fn tool_output_capture_and_tool_embedding() {
        let store = SessionStore::open_in_memory().unwrap();
        let sid = store
            .insert_session(&NewSession::new("Tools", "/repo"))
            .unwrap();
        let tid = store
            .append_tool_call(
                &NewToolCall::new(&sid, "Bash", serde_json::json!({"command": "cargo test"}))
                    .with_tool_use_id("toolu_1"),
            )
            .unwrap();

        // Before its result arrives it's an un-embedded tool call.
        assert_eq!(store.unembedded_tool_calls("m", 10).unwrap().len(), 1);

        // Capturing the output matches by tool-use id and yields embed text.
        let item = store
            .set_tool_output(&sid, "toolu_1", "115 passed; 0 failed", true)
            .unwrap()
            .expect("row matched");
        assert_eq!(item.owner_id, tid);
        assert!(item.content.contains("Bash") && item.content.contains("115 passed"));

        // An unknown tool-use id updates nothing.
        assert!(store
            .set_tool_output(&sid, "nope", "x", true)
            .unwrap()
            .is_none());

        // Once embedded it's a Tool-source hit and stops being "unembedded".
        store
            .upsert_embedding(
                &item.owner_id,
                &item.session_id,
                HitSource::Tool,
                "Bash: cargo test → 115 passed",
                "m",
                &[1.0, 0.0, 0.0],
                1,
            )
            .unwrap();
        assert!(store.unembedded_tool_calls("m", 10).unwrap().is_empty());
        let hits = store.vector_search(&[1.0, 0.0, 0.0], "m", 5).unwrap();
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].source, HitSource::Tool);
        assert!(
            hits[0].snippet.len() < 200,
            "search returns a short snippet, not raw output"
        );
    }
}
