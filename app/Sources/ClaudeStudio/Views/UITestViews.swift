import SwiftUI
import Charts
import ClaudeStudioKit

/// Tab-State-Erhalt über Tab-Wechsel (F029). Die Tab-Inhalte werden über eine `switch`-Anweisung
/// gerendert (wie im echten ProjectWorkspaceView) — d. h. die Tab-View wird beim Wechsel NEU erzeugt.
/// Der Pro-Tab-State (z. B. eingegebener Text / Scrollposition) liegt deshalb in einem @Observable-
/// Modell, das den Wechsel überlebt: verlässt man Tab A und kehrt zurück, ist A's State erhalten.
@Observable
final class TabRetentionModel {
    var currentTab: String = "A"
    var tabState: [String: String] = [:]
}

struct TabRetentionView: View {
    var model: TabRetentionModel

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                ForEach(["A", "B", "C"], id: \.self) { t in
                    Text("Tab \(t)")
                        .font(.system(size: 15, weight: model.currentTab == t ? .bold : .regular))
                        .foregroundStyle(model.currentTab == t ? Color.white : Color.black)
                        .padding(.horizontal, 14).padding(.vertical, 7)
                        .background(model.currentTab == t ? Color.blue : Color(white: 0.92),
                                    in: RoundedRectangle(cornerRadius: 8))
                }
            }
            .padding(16)
            Divider()
            // `switch` erzeugt die Tab-View bei jedem Wechsel NEU — der State muss daher aus dem Modell kommen.
            switch model.currentTab {
            case "A": tabContent("A")
            case "B": tabContent("B")
            default: tabContent("C")
            }
            Spacer()
        }
        .frame(width: 560, height: 360, alignment: .top)
        .background(Color.white)
        .preferredColorScheme(.light)
    }

    private func tabContent(_ tab: String) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Inhalt von Tab \(tab)").font(.system(size: 18, weight: .bold)).foregroundStyle(.black)
            Text("Eingabe: \(model.tabState[tab] ?? "(leer)")")
                .font(.system(size: 16)).foregroundStyle(.black)
        }
        .padding(20).frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// Token-Verbrauch-Chart (F017) — NUR für die headless UI-Verifikation
/// (`CLAUDESTUDIO_UITEST=chart`). Swift-Charts Area+Line+Point über 7 Tage mit
/// festen Werten; die 7 roten Punkt-Marker und der Hoch-/Tiefpunkt sind per
/// Bild-Inspektion eindeutig messbar. Festes Light-Theme, weißer Hintergrund.
struct ChartTestView: View {
    // F017-Testdaten: 7 Tage Token-Werte (Max 6000 an Tag 6, Min 1000 an Tag 1).
    private let values: [(day: Int, tokens: Int)] = [
        (1, 1000), (2, 3000), (3, 2000), (4, 5000), (5, 4000), (6, 6000), (7, 3500),
    ]

    var body: some View {
        ZStack {
            Color.white
            Chart {
                ForEach(values, id: \.day) { v in
                    AreaMark(x: .value("Tag", v.day), y: .value("Tokens", v.tokens))
                        .foregroundStyle(Color.gBlue.opacity(0.18))
                    LineMark(x: .value("Tag", v.day), y: .value("Tokens", v.tokens))
                        .foregroundStyle(Color.gBlue)
                        .lineStyle(StrokeStyle(lineWidth: 3))
                    PointMark(x: .value("Tag", v.day), y: .value("Tokens", v.tokens))
                        .foregroundStyle(Color.gRed)
                        .symbolSize(160)
                }
            }
            .chartXScale(domain: 1...7)
            .chartYScale(domain: 0...6500)
            .padding(48)
            .frame(width: 700, height: 440)
        }
        .frame(width: 800, height: 520, alignment: .center)
        .preferredColorScheme(.light)
    }
}

/// Persistente Dashboard-Karten-Anordnung (F023): die per Drag&Drop gesetzte Reihenfolge und der
/// Kollabiert-Zustand jeder Karte werden in UserDefaults gespeichert und überleben einen Neustart.
struct DashboardCardLayout: Codable {
    var order: [String]
    var collapsed: [String]

    private static let storageKey = "claudestudio.dashboardLayout"

    static func load() -> DashboardCardLayout {
        if let data = UserDefaults.standard.data(forKey: storageKey),
           let l = try? JSONDecoder().decode(DashboardCardLayout.self, from: data) {
            return l
        }
        return DashboardCardLayout(order: ["A", "B", "C"], collapsed: [])
    }

    func save() {
        if let data = try? JSONEncoder().encode(self) {
            UserDefaults.standard.set(data, forKey: Self.storageKey)
            UserDefaults.standard.synchronize()
        }
    }
}

/// Kollabierbare + per Drag&Drop umsortierbare Dashboard-Karten (F023) — `CLAUDESTUDIO_UITEST=cards`.
/// Ist `CLAUDESTUDIO_CARDLAYOUT` gesetzt (z. B. "B,A,C;C" = Reihenfolge B,A,C, Karte C kollabiert),
/// wird diese Anordnung über das ECHTE `DashboardCardLayout.save()` (UserDefaults) persistiert — das
/// simuliert das Drag&Drop + Kollabieren. Ohne das Env (= Neustart) wird die persistierte Anordnung
/// geladen. So sind Reihenfolge + Kollaps-Zustand und ihre Persistenz über Neustart prüfbar.
struct DashboardCardsTestView: View {
    private let layout: DashboardCardLayout
    private let titles = ["A": "Sessions", "B": "Kosten", "C": "Agenten"]

    init() {
        if let spec = ProcessInfo.processInfo.environment["CLAUDESTUDIO_CARDLAYOUT"] {
            let parts = spec.split(separator: ";", omittingEmptySubsequences: false)
            let order = parts.first.map { $0.split(separator: ",").map(String.init) } ?? ["A", "B", "C"]
            let collapsed = parts.count > 1 ? parts[1].split(separator: ",").map(String.init) : []
            let l = DashboardCardLayout(order: order, collapsed: collapsed)
            l.save()
            self.layout = l
        } else {
            self.layout = DashboardCardLayout.load()
        }
    }

