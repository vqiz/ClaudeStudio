import Foundation
import CoreGraphics

/// A reusable, parameterised task definition from the Task Library — a saved
/// prompt + skill + trust-mode preset the user can launch against any project.
struct TaskCard: Identifiable, Hashable, Sendable {
    let id: UUID
    var title: String
    var summary: String
    var skill: String
    var defaultTrustMode: TrustMode
    var estimatedCostUSD: Double
    var runCount: Int

    init(id: UUID = UUID(),
         title: String,
         summary: String,
         skill: String,
         defaultTrustMode: TrustMode,
         estimatedCostUSD: Double,
         runCount: Int) {
        self.id = id
        self.title = title
        self.summary = summary
        self.skill = skill
        self.defaultTrustMode = defaultTrustMode
        self.estimatedCostUSD = estimatedCostUSD
        self.runCount = runCount
    }

    static let samples: [TaskCard] = [
        TaskCard(title: "Security Review", summary: "Audit the current diff for injection, auth, and secret-handling issues.",
                 skill: "security-review", defaultTrustMode: .readOnly, estimatedCostUSD: 0.18, runCount: 42),
        TaskCard(title: "Refactor Module", summary: "Restructure a module for clarity while keeping the public API stable.",
                 skill: "improve-codebase-architecture", defaultTrustMode: .guarded, estimatedCostUSD: 0.46, runCount: 17),
        TaskCard(title: "Write Tests (TDD)", summary: "Add failing tests first, then implement until green.",
                 skill: "tdd", defaultTrustMode: .guarded, estimatedCostUSD: 0.31, runCount: 88),
        TaskCard(title: "Deep Research", summary: "Fan-out web research with adversarial verification and a cited report.",
                 skill: "deep-research", defaultTrustMode: .autonomous, estimatedCostUSD: 1.20, runCount: 9),
        TaskCard(title: "Graphify Repo", summary: "Index the codebase into the semantic knowledge graph.",
                 skill: "graphify", defaultTrustMode: .autonomous, estimatedCostUSD: 0.62, runCount: 23),
        TaskCard(title: "Triage Failures", summary: "Reproduce, bisect, and propose a fix for failing CI.",
                 skill: "diagnose", defaultTrustMode: .guarded, estimatedCostUSD: 0.27, runCount: 31)
    ]
}

/// An archived (completed) session shown in the searchable Archive list.
struct ArchivedSession: Identifiable, Hashable, Sendable {
    let id: UUID
    var title: String
    var project: String
    var finishedAt: Date
    var outcome: String
    var costUSD: Double
    var trustMode: TrustMode

    init(id: UUID = UUID(),
         title: String,
         project: String,
         finishedAt: Date,
         outcome: String,
         costUSD: Double,
         trustMode: TrustMode) {
        self.id = id
        self.title = title
        self.project = project
        self.finishedAt = finishedAt
        self.outcome = outcome
        self.costUSD = costUSD
        self.trustMode = trustMode
    }

    static let samples: [ArchivedSession] = [
        ArchivedSession(title: "Add MessagePack codec", project: "claude-studio",
                        finishedAt: Date(timeIntervalSinceNow: -3_600), outcome: "Merged to main",
                        costUSD: 0.41, trustMode: .guarded),
        ArchivedSession(title: "Fix flaky socket test", project: "claude-studio",
                        finishedAt: Date(timeIntervalSinceNow: -10_800), outcome: "PR opened",
                        costUSD: 0.12, trustMode: .guarded),
        ArchivedSession(title: "Migrate to axum 0.7", project: "atlas-api",
                        finishedAt: Date(timeIntervalSinceNow: -86_400), outcome: "Merged to release/2.4",
                        costUSD: 1.84, trustMode: .autonomous),
        ArchivedSession(title: "Redesign pricing page", project: "marketing-site",
                        finishedAt: Date(timeIntervalSinceNow: -172_800), outcome: "Deployed to preview",
                        costUSD: 0.73, trustMode: .readOnly),
        ArchivedSession(title: "Embed arXiv corpus", project: "research-notebook",
                        finishedAt: Date(timeIntervalSinceNow: -259_200), outcome: "12k vectors indexed",
                        costUSD: 2.05, trustMode: .unleashed)
    ]
}

