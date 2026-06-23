# ClaudeStudio — Build-Projekt

Du baust ClaudeStudio: eine native macOS-App (Swift/SwiftUI) mit Rust-Core, die
Claude Code als GUI umhüllt. Die vollständige Spezifikation steht in
`ClaudeStudio_Konzept.md`. Der Build-Plan steht in `ClaudeStudio_BuildLoop.md`.

## Das eiserne Verifikations-Gesetz

Ein Feature gilt NUR als fertig, wenn du im ECHTEN Betrieb bewiesen hast dass es
funktioniert. "Verbunden", "implementiert", "sollte gehen" zählen NICHT.

- MCP-Feature: erst fertig wenn du über den Server eine echte Operation
  ausgeführt, das Ergebnis ausgelesen und in einem Test-Projekt verwendet hast.
- UI-Feature: erst fertig wenn du per Browser/Computer-Use durch alle States
  geklickt und Screenshots gemacht hast.
- Agent-A2A: erst fertig wenn eine echte Übergabe im Log nachweisbar floss.
- Vector-DB: erst fertig wenn ein echter Chunk eingebettet, gespeichert und per
  semantischer Suche mit korrektem Score zurückgegeben wurde.

Zeige immer EVIDENCE statt Behauptungen: die Test-Ausgabe, den ausgeführten
Befehl und sein Resultat, oder einen Screenshot. Lege Evidence unter
`test-harness/evidence/` ab.

## Arbeitsregeln

- EIN Feature pro Session. Niemals versuchen, alles auf einmal zu bauen.
- Tests NIEMALS entfernen oder vereinfachen — das führt zu fehlender oder
  fehlerhafter Funktionalität.
- Committe oft mit aussagekräftigen Messages. Git ist dein Sicherheitsnetz.
- Wenn die App kaputt ist: ZUERST reparieren, dann Neues bauen.
- Maximal 3 Reparatur-Versuche pro Feature. Danach: Problem unter "BLOCKED" in
  `claude-progress.txt` schreiben, Feature auf "failing" lassen, weitermachen.
- Wiederholst du einen Fehler: schreib die Lektion hier in CLAUDE.md unter
  "Lessons Learned", damit künftige Sessions ihn nicht wiederholen.

## Stack-Regeln

- Frontend: Swift + SwiftUI (macOS-nativ). Backend-Core: Rust.
- Design: heller Google-Analytics-/Material-Look. Karten, weiche Schatten,
  #1A73E8 als Akzent. Kein dunkles Terminal-Theme als Default.
- Kommentare auf Deutsch. Variablen camelCase, Typen PascalCase.

## Lessons Learned
(Hier trägt Claude im Laufe des Loops automatisch Lektionen ein.)
