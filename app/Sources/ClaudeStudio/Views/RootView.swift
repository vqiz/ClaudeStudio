import SwiftUI

/// The application shell: a three-zone `NavigationSplitView` with a sidebar,
/// a content column that switches on the selected sidebar item, and a trailing
/// session panel. The title bar carries the voice mic indicator and the global
/// TrustMode badge.
struct RootView: View {
    @Environment(AppState.self) private var appState

    /// F033: Sichtbarkeit des Tastenkürzel-Overlays (Cmd+/ schaltet um, Esc schließt).
    @State private var showShortcuts = false

    var body: some View {
        @Bindable var appState = appState

        ZStack {
            NavigationSplitView {
                SidebarView(selection: $appState.selectedSidebarItem)
                    // 260px sichtbare Sidebar (Konzept-Spezifikation F026). Feste Spaltenbreite
                    // — überschreibt die intrinsische Mindestbreite der längsten Zeile, damit die
                    // sichtbare Spalte exakt 260pt misst (Zeilen kürzen statt Spalte aufzuweiten).
                    .navigationSplitViewColumnWidth(252)
                    .frame(maxWidth: 252)
            } detail: {
                detailColumn
            }
            .toolbar { titleBarItems }
            .themedChrome(appState.theme)
            // Always-on voice glue (zero-size): spoken command → session → spoken reply.
            .background(VoiceOrchestrator())

            // F033: Cmd+/ bindet das Umschalten (verstecktes Button-Element trägt das Shortcut).
            Button("") { showShortcuts.toggle() }
                .keyboardShortcut("/", modifiers: .command)
                .opacity(0).frame(width: 0, height: 0)

            if showShortcuts {
                ShortcutOverlay(onClose: { showShortcuts = false })
                    .zIndex(100)
                Button("") { showShortcuts = false }
                    .keyboardShortcut(.cancelAction)   // Esc schließt
                    .opacity(0).frame(width: 0, height: 0)
            }
        }
        .onAppear {
            // Headless-Seam (F033): Overlay sichtbar erzwingen, um den Inhalt zu verifizieren.
            if ProcessInfo.processInfo.environment["CLAUDESTUDIO_SHOW_SHORTCUTS"] == "1" {
                showShortcuts = true
            }
        }
    }

    @ViewBuilder
    private var detailColumn: some View {
        switch appState.selectedSidebarItem ?? .projects {
        case .coPilot:
            CoPilotView()
        case .projects:
            ProjectsView()
        case .osView:
            OSView()
        case .brainView:
            BrainView()
        case .archive:
            ArchiveView()
        case .taskLibrary:
            TaskLibraryView()
        case .mcp:
            MCPView()
        case .extensions:
            ExtensionsView()
        case .hooks:
            HooksView()
        case .voiceLog:
            VoiceLogView()
        case .settings:
            SettingsView()
        case .agentStudio:
            AgentStudioView()
        case .context:
            ContextView()
        case .definitionLibrary:
            DefinitionsLibraryView()
        }
    }

    @ToolbarContentBuilder
    private var titleBarItems: some ToolbarContent {
        ToolbarItem(placement: .navigation) {
            CoreStatusButton()
        }
        ToolbarItemGroup(placement: .primaryAction) {
            VoiceMicIndicator()
            TitleBarMicMeter()
            TrustModeMenu()
        }
    }
}

/// Title-bar connection control: a status pill that is itself a menu —
/// connect / reconnect / disconnect, plus the socket / last error.
struct CoreStatusButton: View {
    @Environment(AppState.self) private var appState

    var body: some View {
        Menu {
            Button(appState.coreConnected ? "Reconnect" : "Connect", systemImage: "bolt.horizontal.circle") {
                Task { await appState.connectCore() }
            }
            if appState.coreConnected {
                Button("Disconnect", systemImage: "xmark.circle", role: .destructive) {
                    Task { await appState.core.disconnect() }
                }
            }
            Divider()
            Text(detail).font(.caption)
        } label: {
            HStack(spacing: 6) {
                Circle().fill(color).frame(width: 8, height: 8)
                Text(label).font(.caption.weight(.medium))
            }
            .padding(.horizontal, 9).padding(.vertical, 4)
            .background(color.opacity(0.14), in: Capsule())
            .overlay(Capsule().strokeBorder(color.opacity(0.3), lineWidth: 1))
            .contentShape(Capsule())
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        .help("Core connection")
    }

    private var label: String {
        switch appState.core.status {
        case .online: return "Core connected"
        case .connecting: return "Connecting…"
        case .offline, .failed: return "Core offline"
        }
    }
    private var color: Color {
        switch appState.core.status {
        case .online: return .green
        case .connecting: return .yellow
        case .offline, .failed: return .orange
        }
    }
    private var detail: String {
        if case .failed(let reason) = appState.core.status { return reason }
        return appState.core.socketPath
    }
}

/// Title-bar trust-mode control: the badge, clickable to change mode (the change
/// is persisted to the core).
struct TrustModeMenu: View {
    @Environment(AppState.self) private var appState

