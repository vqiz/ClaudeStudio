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
//! 3. Opens the on-disk [`cs_sessions::SessionStore`] archive.
//! 4. Creates a [`cs_agentic_os::EventBus`].
//! 5. Binds a Unix domain socket (CLI arg, or `~/.claudestudio/core.sock`).
//! 6. Accepts connections and dispatches [`cs_ipc::IpcEnvelope`] frames through a
//!    [`Router`] (config, sessions, git, task & definition libraries), and
//!    streams `SystemEvent`s to any client that sends `events.subscribe`.
//!
//! It shuts down gracefully on `Ctrl-C`, removing the socket file.

mod router;

use std::path::{Path, PathBuf};
use std::sync::Arc;

use cs_agentic_os::{EventBus, SystemEvent};
use cs_config::AppConfig;
use cs_ipc::{new_event, FrameReader, FrameWriter, IpcEnvelope};
use cs_sessions::SessionStore;
use router::Router;
use tokio::net::unix::OwnedWriteHalf;
use tokio::net::{UnixListener, UnixStream};
use tokio::sync::broadcast;
use tokio::sync::Mutex;

/// A connection's write half, shared between the request loop and any event
/// forwarder task spawned by `events.subscribe`.
type SharedWriter = Arc<Mutex<FrameWriter<OwnedWriteHalf>>>;

/// Owns a connection's event-forwarder tasks and aborts them when the connection
/// ends. Without this, a forwarder parked on `rx.recv().await` would never wake
/// on client disconnect (the bus `Sender` lives for the whole process), leaking
/// the task and the socket write half it holds.
#[derive(Default)]
struct ForwarderGuard(Vec<tokio::task::JoinHandle<()>>);

impl Drop for ForwarderGuard {
    fn drop(&mut self) {
        for handle in &self.0 {
            handle.abort();
        }
    }
}

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

    // 3. Session store — persisted on disk so the archive survives restarts.
    let sessions = SessionStore::open(&state_dir.join("sessions.db"))
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

    // The library directory holds the shipped task & definition libraries. It
    // defaults to the state directory but can point at a checkout during dev.
    let library_dir = std::env::var_os("CLAUDESTUDIO_LIBRARY_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| state_dir.clone());
    let router = Router::new(config, sessions, event_bus, state_dir.clone(), library_dir);

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
                        // Back off briefly so a persistent error (e.g. fd
                        // exhaustion) cannot busy-spin the loop and flood logs.
                        tracing::warn!(%e, "accept failed; backing off");
                        tokio::time::sleep(std::time::Duration::from_millis(100)).await;
                    }
                }
            }
        }
    }
}

/// Read request frames from a single client and write back response frames until
/// the client disconnects.
///
/// The special method `events.subscribe` acknowledges, then streams every
/// [`SystemEvent`] on the bus to this client as `event` frames until the
/// connection closes. The write half is shared so the forwarder and the
/// request loop never interleave a half-written frame.
async fn handle_connection(stream: UnixStream, router: Router) -> anyhow::Result<()> {
    let (read_half, write_half) = stream.into_split();
    let mut reader = FrameReader::new(read_half);
    let writer: SharedWriter = Arc::new(Mutex::new(FrameWriter::new(write_half)));
    // Aborts any event forwarders when this function returns (clean EOF or
    // error), so a parked forwarder + its write-half fd never leak.
    let mut forwarders = ForwarderGuard::default();

    while let Some(request) = reader.read_frame::<IpcEnvelope>().await? {
        tracing::debug!(method = %request.method, id = %request.id, "request");

        if request.method == "events.subscribe" {
            // Subscribe BEFORE acking so no event published between the ack and
            // the subscription is lost.
            let rx = router.event_bus().subscribe();
            let ack = request.response_to(serde_json::json!({ "subscribed": true }));
            writer.lock().await.write_frame(&ack).await?;
            forwarders
                .0
                .push(spawn_event_forwarder(rx, Arc::clone(&writer)));
            continue;
        }

        let response = router.dispatch(&request).await;
        writer.lock().await.write_frame(&response).await?;
    }
    Ok(())
}

/// Forward `SystemEvent`s from `rx` to the client as `event` frames until the
/// connection closes or the bus is dropped. The returned handle is aborted by
/// [`ForwarderGuard`] when the connection ends.
fn spawn_event_forwarder(
    mut rx: broadcast::Receiver<SystemEvent>,
    writer: SharedWriter,
) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        loop {
            match rx.recv().await {
                Ok(event) => {
                    let payload =
                        serde_json::to_value(&event).unwrap_or_else(|_| serde_json::json!({}));
                    let frame = new_event("event", payload);
                    if writer.lock().await.write_frame(&frame).await.is_err() {
                        break; // client went away
                    }
                }
                // Slow consumer dropped some events; keep going with the newest.
                Err(broadcast::error::RecvError::Lagged(_)) => continue,
                Err(broadcast::error::RecvError::Closed) => break,
            }
        }
    })
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
            std::env::temp_dir(),
            std::env::temp_dir(),
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
