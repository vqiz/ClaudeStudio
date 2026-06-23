# ClaudeStudio — Autonomer Build-Loop
> Ein selbstlaufender Claude-Code-Loop der ClaudeStudio Feature für Feature baut, **jedes Feature in einem echten Test verifiziert**, und niemals "fertig" sagt bevor es bewiesen ist.

> **Basiert auf:** Anthropics offiziellem Long-Running-Agent-Harness (Nov 2025), dem Claude Agent SDK Loop (gather context → take action → verify → repeat), dem Planner/Generator/Evaluator-Pattern, und Boris Chernys "mistakes → memory"-Prinzip.

---

## 0. Das eiserne Verifikations-Gesetz

> **Ein Feature gilt NUR dann als fertig, wenn Claude im echten Betrieb bewiesen hat, dass es funktioniert. "Verbunden", "implementiert", "sollte gehen" zählen NICHT.**

Konkret bedeutet das für jede Feature-Kategorie:

| Feature-Typ | NICHT fertig | Fertig (Definition of Done) |
|---|---|---|
| MCP-Server-Integration | "MCP-Server ist verbunden, Claude hat Zugang" | Claude hat über den MCP-Server eine echte Operation ausgeführt, das Ergebnis ausgelesen und in einem Test-Projekt sinnvoll verwendet — mit Screenshot/Log als Beweis |
| Agent-Zusammenspiel | "Zwei Agenten sind konfiguriert" | Agent A hat eine Aufgabe an Agent B übergeben, B hat geliefert, A hat das Ergebnis weiterverarbeitet — A2A-Nachrichten im Log sichtbar |
| Vector-DB | "Qdrant läuft, Collection existiert" | Ein echter Chunk wurde eingebettet, gespeichert, und eine semantische Suche hat ihn mit korrektem Score zurückgegeben — Query-Ergebnis als Beweis |
| Voice | "Mikrofon-Zugriff funktioniert" | Ein gesprochener Befehl wurde transkribiert, korrekt geparst, ausgeführt, und die Antwort wurde hörbar ausgegeben — Audio-Roundtrip-Log |
| UI-Komponente | "Komponente rendert" | Claude hat per Browser/Computer-Use durch die UI geklickt, alle States durchgespielt, Edge-Cases getestet — Screenshots aller States |
| Git/Worktree | "Worktree wurde erstellt" | Ein Agent hat im Worktree gearbeitet, committet, der Branch wurde gemerged, kein Konflikt — git log als Beweis |
| Task-Ausführung | "Task ist definiert" | Task wurde auf einem echten Test-Projekt ausgeführt, hat korrekten Output produziert, der Output wurde validiert |

**Beweis-Pflicht (aus Anthropics Harness-Guide):** Claude zeigt *Evidence*, nicht Behauptungen — die Test-Ausgabe, den ausgeführten Befehl und sein Resultat, oder einen Screenshot. Reviewen von Evidence ist schneller als selbst nachzuprüfen, und es funktioniert für Sessions die niemand beobachtet hat.

---

## 1. Loop-Architektur (Überblick)

Der Build-Loop folgt Anthropics Zwei-Phasen-Harness, erweitert um eine dritte Verifikations-Rolle (Planner/Generator/Evaluator):

