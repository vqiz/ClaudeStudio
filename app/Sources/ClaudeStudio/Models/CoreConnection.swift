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

    /// Server-pushed events (newest first), populated while connected.
    private(set) var recentEvents: [CoreEvent] = []
    private var eventTask: Task<Void, Never>?

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

    /// Connect, verify with `ping`, then load the config and context budget.
    /// Tearing down any previous connection first, this is safe to call repeatedly
    /// (e.g. a "Reconnect" button).
    func connect() async {
        guard !connectInFlight else { return }
        connectInFlight = true
        defer { connectInFlight = false }
        await disconnect()
        status = .connecting

        let client = CoreClient(socketPath: socketPath)
        self.client = client
        do {
            try await client.connect()
            guard try await client.ping() else {
                throw IpcError.remote(code: -1, message: "core did not answer ping")
            }
            let config = try await client.fetchConfig()
            let budget = try await client.fetchContextBudget()
            self.config = config
            self.budget = budget
            // Best-effort live data; failure of any one of these must not drop
            // the connection.
            self.sessions = (try? await client.listSessions()) ?? []
            self.tasks = (try? await client.fetchTasks()) ?? []
            self.definitions = (try? await client.fetchDefinitions()) ?? []
            // Subscribe to the live event stream, then drain it on the main actor.
            try? await client.subscribeEvents()
            startEventConsumer(client)
            self.status = .online
        } catch {
            await client.disconnect()
            self.client = nil
            self.config = nil
            self.budget = nil
            self.sessions = []
            self.tasks = []
            self.definitions = []
            self.status = .failed(Self.describe(error))
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
        recentEvents = []
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
        guard envelope.method == "event",
              let kind = envelope.payload?["type"]?.stringValue else { return }
        recentEvents.insert(CoreEvent(kind: kind), at: 0)
        if recentEvents.count > 100 { recentEvents.removeLast() }
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
