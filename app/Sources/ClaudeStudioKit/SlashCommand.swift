import Foundation

/// Ein Slash-Befehl für die Chat-Autovervollständigung: entweder ein
/// eingebauter Claude-Code-Befehl (`/cost`, `/compact`, …) oder eine im Projekt
/// installierte Skill, die als `/<command>` aufgerufen wird.
///
/// Das Modell ist bewusst SwiftUI-frei und reine Logik, damit der Matcher
/// (`query(in:)` / `matches(_:query:)`) im Kit-Test-Target deterministisch
/// geprüft werden kann. Die Darstellung (`SlashCommandMenu`) lebt im App-Target.
public struct SlashCommand: Sendable, Identifiable, Equatable {
    /// Woher der Befehl stammt — steuert das Glyph und die Sortier-Priorität.
    public enum Kind: String, Sendable, Equatable {
        /// Ein eingebauter Claude-Code-CLI-Befehl.
        case builtin
        /// Eine installierte Skill (`~/.claude/commands/` bzw. Projekt-Skills).
        case skill
    }

    /// Das Token nach dem Schrägstrich, z. B. `cost` für `/cost`. Ohne `/`.
    public let token: String
    /// Anzeigename / Kurztitel.
    public let title: String
    /// Eine Zeile Beschreibung, was der Befehl tut.
    public let subtitle: String
    public let kind: Kind
    /// SF-Symbol-Name fürs Icon der Zeile.
    public let glyph: String

    public var id: String { kind.rawValue + "/" + token }

    public init(token: String, title: String, subtitle: String, kind: Kind, glyph: String) {
        // Führenden Schrägstrich tolerieren, intern aber ohne speichern.
        self.token = token.hasPrefix("/") ? String(token.dropFirst()) : token
        self.title = title
        self.subtitle = subtitle
        self.kind = kind
        self.glyph = glyph
    }

    /// Wie der Befehl in den Chat eingefügt wird, inkl. Schrägstrich und
    /// abschließendem Leerzeichen (damit der Cursor gleich Argumente erwartet).
    public var insertion: String { "/" + token + " " }

    // MARK: - Eingebaute Befehle

    /// Die kuratierten Claude-Code-Slash-Befehle, die der Wrapper an die laufende
    /// CLI-Session weiterreicht. Reihenfolge = Default-Anzeigereihenfolge.
    public static let builtins: [SlashCommand] = [
        .init(token: "clear",   title: "Clear",   subtitle: "Verlauf der Konversation leeren",        kind: .builtin, glyph: "eraser"),
        .init(token: "compact", title: "Compact", subtitle: "Kontext zusammenfassen & verdichten",    kind: .builtin, glyph: "arrow.down.right.and.arrow.up.left"),
        .init(token: "cost",    title: "Cost",    subtitle: "Token-Verbrauch & Kosten anzeigen",       kind: .builtin, glyph: "dollarsign.circle"),
        .init(token: "status",  title: "Status",  subtitle: "Session- & Account-Status anzeigen",      kind: .builtin, glyph: "info.circle"),
        .init(token: "model",   title: "Model",   subtitle: "Aktives Modell wechseln",                 kind: .builtin, glyph: "cpu"),
        .init(token: "mcp",     title: "MCP",     subtitle: "MCP-Server verwalten",                    kind: .builtin, glyph: "server.rack"),
        .init(token: "resume",  title: "Resume",  subtitle: "Frühere Session fortsetzen",              kind: .builtin, glyph: "clock.arrow.circlepath"),
        .init(token: "review",  title: "Review",  subtitle: "Aktuelle Änderungen prüfen",              kind: .builtin, glyph: "checkmark.seal"),
        .init(token: "init",    title: "Init",    subtitle: "CLAUDE.md für dieses Projekt erzeugen",   kind: .builtin, glyph: "doc.badge.plus"),
        .init(token: "agents",  title: "Agents",  subtitle: "Sub-Agenten verwalten",                   kind: .builtin, glyph: "person.2"),
        .init(token: "help",    title: "Help",    subtitle: "Verfügbare Befehle auflisten",            kind: .builtin, glyph: "questionmark.circle"),
    ]

    /// Eingebaute Befehle und installierte Skills zu einer Liste zusammenführen.
    /// Skills, deren Token einen eingebauten Befehl doppeln würden, werden
    /// verworfen (eingebaute haben Vorrang). Stabile Reihenfolge: erst Builtins,
    /// dann Skills in Eingangsreihenfolge.
    public static func merged(builtins: [SlashCommand] = SlashCommand.builtins,
                              skills: [SlashCommand]) -> [SlashCommand] {
        var seen = Set(builtins.map { $0.token.lowercased() })
        var out = builtins
        for skill in skills where !seen.contains(skill.token.lowercased()) {
            seen.insert(skill.token.lowercased())
            out.append(skill)
        }
        return out
    }

    // MARK: - Matcher (rein, testbar)

    /// Liefert das gerade getippte Befehls-Token, wenn der Text als Slash-Befehl
    /// am Zeilenanfang beginnt — also `/`, gefolgt von einem Token ohne
    /// Whitespace. Gibt `nil` zurück, sobald der Befehl „fertig" ist (ein
    /// Leerzeichen folgt) oder gar nicht mit `/` beginnt. Beispiele:
    /// `"/"` → `""`, `"/co"` → `"co"`, `"/cost "` → `nil`, `"hi"` → `nil`.
    public static func query(in text: String) -> String? {
        guard text.first == "/" else { return nil }
        let token = text.dropFirst()
        if token.contains(where: { $0.isWhitespace }) { return nil }
        return String(token)
    }

    /// Die zu einem Teil-Token passenden Befehle, nach Relevanz sortiert:
    /// Token-Präfix-Treffer zuerst, dann Token-Teiltreffer, dann Titel-Treffer.
    /// Bei leerem Query die volle Liste in Originalreihenfolge. Die Sortierung
    /// ist deterministisch (Originalindex als Tie-Breaker).
    public static func matches(_ commands: [SlashCommand], query: String) -> [SlashCommand] {
        let needle = query.lowercased()
        guard !needle.isEmpty else { return commands }
        return commands.enumerated().compactMap { index, cmd -> (rank: Int, index: Int, cmd: SlashCommand)? in
            let token = cmd.token.lowercased()
            let rank: Int
            if token.hasPrefix(needle) { rank = 0 }
            else if token.contains(needle) { rank = 1 }
            else if cmd.title.lowercased().contains(needle) { rank = 2 }
            else { return nil }
            return (rank, index, cmd)
        }
        .sorted { ($0.rank, $0.index) < ($1.rank, $1.index) }
        .map { $0.cmd }
    }
}
