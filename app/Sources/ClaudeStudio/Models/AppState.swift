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
    case extensions
    case hooks
    case voiceLog
    case settings
    // Definitions section
    case agentStudio
    case context
    case definitionLibrary

    var id: Self { self }

    var title: String {
        switch self {
        case .projects: return "Projects"
        case .osView: return "OS View"
        case .brainView: return "Brain View"
        case .archive: return "Archive"
        case .taskLibrary: return "Task Library"
        case .mcp: return "MCP Servers"
        case .extensions: return "Skills & Plugins"
        case .hooks: return "Hooks"
        case .voiceLog: return "Voice Log"
        case .settings: return "Settings"
        case .agentStudio: return "Agent Studio"
        case .context: return "Context"
        case .definitionLibrary: return "Definitions Library"
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
        case .extensions: return "square.stack.3d.up"
        case .hooks: return "link"
        case .voiceLog: return "waveform"
        case .settings: return "gearshape"
        case .agentStudio: return "person.crop.rectangle.stack"
        case .context: return "doc.text.magnifyingglass"
        case .definitionLibrary: return "books.vertical"
        }
    }

    /// Items shown in the primary navigation section.
    static let workspace: [SidebarItem] = [
        .projects, .osView, .brainView, .archive, .taskLibrary, .mcp, .extensions, .hooks, .voiceLog, .settings
    ]

    /// Items shown under the "Definitions" sidebar section.
    static let definitions: [SidebarItem] = [.agentStudio, .context, .definitionLibrary]
}

/// The application-wide observable state. Owns the project list, the active
/// session, the global trust posture, and the simulated event bus that drives
/// the OS View.
@Observable
@MainActor
final class AppState {
    /// The user's real projects (folders they added), persisted.
    let projectStore = ProjectStore()
    var projects: [Project] { projectStore.projects }

    /// Reusable agent presets, persisted.
    let agentStore = AgentStore()

    /// Text-to-speech controller (reads responses aloud).
    let voice = VoiceController()

    var selectedSidebarItem: SidebarItem? = .projects
    var selectedProjectID: Project.ID? {
        didSet {
            guard selectedProjectID != oldValue, let project = selectedProject else { return }
            // Warm the per-project caches so the workspace + tab switches are
            // instant (no IPC round-trip on appear).
            Task { await core.prefetch(project: project) }
        }
    }

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
        self.selectedProjectID = projectStore.projects.first?.id
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
        self.busEvents = []
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
        // Warm the selected project's caches as soon as the core is up.
        if let project = selectedProject {
            await core.prefetch(project: project)
        }
    }

    /// Resume an archived conversation (like `claude --resume`): load its
    /// transcript, arm resume, select (or add) its project, and jump to it so the
    /// user can continue in the Session tab.
    func resumeArchived(_ session: CoreSession) async {
        guard await core.resumeArchived(session) else { return }
        let project = projectStore.add(path: session.cwd) // de-duped by path
        selectedProjectID = project.id
        selectedSidebarItem = .projects
    }

    /// Runs the core supervisor loop: keeps the Rust core connected (auto-
    /// reconnecting if it ever drops — e.g. after the app bundle is replaced or
    /// the core is restarted) and mirrors the **real** core event bus into the
    /// OS View feed. Replaces the previous simulated demo stream.
    func startEventBus() {
        guard busTask == nil else { return }
        busTask = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                // Self-heal: whenever the core isn't online — or claims to be but
                // no longer answers a ping (silently-dead link) — (re)connect.
                // This recovers the "core offline / no skills" state on its own.
                var needsReconnect = !self.core.isConnected
                if !needsReconnect {
                    needsReconnect = !(await self.core.isAlive())
                }
                if needsReconnect {
                    await self.connectCore()
                }
                // Reflect the genuine core events (newest first) — no fake data.
                self.busEvents = self.core.recentEvents.map {
                    BusEvent(severity: .info, source: "core", message: $0.label, timestamp: $0.at)
                }
                try? await Task.sleep(nanoseconds: 3_000_000_000)
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
