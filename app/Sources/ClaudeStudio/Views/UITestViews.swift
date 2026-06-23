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
