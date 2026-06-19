import Foundation

/// A typed snapshot of the Rust core's `config.get` response.
///
/// Field names mirror `cs_config::AppConfig` as serialized by the sidecar. The
/// `trustMode` and `defaultModel` strings use the core's lowercase identifiers
/// (e.g. `"standard"`, `"sonnet"`); the SwiftUI layer maps `trustMode` onto its
/// own `TrustMode` enum via `TrustMode(coreValue:)`.
public struct CoreConfig: Sendable, Equatable {
    public var trustMode: String
    public var defaultModel: String
    public var dailyBudgetUSD: Double
    public var contextTokenBudget: Int
    public var voiceEnabled: Bool
    public var vectorCollection: String?

    public init(
        trustMode: String,
        defaultModel: String,
        dailyBudgetUSD: Double,
        contextTokenBudget: Int,
        voiceEnabled: Bool,
        vectorCollection: String?
    ) {
        self.trustMode = trustMode
        self.defaultModel = defaultModel
        self.dailyBudgetUSD = dailyBudgetUSD
        self.contextTokenBudget = contextTokenBudget
        self.voiceEnabled = voiceEnabled
        self.vectorCollection = vectorCollection
    }

    /// Decode from the `config.get` payload. Returns `nil` if the required
    /// `trust_mode` / `default_model` fields are missing or mistyped.
    public init?(payload: MsgPackValue) {
        guard let trust = payload["trust_mode"]?.stringValue,
              let model = payload["default_model"]?.stringValue else {
            return nil
        }
        self.trustMode = trust
        self.defaultModel = model
        self.dailyBudgetUSD = payload["daily_budget_usd"]?.doubleValue ?? 0
        self.contextTokenBudget = Int(payload["context_token_budget"]?.intValue ?? 0)
        self.voiceEnabled = payload["voice"]?["enabled"]?.boolValue ?? false
        self.vectorCollection = payload["vector"]?["collection"]?.stringValue
    }
}

/// One layer of the six-layer context budget reported by `context.budget`.
public struct ContextLayer: Sendable, Equatable, Identifiable {
    public var label: String
    public var requestedTokens: Int
    public var grantedTokens: Int
    public var truncated: Bool

    public var id: String { label }

    public init(label: String, requestedTokens: Int, grantedTokens: Int, truncated: Bool) {
        self.label = label
        self.requestedTokens = requestedTokens
        self.grantedTokens = grantedTokens
        self.truncated = truncated
    }

    /// Decode a single `layers[]` entry. Returns `nil` if it lacks a `layer` label.
    public init?(value: MsgPackValue) {
        guard let label = value["layer"]?.stringValue else { return nil }
        self.label = label
        self.requestedTokens = Int(value["requested_tokens"]?.intValue ?? 0)
        self.grantedTokens = Int(value["granted_tokens"]?.intValue ?? 0)
        self.truncated = value["truncated"]?.boolValue ?? false
    }
}

/// A typed snapshot of the core's `context.budget` response — the assembled
/// per-layer token allocation the orchestrator will use for the next prompt.
public struct ContextBudget: Sendable, Equatable {
    public var totalBudget: Int
    public var grantedTotal: Int
    public var remaining: Int
    public var layers: [ContextLayer]

    public init(totalBudget: Int, grantedTotal: Int, remaining: Int, layers: [ContextLayer]) {
        self.totalBudget = totalBudget
        self.grantedTotal = grantedTotal
        self.remaining = remaining
        self.layers = layers
    }

    /// Decode from the `context.budget` payload. Returns `nil` if the `layers`
    /// array is absent.
    public init?(payload: MsgPackValue) {
        guard let rawLayers = payload["layers"]?.arrayValue else { return nil }
        self.totalBudget = Int(payload["total_budget"]?.intValue ?? 0)
        self.grantedTotal = Int(payload["granted_total"]?.intValue ?? 0)
        self.remaining = Int(payload["remaining"]?.intValue ?? 0)
        self.layers = rawLayers.compactMap(ContextLayer.init(value:))
    }
}

/// A small, typed facade over [`IpcClient`] exposing the Rust core's RPC surface
/// as `async` Swift methods. It owns the underlying connection actor and decodes
/// MessagePack payloads into the value types above so the UI layer never touches
/// the wire format directly.
public final class CoreClient: Sendable {
    /// The Unix-socket path this client dials.
    public let socketPath: String
    private let client: IpcClient

    public init(socketPath: String = IpcProtocol.defaultSocketPath) {
        self.socketPath = socketPath
        self.client = IpcClient(socketPath: socketPath)
    }

    /// Open the socket and start the read loop. Throws `IpcError.connectionFailed`
    /// when the core is not listening.
    public func connect() async throws {
        try await client.connect()
    }

    /// Close the socket and fail any in-flight requests.
    public func disconnect() async {
        await client.disconnect()
    }

    /// Current transport state.
    public var connectionState: IpcClient.State {
        get async { await client.state }
    }

    /// Send a request and await the correlated response, throwing
    /// `IpcError.remote` if the core replies with an error envelope.
    @discardableResult
    public func call(_ method: String, _ payload: MsgPackValue? = nil) async throws -> IpcEnvelope {
        try await client.send(.request(method: method, payload: payload))
    }

    /// Liveness probe — returns `true` when the core answers `ping` with `pong`.
    public func ping() async throws -> Bool {
        let response = try await call("ping")
        return response.payload?["pong"]?.boolValue ?? false
    }

    /// Fetch the effective core configuration.
    public func fetchConfig() async throws -> CoreConfig {
        let response = try await call("config.get")
        guard let payload = response.payload, let config = CoreConfig(payload: payload) else {
            throw IpcError.decodeFailed("config.get: malformed payload")
        }
        return config
    }

    /// Fetch the assembled six-layer context budget.
    public func fetchContextBudget() async throws -> ContextBudget {
        let response = try await call("context.budget")
        guard let payload = response.payload, let budget = ContextBudget(payload: payload) else {
            throw IpcError.decodeFailed("context.budget: malformed payload")
        }
        return budget
    }
}
