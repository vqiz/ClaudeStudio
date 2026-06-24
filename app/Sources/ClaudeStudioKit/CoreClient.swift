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

    /// Decode from the `config.get` payload. Returns `nil` if **any** field the
    /// core is contractually required to send is missing or mistyped —
    /// `trust_mode`, `default_model`, `daily_budget_usd`, and
    /// `context_token_budget`.
    ///
    /// The numeric fields are required (not defaulted to 0) on purpose: a
    /// silently-substituted `0` here would be written straight back to the core
    /// on the next `config.set`, clobbering the user's real daily budget / token
    /// budget (the A17 round-trip overwrite bug). Failing the decode instead
    /// surfaces the protocol mismatch loudly rather than corrupting settings.
    public init?(payload: MsgPackValue) {
        guard let trust = payload["trust_mode"]?.stringValue,
              let model = payload["default_model"]?.stringValue,
              let dailyBudget = payload["daily_budget_usd"]?.doubleValue,
              let tokenBudget = payload["context_token_budget"]?.intValue else {
            return nil
        }
        self.trustMode = trust
        self.defaultModel = model
        self.dailyBudgetUSD = dailyBudget
        self.contextTokenBudget = Int(tokenBudget)
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
    /// The `claude` CLI's session id, if captured — non-nil means this archived
    /// conversation can be continued via `--resume`.
    public let claudeSessionId: String?

    /// Wall-clock creation time (millis → `Date`).
    public var createdDate: Date { Date(timeIntervalSince1970: Double(createdAt) / 1000) }
    /// Whether this session can be resumed.
    public var isResumable: Bool { !(claudeSessionId ?? "").isEmpty }

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
        self.claudeSessionId = value["claude_session_id"]?.stringValue
    }
}

/// A task from the Task Library (`tasks.list`).
public struct LibraryTask: Sendable, Identifiable, Equatable {
    public var id: String { path }
    public let path: String
    public let name: String
    public let category: String
    public let summary: String
    public let tags: [String]
    /// True for items in the user's writable library; false for shipped items.
    public let writable: Bool

    public init?(value: MsgPackValue) {
        guard let path = value["path"]?.stringValue else { return nil }
        self.path = path
        self.name = value["name"]?.stringValue ?? "Untitled task"
        self.category = value["category"]?.stringValue ?? ""
        self.summary = value["description"]?.stringValue ?? ""
        self.tags = (value["tags"]?.arrayValue ?? []).compactMap { $0.stringValue }
        self.writable = value["writable"]?.boolValue ?? false
    }
}

/// A definition from the Definition Library (`definitions.list`).
public struct LibraryDefinition: Sendable, Identifiable, Equatable {
    public var id: String { path }
    public let path: String
    public let name: String
    public let category: String
    public let scope: String
    /// True for items in the user's writable library; false for shipped items.
    public let writable: Bool

    public init?(value: MsgPackValue) {
        guard let path = value["path"]?.stringValue else { return nil }
        self.path = path
        self.name = value["name"]?.stringValue ?? ""
        self.category = value["category"]?.stringValue ?? ""
        self.scope = value["scope"]?.stringValue ?? ""
        self.writable = value["writable"]?.boolValue ?? false
    }
}

/// An installed skill discovered in a project (`skills.list`), invoked as a
/// `/<command>` slash command.
public struct LibrarySkill: Sendable, Identifiable, Equatable {
    public var id: String { command + "@" + scope }
    /// The slash-command token (the skill's directory name).
    public let command: String
    public let name: String
    public let description: String
    public let path: String
    /// `"project"` or `"user"`.
    public let scope: String

    public init?(value: MsgPackValue) {
        guard let command = value["command"]?.stringValue else { return nil }
        self.command = command
        self.name = value["name"]?.stringValue ?? command
        self.description = value["description"]?.stringValue ?? ""
        self.path = value["path"]?.stringValue ?? ""
        self.scope = value["scope"]?.stringValue ?? ""
    }
}

/// An installed Claude Code plugin (`plugins.list`, via the `claude` CLI).
public struct Plugin: Sendable, Identifiable, Equatable {
    public var id: String { fullId }
    /// The full `name@marketplace` identifier.
    public let fullId: String
    public let name: String
    public let marketplace: String
    public let version: String
    public let scope: String
    public let enabled: Bool
    /// Whether the plugin ships an MCP server.
    public let hasMcp: Bool

    public init?(value: MsgPackValue) {
        guard let id = value["id"]?.stringValue else { return nil }
        self.fullId = id
        self.name = value["name"]?.stringValue ?? id
        self.marketplace = value["marketplace"]?.stringValue ?? ""
        self.version = value["version"]?.stringValue ?? "unknown"
        self.scope = value["scope"]?.stringValue ?? "user"
        self.enabled = value["enabled"]?.boolValue ?? false
        self.hasMcp = value["has_mcp"]?.boolValue ?? false
    }
}

