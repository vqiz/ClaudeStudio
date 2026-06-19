//! The IPC method router.
//!
//! [`Router`] owns the shared application state — the loaded [`AppConfig`], the
//! [`SessionStore`], and the [`EventBus`] — and dispatches each incoming
//! [`IpcEnvelope`] request to a handler keyed by its `method` string.
//!
//! Handlers are deliberately small and synchronous; they return a JSON payload
//! that the connection loop wraps into a response envelope.

use std::sync::{Arc, Mutex};

use cs_agentic_os::EventBus;
use cs_config::{AppConfig, ContextAssembler, LayerKind};
use cs_ipc::IpcEnvelope;
use cs_sessions::SessionStore;

/// Shared, cloneable application state plus method dispatch.
///
/// The [`Router`] is cloned into every accepted connection task, so it must be
/// `Send + Sync`. The [`SessionStore`] wraps a `rusqlite::Connection`, which is
/// `Send` but not `Sync`, so it is held behind a [`Mutex`] to make the shared
/// state safe to move across the `tokio::spawn` boundary.
#[derive(Clone)]
pub struct Router {
    inner: Arc<Inner>,
}

struct Inner {
    config: AppConfig,
    #[allow(dead_code)] // held so the store lives for the server's lifetime
    sessions: Mutex<SessionStore>,
    event_bus: EventBus,
}

impl Router {
    /// Build a router from the loaded application components.
    pub fn new(config: AppConfig, sessions: SessionStore, event_bus: EventBus) -> Self {
        Self {
            inner: Arc::new(Inner {
                config,
                sessions: Mutex::new(sessions),
                event_bus,
            }),
        }
    }

    /// Dispatch a request envelope, returning the response envelope to send back.
    ///
    /// Unknown methods produce an error response rather than failing the
    /// connection. Each dispatched request also emits a `TaskOneClick`
    /// [`SystemEvent`] on the bus so subscribers (e.g. the Supervisor) can
    /// observe front-end activity; a lack of subscribers is not an error.
    pub fn dispatch(&self, request: &IpcEnvelope) -> IpcEnvelope {
        let _ = self
            .inner
            .event_bus
            .publish(cs_agentic_os::SystemEvent::TaskOneClick);

        match request.method.as_str() {
            "ping" => request.response_to(serde_json::json!({ "pong": true })),
            "config.get" => request.response_to(self.config_payload()),
            "context.budget" => request.response_to(self.budget_payload()),
            other => cs_ipc::error_response(request, format!("unknown method: {other}")),
        }
    }

    fn config_payload(&self) -> serde_json::Value {
        let cfg = &self.inner.config;
        serde_json::json!({
            "trust_mode": cfg.trust_mode,
            "default_model": cfg.default_model,
            "daily_budget_usd": cfg.daily_budget_usd,
            "context_token_budget": cfg.context_token_budget,
            "voice": cfg.voice,
            "vector": cfg.vector,
        })
    }

    fn budget_payload(&self) -> serde_json::Value {
        // Demo budget: real layer sizes are supplied by the orchestrator. Here we
        // show the assembler wiring with representative placeholder estimates.
        let budget = ContextAssembler::new(self.inner.config.context_token_budget)
            .with_layer(LayerKind::GlobalClaudeMd, 1_200)
            .with_layer(LayerKind::CrossProjectMemory, 3_000)
            .with_layer(LayerKind::ProjectClaudeMd, 2_400)
            .with_layer(LayerKind::VectorRetrieval, 6_000)
            .with_layer(LayerKind::ActiveDefinitions, 4_000)
            .with_layer(LayerKind::WorktreeOverride, 800)
            .assemble();

        let layers: Vec<serde_json::Value> = budget
            .layers
            .iter()
            .map(|l| {
                serde_json::json!({
                    "layer": l.kind.label(),
                    "requested_tokens": l.requested_tokens,
                    "granted_tokens": l.granted_tokens,
                    "truncated": l.was_truncated(),
                })
            })
            .collect();

        serde_json::json!({
            "total_budget": budget.total_budget,
            "granted_total": budget.granted_total(),
            "remaining": budget.remaining(),
            "layers": layers,
        })
    }
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
        )
    }

    #[test]
    fn ping_responds_pong() {
        let r = router();
        let req = new_request("ping", serde_json::json!({}));
        let res = r.dispatch(&req);
        assert_eq!(res.payload["pong"], serde_json::json!(true));
        assert_eq!(res.id, req.id);
    }

    #[test]
    fn unknown_method_is_error_response() {
        use cs_types::IpcKind;
        let r = router();
        let req = new_request("does.not.exist", serde_json::json!({}));
        let res = r.dispatch(&req);
        assert_eq!(res.kind, IpcKind::Error);
        assert!(res.payload.get("message").is_some());
        assert_eq!(res.id, req.id);
    }

    #[test]
    fn context_budget_reports_six_layers() {
        let r = router();
        let req = new_request("context.budget", serde_json::json!({}));
        let res = r.dispatch(&req);
        let layers = res.payload["layers"].as_array().expect("layers array");
        assert_eq!(layers.len(), 6);
    }
}
