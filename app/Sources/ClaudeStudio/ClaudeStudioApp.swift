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

    /// Headless-UITest-Seam: `CLAUDESTUDIO_UITEST=gallery` rendert die
    /// deterministische Design-Galerie statt der App-Shell, damit die
    /// Design-System-Features (F022/F025) per Bild-Inspektion reproduzierbar
    /// verifiziert werden können. Im Normalbetrieb leer.
    private var uiTestMode: String? {
        ProcessInfo.processInfo.environment["CLAUDESTUDIO_UITEST"]
    }
    /// Breite für das Grid-UITest (F021), Default 1200.
    private var uiTestWidth: CGFloat {
        if let s = ProcessInfo.processInfo.environment["CLAUDESTUDIO_UITEST_WIDTH"],
           let w = Double(s) { return CGFloat(w) }
        return 1200
    }

    var body: some Scene {
        WindowGroup {
            if uiTestMode == "gallery" {
                DesignGalleryView()
                    .environment(appState)
            } else if uiTestMode == "chart" {
                ChartTestView()
            } else if uiTestMode == "grid" {
                GridTestView(width: uiTestWidth)
            } else if uiTestMode == "kpi" {
                KPITestView()
            } else if uiTestMode == "table-asc" {
                SortTableTestView(ascending: true)
            } else if uiTestMode == "table-desc" {
                SortTableTestView(ascending: false)
            } else if uiTestMode == "density-kompakt" {
                DensityTableTestView(density: .kompakt)
            } else if uiTestMode == "density-geraeumig" {
                DensityTableTestView(density: .geraeumig)
            } else if uiTestMode == "theme" {
                ThemeTestView()
            } else if uiTestMode == "mic-idle" {
                MicIndicatorTestView(state: .idle)
            } else if uiTestMode == "mic-listening" {
                MicIndicatorTestView(state: .listening)
            } else if uiTestMode == "mic-thinking" {
                MicIndicatorTestView(state: .thinking)
            } else if uiTestMode == "mic-speaking" {
                MicIndicatorTestView(state: .speaking)
            } else if uiTestMode == "defs-expanded" {
                DefinitionsSectionTestView(expanded: true)
            } else if uiTestMode == "defs-collapsed" {
                DefinitionsSectionTestView(expanded: false)
            } else if uiTestMode == "tooloutput" {
                ToolOutputTestView()
            } else if uiTestMode == "cost-step1" {
                CostCounterTestView(responses: 2)
            } else if uiTestMode == "cost-step2" {
                CostCounterTestView(responses: 6)
            } else if uiTestMode == "panel-collapsed" {
                SessionPanelToolCardsTestView(expanded: false)
            } else if uiTestMode == "panel-expanded" {
                SessionPanelToolCardsTestView(expanded: true)
            } else if uiTestMode == "voicelog-all" {
                VoiceLogSearchTestView(query: "")
            } else if uiTestMode == "voicelog-search" {
                VoiceLogSearchTestView(
                    query: ProcessInfo.processInfo.environment["CLAUDESTUDIO_VOICELOG_QUERY"] ?? "security")
            } else {
                RootView()
                    .environment(appState)
                    .frame(minWidth: 1040, idealWidth: 1320, minHeight: 680, idealHeight: 860)
                    .tint(.brandIndigo)
                    .task {
                        appState.startEventBus()
                        appState.activeSession?.startSimulatedStream()
                        await appState.connectCore()
                    }
            }
        }
        .defaultSize(width: 1320, height: 860)
        .windowStyle(.titleBar)
        .windowToolbarStyle(.unified)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("New Session") { appState.selectedSidebarItem = .projects }
                    .keyboardShortcut("n", modifiers: [.command])
            }
            CommandMenu("Core") {
                Button(appState.coreConnected ? "Reconnect" : "Connect") {
                    Task { await appState.connectCore() }
                }
                .keyboardShortcut("r", modifiers: [.command, .shift])
                Divider()
                Menu("Trust mode") {
                    ForEach(TrustMode.allCases) { mode in
                        Button(mode.label) { appState.globalTrustMode = mode }
                    }
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
