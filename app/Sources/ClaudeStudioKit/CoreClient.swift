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

/// A persisted session from the archive (`session.list` / `session.get`).
public struct CoreSession: Sendable, Identifiable, Equatable {
    public let id: String
    public let title: String
    public let cwd: String
    public let branch: String?
    public let model: String?
    public let createdAt: Int
    public let updatedAt: Int

    /// Wall-clock creation time (millis → `Date`).
    public var createdDate: Date { Date(timeIntervalSince1970: Double(createdAt) / 1000) }

    public init?(value: MsgPackValue) {
        guard let id = value["id"]?.stringValue,
              let title = value["title"]?.stringValue else { return nil }
        self.id = id
        self.title = title
        self.cwd = value["cwd"]?.stringValue ?? ""
        self.branch = value["branch"]?.stringValue
        self.model = value["model"]?.stringValue
        self.createdAt = Int(value["created_at"]?.intValue ?? 0)
        self.updatedAt = Int(value["updated_at"]?.intValue ?? 0)
    }
}

/// A task from the shipped Task Library (`tasks.list`).
public struct LibraryTask: Sendable, Identifiable, Equatable {
    public var id: String { path }
    public let path: String
    public let name: String
    public let category: String
    public let summary: String
    public let tags: [String]

    public init?(value: MsgPackValue) {
        guard let path = value["path"]?.stringValue else { return nil }
        self.path = path
        self.name = value["name"]?.stringValue ?? "Untitled task"
        self.category = value["category"]?.stringValue ?? ""
        self.summary = value["description"]?.stringValue ?? ""
        self.tags = (value["tags"]?.arrayValue ?? []).compactMap { $0.stringValue }
    }
}

/// A definition from the Definition Library (`definitions.list`).
public struct LibraryDefinition: Sendable, Identifiable, Equatable {
    public var id: String { path }
    public let path: String
    public let name: String
    public let category: String
    public let scope: String

    public init?(value: MsgPackValue) {
        guard let path = value["path"]?.stringValue else { return nil }
        self.path = path
        self.name = value["name"]?.stringValue ?? ""
        self.category = value["category"]?.stringValue ?? ""
        self.scope = value["scope"]?.stringValue ?? ""
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

    /// Server-pushed `event` envelopes (e.g. supervisor / event-bus ticks). Call
    /// ``subscribeEvents()`` first; then `for await` over this stream.
    public var events: AsyncStream<IpcEnvelope> {
        client.events
    }

    /// Ask the core to start streaming `SystemEvent`s on this connection.
    public func subscribeEvents() async throws {
        _ = try await call("events.subscribe")
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

    /// Update one or more configuration fields, persisting them, and return the
    /// new effective config. Pass only the fields you want to change.
    public func setConfig(
        trustMode: String? = nil,
        defaultModel: String? = nil,
        dailyBudgetUSD: Double? = nil,
        contextTokenBudget: Int? = nil
    ) async throws -> CoreConfig {
        var payload: [String: MsgPackValue] = [:]
        if let trustMode { payload["trust_mode"] = .string(trustMode) }
        if let defaultModel { payload["default_model"] = .string(defaultModel) }
        if let dailyBudgetUSD { payload["daily_budget_usd"] = .double(dailyBudgetUSD) }
        if let contextTokenBudget { payload["context_token_budget"] = .int(Int64(contextTokenBudget)) }
        let response = try await call("config.set", .map(payload))
        guard let p = response.payload, let config = CoreConfig(payload: p) else {
            throw IpcError.decodeFailed("config.set: malformed payload")
        }
        return config
    }

    /// List archived sessions, newest first.
    public func listSessions(limit: Int = 100, offset: Int = 0) async throws -> [CoreSession] {
        let response = try await call("session.list", .map([
            "limit": .int(Int64(limit)),
            "offset": .int(Int64(offset)),
        ]))
        let rows = response.payload?["sessions"]?.arrayValue ?? []
        return rows.compactMap(CoreSession.init(value:))
    }

    /// Load the shipped Task Library.
    public func fetchTasks() async throws -> [LibraryTask] {
        let response = try await call("tasks.list")
        let rows = response.payload?["tasks"]?.arrayValue ?? []
        return rows.compactMap(LibraryTask.init(value:))
    }

    /// Load the shipped Definition Library.
    public func fetchDefinitions() async throws -> [LibraryDefinition] {
        let response = try await call("definitions.list")
        let rows = response.payload?["definitions"]?.arrayValue ?? []
        return rows.compactMap(LibraryDefinition.init(value:))
    }
}
