import Foundation
import Observation
import ClaudeStudioKit

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

    /// The socket path used on the next ``connect()``. Editable from Settings.
    var socketPath: String

    private var client: CoreClient?

    var isConnected: Bool { status.isOnline }

    init(socketPath: String = IpcProtocol.defaultSocketPath) {
        self.socketPath = socketPath
    }

    /// Connect, verify with `ping`, then load the config and context budget.
    /// Tearing down any previous connection first, this is safe to call repeatedly
    /// (e.g. a "Reconnect" button).
    func connect() async {
        if status == .connecting { return }
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
            self.status = .online
        } catch {
            await client.disconnect()
            self.client = nil
            self.config = nil
            self.budget = nil
            self.status = .failed(Self.describe(error))
        }
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
        if let client { await client.disconnect() }
        client = nil
        if status != .offline { status = .offline }
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
