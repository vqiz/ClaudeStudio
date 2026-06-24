import Foundation

/// Persistenter, durchsuchbarer Voice-Log (F236): speichert ALLE Voice-Interaktionen
/// (Transkript + erkannter Intent + Erledigt-Status + Zeitstempel) als Text und macht sie
/// per Volltextsuche auffindbar. Die Einträge werden in `UserDefaults` persistiert, sodass
/// der Log über Neustarts erhalten bleibt. Mikrofon-unabhängig: das Aufzeichnen erfolgt
/// beim Verarbeiten jeder Voice-Aktion, die Suche/Anzeige hier ist reine Textlogik.
@Observable
final class VoiceLog {
    private(set) var entries: [VoiceLogEntry]

    private static let storageKey = "claudestudio.voiceLog"

    init(entries: [VoiceLogEntry]? = nil) {
        if let entries {
            self.entries = entries
        } else {
            self.entries = Self.load()
        }
    }

    /// Eine neue Interaktion aufzeichnen (neueste zuerst) und persistieren.
    func record(_ entry: VoiceLogEntry) {
        entries.insert(entry, at: 0)
        save()
    }

    /// Volltextsuche über Transkript UND erkannten Intent (case-insensitive, Teilstring).
    /// Leere Anfrage liefert alle Einträge.
    func search(_ query: String) -> [VoiceLogEntry] {
        let needle = query.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !needle.isEmpty else { return entries }
        return entries.filter {
            $0.transcript.lowercased().contains(needle) || $0.intent.lowercased().contains(needle)
        }
    }

    private func save() {
        if let data = try? JSONEncoder().encode(entries) {
            UserDefaults.standard.set(data, forKey: Self.storageKey)
        }
    }

    private static func load() -> [VoiceLogEntry] {
        guard let data = UserDefaults.standard.data(forKey: storageKey),
              let decoded = try? JSONDecoder().decode([VoiceLogEntry].self, from: data),
              !decoded.isEmpty else {
            return VoiceLogEntry.samples
        }
        return decoded
    }
}