```
┌──────────────────────────────────────────────────────────────────┐
│  PHASE 0 — INITIALIZER (läuft genau EINMAL)                       │
│  ────────────────────────────────────────────                     │
│  Erstellt: feature_list.json (alle ~280 Features, alle "failing") │
│           init.sh (baut + startet ClaudeStudio dev-Umgebung)      │
│           claude-progress.txt (leeres Log)                        │
│           test-harness/ (Test-Infrastruktur)                      │
│           initial git commit                                      │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────┐
│  LOOP — läuft bis alle Features "passing" (jede Session frisch)   │
│                                                                    │
│   ┌────────────────────────────────────────────────────────┐    │
│   │ SCHRITT 1 — GET BEARINGS (Orientierung)                 │    │
│   │  pwd · git log -20 · read claude-progress.txt           │    │
│   │  read feature_list.json · run init.sh                   │    │
│   │  → Smoke-Test: läuft die App überhaupt noch?            │    │
│   └────────────────────────────────────────────────────────┘    │
│                          ▼                                        │
│   ┌────────────────────────────────────────────────────────┐    │
│   │ SCHRITT 2 — PLANNER (Opus)                              │    │
│   │  Wähle EIN Feature mit höchster Priorität, das "failing"│    │
│   │  Schreibe Mini-Spec: was, wie, welcher Test beweist es  │    │
│   └────────────────────────────────────────────────────────┘    │
│                          ▼                                        │
│   ┌────────────────────────────────────────────────────────┐    │
│   │ SCHRITT 3 — GENERATOR (Sonnet, in Worktree)            │    │
│   │  Implementiere NUR dieses eine Feature                  │    │
│   │  gather context → take action → verify → repeat         │    │
│   └────────────────────────────────────────────────────────┘    │
│                          ▼                                        │
│   ┌────────────────────────────────────────────────────────┐    │
│   │ SCHRITT 4 — EVALUATOR (Opus, FRISCHER Context)         │    │
│   │  Echter Test im realen Umfeld (siehe §0)                │    │
│   │  Versucht das Feature zu WIDERLEGEN, nicht zu bestätigen│    │
│   │  Sammelt Evidence (Screenshot/Log/Query-Result)         │    │
│   └────────────────────────────────────────────────────────┘    │
│                          ▼                                        │
│              ╔═══════════════════════╗                           │
│              ║  Test bestanden?       ║                           │
│              ╚═══════════════════════╝                           │
│                  │ JA          │ NEIN                             │
│                  ▼             ▼                                  │
│        ┌──────────────┐  ┌──────────────────────────┐           │
│        │ SCHRITT 5a   │  │ SCHRITT 5b — FIX-LOOP     │           │
│        │ Mark passing │  │ Bug an Generator zurück   │           │
│        │ git commit   │  │ Max 3 Versuche, dann      │           │
│        │ progress++   │  │ → Mistake-Log → eskalieren│           │
│        └──────────────┘  └──────────────────────────┘           │
│                  │                                                │
│                  ▼                                                │
│   ┌────────────────────────────────────────────────────────┐    │
│   │ SCHRITT 6 — CLEAN STATE & HANDOFF                       │    │
│   │  git commit (descriptive) · update claude-progress.txt  │    │
│   │  update feature_list.json · Lessons → CLAUDE.md/skill   │    │
│   └────────────────────────────────────────────────────────┘    │
│                          │                                        │
│                          ▼ (nächste Session, frischer Context)   │
└──────────────────────────────────────────────────────────────────┘
```

---

## 2. Loop-Guards (gegen Endlos-Schleifen)

> Aus der Produktions-Praxis: Das häufigste Failure-Pattern ist ein Loop der nie terminiert. Das SDK baut keine Loop-Guards ein — der Entwickler braucht numerische Iterations-Limits UND Wiederholungs-Erkennung. Anthropic empfiehlt explizit harte Caps auf Harness-Ebene statt sich auf Billing-Alerts zu verlassen.

ClaudeStudio Build-Loop hat vier Schutzmechanismen:

**1. Numerisches Iterations-Limit**
- Max 3 Fix-Versuche pro Feature → dann Mistake-Log + Eskalation an User
- Max 50 Sessions pro 24h (konfigurierbar) → dann Pause + Report

**2. Repetition-Detection**
- Wenn der Evaluator denselben Fehler 2× hintereinander sieht → Stop, anderer Ansatz erzwungen
- Wenn der git-Diff zweier Sessions identisch ist → "kein Fortschritt"-Alarm

**3. Budget-Cap**
- Hartes USD-Tageslimit (z.B. $20/Tag) → bei Erreichen sofort Pause
- Token-Cap pro Session → Auto-Compact, dann Handoff

**4. Progress-Watchdog**
- Wenn 3 Sessions in Folge kein einziges Feature von "failing" auf "passing" bringen → Loop pausiert, Report an User: "Stecke fest bei Feature X, hier ist warum"

---

## 3. PHASE 0 — Initializer-Prompt (läuft einmal)

