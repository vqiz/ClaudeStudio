import SwiftUI

/// ClaudeStudio design system — retargeted to a Google-Analytics / Material
/// dashboard aesthetic: a light canvas, flat white cards with hairline borders
/// and a single soft elevation, restrained Google-blue accents, KPI metric
/// cards with deltas, sparklines, and chart-card wrappers.
///
/// The public API names (`DS`, `DSCard`/`dsCard`, `PageHeader`, `StatTile`,
/// `SectionCard`, `BrandButtonStyle`/`.brand`, `brandRich`, `pageWash`) are kept
/// stable so every existing screen adopts the new look without edits.
enum DS {
    // Spacing scale (Material 4/8 rhythm).
    static let s1: CGFloat = 6
    static let s2: CGFloat = 10
    static let s3: CGFloat = 16
    static let s4: CGFloat = 24
    static let s5: CGFloat = 36

    // Corner radii — flatter than before, Material card rounding.
    static let rSm: CGFloat = 8
    static let rMd: CGFloat = 12
    static let rLg: CGFloat = 16

    // Semantic surfaces (adaptive light/dark via AppKit system colors).
    /// The page canvas behind cards (#F8F9FA in light).
    static var canvas: Color { Color(nsColor: .underPageBackgroundColor) }
    /// A flat card surface (white in light, elevated grey in dark).
    static var surface: Color { Color(nsColor: .controlBackgroundColor) }
    /// Hairline border / divider.
    static var hairline: Color { Color(nsColor: .separatorColor) }
}

// MARK: - Gradients & materials

extension ShapeStyle where Self == LinearGradient {
    /// A restrained Google-blue accent gradient for the few hero accents
    /// (icon tiles, primary buttons). Flatter than the old 3-stop brand wash.
    static var brandRich: LinearGradient {
        LinearGradient(
            colors: [Color.gBlue, Color.brandViolet],
            startPoint: .topLeading, endPoint: .bottomTrailing
        )
    }

    /// A barely-there top wash used behind page content for subtle depth.
    static var pageWash: LinearGradient {
        LinearGradient(
            colors: [Color.gBlue.opacity(0.04), Color.clear],
            startPoint: .top, endPoint: .bottom
        )
    }
}

// MARK: - Card surface

/// A flat Material card: white surface, hairline border, a single soft shadow,
/// continuous-corner rounding. The foundation of the dashboard look.
struct DSCard: ViewModifier {
    var padding: CGFloat = DS.s3
    var radius: CGFloat = DS.rMd
    var elevated: Bool = true

    func body(content: Content) -> some View {
        content
            .padding(padding)
            .background(DS.surface, in: RoundedRectangle(cornerRadius: radius, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .strokeBorder(DS.hairline, lineWidth: 1)
            )
            .shadow(color: .black.opacity(elevated ? 0.05 : 0), radius: elevated ? 3 : 0, y: elevated ? 1 : 0)
    }
}

extension View {
    /// Wrap content in the standard flat dashboard card surface.
    func dsCard(padding: CGFloat = DS.s3, radius: CGFloat = DS.rMd, elevated: Bool = true) -> some View {
        modifier(DSCard(padding: padding, radius: radius, elevated: elevated))
    }

    /// A subtle interactive lift on hover for clickable cards.
    func dsHoverLift(_ hovering: Bool) -> some View {
        scaleEffect(hovering ? 1.008 : 1)
            .animation(.spring(response: 0.3, dampingFraction: 0.7), value: hovering)
    }

    /// Paint the Google-dashboard canvas behind a whole screen.
    func dashboardCanvas() -> some View {
        background(DS.canvas.ignoresSafeArea())
    }
}

// MARK: - Page header

/// A clean dashboard page header: a compact flat icon tile, the page title, an
/// optional subtitle, and optional trailing controls.
struct PageHeader<Trailing: View>: View {
    let title: String
    let symbol: String
    var subtitle: String?
    @ViewBuilder var trailing: () -> Trailing