/// A configured plugin marketplace (`plugins.marketplace_list`).
public struct PluginMarketplace: Sendable, Identifiable, Equatable {
    public var id: String { name }
    public let name: String
    public let source: String
    public let repo: String

    public init?(value: MsgPackValue) {
        guard let name = value["name"]?.stringValue else { return nil }
        self.name = name
        self.source = value["source"]?.stringValue ?? ""
        self.repo = value["repo"]?.stringValue ?? ""
    }
}

/// A configured MCP server from the Claude config (`mcp.list`).
public struct McpServer: Sendable, Identifiable, Equatable {
    public var id: String { scope + "/" + name }
    public let name: String
    /// Transport kind: `"stdio"`, `"sse"`, or `"http"`.
    public let transport: String
    /// The command (stdio) or URL (sse/http).
    public let target: String
    /// Visibility scope: `"project"` or `"user"` (empty for CLI-listed servers).
    public let scope: String
    /// Arguments for a stdio server.
    public let args: [String]
    /// Environment variables for a stdio server.
    public let env: [String: String]
    /// The endpoint URL for an sse/http server.
    public let url: String
    /// Live connection status from `claude mcp list`: `connected`, `failed`,
    /// `needs-auth`, `pending`, `unknown`, or empty (file-only listing).
    public let status: String

    public init?(value: MsgPackValue) {
        guard let name = value["name"]?.stringValue else { return nil }
        self.name = name
        self.transport = value["transport"]?.stringValue ?? ""
        self.target = value["target"]?.stringValue ?? ""
        self.scope = value["scope"]?.stringValue ?? ""
        self.args = (value["args"]?.arrayValue ?? []).compactMap { $0.stringValue }
        var env: [String: String] = [:]
        for (k, v) in value["env"]?.mapValue ?? [:] {
            if let s = v.stringValue { env[k] = s }
        }
        self.env = env
        self.url = value["url"]?.stringValue ?? ""
        self.status = value["status"]?.stringValue ?? ""
    }

    public init(name: String, transport: String, target: String, scope: String,
                args: [String] = [], env: [String: String] = [:], url: String = "",
                status: String = "") {
        self.name = name
        self.transport = transport
        self.target = target
        self.scope = scope
        self.args = args
        self.env = env
        self.url = url
        self.status = status
    }
}

/// A configured Claude hook from `settings.json` (`hooks.list`).
public struct CoreHook: Sendable, Identifiable, Equatable {
    public var id: String { "\(event)|\(matcher)|\(command)|\(source)" }
    public let event: String
    public let matcher: String
    public let command: String
    public let source: String

