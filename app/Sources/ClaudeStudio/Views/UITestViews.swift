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
