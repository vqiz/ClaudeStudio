import Foundation
import Observation
import ClaudeStudioKit

/// A server-pushed `SystemEvent`, surfaced in the OS View's live feed.
struct CoreEvent: Identifiable, Sendable {
    let id = UUID()
    let kind: String
    let at: Date = Date()

    /// A human-friendly label for the raw `snake_case` event type.
    var label: String {
        kind.split(separator: "_").map { $0.capitalized }.joined(separator: " ")
    }
}

/// One streamed item from a live Claude session (`session.event`).
struct LiveSessionEvent: Identifiable, Sendable {
    let id = UUID()
    /// `user`, `assistant_text`, `tool_use`, `tool_result`, `result`, or `error`.
    let kind: String
    /// The primary line (assistant prose, the user's prompt, or a tool name).
    let text: String
    /// Secondary monospaced line — the command / file / arguments of a tool call.
    let detail: String?

    init(kind: String, text: String, detail: String? = nil) {
        self.kind = kind
        self.text = text
        self.detail = detail
    }

    var symbol: String {
        switch kind {
        case "user": return "person.fill"
        case "assistant_text": return "text.bubble"
        case "tool_use": return "wrench.and.screwdriver"
        case "tool_result": return "arrow.turn.down.right"
        case "result": return "checkmark.seal"
        case "error": return "exclamationmark.triangle"
        default: return "circle"
        }
    }
}

/// Owns the live connection to the Rust core sidecar and exposes its state to the
/// UI as observable properties.
///
/// Connecting is best-effort and never throws into the UI: when the core is not
/// running, ``status`` becomes ``Status/failed(_:)`` with a human-readable reason
/// and the rest of the app keeps working against its sample data. On success the
/// real `config.get` and `context.budget` snapshots are published.
@Observable
@MainActor
final class CoreConnection {
    enum Status: Equatable {
        case offline
        case connecting
        case online
        case failed(String)

        var isOnline: Bool { self == .online }
    }

    private(set) var status: Status = .offline
    private(set) var config: CoreConfig?
    private(set) var budget: ContextBudget?

    /// Live data loaded from the core on connect.
    private(set) var sessions: [CoreSession] = []
    private(set) var tasks: [LibraryTask] = []
    private(set) var definitions: [LibraryDefinition] = []
    private(set) var mcpServers: [McpServer] = []

    /// Server-pushed events (newest first), populated while connected.
    private(set) var recentEvents: [CoreEvent] = []
    private var eventTask: Task<Void, Never>?

    /// The transcript of the currently-running live Claude session, in order.
    private(set) var liveSession: [LiveSessionEvent] = []
    /// The id of the running session, or nil when none is active.
    private(set) var runningSessionId: String?
    /// What launched the current transcript: `"session"`, `"skill"`, `"task"`,
    /// or `"agent"`. Lets each surface show only its own runs instead of bleeding
    /// the Session output into the Agents tab.
    private(set) var liveRunOrigin: String?

    /// The socket path used on the next ``connect()``. Editable from Settings.
    var socketPath: String

    private var client: CoreClient?
    /// Set synchronously at the top of ``connect()`` (before any `await`) so two
    /// overlapping connects can't both build a client and leak the first one.
    private var connectInFlight = false

    var isConnected: Bool { status.isOnline }

    init(socketPath: String = IpcProtocol.defaultSocketPath) {
        self.socketPath = socketPath
    }

    /// Connect to the core, **starting it automatically if nothing is listening**
    /// (no terminal needed). Verifies with `ping`, then loads config, budget, and
    /// the live libraries. Safe to call repeatedly (e.g. a "Reconnect" button).
    func connect() async {
        guard !connectInFlight else { return }
        connectInFlight = true
        defer { connectInFlight = false }
        await disconnect()
        status = .connecting

        // 1. Use a core that's already running (e.g. started by the dev script).
        if await attach() { return }

        // 2. Nothing there — spawn the bundled/dev core and try once more.
        if await CoreLauncher.shared.ensureRunning(socketPath: socketPath), await attach() {
            return
        }

        if status == .connecting {
            status = .failed("Could not reach or start the core sidecar.")
        }
    }

    /// One connect-and-load attempt. Returns `true` on success; on failure it
    /// leaves `status == .connecting` so the caller can decide whether to spawn.
    private func attach() async -> Bool {
        let client = CoreClient(socketPath: socketPath)
        do {
            try await client.connect()
            guard try await client.ping() else {
                throw IpcError.remote(code: -1, message: "core did not answer ping")
            }
            let config = try await client.fetchConfig()
            let budget = try await client.fetchContextBudget()
            self.client = client
            self.config = config
            self.budget = budget
            // Best-effort live data; failure of any one of these must not drop
            // the connection.
            self.sessions = (try? await client.listSessions()) ?? []
            self.tasks = (try? await client.fetchTasks()) ?? []
            self.definitions = (try? await client.fetchDefinitions()) ?? []
            self.mcpServers = (try? await client.fetchMcpServers()) ?? []
            // Subscribe to the live event stream, then drain it on the main actor.
            try? await client.subscribeEvents()
            startEventConsumer(client)
            self.status = .online
            return true
        } catch {
            await client.disconnect()
            return false
        }
    }