    var body: some View {
        ZStack(alignment: .top) {
            Color.white
            VStack(spacing: 12) {
                ForEach(layout.order, id: \.self) { id in
                    let isCollapsed = layout.collapsed.contains(id)
                    VStack(alignment: .leading, spacing: 6) {
                        HStack(spacing: 6) {
                            Image(systemName: isCollapsed ? "chevron.right" : "chevron.down")
                            Text("Card \(id) \(titles[id] ?? "")")
                                .font(.system(size: 17, weight: .bold)).foregroundStyle(.black)
                        }
                        if !isCollapsed {
                            Text("Inhalt von Karte \(id): Detailwerte und Diagramm.")
                                .font(.system(size: 14)).foregroundStyle(.black)
                        }
                    }
                    .padding(16)
                    .frame(width: 420, alignment: .leading)
                    .background(Color(white: 0.96), in: RoundedRectangle(cornerRadius: 10))
                }
                Spacer()
            }
            .padding(20)
        }
        .frame(width: 480, height: 440, alignment: .top)
        .preferredColorScheme(.light)
    }
}

/// Eine KPI-Metrik (F016): berechnet aus Heute-/Gestern-Rohwert den angezeigten
/// Wert, den absoluten Delta-Text (z. B. "+2", "+0,40") und die Pfeilrichtung.
/// Reine Logik — der Test seedet die Rohwerte, die Anzeige wird daraus errechnet.
struct KpiMetric: Identifiable {
    let id = UUID()
    let label: String
    let today: Double
    let yesterday: Double
    /// Anzahl Nachkommastellen für Wert + Delta (0 = ganzzahlig).
    var decimals: Int = 0
    /// Optionales Suffix am Wert (z. B. "USD").
    var unit: String = ""

    private func fmt(_ v: Double, signed: Bool = false) -> String {
        // Deutsche Dezimaldarstellung mit Komma.
        let s = String(format: "%.\(decimals)f", abs(v)).replacingOccurrences(of: ".", with: ",")
        let sign = signed ? (v >= 0 ? "+" : "−") : ""
        return sign + s
    }

    /// Angezeigter Hauptwert, z. B. "5" oder "1,20 USD".
    var valueText: String { unit.isEmpty ? fmt(today) : "\(fmt(today)) \(unit)" }
    /// Absoluter, vorzeichenbehafteter Delta-Text gegenüber Vortag, z. B. "+2".
    var deltaText: String { fmt(today - yesterday, signed: true) }
    /// Aufwärts-Trend (mehr als gestern)?
    var isUp: Bool { today >= yesterday }
}

/// KPI-Karten-Reihe (F016) — `CLAUDESTUDIO_UITEST=kpi`. Vier Metriken (Sessions
/// heute, Kosten heute, Features passing, aktive Agenten) als Karten mit großem
/// Wert und farbigem Delta-Pfeil + absolutem Delta gegenüber Vortag. Die Werte
/// sind aus dem Seed-Szenario (5/3 Sessions, 1,20/0,80 USD) errechnet und per
/// OCR/Pixel reproduzierbar prüfbar. Festes Light-Theme, weißer Hintergrund.
struct KPITestView: View {
    // F016-Seed: entspricht der gesäten sessions.db (heute/gestern).
    private let metrics: [KpiMetric] = [
        KpiMetric(label: "Sessions heute", today: 5, yesterday: 3),
        KpiMetric(label: "Kosten heute", today: 1.20, yesterday: 0.80, decimals: 2, unit: "USD"),
        KpiMetric(label: "Features passing", today: 314, yesterday: 310),
        KpiMetric(label: "Aktive Agenten", today: 2, yesterday: 1),
    ]

    var body: some View {
        ZStack(alignment: .topLeading) {
            Color.white
            HStack(alignment: .top, spacing: 16) {
                ForEach(metrics) { m in
                    VStack(alignment: .leading, spacing: 10) {
                        Text(m.label)
                            .font(.system(size: 15, weight: .medium))
                            .foregroundStyle(.secondary)
                        Text(m.valueText)
                            .font(.system(size: 38, weight: .bold))
                            .foregroundStyle(.black)
                            .monospacedDigit()
                        HStack(spacing: 4) {
                            Image(systemName: m.isUp ? "arrow.up" : "arrow.down")
                                .font(.system(size: 18, weight: .bold))
                            Text(m.deltaText)
                                .font(.system(size: 19, weight: .semibold))
                                .monospacedDigit()
                        }
                        .foregroundStyle(m.isUp ? Color.gGreen : Color.gRed)
                    }
                    .padding(18)
                    .frame(width: 220, height: 150, alignment: .topLeading)
                    .background(Color.white)
                    .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color(white: 0.88)))
                    .shadow(color: .black.opacity(0.08), radius: 6, y: 2)
                }
            }
            .padding(28)
        }
        .frame(width: 1000, height: 240, alignment: .topLeading)
        .preferredColorScheme(.light)
    }
}

/// Inline-Datei-Vorschau (F057) — `CLAUDESTUDIO_UITEST=filepreview`, Datei aus
/// `CLAUDESTUDIO_PREVIEW_FILE`. Bettet den ECHTEN FilePreview ein, der Bilder/SVG/Markdown direkt
/// aus der echten Datei rendert. Per OCR/Pixel der gerenderten Vorschau nachgewiesen.
struct FilePreviewTestView: View {
    private var fileURL: URL? {
        ProcessInfo.processInfo.environment["CLAUDESTUDIO_PREVIEW_FILE"].map { URL(fileURLWithPath: $0) }
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                Image(systemName: "eye")
                Text("Vorschau").font(.headline)
                Spacer()
                Text(fileURL?.lastPathComponent ?? "—").font(.caption.monospaced()).foregroundStyle(.secondary)
            }
            .padding(10).background(.bar)
            Divider()
            if let fileURL {
                FilePreview(fileURL: fileURL)
            } else {
                ContentUnavailableView("Keine Datei", systemImage: "doc")
            }
        }
        .frame(width: 720, height: 520)
        .preferredColorScheme(.light)
    }
}

