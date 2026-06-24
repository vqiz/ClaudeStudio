import SwiftUI
import ClaudeStudioKit

/// Das Autovervollständigungs-Popup für Slash-Befehle im Chat-Composer.
///
/// Erscheint über dem Eingabefeld, sobald der Nutzer eine Zeile mit `/` beginnt,
/// und listet die passenden Befehle (eingebaute CLI-Befehle + installierte
/// Skills). Die ausgewählte Zeile ist hervorgehoben; ein Klick (oder Return/Tab)
/// fügt den Befehl ein. Reine Darstellung — Filtern/Auswahl steuert der Aufrufer.
struct SlashCommandMenu: View {
    /// Die bereits gefilterten/sortierten Befehle (siehe `SlashCommand.matches`).
    let commands: [SlashCommand]
    /// Index der hervorgehobenen Zeile (Tastatur-Navigation).
    let selection: Int
    /// Wird mit dem gewählten Befehl aufgerufen (Klick).
    let onPick: (SlashCommand) -> Void

    /// Höchstens so viele Zeilen zeigen; darüber scrollt die Liste.
    private static let maxVisibleRows = 7
    /// Höhe einer Befehlszeile (Icon 26 + 2×7 Padding).
    private static let rowHeight: CGFloat = 44

    /// Definitive Listenhöhe: so hoch wie der Inhalt, gedeckelt bei
    /// `maxVisibleRows`. Eine feste Höhe (statt nur `maxHeight`) verhindert, dass
    /// die scrollbare Liste unter Layout-Druck (z. B. ein `Spacer` darüber) auf
    /// wenige Zeilen zusammenfällt.
    private var listHeight: CGFloat {
        CGFloat(min(commands.count, Self.maxVisibleRows)) * Self.rowHeight
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: 0) {
                        ForEach(Array(commands.enumerated()), id: \.element.id) { index, cmd in
                            row(cmd, isSelected: index == selection)
                                .id(index)
                                .contentShape(Rectangle())
                                .onTapGesture { onPick(cmd) }
                            if index < commands.count - 1 {
                                Divider().opacity(0.4)
                            }
                        }
                    }
                }
                .frame(height: listHeight)
                .onChange(of: selection) { _, new in
                    withAnimation(.easeOut(duration: 0.1)) { proxy.scrollTo(new, anchor: .center) }
                }
            }
        }
        .background(DS.surface)
        .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .stroke(DS.hairline, lineWidth: 1)
        )
        .shadow(color: .black.opacity(0.12), radius: 12, y: 4)
        .frame(maxWidth: 460, alignment: .leading)
    }

    /// Kopfzeile mit Hinweis auf die Tastatur-Navigation.
    private var header: some View {
        HStack(spacing: 6) {
            Image(systemName: "command").font(.caption2)
            Text("Befehle").font(.caption.weight(.semibold))
            Spacer()
            Text("↑↓ wählen · ↵ einfügen · esc")
                .font(.caption2)
                .foregroundStyle(.tertiary)
        }
        .foregroundStyle(.secondary)
        .padding(.horizontal, 12)
        .padding(.vertical, 7)
    }

    private func row(_ cmd: SlashCommand, isSelected: Bool) -> some View {
        HStack(spacing: 10) {
            Image(systemName: cmd.glyph)
                .font(.callout)
                .foregroundStyle(isSelected ? Color.white : .accentColor)
                .frame(width: 26, height: 26)
                .background(
                    RoundedRectangle(cornerRadius: 7, style: .continuous)
                        .fill(isSelected ? Color.accentColor : Color.accentColor.opacity(0.12))
                )
            VStack(alignment: .leading, spacing: 1) {
                HStack(spacing: 6) {
                    Text("/\(cmd.token)")
                        .font(.system(.callout, design: .monospaced).weight(.semibold))
                    if cmd.kind == .skill {
                        Text("Skill")
                            .font(.caption2.weight(.semibold))
                            .padding(.horizontal, 5).padding(.vertical, 1)
                            .background(.purple.opacity(0.16), in: Capsule())
                            .foregroundStyle(.purple)
                    }
                }
                Text(cmd.subtitle)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 7)
        .background(isSelected ? Color.accentColor.opacity(0.10) : Color.clear)
    }
}