/// An entry in the Voice Log — a transcribed voice command and its disposition.
struct VoiceLogEntry: Identifiable, Hashable, Sendable, Codable {
    let id: UUID
    var transcript: String
    var intent: String
    var handled: Bool
    var timestamp: Date

    init(id: UUID = UUID(), transcript: String, intent: String, handled: Bool, timestamp: Date) {
        self.id = id
        self.transcript = transcript
        self.intent = intent
        self.handled = handled
        self.timestamp = timestamp
    }

    static let samples: [VoiceLogEntry] = [
        VoiceLogEntry(transcript: "Start a security review on the current diff.",
                      intent: "task.launch(security-review)", handled: true,
                      timestamp: Date(timeIntervalSinceNow: -300)),
        VoiceLogEntry(transcript: "Switch claude-studio to guarded mode.",
                      intent: "project.setTrustMode(guarded)", handled: true,
                      timestamp: Date(timeIntervalSinceNow: -640)),
        VoiceLogEntry(transcript: "What did the supervisor do in the last hour?",
                      intent: "query.busEvents(window: 1h)", handled: true,
                      timestamp: Date(timeIntervalSinceNow: -1_200)),
        VoiceLogEntry(transcript: "Open the brain view for atlas-api.",
                      intent: "navigate(brainView, atlas-api)", handled: false,
                      timestamp: Date(timeIntervalSinceNow: -1_800))
    ]
}

/// A node in the Brain View knowledge graph.
struct GraphNode: Identifiable, Hashable, Sendable {
    enum Kind: String, Sendable {
        case concept
        case file
        case session
        case skill
        case memory
    }

    let id: UUID
    var label: String
    var kind: Kind
    /// Normalised layout position in [0,1]×[0,1]; the view maps it to pixels.
    var position: CGPoint

    init(id: UUID = UUID(), label: String, kind: Kind, position: CGPoint) {
        self.id = id
        self.label = label
        self.kind = kind
        self.position = position
    }
}

/// An undirected edge between two graph nodes.
struct GraphEdge: Identifiable, Hashable, Sendable {
    let id: UUID
    var from: GraphNode.ID
    var to: GraphNode.ID
    var weight: Double

    init(id: UUID = UUID(), from: GraphNode.ID, to: GraphNode.ID, weight: Double = 1) {
        self.id = id
        self.from = from
        self.to = to
        self.weight = weight
    }
}

/// A small sample knowledge graph for the Brain View placeholder.
struct KnowledgeGraph {
    var nodes: [GraphNode]
    var edges: [GraphEdge]

    static func sample() -> KnowledgeGraph {
        let ipc = GraphNode(label: "IPC Layer", kind: .concept, position: CGPoint(x: 0.5, y: 0.5))
        let codec = GraphNode(label: "codec.rs", kind: .file, position: CGPoint(x: 0.28, y: 0.32))
        let client = GraphNode(label: "IpcClient", kind: .file, position: CGPoint(x: 0.72, y: 0.30))
        let session = GraphNode(label: "Refactor IPC", kind: .session, position: CGPoint(x: 0.30, y: 0.72))
        let memory = GraphNode(label: "Framing decision", kind: .memory, position: CGPoint(x: 0.74, y: 0.70))
        let skill = GraphNode(label: "code-review", kind: .skill, position: CGPoint(x: 0.5, y: 0.14))
        let nodes = [ipc, codec, client, session, memory, skill]
        let edges = [
            GraphEdge(from: ipc.id, to: codec.id, weight: 0.9),
            GraphEdge(from: ipc.id, to: client.id, weight: 0.9),
            GraphEdge(from: ipc.id, to: session.id, weight: 0.6),
            GraphEdge(from: session.id, to: codec.id, weight: 0.7),
            GraphEdge(from: session.id, to: memory.id, weight: 0.8),
            GraphEdge(from: ipc.id, to: skill.id, weight: 0.4),
            GraphEdge(from: client.id, to: memory.id, weight: 0.5)
        ]
        return KnowledgeGraph(nodes: nodes, edges: edges)
    }
}