/// Eingebettete Browser-Vorschau (F359) — `CLAUDESTUDIO_UITEST=webpreview`, URL aus
/// `CLAUDESTUDIO_PREVIEW_URL`. Bettet den ECHTEN WKWebView (`WebPreview`) ein, der die lokale
/// Dev-Server-Seite lädt und per Live-Reload aktualisiert. Per OCR der gerenderten Seite nachgewiesen.
struct WebPreviewTestView: View {
    private var url: URL {
        URL(string: ProcessInfo.processInfo.environment["CLAUDESTUDIO_PREVIEW_URL"]
            ?? "http://127.0.0.1:8000/") ?? URL(string: "about:blank")!
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                Image(systemName: "globe")
                Text("Browser Preview").font(.headline)
                Spacer()
                Text(url.absoluteString).font(.caption.monospaced()).foregroundStyle(.secondary)
            }
            .padding(10)
            .background(.bar)
            Divider()
            WebPreview(url: url, reloadInterval: 1.0)
        }
        .frame(width: 900, height: 600)
        .preferredColorScheme(.light)
    }
}

/// Per Sprachbefehl gesetzte Hintergrundfarbe (F230) — `CLAUDESTUDIO_UITEST=bgcolor`, Farbe aus
/// `CLAUDESTUDIO_BGCOLOR` (vom voice.run_command erkannten Wert). Wendet die Farbe sichtbar als
/// Fensterhintergrund an — der „visuell angewendete" Teil des Sprachbefehls.
struct BackgroundColorTestView: View {
    private var name: String { ProcessInfo.processInfo.environment["CLAUDESTUDIO_BGCOLOR"] ?? "white" }
    private var color: Color {
        switch name {
        case "blue": return .blue
        case "red": return .red
        case "green": return .green
        case "yellow": return .yellow
        case "black": return .black
        default: return .white
        }
    }
    var body: some View {
        ZStack {
            color
            Text("Hintergrund: \(name)")
                .font(.system(size: 28, weight: .bold))
                .foregroundStyle(name == "white" || name == "yellow" ? .black : .white)
        }
        .frame(minWidth: 600, maxWidth: .infinity, minHeight: 400, maxHeight: .infinity)
        .ignoresSafeArea()
    }
}

/// Trust-Modus-Indikator (F031) — `CLAUDESTUDIO_UITEST=trust-locked|trust-ask|trust-trusted|trust-full`.
/// Rendert den ECHTEN TrustModeBadge je Modus groß; das Spec-Indikator-Symbol (🔴 locked · 🟡 ask ·
/// 🟢 trusted · ⚡ full) ist per Pixelfarbe prüfbar.
struct TrustIndicatorTestView: View {
    let mode: TrustMode

    var body: some View {
        ZStack {
            Color.white
            VStack(spacing: 16) {
                Text(mode.indicatorEmoji).font(.system(size: 100))
                TrustModeBadge(mode: mode).scaleEffect(2.2)
                Text(mode.specLabel).font(.system(size: 18)).foregroundStyle(.secondary)
            }
        }
        .frame(width: 420, height: 320)
        .preferredColorScheme(.light)
    }
}

/// Approval-Flow je Trust-Modus (F143) — `CLAUDESTUDIO_UITEST=approval-ask` (Guarded) bzw.
/// `approval-auto` (Unleashed). Eine riskante/destruktive Operation (rm -rf) wird über die ECHTE
/// `TrustMode.requiresApproval(destructive:)`-Logik bewertet: in Guarded erscheint ein Bestätigungs-
/// Prompt (Approve/Deny), in Unleashed läuft sie ohne Rückfrage. Per OCR nachgewiesen.
struct ApprovalFlowTestView: View {
    let mode: TrustMode
    private let command = "Bash: rm -rf build/"

    var body: some View {
        let needsApproval = mode.requiresApproval(destructive: true)
        return ZStack(alignment: .top) {
            Color.white
            VStack(alignment: .leading, spacing: 14) {
                HStack(spacing: 8) {
                    Label(mode.label, systemImage: mode.symbol).foregroundStyle(mode.tint)
                    Text("Trust-Modus").font(.caption).foregroundStyle(.secondary)
                }
                .font(.headline)

                Text(command).font(.system(.body, design: .monospaced))

                if needsApproval {
                    VStack(alignment: .leading, spacing: 8) {
                        Label("Approval required — riskante Operation wartet auf Bestätigung",
                              systemImage: "hand.raised.fill")
                            .foregroundStyle(.orange).font(.callout.weight(.semibold))
                        HStack(spacing: 10) {
                            Text("Approve").padding(.horizontal, 12).padding(.vertical, 5)
                                .background(.green.opacity(0.18), in: Capsule()).foregroundStyle(.green)
                            Text("Deny").padding(.horizontal, 12).padding(.vertical, 5)
                                .background(.red.opacity(0.18), in: Capsule()).foregroundStyle(.red)
                        }
                        .font(.callout.weight(.semibold))
                    }
                    .padding(12)
                    .background(.orange.opacity(0.10), in: RoundedRectangle(cornerRadius: 8))
                } else {
                    Label("Auto-approved — ohne Rückfrage ausgeführt (succeeded)",
                          systemImage: "checkmark.seal.fill")
                        .foregroundStyle(.green).font(.callout.weight(.semibold))
                        .padding(12)
                        .background(.green.opacity(0.10), in: RoundedRectangle(cornerRadius: 8))
                }
                Spacer()
            }
            .padding(24)
            .frame(width: 520, alignment: .leading)
        }
        .frame(width: 560, height: 320, alignment: .top)
        .preferredColorScheme(.light)
    }
}

