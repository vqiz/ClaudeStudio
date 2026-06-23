import SwiftUI

/// Deterministische Design-Galerie — NUR für die headless UI-Verifikation
/// (`CLAUDESTUDIO_UITEST=gallery`). Rendert die Statusfarben-Token (F025) als
/// vier randlose Farbflächen an festen Koordinaten und zwei Karten mit/ohne
/// Eleviations-Schatten (F022). Festes Fenster (760×560), erzwungenes Light-
/// Theme und ein expliziter weißer Hintergrund machen jeden Pixel reproduzierbar
/// per Bild-Inspektion messbar — keine dynamischen System-Farben im Messbereich.
struct DesignGalleryView: View {
    // Feste Layout-Konstanten (Punkte ab oben-links des Inhalts).
    static let badgeW: CGFloat = 130
    static let badgeH: CGFloat = 70
    static let badgeY: CGFloat = 24
    static let badgeXs: [CGFloat] = [20, 180, 340, 500]
    static let cardW: CGFloat = 220
    static let cardH: CGFloat = 120
    static let cardY: CGFloat = 180
    static let cardAX: CGFloat = 60   // elevierte Karte (mit Schatten)
    static let cardBX: CGFloat = 480  // Vergleichskarte (ohne Schatten)

    private let badges: [(String, Color)] = [
        ("accent", .statusAccent), ("success", .statusSuccess),
        ("warning", .statusWarning), ("error", .statusError),
    ]

    var body: some View {
        ZStack(alignment: .topLeading) {
            // Expliziter reinweißer Hintergrund — der Schatten von Karte A ist
            // dadurch als Abdunkelung gegenüber #FFFFFF eindeutig messbar.
            Color.white

            // F025 — vier Statusfarben-Badges als pure Farbflächen (kein Text drüber).
            ForEach(Array(badges.enumerated()), id: \.offset) { idx, badge in
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(badge.1)
                    .frame(width: Self.badgeW, height: Self.badgeH)
                    .offset(x: Self.badgeXs[idx], y: Self.badgeY)
            }

            // F022 — Karte MIT echtem Eleviations-Schatten (der reale dsCard-Modifier,
            // elevated: true → shadow opacity 0.05, radius 3, y 1).
            Color.clear.frame(width: Self.cardW - 2 * DS.s3, height: Self.cardH - 2 * DS.s3)
                .dsCard(elevated: true)
                .offset(x: Self.cardAX, y: Self.cardY)

            // F022 — Vergleichskarte OHNE Schatten (derselbe Modifier, elevated: false).
            Color.clear.frame(width: Self.cardW - 2 * DS.s3, height: Self.cardH - 2 * DS.s3)
                .dsCard(elevated: false)
                .offset(x: Self.cardBX, y: Self.cardY)
        }
        .frame(width: 760, height: 560, alignment: .topLeading)
        .preferredColorScheme(.light)
    }
}
