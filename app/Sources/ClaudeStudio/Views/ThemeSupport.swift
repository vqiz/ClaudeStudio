import SwiftUI
import AppKit

/// A SwiftUI bridge to `NSVisualEffectView`, used for the transparent theme's
/// behind-window vibrancy.
struct VisualEffectBackground: NSViewRepresentable {
    var material: NSVisualEffectView.Material = .underWindowBackground
    var blending: NSVisualEffectView.BlendingMode = .behindWindow
    var emphasized: Bool = false

    func makeNSView(context: Context) -> NSVisualEffectView {
        let view = NSVisualEffectView()
        view.material = material
        view.blendingMode = blending
        view.state = .active
        view.isEmphasized = emphasized
        return view
    }

    func updateNSView(_ view: NSVisualEffectView, context: Context) {
        view.material = material
        view.blendingMode = blending
        view.isEmphasized = emphasized
    }
}

/// Reaches the hosting `NSWindow` so we can toggle translucency for the
/// transparent theme.
struct WindowConfigurator: NSViewRepresentable {
    var theme: AppTheme

    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        DispatchQueue.main.async { [weak view] in apply(to: view?.window) }
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {
        DispatchQueue.main.async { [weak nsView] in apply(to: nsView?.window) }
    }

    private func apply(to window: NSWindow?) {
        guard let window else { return }
        if theme.isTranslucent {
            window.isOpaque = false
            window.backgroundColor = .clear
            window.titlebarAppearsTransparent = true
        } else {
            window.isOpaque = true
            window.backgroundColor = .windowBackgroundColor
            window.titlebarAppearsTransparent = false
        }
    }
}

/// Applies a theme to a view subtree: forces the color scheme, paints the
/// window background (translucent when requested), and configures the window.
struct ThemedChrome: ViewModifier {
    var theme: AppTheme

    func body(content: Content) -> some View {
        content
            .background(themeBackground.ignoresSafeArea())
            .background(WindowConfigurator(theme: theme))
            .preferredColorScheme(theme.colorScheme)
    }

    @ViewBuilder
    private var themeBackground: some View {
        if theme.isTranslucent {
            VisualEffectBackground(material: .underWindowBackground, blending: .behindWindow)
        } else {
            Color(nsColor: .windowBackgroundColor)
        }
    }
}

extension View {
    /// Apply the ClaudeStudio appearance for the given theme.
    func themedChrome(_ theme: AppTheme) -> some View {
        modifier(ThemedChrome(theme: theme))
    }
}

/// The ClaudeStudio brand mark, drawn entirely with SwiftUI shapes so it renders
/// crisply at any size without a bundled raster asset. Matches `assets/logo.png`:
/// a squircle gradient tile with a supervisor "core" orchestrating three agent
/// nodes (the top one is the active/coral agent).
struct BrandMark: View {
    var size: CGFloat = 28
    var showsTile: Bool = true

    var body: some View {
        ZStack {
            if showsTile {
                RoundedRectangle(cornerRadius: size * 0.26, style: .continuous)
                    .fill(.brandGradient)
                    .overlay(
                        RoundedRectangle(cornerRadius: size * 0.26, style: .continuous)
                            .stroke(.white.opacity(0.12), lineWidth: max(0.5, size * 0.01))
                    )
            }
            GeometryReader { geo in
                let s = min(geo.size.width, geo.size.height)
                let c = CGPoint(x: geo.size.width / 2, y: geo.size.height / 2)
                let orbit = s * 0.275
                let sat = s * 0.072
                let core = s * 0.125
                let angles: [Double] = [-90, 150, 30]
                let sats = angles.map { a in
                    CGPoint(x: c.x + orbit * cos(a * .pi / 180),
                            y: c.y + orbit * sin(a * .pi / 180))
                }
                let nodeColor: Color = showsTile ? .white : .brandViolet

                Path { p in
                    for s in sats { p.move(to: c); p.addLine(to: s) }
                }
                .stroke(nodeColor.opacity(0.55), lineWidth: max(1, s * 0.02))

                ForEach(Array(sats.enumerated()), id: \.offset) { idx, point in
                    Circle()
                        .fill(idx == 0 ? Color.brandCoral : nodeColor)
                        .frame(width: sat * 2, height: sat * 2)
                        .position(point)
                }

                Circle()
                    .fill(nodeColor)
                    .frame(width: core * 2, height: core * 2)
                    .position(c)
                Circle()
                    .fill(showsTile ? Color.brandViolet : Color.white)
                    .frame(width: core * 0.84, height: core * 0.84)
                    .position(c)
            }
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}
