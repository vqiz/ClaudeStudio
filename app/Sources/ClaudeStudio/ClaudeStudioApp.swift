import SwiftUI
import AppKit
import ClaudeStudioKit

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

    /// True, wenn die App über einen Headless-Render-/Ausführungs-Seam (AppDelegate) gestartet wurde.
    /// Dann KEIN RootView rendern (dessen `.task` würde sonst einen zweiten Core starten) — der
    /// AppDelegate erledigt das Rendern bzw. die Ausführung und beendet den Prozess via `exit(0)`.
    private var headlessSeamActive: Bool {
        let env = ProcessInfo.processInfo.environment
        return env["CLAUDESTUDIO_RENDER_OVERLAY"] != nil
            || env["CLAUDESTUDIO_RENDER_TABRETENTION"] != nil
            || env["CLAUDESTUDIO_RUN_QUICKACTIONS"] != nil
    }

    var body: some Scene {
        WindowGroup {
            if headlessSeamActive {
                Color.clear
            } else if uiTestMode == "gallery" {
                DesignGalleryView()
                    .environment(appState)
            } else if uiTestMode == "chart" {
                ChartTestView()
            } else if uiTestMode == "grid" {
                GridTestView(width: uiTestWidth)
            } else if uiTestMode == "kpi" {
                KPITestView()
            } else if uiTestMode == "cards" {
                DashboardCardsTestView()
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
            } else if uiTestMode == "split" {
                SessionSplitTestView()
            } else if uiTestMode == "context" {
                ContextBarTestView()
            } else if uiTestMode == "project-workspace" {
                ProjectWorkspaceView(project: Project(
                    name: ProcessInfo.processInfo.environment["CLAUDESTUDIO_PROJECT_NAME"] ?? "data-pipeline",
                    path: ProcessInfo.processInfo.environment["CLAUDESTUDIO_PROJECT_PATH"] ?? "/tmp"))
                    .environment(appState)
                    .frame(width: 1100, height: 720)
            } else if uiTestMode == "webpreview" {
                WebPreviewTestView()
            } else if uiTestMode == "filepreview" {
                FilePreviewTestView()
            } else if uiTestMode == "bgcolor" {
                BackgroundColorTestView()
            } else if uiTestMode == "trust-locked" {
                TrustIndicatorTestView(mode: .readOnly)
            } else if uiTestMode == "trust-ask" {
                TrustIndicatorTestView(mode: .guarded)
            } else if uiTestMode == "trust-trusted" {
                TrustIndicatorTestView(mode: .autonomous)
            } else if uiTestMode == "trust-full" {
                TrustIndicatorTestView(mode: .unleashed)
            } else if uiTestMode == "approval-ask" {
                ApprovalFlowTestView(mode: .guarded)
            } else if uiTestMode == "approval-auto" {
                ApprovalFlowTestView(mode: .unleashed)
            } else if uiTestMode == "think-collapsed" {
                ThinkingSectionTestView(expanded: false)
            } else if uiTestMode == "think-expanded" {
                ThinkingSectionTestView(expanded: true)
            } else if uiTestMode == "findings" {
                FindingsInlineTestView()
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
        // Headless-Render-Seam: eine SwiftUI-View per ImageRenderer OHNE Fenster-Server in eine PNG
        // rendern (umgeht screencapture). Genutzt zur Verifikation von Overlays (z. B. F033).
        if let path = ProcessInfo.processInfo.environment["CLAUDESTUDIO_RENDER_OVERLAY"] {
            renderViewToPNG(AnyView(ShortcutOverlay().frame(width: 520, height: 420)), to: path)
            exit(0)
        }
        // F029-Seam: Tab-State-Erhalt über einen Tab-Wechsel A→B→A nachweisen — drei Renderings mit
        // EINEM gemeinsamen Modell. Der Pro-Tab-State liegt im Modell und überlebt den (neu erzeugenden)
        // `switch`-Wechsel; das dritte A-Rendering zeigt dieselbe Eingabe wie das erste.
        if let dir = ProcessInfo.processInfo.environment["CLAUDESTUDIO_RENDER_TABRETENTION"] {
            let model = TabRetentionModel()
            model.tabState["A"] = "EINGABE-A-77"
            model.tabState["B"] = "EINGABE-B-08"
            model.currentTab = "A"
            renderViewToPNG(AnyView(TabRetentionView(model: model)), to: "\(dir)/1-A.png")
            model.currentTab = "B"   // weg von A
            renderViewToPNG(AnyView(TabRetentionView(model: model)), to: "\(dir)/2-B.png")
            model.currentTab = "A"   // zurück zu A — State erhalten?
            renderViewToPNG(AnyView(TabRetentionView(model: model)), to: "\(dir)/3-A.png")
            exit(0)
        }
        // F054-Seam: die fünf Schnell-Aktionen des Rechtsklickmenüs gegen einen echten Core ausführen
        // (genau der Code-Pfad, den das Kontextmenü nutzt — die Rechtsklick-Geste ist ersetzt), den
        // Menü-Inhalt und den im Monaco-Editor geöffneten Dateiinhalt rendern, Ergebnisse als JSON ablegen.
        if let dir = ProcessInfo.processInfo.environment["CLAUDESTUDIO_RUN_QUICKACTIONS"] {
            let sock = ProcessInfo.processInfo.environment["CLAUDESTUDIO_QA_SOCK"] ?? ""
            let file = ProcessInfo.processInfo.environment["CLAUDESTUDIO_QA_FILE"] ?? ""
            let sid = ProcessInfo.processInfo.environment["CLAUDESTUDIO_QA_SESSION"] ?? ""
            Task { @MainActor in
                await self.runQuickActionsSeam(dir: dir, sock: sock, file: file, sessionId: sid)
                exit(0)
            }
            return
        }
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }

    /// Führt alle Schnell-Aktionen (F054) gegen den Core unter `sock` aus und legt Belege in `dir` ab:
    /// `result.json` (real ausgelöste Operationen je Aktion), `menu.png` (Menü-Inhalt) und `monaco.png`
    /// (die via „In Monaco öffnen“ geladene Datei).
    @MainActor
    private func runQuickActionsSeam(dir: String, sock: String, file: String, sessionId: String) async {
        let client = CoreClient(socketPath: sock)
        var results: [[String: Any]] = []
        var monacoContent = ""
        do {
            try await client.connect()
            let runner = QuickActionRunner(client: client)
            for action in QuickAction.allCases {
                let r = await runner.perform(action, file: file, sessionId: sessionId)
                results.append(["action": action.rawValue, "label": action.label,
                                "ok": r.ok, "op": r.op, "detail": r.detail])
                if action == .openInMonaco,
                   let resp = try? await client.call("file.read", .map(["path": .string(file)])) {
                    monacoContent = resp.payload?["content"]?.stringValue ?? ""
                }
            }
            await client.disconnect()
        } catch {
            results.append(["error": "\(error)"])
        }
        renderViewToPNG(AnyView(QuickActionMenuView()), to: "\(dir)/menu.png")
        let fname = (file as NSString).lastPathComponent
        renderViewToPNG(AnyView(MonacoOpenView(filename: fname, content: monacoContent)), to: "\(dir)/monaco.png")
        if let json = try? JSONSerialization.data(withJSONObject: ["results": results],
                                                  options: [.prettyPrinted, .sortedKeys]) {
            try? json.write(to: URL(fileURLWithPath: "\(dir)/result.json"))
        }
    }

    @MainActor
    private func renderViewToPNG(_ view: AnyView, to path: String) {
        let renderer = ImageRenderer(content: view)
        renderer.scale = 2
        guard let img = renderer.nsImage,
              let tiff = img.tiffRepresentation,
              let rep = NSBitmapImageRep(data: tiff),
              let png = rep.representation(using: .png, properties: [:]) else { return }
        try? png.write(to: URL(fileURLWithPath: path))
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        true
    }

    func applicationWillTerminate(_ notification: Notification) {
        // Stop the core sidecar we spawned (no-op if the user started it).
        MainActor.assumeIsolated { CoreLauncher.shared.terminate() }
    }
}
