//! Built-in MCP stdio server exposing the core's session database.
//!
//! Run via the `claudestudio-core mcp` subcommand. Every Claude session the
//! core spawns is launched with this server pre-registered (`--mcp-config`) and
//! its tools pre-approved (`--allowedTools`), so **every agent has default,
//! prompt-free read access to the ClaudeStudio session archive**.
//!
//! Wire protocol: newline-delimited JSON-RPC 2.0 over stdin/stdout (the framing
//! Claude Code's MCP stdio transport expects). All request→response dispatch is
//! in pure functions ([`handle_message`], [`call_tool`]) so it is unit-testable
//! without spawning a process.

use cs_sessions::SessionStore;
use cs_vector::Embedder;
use serde_json::{json, Value};
use std::io::{BufRead, Write};
use std::sync::Arc;

/// MCP protocol revision we implement (matches what the `claude` CLI negotiates).
const PROTOCOL_VERSION: &str = "2024-11-05";

/// Run the MCP stdio loop until stdin closes. Reads one JSON-RPC message per
/// line; writes one JSON response per line. Notifications produce no output.
///
/// `embedder` / `model_tag` power semantic search: the query is embedded and
/// matched against the vector index; keyword FTS is the fallback when the index
/// is empty (e.g. before the core has finished backfilling).
pub fn run(
    store: SessionStore,
    embedder: Arc<dyn Embedder>,
    model_tag: String,
) -> anyhow::Result<()> {
    let stdin = std::io::stdin();
    let mut stdout = std::io::stdout();
    let mut reader = stdin.lock();
    let mut line = String::new();
    loop {
        line.clear();
        if reader.read_line(&mut line)? == 0 {
            break; // EOF: the client (claude) went away.
        }
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        let request: Value = match serde_json::from_str(trimmed) {
            Ok(v) => v,
            Err(_) => continue, // ignore unparseable lines rather than crash
        };
        if let Some(response) = handle_message(&store, embedder.as_ref(), &model_tag, &request) {
            let encoded = serde_json::to_string(&response)?;
            stdout.write_all(encoded.as_bytes())?;
            stdout.write_all(b"\n")?;
            stdout.flush()?;
        }
    }
    Ok(())
}

/// Dispatch a single JSON-RPC message. Returns `Some(response)` for requests and
/// `None` for notifications (messages without an `id`, e.g.
/// `notifications/initialized`).
pub fn handle_message(
    store: &SessionStore,
    embedder: &dyn Embedder,
    model_tag: &str,
    request: &Value,
) -> Option<Value> {
    let id = request.get("id").cloned();
    let method = request.get("method").and_then(Value::as_str).unwrap_or("");
    match method {
        "initialize" => Some(ok(
            id,
            json!({
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": { "tools": {} },
                "serverInfo": { "name": "claudestudio", "version": env!("CARGO_PKG_VERSION") }
            }),
        )),
        "notifications/initialized" => None,
        "ping" => Some(ok(id, json!({}))),
        "tools/list" => Some(ok(id, json!({ "tools": tool_definitions() }))),
        "tools/call" => Some(handle_tool_call(
            store,
            embedder,
            model_tag,
            id,
            request.get("params"),
        )),
        _ => {
            // Don't reply to unknown notifications (no id); error on unknown requests.
            id.as_ref()?;
            Some(err(id, -32601, &format!("method not found: {method}")))
        }
    }
}

fn handle_tool_call(
    store: &SessionStore,
    embedder: &dyn Embedder,
    model_tag: &str,
    id: Option<Value>,
    params: Option<&Value>,
) -> Value {
    let name = params
        .and_then(|p| p.get("name"))
        .and_then(Value::as_str)
        .unwrap_or("");
    let args = params
        .and_then(|p| p.get("arguments"))
        .cloned()
        .unwrap_or_else(|| json!({}));
    match call_tool(store, embedder, model_tag, name, &args) {
        Ok(value) => ok(
            id,
            json!({
                "content": [{ "type": "text", "text": serde_json::to_string_pretty(&value).unwrap_or_default() }],
                "isError": false
            }),
        ),
        Err(message) => ok(
            id,
            json!({
                "content": [{ "type": "text", "text": message }],
                "isError": true
            }),
        ),
    }
}