```markdown
# ROLLE: Initializer-Agent für ClaudeStudio

Du richtest die Build-Umgebung für ClaudeStudio ein — eine native macOS-App
(Swift/SwiftUI) mit Rust-Core, die Claude Code als GUI umhüllt.

## Deine Aufgaben (in dieser Reihenfolge):

### 1. Lies die Spezifikation
Lies ClaudeStudio_Konzept.md vollständig. Das ist die Produktdefinition.

### 2. Erstelle feature_list.json
Erstelle eine strukturierte JSON-Datei mit JEDEM einzelnen Feature aus dem
Konzept als end-to-end testbare Einheit. Erwarte ~280 Features.

Format pro Feature:
{
  "id": "F001",
  "category": "vector-db",
  "description": "Ein Chunk kann eingebettet und per semantischer Suche gefunden werden",
  "real_world_test": [
    "Starte Qdrant lokal via init.sh",
    "Bette den Text 'Stripe payment integration' ein",
    "Speichere in Collection 'sessions'",
    "Suche semantisch nach 'Zahlungsanbieter'",
    "Verifiziere: der Chunk wird mit Score > 0.7 zurückgegeben"
  ],
  "evidence_required": "Query-Ergebnis mit Score als JSON-Log",
  "priority": "critical",
  "depends_on": [],
  "passes": false
}

WICHTIG:
- Nutze JSON, NICHT Markdown (Modell überschreibt JSON seltener fälschlich)
- Jeder real_world_test MUSS ein echter Betriebstest sein, kein "ist verbunden"
- Sortiere nach Abhängigkeiten: Foundation-Features zuerst
- Markiere ALLE als "passes": false

### 3. Erstelle init.sh
Ein Script das die KOMPLETTE Dev-Umgebung hochfährt:
- Rust-Core kompilieren (cargo build)
- Qdrant-Container starten (docker run qdrant)
- Swift-App im Dev-Modus bauen (xcodebuild oder swift build)
- Lokales Test-Claude-Code-Setup verifizieren (claude --version)
- Health-Check: alle Komponenten erreichbar?
Das Script muss idempotent sein (mehrfach ausführbar ohne Schaden).

### 4. Erstelle test-harness/
Test-Infrastruktur:
- test-harness/projects/   → Mini-Test-Projekte zum Testen von Features
- test-harness/mcp-mock/   → echte lokale MCP-Test-Server
- test-harness/evidence/   → hier landen Screenshots, Logs, Query-Results
- test-harness/run-test.sh → führt einen einzelnen Feature-Test aus

### 5. Erstelle claude-progress.txt
Leeres Log mit Header:
"# ClaudeStudio Build Progress
 # Format: [SESSION N] [DATUM] [FEATURE-ID] [STATUS] [NOTIZ]"

### 6. Initial git commit
git init, .gitignore (target/, .build/, qdrant-data/), commit "chore: initial scaffold"

## Regeln:
- Es ist INAKZEPTABEL Tests zu entfernen oder zu vereinfachen — das führt zu
  fehlender oder fehlerhafter Funktionalität.
- Schreibe lieber zu viele als zu wenige Features.
- Jedes Feature muss unabhängig testbar sein.
```

---

## 4. LOOP — Coding-Agent-Prompt (jede Session)

