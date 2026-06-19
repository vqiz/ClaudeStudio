import SwiftUI

/// The Claude Studio macOS application entry point.
///
/// A single `WindowGroup` hosts the `RootView` shell (a `NavigationSplitView`).
/// The shared `AppState` is created once and injected into the environment so
/// every view reads from the same observable model.
@main
struct ClaudeStudioApp: App {
    @State private var appState = AppState()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(appState)
                .frame(minWidth: 1_040, minHeight: 680)
                .task {
                    appState.startEventBus()
                    appState.activeSession?.startSimulatedStream()
                    await appState.connectCore()
                }
        }
        .windowStyle(.titleBar)
        .windowToolbarStyle(.unified)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("New Session…") {}
                    .keyboardShortcut("n", modifiers: [.command])
                Button("New Worktree…") {}
                    .keyboardShortcut("n", modifiers: [.command, .shift])
            }
            CommandMenu("Agent") {
                Button("Toggle Voice Assistant") { appState.isListening.toggle() }
                    .keyboardShortcut("l", modifiers: [.command, .shift])
                Divider()
                ForEach(TrustMode.allCases) { mode in
                    Button("Trust: \(mode.label)") { appState.globalTrustMode = mode }
                }
            }
        }

        Settings {
            SettingsView()
                .environment(appState)
                .frame(width: 560, height: 480)
        }
    }
}