    var body: some View {
        Menu {
            ForEach(TrustMode.allCases) { mode in
                Button {
                    appState.globalTrustMode = mode
                } label: {
                    Label(mode.label,
                          systemImage: appState.globalTrustMode == mode ? "checkmark" : mode.symbol)
                }
            }
        } label: {
            TrustModeBadge(mode: appState.globalTrustMode)
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        .help("Change trust mode")
    }
}

/// A compact mic-input meter shown beside the title-bar mic while a hands-free
/// conversation is active, so you can see at a glance whether audio is arriving.
struct TitleBarMicMeter: View {
    @Environment(AppState.self) private var appState
    var body: some View {
        if appState.voice.conversationActive {
            MicLevelMeter(level: appState.voice.inputLevel, segments: 10)
                .frame(width: 54, height: 12)
                .help("Microphone input level — should move when you speak")
        }
    }
}

/// Symbol + Farbe des Mikrofon-Indikators je Sprach-Status (F030) — eine einzige
/// Quelle der Wahrheit, die sowohl der echte `VoiceMicIndicator` als auch der
/// UITest (`MicIndicatorTestView`) nutzen: grau idle · grün listening · orange
/// thinking · blau speaking.
extension VoiceController.VoiceState {
    var micSymbol: String {
        switch self {
        case .idle: return "mic.slash"
        case .listening: return "mic.fill"
        case .thinking: return "waveform"
        case .speaking: return "speaker.wave.2.fill"
        }
    }
    var micColor: Color {
        switch self {
        case .idle: return .secondary
        case .listening: return .green
        case .thinking: return .orange
        case .speaking: return .blue
        }
    }
}

/// Title-bar voice indicator: a mic button whose colour reflects the assistant
/// state — grey idle · green listening · orange thinking · blue speaking.
struct VoiceMicIndicator: View {
    @Environment(AppState.self) private var appState
    @State private var pulse = false

    private var state: VoiceController.VoiceState { appState.voice.state }

    var body: some View {
        Button {
            appState.voice.toggleListening()
        } label: {
            Image(systemName: symbol)
                .symbolRenderingMode(.hierarchical)
                .foregroundStyle(color)
                .scaleEffect(state == .listening && pulse ? 1.18 : 1.0)
                .animation(.easeInOut(duration: 0.7).repeatForever(autoreverses: true), value: pulse)
        }
        .buttonStyle(.plain)
        .disabled(!appState.voice.sttAvailable)
        .help(helpText)
        .onChange(of: state) { _, s in pulse = (s == .listening) }
    }

    private var symbol: String { state.micSymbol }
    private var color: Color { state.micColor }
    private var helpText: String {
        guard appState.voice.sttAvailable else {
            return "Voice input needs the packaged app (microphone permission)"
        }
        switch state {
        case .idle: return "Start a hands-free conversation with Claude"
        case .listening: return "Listening… just speak; I'll detect when you're done. Click to end."
        case .thinking: return "Claude is working… start speaking to interrupt"
        case .speaking: return "Claude is speaking… start speaking to interrupt"
        }
    }
}

/// A compact pill that renders the current TrustMode with its tint and symbol.
struct TrustModeBadge: View {
    let mode: TrustMode

    var body: some View {
        // F031: das Spec-Indikator-Symbol (⚡/🟢/🟡/🔴) vorangestellt.
        Label {
            Text("\(mode.indicatorEmoji) \(mode.label)")
        } icon: {
            Image(systemName: mode.symbol)
        }
            .labelStyle(.titleAndIcon)
            .font(.caption.weight(.semibold))
            .padding(.horizontal, 9)
            .padding(.vertical, 4)
            .background(mode.tint.opacity(0.16), in: Capsule())
            .foregroundStyle(mode.tint)
            .overlay(Capsule().strokeBorder(mode.tint.opacity(0.35), lineWidth: 1))
            .help(mode.blurb)
    }
}

/// The sidebar: a workspace section and a Definitions section.
struct SidebarView: View {
    @Binding var selection: SidebarItem?
    // F032: die Definitionen-Sektion am unteren Sidebar-Rand ist kollabierbar.
    @State private var definitionsExpanded = true

    var body: some View {
        List(selection: $selection) {
            Section("Workspace") {
                ForEach(SidebarItem.workspace) { item in
                    Label(item.title, systemImage: item.symbol).tag(item)
                }
            }
            Section("Tools") {
                ForEach(SidebarItem.tools) { item in
                    Label(item.title, systemImage: item.symbol).tag(item)
                }
            }
            Section("Definitions", isExpanded: $definitionsExpanded) {
                ForEach(SidebarItem.definitions) { item in
                    Label(item.title, systemImage: item.symbol).tag(item)
                }
            }
        }
        .listStyle(.sidebar)
        .safeAreaInset(edge: .top) {
            HStack(spacing: 11) {
                BrandMark(size: 38)
                    .shadow(color: Color.brandViolet.opacity(0.45), radius: 10, y: 4)
                VStack(alignment: .leading, spacing: 1) {
                    Text("ClaudeStudio")
                        .font(.system(size: 17, weight: .bold))
                    Text("Agentic OS")
                        .font(.caption2.weight(.medium))
                        .foregroundStyle(.brandRich)
                }
                Spacer()
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 14)
            .background(.bar)
        }
    }
}
