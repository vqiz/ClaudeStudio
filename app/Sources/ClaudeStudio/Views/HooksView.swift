import SwiftUI
import ClaudeStudioKit

/// The Hooks view — the Claude hooks configured in `settings.json` (the selected
/// project's `.claude/settings.json` plus the global `~/.claude/settings.json`),
/// read live from the core and grouped by event.
struct HooksView: View {
    @Environment(AppState.self) private var appState
    @State private var hooks: [CoreHook] = []
    @State private var loaded = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                PageHeader(title: "Hooks", symbol: "link", subtitle: subtitle)

                if !appState.coreConnected {
                    unavailable("Core offline", "bolt.horizontal.circle",
                                "Connect the core to load your hooks.")
                } else if !loaded {
                    ProgressView().padding(.top, 40)
                } else if hooks.isEmpty {
                    unavailable("No hooks configured", "link",
                                "Hooks in ~/.claude/settings.json (and the selected project's .claude/settings.json) appear here, grouped by event.")
                } else {
                    ForEach(groupedHooks, id: \.0) { event, items in
                        GroupBox {
                            VStack(alignment: .leading, spacing: 10) {
                                ForEach(items) { hook in HookRow(hook: hook) }
                            }
                            .padding(6)
                        } label: {
                            Label(event, systemImage: "bolt.horizontal")
                        }
                    }
                }
            }
            .padding(20)
        }
        .task(id: taskKey) { await reload() }
    }

    private var subtitle: String {
        appState.coreConnected ? "\(hooks.count) hooks · live from settings.json" : "PreToolUse · PostToolUse · Stop · …"
    }

    private var taskKey: String {
        "\(appState.coreConnected)·\(appState.selectedProject?.path ?? "")"
    }

    private var groupedHooks: [(String, [CoreHook])] {
        Dictionary(grouping: hooks, by: \.event).sorted { $0.key < $1.key }
    }

    private func reload() async {
        loaded = false
        hooks = await appState.core.hooks(cwd: appState.selectedProject?.path)
        loaded = true
    }

    private func unavailable(_ title: String, _ symbol: String, _ message: String) -> some View {
        ContentUnavailableView(title, systemImage: symbol, description: Text(message))
            .padding(.top, 30)
    }
}

private struct HookRow: View {
    let hook: CoreHook

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Text(hook.matcher)
                .font(.caption.weight(.semibold).monospaced())
                .padding(.horizontal, 6).padding(.vertical, 2)
                .background(.quaternary, in: Capsule())
            Text(hook.command.isEmpty ? "—" : hook.command)
                .font(.system(.caption, design: .monospaced))
                .textSelection(.enabled)
            Spacer(minLength: 0)
        }
    }
}
