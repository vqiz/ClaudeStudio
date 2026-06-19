import Foundation
import Observation
import ClaudeStudioKit

/// Top-level navigation destinations surfaced in the sidebar.
enum SidebarItem: Hashable, Identifiable, CaseIterable {
    case projects
    case osView
    case brainView
    case archive
    case taskLibrary
    case mcp
    case voiceLog
    case settings
    // Definitions section
    case agentStudio
    case context

    var id: Self { self }

    var title: String {
        switch self {
        case .projects: return "Projects"
        case .osView: return "OS View"
        case .brainView: return "Brain View"
        case .archive: return "Archive"
        case .taskLibrary: return "Task Library"
        case .mcp: return "MCP Servers"
        case .voiceLog: return "Voice Log"
        case .settings: return "Settings"
        case .agentStudio: return "Agent Studio"
        case .context: return "Context"
        }
    }

    var symbol: String {
        switch self {
        case .projects: return "folder.badge.gearshape"
        case .osView: return "rectangle.3.group"
        case .brainView: return "brain"
        case .archive: return "archivebox"
        case .taskLibrary: return "square.grid.2x2"
        case .mcp: return "puzzlepiece.extension"
        case .voiceLog: return "waveform"
        case .settings: return "gearshape"
        case .agentStudio: return "person.crop.rectangle.stack"
        case .context: return "doc.text.magnifyingglass"
        }
    }

    /// Items shown in the primary navigation section.
    static let workspace: [SidebarItem] = [
        .projects, .osView, .brainView, .archive, .taskLibrary, .mcp, .voiceLog, .settings
    ]

    /// Items shown under the "Definitions" sidebar section.
    static let definitions: [SidebarItem] = [.agentStudio, .context]
}

/// The application-wide observable state. Owns the project list, the active
/// session, the global trust posture, and the simulated event bus that drives
/// the OS View.
@Observable
@MainActor
final class AppState {
    var projects: [Project]
    var selectedSidebarItem: SidebarItem? = .projects
    var selectedProjectID: Project.ID?

    /// The session currently shown in the session panel.
    var activeSession: AgentSession?
    var sessions: [AgentSession]

    /// Global trust posture shown in the title-bar badge. Changing it persists
    /// the new posture to the core when connected.
    var globalTrustMode: TrustMode = .guarded {
        didSet {
            guard oldValue != globalTrustMode, core.isConnected else { return }
            let coreValue = globalTrustMode.coreValue
            Task { await core.setTrustMode(coreValue) }
        }
    }

    /// The selected appearance. Persisted across launches.
    var theme: AppTheme = .load() {
        didSet { theme.save() }
    }

    /// Whether the voice assistant is actively listening.
    var isListening = false

    /// Live connection to the Rust core sidecar.
    let core = CoreConnection()

    /// Whether the Rust core sidecar is currently connected.
    var coreConnected: Bool { core.isConnected }

    /// Live event-bus feed powering the OS View's event stream.
    private(set) var busEvents: [BusEvent]

    private var busTask: Task<Void, Never>?

    init() {
        let projects = Project.samples
        self.projects = projects
        self.selectedProjectID = projects.first?.id
        let session = AgentSession(
            title: "Refactor IPC framing",
            projectName: "claude-studio",
            worktreeBranch: "fix/ipc-framing",
            trustMode: .guarded,
            status: .awaitingApproval
        )
        self.sessions = [
            session,
            AgentSession(title: "Migrate to axum 0.7",
                         projectName: "atlas-api",
                         worktreeBranch: "chore/migrate-axum",
                         model: "claude-sonnet-4-8",
                         trustMode: .autonomous,
                         status: .running),
            AgentSession(title: "Embed paper corpus",
                         projectName: "research-notebook",
                         trustMode: .unleashed,
                         status: .running)
        ]
        self.activeSession = session
        self.busEvents = BusEvent.samples
    }

    var selectedProject: Project? {
        projects.first { $0.id == selectedProjectID }
    }

    /// Connect to the Rust core (best-effort) and adopt its configured trust
    /// posture for the title-bar badge. Safe to call when the core is offline —
    /// the app simply stays on its sample data.
    func connectCore() async {
        await core.connect()
        if let coreTrust = core.config?.trustMode,
           let mode = TrustMode(coreValue: coreTrust) {
            globalTrustMode = mode
        }
    }

    /// Starts the simulated supervisor event bus feeding the OS View.
    func startEventBus() {
        guard busTask == nil else { return }
        busTask = Task { @MainActor [weak self] in
            for await event in BusEvent.simulatedStream() {
                guard let self, !Task.isCancelled else { return }
                self.busEvents.insert(event, at: 0)
                if self.busEvents.count > 200 { self.busEvents.removeLast() }
            }
        }
    }

    func stopEventBus() {
        busTask?.cancel()
        busTask = nil
    }
}

/// An entry on the Supervisor / Event-Bus feed shown in the OS View.
struct BusEvent: Identifiable, Hashable, Sendable {
    enum Severity: String, Sendable {
        case info
        case action
        case approval
        case warning
        case success
    }

    let id: UUID
    var severity: Severity
    var source: String
    var message: String
    var timestamp: Date

    init(id: UUID = UUID(), severity: Severity, source: String, message: String, timestamp: Date = .now) {
        self.id = id
        self.severity = severity
        self.source = source
        self.message = message
        self.timestamp = timestamp
    }

    static let samples: [BusEvent] = [
        BusEvent(severity: .approval, source: "claude-studio", message: "Edit to codec.rs awaiting approval", timestamp: Date(timeIntervalSinceNow: -30)),
        BusEvent(severity: .action, source: "atlas-api", message: "Ran cargo build --release", timestamp: Date(timeIntervalSinceNow: -75)),
        BusEvent(severity: .success, source: "atlas-api", message: "14 integration tests passed", timestamp: Date(timeIntervalSinceNow: -90)),
        BusEvent(severity: .info, source: "supervisor", message: "Memory consolidation pass written to Qdrant", timestamp: Date(timeIntervalSinceNow: -140)),
        BusEvent(severity: .warning, source: "research-notebook", message: "Budget at 78% in unleashed session", timestamp: Date(timeIntervalSinceNow: -200))
    ]

    static func simulatedStream() -> AsyncStream<BusEvent> {
        let scripted: [(Severity, String, String)] = [
            (.action, "claude-studio", "Approved edit applied to codec.rs"),
            (.success, "claude-studio", "cargo test -p ipc — 14 passed"),
            (.info, "supervisor", "Knowledge graph updated: 3 nodes, 5 edges"),
            (.action, "atlas-api", "Opened PR #218: migrate to axum 0.7"),
            (.warning, "research-notebook", "Approaching token budget, pausing soon")
        ]
        return AsyncStream { continuation in
            let task = Task {
                var index = 0
                while !Task.isCancelled {
                    try? await Task.sleep(nanoseconds: 3_000_000_000)
                    if Task.isCancelled { break }
                    let entry = scripted[index % scripted.count]
                    continuation.yield(BusEvent(severity: entry.0, source: entry.1, message: entry.2))
                    index += 1
                }
                continuation.finish()
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}