```markdown
# ROLLE: Coding-Agent für ClaudeStudio (eine Session = ein Feature)

Du arbeitest an ClaudeStudio. Du machst in dieser Session GENAU EIN Feature
fertig — vollständig, getestet, bewiesen. Nicht mehr.

## SCHRITT 1 — Orientierung (immer zuerst)
1. Führe `pwd` aus. Du darfst nur in diesem Verzeichnis editieren.
2. Lies `git log --oneline -20` — was wurde zuletzt gemacht?
3. Lies `claude-progress.txt` — wo steht das Projekt?
4. Lies `feature_list.json` — welche Features sind noch "failing"?
5. Führe `init.sh` aus und mache einen Smoke-Test: Läuft die App noch?
   Falls die App kaputt ist: REPARIERE ZUERST das, bevor du Neues baust.

## SCHRITT 2 — Feature wählen (Planner-Denkweise, nutze Extended Thinking)
- Wähle das Feature mit höchster Priorität, das noch "failing" ist UND dessen
  depends_on alle "passing" sind.
- Schreibe eine Mini-Spec in dein Denken:
  * Was genau baue ich?
  * Wie teste ich es im ECHTEN Betrieb? (siehe real_world_test im JSON)
  * Welche Evidence muss ich am Ende vorlegen?

## SCHRITT 3 — Implementieren (im Worktree)
- Erstelle einen Worktree für dieses Feature (isolierter Branch).
- Implementiere NUR dieses eine Feature.
- Arbeite im Loop: Kontext sammeln → handeln → selbst prüfen → wiederholen.
- Committe Zwischenstände mit aussagekräftigen Messages.

## SCHRITT 4 — Echter Verifikations-Test (KRITISCH)
Hier entscheidet sich ob das Feature fertig ist. Folge dem real_world_test
aus feature_list.json EXAKT. Beispiele:

→ MCP-Feature: Rufe eine echte Operation über den MCP-Server auf. Nutze das
  Ergebnis in einem Test-Projekt. Logge das Resultat nach test-harness/evidence/.
  NICHT genug: "Server antwortet auf ping". GENUG: "Habe via GitHub-MCP ein
  echtes Issue erstellt, Issue-Nummer #42 zurückbekommen, in der UI angezeigt."

→ UI-Feature: Starte die App. Nutze Computer-Use/Puppeteer um durch die UI zu
  klicken. Spiele ALLE States durch (loading, error, empty, success).
  Mache Screenshots jedes States nach test-harness/evidence/.

→ Agent-A2A-Feature: Lass Agent A eine Aufgabe an Agent B geben. Verifiziere im
  Log dass die A2A-Nachricht floss und B's Ergebnis bei A ankam.

→ Vector-DB-Feature: Bette echten Text ein, suche semantisch, prüfe Score.

WICHTIG: Versuche das Feature zu WIDERLEGEN. Sei ein Skeptiker. Wenn du einen
Weg findest wie es bricht, ist es NICHT fertig.

## SCHRITT 5 — Ergebnis verbuchen
- BESTANDEN: Setze "passes": true in feature_list.json (ändere NUR dieses Feld).
  Merge den Worktree-Branch. Committe.
- NICHT BESTANDEN: Maximal 3 Reparatur-Versuche. Wenn nach 3 Versuchen immer
  noch kaputt: Schreibe das Problem nach claude-progress.txt unter "BLOCKED",
  schreibe die Lektion nach CLAUDE.md (damit künftige Sessions es wissen),
  und lass das Feature auf "failing".

## SCHRITT 6 — Clean State & Handoff (immer am Ende)
- git commit mit beschreibender Message
- Update claude-progress.txt:
  "[SESSION N] [DATUM] [F042] [PASSING] MCP-GitHub getestet, Issue #42 erstellt,
   Evidence in evidence/f042-github-issue.png"
- Wenn du einen Fehler gemacht hast den du wiederholt hast: Schreibe die Lektion
  nach CLAUDE.md oder in einen Skill, damit sie in künftige Runs übergeht.
- Hinterlasse die Umgebung sauber (mergeable, keine offenen Bugs, dokumentiert).

## EISERNE REGELN:
- EIN Feature pro Session. Nicht mehr. Kein One-Shot-Versuch.
- Tests NIEMALS entfernen oder vereinfachen.
- "Fertig" nur mit Evidence. Behauptungen zählen nicht.
- Wenn die App kaputt ist: erst reparieren, dann Neues.
- Committe oft. Git ist dein Sicherheitsnetz zum Zurückrollen.
```

---

## 5. PHASE EVALUATOR — Separater Verifikations-Agent (frischer Context)

> Anthropic-Prinzip: Ein Verifikations-Subagent mit frischem Modell versucht das Ergebnis zu widerlegen — der Agent der die Arbeit macht ist NICHT der der sie benotet.

Der Evaluator läuft als eigener Subagent mit komplett frischem Kontext (kein Bias zugunsten des gerade geschriebenen Codes):

