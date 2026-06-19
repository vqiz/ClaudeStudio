import SwiftUI

/// The application shell: a three-zone `NavigationSplitView` with a sidebar,
/// a content column that switches on the selected sidebar item, and a trailing
/// session panel. The title bar carries the voice mic indicator and the global
/// TrustMode badge.
struct RootView: View {
    @Environment(AppState.self) private var appState

    var body: some View {
        @Bindable var appState = appState

        NavigationSplitView {
            SidebarView(selection: $appState.selectedSidebarItem)
                .navigationSplitViewColumnWidth(min: 200, ideal: 230, max: 280)
        } detail: {
            detailColumn
        }
        .toolbar { titleBarItems }
        .themedChrome(appState.theme)
    }

    @ViewBuilder
    private var detailColumn: some View {
        switch appState.selectedSidebarItem ?? .projects {
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
        case .voiceLog:
            VoiceLogView()
        case .settings:
            SettingsView()
        case .agentStudio:
            AgentStudioView()
        case .context:
            ContextView()
        }
    }

    @ToolbarContentBuilder
    private var titleBarItems: some ToolbarContent {
        ToolbarItem(placement: .navigation) {
            HStack(spacing: 6) {
                Circle()
                    .fill(appState.coreConnected ? Color.green : Color.orange)
                    .frame(width: 8, height: 8)
                Text(appState.coreConnected ? "Core connected" : "Core offline")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        ToolbarItemGroup(placement: .primaryAction) {
            VoiceMicIndicator()
            TrustModeBadge(mode: appState.globalTrustMode)
        }
    }
}

/// Title-bar microphone indicator that pulses while the voice assistant listens.
struct VoiceMicIndicator: View {
    @Environment(AppState.self) private var appState
    @State private var pulse = false

    var body: some View {
        Button {
            appState.isListening.toggle()
        } label: {
            Image(systemName: appState.isListening ? "mic.fill" : "mic.slash")
                .symbolRenderingMode(.hierarchical)
                .foregroundStyle(appState.isListening ? Color.red : Color.secondary)
                .scaleEffect(appState.isListening && pulse ? 1.18 : 1.0)
                .animation(.easeInOut(duration: 0.7).repeatForever(autoreverses: true), value: pulse)
        }
        .buttonStyle(.plain)
        .help(appState.isListening ? "Voice assistant listening" : "Voice assistant muted")
        .onChange(of: appState.isListening) { _, listening in
            pulse = listening
        }
    }
}

/// A compact pill that renders the current TrustMode with its tint and symbol.
struct TrustModeBadge: View {
    let mode: TrustMode

    var body: some View {
        Label(mode.label, systemImage: mode.symbol)
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

    var body: some View {
        List(selection: $selection) {
            Section("Workspace") {
                ForEach(SidebarItem.workspace) { item in
                    Label(item.title, systemImage: item.symbol).tag(item)
                }
            }
            Section("Definitions") {
                ForEach(SidebarItem.definitions) { item in
                    Label(item.title, systemImage: item.symbol).tag(item)
                }
            }
        }
        .listStyle(.sidebar)
        .safeAreaInset(edge: .top) {
            HStack(spacing: 8) {
                BrandMark(size: 26)
                VStack(alignment: .leading, spacing: 0) {
                    Text("ClaudeStudio").font(.headline)
                    Text("Agentic OS").font(.caption2).foregroundStyle(.secondary)
                }
                Spacer()
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .background(.bar)
        }
    }
}