/// Execute a tool against the session store. Read-only.
pub fn call_tool(
    store: &SessionStore,
    embedder: &dyn Embedder,
    model_tag: &str,
    name: &str,
    args: &Value,
) -> Result<Value, String> {
    match name {
        "list_sessions" => {
            let limit = args.get("limit").and_then(Value::as_i64).unwrap_or(20);
            let sessions = store.list_sessions(limit, 0).map_err(|e| e.to_string())?;
            Ok(json!({ "sessions": sessions }))
        }
        "get_session" => {
            let id = args
                .get("id")
                .and_then(Value::as_str)
                .ok_or("missing 'id'")?;
            let session = store.get_session(id).map_err(|e| e.to_string())?;
            Ok(serde_json::to_value(session).unwrap_or(Value::Null))
        }
        "search_sessions" => {
            let query = args
                .get("query")
                .and_then(Value::as_str)
                .ok_or("missing 'query'")?;
            // Default to a small result set; each hit is just a title + short
            // snippet, so this stays cheap on tokens.
            let limit = args.get("limit").and_then(Value::as_i64).unwrap_or(8);
            // Semantic-first: embed the query and rank by meaning. Fall back to
            // keyword FTS only when the vector index returns nothing.
            let query_vec = embedder.embed(query);
            let mut hits = store
                .vector_search(&query_vec, model_tag, limit)
                .map_err(|e| e.to_string())?;
            if hits.is_empty() {
                hits = store
                    .full_text_search(query, limit)
                    .map_err(|e| e.to_string())?;
            }
            Ok(json!({ "hits": hits }))
        }
        "session_stats" => {
            let stats = store.stats().map_err(|e| e.to_string())?;
            Ok(serde_json::to_value(stats).unwrap_or(Value::Null))
        }
        other => Err(format!("unknown tool: {other}")),
    }
}

/// The `tools/list` schema advertised to the client.
fn tool_definitions() -> Vec<Value> {
    vec![
        json!({
            "name": "search_sessions",
            "description": "PREFER THIS. Token-cheap SEMANTIC search over the ClaudeStudio session archive: ranks past sessions by meaning (a local neural embedding), not just keywords, and falls back to keyword matching automatically. Returns only the best-matching sessions as {session_id, title, short snippet} — never full transcripts. Use a natural-language query and read the snippets; only call get_session afterwards if you truly need a specific session's metadata. This is the efficient way to recall past work.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": { "type": "string", "description": "Natural-language description of what you're looking for (semantic match)." },
                    "limit": { "type": "integer", "description": "Max hits to return (default 8). Keep small.", "minimum": 1, "maximum": 50 }
                },
                "required": ["query"]
            }
        }),
        json!({
            "name": "session_stats",
            "description": "Tiny aggregate counts for the whole archive (sessions, messages, tool calls, file diffs, events). Cheap — use it for overview questions about totals.",
            "inputSchema": { "type": "object", "properties": {} }
        }),
        json!({
            "name": "list_sessions",
            "description": "List recent sessions (newest first) as compact metadata rows. Use a SMALL limit; for finding specific past work prefer search_sessions instead of listing many.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "limit": { "type": "integer", "description": "Maximum sessions to return (default 20; keep small).", "minimum": 1, "maximum": 100 }
                }
            }
        }),
        json!({
            "name": "get_session",
            "description": "Get ONE session's metadata (title, cwd, model, timestamps) by id. Metadata only — it does not return messages, tool calls, or outputs. Call sparingly, after search_sessions has pinpointed the session you need.",
            "inputSchema": {
                "type": "object",
                "properties": { "id": { "type": "string", "description": "The session id (UUID)." } },
                "required": ["id"]
            }
        }),
    ]
}

