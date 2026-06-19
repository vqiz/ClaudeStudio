import Foundation

/// A single entry in a live session transcript. Tool calls collapse into a
/// nested view; the cost delta feeds the running `CostTracker`.
struct SessionEvent: Identifiable, Hashable, Sendable {
    enum Role: String, Sendable {
        case user
        case assistant
        case tool
        case system
        case supervisor
    }

    enum Kind: Hashable, Sendable {
        case message(String)
        case toolCall(ToolCall)
        case planStep(String)
        case permissionRequest(String)
        case status(String)
    }

    let id: UUID
    var role: Role
    var kind: Kind
    var timestamp: Date
    /// Incremental USD cost attributed to producing this event.
    var costDelta: Double
    /// Incremental tokens (input + output) attributed to this event.
    var tokenDelta: Int

    init(id: UUID = UUID(),
         role: Role,
         kind: Kind,
         timestamp: Date = .now,
         costDelta: Double = 0,
         tokenDelta: Int = 0) {
        self.id = id
        self.role = role
        self.kind = kind
        self.timestamp = timestamp
        self.costDelta = costDelta
        self.tokenDelta = tokenDelta
    }
}

/// A tool invocation captured in the transcript, including its result so the
/// UI can render a collapsible call → output pair.
struct ToolCall: Identifiable, Hashable, Sendable {
    enum Status: String, Sendable {
        case running
        case succeeded
        case failed
        case awaitingApproval
    }

    let id: UUID
    var name: String
    var input: String
    var output: String?
    var status: Status

    init(id: UUID = UUID(), name: String, input: String, output: String? = nil, status: Status = .running) {
        self.id = id
        self.name = name
        self.input = input
        self.output = output
        self.status = status
    }
}

extension SessionEvent {
    /// A scripted opening transcript used to seed a freshly opened session
    /// panel. The simulated stream appends to this over time.
    static let sampleTranscript: [SessionEvent] = [
        SessionEvent(
            role: .user,
            kind: .message("Refactor the IPC framing to length-prefixed MessagePack and add a reconnect loop."),
            timestamp: Date(timeIntervalSinceNow: -210),
            tokenDelta: 38
        ),
        SessionEvent(
            role: .assistant,
            kind: .planStep("Read core/crates/ipc to map the current frame format."),
            timestamp: Date(timeIntervalSinceNow: -205),
            costDelta: 0.004,
            tokenDelta: 120
        ),
        SessionEvent(
            role: .tool,
            kind: .toolCall(ToolCall(
                name: "Read",
                input: "core/crates/ipc/src/frame.rs",
                output: "pub struct Frame { len: u32, body: Vec<u8> } …",
                status: .succeeded
            )),
            timestamp: Date(timeIntervalSinceNow: -200),
            costDelta: 0.002,
            tokenDelta: 540
        ),
        SessionEvent(
            role: .assistant,
            kind: .message("The existing frame already carries a u32 length. I'll swap the JSON body for MessagePack and add a backoff reconnect."),
            timestamp: Date(timeIntervalSinceNow: -190),
            costDelta: 0.011,
            tokenDelta: 310
        ),
        SessionEvent(
            role: .tool,
            kind: .toolCall(ToolCall(
                name: "Edit",
                input: "core/crates/ipc/src/codec.rs",
                output: nil,
                status: .awaitingApproval
            )),
            timestamp: Date(timeIntervalSinceNow: -120),
            tokenDelta: 0
        ),
        SessionEvent(
            role: .supervisor,
            kind: .status("Budget at 38% — autonomous mode will pause at 80%."),
            timestamp: Date(timeIntervalSinceNow: -60)
        )
    ]
}
