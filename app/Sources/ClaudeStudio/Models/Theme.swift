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

extension Color {
    /// #6366F1 — gradient start, primary brand indigo.
    static let brandIndigo = Color(red: 0.388, green: 0.400, blue: 0.945)
    /// #8B3FF6 — gradient end, brand violet.
    static let brandViolet = Color(red: 0.545, green: 0.247, blue: 0.965)
    /// #FB7185 — active-agent accent coral.
    static let brandCoral = Color(red: 0.984, green: 0.443, blue: 0.522)
}

extension ShapeStyle where Self == LinearGradient {
    /// The ClaudeStudio indigo→violet brand gradient (top-leading → bottom-trailing).
    static var brandGradient: LinearGradient {
        LinearGradient(
            colors: [.brandIndigo, .brandViolet],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
    }
}