    /// Persist a new trust mode (core wire identifier) and adopt the returned
    /// config. No-op when offline.
    func setTrustMode(_ coreValue: String) async {
        guard isConnected, let client else { return }
        if let updated = try? await client.setConfig(trustMode: coreValue) {
            self.config = updated
        }
    }

    /// Persist one or more config fields and adopt the returned config.
    func updateConfig(defaultModel: String? = nil, contextTokenBudget: Int? = nil) async {
        guard isConnected, let client else { return }
        if let updated = try? await client.setConfig(defaultModel: defaultModel,
                                                     contextTokenBudget: contextTokenBudget) {
            self.config = updated
        }
    }

    /// Reload the session archive (e.g. after creating a session).
    func reloadSessions() async {
        guard isConnected, let client else { return }
        self.sessions = (try? await client.listSessions()) ?? sessions
    }

    /// Reload the task & definition libraries (after a create/delete).
    func reloadLibraries() async {
        guard isConnected, let client else { return }
        self.tasks = (try? await client.fetchTasks()) ?? tasks
        self.definitions = (try? await client.fetchDefinitions()) ?? definitions
    }

    /// Create a new editable task; returns its path (nil when offline/failed).
    func createTask(name: String) async -> String? {
        guard isConnected, let client else { return nil }
        let path = try? await client.createTask(name: name)
        await reloadLibraries()
        return path
    }

    /// Delete a user-library task by path.
    @discardableResult
    func deleteTask(path: String) async -> Bool {
        guard isConnected, let client else { return false }
        let ok = (try? await client.deleteTask(path: path)) ?? false
        await reloadLibraries()
        return ok
    }

    /// Load the shipped default tasks & definitions into the user library, then
    /// refresh. Returns how many of each were newly added.
    @discardableResult
    func loadDefaultTemplates() async -> (tasks: Int, definitions: Int)? {
        guard isConnected, let client else { return nil }
        let result = try? await client.loadDefaultTemplates()
        await reloadLibraries()
        return result
    }

    /// Create a new editable definition; returns its path.
    func createDefinition(name: String) async -> String? {
        guard isConnected, let client else { return nil }
        let path = try? await client.createDefinition(name: name)
        await reloadLibraries()
        return path
    }

    /// Delete a user-library definition by path.
    @discardableResult
    func deleteDefinition(path: String) async -> Bool {
        guard isConnected, let client else { return false }
        let ok = (try? await client.deleteDefinition(path: path)) ?? false
        await reloadLibraries()
        return ok
    }

    /// Installed skills for a project (and the user's global skills).
    func skills(cwd: String?) async -> [LibrarySkill] {
        guard isConnected, let client else { return [] }
        return (try? await client.fetchSkills(cwd: cwd)) ?? []
    }

    /// Scaffold a new skill; returns its SKILL.md path (nil when offline/failed).
    func createSkill(name: String, scope: String, cwd: String?) async -> String? {
        guard isConnected, let client else { return nil }
        return try? await client.createSkill(name: name, scope: scope, cwd: cwd)
    }

    /// Install skills from a git URL or local directory; returns the names added.
    func installSkills(source: String, scope: String, cwd: String?) async -> [String] {
        guard isConnected, let client else { return [] }
        return (try? await client.installSkills(source: source, scope: scope, cwd: cwd)) ?? []
    }

    /// Uninstall a skill by SKILL.md path or directory.
    @discardableResult
    func uninstallSkill(path: String) async -> Bool {
        guard isConnected, let client else { return false }
        return (try? await client.uninstallSkill(path: path)) ?? false
    }

    /// Installed Claude Code plugins.
    func plugins() async -> [Plugin] {
        guard isConnected, let client else { return [] }
        return (try? await client.fetchPlugins()) ?? []
    }

    /// Install a plugin (`plugin@marketplace`).
    @discardableResult
    func installPlugin(source: String, scope: String = "user") async -> Bool {
        guard isConnected, let client else { return false }
        return (try? await client.installPlugin(source: source, scope: scope)) ?? false
    }

    /// Uninstall a plugin by name.
    @discardableResult
    func uninstallPlugin(name: String, scope: String = "user") async -> Bool {
        guard isConnected, let client else { return false }
        return (try? await client.uninstallPlugin(name: name, scope: scope)) ?? false
    }

