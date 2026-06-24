import SwiftUI
import Charts

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
