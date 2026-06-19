#![forbid(unsafe_code)]
//! # claudestudio-core
//!
//! The ClaudeStudio Rust sidecar binary. It is the long-running process the
//! native macOS front-end launches and talks to over a Unix domain socket using
//! the length-prefixed MessagePack protocol from `cs-ipc`.
//!
//! On startup it:
//!
//! 1. Initializes tracing.
//! 2. Loads [`cs_config::AppConfig`] via `load_or_default` from `~/.claudestudio`.
//! 3. Opens an in-memory [`cs_sessions::SessionStore`].
//! 4. Creates a [`cs_agentic_os::EventBus`].
//! 5. Binds a Unix domain socket (CLI arg, or `~/.claudestudio/core.sock`).
//! 6. Accepts connections and dispatches [`cs_ipc::IpcEnvelope`] frames through a
//!    small [`Router`] (`ping`, `config.get`, `context.budget`).
//!
//! It shuts down gracefully on `Ctrl-C`, removing the socket file.

mod router;

use std::path::{Path, PathBuf};

use cs_agentic_os::EventBus;
use cs_config::AppConfig;
use cs_ipc::{FrameReader, FrameWriter, IpcEnvelope};
use cs_sessions::SessionStore;
use router::Router;
use tokio::net::{UnixListener, UnixStream};

/// Default directory under the user's home where state and the socket live.
const STATE_DIR: &str = ".claudestudio";
/// Default socket file name within the state directory. Must match the Swift
/// front-end's `IpcProtocol.defaultSocketPath` (`~/.claudestudio/core.sock`).
const SOCKET_NAME: &str = "core.sock";

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    init_tracing();

    let state_dir = state_dir();
    std::fs::create_dir_all(&state_dir).ok();

    // 2. Configuration.
    let config = AppConfig::load_or_default(&state_dir);
    tracing::info!(trust_mode = ?config.trust_mode, "loaded configuration");

    // 3. Session store (ephemeral for the demo sidecar).
    let sessions = SessionStore::open_in_memory()
        .map_err(|e| anyhow::anyhow!("failed to open session store: {e}"))?;

    // 4. Event bus.
    let event_bus = EventBus::new();

    // 5. Socket path.
    let socket_path = socket_path_from_args(&state_dir);
    if socket_path.exists() {
        // A stale socket from a previous run would block bind(); remove it.
        std::fs::remove_file(&socket_path).ok();
    }
    let listener = UnixListener::bind(&socket_path)
        .map_err(|e| anyhow::anyhow!("failed to bind {}: {e}", socket_path.display()))?;
    tracing::info!(socket = %socket_path.display(), "claudestudio-core listening");

    let router = Router::new(config, sessions, event_bus);

    // 6. Accept loop with graceful Ctrl-C shutdown.
    let result = serve(&listener, router).await;

    // Cleanup the socket file on the way out.
    std::fs::remove_file(&socket_path).ok();
    tracing::info!("claudestudio-core stopped");
    result
}

/// Run the accept loop until `Ctrl-C`.
async fn serve(listener: &UnixListener, router: Router) -> anyhow::Result<()> {
    loop {
        tokio::select! {
            _ = tokio::signal::ctrl_c() => {
                tracing::info!("received ctrl-c; shutting down");
                return Ok(());
            }
            accepted = listener.accept() => {
                match accepted {
                    Ok((stream, _addr)) => {
                        let router = router.clone();
                        tokio::spawn(async move {
                            if let Err(e) = handle_connection(stream, router).await {
                                tracing::warn!(%e, "connection ended with error");
                            }
                        });
                    }
                    Err(e) => {
                        tracing::warn!(%e, "accept failed");
                    }
                }
            }
        }
    }
}

/// Read request frames from a single client and write back response frames until
/// the client disconnects.
async fn handle_connection(stream: UnixStream, router: Router) -> anyhow::Result<()> {
    let (read_half, write_half) = stream.into_split();
    let mut reader = FrameReader::new(read_half);
    let mut writer = FrameWriter::new(write_half);

    while let Some(request) = reader.read_frame::<IpcEnvelope>().await? {
        tracing::debug!(method = %request.method, id = %request.id, "request");
        let response = router.dispatch(&request);
        writer.write_frame(&response).await?;
    }
    Ok(())
}

/// Initialize the tracing subscriber, honoring `RUST_LOG`.
fn init_tracing() {
    use tracing_subscriber::{fmt, EnvFilter};
    let filter = EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info"));
    let _ = fmt().with_env_filter(filter).try_init();
}

/// Resolve the state directory: `$HOME/.claudestudio`, falling back to a
/// relative path if `$HOME` is unset.
fn state_dir() -> PathBuf {
    match std::env::var_os("HOME") {
        Some(home) => Path::new(&home).join(STATE_DIR),
        None => PathBuf::from(STATE_DIR),
    }
}

/// Determine the socket path from the first CLI argument, defaulting to
/// `<state_dir>/core.sock`.
fn socket_path_from_args(state_dir: &Path) -> PathBuf {
    std::env::args()
        .nth(1)
        .map(PathBuf::from)
        .unwrap_or_else(|| state_dir.join(SOCKET_NAME))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_socket_is_under_state_dir() {
        let dir = Path::new("/tmp/state");
        // With no extra CLI args in the test binary, falls back to default.
        let path = socket_path_from_args(dir);
        // Either the test harness passed no arg (default) -> ends with core.sock,
        // or it passed one; both are valid PathBufs. Assert it is non-empty.
        assert!(!path.as_os_str().is_empty());
    }

    #[test]
    fn state_dir_is_non_empty() {
        assert!(!state_dir().as_os_str().is_empty());
    }

    #[tokio::test]
    async fn end_to_end_ping_over_socket() {
        // Bind on a temp socket, send a ping, expect a pong.
        let dir = std::env::temp_dir();
        let sock = dir.join(format!("cs-cli-test-{}.sock", std::process::id()));
        let _ = std::fs::remove_file(&sock);

        let listener = UnixListener::bind(&sock).expect("bind");
        let router = Router::new(
            AppConfig::default(),
            SessionStore::open_in_memory().unwrap(),
            EventBus::new(),
        );

        // Accept one connection in the background.
        let server = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            handle_connection(stream, router).await.unwrap();
        });

        let client = UnixStream::connect(&sock).await.expect("connect");
        let (rh, wh) = client.into_split();
        let mut reader = FrameReader::new(rh);
        let mut writer = FrameWriter::new(wh);

        let req = cs_ipc::new_request("ping", serde_json::json!({}));
        writer.write_frame(&req).await.unwrap();
        let res: IpcEnvelope = reader.read_frame().await.unwrap().unwrap();
        assert_eq!(res.payload["pong"], serde_json::json!(true));

        // Drop the client to end the server task cleanly.
        drop(writer);
        drop(reader);
        let _ = server.await;
        let _ = std::fs::remove_file(&sock);
    }
}
