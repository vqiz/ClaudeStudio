import SwiftUI

/// Tastenkürzel-Overlay (F033): listet die verfügbaren Tastenkürzel. Wird per Cmd+/ ein-/ausgeblendet
/// (siehe RootView) und per Esc oder Klick auf den abgedunkelten Hintergrund geschlossen.
struct ShortcutOverlay: View {
    var onClose: () -> Void = {}

    private let shortcuts: [(keys: String, label: String)] = [
        ("Cmd N", "Neue Session"),
        ("Cmd K", "Befehlspalette"),
        ("Cmd /", "Tastenkürzel anzeigen"),
        ("Cmd B", "Sidebar umschalten"),
        ("Cmd 1-8", "Projekt-Tab wechseln"),
        ("Esc", "Schließen"),
    ]

    var body: some View {
        ZStack {
            Color.black.opacity(0.4).ignoresSafeArea()
                .onTapGesture(perform: onClose)
            VStack(alignment: .leading, spacing: 14) {
                Text("Tastenkürzel")
                    .font(.system(size: 24, weight: .bold)).foregroundStyle(.black)
                ForEach(shortcuts, id: \.keys) { sc in
                    HStack(spacing: 16) {
                        Text(sc.keys)
                            .font(.system(size: 16, design: .monospaced).weight(.bold))
                            .frame(width: 110, alignment: .leading)
                            .foregroundStyle(.black)
                        Text(sc.label)
                            .font(.system(size: 16)).foregroundStyle(.black)
                    }
                }
            }
            .padding(32)
            .background(Color.white, in: RoundedRectangle(cornerRadius: 16))
            .shadow(color: .black.opacity(0.3), radius: 24, y: 6)
        }
        .preferredColorScheme(.light)
    }
}
