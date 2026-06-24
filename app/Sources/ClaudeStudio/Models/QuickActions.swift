import Foundation
import AppKit
import ClaudeStudioKit

/// Schnell-Aktionen im Rechtsklick-/Kontextmenü einer Datei (F054). Jede Aktion löst die
/// jeweils ECHTE Operation aus — drei davon über den Rust-Core (`session.inject`,
/// `session.create`, `file.to_asset`, `file.read`), zwei lokal über macOS bzw. den Monaco-Editor.
enum QuickAction: String, CaseIterable, Identifiable {
    case attachToSession   // An Session anhängen
    case explain           // Von Claude erklären
    case markAsset         // Als Asset markieren
    case openInMonaco      // In Monaco öffnen
    case revealInFinder    // Im Finder zeigen

    var id: String { rawValue }

    /// Menü-Beschriftung (Deutsch, wie in der Feature-Spec).
    var label: String {
        switch self {
        case .attachToSession: return "An Session anhängen"
        case .explain:         return "Von Claude erklären"
        case .markAsset:       return "Als Asset markieren"
        case .openInMonaco:    return "In Monaco öffnen"
        case .revealInFinder:  return "Im Finder zeigen"
        }
    }

    var systemImage: String {
        switch self {
        case .attachToSession: return "paperclip"
        case .explain:         return "questionmark.bubble"
        case .markAsset:       return "star"
        case .openInMonaco:    return "chevron.left.forwardslash.chevron.right"
        case .revealInFinder:  return "folder"
        }
    }
}

/// Ergebnis einer ausgeführten Schnell-Aktion — beschreibt die tatsächlich ausgelöste Operation,
/// damit der Aufrufer (UI bzw. Verifikations-Seam) den echten Effekt belegen kann.
struct QuickActionResult {
    let action: QuickAction
    let ok: Bool
    /// Die real ausgeführte Operation (Core-Methode bzw. macOS-Call).
    let op: String
    /// Aktionsspezifisches Detail (Node-Id, neue Session-Id, gelesener Inhalt, URL …).
    var detail: [String: String] = [:]
}

/// Führt die Schnell-Aktionen gegen einen verbundenen `CoreClient` (bzw. lokales macOS) aus.
/// Genau dieser Code-Pfad wird sowohl vom Kontextmenü als auch vom Verifikations-Seam genutzt —
/// die Rechtsklick-Geste ist (wie bei allen UI-Features) durch den direkten Aufruf ersetzt.
struct QuickActionRunner {
    let client: CoreClient

    func perform(_ action: QuickAction, file: String, sessionId: String) async -> QuickActionResult {
        switch action {
        case .attachToSession:
            // Datei-Referenz als Nachricht in die laufende Session einschleusen.
            let resp = try? await client.call("session.inject", .map([
                "session_id": .string(sessionId),
                "message": .string("Angehängte Datei: \(file)"),
            ]))
            let ok = resp?.payload?["injected"]?.boolValue ?? false
            return QuickActionResult(action: action, ok: ok, op: "session.inject",
                                     detail: ["session_id": sessionId])

        case .explain:
            // Neue Session erzeugen, die Claude um eine Erklärung der Datei bittet.
            let name = (file as NSString).lastPathComponent
            let cwd = (file as NSString).deletingLastPathComponent
            let created = try? await client.call("session.create", .map([
                "title": .string("Erkläre \(name)"),
                "cwd": .string(cwd.isEmpty ? "/tmp" : cwd),
            ]))
            let newId = created?.payload?["id"]?.stringValue ?? ""
            if !newId.isEmpty {
                _ = try? await client.call("session.inject", .map([
                    "session_id": .string(newId),
                    "message": .string("Erkläre mir die Datei \(file)"),
                ]))
            }
            return QuickActionResult(action: action, ok: !newId.isEmpty, op: "session.create+inject",
                                     detail: ["session_id": newId, "title": "Erkläre \(name)"])

        case .markAsset:
            // Datei als Asset im Wissensgraphen markieren.
            let resp = try? await client.call("file.to_asset", .map([
                "path": .string(file),
            ]))
            let ok = resp?.payload?["ok"]?.boolValue ?? false
            return QuickActionResult(action: action, ok: ok, op: "file.to_asset",
                                     detail: ["node_id": resp?.payload?["node_id"]?.stringValue ?? "",
                                              "label": resp?.payload?["label"]?.stringValue ?? ""])

        case .openInMonaco:
            // Dateiinhalt über den Core laden — dieser Inhalt wird im Monaco-Editor angezeigt.
            let resp = try? await client.call("file.read", .map([
                "path": .string(file),
            ]))
            let content = resp?.payload?["content"]?.stringValue ?? ""
            let exists = resp?.payload?["exists"]?.boolValue ?? false
            return QuickActionResult(action: action, ok: exists && !content.isEmpty, op: "file.read→monaco",
                                     detail: ["bytes": String(content.utf8.count)])

        case .revealInFinder:
            // Echte lokale macOS-Operation: die Datei im Finder selektieren.
            let url = URL(fileURLWithPath: file)
            NSWorkspace.shared.activateFileViewerSelecting([url])
            let exists = FileManager.default.fileExists(atPath: file)
            return QuickActionResult(action: action, ok: exists, op: "NSWorkspace.activateFileViewerSelecting",
                                     detail: ["url": url.absoluteString])
        }
    }
}