/// Active-Context-Bar (F145) — `CLAUDESTUDIO_UITEST=context`. Rendert den ECHTEN ContextBar mit
/// geseedeten Kontext-Blöcken (Dateien + Tools + Memory) und ihren Token-Anteilen. Per OCR
/// nachgewiesen (Blocknamen + Token-Zahlen + Gesamtsumme).
struct ContextBarTestView: View {
    private let blocks: [ContextBlock] = [
        ContextBlock(kind: .file, name: "src/index.js", tokens: 1200),
        ContextBlock(kind: .file, name: "README.md", tokens: 800),
        ContextBlock(kind: .memory, name: "Projekt-Notizen", tokens: 300),
        ContextBlock(kind: .tool, name: "Bash", tokens: 60),
        ContextBlock(kind: .tool, name: "Read", tokens: 40),
    ]

    var body: some View {
        ZStack(alignment: .top) {
            Color.white
            ContextBar(blocks: blocks)
                .frame(width: 460)
                .padding(.top, 16)
        }
        .frame(width: 520, height: 320, alignment: .top)
        .preferredColorScheme(.light)
    }
}

/// Split-View Session + bearbeitete Datei (F146) — `CLAUDESTUDIO_UITEST=split`. Rendert den ECHTEN
/// SessionSplitView: links die Session (Transkript inkl. Edit-Tool-Call), rechts die abgeleitete
/// bearbeitete Datei (src/index.js) read-only. Per OCR beider Spalten nachgewiesen.
struct SessionSplitTestView: View {
    private var events: [SessionEvent] {
        [
            SessionEvent(role: .user, kind: .message("Füge einen DELETE-Endpoint hinzu.")),
            SessionEvent(role: .assistant, kind: .message("Ich bearbeite die Route-Datei.")),
            SessionEvent(role: .tool, kind: .toolCall(ToolCall(
                name: "Edit", input: "src/index.js — DELETE /todos/:id",
                output: "+6 −0", status: .succeeded))),
        ]
    }

    private let content = """
    const express = require('express');
    const app = express();
    let todos = [{ id: 1, title: 'x' }];
    app.get('/todos', (req, res) => res.json(todos));
    app.delete('/todos/:id', (req, res) => {
      todos = todos.filter(t => t.id !== Number(req.params.id));
      res.status(204).end();
    });
    app.listen(3000);
    """

    var body: some View {
        SessionSplitView(events: events, fileContent: content)
            .frame(width: 940, height: 520)
            .preferredColorScheme(.light)
    }
}

/// Extended-Thinking als kollabierbare Sektion (F147) — `CLAUDESTUDIO_UITEST=think-collapsed` bzw.
/// `think-expanded`. Rendert die ECHTE TranscriptRow für ein `.thinking`-Event: zugeklappt nur der
/// "Extended Thinking"-Button, aufgeklappt der vollständige Denkprozess. Per OCR/Pixel nachgewiesen.
struct ThinkingSectionTestView: View {
    let expanded: Bool

    private var event: SessionEvent {
        SessionEvent(role: .assistant, kind: .thinking(
            "Der Nutzer will einen DELETE-Endpoint. Ich prüfe zuerst die bestehende Route-Struktur in "
            + "index.js, dann füge ich app.delete('/todos/:id') hinzu und filtere das Array nach id. "
            + "Anschließend schreibe ich einen Test, der einen 204-Status erwartet."))
    }

    var body: some View {
        ZStack(alignment: .top) {
            Color.white
            VStack(alignment: .leading, spacing: 12) {
                TranscriptRow(event: event, initiallyExpanded: expanded)
            }
            .padding(20)
            .frame(width: 560, alignment: .leading)
        }
        .frame(width: 600, height: 320, alignment: .top)
        .preferredColorScheme(.light)
    }
}

/// Inline-Findings (F148) — `CLAUDESTUDIO_UITEST=findings`. Rendert die ECHTEN TranscriptRow-Karten
/// mit `.finding`-Events: jedes Finding erscheint als hervorgehobener Inline-Block mit Schweregrad,
/// Nachricht und Datei:Zeilennummer (z. B. ein Security-Finding). Per OCR nachgewiesen.
struct FindingsInlineTestView: View {
    private var events: [SessionEvent] {
        [
            SessionEvent(role: .assistant, kind: .message("Sicherheits-Scan abgeschlossen — 2 Findings:")),
            SessionEvent(role: .tool, kind: .finding(CodeFinding(
                file: "src/db.js", line: 42, severity: .high,
                message: "SQL-Injection: ungeprüfte Query-Konkatenation"))),
            SessionEvent(role: .tool, kind: .finding(CodeFinding(
                file: "src/auth.js", line: 88, severity: .medium,
                message: "Hartcodiertes Secret im Quelltext"))),
        ]
    }

    var body: some View {
        ZStack(alignment: .top) {
            Color.white
            VStack(alignment: .leading, spacing: 12) {
                ForEach(events) { e in
                    TranscriptRow(event: e)
                }
            }
            .padding(20)
            .frame(width: 560, alignment: .leading)
        }
        .frame(width: 600, height: 420, alignment: .top)
        .preferredColorScheme(.light)
    }
}

/// Strukturierter Tool-Output (F149) — `CLAUDESTUDIO_UITEST=tooloutput`. Rendert die ECHTEN
/// TranscriptRow-Karten (aufgeklappt) für (a) eine Shell-Ausführung, deren stdout und Exit-Code
/// GETRENNT angezeigt werden, und (b) ein JSON-Resultat, das eingerückt (geparst) statt als
/// Rohtext erscheint — beides strukturiert, nicht als Rohtext.
struct ToolOutputTestView: View {
    private var events: [SessionEvent] {
        [
            SessionEvent(role: .tool, kind: .toolCall(ToolCall(
                name: "Bash", input: "npm test",
                output: "PASS  test/todos.test.js\nTests: 4 passed, 4 total",
                status: .succeeded, exitCode: 0))),
            SessionEvent(role: .tool, kind: .toolCall(ToolCall(
                name: "Read", input: "package.json",
                output: "{\"name\":\"todo-api\",\"version\":\"1.0.0\",\"scripts\":{\"test\":\"jest\"}}",
                status: .succeeded))),
        ]
    }

