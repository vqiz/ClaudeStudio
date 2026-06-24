//! Constructors for [`IpcEnvelope`] that generate correlation ids automatically.
//!
//! The bare envelope type lives in `cs-types`; these helpers add ergonomic,
//! UUID-stamped builders used by both the front-end bridge and the router.

use cs_types::IpcEnvelope;

/// Build a [`IpcEnvelope`] request with a freshly generated UUID `id`.
pub fn new_request(method: impl Into<String>, payload: serde_json::Value) -> IpcEnvelope {
    IpcEnvelope::request(uuid::Uuid::new_v4().to_string(), method, payload)
}

/// Build a [`IpcEnvelope`] event with a freshly generated UUID `id`.
pub fn new_event(method: impl Into<String>, payload: serde_json::Value) -> IpcEnvelope {
    IpcEnvelope::event(uuid::Uuid::new_v4().to_string(), method, payload)
}

/// Generic error code used when a handler reports a failure without a more
/// specific code. Mirrors the front-end's `-1` fallback.
pub const GENERIC_ERROR_CODE: i64 = -1;

/// The error taxonomy shared across the IPC boundary.
///
/// Every handler failure carries one of these codes in the `{ "code", "message" }`
/// error payload so the SwiftUI client can branch on the *kind* of failure rather
/// than parsing the human-readable message. The numeric values intentionally
/// echo familiar HTTP semantics (4xx = caller's fault, 5xx = core's fault) so
/// they read naturally in logs.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ErrorCode {
    /// A required parameter was missing or malformed (caller error).
    InvalidParameter = 400,
    /// The requested entity does not exist.
    NotFound = 404,
    /// A configuration file could not be read, parsed, or written.
    ConfigError = 451,
    /// A session/transcript operation failed (DB, embedding, lifecycle).
    SessionError = 452,
    /// An unexpected internal failure (the catch-all).
    Internal = 500,
}

impl ErrorCode {
    /// The wire value carried in the error payload's `code` field.
    #[must_use]
    pub const fn as_i64(self) -> i64 {
        self as i64
    }
}

/// A typed handler failure: a taxonomy [`ErrorCode`] plus a human-readable
/// message. Handlers return `Result<Value, IpcFailure>`; [`error_response_typed`]
/// turns the failure into the wire `{ code, message }` payload.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct IpcFailure {
    /// The taxonomy code the front-end branches on.
    pub code: ErrorCode,
    /// The human-readable detail (shown in logs and, if needed, the UI).
    pub message: String,
}

impl IpcFailure {
    /// Construct a failure with an explicit code.
    pub fn new(code: ErrorCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }

    /// Shorthand for an [`ErrorCode::InvalidParameter`] failure.
    pub fn invalid(message: impl Into<String>) -> Self {
        Self::new(ErrorCode::InvalidParameter, message)
    }

    /// Shorthand for an [`ErrorCode::NotFound`] failure.
    pub fn not_found(message: impl Into<String>) -> Self {
        Self::new(ErrorCode::NotFound, message)
    }

    /// Shorthand for an [`ErrorCode::ConfigError`] failure.
    pub fn config(message: impl Into<String>) -> Self {
        Self::new(ErrorCode::ConfigError, message)
    }

    /// Shorthand for an [`ErrorCode::SessionError`] failure.
    pub fn session(message: impl Into<String>) -> Self {
        Self::new(ErrorCode::SessionError, message)
    }

    /// Shorthand for an [`ErrorCode::Internal`] failure.
    pub fn internal(message: impl Into<String>) -> Self {
        Self::new(ErrorCode::Internal, message)
    }
}

impl std::fmt::Display for IpcFailure {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "[{}] {}", self.code.as_i64(), self.message)
    }
}

/// A bare `String` error (legacy handler signature) maps to
/// [`ErrorCode::Internal`] — the safe default for an un-categorized failure.
impl From<String> for IpcFailure {
    fn from(message: String) -> Self {
        Self::internal(message)
    }
}

impl From<&str> for IpcFailure {
    fn from(message: &str) -> Self {
        Self::internal(message.to_string())
    }
}

/// Build an [`IpcKind::Error`](cs_types::IpcKind::Error) envelope correlated to
/// `request`, packing the failure into the `{ "code", "message" }` payload the
/// SwiftUI client decodes into a thrown `IpcError.remote`. Uses the generic code;
/// prefer [`error_response_typed`] so the front-end can branch on the kind.
pub fn error_response(request: &IpcEnvelope, message: impl Into<String>) -> IpcEnvelope {
    request.error_to(GENERIC_ERROR_CODE, message)
}

/// Build a typed [`IpcKind::Error`](cs_types::IpcKind::Error) envelope from an
/// [`IpcFailure`], carrying the taxonomy code so the client can branch on it.
pub fn error_response_typed(request: &IpcEnvelope, failure: &IpcFailure) -> IpcEnvelope {
    request.error_to(failure.code.as_i64(), failure.message.clone())
}

#[cfg(test)]
mod tests {
    use super::*;
    use cs_types::IpcKind;

    #[test]
    fn new_request_has_uuid_id() {
        let env = new_request("ping", serde_json::json!({}));
        assert_eq!(env.kind, IpcKind::Request);
        // UUID v4 string length is 36 characters.
        assert_eq!(env.id.len(), 36);
    }

    #[test]
    fn error_response_correlates_and_packs_message() {
        let req = new_request("config.get", serde_json::json!({}));
        let res = error_response(&req, "boom");
        assert_eq!(res.id, req.id);
        assert_eq!(res.kind, IpcKind::Error);
        assert_eq!(res.payload["code"], serde_json::json!(GENERIC_ERROR_CODE));
        assert_eq!(res.payload["message"], serde_json::json!("boom"));
    }
}
