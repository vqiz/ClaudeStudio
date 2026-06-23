---
description: Eine Loop-Runde — genau EIN Feature fertig bauen und im Echtbetrieb verifizieren
allowed-tools: Read, Write, Edit, Bash, Grep, Glob
---

Du machst in dieser Session GENAU EIN Feature fertig — vollständig, getestet,
bewiesen. Nicht mehr.

## SCHRITT 1 — Orientierung (immer zuerst)
1. `pwd` ausführen. Du darfst nur in diesem Verzeichnis editieren.
2. `git log --oneline -20` lesen — was wurde zuletzt gemacht?
3. `claude-progress.txt` lesen — wo steht das Projekt? Gibt es BLOCKED-Einträge?
4. `feature_list.json` lesen — welche Features sind noch `"passes": false`?
5. `init.sh` ausführen, Smoke-Test: läuft die App noch?
   Falls kaputt: ZUERST reparieren, bevor du Neues baust.

## SCHRITT 2 — Feature wählen (denk gründlich nach)
Wähle das Feature mit höchster Priorität, das noch `false` ist UND dessen
`depends_on` alle `true` sind. Schreibe dir eine Mini-Spec:
- Was genau baue ich?
- Wie teste ich es im ECHTEN Betrieb (siehe `real_world_test` im JSON)?
- Welche Evidence muss ich am Ende vorlegen?

Delegiere die Planung bei Bedarf an einen `planner`-Subagent.

## SCHRITT 3 — Implementieren (im Worktree)
- Erstelle einen Git-Worktree für dieses Feature (isolierter Branch).
- Implementiere NUR dieses eine Feature.
- Loop: Kontext sammeln → handeln → selbst prüfen → wiederholen.
- Committe Zwischenstände.
Delegiere bei Bedarf an einen `generator`-Subagent.

## SCHRITT 4 — Echter Verifikations-Test (KRITISCH)
Folge dem `real_world_test` aus `feature_list.json` EXAKT. Beispiele:
- MCP: echte Operation über den Server ausführen, Ergebnis in Test-Projekt nutzen,
  Log nach test-harness/evidence/. NICHT "Server antwortet auf ping".
- UI: App starten, per Computer-Use/Puppeteer durch ALLE States klicken,
  Screenshots nach test-harness/evidence/.
- Agent-A2A: A gibt Aufgabe an B, B liefert, A verarbeitet — A2A-Nachricht im Log.
- Vector-DB: echten Text einbetten, semantisch suchen, Score prüfen.

Versuche das Feature aktiv zu WIDERLEGEN. Lass am besten einen `evaluator`-Subagent
mit frischem Context gegenprüfen (der den Code nicht kennt, nur den Test).

## SCHRITT 5 — Verbuchen
- BESTANDEN: setze `"passes": true` (ändere NUR dieses Feld). Worktree mergen.
- NICHT BESTANDEN: max 3 Reparatur-Versuche. Danach Problem unter "BLOCKED" in
  claude-progress.txt, Lektion in CLAUDE.md, Feature bleibt `false`.

## SCHRITT 6 — Clean State & Handoff (immer am Ende)
- `git commit` mit beschreibender Message.
- claude-progress.txt updaten:
  `[SESSION N] [DATUM] [F042] [PASSING] MCP-GitHub getestet, Issue #42 erstellt, evidence/f042.png`
- Wiederholter Fehler? → Lektion in CLAUDE.md unter "Lessons Learned".
- Umgebung sauber hinterlassen (mergeable, keine offenen Bugs, dokumentiert).

EISERN: Ein Feature pro Session. Tests nie entfernen. "Fertig" nur mit Evidence.