fn ok(id: Option<Value>, result: Value) -> Value {
    json!({ "jsonrpc": "2.0", "id": id.unwrap_or(Value::Null), "result": result })
}

fn err(id: Option<Value>, code: i64, message: &str) -> Value {
    json!({ "jsonrpc": "2.0", "id": id.unwrap_or(Value::Null), "error": { "code": code, "message": message } })
}

#[cfg(test)]
mod tests {
    use super::*;
    use cs_sessions::{NewSession, SessionStore};
    use cs_vector::HashEmbedder;

    /// A cheap, download-free embedder for tests (the neural model isn't loaded
    /// in unit tests; search falls back to FTS anyway).
    fn emb() -> HashEmbedder {
        HashEmbedder::default()
    }

    fn store_with_one() -> SessionStore {
        let store = SessionStore::open_in_memory().expect("in-memory store");
        store
            .insert_session(&NewSession::new("Refactor IPC", "/tmp/proj"))
            .expect("insert");
        store
    }

    #[test]
    fn initialize_returns_server_info() {
        let store = SessionStore::open_in_memory().unwrap();
        let req = json!({ "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {} });
        let resp = handle_message(&store, &emb(), "hash", &req).expect("response");
        assert_eq!(resp["result"]["serverInfo"]["name"], json!("claudestudio"));
        assert_eq!(resp["result"]["protocolVersion"], json!(PROTOCOL_VERSION));
        assert!(resp["result"]["capabilities"]["tools"].is_object());
    }

    #[test]
    fn initialized_notification_has_no_response() {
        let store = SessionStore::open_in_memory().unwrap();
        let req = json!({ "jsonrpc": "2.0", "method": "notifications/initialized" });
        assert!(handle_message(&store, &emb(), "hash", &req).is_none());
    }

    #[test]
    fn tools_list_advertises_every_db_tool() {
        let store = SessionStore::open_in_memory().unwrap();
        let req = json!({ "jsonrpc": "2.0", "id": 2, "method": "tools/list" });
        let resp = handle_message(&store, &emb(), "hash", &req).expect("response");
        let names: Vec<String> = resp["result"]["tools"]
            .as_array()
            .unwrap()
            .iter()
            .map(|t| t["name"].as_str().unwrap().to_string())
            .collect();
        for tool in [
            "list_sessions",
            "get_session",
            "search_sessions",
            "session_stats",
        ] {
            assert!(names.contains(&tool.to_string()), "missing tool {tool}");
        }
    }

    #[test]
    fn tools_call_list_sessions_returns_rows() {
        let store = store_with_one();
        let req = json!({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": { "name": "list_sessions", "arguments": { "limit": 10 } }
        });
        let resp = handle_message(&store, &emb(), "hash", &req).expect("response");
        assert_eq!(resp["result"]["isError"], json!(false));
        let text = resp["result"]["content"][0]["text"].as_str().unwrap();
        assert!(text.contains("Refactor IPC"));
    }

    #[test]
    fn tools_call_unknown_tool_is_error_result() {
        let store = SessionStore::open_in_memory().unwrap();
        let req = json!({
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": { "name": "drop_tables", "arguments": {} }
        });
        let resp = handle_message(&store, &emb(), "hash", &req).expect("response");
        assert_eq!(resp["result"]["isError"], json!(true));
    }

    #[test]
    fn unknown_method_returns_jsonrpc_error() {
        let store = SessionStore::open_in_memory().unwrap();
        let req = json!({ "jsonrpc": "2.0", "id": 5, "method": "does.not.exist" });
        let resp = handle_message(&store, &emb(), "hash", &req).expect("response");
        assert_eq!(resp["error"]["code"], json!(-32601));
    }
}