    var body: some View {
        ZStack(alignment: .top) {
            Color.white
            VStack(alignment: .leading, spacing: 12) {
                ForEach(events) { e in
                    TranscriptRow(event: e, initiallyExpanded: true)
                }
            }
            .padding(20)
            .frame(width: 560, alignment: .leading)
        }
        .frame(width: 600, height: 480, alignment: .top)
        .preferredColorScheme(.light)
    }
}

/// Live-Kosten-Counter (F144) — `CLAUDESTUDIO_UITEST=cost-step1` bzw. `cost-step2`. Seedet eine
/// AgentSession mit unterschiedlich vielen Antwort-Events; der ECHTE CostTracker summiert die
/// Kosten jeder Antwort, sodass der USD-Counter zwischen den Schritten steigt. Gerendert wird der
/// ECHTE SessionCostFooter; der große USD-Wert oben ist derselbe cost.formattedCost (OCR-lesbar).
struct CostCounterTestView: View {
    let responses: Int

    private var session: AgentSession {
        // Jede „Modell-Antwort" trägt 0,012 USD bei — der CostTracker summiert über die Events.
        let events = (0..<responses).map { i in
            SessionEvent(role: .assistant, kind: .message("Antwort \(i + 1)"),
                         costDelta: 0.012, tokenDelta: 180)
        }
        return AgentSession(title: "Demo", projectName: "todo-api", events: events, budgetUSD: 5.0)
    }

    var body: some View {
        let s = session
        return ZStack {
            Color.white
            VStack(spacing: 20) {
                VStack(spacing: 4) {
                    Text("Kosten nach \(responses) Antworten")
                        .font(.system(size: 15)).foregroundStyle(.secondary)
                    Text(s.cost.formattedCost)
                        .font(.system(size: 44, weight: .bold)).monospacedDigit()
                        .foregroundStyle(.black)
                }
                SessionCostFooter(cost: s.cost)
                    .frame(width: 360)
            }
            .padding(28)
        }
        .frame(width: 460, height: 320)
        .preferredColorScheme(.light)
    }
}

/// Session-Panel mit auf-/zuklappbaren Tool-Call-Karten (F137) — `CLAUDESTUDIO_UITEST=panel-collapsed`
/// bzw. `panel-expanded`. Rendert die ECHTEN `TranscriptRow`-Karten (DisclosureGroup + Status-Badge)
/// mit geseedeten Tool-Calls (Edit, Bash). Zugeklappt zeigt jede Karte nur Name + Status; aufgeklappt
/// zusätzlich Input + Output — beides per Bild-Inspektion prüfbar.
struct SessionPanelToolCardsTestView: View {
    let expanded: Bool

    private var events: [SessionEvent] {
        [
            SessionEvent(role: .assistant, kind: .message("Ich wende die Änderung an und führe die Tests aus.")),
            SessionEvent(role: .tool, kind: .toolCall(ToolCall(
                name: "Edit", input: "todo-api/index.js — DELETE /todos/:id hinzufügen",
                output: "1 Datei geändert (+6 −0)", status: .succeeded))),
            SessionEvent(role: .tool, kind: .toolCall(ToolCall(
                name: "Bash", input: "npm test",
                output: "Test Suites: 1 passed\nTests: 4 passed\nexit 0", status: .succeeded))),
        ]
    }

    var body: some View {
        ZStack(alignment: .top) {
            Color.white
            VStack(alignment: .leading, spacing: 12) {
                ForEach(events) { e in
                    TranscriptRow(event: e, initiallyExpanded: expanded)
                }
            }
            .padding(20)
            .frame(width: 560, alignment: .leading)
        }
        .frame(width: 600, height: 480, alignment: .top)
        .preferredColorScheme(.light)
    }
}

/// Durchsuchbarer Voice-Log (F236) — `CLAUDESTUDIO_UITEST=voicelog-all` bzw. `voicelog-search`
/// (Suchbegriff aus `CLAUDESTUDIO_VOICELOG_QUERY`). Nutzt den ECHTEN `VoiceLog.search()` über
/// die geseedeten Einträge und zeigt Treffer-Anzahl + passende Transkripte. Die Suche filtert
/// per Volltext — eine Anfrage 'security' liefert nur die Security-Review-Interaktion.
struct VoiceLogSearchTestView: View {
    let query: String
    private let log = VoiceLog(entries: VoiceLogEntry.samples)

    var body: some View {
        let results = log.search(query)
        return ZStack {
            Color.white
            VStack(alignment: .leading, spacing: 12) {
                Text("Voice-Log")
                    .font(.system(size: 24, weight: .bold)).foregroundStyle(.black)
                Text("Suche: \(query.isEmpty ? "(alle)" : query) — \(results.count) Treffer")
                    .font(.system(size: 16, weight: .medium)).foregroundStyle(.black)
                ForEach(results) { e in
                    Text("• \(e.transcript)")
                        .font(.system(size: 14)).foregroundStyle(.black)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
            }
            .padding(24)
            .frame(width: 640, alignment: .leading)
        }
        .frame(width: 680, height: 360, alignment: .topLeading)
        .preferredColorScheme(.light)
    }
}

/// Kollabierbare Definitionen-Sektion (F032) — `CLAUDESTUDIO_UITEST=defs-expanded` bzw.
/// `defs-collapsed`. Rendert dieselbe kollabierbare Sidebar-`Section(isExpanded:)` mit den
/// ECHTEN `SidebarItem.definitions` (Agent Studio, Context, Definitions Library) wie die
/// echte `SidebarView`. Im expandierten State sind die Unterpunkte sichtbar, im kollabierten
/// ausgeblendet — per OCR prüfbar.
struct DefinitionsSectionTestView: View {
    let expanded: Bool

