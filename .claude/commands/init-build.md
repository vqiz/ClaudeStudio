---
description: Phase 0 — Build-Umgebung aufsetzen (läuft genau EINMAL)
allowed-tools: Read, Write, Bash, Glob
---

Du bist der Initializer-Agent. Setze die Build-Umgebung für ClaudeStudio auf.
Führe diese Schritte in dieser Reihenfolge aus:

## 1. Spezifikation lesen
Lies `ClaudeStudio_Konzept.md` UND `ClaudeStudio_BuildLoop.md` vollständig.

## 2. feature_list.json erzeugen
Erstelle eine JSON-Datei mit JEDEM einzelnen Feature aus dem Konzept als
end-to-end testbare Einheit. Erwarte ~280 Features. Format pro Feature:

```json
{
  "id": "F001",
  "category": "vector-db",
  "description": "Ein Chunk kann eingebettet und per semantischer Suche gefunden werden",
  "real_world_test": [
    "Starte Qdrant lokal via init.sh",
    "Bette den Text 'Stripe payment integration' ein",
    "Suche semantisch nach 'Zahlungsanbieter'",
    "Verifiziere: Chunk wird mit Score > 0.7 zurückgegeben"
  ],
  "evidence_required": "Query-Ergebnis mit Score als JSON-Log",
  "priority": "critical",
  "depends_on": [],
  "passes": false
}
```

Regeln:
- JSON, nicht Markdown (Modell überschreibt JSON seltener fälschlich).
- Jeder `real_world_test` MUSS ein echter Betriebstest sein, kein "ist verbunden".
- Nach Abhängigkeiten sortieren: Foundation-Features zuerst.
- ALLE auf `"passes": false`.

## 3. init.sh erzeugen
Ein idempotentes Script das die komplette Dev-Umgebung hochfährt: Rust-Core
bauen (cargo build), Qdrant-Container starten, Swift-App im Dev-Modus bauen,
Health-Check aller Komponenten.

## 4. test-harness/ anlegen
```
test-harness/projects/   → Mini-Test-Projekte
test-harness/evidence/   → hier landen Screenshots, Logs, Query-Results
test-harness/run-test.sh → führt einen einzelnen Feature-Test aus
```

## 5. claude-progress.txt anlegen
Leeres Log mit Header:
```
# ClaudeStudio Build Progress
# Format: [SESSION N] [DATUM] [FEATURE-ID] [STATUS] [NOTIZ]
```

## 6. Git initialisieren
`git init`, `.gitignore` (target/, .build/, qdrant-data/, test-harness/evidence/),
dann `git commit -m "chore: initial scaffold + feature list"`.

Es ist INAKZEPTABEL, Tests zu entfernen oder zu vereinfachen. Schreibe lieber zu
viele als zu wenige Features. Jedes muss unabhängig testbar sein.
