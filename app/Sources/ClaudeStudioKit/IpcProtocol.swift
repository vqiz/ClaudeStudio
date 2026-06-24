import Foundation

/// Wire protocol shared with the Rust core sidecar.
///
/// The core and the app communicate over a Unix domain socket using
/// length-prefixed MessagePack frames:
///
/// ```text
/// ┌──────────────┬───────────────────────────────┐
/// │ u32 (BE) len │ MessagePack-encoded IpcEnvelope │
/// └──────────────┴───────────────────────────────┘
/// ```
///
/// `IpcEnvelope` mirrors the Rust `IpcEnvelope { id, kind, method, payload }`
/// struct so both sides decode identically.
public enum IpcProtocol {
    /// Default socket path under the user's home directory. The Rust core
    /// publishes its socket here; both sides agree on this location.
    public static let defaultSocketPath: String = {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return "\(home)/.claudestudio/core.sock"
    }()

    /// Maximum frame size we will read before assuming a desync (16 MiB).
    public static let maxFrameBytes = 16 * 1024 * 1024
}

/// The category of an IPC message. Mirrors the Rust `IpcKind`.
public enum IpcKind: String, Codable, Sendable, CaseIterable {
    /// A request that expects a matching `response`.
    case request
    /// A response correlated to a previous `request` by `id`.
    case response
    /// A fire-and-forget notification (no response expected).
    case event
    /// An error correlated to a `request` by `id`.
    case error
}

/// The envelope exchanged over the socket. `payload` is left as a flexible
/// MessagePack value so the transport layer never needs to know about every
/// concrete method payload — higher layers decode it on demand.
public struct IpcEnvelope: Codable, Sendable, Equatable {
    /// Correlation id. Requests generate one; responses echo it back.
    public var id: String
    /// Message category.
    public var kind: IpcKind
    /// RPC method name, e.g. `"session.start"`, `"supervisor.tick"`.
    public var method: String
    /// Free-form payload. `nil` when a method takes no arguments.
    public var payload: MsgPackValue?

    public init(id: String = UUID().uuidString,
                kind: IpcKind,
                method: String,
                payload: MsgPackValue? = nil) {
        self.id = id
        self.kind = kind
        self.method = method
        self.payload = payload
    }

    /// Convenience constructor for an outbound request.
    public static func request(method: String, payload: MsgPackValue? = nil) -> IpcEnvelope {
        IpcEnvelope(kind: .request, method: method, payload: payload)
    }
}

/// The error taxonomy shared with the Rust core (`cs_ipc::ErrorCode`).
///
/// A `error` envelope carries one of these numeric codes in its
/// `{ "code", "message" }` payload so the UI can branch on the *kind* of failure
/// (e.g. prompt re-auth on `.configError`, show a "not found" empty state on
/// `.notFound`) instead of string-matching the message. `.generic`/`.unknown`
/// cover legacy `-1` and any unrecognized code so the mapping never traps.
public enum IpcErrorCode: Int, Sendable, Equatable {
    /// Legacy/uncategorized failure (wire value `-1`).
    case generic = -1
    /// A required parameter was missing or malformed (caller error).
    case invalidParameter = 400
    /// The requested entity does not exist.
    case notFound = 404
    /// A configuration file could not be read, parsed, or written.
    case configError = 451
    /// A session/transcript operation failed.
    case sessionError = 452
    /// An unexpected internal failure in the core.
    case internalError = 500

    /// Map a raw wire code to a taxonomy case, falling back to `.generic` for
    /// anything unrecognized so callers always get a value.
    public init(wire: Int) {
        self = IpcErrorCode(rawValue: wire) ?? .generic
    }
}

/// Errors surfaced by the IPC client.
public enum IpcError: Error, Sendable, Equatable {
    case notConnected
    case connectionFailed(String)
    case frameTooLarge(Int)
    case decodeFailed(String)
    case socketClosed
    case remote(code: Int, message: String)
    /// A request was sent but the core did not reply within the deadline.
    case timedOut(method: String)

    /// The taxonomy code for a `.remote` failure, so callers can `switch` on the
    /// kind rather than parse the message. `nil` for non-remote errors (transport
    /// failures, decode failures, timeouts) which have no server-assigned code.
    public var remoteCode: IpcErrorCode? {
        guard case let .remote(code, _) = self else { return nil }
        return IpcErrorCode(wire: code)
    }
}