    var body: some View {
        List {
            Section("Definitions", isExpanded: .constant(expanded)) {
                ForEach(SidebarItem.definitions) { item in
                    Label(item.title, systemImage: item.symbol)
                }
            }
        }
        .listStyle(.sidebar)
        .frame(width: 280, height: 420)
        .preferredColorScheme(.light)
    }
}

/// Mikrofon-Indikator-Test (F030) — `CLAUDESTUDIO_UITEST=mic-idle` bzw. `mic-listening`.
/// Rendert das ECHTE Symbol+Farbe-Mapping (`VoiceController.VoiceState.micSymbol/.micColor`,
/// dieselbe Quelle wie der `VoiceMicIndicator` der Titelleiste): inaktiv ⇒ graues `mic.slash`,
/// aktiv/aufnehmend ⇒ grünes `mic.fill`. Per Pixelfarbe prüfbar (grau vs. grün).
struct MicIndicatorTestView: View {
    let state: VoiceController.VoiceState

    var body: some View {
        ZStack {
            Color.white
            Image(systemName: state.micSymbol)
                .symbolRenderingMode(.monochrome)
                .font(.system(size: 120, weight: .bold))
                .foregroundStyle(state.micColor)
        }
        .frame(width: 320, height: 320)
        .preferredColorScheme(.light)
    }
}

/// Theme-Persistenz-Test (F024) — `CLAUDESTUDIO_UITEST=theme`. Ist `CLAUDESTUDIO_THEME`
/// gesetzt (light/dark/system), wird die Auswahl über das ECHTE `AppTheme.save()`
/// (UserDefaults) persistiert — das simuliert den Umschalt-Toggle. Anschließend rendert
/// die View die Auswahl aus dem ECHTEN `AppTheme.load()` mit dem ECHTEN `.themedChrome()`
/// (Fensterhintergrund + colorScheme). Ohne `CLAUDESTUDIO_THEME` (= App-Neustart) wird die
/// zuvor persistierte Auswahl geladen — so ist Dark-Mode + Persistenz über Neustart per
/// Fensterhintergrund-Helligkeit nachweisbar.
struct ThemeTestView: View {
    private let theme: AppTheme

    init() {
        if let raw = ProcessInfo.processInfo.environment["CLAUDESTUDIO_THEME"],
           let chosen = AppTheme(rawValue: raw) {
            chosen.save()
            UserDefaults.standard.synchronize()  // vor SIGTERM durabel auf Platte schreiben
        }
        self.theme = AppTheme.load()
    }

    var body: some View {
        VStack(spacing: 18) {
            Text("ClaudeStudio")
                .font(.system(size: 34, weight: .bold))
                .foregroundStyle(.primary)
            RoundedRectangle(cornerRadius: 12)
                .fill(Color(nsColor: .controlBackgroundColor))
                .frame(width: 320, height: 90)
                .overlay(Text("Dashboard-Karte").foregroundStyle(.primary))
                .shadow(color: .black.opacity(0.12), radius: 6, y: 2)
            Text("Theme: \(theme.label)")
                .font(.system(size: 15))
                .foregroundStyle(.secondary)
        }
        .frame(width: 600, height: 380)
        .themedChrome(theme)
    }
}

/// Eine Session-Zeile für die Tabellen-Tests (F018/F019).
struct SessionRowData: Identifiable {
    let id = UUID()
    let datum: String
    let projekt: String
    let dauer: String
    let kosten: Double

    var kostenText: String {
        String(format: "%.2f", kosten).replacingOccurrences(of: ".", with: ",")
    }
}

/// Sortierbare Sessions-Tabelle (F018) — `CLAUDESTUDIO_UITEST=table-asc` bzw.
/// `table-desc`. Eine echte SwiftUI `Table` mit Spalten Datum/Projekt/Dauer/Kosten;
/// die Zeilen werden über einen echten `KeyPathComparator` auf die Kosten sortiert
/// (aufsteigend → 0,10 oben; absteigend → 0,90 oben). Der Seam stellt die Richtung,
/// die ein Spaltenkopf-Klick im Betrieb umschaltet, deterministisch ein.
struct SortTableTestView: View {
    let ascending: Bool
    private let rows: [SessionRowData] = [
        SessionRowData(datum: "2026-06-20", projekt: "todo-api", dauer: "12m", kosten: 0.10),
        SessionRowData(datum: "2026-06-21", projekt: "data-pipe", dauer: "30m", kosten: 0.50),
        SessionRowData(datum: "2026-06-22", projekt: "landing", dauer: "18m", kosten: 0.30),
        SessionRowData(datum: "2026-06-23", projekt: "infra", dauer: "45m", kosten: 0.90),
    ]

    var body: some View {
        let order: [KeyPathComparator<SessionRowData>] =
            [KeyPathComparator(\.kosten, order: ascending ? .forward : .reverse)]
        let sorted = rows.sorted(using: order)
        ZStack {
            Color.white
            Table(sorted) {
                TableColumn("Datum", value: \.datum)
                TableColumn("Projekt", value: \.projekt)
                TableColumn("Dauer", value: \.dauer)
                TableColumn("Kosten") { Text($0.kostenText).monospacedDigit() }
            }
            .frame(width: 660, height: 240)
            .padding(20)
        }
        .frame(width: 720, height: 300)
        .preferredColorScheme(.light)
    }
}

/// Dichte-Stufen für Listen/Tabellen (F019).
enum RowDensity {
    case kompakt, komfortabel, geraeumig
    var rowHeight: CGFloat { switch self { case .kompakt: 22; case .komfortabel: 36; case .geraeumig: 52 } }
    var vPadding: CGFloat { switch self { case .kompakt: 2; case .komfortabel: 7; case .geraeumig: 14 } }
}

/// Dichte-umschaltbare Zeilenliste (F019) — `CLAUDESTUDIO_UITEST=density-kompakt`
/// bzw. `density-geraeumig`. 10 Zeilen; Zeilenhöhe + vertikales Padding kommen aus
/// der `RowDensity`. Im Kompakt-Modus ist die Zeilenhöhe (und damit der Zeilen-Pitch)
/// messbar kleiner als im Geräumig-Modus — per Bild-Inspektion zählbar.
struct DensityTableTestView: View {
    let density: RowDensity
    private let rows = Array(1...10)

