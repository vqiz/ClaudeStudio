import Foundation
import Observation

/// A live (or recently finished) Claude Code session running against a project
/// or worktree. The transcript grows from a simulated `AsyncStream` until the
/// Rust core supplies real events over IPC.
@Observable
final class AgentSession: Identifiable {
    enum Status: String, Sendable {
        case running
        case awaitingApproval
        case paused
        case completed
        case failed

        var label: String {
            switch self {
            case .running: return "Running"
            case .awaitingApproval: return "Awaiting Approval"
            case .paused: return "Paused"
            case .completed: return "Completed"
            case .failed: return "Failed"
            }
        }
    }

    let id: UUID
    var title: String
    var projectName: String
    var worktreeBranch: String?
    var model: String
    var trustMode: TrustMode
    var status: Status
    var startedAt: Date
    private(set) var events: [SessionEvent]
    let cost: CostTracker

    private var streamTask: Task<Void, Never>?

    init(id: UUID = UUID(),
         title: String,
         projectName: String,
         worktreeBranch: String? = nil,
         model: String = "claude-opus-4-8",
         trustMode: TrustMode = .guarded,
         status: Status = .running,
         startedAt: Date = .now,
         events: [SessionEvent] = SessionEvent.sampleTranscript,
         budgetUSD: Double = 5.0) {
        self.id = id
        self.title = title
        self.projectName = projectName
        self.worktreeBranch = worktreeBranch
        self.model = model
        self.trustMode = trustMode
        self.status = status
        self.startedAt = startedAt
        self.events = events
        self.cost = CostTracker(budgetUSD: budgetUSD)
        for event in events { cost.record(event) }
    }

    @MainActor
    func append(_ event: SessionEvent) {
        events.append(event)
        cost.record(event)
    }

    /// Starts a simulated live event stream. Replace the generator with the
    /// `IpcClient.events` mapping once the Rust core is wired in.
    @MainActor
    func startSimulatedStream() {
        guard streamTask == nil else { return }
        let stream = AgentSession.simulatedEvents()
        streamTask = Task { @MainActor [weak self] in
            for await event in stream {
                guard let self, !Task.isCancelled else { return }
                self.append(event)
            }
            self?.status = .completed
        }
    }

    func stopStream() {
        streamTask?.cancel()
        streamTask = nil
    }

    deinit {
        streamTask?.cancel()
    }

    /// A finite, scripted async stream that emits transcript events on a delay,
    /// modelling Claude working through a task. Uses Swift structured
    /// concurrency so it cancels cleanly with the owning task.
    static func simulatedEvents() -> AsyncStream<SessionEvent> {
        let scripted: [SessionEvent] = [
            SessionEvent(role: .assistant,
                         kind: .message("Approved. Applying the MessagePack codec edit."),
                         costDelta: 0.006, tokenDelta: 180),
            SessionEvent(role: .tool,
                         kind: .toolCall(ToolCall(name: "Edit",
                                                  input: "core/crates/ipc/src/codec.rs",
                                                  output: "Updated 2 functions, 41 insertions.",
                                                  status: .succeeded)),
                         costDelta: 0.003, tokenDelta: 220),
            SessionEvent(role: .tool,
                         kind: .toolCall(ToolCall(name: "Bash",
                                                  input: "cargo test -p ipc",
                                                  output: "test result: ok. 14 passed; 0 failed",
                                                  status: .succeeded)),
                         costDelta: 0.002, tokenDelta: 95),
            SessionEvent(role: .assistant,
                         kind: .message("Tests pass. Added an exponential-backoff reconnect loop to the client."),
                         costDelta: 0.009, tokenDelta: 260),
            SessionEvent(role: .supervisor,
                         kind: .status("Task complete. 0 destructive actions, budget at 41%."))
        ]
        return AsyncStream { continuation in
            let task = Task {
                for event in scripted {
                    try? await Task.sleep(nanoseconds: 1_400_000_000)
                    if Task.isCancelled { break }
                    continuation.yield(event)
                }
                continuation.finish()
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}