```markdown
# ROLLE: Evaluator — Skeptischer Tester

Ein anderer Agent behauptet, Feature {{feature_id}} sei fertig.
Dein Job: Beweise dass er sich irrt — oder bestätige mit Evidence dass es stimmt.

## Du kennst NICHT den Code den der Generator geschrieben hat.
## Du kennst NUR: die Feature-Beschreibung und den real_world_test.

1. Lies feature_list.json → Feature {{feature_id}} → real_world_test
2. Führe den real_world_test im ECHTEN Betrieb aus (nicht simuliert)
3. Versuche aktiv Edge-Cases zu finden die brechen:
   - Was bei leerem Input?
   - Was bei Netzwerk-Fehler?
   - Was bei gleichzeitiger Ausführung?
   - Was wenn der MCP-Server langsam antwortet?
4. Sammle Evidence: Screenshot, Log, Query-Result → test-harness/evidence/
5. Urteil:
   - PASS: "Verifiziert. Evidence: {{pfad}}. Habe folgende Edge-Cases getestet: ..."
   - FAIL: "Bricht bei: {{szenario}}. Evidence des Fehlers: {{pfad}}. Zurück an Generator."

Sei gründlich. Ein durchgewunkenes kaputtes Feature ist schlimmer als ein
ehrliches "noch nicht fertig".
```

---

## 6. Test-Strategie: Mini-Projekte für jedes Subsystem

> Der User will: Claude baut kleine reale Projekte um Agenten-Zusammenspiel, DB etc. zu testen.

Statt ClaudeStudio "im Trockenen" zu testen, baut der Loop für jedes Subsystem ein echtes Mini-Projekt in `test-harness/projects/`:

### 6.1 Test-Projekt "todo-api" (für MCP, Git, Agent-Tests)
Ein winziges Node/Express Todo-API. Wird genutzt um zu testen:
- **MCP-Test**: Claude verbindet GitHub-MCP, erstellt echtes Issue "Add DELETE endpoint", implementiert es, schließt Issue
- **Worktree-Test**: Zwei Agenten bauen parallel zwei Endpoints in separaten Worktrees, beide werden gemerged
- **A2A-Test**: Planner-Agent zerlegt "Add auth", verteilt an Logic-Agent + Test-Agent
- **Evidence**: git log zeigt beide Merges, Issue #X geschlossen, Tests grün

### 6.2 Test-Projekt "landing-page" (für UI, Browser, Voice-Tests)
Eine kleine statische Landing-Page. Wird genutzt um zu testen:
- **UI-Verifikation**: Claude klickt per Computer-Use durch, macht Screenshots
- **Design-Mode**: Claude ändert eine Farbe, verifiziert visuell im Browser
- **Voice-Test**: "Ändere die Hintergrundfarbe auf blau" → Voice → Ausführung → visuelle Bestätigung
- **Evidence**: Screenshots vor/nach, Audio-Transkript-Log

### 6.3 Test-Projekt "invoice-app" (für Task-Library, Compliance-Tests)
Eine Mini-Rechnungs-App (passt zu Dominics Abrevia-Kontext). Wird genutzt um zu testen:
- **Task-Test**: Der "Kleinunternehmer-Check"-Task läuft echt gegen diese App
- **Evidence**: PDF-Report wird generiert, enthält korrekte ✅/❌ Befunde
- **Vector-Test**: App-Code wird eingebettet, semantische Suche "wo wird die Steuer berechnet?" findet die richtige Datei

### 6.4 Test-Projekt "data-pipeline" (für Vector-DB, Memory, Brain-Graph)
Ein kleines Daten-Skript-Projekt. Wird genutzt um zu testen:
- **Memory-Test**: Über 3 Sessions hinweg — merkt sich Claude Entscheidungen?
- **Brain-Graph-Test**: Wird ein Asset korrekt als Node mit Edge angelegt?
- **Cross-Project-Test**: "Nimm das Logo aus landing-page" — findet Claude es?
- **Evidence**: Graph-Export zeigt Nodes+Edges, Memory-Datei zeigt persistente Fakten

---

## 7. Verifikations-Matrix: Wie jedes ClaudeStudio-Feature getestet wird