    public init?(value: MsgPackValue) {
        guard let event = value["event"]?.stringValue else { return nil }
        self.event = event
        self.matcher = value["matcher"]?.stringValue ?? "*"
        self.command = value["command"]?.stringValue ?? ""
        self.source = value["source"]?.stringValue ?? ""
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

    /// The stored transcript of a session (oldest first) as `(role, content)`.
    public func fetchSessionMessages(id: String) async throws -> [(role: String, content: String)] {
        let response = try await call("session.messages", .map(["id": .string(id)]))
        let rows = response.payload?["messages"]?.arrayValue ?? []
        return rows.compactMap { row in
            guard let role = row["role"]?.stringValue,
                  let content = row["content"]?.stringValue else { return nil }
            return (role, content)
        }
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

    /// Create a new, editable task in the user library. Returns its file path.
    public func createTask(name: String) async throws -> String {
        let response = try await call("tasks.create", .map(["name": .string(name)]))
        return response.payload?["path"]?.stringValue ?? ""
    }

    /// Delete a user-library task by path (shipped tasks are protected).
    @discardableResult
    public func deleteTask(path: String) async throws -> Bool {
        let response = try await call("tasks.delete", .map(["path": .string(path)]))
        return response.payload?["ok"]?.boolValue ?? false
    }

    /// Create a new, editable definition in the user library. Returns its path.
    public func createDefinition(name: String) async throws -> String {
        let response = try await call("definitions.create", .map(["name": .string(name)]))
        return response.payload?["path"]?.stringValue ?? ""
    }

    /// Copy the shipped default tasks & definitions into the user's editable
    /// library (idempotent). Returns how many of each were newly added.
    @discardableResult
    public func loadDefaultTemplates() async throws -> (tasks: Int, definitions: Int) {
        let response = try await call("library.load_defaults")
        return (Int(response.payload?["tasks"]?.intValue ?? 0),
                Int(response.payload?["definitions"]?.intValue ?? 0))
    }

    /// Delete a user-library definition by path (shipped ones are protected).
    @discardableResult
    public func deleteDefinition(path: String) async throws -> Bool {
        let response = try await call("definitions.delete", .map(["path": .string(path)]))
        return response.payload?["ok"]?.boolValue ?? false
    }

    /// List installed skills for a project (and the user's global skills).
    public func fetchSkills(cwd: String? = nil) async throws -> [LibrarySkill] {
        var payload: [String: MsgPackValue] = [:]
        if let cwd { payload["cwd"] = .string(cwd) }
        let response = try await call("skills.list", .map(payload))
        let rows = response.payload?["skills"]?.arrayValue ?? []
        return rows.compactMap(LibrarySkill.init(value:))
    }

    /// Scaffold a new skill in the given scope (`project` needs `cwd`). Returns
    /// the new SKILL.md path.
    public func createSkill(name: String, scope: String, cwd: String? = nil) async throws -> String {
        var payload: [String: MsgPackValue] = ["name": .string(name), "scope": .string(scope)]
        if let cwd { payload["cwd"] = .string(cwd) }
        let response = try await call("skills.create", .map(payload))
        return response.payload?["path"]?.stringValue ?? ""
    }

    /// Install skills from a git URL or local directory into the given scope.
    /// Returns the names installed.
    public func installSkills(source: String, scope: String, cwd: String? = nil) async throws -> [String] {
        var payload: [String: MsgPackValue] = ["source": .string(source), "scope": .string(scope)]
        if let cwd { payload["cwd"] = .string(cwd) }
        let response = try await call("skills.install", .map(payload))
        return (response.payload?["installed"]?.arrayValue ?? []).compactMap { $0.stringValue }
    }

    /// Uninstall a skill by its SKILL.md path or directory.
    @discardableResult
    public func uninstallSkill(path: String) async throws -> Bool {
        let response = try await call("skills.uninstall", .map(["path": .string(path)]))
        return response.payload?["ok"]?.boolValue ?? false
    }

    /// List installed Claude Code plugins.
    public func fetchPlugins() async throws -> [Plugin] {
        let response = try await call("plugins.list")
        let rows = response.payload?["plugins"]?.arrayValue ?? []
        return rows.compactMap(Plugin.init(value:))
    }

    /// Install a plugin (`plugin@marketplace`) at the given scope.
    @discardableResult
    public func installPlugin(source: String, scope: String = "user") async throws -> Bool {
        let response = try await call("plugins.install",
                                      .map(["source": .string(source), "scope": .string(scope)]))
        return response.payload?["ok"]?.boolValue ?? false
    }

    /// Uninstall a plugin by name.
    @discardableResult
    public func uninstallPlugin(name: String, scope: String = "user") async throws -> Bool {
        let response = try await call("plugins.uninstall",
                                      .map(["name": .string(name), "scope": .string(scope)]))
        return response.payload?["ok"]?.boolValue ?? false
    }

    /// Enable or disable an installed plugin.
    @discardableResult
    public func setPluginEnabled(name: String, enabled: Bool) async throws -> Bool {
        let response = try await call("plugins.set_enabled",
                                      .map(["name": .string(name), "enabled": .bool(enabled)]))
        return response.payload?["ok"]?.boolValue ?? false
    }

    /// List configured plugin marketplaces.
    public func fetchMarketplaces() async throws -> [PluginMarketplace] {
        let response = try await call("plugins.marketplace_list")
        let rows = response.payload?["marketplaces"]?.arrayValue ?? []
        return rows.compactMap(PluginMarketplace.init(value:))
    }

    /// Add a plugin marketplace from a URL, path, or GitHub repo.
    @discardableResult
    public func addMarketplace(source: String) async throws -> Bool {
        let response = try await call("plugins.marketplace_add", .map(["source": .string(source)]))
        return response.payload?["ok"]?.boolValue ?? false
    }

    /// List configured MCP servers (project `<cwd>/.mcp.json` + user config).
    public func fetchMcpServers(cwd: String? = nil) async throws -> [McpServer] {
        var payload: [String: MsgPackValue] = [:]
        if let cwd { payload["cwd"] = .string(cwd) }
        let response = try await call("mcp.list", .map(payload))
        let rows = response.payload?["servers"]?.arrayValue ?? []
        return rows.compactMap(McpServer.init(value:))
    }

    /// List **every** MCP server the `claude` CLI knows about — across all
    /// scopes plus plugin / claude.ai connector servers — with live status.
    public func fetchAllMcpServers(cwd: String? = nil) async throws -> [McpServer] {
        var payload: [String: MsgPackValue] = [:]
        if let cwd { payload["cwd"] = .string(cwd) }
        let response = try await call("mcp.list_all", .map(payload))
        let rows = response.payload?["servers"]?.arrayValue ?? []
        return rows.compactMap(McpServer.init(value:))
    }

    /// Remove an MCP server via `claude mcp remove <name>` (any CLI-managed scope).
    @discardableResult
    public func cliRemoveMcpServer(name: String) async throws -> Bool {
        let response = try await call("mcp.cli_remove", .map(["name": .string(name)]))
        return response.payload?["ok"]?.boolValue ?? false
    }

    /// Add or update an MCP server in the project (`scope: "project"`, needs
    /// `cwd`) or user (`scope: "user"`) config. `command`/`args`/`env` apply to
    /// stdio; `url` to sse/http.
    @discardableResult
    public func upsertMcpServer(
        name: String, transport: String, scope: String, cwd: String? = nil,
        command: String? = nil, args: [String] = [], env: [String: String] = [:],
        url: String? = nil
    ) async throws -> Bool {
        var payload: [String: MsgPackValue] = [
            "name": .string(name),
            "transport": .string(transport),
            "scope": .string(scope),
        ]
        if let cwd { payload["cwd"] = .string(cwd) }
        if let command { payload["command"] = .string(command) }
        if !args.isEmpty { payload["args"] = .array(args.map { .string($0) }) }
        if !env.isEmpty { payload["env"] = .map(env.mapValues { .string($0) }) }
        if let url { payload["url"] = .string(url) }
        let response = try await call("mcp.upsert", .map(payload))
        return response.payload?["ok"]?.boolValue ?? false
    }

    /// Remove an MCP server by name from the project or user config.
    @discardableResult
    public func removeMcpServer(name: String, scope: String, cwd: String? = nil) async throws -> Bool {
        var payload: [String: MsgPackValue] = ["name": .string(name), "scope": .string(scope)]
        if let cwd { payload["cwd"] = .string(cwd) }
        let response = try await call("mcp.remove", .map(payload))
        return response.payload?["ok"]?.boolValue ?? false
    }

    /// List configured hooks from `settings.json` (project `cwd` + global).
    public func fetchHooks(cwd: String? = nil) async throws -> [CoreHook] {
        var payload: [String: MsgPackValue] = [:]
        if let cwd { payload["cwd"] = .string(cwd) }
        let response = try await call("hooks.list", .map(payload))
        let rows = response.payload?["hooks"]?.arrayValue ?? []
        return rows.compactMap(CoreHook.init(value:))
    }

    /// Read a UTF-8 text file. `exists` is false for a missing file (content "").
    public func readFile(_ path: String) async throws -> (content: String, exists: Bool) {
        let response = try await call("file.read", .map(["path": .string(path)]))
        return (response.payload?["content"]?.stringValue ?? "",
                response.payload?["exists"]?.boolValue ?? false)
    }

    /// Write a UTF-8 text file, creating parent directories as needed.
    @discardableResult
    public func writeFile(_ path: String, content: String) async throws -> Bool {
        let response = try await call("file.write",
                                      .map(["path": .string(path), "content": .string(content)]))
        return response.payload?["ok"]?.boolValue ?? false
    }

    /// Start a live Claude session and return its new id. The core spawns the
    /// `claude` CLI and streams its output back as `session.event` frames on
    /// ``events`` (subscribe by iterating `events`). `binary` overrides the
    /// `claude` executable (used by tests).
    @discardableResult
    public func startSession(
        prompt: String,
        cwd: String? = nil,
        model: String? = nil,
        systemPrompt: String? = nil,
        effort: String? = nil,
        resume: String? = nil,
        binary: String? = nil
    ) async throws -> String {
        var payload: [String: MsgPackValue] = ["prompt": .string(prompt)]
        if let cwd { payload["cwd"] = .string(cwd) }
        if let model { payload["model"] = .string(model) }
        if let systemPrompt, !systemPrompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            payload["system_prompt"] = .string(systemPrompt)
        }
        if let effort, !effort.isEmpty { payload["effort"] = .string(effort) }
        if let resume, !resume.isEmpty { payload["resume"] = .string(resume) }
        if let binary { payload["binary"] = .string(binary) }
        let response = try await call("session.start", .map(payload))
        return response.payload?["session_id"]?.stringValue ?? ""
    }

    /// Stop a running live session by id (the core kills the `claude` process).
    @discardableResult
    public func stopSession(sessionId: String) async throws -> Bool {
        let response = try await call("session.stop", .map(["session_id": .string(sessionId)]))
        return response.payload?["ok"]?.boolValue ?? false
    }
}
