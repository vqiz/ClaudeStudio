import SwiftUI

/// ClaudeStudio design system — spacing, radii, gradients, and premium reusable
/// components for a cohesive, Apple-grade look. Use these instead of ad-hoc
/// paddings/backgrounds so every surface feels part of one product.
enum DS {
    // Spacing scale
    static let s1: CGFloat = 6
    static let s2: CGFloat = 10
    static let s3: CGFloat = 16
    static let s4: CGFloat = 24
    static let s5: CGFloat = 36

    // Corner radii
    static let rSm: CGFloat = 10
    static let rMd: CGFloat = 16
    static let rLg: CGFloat = 22
}

// MARK: - Gradients & materials

extension ShapeStyle where Self == LinearGradient {
    /// A richer 3-stop brand gradient for hero surfaces and accents.
    static var brandRich: LinearGradient {
        LinearGradient(
            colors: [Color.brandIndigo, Color.brandViolet, Color.brandCoral.opacity(0.92)],
            startPoint: .topLeading, endPoint: .bottomTrailing
        )
    }

    /// A very faint top-down wash used behind page content for subtle depth.
    static var pageWash: LinearGradient {
        LinearGradient(
            colors: [Color.brandViolet.opacity(0.10), Color.clear],
            startPoint: .top, endPoint: .bottom
        )
    }
}

// MARK: - Card surface

/// A premium card surface: layered material, hairline border, soft shadow,
/// continuous-corner rounding. The foundation of the whole UI's depth.
struct DSCard: ViewModifier {
    var padding: CGFloat = DS.s3
    var radius: CGFloat = DS.rMd
    var elevated: Bool = true

    func body(content: Content) -> some View {
        content
            .padding(padding)
            .background(.background.secondary, in: RoundedRectangle(cornerRadius: radius, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .strokeBorder(.primary.opacity(0.06), lineWidth: 1)
            )
            .shadow(color: .black.opacity(elevated ? 0.16 : 0), radius: elevated ? 12 : 0, y: elevated ? 5 : 0)
    }
}

extension View {
    /// Wrap content in the standard premium card surface.
    func dsCard(padding: CGFloat = DS.s3, radius: CGFloat = DS.rMd, elevated: Bool = true) -> some View {
        modifier(DSCard(padding: padding, radius: radius, elevated: elevated))
    }

    /// A subtle interactive lift on hover (premium feel for clickable cards).
    func dsHoverLift(_ hovering: Bool) -> some View {
        scaleEffect(hovering ? 1.012 : 1)
            .animation(.spring(response: 0.3, dampingFraction: 0.7), value: hovering)
    }
}

// MARK: - Hero page header

/// A large, premium page header: a gradient icon tile, a massive title, an
/// optional subtitle, and optional trailing controls. Replaces the old compact
/// header everywhere `PageHeader(...)` is used.
struct PageHeader<Trailing: View>: View {
    let title: String
    let symbol: String
    var subtitle: String?
    @ViewBuilder var trailing: () -> Trailing

    var body: some View {
        HStack(alignment: .center, spacing: DS.s3) {
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .fill(.brandRich)
                .frame(width: 56, height: 56)
                .overlay(
                    Image(systemName: symbol)
                        .font(.system(size: 24, weight: .semibold))
                        .foregroundStyle(.white)
                )
                .overlay(
                    RoundedRectangle(cornerRadius: 16, style: .continuous)
                        .strokeBorder(.white.opacity(0.18), lineWidth: 1)
                )
                .shadow(color: Color.brandViolet.opacity(0.45), radius: 14, y: 7)

            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.system(size: 28, weight: .bold))
                    .foregroundStyle(.primary)
                if let subtitle {
                    Text(subtitle)
                        .font(.title3)
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

// MARK: - Stat tile

/// A premium metric tile: small caption label, a big value, and a tinted icon.
struct StatTile: View {
    let label: String
    let value: String
    let symbol: String
    var tint: Color = .brandIndigo

    var body: some View {
        VStack(alignment: .leading, spacing: DS.s2) {
            HStack(spacing: 7) {
                Image(systemName: symbol)
                    .font(.callout.weight(.semibold))
                    .foregroundStyle(tint)
                    .frame(width: 26, height: 26)
                    .background(tint.opacity(0.14), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                Text(label.uppercased())
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(.secondary)
                    .tracking(0.5)
            }
            Text(value)
                .font(.system(size: 22, weight: .semibold))
                .lineLimit(1)
                .minimumScaleFactor(0.7)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .dsCard(padding: DS.s3, radius: DS.rMd, elevated: false)
    }
}

// MARK: - Buttons

/// A prominent brand-gradient button style for primary calls to action.
struct BrandButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.callout.weight(.semibold))
            .foregroundStyle(.white)
            .padding(.horizontal, DS.s3)
            .padding(.vertical, DS.s2)
            .background(.brandRich, in: RoundedRectangle(cornerRadius: DS.rSm, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: DS.rSm, style: .continuous)
                    .strokeBorder(.white.opacity(0.18), lineWidth: 1)
            )
            .shadow(color: Color.brandViolet.opacity(0.35), radius: 8, y: 4)
            .opacity(configuration.isPressed ? 0.85 : 1)
            .scaleEffect(configuration.isPressed ? 0.98 : 1)
            .animation(.spring(response: 0.25, dampingFraction: 0.7), value: configuration.isPressed)
    }
}

extension ButtonStyle where Self == BrandButtonStyle {
    static var brand: BrandButtonStyle { BrandButtonStyle() }
}

// MARK: - Section card (titled group)

/// A titled section presented as a premium card — a labelled header row above
/// the content, all inside the standard card surface.
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