    var body: some View {
        HStack(alignment: .center, spacing: DS.s3) {
            RoundedRectangle(cornerRadius: 11, style: .continuous)
                .fill(.brandRich)
                .frame(width: 44, height: 44)
                .overlay(
                    Image(systemName: symbol)
                        .font(.system(size: 19, weight: .semibold))
                        .foregroundStyle(.white)
                )
                .shadow(color: Color.gBlue.opacity(0.25), radius: 6, y: 3)

            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.system(size: 24, weight: .semibold))
                    .foregroundStyle(.primary)
                if let subtitle {
                    Text(subtitle)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
            }
            Spacer(minLength: DS.s3)
            trailing()
        }
        .padding(.bottom, DS.s1)
    }
}

extension PageHeader where Trailing == EmptyView {
    init(title: String, symbol: String, subtitle: String? = nil) {
        self.init(title: title, symbol: symbol, subtitle: subtitle) { EmptyView() }
    }
}

// MARK: - Metric / KPI cards

/// The change of a metric versus the prior period, rendered as a Google-style
/// delta chip: a tinted up/down arrow with a signed percentage.
struct DeltaChip: View {
    /// Signed fraction, e.g. `0.123` → "+12.3%", `-0.04` → "−4.0%".
    let change: Double
    /// When false, a positive change is shown red and a drop green (e.g. cost).
    var higherIsBetter: Bool = true

    private var positive: Bool { change >= 0 }
    private var good: Bool { positive == higherIsBetter }
    private var tint: Color { change == 0 ? .secondary : (good ? .gGreen : .gRed) }

    var body: some View {
        HStack(spacing: 2) {
            Image(systemName: positive ? "arrow.up.right" : "arrow.down.right")
                .font(.caption2.weight(.bold))
            Text(String(format: "%@%.1f%%", positive ? "+" : "−", abs(change) * 100))
                .font(.caption.weight(.semibold))
                .monospacedDigit()
        }
        .foregroundStyle(tint)
        .padding(.horizontal, 7)
        .padding(.vertical, 3)
        .background(tint.opacity(0.12), in: Capsule())
    }
}

/// A tiny inline trend line drawn with a `Path` — the GA scorecard sparkline.
struct Sparkline: View {
    let points: [Double]
    var tint: Color = .gBlue

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width, h = geo.size.height
            let lo = points.min() ?? 0, hi = points.max() ?? 1
            let span = max(hi - lo, 0.0001)
            let step = points.count > 1 ? w / CGFloat(points.count - 1) : w
            let pt: (Int) -> CGPoint = { i in
                CGPoint(x: CGFloat(i) * step,
                        y: h - CGFloat((points[i] - lo) / span) * h)
            }

            ZStack {
                if points.count > 1 {
                    // Soft area fill under the line.
                    Path { p in
                        p.move(to: CGPoint(x: 0, y: h))
                        for i in points.indices { p.addLine(to: pt(i)) }
                        p.addLine(to: CGPoint(x: w, y: h))
                        p.closeSubpath()
                    }
                    .fill(LinearGradient(colors: [tint.opacity(0.22), tint.opacity(0.0)],
                                         startPoint: .top, endPoint: .bottom))
                    // The trend line.
                    Path { p in
                        p.move(to: pt(0))
                        for i in points.indices.dropFirst() { p.addLine(to: pt(i)) }
                    }
                    .stroke(tint, style: StrokeStyle(lineWidth: 1.8, lineCap: .round, lineJoin: .round))
                }
            }
        }
        .accessibilityHidden(true)
    }
}

/// A Google-Analytics scorecard: caption label, a big value, an optional delta
/// chip versus the prior period, and an optional sparkline. The hallmark of the
/// dashboard.
struct MetricCard: View {
    let label: String
    let value: String
    var symbol: String? = nil
    var tint: Color = .gBlue
    var delta: Double? = nil
    var higherIsBetter: Bool = true
    var spark: [Double]? = nil
    var footnote: String? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: DS.s2) {
            HStack(spacing: 7) {
                if let symbol {
                    Image(systemName: symbol)
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(tint)
                        .frame(width: 24, height: 24)
                        .background(tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 7, style: .continuous))
                }
                Text(label.uppercased())
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(.secondary)
                    .tracking(0.6)
                Spacer(minLength: 0)
                if let delta {
                    DeltaChip(change: delta, higherIsBetter: higherIsBetter)
                }
            }

            Text(value)
                .font(.system(size: 28, weight: .medium))
                .monospacedDigit()
                .lineLimit(1)
                .minimumScaleFactor(0.6)

            if let spark, spark.count > 1 {
                Sparkline(points: spark, tint: tint)
                    .frame(height: 30)
            } else if let footnote {
                Text(footnote)
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .dsCard(padding: DS.s3, radius: DS.rMd)
    }
}