    /// Enable or disable an installed plugin.
    @discardableResult
    func setPluginEnabled(name: String, enabled: Bool) async -> Bool {
        guard isConnected, let client else { return false }
        return (try? await client.setPluginEnabled(name: name, enabled: enabled)) ?? false
    }

    /// Configured plugin marketplaces.
    func marketplaces() async -> [PluginMarketplace] {
        guard isConnected, let client else { return [] }
        return (try? await client.fetchMarketplaces()) ?? []
    }

    /// Add a plugin marketplace from a URL, path, or GitHub repo.
    @discardableResult
    func addMarketplace(source: String) async -> Bool {
        guard isConnected, let client else { return false }
        return (try? await client.addMarketplace(source: source)) ?? false
    }

    /// MCP servers for a project (`<cwd>/.mcp.json`) plus the user config.
    func mcpServers(cwd: String?) async -> [McpServer] {
        guard isConnected, let client else { return [] }
        return (try? await client.fetchMcpServers(cwd: cwd)) ?? []
    }

    /// Add or update an MCP server, then refresh the global list.
    @discardableResult
    func upsertMcpServer(
        name: String, transport: String, scope: String, cwd: String?,
        command: String? = nil, args: [String] = [], env: [String: String] = [:],
        url: String? = nil
    ) async -> Bool {
        guard isConnected, let client else { return false }
        let ok = (try? await client.upsertMcpServer(
            name: name, transport: transport, scope: scope, cwd: cwd,
            command: command, args: args, env: env, url: url)) ?? false
        self.mcpServers = (try? await client.fetchMcpServers(cwd: cwd)) ?? mcpServers
        return ok
    }

    /// Remove an MCP server, then refresh the global list.
    @discardableResult
    func removeMcpServer(name: String, scope: String, cwd: String?) async -> Bool {
        guard isConnected, let client else { return false }
        let ok = (try? await client.removeMcpServer(name: name, scope: scope, cwd: cwd)) ?? false
        self.mcpServers = (try? await client.fetchMcpServers(cwd: cwd)) ?? mcpServers
        return ok
    }

    /// Read a text file via the core (`nil` when offline). `exists` is false for
    /// a missing file.
    func readFile(_ path: String) async -> (content: String, exists: Bool)? {
        guard isConnected, let client else { return nil }
        return try? await client.readFile(path)
    }

    /// Write a text file via the core. Returns false when offline or on error.
    @discardableResult
    func writeFile(_ path: String, content: String) async -> Bool {
        guard isConnected, let client else { return false }
        return (try? await client.writeFile(path, content: content)) ?? false
    }

    /// Configured hooks (project + global), or `[]` when offline.
    func hooks(cwd: String?) async -> [CoreHook] {
        guard isConnected, let client else { return [] }
        return (try? await client.fetchHooks(cwd: cwd)) ?? []
    }

    /// Git worktrees of the repo at `cwd` (empty when offline / not a repo).
    func worktrees(cwd: String) async -> [ProjectWorktree] {
        guard isConnected, let client else { return [] }
        guard let res = try? await client.call("git.worktrees", .map(["cwd": .string(cwd)])) else {
            return []
        }
        return (res.payload?["worktrees"]?.arrayValue ?? []).compactMap { entry in
            guard let path = entry["path"]?.stringValue else { return nil }
            return ProjectWorktree(branch: entry["branch"]?.stringValue ?? "(detached)", path: path)
        }
    }

    /// Current branch + number of changed files for a git repo at `cwd`
    /// (`nil` when offline or not a repo).
    func gitInfo(cwd: String) async -> (branch: String, changes: Int)? {
        guard isConnected, let client else { return nil }
        guard let branchRes = try? await client.call("git.branch", .map(["cwd": .string(cwd)])),
              let branch = branchRes.payload?["branch"]?.stringValue else { return nil }
        let statusRes = try? await client.call("git.status", .map(["cwd": .string(cwd)]))
        let changes = statusRes?.payload?["entries"]?.arrayValue?.count ?? 0
        return (branch, changes)
    }

    /// Re-fetch config and budget over an existing connection, reconnecting if the
    /// link has dropped.
    func refresh() async {
        guard isConnected, let client else {
            await connect()
            return
        }
        do {
            self.config = try await client.fetchConfig()
            self.budget = try await client.fetchContextBudget()
        } catch {
            self.status = .failed(Self.describe(error))
        }
    }

    func disconnect() async {
        eventTask?.cancel()
        eventTask = nil
        if let client { await client.disconnect() }
        client = nil
        sessions = []
        tasks = []
        definitions = []
        mcpServers = []
        recentEvents = []
        liveSession = []
        runningSessionId = nil
        liveRunOrigin = nil
        if status != .offline { status = .offline }
    }

