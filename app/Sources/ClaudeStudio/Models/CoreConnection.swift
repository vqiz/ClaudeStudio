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
    /// `assistant_text`, `tool_use`, `tool_result`, `result`, or `error`.
    let kind: String
    let text: String

    var symbol: String {
        switch kind {
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
                liveSession.append(LiveSessionEvent(kind: kind, text: event["name"]?.stringValue ?? "tool"))
            case "tool_result":
                liveSession.append(LiveSessionEvent(kind: kind, text: event["content"]?.stringValue ?? ""))
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
    func startSession(prompt: String, cwd: String? = nil, model: String? = nil) async {
        guard isConnected, let client else { return }
        liveSession = []
        runningSessionId = nil
        if let id = try? await client.startSession(prompt: prompt, cwd: cwd, model: model), !id.isEmpty {
            runningSessionId = id
        }
        // Refresh the archive so the new session appears in the list.
        await reloadSessions()
    }

    /// A short, user-facing description of an IPC failure.
    static func describe(_ error: Error) -> String {
        switch error {
        case IpcError.notConnected: return "Not connected."
        case let IpcError.connectionFailed(reason): return "Core offline (\(reason))."
        case IpcError.socketClosed: return "Connection closed by the core."
        case let IpcError.remote(_, message): return message
        case let IpcError.decodeFailed(reason): return "Unexpected reply (\(reason))."
        case IpcError.frameTooLarge: return "Reply exceeded the frame limit."
        default: return error.localizedDescription
        }
    }
}