    var body: some View {
        ZStack(alignment: .top) {
            Color.white
            VStack(spacing: 0) {
                ForEach(rows, id: \.self) { i in
                    HStack {
                        Text("Zeile \(i)")
                            .font(.system(size: 13))
                            .foregroundStyle(.black)
                        Spacer()
                    }
                    .padding(.horizontal, 16)
                    .padding(.vertical, density.vPadding)
                    .frame(height: density.rowHeight, alignment: .leading)
                    .background(i % 2 == 0 ? Color(white: 0.96) : Color.white)
                }
            }
            .frame(width: 360)
        }
        .frame(width: 420, height: 620, alignment: .top)
        .preferredColorScheme(.light)
    }
}

/// Responsives Karten-Grid (F021) — `CLAUDESTUDIO_UITEST=grid`, Breite per
/// `CLAUDESTUDIO_UITEST_WIDTH`. 9 Karten in einem adaptiven LazyVGrid (minimum
/// 260, wie die echte MetricGrid); bei größerer Breite passen mehr Spalten in
/// die erste Reihe — per Bild-Inspektion zählbar.
struct GridTestView: View {
    let width: CGFloat
    private let cards = Array(0..<9)

    var body: some View {
        ZStack(alignment: .topLeading) {
            Color.white
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 260), spacing: 16)], spacing: 16) {
                ForEach(cards, id: \.self) { i in
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .fill(Color.gBlue)
                        .frame(height: 90)
                        .overlay(Text("\(i)").foregroundStyle(.white).font(.title3))
                }
            }
            .padding(16)
        }
        .frame(width: width, height: 600, alignment: .topLeading)
        .preferredColorScheme(.light)
    }
}

/// Rendert den Inhalt des F054-Rechtsklickmenüs (die fünf Schnell-Aktionen) als Menü-Liste —
/// damit der Menü-Inhalt per Bild/OCR verifizierbar ist (die contextMenu-Popup-Geste selbst ist,
/// wie bei allen UI-Features, durch diesen Render-Seam ersetzt).
struct QuickActionMenuView: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("data.csv").font(.system(size: 13, weight: .semibold))
                .foregroundStyle(.secondary).padding(.horizontal, 14).padding(.top, 12).padding(.bottom, 6)
            ForEach(QuickAction.allCases) { action in
                HStack(spacing: 10) {
                    Image(systemName: action.systemImage).frame(width: 18).foregroundStyle(Color.blue)
                    Text(action.label).font(.system(size: 15)).foregroundStyle(.black)
                    Spacer()
                }
                .padding(.horizontal, 14).padding(.vertical, 9)
            }
            .padding(.bottom, 6)
        }
        .frame(width: 300, alignment: .leading)
        .background(Color.white, in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(Color(white: 0.85)))
        .padding(24)
        .frame(width: 360, height: 360, alignment: .center)
        .background(Color(white: 0.96))
        .preferredColorScheme(.light)
    }
}

/// Rendert den Monaco-Editor mit dem geladenen Dateiinhalt (F054, Aktion „In Monaco öffnen“).
/// Der Inhalt wird über den Core (`file.read`) geladen; diese View zeigt ihn — der OCR-Nachweis
/// belegt, dass die Aktion die Datei real im Editor öffnet.
struct MonacoOpenView: View {
    var filename: String
    var content: String
    private var lines: [String] { content.components(separatedBy: "\n") }
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 8) {
                Image(systemName: "chevron.left.forwardslash.chevron.right").foregroundStyle(Color.blue)
                Text("Monaco · \(filename)").font(.system(size: 14, weight: .semibold)).foregroundStyle(.black)
                Spacer()
            }
            .padding(.horizontal, 14).padding(.vertical, 10)
            .background(Color(white: 0.95))
            Divider()
            // Kein ScrollView: ImageRenderer rendert ScrollView-Inhalt nicht zuverlässig.
            VStack(alignment: .leading, spacing: 3) {
                ForEach(Array(lines.enumerated()), id: \.offset) { idx, line in
                    HStack(alignment: .top, spacing: 12) {
                        Text("\(idx + 1)").font(.system(size: 14, design: .monospaced))
                            .foregroundStyle(Color(white: 0.55)).frame(width: 28, alignment: .trailing)
                        Text(line).font(.system(size: 14, design: .monospaced))
                            .foregroundStyle(.black).frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                Spacer(minLength: 0)
            }
            .padding(14).frame(maxWidth: .infinity, alignment: .leading)
        }
        .frame(width: 560, height: 360, alignment: .top)
        .background(Color.white)
        .preferredColorScheme(.light)
    }
}

/// Zeigt das Ergebnis einer GitHub-MCP-Operation in der UI (F250): die per MCP erzeugte Issue-Nummer
/// und ihren Status. Die Daten stammen aus einem ECHTEN MCP-Tool-Aufruf (create_issue/close_issue),
/// nicht aus Mock-Werten der View.
struct GitHubIssueResultView: View {
    var repo: String
    var number: Int
    var title: String
    var closed: Bool
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 10) {
                Image(systemName: "ant.circle.fill").font(.system(size: 26)).foregroundStyle(Color.blue)
                VStack(alignment: .leading, spacing: 2) {
                    Text("GitHub-Issue über MCP").font(.system(size: 18, weight: .bold)).foregroundStyle(.black)
                    Text(repo).font(.system(size: 14)).foregroundStyle(.secondary)
                }
                Spacer()
            }
            Divider()
            HStack(spacing: 12) {
                Text("Issue #\(number)").font(.system(size: 30, weight: .heavy)).foregroundStyle(.black)
                statusChip
                Spacer()
            }
            Text(title).font(.system(size: 16)).foregroundStyle(.black.opacity(0.8))
            Text("Status: \(closed ? "geschlossen" : "offen")")
                .font(.system(size: 15, weight: .medium)).foregroundStyle(.black)
            Spacer()
        }
        .padding(28)
        .frame(width: 520, height: 300, alignment: .topLeading)
        .background(Color.white)
        .preferredColorScheme(.light)
    }

    private var statusChip: some View {
        let label = closed ? "geschlossen" : "offen"
        let color = closed ? Color(red: 0.55, green: 0.36, blue: 0.96) : Color(red: 0.13, green: 0.6, blue: 0.27)
        return Text(label).font(.system(size: 14, weight: .semibold)).foregroundStyle(.white)
            .padding(.horizontal, 12).padding(.vertical, 5)
            .background(color, in: Capsule())
    }
}

