import SwiftUI
import AppKit

/// The Claude Studio macOS application entry point.
///
/// A single `WindowGroup` hosts the `RootView` shell (a `NavigationSplitView`).
/// The shared `AppState` is created once and injected into the environment so
/// every view reads from the same observable model.
@main
struct ClaudeStudioApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
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

/// When launched as a bare SwiftPM executable (`swift run`) there is no `.app`
/// bundle, so macOS would otherwise run ClaudeStudio as a background tool with no
/// Dock icon and no foreground window. This promotes it to a regular app and
/// brings its window to the front, and quits when the last window closes so the
/// dev launcher can tear the core down.
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func applicationWillTerminate(_ notification: Notification) {
        // Stop the core sidecar we spawned (no-op if the user started it).
        MainActor.assumeIsolated { CoreLauncher.shared.terminate() }
    }
}
