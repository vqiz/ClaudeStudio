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

/// Build an [`IpcKind::Error`](cs_types::IpcKind::Error) envelope correlated to
/// `request`, packing the failure into the `{ "code", "message" }` payload the
/// SwiftUI client decodes into a thrown `IpcError.remote`.
pub fn error_response(request: &IpcEnvelope, message: impl Into<String>) -> IpcEnvelope {
    request.error_to(GENERIC_ERROR_CODE, message)
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