    // MARK: Live events

    private func startEventConsumer(_ client: CoreClient) {
        eventTask?.cancel()
        let stream = client.events
        eventTask = Task { @MainActor [weak self] in
            for await envelope in stream {
                guard let self else { return }
                self.handleEvent(envelope)
            }
        }
    }

    private func handleEvent(_ envelope: IpcEnvelope) {
        switch envelope.method {
        case "event":
            guard let kind = envelope.payload?["type"]?.stringValue else { return }
            recentEvents.insert(CoreEvent(kind: kind), at: 0)
            if recentEvents.count > 100 { recentEvents.removeLast() }

        case "session.event":
            guard let event = envelope.payload?["event"],
                  let kind = event["kind"]?.stringValue else { return }
            switch kind {
            case "done":
                runningSessionId = nil
            case "assistant_text":
                liveSession.append(LiveSessionEvent(kind: kind, text: event["text"]?.stringValue ?? ""))
            case "tool_use":
                let name = event["name"]?.stringValue ?? "tool"
                liveSession.append(LiveSessionEvent(
                    kind: kind, text: name,
                    detail: Self.toolDetail(name: name, input: event["input"])))
            case "tool_result":
                let content = event["content"]?.stringValue ?? ""
                liveSession.append(LiveSessionEvent(kind: kind, text: Self.truncate(content, 600)))
            case "result":
                let cost = event["cost_usd"]?.doubleValue ?? 0
                liveSession.append(LiveSessionEvent(kind: kind, text: String(format: "completed · $%.4f", cost)))
                runningSessionId = nil
            case "error":
                liveSession.append(LiveSessionEvent(kind: kind, text: event["message"]?.stringValue ?? "error"))
                runningSessionId = nil
            default:
                break
            }

        default:
            break
        }
    }

    /// Start a live Claude session. Streamed output lands in ``liveSession`` and
    /// the run is archived by the core. No-op when offline.
    func startSession(prompt: String, cwd: String? = nil, model: String? = nil,
                      systemPrompt: String? = nil, effort: String? = nil,
                      origin: String = "session") async {
        guard isConnected, let client else { return }
        // Echo the user's own message into the transcript so the session reads
        // like a conversation.
        liveSession = [LiveSessionEvent(kind: "user", text: prompt)]
        liveRunOrigin = origin
        runningSessionId = nil
        if let id = try? await client.startSession(prompt: prompt, cwd: cwd, model: model,
                                                   systemPrompt: systemPrompt, effort: effort), !id.isEmpty {
            runningSessionId = id
        }
        // Refresh the archive so the new session appears in the list.
        await reloadSessions()
    }

    /// A short, human-readable summary of a tool call's input for the transcript
    /// (the Bash command, the edited file, the launched sub-agent, …).
    static func toolDetail(name: String, input: MsgPackValue?) -> String? {
        guard let input else { return nil }
        func s(_ key: String) -> String? {
            let v = input[key]?.stringValue
            return (v?.isEmpty == false) ? v : nil
        }
        switch name {
        case "Bash": return s("command")
        case "Read", "Write", "Edit", "NotebookEdit": return s("file_path") ?? s("notebook_path")
        case "Glob", "Grep": return [s("pattern"), s("path")].compactMap { $0 }.joined(separator: "  ·  ")
        case "Task", "Agent":
            return [s("subagent_type"), s("description")].compactMap { $0 }.joined(separator: ": ")
        case "WebFetch": return s("url")
        case "WebSearch": return s("query")
        default:
            // Fall back to the first short string field, if any.
            if let map = input.mapValue {
                for key in ["command", "path", "file_path", "url", "query", "prompt", "description"] {
                    if let v = map[key]?.stringValue, !v.isEmpty { return v }
                }
            }
            return nil
        }
    }

    /// Collapse whitespace-heavy tool output and cap it for the transcript.
    static func truncate(_ text: String, _ limit: Int) -> String {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.count > limit ? String(trimmed.prefix(limit)) + "…" : trimmed
    }

    /// A short, user-facing description of an IPC failure.
    static func describe(_ error: Error) -> String {
        switch error {
        case IpcError.notConnected: return "Not connected."
        case let IpcError.connectionFailed(reason): return "Core offline (\(reason))."
        case IpcError.socketClosed: return "Connection closed by the core."
        case let IpcError.timedOut(method): return "The core did not respond to \(method) in time."
        case let IpcError.remote(_, message): return message
        case let IpcError.decodeFailed(reason): return "Unexpected reply (\(reason))."
        case IpcError.frameTooLarge: return "Reply exceeded the frame limit."
        default: return error.localizedDescription
        }
    }
}
