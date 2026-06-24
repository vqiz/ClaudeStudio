#if canImport(XCTest)
import XCTest
@testable import ClaudeStudioKit

/// Prüft den reinen Matcher hinter der Slash-Befehl-Autovervollständigung im
/// Chat-Composer: Wann erscheint das Menü, und welche Befehle in welcher
/// Reihenfolge.
final class SlashCommandTests: XCTestCase {

    // MARK: query(in:) — wann ist die Zeile ein (noch getipptes) Slash-Token?

    func testQueryDetectsSlashPrefix() {
        XCTAssertEqual(SlashCommand.query(in: "/"), "")
        XCTAssertEqual(SlashCommand.query(in: "/co"), "co")
        XCTAssertEqual(SlashCommand.query(in: "/cost"), "cost")
    }

    func testQueryEndsAtWhitespaceAndNonSlash() {
        // Abgeschlossener Befehl (Leerzeichen folgt) → kein Menü mehr.
        XCTAssertNil(SlashCommand.query(in: "/cost "))
        XCTAssertNil(SlashCommand.query(in: "/cost arg"))
        // Normaler Prompt ist kein Befehl.
        XCTAssertNil(SlashCommand.query(in: "fix the tests"))
        XCTAssertNil(SlashCommand.query(in: ""))
        // Schrägstrich nicht am Anfang zählt nicht.
        XCTAssertNil(SlashCommand.query(in: "and/or"))
    }

    // MARK: matches — Filtern & Ranking

    func testEmptyQueryReturnsAll() {
        XCTAssertEqual(SlashCommand.matches(SlashCommand.builtins, query: "").count,
                       SlashCommand.builtins.count)
    }

    func testPrefixMatchesRankBeforeContains() {
        // "co": Präfix-Treffer (compact, cost) vor Teiltreffer/Titel-Treffer.
        let tokens = SlashCommand.matches(SlashCommand.builtins, query: "co").map(\.token)
        XCTAssertEqual(Array(tokens.prefix(2)), ["compact", "cost"])
    }

    func testFiltersToSingleCommand() {
        let mcp = SlashCommand.matches(SlashCommand.builtins, query: "mcp")
        XCTAssertEqual(mcp.map(\.token), ["mcp"])
    }

    func testNoMatchReturnsEmpty() {
        XCTAssertTrue(SlashCommand.matches(SlashCommand.builtins, query: "zzz").isEmpty)
    }

    // MARK: merged — Builtins + Skills

    func testMergedAppendsSkillsAndBuiltinsWin() {
        let skills = [
            SlashCommand(token: "graphify", title: "graphify", subtitle: "", kind: .skill, glyph: "sparkles"),
            // Doppelt zum eingebauten /cost → muss verworfen werden.
            SlashCommand(token: "cost", title: "Skill cost", subtitle: "", kind: .skill, glyph: "sparkles"),
        ]
        let merged = SlashCommand.merged(skills: skills)
        // graphify ist dabei, der doppelte cost-Skill nicht.
        XCTAssertTrue(merged.contains { $0.token == "graphify" && $0.kind == .skill })
        XCTAssertEqual(merged.filter { $0.token == "cost" }.count, 1)
        XCTAssertEqual(merged.first { $0.token == "cost" }?.kind, .builtin)
    }

    func testInsertionFormat() {
        let cost = SlashCommand.builtins.first { $0.token == "cost" }
        XCTAssertEqual(cost?.insertion, "/cost ")
        // Führender Schrägstrich im Token wird normalisiert.
        XCTAssertEqual(SlashCommand(token: "/foo", title: "", subtitle: "", kind: .skill, glyph: "x").token, "foo")
    }
}
#endif