/// Zeigt die geöffnete Projekt-Tab-Ansicht GENAU des angeklickten Projekts (F043): den Projekt-Titel
/// plus die acht Tabs. Die Tabs stammen aus der REALEN Quelle `ProjectWorkspaceView.Tab.allCases`
/// (dieselbe Aufzählung, über die ProjectWorkspaceView per `switch` rendert) — ImageRenderer kann den
/// AppKit-`.segmented`-Picker nicht zeichnen, daher diese render-fähige Variante derselben Tab-Liste.
struct ProjectTabsStripView: View {
    var projectName: String
    private var tabs: [ProjectWorkspaceView.Tab] { ProjectWorkspaceView.Tab.allCases }
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 10) {
                Image(systemName: "folder.badge.gearshape").foregroundStyle(Color.blue)
                Text(projectName).font(.system(size: 20, weight: .bold)).foregroundStyle(.black)
                Spacer()
            }
            .padding(.horizontal, 18).padding(.vertical, 14)
            Divider()
            HStack(spacing: 8) {
                ForEach(tabs) { t in
                    HStack(spacing: 5) {
                        Image(systemName: t.symbol).font(.system(size: 12)).foregroundStyle(Color.blue)
                        Text(t.title).font(.system(size: 14, weight: .medium)).foregroundStyle(.black)
                    }
                    .padding(.horizontal, 10).padding(.vertical, 7)
                    .background(Color(white: 0.94), in: RoundedRectangle(cornerRadius: 7))
                }
            }
            .padding(.horizontal, 14).padding(.vertical, 12)
            Divider()
            Text("\(tabs.count) Tabs für „\(projectName)“ geöffnet")
                .font(.system(size: 13)).foregroundStyle(.secondary)
                .padding(.horizontal, 18).padding(.top, 10)
            Spacer()
        }
        .frame(width: 920, height: 220, alignment: .topLeading)
        .background(Color.white)
        .preferredColorScheme(.light)
    }
}

/// Slash-Befehl-Autovervollständigung im Chat-Composer — `CLAUDESTUDIO_UITEST=slash`.
/// Tippt der Nutzer im Chat eine Zeile, die mit „/" beginnt, erscheint dieses
/// Popup mit den passenden Befehlen (eingebaute CLI-Befehle + installierte
/// Skills). Der getippte Text kommt aus `CLAUDESTUDIO_SLASH_QUERY` (Default „/"),
/// z. B. „/co" filtert auf /compact, /cost & /code-review. Nutzt den ECHTEN
/// Matcher `SlashCommand.matches` und das ECHTE `SlashCommandMenu` aus dem Composer.
struct SlashAutocompleteTestView: View {
    private var typed: String { ProcessInfo.processInfo.environment["CLAUDESTUDIO_SLASH_QUERY"] ?? "/" }

    /// Beispiel-Skills, damit das Menü Builtins UND Skills zeigt (wie im echten
    /// Projekt mit installierten `~/.claude/commands/`-Skills).
    private let skills: [SlashCommand] = [
        SlashCommand(token: "graphify", title: "graphify",
                     subtitle: "Beliebigen Input in einen Wissensgraphen umwandeln", kind: .skill, glyph: "sparkles"),
        SlashCommand(token: "commit", title: "commit",
                     subtitle: "Änderungen committen mit aussagekräftiger Message", kind: .skill, glyph: "sparkles"),
        SlashCommand(token: "code-review", title: "code-review",
                     subtitle: "Diff auf Bugs & Vereinfachungen prüfen", kind: .skill, glyph: "sparkles"),
    ]

    private var matches: [SlashCommand] {
        let all = SlashCommand.merged(skills: skills)
        guard let query = SlashCommand.query(in: typed) else { return [] }
        return SlashCommand.matches(all, query: query)
    }

    var body: some View {
        VStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 8) {
                    Label("Live Session", systemImage: "sparkles").font(.headline)
                    Spacer()
                }
                Text("Der Core spawnt die Claude-CLI und streamt das Ergebnis hierher.")
                    .font(.callout).foregroundStyle(.secondary)
                Spacer()
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(16)

            Divider()
            VStack(alignment: .leading, spacing: 8) {
                if !matches.isEmpty {
                    SlashCommandMenu(commands: matches, selection: 0, onPick: { _ in })
                }
                HStack(spacing: 8) {
                    // Eingabefeld-Attrappe mit dem getippten Befehl.
                    HStack(spacing: 1) {
                        Text(typed)
                            .font(.body)
                            .foregroundStyle(.primary)
                        Rectangle().fill(Color.accentColor).frame(width: 2, height: 16)
                        Spacer(minLength: 0)
                    }
                    .padding(.horizontal, 8).padding(.vertical, 7)
                    .background(RoundedRectangle(cornerRadius: 6).fill(Color.white))
                    .overlay(RoundedRectangle(cornerRadius: 6).stroke(DS.hairline))
                    Image(systemName: "arrow.up.circle.fill").font(.title2).foregroundStyle(.tertiary)
                }
            }
            .padding(12)
            .background(.bar)
        }
        .frame(width: 660, height: 470, alignment: .top)
        .preferredColorScheme(.light)
    }
}
