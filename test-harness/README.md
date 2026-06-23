# test-harness/

Test-Infrastruktur für den autonomen ClaudeStudio-Build-Loop. Jedes Feature aus
`feature_list.json` gilt erst als `passing`, wenn es hier im **echten Betrieb**
verifiziert und mit Evidence belegt wurde (siehe `ClaudeStudio_BuildLoop.md` §0).

## Struktur

```
test-harness/
├── run-test.sh     Führt einen einzelnen Feature-Test aus (./run-test.sh F008)
├── projects/       Mini-Test-Projekte (vom Loop angelegt, siehe unten)
├── mcp-mock/       Lokale echte MCP-Test-Server für MCP-Verifikation
└── evidence/       Screenshots, Logs, Query-Results pro Feature (gitignored)
```

## Die vier Test-Projekte (vom Loop gebaut)

| Projekt | Verifiziert | Beispiel-Tests |
|---|---|---|
| `todo-api` | MCP, Git, Worktrees, Agent-A2A, Security | GitHub-Issue via MCP erstellen+schließen; 2 Worktrees parallel mergen; Security-Scan findet echte Vulnerability |
| `landing-page` | UI, Browser (Playwright), Voice, Design-Mode | Per Computer-Use durch UI klicken + Screenshots; "Farbe auf blau" per Voice |
| `invoice-app` | Task-Library, Compliance (DE/AT) | Kleinunternehmer-Check §19 UStG erzeugt korrekten PDF-Report |
| `data-pipeline` | Vector-DB, Memory, Brain-Graph | Chunk einbetten + semantisch finden (Score >0.7); Fakt über 3 Sessions persistent |

Diese Projekte werden vom Build-Loop selbst angelegt, sobald das jeweilige
Subsystem an der Reihe ist — nicht vom Initializer. `projects/` und `mcp-mock/`
starten daher leer (nur mit `.gitkeep`).

## Evidence-Regel

`evidence/` ist **gitignored** (Screenshots/Logs können groß sein). Lege Evidence
pro Feature unter `evidence/<FEATURE-ID>/` ab. Im `claude-progress.txt`-Eintrag
wird der Evidence-Pfad referenziert, damit ein Reviewer ihn lokal findet.