/// Kept for back-compat: a compact metric tile (now styled as a flat card).
struct StatTile: View {
    let label: String
    let value: String
    let symbol: String
    var tint: Color = .gBlue

    var body: some View {
        MetricCard(label: label, value: value, symbol: symbol, tint: tint)
    }
}

/// An adaptive grid of metric cards that wraps to the available width — the GA
/// scorecard row.
struct MetricGrid<Content: View>: View {
    var minWidth: CGFloat = 180
    @ViewBuilder var content: () -> Content

    var body: some View {
        LazyVGrid(
            columns: [GridItem(.adaptive(minimum: minWidth), spacing: DS.s3)],
            spacing: DS.s3
        ) {
            content()
        }
    }
}

// MARK: - Chart card & period picker

/// A titled card that frames a chart (or any analytics content), with optional
/// trailing controls in the header — the GA "report module".
struct ChartCard<Content: View, Trailing: View>: View {
    let title: String
    var symbol: String? = nil
    var subtitle: String? = nil
    @ViewBuilder var trailing: () -> Trailing
    @ViewBuilder var content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: DS.s3) {
            HStack(alignment: .firstTextBaseline, spacing: DS.s2) {
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 7) {
                        if let symbol {
                            Image(systemName: symbol).foregroundStyle(Color.gBlue)
                        }
                        Text(title).font(.headline)
                    }
                    if let subtitle {
                        Text(subtitle).font(.caption).foregroundStyle(.secondary)
                    }
                }
                Spacer(minLength: 0)
                trailing()
            }
            content()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .dsCard()
    }
}

extension ChartCard where Trailing == EmptyView {
    init(title: String, symbol: String? = nil, subtitle: String? = nil,
         @ViewBuilder content: @escaping () -> Content) {
        self.init(title: title, symbol: symbol, subtitle: subtitle, trailing: { EmptyView() }, content: content)
    }
}

/// The reporting window selector shown on dashboards.
enum DashboardPeriod: String, CaseIterable, Identifiable {
    case d7 = "7 days"
    case d28 = "28 days"
    case d90 = "90 days"
    case all = "All time"

    var id: String { rawValue }
    var short: String {
        switch self {
        case .d7: return "7D"
        case .d28: return "28D"
        case .d90: return "90D"
        case .all: return "All"
        }
    }
    /// Number of days in the window, or nil for all-time.
    var days: Int? {
        switch self {
        case .d7: return 7
        case .d28: return 28
        case .d90: return 90
        case .all: return nil
        }
    }
}

/// A compact segmented control for choosing the dashboard reporting window.
struct PeriodPicker: View {
    @Binding var period: DashboardPeriod

    var body: some View {
        Picker("Period", selection: $period) {
            ForEach(DashboardPeriod.allCases) { p in
                Text(p.short).tag(p)
            }
        }
        .pickerStyle(.segmented)
        .labelsHidden()
        .fixedSize()
    }
}

// MARK: - Section card (titled group)

/// A titled section presented as a flat card — a labelled header row above the
/// content, all inside the standard card surface.
struct SectionCard<Content: View>: View {
    let title: String
    var symbol: String
    @ViewBuilder var content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: DS.s2) {
            Label(title, systemImage: symbol)
                .font(.headline)
                .foregroundStyle(.primary)
            content()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .dsCard()
    }
}

// MARK: - Buttons

/// A flat Google-blue primary button.
struct BrandButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.callout.weight(.semibold))
            .foregroundStyle(.white)
            .padding(.horizontal, DS.s3)
            .padding(.vertical, DS.s2)
            .background(Color.gBlue, in: RoundedRectangle(cornerRadius: DS.rSm, style: .continuous))
            .shadow(color: Color.gBlue.opacity(0.25), radius: 5, y: 2)
            .opacity(configuration.isPressed ? 0.85 : 1)
            .scaleEffect(configuration.isPressed ? 0.98 : 1)
            .animation(.spring(response: 0.25, dampingFraction: 0.7), value: configuration.isPressed)
    }
}

extension ButtonStyle where Self == BrandButtonStyle {
    static var brand: BrandButtonStyle { BrandButtonStyle() }
}