| # | Feature | Test-Projekt | Echter Test | Evidence |
|---|---|---|---|---|
| 1 | Projekt anlegen | — | Lege todo-api als Projekt an, Stack auto-detect | Screenshot Project Hub mit korrektem Stack-Icon |
| 2 | CLAUDE.md Editor | todo-api | Editiere, speichere, prüfe Datei auf Disk | Diff der Datei vor/nach |
| 3 | Session starten | todo-api | Starte Agent, gib Aufgabe, sieh Live-Output | Session-Transcript |
| 4 | Worktree-Isolation | todo-api | 2 parallele Worktrees, beide mergen | git log + git branch |
| 5 | Agent Teams A2A | todo-api | Planner→Worker→Review-Flow | A2A-Nachrichten im OS-Log |
| 6 | MCP GitHub | todo-api | Echtes Issue erstellen+schließen | Issue-URL + Screenshot |
| 7 | MCP Playwright | landing-page | Browser steuern, Screenshot | PNG des gerenderten Zustands |
| 8 | Vector-DB Embed+Search | data-pipeline | Embed, semantische Suche, Score prüfen | Query-Result JSON mit Score |
| 9 | Cross-Project Memory | data-pipeline | 3 Sessions, Fakt persistiert? | memory/*.md Inhalt über Zeit |
| 10 | Brain-Graph | data-pipeline | Asset→Node+Edge anlegen | Graph-Export JSON |
| 11 | Voice STT→Action | landing-page | Sprachbefehl ausführen | Audio-Transkript + Ergebnis |
| 12 | Voice TTS | — | Antwort hörbar ausgeben | Audio-Output-Datei |
| 13 | Task Kleinunternehmer | invoice-app | Task läuft, Report korrekt | PDF-Report |
| 14 | Task Security-Scan | todo-api | Echte Vulnerability finden | Finding-Liste mit Zeilennummer |
| 15 | Hooks PostToolUse | todo-api | Auto-Format nach Edit feuert | Datei wurde formatiert |
| 16 | Trust-Modus YOLO | todo-api | Kritisches Gate hält trotzdem | Log zeigt Stopp bei rm -rf |
| 17 | Cost-Tracking | alle | USD stimmt mit API-Bill überein | Vergleich Dashboard vs. Console |
| 18 | Supervisor-Restart | todo-api | Hängenden Agent killen+neu | OS-Log zeigt Restart |
| 19 | Event-Bus git.push | todo-api | Push triggert Security-Scan | Event-Log + Scan-Start |
| 20 | Ratgeber-Tool | alle | Sinnvoller Vorschlag basierend auf State | Vorschlag-Text passt zur Situation |

(Vollständige Matrix mit allen ~280 Features wird vom Initializer in feature_list.json generiert.)

---

## 8. Wie der Loop praktisch gestartet wird

### 8.1 Als Claude Agent SDK Script (empfohlen für unbeaufsichtigt)

```python
import asyncio
from claude_agent_sdk import query, ClaudeAgentOptions, AgentDefinition, HookMatcher

# Loop-Guard State
MAX_SESSIONS = 50
MAX_FIX_ATTEMPTS = 3
DAILY_BUDGET_USD = 20.0

async def run_initializer():
    """Phase 0 — läuft genau einmal."""
    async for msg in query(
        prompt=open("prompts/initializer.md").read(),
        options=ClaudeAgentOptions(
            permission_mode="acceptEdits",
            allowed_tools=["Read", "Write", "Bash", "Glob"],
            model="claude-opus-4-8",
        ),
    ):
        if hasattr(msg, "result"):
            print("Initializer done:", msg.result)

async def run_coding_session(session_num: int):
    """Eine Loop-Iteration — ein Feature."""
    async for msg in query(
        prompt=open("prompts/coding-agent.md").read(),
        options=ClaudeAgentOptions(
            permission_mode="acceptEdits",
            allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep"],
            model="claude-opus-4-8",
            # Subagenten: Planner, Generator, Evaluator
            agents={
                "planner": AgentDefinition(
                    description="Plant ein Feature, schreibt Mini-Spec",
                    prompt=open("prompts/planner.md").read(),
                    tools=["Read", "Grep", "Glob"],
                    model="opus",
                ),
                "generator": AgentDefinition(
                    description="Implementiert ein einzelnes Feature im Worktree",
                    prompt=open("prompts/generator.md").read(),
                    tools=["Read", "Write", "Edit", "Bash"],
                    model="sonnet",
                ),
                "evaluator": AgentDefinition(
                    description="Skeptischer Tester, versucht Feature zu widerlegen",
                    prompt=open("prompts/evaluator.md").read(),
                    tools=["Read", "Bash", "Glob"],  # Kann lesen+testen, NICHT schreiben
                    model="opus",
                ),
            },
            # MCP-Server für echte Tests
            mcp_servers={
                "github": {"command": "npx", "args": ["@modelcontextprotocol/server-github"]},
                "playwright": {"command": "npx", "args": ["@playwright/mcp@latest"]},
            },
            # Hook: jede Datei-Änderung loggen (Evidence)
            hooks={
                "PostToolUse": [HookMatcher(matcher="Edit|Write", hooks=[log_change])],
            },
        ),
    ):
        # Stream verarbeiten, Kosten tracken
        track_cost(msg)
        if hasattr(msg, "result"):
            return msg.result

def all_features_passing() -> bool:
    import json
    features = json.load(open("feature_list.json"))
    return all(f["passes"] for f in features)

def progress_made_recently(window=3) -> bool:
    """Watchdog: wurde in den letzten N Sessions ein Feature passing?"""
    # liest claude-progress.txt, prüft auf neue PASSING-Einträge
    ...

async def main():
    import os
    if not os.path.exists("feature_list.json"):
        await run_initializer()

    session = 0
    while not all_features_passing():
        if session >= MAX_SESSIONS:
            print("Session-Cap erreicht. Report wird erstellt."); break
        if total_cost_today() >= DAILY_BUDGET_USD:
            print("Budget-Cap erreicht. Pause."); break
        if session > 3 and not progress_made_recently():
            print("Watchdog: Kein Fortschritt in 3 Sessions. Eskalation."); break

        result = await run_coding_session(session)
        session += 1

    print("Loop beendet. Features passing:", count_passing())

asyncio.run(main())
```

### 8.2 Innerhalb von ClaudeStudio selbst (Dogfooding)

Sobald ClaudeStudios Agentic-OS-Kern läuft, baut sich ClaudeStudio mit sich selbst weiter:
- Der Build-Loop wird als **Task** in der Task-Library hinterlegt ("Continue ClaudeStudio Build")
- Der Supervisor-Agent steuert den Loop
- Jedes neue Feature erscheint live im OS View
- Evidence landet automatisch im Session-Archiv
- Das ist der ultimative Test: **Wenn ClaudeStudio sich selbst bauen kann, funktioniert es.**

---

## 9. Mistakes → Memory (Selbstverbesserung)

> Boris Cherny (Claude-Code-Schöpfer): Wenn Claude einen wiederholten Fehler macht, lässt er die Lektion in CLAUDE.md oder einen Skill schreiben — so überlebt der Fix in künftige Runs statt in einer Session privat zu bleiben.

Der Loop hat einen eingebauten Lern-Mechanismus:

1. Wenn der Evaluator denselben Fehlertyp 2× in verschiedenen Features sieht → schreibt eine Regel nach `CLAUDE.md`
   - Beispiel: "LEKTION: SwiftUI Canvas braucht explizite `.drawingGroup()` für Metal-Beschleunigung bei >500 Nodes. Immer von Anfang an setzen."
2. Wiederkehrende erfolgreiche Patterns → werden zu Skills
   - Beispiel: Skill `/verify-mcp` der den Standard-MCP-Verifikationstest kapselt
3. `claude-progress.txt` führt ein "LESSONS LEARNED"-Sektion die jede Session liest

So wird der Loop mit jeder Session schlauer statt dieselben Fehler zu wiederholen.

---

## 10. Definition of Done — Gesamtprojekt

ClaudeStudio v1.0 gilt als fertig wenn:

- [ ] Alle ~280 Features in feature_list.json auf "passes": true
- [ ] Jedes "passing" hat Evidence in test-harness/evidence/
- [ ] Alle 4 Test-Projekte (todo-api, landing-page, invoice-app, data-pipeline) laufen
- [ ] ClaudeStudio kann sich selbst weiterbauen (Dogfooding-Test bestanden)
- [ ] Kein Feature gilt als fertig durch bloße "Verbindung" — jedes wurde im Betrieb bewiesen
- [ ] Der Loop lief stabil ohne manuelle Eingriffe über mindestens 24h
- [ ] Mistakes-Log zeigt: keine wiederholten Fehler in den letzten 10 Sessions

---

*ClaudeStudio Build-Loop v1.0 — basiert auf Anthropic Long-Running-Agent-Harness (Nov 2025), Claude Agent SDK, Planner/Generator/Evaluator-Pattern — Juni 2026*
