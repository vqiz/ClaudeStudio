#![forbid(unsafe_code)]
//! # cs-types
//!
//! Canonical, cross-crate type definitions for the ClaudeStudio Rust core.
//!
//! Every other crate in the workspace imports the enums and structs declared
//! here rather than redefining them. Keeping a single source of truth avoids
//! serialization drift between the SwiftUI front-end and the Rust sidecar.
//!
//! All public types derive `serde::Serialize`/`Deserialize` so they can travel
//! across the MessagePack IPC boundary (see the `cs-ipc` crate).

use serde::{Deserialize, Serialize};

/// Errors produced by helpers in this crate.
///
/// The shared type definitions themselves are largely infallible; this enum
/// exists so that `cs-types` follows the workspace convention of exposing a
/// `thiserror`-based [`Error`] and [`Result`].
#[derive(Debug, thiserror::Error)]
pub enum Error {
    /// A value could not be (de)serialized into/from JSON.
    #[error("serialization error: {0}")]
    Serde(#[from] serde_json::Error),

    /// A string could not be parsed into the requested type.
    #[error("invalid value: {0}")]
    InvalidValue(String),
}

/// Convenience result type used throughout this crate.
pub type Result<T> = std::result::Result<T, Error>;

/// How aggressively ClaudeStudio is permitted to act without human confirmation.
///
/// Ordered loosely from most cautious ([`TrustMode::Strict`]) to fully
/// autonomous ([`TrustMode::Yolo`]).
#[derive(Copy, Clone, Debug, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum TrustMode {
    /// Confirm every potentially mutating action.
    Strict,
    /// The default balance: confirm risky actions, auto-approve safe ones.
    #[default]
    Standard,
    /// Auto-approve most actions, pausing only on destructive operations.
    Auto,
    /// Approve everything; no confirmation prompts. Use with care.
    Yolo,
}

/// The Claude model tier selected for a given task.
#[derive(Copy, Clone, Debug, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum ModelTier {
    /// Fastest and cheapest; good for routine or background work.
    Haiku,
    /// Balanced default for interactive coding sessions.
    #[default]
    Sonnet,
    /// Most capable; reserved for the hardest reasoning tasks.
    Opus,
}

/// Scheduling priority for agentic tasks on the event bus.
///
/// Ordered so that [`Priority::Critical`] is the greatest, allowing priority
/// queues to pop the most urgent work first.
#[derive(Ord, PartialOrd, Eq, PartialEq, Copy, Clone, Debug, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum Priority {
    /// Lowest priority; runs only when nothing else is pending.
    Background,
    /// Default priority for ordinary work.
    #[default]
    Normal,
    /// Elevated priority; preempts normal work.
    High,
    /// Highest priority; runs ahead of everything else.
    Critical,
}

/// Lifecycle state of an agent within the agentic layer.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum AgentStatus {
    /// Created but not currently doing work.
    #[default]
    Idle,
    /// Actively executing.
    Running,
    /// Blocked awaiting user input or approval.
    WaitingForInput,
    /// Finished successfully.
    Done,
    /// Finished with an error.
    Error,
}

/// The category of an IPC message exchanged with the front-end.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum IpcKind {
    /// A client-initiated request expecting a matching response.
    Request,
    /// A reply to a previous [`IpcKind::Request`].
    Response,
    /// A fire-and-forget event broadcast (no response expected).
    Event,
    /// An error correlated to a previous [`IpcKind::Request`] by `id`. Its
    /// payload carries `{ "code": <int>, "message": <string> }`. The SwiftUI
    /// client surfaces this as a thrown error rather than a successful response.
    Error,
}

/// The envelope wrapping every message on the IPC channel.
///
/// `payload` is a free-form [`serde_json::Value`] so the protocol can evolve
/// without changing the envelope. The matching `id` ties a [`IpcKind::Response`]
/// back to its originating [`IpcKind::Request`].
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct IpcEnvelope {
    /// Correlation identifier (typically a UUID).
    pub id: String,
    /// The category of this message.
    pub kind: IpcKind,
    /// The RPC method name, e.g. `"ping"` or `"config.get"`.
    pub method: String,
    /// Method-specific arguments or result data.
    pub payload: serde_json::Value,
}

impl IpcEnvelope {
    /// Construct a request envelope with the given id, method, and payload.
    pub fn request(
        id: impl Into<String>,
        method: impl Into<String>,
        payload: serde_json::Value,
    ) -> Self {
        Self {
            id: id.into(),
            kind: IpcKind::Request,
            method: method.into(),
            payload,
        }
    }

    /// Construct a response that correlates with `self` via its `id`/`method`.
    pub fn response_to(&self, payload: serde_json::Value) -> Self {
        Self {
            id: self.id.clone(),
            kind: IpcKind::Response,
            method: self.method.clone(),
            payload,
        }
    }

    /// Construct an [`IpcKind::Error`] envelope correlated to `self`, packing the
    /// failure into the conventional `{ "code", "message" }` payload the
    /// front-end expects.
    pub fn error_to(&self, code: i64, message: impl Into<String>) -> Self {
        Self {
            id: self.id.clone(),
            kind: IpcKind::Error,
            method: self.method.clone(),
            payload: serde_json::json!({ "code": code, "message": message.into() }),
        }
    }

    /// Construct an event envelope (no correlation id required).
    pub fn event(
        id: impl Into<String>,
        method: impl Into<String>,
        payload: serde_json::Value,
    ) -> Self {
        Self {
            id: id.into(),
            kind: IpcKind::Event,
            method: method.into(),
            payload,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn priority_orders_critical_highest() {
        assert!(Priority::Critical > Priority::High);
        assert!(Priority::High > Priority::Normal);
        assert!(Priority::Normal > Priority::Background);
        let mut v = vec![Priority::Normal, Priority::Critical, Priority::Background];
        v.sort();
        assert_eq!(
            v,
            vec![Priority::Background, Priority::Normal, Priority::Critical]
        );
    }

    #[test]
    fn enums_serialize_lowercase() {
        assert_eq!(serde_json::to_string(&TrustMode::Yolo).unwrap(), "\"yolo\"");
        assert_eq!(serde_json::to_string(&ModelTier::Opus).unwrap(), "\"opus\"");
        assert_eq!(
            serde_json::to_string(&IpcKind::Request).unwrap(),
            "\"request\""
        );
        assert_eq!(
            serde_json::to_string(&AgentStatus::WaitingForInput).unwrap(),
            "\"waitingforinput\""
        );
    }

    #[test]
    fn envelope_response_preserves_correlation() {
        let req = IpcEnvelope::request("abc", "ping", serde_json::json!({}));
        let res = req.response_to(serde_json::json!({"ok": true}));
        assert_eq!(res.id, "abc");
        assert_eq!(res.method, "ping");
        assert_eq!(res.kind, IpcKind::Response);
    }

    #[test]
    fn envelope_error_carries_code_and_message() {
        let req = IpcEnvelope::request("abc", "session.start", serde_json::json!({}));
        let err = req.error_to(-1, "boom");
        assert_eq!(err.id, "abc");
        assert_eq!(err.method, "session.start");
        assert_eq!(err.kind, IpcKind::Error);
        assert_eq!(err.payload["code"], serde_json::json!(-1));
        assert_eq!(err.payload["message"], serde_json::json!("boom"));
    }

    #[test]
    fn error_kind_serializes_lowercase() {
        assert_eq!(serde_json::to_string(&IpcKind::Error).unwrap(), "\"error\"");
    }
}
