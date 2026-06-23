import SwiftUI

/// The user-selectable appearance for ClaudeStudio.
///
/// `system`/`light`/`dark` map onto SwiftUI's `ColorScheme`. `transparent`
/// follows the system light/dark setting but renders the window chrome with
/// behind-window vibrancy so the desktop blurs through — the classic "native
/// macOS" translucent look.
enum AppTheme: String, CaseIterable, Identifiable, Codable, Sendable {
    case system
    case light
    case dark
    case transparent

    var id: String { rawValue }

    var label: String {
        switch self {
        case .system: return "System"
        case .light: return "Light"
        case .dark: return "Dark"
        case .transparent: return "Transparent"
        }
    }

    var symbol: String {
        switch self {
        case .system: return "circle.lefthalf.filled"
        case .light: return "sun.max"
        case .dark: return "moon.stars"
        case .transparent: return "square.on.square.dashed"
        }
    }

    var blurb: String {
        switch self {
        case .system: return "Follow the macOS appearance setting."
        case .light: return "Always use the light appearance."
        case .dark: return "Always use the dark appearance."
        case .transparent: return "Translucent window chrome with desktop blur (follows system)."
        }
    }

    /// The color scheme to force, or `nil` to follow the system.
    var colorScheme: ColorScheme? {
        switch self {
        case .light: return .light
        case .dark: return .dark
        case .system, .transparent: return nil
        }
    }

    /// Whether this theme uses behind-window vibrancy for the chrome.
    var isTranslucent: Bool { self == .transparent }

    // MARK: Persistence

    private static let storageKey = "claudestudio.appTheme"

    static func load() -> AppTheme {
        guard let raw = UserDefaults.standard.string(forKey: storageKey),
              let theme = AppTheme(rawValue: raw) else { return .system }
        return theme
    }

    func save() {
        UserDefaults.standard.set(rawValue, forKey: Self.storageKey)
    }
}

// MARK: - Brand tokens
//
// Retargeted to a Google-Analytics / Material dashboard palette. The token
// *names* are preserved (the whole app references `brandIndigo`/`brandViolet`/
// `brandCoral` and `brandGradient`), but their values now map onto Google's
// product palette so every surface adopts the dashboard look at once.

extension Color {
    /// #1A73E8 — Google blue. Primary accent / gradient start. (was brand indigo)
    static let brandIndigo = Color(red: 0.102, green: 0.451, blue: 0.910)
    /// #1557B0 — deep Google blue. Gradient end / soft accent shadow. (was violet)
    static let brandViolet = Color(red: 0.082, green: 0.341, blue: 0.690)
    /// #34A853 — Google green. Positive / active-node accent. (was coral)
    static let brandCoral = Color(red: 0.204, green: 0.659, blue: 0.325)

    // Full Google product palette for dashboard components (charts, deltas, KPIs).
    /// #1A73E8 — Google blue.
    static let gBlue = Color(red: 0.102, green: 0.451, blue: 0.910)
    /// #34A853 — Google green (gains, healthy).
    static let gGreen = Color(red: 0.204, green: 0.659, blue: 0.325)
    /// #FBBC04 — Google yellow (warnings, neutral series). Kanonischer Material-Gelbton.
    static let gYellow = Color(red: 0.98431, green: 0.73725, blue: 0.01569)
    /// #EA4335 — Google red (losses, errors). Kanonischer Material-Rotton.
    static let gRed = Color(red: 0.91765, green: 0.26275, blue: 0.20784)
    /// #A142F4 — Google purple (auxiliary series).
    static let gPurple = Color(red: 0.631, green: 0.259, blue: 0.957)
    /// #12B5CB — Google cyan (auxiliary series).
    static let gCyan = Color(red: 0.071, green: 0.710, blue: 0.796)

    /// #F8F9FA — the canonical Google dashboard canvas (behind cards).
    static let gCanvas = Color(red: 0.973, green: 0.976, blue: 0.980)
    /// #DADCE0 — Google hairline / divider grey.
    static let gHairline = Color(red: 0.855, green: 0.863, blue: 0.878)

    /// An ordered palette for multi-series charts and category legends.
    static let gSeries: [Color] = [.gBlue, .gGreen, .gYellow, .gRed, .gPurple, .gCyan]

    // MARK: - Semantische Statusfarben-Token (Akzent/Erfolg/Warnung/Fehler)
    // Exakt die kanonischen Google-Material-Werte; werden für Status-Badges,
    // Chips und Indikatoren verwendet (siehe DesignGalleryView).
    /// #1A73E8 — Akzent / primärer Status.
    static let statusAccent = gBlue
    /// #34A853 — Erfolg / bestanden / aktiv.
    static let statusSuccess = gGreen
    /// #FBBC04 — Warnung / ausstehend.
    static let statusWarning = gYellow
    /// #EA4335 — Fehler / fehlgeschlagen.
    static let statusError = gRed
}

extension ShapeStyle where Self == LinearGradient {
    /// The dashboard accent gradient — a restrained Google-blue wash used on the
    /// few hero accents (icon tiles, primary buttons). Flatter than the old
    /// indigo→violet brand gradient to suit the analytics look.
    static var brandGradient: LinearGradient {
        LinearGradient(
            colors: [.brandIndigo, .brandViolet],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
    }
}
