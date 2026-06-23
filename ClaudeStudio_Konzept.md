# ClaudeStudio — Konzept v1.1
> Native macOS-App (Swift + Rust) als vollständiges GUI für Claude Code  
> Ein Agentic OS für Entwickler — alles per Klick, Drag & Drop, Voice  
> **Design-Sprache:** Google-Analytics-/Material-Dashboard-Stil  
> **Begleitdokument:** `ClaudeStudio_BuildLoop.md` — der autonome Build-Loop der das Tool selbst baut und jedes Feature im echten Betrieb verifiziert

---

## Inhaltsverzeichnis

1. Vision & Produktphilosophie
2. Technische Architektur
3. Datenschicht — Storage & Vector-DB
4. Design-Sprache — Google-Analytics-Dashboard-Look
5. Navigation & UI-Struktur
6. Agentic OS — Betriebssystem-Kern
7. Projekt-Management & File-Explorer
8. Context-System (CLAUDE.md, Memory, Definitionen)
9. Agent Studio
10. Session-Panel & Live View
11. Session-Archiv
12. Voice Assistant
13. Brain View — Knowledge Graph
14. Task-Library — Ein-Klick-Workflows
15. Co-Pilot — KI-Ratgeber & Vorschlagstool
16. Prompt Studio & Skill-Manager
17. MCP-Manager & Plugin-System
18. Hooks-Editor
19. Git & Deployment
20. Cost & Telemetry
21. Sicherheit & Permissions
22. Autonomer Build-Loop (→ separates Dokument)
23. Feature-Katalog (100 weitere Features)
24. Roadmap
25. Open-Source-Strategie

---

## 1. Vision & Produktphilosophie

Claude Code ist das mächtigste KI-Entwicklerwerkzeug der Welt — aber es lebt im Terminal. ClaudeStudio gibt ihm eine native macOS-Oberfläche die **alle** Fähigkeiten zugänglich macht: per Klick, Drag & Drop, Voice — ohne eine einzige CLI-Flag auswendig zu kennen.

**Drei Kernversprechen:**

**1. Kein verstecktes Wissen nötig.**
Jede Claude-Code-Funktion (Worktrees, Hooks, Skills, MCP-Server, AGENTS.md, Plan-Mode, Permissions) ist per UI konfigurierbar. Wer will, sieht darunter immer das rohe Äquivalent in Dateien und Befehlen.

**2. Gedächtnis über alles.**
ClaudeStudio erinnert sich an jede Session, jedes Asset, jede Entscheidung — über alle Projekte und unbegrenzt in die Vergangenheit. Via Vector-DB semantisch durchsuchbar. Via Voice abrufbar.

**3. Ein Betriebssystem für Agenten.**
ClaudeStudio ist kein einfacher Wrapper. Es ist ein vollständiges Agentic OS: Supervisor-Agent, Event-Bus, A2A-Kommunikation, Prioritäts-Queue, Continuous-Monitoring — Agenten arbeiten autonom während du schläfst.

**Plattform:** macOS-native (v1). Rust-Core plattformübergreifend — Linux/Windows-Port via Tauri-Frontend in späteren Versionen möglich.

---

## 2. Technische Architektur

### 2.1 Stack-Entscheidung: Swift + Rust

```
┌─────────────────────────────────────────────────────────────┐
│           SWIFT / SWIFTUI  (native macOS UI)                │
│  NavigationSplitView · SwiftUI Views · @Observable          │
│  AsyncStream · AVFoundation · WKWebView · SwiftTerm         │
│  Swift Charts · Metal Canvas · SwiftData (Settings)         │
└──────────────────────┬──────────────────────────────────────┘
                       │ Unix Domain Socket + MessagePack
                       │ (Swift: Codable structs + async/await)
                       │ (Rust: tokio + serde_msgpack)
┌──────────────────────▼──────────────────────────────────────┐
│           RUST CORE  (Backend-Sidecar-Prozess)              │
│                                                             │
│  ┌─────────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │ Claude Code CLI │  │ Agentic OS   │  │ Vector Layer  │  │
│  │ Process Manager │  │ Supervisor   │  │ Qdrant Client │  │
│  │ tokio::process  │  │ Event-Bus    │  │ nomic-embed   │  │
│  │ Session State   │  │ A2A Router   │  │ ONNX Runtime  │  │
│  └─────────────────┘  └──────────────┘  └───────────────┘  │
│  ┌─────────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │ Git Manager     │  │ SQLite       │  │ MCP Lifecycle │  │
│  │ git2-rs         │  │ rusqlite+FTS5│  │ Manager       │  │
│  │ Worktree-Mgmt   │  │ Session-Arch.│  │ stdio+SSE     │  │
│  └─────────────────┘  └──────────────┘  └───────────────┘  │
│  ┌─────────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │ File Watcher    │  │ OTel         │  │ SSH Client    │  │
│  │ notify-rs       │  │ opentelemetry│  │ libssh2-rs    │  │
│  │                 │  │ -rust        │  │ (Voice-Cmds)  │  │
│  └─────────────────┘  └──────────────┘  └───────────────┘  │
└──────────────────────┬──────────────────────────────────────┘
                       │
        ┌──────────────┼──────────────────┐
        ▼              ▼                  ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────────────┐
│   QDRANT     │ │   SQLITE     │ │  EXTERNAL SERVICES   │
│  (lokal,     │ │  sessions.db │ │  Anthropic Claude API│
│   Docker     │ │  FTS5-Index  │ │  Deepgram / Whisper  │
│   oder       │ │              │ │  ElevenLabs / Kokoro │
│   embedded)  │ │              │ │  OTel Backends       │
│              │ │              │ │  GitHub / Linear     │
│  5 Collections│ │             │ │  Sentry / Datadog    │
└──────────────┘ └──────────────┘ └──────────────────────┘
```

**Warum Swift + Rust (nicht Electron/Tauri):**

| Kriterium | Electron | Tauri | Swift + Rust |
|---|---|---|---|
| macOS Look & Feel | ✗ Chromium | ~ Teilweise | ✓ Vollständig nativ |
| RAM idle | ~300 MB | ~80 MB | ~40 MB |
| Startup | 2–5 s | 0,5 s | < 0,3 s |
| AVFoundation / Voice | Umständlich | Umständlich | ✓ Native API |
| Metal-Animationen | ✗ | ✗ | ✓ |
| App Store | Schwierig | Möglich | ✓ Problemlos |
| Rust-Core-Integration | Via Node.js | Rust ist Core | ✓ Direktes IPC |
| Bundle-Größe | ~150 MB | ~15 MB | ~25 MB + Rust-Binary |

### 2.2 Swift-Implementierungsdetails

- **Concurrency**: Swift Structured Concurrency — `async/await`, `Actor`, `AsyncStream<SessionEvent>` für Session-Live-Updates ohne Polling
- **State**: `@Observable` Macro (Swift 5.9+) — kein Redux-Overhead
- **Monaco-Editor**: Eingebettet als `WKWebView` mit `WKScriptMessageHandler` für Bidirektional-Sync (CLAUDE.md, Definition-Editor, Skill-Editor)
- **Terminal-Output**: `SwiftTerm` — natives Terminal-Rendering in SwiftUI
- **Brain-Graph**: SwiftUI `Canvas` + Force-Directed-Layout-Engine in Swift; bei >500 Nodes: Metal-Shader
- **Voice-Input**: `AVAudioEngine` + Wakeword-Engine (openwakeword, läuft lokal via Python-Subprocess oder Swift-Port)
- **TTS-Fallback lokal**: `AVSpeechSynthesizer` (macOS System-TTS), ElevenLabs via `URLSession`-Streaming
- **Persistenz App-Settings**: `SwiftData`

### 2.3 Rust-Crate-Struktur

```
claudestudio-core/          (Rust Workspace)
├── crates/
│   ├── cs-ipc/             IPC Bridge (MessagePack, Socket-Server)
│   ├── cs-claude/          Claude Code CLI Subprocess Manager
│   ├── cs-agentic-os/      Supervisor, Event-Bus, A2A, Scheduler
│   ├── cs-vector/          Qdrant Client, Embedding (nomic/candle)
│   ├── cs-sessions/        SQLite Session Archive (rusqlite + FTS5)
│   ├── cs-git/             Worktree Manager (git2-rs)
│   ├── cs-mcp/             MCP Server Lifecycle (stdio + SSE)
│   ├── cs-hooks/           Hook Engine (PreToolUse, PostToolUse…)
│   ├── cs-otel/            OpenTelemetry Export (opentelemetry-rust)
│   ├── cs-ssh/             SSH Client für Voice-Server-Cmds (libssh2)
│   └── cs-config/          Settings, CLAUDE.md Parse, Memory Store
└── Cargo.toml
```

---

## 3. Datenschicht — Storage & Vector-DB

### 3.1 Gesamtübersicht aller Speicher

```
~/.claudestudio/
├── settings.json                   App-Settings (Trust-Modus, Budget, Voice-Config…)
├── memory/
│   ├── global.md                   Cross-Project Memory (in jede Session injiziert)
│   └── projects/
│       ├── abrevia.md              Per-Projekt Memory
│       └── bachl-systems.md
├── definitions/                    Definition-Library (.def.md Dateien)
│   ├── loading-systems/
│   │   └── video-frame-loading.def.md
│   └── code-standards/
│       └── error-handling.def.md
├── tasks/                          Task-Library (.task.json Dateien)
│   ├── compliance/
│   │   └── kleinunternehmer-check.task.json
│   └── code-quality/
│       └── security-scan.task.json
├── skills/                         → symlink zu ~/.claude/commands/
├── sessions.db                     SQLite — permanentes Session-Archiv
└── qdrant-data/                    Lokale Qdrant-Daten

~/.claude/                          (Standard Claude Code Verzeichnis)
├── settings.json                   Claude Code Permissions (durch ClaudeStudio verwaltet)
├── CLAUDE.md                       Global CLAUDE.md (User-Level, in jeder Session)
└── commands/                       Skills / Slash Commands
```

### 3.2 Context-Lade-Reihenfolge (jede Session)

Bevor Claude antwortet, baut ClaudeStudio den Kontext in dieser fixen Reihenfolge:

```
Ebene 1: ~/.claude/CLAUDE.md               (Global User — immer, überall)        ~800 Token
Ebene 2: ~/.claudestudio/memory/global.md  (Cross-Project Memory)                ~1.200 Token
Ebene 3: <project>/.claude/CLAUDE.md       (Projekt-spezifisch)                  ~600 Token
Ebene 4: Vector-DB Retrieval               (semantisch relevante Chunks)          ~1.500 Token
Ebene 5: Aktive Definitionen               (per Drag&Drop oder auto-inject)       ~400 Token
Ebene 6: <worktree>/CLAUDE.md             (Worktree-Override, falls vorhanden)   ~200 Token
─────────────────────────────────────────────────────────────────────────────────────────────
Gesamt-Kontext vor erstem Prompt                                                 ~4.700 Token
(konfigurierbar — jede Ebene einzeln an/abschaltbar)
```

**Context-Budget-Anzeige** (immer sichtbar vor Session-Start):
```
Ebene 1  Global CLAUDE.md        800 Token  ✓
Ebene 2  Cross-Project Memory  1.200 Token  ✓
Ebene 3  Projekt CLAUDE.md       600 Token  ✓
Ebene 4  Vector-Retrieval      1.500 Token  ✓
Ebene 5  2 Definitionen          400 Token  ✓
Ebene 6  Kein Worktree-Override    — Token
─────────────────────────────────
Gesamt                         4.500 Token  [Budget: 10.000] ✓
```

### 3.3 Vector-Datenbank (Qdrant)

**Technologie:** Qdrant (self-hosted, lokaler Docker-Container oder Qdrant-embedded)
**Embedding-Modell:** `nomic-embed-text` via Ollama/candle (lokal, kostenlos, 768 Dimensionen)
**Fallback:** OpenAI `text-embedding-3-small` wenn kein lokales Modell konfiguriert

**5 Collections:**

| Collection | Inhalt | Chunks | Payload-Filter |
|---|---|---|---|
| `sessions` | Alle Session-Transcripts (à ~300 Token) | Auto nach Session-Ende | project, agent, timestamp, type |
| `definitions` | Alle Definitionen aus der Library | Bei jeder Änderung | category, name, tags, scope |
| `knowledge` | CLAUDE.md, Memory, ADRs, Code-Kommentare | Bei Datei-Änderungen | project, source, last_updated |
| `assets` | Asset-Beschreibungen + OCR-Text + SVG-Semantik | Bei Projekt-Scan | project, file_path, asset_type |
| `errors` | Fehler + Stack-Traces + Lösungen aus Sessions | Auto nach Session-Ende | project, resolved, fix_applied |

**Token-Einsparung durch Vector-Retrieval:**
Statt 50.000 Token History blind reinladen → Top-5 semantisch relevante Chunks pro Collection → ~1.500 Token. Faktor 13–30× günstiger.

**Retrieval-Pipeline (pro Session-Start):**
1. User-Prompt → Embedding (~10 ms)
2. Qdrant-Query mit Projekt-Filter + semantischer Ähnlichkeit (~20 ms)
3. Top-K Chunks zurück (k=5 je Collection)
4. Re-Ranking nach Score
5. In Kontext-Budget einbauen

**Wissensaufbau (automatisch nach jeder Session):**
Claude-Hook extrahiert neue Entitäten und Erkenntnisse → werden in `knowledge`-Collection eingebettet → Memory-Update wird dem User vorgeschlagen ("Claude hat 2 neue Erkenntnisse — hinzufügen?")

**Manuell lehren:**
Datei, PDF, Code-Snippet in "Teach Claude"-Panel ziehen → sofort eingebettet → ab nächster Session verfügbar.

### 3.4 SQLite Session-Archiv

**Prinzip: Keine Session geht verloren. Nie. Kein Ablaufdatum.**

Pro Session gespeichert: vollständiger Transcript, alle Tool-Calls mit Input/Output, File-Diffs (Git-Patch-Format), Token/Kosten, Permission-Events, Hook-Events, MCP-Calls, Fehler & Retries, Voice-Log-Verknüpfung.

FTS5-Index ermöglicht Volltextsuche über Millionen Einträge in unter 100 ms. Inhalte gzip-komprimiert (~80 % Größenreduktion). Kein automatisches Löschen — manuelles Löschen per Auswahl möglich.

---

## 4. Design-Sprache — Google-Analytics-Dashboard-Look

ClaudeStudio sieht aus und fühlt sich an wie ein modernes Analytics-Dashboard im Stil von Google Analytics 4 / Google Cloud Console — klar, datendicht aber ruhig, mit Material-Design-Prinzipien. Kein Terminal-Look, kein dunkles Hacker-Theme als Default (Dark-Mode optional). Stattdessen: helle, freundliche Oberfläche mit Karten, sanften Schatten, klarer Typo-Hierarchie.

### 4.1 Design-Prinzipien (aus moderner Dashboard-UX)

**Visuelle Hierarchie — der "Squint-Test":** Wenn man die Augen zusammenkneift bis Details verschwimmen, müssen die wichtigsten Elemente (KPI-Karten, Haupt-Chart) dominieren — nicht Sidebar oder dichte Tabellen. Die 3–5 wichtigsten Metriken werden zuerst gelesen, bevor unterstützende Details kommen.

**Progressive Disclosure:** Sekundäre Daten in kollabierbaren Sektionen, Drilldown von Übersicht zu Detail, Tab-Sub-Views — damit das Dashboard nie versucht alles auf einmal zu zeigen. Whitespace verbessert die Verständnisgeschwindigkeit messbar.

**Karten als Grundbaustein:** Jede Informationseinheit lebt in einer Karte mit weichem Schatten und abgerundeten Ecken (Berry/Material-Stil). Karten sind anordbar, kollabierbar, per Drag & Drop umsortierbar.

**Density als Feature, nicht Default:** Drei Dichte-Stufen wählbar (Kompakt / Komfortabel / Geräumig) — ein Power-User und ein Gelegenheitsnutzer wollen unterschiedliche Zeilenhöhen.

### 4.2 Konkrete Design-Tokens

```
FARBEN (Light-Mode Default)
─────────────────────────────────────
Hintergrund (App):      #FFFFFF / #F8F9FA (GA4-Grau)
Karten-Hintergrund:     #FFFFFF
Karten-Schatten:        0 1px 3px rgba(60,64,67,.15)  (Material elevation 1)
Primär-Akzent:          #1A73E8  (Google-Blau)
Erfolg / Passing:       #34A853  (Google-Grün)
Warnung:                #FBBC04  (Google-Gelb)
Fehler / Failing:       #EA4335  (Google-Rot)
Text primär:            #202124
Text sekundär:          #5F6368
Trennlinien:            #DADCE0

TYPOGRAFIE
─────────────────────────────────────
Font:                   SF Pro (macOS-nativ) / "Google Sans"-Anmutung
KPI-Großzahl:           28–32pt, Medium
Karten-Titel:           14pt, Medium, #5F6368
Body:                   13pt, Regular
Tabellen:               13pt, monospace für Zahlen

ABSTÄNDE & FORM
─────────────────────────────────────
Karten-Radius:          12px
Karten-Padding:         20px
Grid-Gap:               16px
Sidebar-Breite:         260px
```

### 4.3 Dashboard-Anatomie (Projekt-Übersicht als Beispiel)

```
┌──────────────────────────────────────────────────────────────────┐
│  KPI-KARTEN-REIHE (oberste Priorität, "Hero")                     │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐            │
│  │ Sessions │ │ Kosten   │ │ Features │ │ Aktive   │            │
│  │ heute    │ │ heute    │ │ passing  │ │ Agenten  │            │
│  │   12     │ │  $3.20   │ │ 184/280  │ │    3     │            │
│  │ ↑ 20%    │ │ ↓ 5%     │ │ ▓▓▓▓░ 66%│ │ 🟢🟢🟡   │            │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘            │
├──────────────────────────────────────────────────────────────────┤
│  HAUPT-CHART (größter Block)        │  SEITEN-PANEL              │
│  ┌─────────────────────────────┐    │  ┌──────────────────────┐ │
│  │ Token-Verbrauch über Zeit   │    │  │ Letzte Aktivität     │ │
│  │     ╱╲      ╱╲               │    │  │ • Agent X fertig     │ │
│  │    ╱  ╲___ ╱  ╲___╱          │    │  │ • PR #42 erstellt    │ │
│  │  Swift Charts Area-Chart    │    │  │ • Task: Tax-Check ✓  │ │
│  └─────────────────────────────┘    │  └──────────────────────┘ │
├──────────────────────────────────────────────────────────────────┤
│  DATEN-TABELLE (Details)                                          │
│  Agent          Status      Tokens    Kosten    Dauer            │
│  Security-Scan  🟢 läuft     12.4k     $0.08     2m 14s          │
│  Doc-Writer     ⚪ fertig     8.1k      $0.03     1m 02s          │
│  ...                                                              │
└──────────────────────────────────────────────────────────────────┘
```

### 4.4 Umsetzung in SwiftUI

- **KPI-Karten**: SwiftUI-Views mit `.shadow()` und `RoundedRectangle`, Delta-Pfeile via SF Symbols
- **Charts**: `Swift Charts` (nativ, Area/Line/Bar) — entspricht der ApexCharts-Anmutung der Web-Dashboards
- **Tabellen**: `Table` (SwiftUI) mit sortierbaren Spalten, drei Dichte-Stufen, monospace für Zahlen
- **Filter-Chips**: Aktive Filter als entfernbare Chips über Tabellen mit Ergebniszähler ("zeige 12 von 240")
- **Grid-Layout**: `LazyVGrid` für responsive Karten-Anordnung
- **Material-Schatten**: Custom ViewModifier der Google-Material-Elevation nachbildet

### 4.5 Konsistenz über alle Views

Jeder Tab (OS View, Brain View, Archive, Task-Library) folgt derselben Anatomie: KPI-Karten oben → Haupt-Visualisierung → Detail-Tabelle. So lernt man ein Layout und alle anderen lesen sich schneller. Die Sidebar bleibt überall gleich (260px, links). Trust-Modus-Indikator und Voice-Mikrofon immer oben rechts.

---

## 5. Navigation & UI-Struktur

### 4.1 App-Shell (macOS NavigationSplitView)

```
┌──────────────────────────────────────────────────────────────────┐
│ ● ● ●  ClaudeStudio                                   [🎙 ][⚡🟢]│
├──────────────┬───────────────────────────────────────────────────┤
│ SIDEBAR      │  HAUPTBEREICH                                     │
│              │                                                   │
│ 💡 Co-Pilot  │  (Vorschlags-Karte immer oben im Dashboard)      │
│ ──────────── │                                                   │
│ 📁 Projekte  │  (wechselt je nach Sidebar-Auswahl)              │
│  > Abrevia ● │                                                   │
│  > Bachl Sys │                                                   │
│  > [+ Neu]   │                                                   │
│              │                                                   │
│ ──────────── │                                                   │
│ OS View      │                                                   │
│ Brain View   │                                                   │
│ Archive      │                                                   │
│ Task-Library │                                                   │
│ Voice-Log    │                                                   │
│ Settings     │                                                   │
│              │                                                   │
│ ──────────── │                                                   │
│ DEFINITIONEN │                                                   │
│ 📁 Loading   │                                                   │
│   🎬 Video   │                                                   │
│   🖼 Image   │                                                   │
│ 📁 Standards │                                                   │
│   🛡 Errors  │                                                   │
│ [+ Definition│                                                   │
│   hinzufügen]│                                                   │
└──────────────┴───────────────────────────────────────────────────┘

Titelleiste rechts:
[🎙 ]  = Voice-Assistent Mikrofon (grün = aktiv, grau = stumm)
[⚡🟢] = Trust-Modus Indikator (⚡ = YOLO, 🟢 = Auto, 🟡 = Standard, 🔴 = Strict)
```

### 4.2 Projekt-Tab-Struktur

Wenn ein Projekt ausgewählt ist, erscheinen Tabs im Hauptbereich:

```
[Agents] [Sessions] [Files] [Git] [Tasks] [Context] [Code] [Settings]
```

- **Agents**: Agent Studio — alle Agenten des Projekts, starten, überwachen
- **Sessions**: Live-Session-Panel der aktiven Session
- **Git**: Git-Status, Worktrees, PRs, Commits
- **Tasks**: Projekt-spezifische Task-Library + globale Tasks
- **Context**: CLAUDE.md Editor, aktive Definitionen, Memory-Einträge
- **Files**: File-Explorer — Projekt-Dateibaum mit Claude-Status, Drag & Drop, geschützten Pfaden (siehe 7.4)
- **Code**: Monaco-Editor (leichtgewichtig, für Quick Edits)
- **Settings**: Projekt-Settings (Permissions, Model, Worktree-Limits)

### 4.3 Globale Ansichten (keine Projekt-Bindung)

- **Co-Pilot**: Proaktives Vorschlags-Panel — was du als Nächstes tun solltest (siehe Sektion 15)
- **OS View**: Agentic OS Mission Control (alle Agenten, Event-Stream, Queue)
- **Brain View**: Knowledge Graph über alle Projekte
- **Archive**: Alle Sessions chronologisch (alle Projekte zusammen oder gefiltert)
- **Task-Library**: Alle Tasks, global oder nach Projekt
- **Voice-Log**: Alle Voice-Interaktionen chronologisch
- **Settings**: Global CLAUDE.md, Cross-Project Memory, Vector-DB, Trust-Modus Default, Budget-Limits, Voice-Config

---

## 6. Agentic OS — Betriebssystem-Kern

### 5.1 Konzept

Das Agentic OS ist die Schicht die ClaudeStudio von einem Claude-GUI-Wrapper zu einem vollständigen autonomen Entwicklungsassistenten macht. Agenten können eigenständig arbeiten, miteinander kommunizieren, auf Events reagieren und Tasks ausführen — auch wenn kein User aktiv ist.

**OS-Analogie:**

| Klassisches OS | Agentic OS (ClaudeStudio) |
|---|---|
| Prozess-Scheduler | Agent-Orchestrator |
| Speicher-Manager | Context-Budget-Manager |
| Interrupt-Handler | Event-Bus |
| Kernel | Supervisor-Agent |
| Shell | Natural Language + Voice |
| Benutzerrechte | Permission-Matrix |
| Prozess-Isolation | Worktree-Isolation |
| IPC | A2A (Agent-to-Agent) |
| Cron-Daemon | Scheduled-Task-Engine |
| syslog | Session-Archiv + OTel |

### 5.2 Schichtenmodell

```
╔═══════════════════════════════════════════════════════════╗
║  LAYER 5 — Benutzerschicht                                ║
║  SwiftUI · Voice · Definition-Sidebar · Task-Klick        ║
╠═══════════════════════════════════════════════════════════╣
║  LAYER 4 — Intent-Layer                                   ║
║  Voice-Parser · Prompt-Router · Task-Matcher              ║
║  Definition-Auto-Inject · Vector-Retrieval                ║
╠═══════════════════════════════════════════════════════════╣
║  LAYER 3 — Orchestration (Supervisor-Agent)               ║
║  Task-Dispatcher · Agent-Scheduler · A2A-Bus              ║
║  Prioritäts-Queue · Conflict-Resolver · Rollback          ║
╠═══════════════════════════════════════════════════════════╣
║  LAYER 2 — Agent Runtime                                  ║
║  Worker-Agenten · Worktree-Isolation · Context-Budget     ║
║  Tool-Execution · Hook-System · MCP-Server-Pools          ║
╠═══════════════════════════════════════════════════════════╣
║  LAYER 1 — Resource Layer                                 ║
║  Qdrant · SQLite · Git · Filesystem · SSH · OTel          ║
╠═══════════════════════════════════════════════════════════╣
║  LAYER 0 — Substrate                                      ║
║  Claude Code CLI · Anthropic API · Rust Core              ║
╚═══════════════════════════════════════════════════════════╝
```

### 5.3 Supervisor-Agent

Dauerhaft laufender Meta-Agent (Haiku-Modell, minimal token-intensiv). Er überwacht alle anderen Agenten, verteilt Tasks, vermittelt A2A-Kommunikation.

**Monitoring:** Hängende Agenten erkennen (kein Output > N Minuten → Restart oder Alert), Token-Budget-Überschreitungen → Agent pausieren, Fehler-Loops (gleicher Fehler > 3× → User-Eskalation).

**Routing:** Neuer Task → welcher Agent bearbeitet ihn? Parallele Tasks → Worktrees zuweisen. Prioritäten managen (Hotfix überholt Feature-Entwicklung).

**A2A-Kommunikation:** Agent A braucht Ergebnis von Agent B → Supervisor vermittelt. Alle A2A-Nachrichten protokolliert und im OS View sichtbar.

**Eskalation:** Wenn ein Agent nicht weiterkommt → Supervisor sammelt Kontext und formuliert eine präzise, kompakte Frage an den User. Nie rohe API-Dumps.

**Lernschleife:** Nach jeder abgeschlossenen Sequenz Erkenntnisse extrahieren → Vector-DB aktualisieren → User-Vorschlag.

### 5.4 Event-Bus

Alle Ereignisse im System fließen durch einen zentralen Event-Bus. Jedes Event kann Agenten triggern, Hooks auslösen, Notifications senden.

**Event-Typen (Auswahl):**

| Event | Beispiel | Mögliche Reaktion |
|---|---|---|
| `git.push` | Push auf main | Security-Scan-Agent |
| `git.pr_opened` | Neuer PR | Review-Agent |
| `file.changed` | package.json | pnpm install |
| `test.failed` | CI rot | Fix-Agent (optional) |
| `error.production` | Sentry Alert | Debug-Agent |
| `budget.warning` | 80% Token | User-Notification, Agent pausieren |
| `schedule.cron` | Mo 9 Uhr | Weekly-Audit-Task |
| `voice.command` | "starte deploy" | Deploy-Pipeline |
| `agent.completed` | Agent fertig | Nächsten in Kette starten |
| `task.oneclick` | User klickt Task | Task-Agent starten |
| `deployment.failed` | Deploy fehlgeschlagen | Rollback-Agent |

**Regeleditor (visuell — kein YAML):**
```
WENN  git.push  UND  branch == "main"
DANN  starte_agent("security-scan")
      UND benachrichtige("slack:#deploy")
      UND WENN ergebnis == "critical"
          DANN blockiere_deploy()
```

### 5.5 Agent-Scheduler & Prioritäts-Queue

- **Prioritätsstufen**: Critical → High → Normal → Background
- **Ressourcen-Limits**: Max N parallele Agenten, max M Worktrees (konfigurierbar)
- **Queue-Visualisierung**: Was läuft, was wartet, Abhängigkeitsgraph als DAG
- **Manuell priorisieren**: Per Drag & Drop in der Queue
- **Abhängigkeiten**: Task C erst nach Tasks A und B

### 5.6 Continuous-Monitor-Agenten (immer aktiv, sehr günstig)

| Monitor | Überwacht | Reagiert bei |
|---|---|---|
| Health-Monitor | Server-Endpoints | HTTP ≠ 200 → Alert |
| Cost-Guard | Tages-/Monatsbudget | >80% → Warnung, >100% → Stopp |
| Security-Watch | Neue Dependencies, Commits | CVE erkannt → Alert |
| Test-Sentinel | CI-Status | Test-Fail → Notification |
| Disk-Guard | Archiv- + Qdrant-Größe | >80% Disk → Komprimierungsvorschlag |
| Stale-Agent-Killer | Laufende Agenten | Kein Output >15 min → Restart |
| Memory-Curator | Vector-DB-Einträge | Veraltete Chunks markieren |

Alle ein-/abschaltbar, Schwellwerte konfigurierbar. Statusindikator: grüner Dot = alles ok, roter Dot = Problem.

### 5.7 OS View — Mission Control

Eigener Vollbild-Tab mit Echtzeit-Überblick über das gesamte System:

- **Agent-Kacheln**: Alle aktiven/wartenden/geplanten Agenten mit Status-Farbe und laufender Aktion
- **Event-Stream**: Live-Strom aller Events (filterbar nach Typ, Projekt)
- **Resource-Gauges**: Token-Budget, API-Latenz, Qdrant-Health, Disk-Usage
- **Queue-Board**: Warteschlange mit Abhängigkeitsgraph
- **A2A-Nachrichten-Feed**: Welche Agenten gerade miteinander kommunizieren
- **Monitor-Agent-Panel**: Status aller Continuous-Monitor-Agenten
- **System-Log**: Alle Events, Agent-Starts, Tool-Calls, Errors — durchsuchbar, exportierbar

---

## 7. Projekt-Management & File-Explorer

### 7.1 Project Hub (Dashboard)

Alle Projekte als Cards: Name, Stack-Icon, letzter Agent-Run, Token-Kosten heute, offene Tasks, Git-Branch, Online-Status (Server erreichbar?).

**Projekt anlegen:**
Wizard in 4 Schritten: (1) Git-Repo wählen oder neu erstellen → (2) Stack auto-detecten (liest package.json, requirements.txt, Cargo.toml) → (3) CLAUDE.md-Template auswählen → (4) Standard-Agent-Set konfigurieren.

**Projekt importieren:** Vorhandenes Verzeichnis mit `.claude/`-Ordner → wird automatisch eingelesen. Alle Skills, Settings, bestehende Sessions erkannt.

### 7.2 Worktree-Manager

Alle aktiven Worktrees als Liste: Branch-Name, Status (läuft/wartet/fertig/Error), zugeordneter Agent, Fortschritt, Erstellungszeitpunkt.

- Create/Delete Worktree per Button
- Worktree-spezifisches CLAUDE.md editierbar (überschreibt Projekt-Root für diesen Worktree)
- Status-Farben: 🟢 läuft, 🟡 wartet auf Input, ⚪ fertig, 🔴 Error
- Merge-Assistent: PR erstellen / Branch mergen direkt aus der UI
- Max-Parallel-Worktrees konfigurierbar (Standard: 4)

### 7.3 Git-Integration

- **Git-Status Panel**: Staged/Unstaged, Branch, letzter Commit
- **Diff-View**: Was hat der Agent geändert — vor Commit reviewen
- **Commit-Assistent**: Claude generiert Conventional-Commit-Message aus dem Diff
- **PR-Erstellung**: Titel und Description von Claude generieren
- **Merge-Konflikte**: Claude schlägt Lösung vor, User bestätigt
- **Blame-Ansicht**: Welcher Agent hat welche Zeile geschrieben
- **Branch-Schutz**: Main/Production-Branch-Pushes immer als "kritische Aktion" behandelt (auch im YOLO-Modus konfigurierbar)

### 7.4 File-Explorer (Projekt-Dateibaum)

Ein vollwertiger, in die App integrierter Datei-Explorer pro Projekt — wie der Finder oder VS Codes Explorer, aber Claude-bewusst.

**Baum-Ansicht (linke Spalte im Projekt-Tab):**
```
📂 abrevia/
├── 📂 src/
│   ├── 📂 api/
│   │   ├── 📄 auth.ts          ● (von Agent bearbeitet)
│   │   └── 📄 invoices.ts
│   ├── 📂 components/
│   └── 📄 index.ts
├── 📂 public/
│   └── 🖼 logo.svg             ⭐ (im Brain-Graph)
├── 📄 CLAUDE.md                📌 (Context-Datei)
├── 📄 package.json
└── 📄 .env                     🔒 (geschützt, nie an Claude)
```

**Features:**
- **Standard-Dateioperationen**: Anlegen, Umbenennen, Verschieben, Löschen, Duplizieren per Rechtsklick oder Drag & Drop
- **Claude-Status-Indikatoren** pro Datei: ● gerade von Agent bearbeitet · ⭐ im Brain-Graph als Asset · 📌 Context-Datei (CLAUDE.md) · 🔒 geschützt (z.B. `.env`, nie an Claude gesendet)
- **Schnell-Aktionen pro Datei** (Rechtsklick): "An Session anhängen" · "Von Claude erklären lassen" · "Als Asset im Brain-Graph markieren" · "In Monaco öffnen" · "Im Finder zeigen"
- **Drag & Drop in Session**: Datei aus dem Explorer in den Chat ziehen → Pfad + Inhalt wird als Kontext angehängt
- **Drag & Drop in Brain-Graph**: Datei in den Graph ziehen → wird als Asset-Node angelegt
- **Vorschau**: Bilder, SVGs, Markdown mit Inline-Preview beim Hovern
- **Geschützte Pfade**: `.env`, `secrets/`, Credentials-Dateien sind visuell als 🔒 markiert und werden niemals automatisch an Claude gesendet (Schutz vor versehentlichem Leak)
- **Git-Status-Farben**: Geänderte Dateien orange, neue grün, gelöschte durchgestrichen (wie in IDEs)
- **Diff-Indikator**: Klick auf eine geänderte Datei zeigt sofort den Diff zur letzten Committed-Version
- **Suche**: Dateiname-Suche (fuzzy) + Volltextsuche im Projekt (ripgrep-basiert via Rust)
- **Watcher-Sync**: Wenn Claude oder ein externer Editor eine Datei ändert, aktualisiert sich der Baum live (notify-rs im Rust-Core)

**Cross-Project-Modus:** Ein Toggle erweitert den Explorer auf ALLE Projekte gleichzeitig — nützlich um z.B. ein Asset von Projekt A nach B zu ziehen. Verbindet sich direkt mit dem Asset-Index und Brain-Graph.

---

## 8. Context-System

### 8.1 Global CLAUDE.md (User-Level)

Gespeichert unter `~/.claude/CLAUDE.md` — wird in **jeder** Session als erste Ebene geladen.

In ClaudeStudio visuell als strukturierter Editor mit vorgefertigten Sektionen:

**Sektion: Über mich & Unternehmen** — Name, Rolle, Unternehmensname, Primärsprache

**Sektion: Projekte (Übersicht)** — Name, Pfad, Repo-URL, Stack, kurze Beschreibung je Projekt

**Sektion: GitHub & Repositories** — Username, Token-Speicherort (Referenz, nie der Key selbst), Default-Branch, Commit-Style

**Sektion: Assets & Branding** — Logo-Pfade, Primärfarben, Fonts pro Projekt

**Sektion: Coding Preferences** — Package Manager, Frameworks, Deploy-Targets, Kommentar-Sprache, Naming-Konventionen, Test-Framework

**Sektion: Tool-Referenzen** — API-Key-Pfade, MCP-Server-Ports, lokale Tool-Pfade

**Sektion: Regeln (immer einhalten)** — Persönliche Never-Do/Always-Do-Liste

Editor-Features: Monaco-Editor, Token-Counter live, Warnung bei >4.000 Token, Diff zum letzten Stand, "In Test-Session laden"-Button, automatisches Backup vor jeder Änderung.

### 8.2 Cross-Project Memory

Gespeichert unter `~/.claudestudio/memory/global.md` — wird als Ebene 2 in jede Session geladen.

Enthält: Projekt-Kurzübersichten, Asset-Index (welche Assets liegen wo), Credentials-Referenzen (nie Keys selbst), aktive Clients, Coding-Entscheidungen die projekt-übergreifend gelten.

**Memory Manager UI:**
- Kategorisierte Ansicht aller Einträge (Projekte, Assets, Preferences, Credentials-Refs…)
- Inline-Editierung
- Nach jeder Session: ClaudeStudio zeigt "Claude hat 2 neue Erkenntnisse — hinzufügen?" mit Preview
- Relevanz-Score: Einträge die >90 Tage nie genutzt wurden → als "veraltet" markieren
- Token-Usage-Bar: Wie viel kosten alle Memory-Einträge beim nächsten Session-Start

**Per-Projekt Memory:** `~/.claudestudio/memory/projects/<name>.md` — letzte Entscheidungen, bekannte Bugs, offene TODOs, Besonderheiten dieses Projekts. Auto-Update nach jeder Session.

### 8.3 CLAUDE.md Visual Editor (Projekt-Level)

In jedem Projekt: `<project>/.claude/CLAUDE.md` — Ebene 3 des Kontext-Systems.

- Sektions-Editor mit Drag & Drop Reihenfolge
- Live-Preview (gerendetes Markdown)
- Templates für gängige Stacks (Next.js, FastAPI, React Native…)
- Diff-Ansicht: Was hat sich seit dem letzten Claude-Run verändert
- Memory-Watcher: Warnung wenn CLAUDE.md zu groß wird
- AGENTS.md-Tab: Visueller Editor für Agent-Team-Definitionen (Frontmatter-Felder per Formular, Verbindungslinien zwischen Orchestrator und Workers)

### 8.4 Definition-Library

Wiederverwendbare, thematisch fokussierte Prompt-Blöcke — präziser als Skills, kleiner als CLAUDE.md. Gespeichert als `.def.md`-Dateien in `~/.claudestudio/definitions/`.

**Was eine Definition ist:**
```yaml
---
name: Video Frame Loading System
category: Loading Systems
tags: [video, performance, lazy-loading, media]
scope: global        # oder: project:abrevia
tokens: ~180
version: 1.2
---

Videos in diesem Projekt werden nach folgendem System geladen:
1. Initial: Poster-Frame (<20 KB) sofort anzeigen
2. Bei Viewport-Entry (IntersectionObserver, threshold 0.1):
   Preload der ersten 3 Sekunden
3. Buffering-Indikator: Custom Spinner, nie Browser-Native
4. Fehlerfall: Fallback-Thumbnail + Retry-Button nach 3 s
5. Format-Prio: WebM/VP9 → MP4/H.264 → HLS
6. Alle Video-Tags: playsinline, muted, preload="none"
```

**Sidebar-Hierarchie (immer sichtbar, kollabierbar):**
```
📁 Loading Systems
   🎬 Video Frame Loading
   🖼  Image Lazy Loading
📁 Code Standards
   🛡  Error Handling
   📝 TypeScript Strict
📁 UI / Design
   🎨 Color Token System
📁 Performance
   🚀 Bundle Optimization
[+ Neue Kategorie]  [+ Neue Definition]
```

**Inject-Mechanismen:**
1. **Drag & Drop** aus Sidebar in Chat-Eingabefeld → erscheint als blauer Context-Block
2. **Agent-Zuordnung**: Definition dauerhaft einem Agent zuweisen → bei jeder Session dieses Agents automatisch geladen
3. **Auto-Suggest**: ClaudeStudio erkennt Keywords im Prompt → schlägt passende Definition vor (einmalige Bestätigung)
4. **Voice**: "Füge die Video-Loading-Definition hinzu" → Vector-Suche findet die richtige

**Active-Context-Bar** (über dem Chat-Input, immer sichtbar):
```
[~/.claude/CLAUDE.md 800T ×] [memory 1.2kT ×] [🎬 Video Loading 180T ×]
```
X-Button zum Entfernen jedes Blocks. Token-Zähler pro Block.

**Definition erstellen:** Monaco-Editor + Formular-Felder (Name, Kategorie, Tags, Scope, Abhängigkeiten) + Token-Counter + Test-Button. Export als `.def.md`, Import per Drag & Drop, Git-Sync für Team-Sharing.

---

## 9. Agent Studio

### 8.1 Agent-Designer

Pro Agent konfigurierbar:

- **Identität**: Name, Icon, Farbe, Beschreibung, Modell (Haiku/Sonnet/Opus)
- **Allowed Tools**: Checkbox-Matrix (Bash, Read, Write, Edit, WebSearch, je MCP-Tool)
- **System Prompt / Rolle**: Monaco-Editor mit `{{variable}}`-Support
- **Isolation**: Toggle für Worktree-Isolation (eigene Git-Branch, automatisch)
- **Context Fork**: Läuft in eigenem Kontext-Fenster (kein Context-Pollution)
- **Trigger**: Manuell / Event-getriggert / Scheduled / A2A
- **Effort-Limits**: Token-Budget, USD-Budget, Timeout, Retry-Policy, Priorität
- **Zugeordnete Definitionen**: Welche Definitionen automatisch injiziert werden
- **Post-Run-Hook**: Was nach Abschluss passiert (Commit, Notification, nächster Agent…)

### 8.2 Agent Teams

Strukturiertes Team aus einem Orchestrator-Agent und beliebig vielen Worker-Agenten (Claude Opus 4.6+):

- **Team-Builder**: Orchestrator + Workers visuell verbinden
- **Aufgabenverteilung**: Orchestrator bricht Task auf → weist Subtasks zu (automatisch oder manuell)
- **Parallelisierung**: Workers laufen parallel, jeder in eigenem Worktree
- **Review-Gate**: Orchestrator reviewed alle Worker-Ergebnisse vor Merge
- **A2A-Flow sichtbar**: Im OS View welche Agenten miteinander kommunizieren

**Beispiel-Flow "Dark-Mode implementieren":**
```
Supervisor-Agent
  └→ Orchestrator-Agent (Opus) erstellt Plan
       ├→ Design-Agent (Sonnet, Worktree A): CSS-Tokens
       ├→ Logic-Agent (Sonnet, Worktree B): Toggle-State  
       ├→ Test-Agent (Haiku, Worktree C): Tests
       └→ Review-Agent (Opus): Review + Merge
```

### 8.3 Model-Router

Automatisches Routing basierend auf Task-Typ — Kosten minimieren ohne Qualität zu verlieren:

| Task-Typ | Modell | Begründung |
|---|---|---|
| Dokumentation, Simple Edits | Haiku | Schnell, günstig |
| Feature-Implementierung, Reviews | Sonnet | Balance |
| Architektur, komplexe Planung | Opus | Beste Reasoning-Qualität |
| Monitor-Agenten | Haiku | Dauerhaft aktiv, minimal |

Konfigurierbar: Schwellwerte für automatisches Routing, Override per Agent möglich.

**Fallback-Chain:** Opus nicht erreichbar → Sonnet → Haiku. Niemals eine Session durch API-Fehler verlieren.

---

## 10. Session-Panel & Live View

Das Session-Panel ist die Echtzeit-Ansicht einer laufenden Agent-Session.

**Layout:**
```
┌─────────────────────────────────────────────────────────┐
│ Agent: Security-Scan  |  Projekt: Abrevia  |  $0.08 ↑  │
│ [⏸ Pause] [💬 Einwerfen] [⏹ Stopp]    [/cost /status] │
├─────────────────────────────────────────────────────────┤
│ Active Context: [CLAUDE.md 800T] [Video-Def 180T] [×]  │
├─────────────────────────────────────────────────────────┤
│                                                         │
│ 🧑 User: Führe einen vollständigen Security-Scan durch  │
│                                                         │
│ 🤖 Ich analysiere zunächst die Projektstruktur…        │
│                                                         │
│ ▶ Tool: Read [src/api/auth.ts]              [0.2s] ▼   │
│   └ Output: 142 Zeilen gelesen                          │
│                                                         │
│ ▶ Tool: Bash [grep -r "eval(" src/]         [0.1s] ▼   │
│   └ Output: 0 Treffer                                   │
│                                                         │
│ ⚠ FINDING: SQL-Injection-Risiko in /api/users          │
│   Line 47: query = `SELECT * WHERE id=${req.params.id}` │
│   → Severity: HIGH                                      │
│                                                         │
│ [Plan-Mode: Claude schlägt vor…]  [✓ Bestätigen]       │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

**Features:**
- Tool-Calls aufklappbar/zugeklappt mit Dauer
- Plan-Mode Visualizer: Claude zeigt Plan → User bestätigt oder modifiziert → dann Ausführung
- Interrupt jederzeit: "Pause" hält an, User kann Nachricht einwerfen
- `/simplify`, `/resume`, `/cost`, `/status`, `/mcp`, `/compact` als UI-Buttons
- Approval-Flow: Per Trust-Modus-Einstellung — auto, ask, oder immer bestätigen
- Kosten-Akkumulation live (USD-Counter läuft mit)
- Split-View: Links Session, rechts die gerade bearbeitete Datei (Monaco, read-only während Agent schreibt)
- Extended-Thinking-Button: Für komplexe Fragen erweiterte Chain-of-Thought aktivieren (Denkprozess als kollabierbare Sektion)

---

## 11. Session-Archiv

**Kernprinzip: Keine Session geht verloren. Nie. Kein Ablaufdatum. Kein automatisches Löschen.**

### 10.1 Was pro Session gespeichert wird

Vollständiger Transcript (jede Nachricht, jeder Tool-Call, jeder Output) · File-Diffs im Git-Patch-Format · Token-Aufschlüsselung (Input/Output/Cache) · USD-Kosten · Permission-Events · Hook-Events · MCP-Calls · Fehler & Retries · Context-Compaction-Events · Verknüpfte Voice-Interaktionen · Session-Notizen des Users.

### 10.2 Archive-UI

**Listenansicht:**
- Chronologisch, neueste oben
- Filter: Projekt, Agent, Zeitraum, Modell, Kostenrange, enthaltene Tools
- Volltextsuche via FTS5 (auch über Millionen Einträge <100 ms)
- Zeile pro Session: Datum, Projekt, erste Prompt-Zeile, Dauer, Kosten, Status-Icon

**Session-Replay-Modus:**
- Step-Through mit ← → durch einzelne Events
- File-Diffs eingebettet (grün/rot wie GitHub)
- Kosten-Akkumulation live beim Scrollen durch die Session
- "Diesen Prompt wiederholen": Neue Session mit exakt demselben initialen Prompt
- "Ab hier weitermachen": Neue Session mit Transcript bis zu diesem Punkt als Kontext

**Statistiken:**
- Gesamtzahl Sessions, Gesamttoken, Gesamtkosten (seit Beginn)
- Längste Session, teuerste Session, aktivste Projekte
- Token-Verbrauch über Zeit als Chart

### 10.3 Privacy-Modus

Sessions als "Private" markierbar → lokal AES-256 verschlüsselt → erscheinen in normaler Liste nicht ohne explizites "Private anzeigen". Nützlich für Sessions mit Credential-Diskussionen oder Kundendaten.

---

## 12. Voice Assistant

### 11.1 Pipeline

```
Mikrofon → Wakeword ("Hey Claude") → STT → Intent-Parser
→ Vector-DB-Retrieval → Claude API → TTS → Lautsprecher
```

**Latenz-Ziel:** Wake-Word bis erste Silbe der Antwort <1,5 Sekunden.

- **STT Online**: Deepgram Nova-3 Realtime WebSocket (<300 ms)
- **STT Offline**: Whisper.cpp lokal (Apple-Silicon-optimiert)
- **TTS Online**: ElevenLabs Eleven v3 (Streaming, erste Silbe in ~300 ms)
- **TTS Offline**: Kokoro lokal (Metal-optimiert) oder `AVSpeechSynthesizer` als Fallback
- **Wakeword**: openwakeword lokal (kein Cloud-Ping)
- **PTT-Modus**: Tastenkombination (z.B. `Fn+Space`) als Alternative zu dauerhaftem Listening

### 11.2 Was der Voice Assistant weiß

Zugriff auf alle 5 Qdrant-Collections + Brain-Graph + Global Memory + Projekt-Status + laufende Agenten + Kosten-Status. Er kennt alle Projekte, alle vergangenen Sessions (semantisch), alle Assets, alle Definitionen — ohne dass der User Kontext erklären muss.

### 11.3 Antwort-Stil

Kurz, direkt, informell — wie ein kompetenter Kollege:

| User sagt | Antwort |
|---|---|
| "Ist die Sitemap auf bachl-systems.de erreichbar?" | "Nein, gibt 404. Schau ich kurz auf dem Server nach." |
| "Was kostet mich der heutige Tag?" | "Bisher $3.20, hauptsächlich der Abrevia-Agent heute Morgen." |
| "Wo liegt das Bachl-Logo?" | "~/projects/bachl-systems-web/public/logo.svg" |
| "Log dich auf dem Server ein und schau den Nginx-Fehler an" | "Bin drin. Nginx-Config hat eine fehlende SSL-Direktive in Zeile 23." |

Maximale Antwortlänge: 2 Sätze für einfache Queries. Komplexe Aktionen: 1 Satz Ankündigung, dann handeln.

### 11.4 Aktionen per Voice (Auswahl)

Navigation & Info · Agent starten/stoppen · Server-Zugriff via SSH · Definitions injizieren ("Füge die Video-Loading-Definition zur aktuellen Session hinzu") · Task starten ("Starte den Steuer-Check auf Abrevia") · Git-Aktionen · Budget-Status · Daily Briefing

### 11.5 Steuerung & UI

- **Status-Icon** in Menüleiste: grau = idle, grün = hört zu, orange = denkt, blau = spricht
- **Barge-In**: Neues Sprachkommando unterbricht laufende TTS-Ausgabe sofort
- **Mute-on-Screen-Share**: Automatisch stumm bei Bildschirmteilung (konfigurierbar)
- **Voice-Log**: Alle Voice-Interaktionen als Text im Voice-Log-Tab gespeichert, durchsuchbar, Teil des permanenten Archivs

---

## 13. Brain View — Knowledge Graph

### 12.1 Was der Graph enthält

Ein semantisches Netz aus allem was ClaudeStudio über Projekte, Assets, Entscheidungen und Zusammenhänge weiß. Kein Code-Index — ein Wissens-Graph.

**Node-Typen:**
- 🔵 **Projekte** — jedes ClaudeStudio-Projekt
- 🟠 **Assets** — Logos, Icons, Fonts, Konfigurationsdateien (mit Thumbnail)
- 🟢 **Konfigurationen** — Key-Referenzen, Repo-URLs, Deploy-Targets
- 🟣 **Personen** — Kunden, Team-Mitglieder
- 🟡 **Entscheidungen** — ADRs, architektonische Entscheidungen aus Sessions
- 🔴 **Fehler-Patterns** — bekannte Probleme und ihre Lösungen
- ⚪ **Konzepte** — fachliche Entitäten (z.B. "Rechnungsstellung", "Auth-System")

**Edge-Typen:**
`USES_ASSET` · `SHARES_MODULE` · `DEPENDS_ON` · `BELONGS_TO` · `SIMILAR_TO` · `DERIVED_FROM` · `CONFIGURED_WITH` · `DOCUMENTED_IN` · `RESOLVED_BY`

### 12.2 Graph-UI

Force-Directed Layout (Nodes ziehen sich nach Relevanz zusammen) · Zoom & Pan · Node-Größe proportional zu Verbindungszahl (God-Nodes sofort sichtbar) · Farb-Coding nach Typ · Cluster-Erkennung · Hover-Preview · Click → Detail-Panel · Suche (filtert live) · Zeitachse (Slider zeigt Graphzustand zu beliebigem Datum).

### 12.3 Wofür der Graph nützlich ist

"Nimm das Logo aus Projekt Bachl Systems" → Claude findet Node `Asset "logo.svg" → BELONGS_TO → Bachl Systems` → gibt Pfad zurück, kopiert Datei in aktuelle Session.

"Welche Projekte nutzen denselben Auth-Stack?" → Graph-Traversal über `SIMILAR_TO`/`DEPENDS_ON`-Edges.

"Wo hatten wir schon mal diesen Fehler?" → `errors`-Collection + `RESOLVED_BY`-Edge zeigt welche Session die Lösung enthält.

### 12.4 Aufbau (automatisch + manuell)

**Automatisch:** Beim Projekt-Scan (Assets, Dependencies, CLAUDE.md parsen) · Nach jeder Session (neue Entitäten via Hook extrahiert) · Bei CLAUDE.md-Änderungen.

**Manuell:** Datei aus Projekt-Explorer in Graph ziehen · "Neuen Node anlegen"-Formular · Natural Language: "Merke dir dass das Logo aus /assets/logo.svg zu Bachl Systems gehört".

---

## 14. Task-Library — Ein-Klick-Workflows

### 13.1 Konzept

Fertige, sofort ausführbare Agent-Workflows. Von außen: ein Klick. Von innen: vollständig definierter Multi-Step-Agent mit Input-Parametern, Tool-Permissions, Output-Definition.

### 13.2 Task-Library UI

Kachel-Grid oder Liste, filterbar nach Kategorie und Tags.

**Vorinstallierte Kategorien:**
```
📁 Recht & Compliance (DE)
   🧾 Kleinunternehmer-Check (§19 UStG)
   ⚖️  DSGVO / Datenschutz Audit
   📜 AGB & Impressum Checker
   🇦🇹 Reverse Charge EU-B2B Check
   📋 EU AI Act Readiness

📁 Code-Qualität
   🔒 Security Scan (OWASP Top 10)
   🧪 Test Coverage Report
   📊 Complexity Heatmap
   🔍 Dead Code Detector
   📦 Dependency CVE-Audit

📁 Dokumentation
   📖 README generieren
   🗺️  API-Doku (OpenAPI)
   📋 Changelog für dieses Release
   🏗️  Architektur-Diagramm Update

📁 Deployment & Release
   🚀 Pre-Deploy Checklist
   🔖 Release Notes generieren
   🔄 Dependency Update (alle Pakete)

📁 Business
   ⏱️  Zeiterfassungs-Export (Kundenrechnung)
   📈 Monatsbericht (Entwicklungsaktivität)
   🎯 Sprint-Velocity-Report
```

### 13.3 Task ausführen

Klick auf Task-Karte → Modal mit Beschreibung, Scope, Modell-Schätzung, Kosten-Schätzung, Input-Felder → Klick "Ausführen" → Task in Agent-Queue, Session-Panel öffnet Live-Output, Output nach Abschluss direkt zugänglich.

### 13.4 Vorgefertigte Tasks — Detail

**🧾 Kleinunternehmer-Check (§19 UStG):**
Prüft alle Rechnungsvorlagen auf §19-Hinweistext · Pflichtfelder (Steuernummer, Leistungsdatum, Nummernkreis-Lücken) · Reverse-Charge-Hinweise bei EU-B2B · ATU-Nummern-Validierung für Österreich-Kunden · Peppol-Kompatibilität.
Output: PDF-Report mit ✅/❌/⚠️ pro Punkt + automatische Fix-Option.

**⚖️ DSGVO Audit:**
Datenspeicherungslogik · Lösch-Konzept · Drittanbieter-Datenweitergabe · Cookie-Consent-Implementierung · Datenschutzerklärung Vollständigkeit · AVV-Liste generieren · Art.-13/14-Informationspflichten.

**🔒 Security Scan (OWASP):**
SQL-Injection · XSS · CSRF · Broken Auth · Sensitive Data Exposure · Security Misconfiguration · CVEs in Dependencies.
Output: Finding-Liste mit Severity-Labels, CVSS-Score-Schätzung, Inline-Fix-Vorschläge.

**🚀 Pre-Deploy Checklist:**
Tests grün · Keine bekannten CVEs · Environment Variables vollständig · Kein Debug-Code committed · Migrations reversibel · Health-Check-Endpoint erreichbar · Rollback-Plan dokumentiert.
Output: Ampel-Status (Rot = blockt Deploy, Gelb = Warning, Grün = ok).

### 13.5 Eigene Tasks erstellen

6-Tab-Builder:
1. **Grunddaten**: Name, Icon, Kategorie, Tags, Scope (Global/Projekt/Team)
2. **Agent-Config**: Modell, Tool-Permissions, Budget-Limit, Isolation-Modus
3. **Input-Parameter**: Formularfelder (Text, Zahl, Dropdown, Datei, Boolean), referenzierbar als `{{param}}`
4. **Workflow**: Monaco-Editor, Multi-Step-Modus, Parameter-Chips, Definitions-Drag-Drop, A2A-Unter-Tasks
5. **Output**: Typ (Report/Datei/PR/Slack/Email/Board-Task), Post-Task-Hook
6. **Test**: Task direkt im Builder ausführen, Token/Dauer-Anzeige

**Import/Export:** `.task.json` · Task-Packs (mehrere Tasks gebündelt) · Community-Marketplace

### 13.6 Task-Scheduling

Manuell · Scheduled (Cron) · Event-getriggert (bei git.push, Test-Fail, Sentry-Alert) · Threshold (wenn Coverage <80%) · Voice ("Starte den Steuer-Check auf Abrevia").

---

## 15. Co-Pilot — KI-Ratgeber & Vorschlagstool

Der Co-Pilot ist ein proaktiver Berater der dauerhaft den gesamten Workspace beobachtet und dir vorschlägt was du als Nächstes tun solltest. Kein passives Chat-Fenster — ein aktiver Assistent der den Zustand aller Projekte, Agenten, Tasks und deiner Historie kennt und daraus konkrete Handlungsempfehlungen ableitet.

### 15.1 Wo der Co-Pilot lebt

Eine immer erreichbare Karte oben im Dashboard (im Google-Analytics-Stil, wie GA4's "Insights"-Karte) plus ein ausklappbares Seiten-Panel. Auf Wunsch auch als Voice-Ansage beim App-Start ("Guten Morgen Dominic — drei Dinge stehen an…").

```
┌─────────────────────────────────────────────────────────┐
│  💡 Co-Pilot Vorschläge                          [mehr ▾]│
├─────────────────────────────────────────────────────────┤
│  🔴 Wichtig                                              │
│  Der Security-Scan auf Abrevia fand gestern 2 neue       │
│  HIGH-Findings. Sie sind noch offen.                     │
│  [Jetzt beheben]  [Später]  [Ignorieren]                │
│                                                          │
│  🟡 Empfohlen                                            │
│  Du hast seit 8 Tagen kein Backup des Brain-Graphs       │
│  gemacht. 47 neue Nodes seitdem.                         │
│  [Backup jetzt]                                          │
│                                                          │
│  🟢 Optimierung                                          │
│  Der Doc-Writer-Agent nutzt Opus, käme aber mit Haiku    │
│  aus — spart ~$1.20/Tag.                                 │
│  [Auf Haiku umstellen]  [Warum?]                        │
└─────────────────────────────────────────────────────────┘
```

### 15.2 Worauf der Co-Pilot seine Vorschläge stützt

Der Co-Pilot wertet kontinuierlich aus (alles lokal, via Vector-DB + Live-State):

- **Offene Probleme**: Unbehobene Findings aus Tasks, fehlgeschlagene Tests, rote CI, Production-Errors
- **Vernachlässigtes**: Lange nicht aktualisierte Projekte, fehlende Backups, veraltete Dependencies
- **Kosten-Ineffizienz**: Agenten die teurere Modelle nutzen als nötig, ungenutzte auto-injizierte Definitionen, niedrige Cache-Hit-Rate
- **Muster aus der Historie**: "Du machst freitags immer den Wochenbericht — soll ich ihn vorbereiten?"
- **Hängende Arbeit**: Worktrees die seit Tagen offen sind, halbfertige Features, nicht gemergte Branches
- **Abhängigkeiten**: "Feature X wartet auf Y, und Y ist jetzt fertig — du kannst X angehen"
- **Sicherheit**: Neue CVEs in genutzten Paketen, Secrets versehentlich im Git-Staging, abgelaufene Tokens
- **Lern-Momente**: Wiederholte Fehler die Claude gemacht hat → "Soll ich eine Regel in CLAUDE.md schreiben?"

### 15.3 Vorschlags-Kategorien & Priorität

| Stufe | Bedeutung | Beispiel |
|---|---|---|
| 🔴 Wichtig | Sollte zeitnah erledigt werden | Offene Security-Findings, Production-Error |
| 🟡 Empfohlen | Sinnvoll, nicht dringend | Backup fällig, Dependency-Update verfügbar |
| 🟢 Optimierung | Spart Zeit/Geld | Modell-Downgrade, Definition aufräumen |
| 🔵 Idee | Inspiration | "Du könntest Task X automatisieren" |

Jeder Vorschlag hat Aktions-Buttons (sofort ausführen) und ein "Warum?" das die Begründung mit den zugrundeliegenden Daten zeigt — transparent, nie eine Blackbox.

### 15.4 "Was soll ich jetzt tun?" — Der Fokus-Vorschlag

Auf Knopfdruck (oder Voice: "Was soll ich als Nächstes tun?") gibt der Co-Pilot **eine** klare Empfehlung statt einer Liste:

> "Beheb die 2 Security-Findings auf Abrevia — das ist gerade das Wichtigste. Ich hab den Fix-Agent schon vorbereitet, du musst nur bestätigen. Danach wäre der Wochenbericht dran, den kann ich parallel im Hintergrund vorbereiten."

Das nutzt dieselbe Priorisierungslogik wie der Supervisor-Agent (Sektion 6.3), aber für **dich** statt für die Agenten.

### 15.5 Proaktivität-Einstellungen

- **Aufdringlichkeit**: Still (nur auf Anfrage) / Dezent (Karte aktualisiert sich) / Aktiv (Notifications) / Voice (spricht Vorschläge)
- **Themen filtern**: Welche Vorschlags-Kategorien aktiv sind
- **Ruhezeiten**: Keine proaktiven Vorschläge außerhalb der Arbeitszeit / im Weekend-Modus
- **Lernfähig**: Wenn du Vorschläge eines Typs oft ignorierst, lernt der Co-Pilot und zeigt sie seltener

### 15.6 Abgrenzung zum Voice Assistant und Supervisor

- **Voice Assistant** (Sektion 12): Führt aus was du sagst — reaktiv.
- **Supervisor-Agent** (Sektion 6.3): Koordiniert die Agenten — intern, technisch.
- **Co-Pilot** (diese Sektion): Berät *dich* proaktiv — was du tun solltest, nicht was die Agenten tun.

Die drei teilen sich dieselbe Wissensbasis (Vector-DB, Brain-Graph, Memory), haben aber unterschiedliche Adressaten.

---

## 16. Prompt Studio & Skill-Manager

### 14.1 Slash Commands / Skills

Alle Skills in `~/.claude/commands/` als Karten-Liste. Skill-Editor: Frontmatter-Felder (`name`, `description`, `allowed-tools`, `agent`, `context`, `disable-model-invocation`) per Formular, darunter Prompt-Body in Monaco. Kategorien als Ordner-Tree. Skills direkt aus dem Editor heraus testen. Import/Export als ZIP.

### 14.2 Prompt-Bibliothek

Vorgefertigte Templates für häufige Tasks (Code Review, Feature implementieren, Bug fixen, Tests schreiben, Refactoring, Security Audit, API-Design…). Parameter-Felder zum Befüllen, dann Prompt zusammenbauen. Eigene Templates erstellen, speichern, teilen.

### 14.3 Prompt-History

Alle ausgeführten Prompts chronologisch mit Timestamp, Agent, Token-Verbrauch, Ergebnis. Mit einem Klick wiederholen. Favoriten markieren. Volltextsuche.

### 14.4 Prompt-Chains

Mehrere Prompts hintereinander verketten (Output als Input übergeben). Visueller Chain-Builder per Drag & Drop. Bedingte Verzweigungen: Wenn Output enthält X → Prompt A, sonst B. Chain als Task speicherbar.

---

## 17. MCP-Manager & Plugin-System

### 15.1 MCP-Übersicht

Alle konfigurierten MCP-Server als Karten: Name, Transport (stdio/SSE/HTTP), Status (verbunden/getrennt/Fehler), Tool-Anzahl. `/mcp`-Status als Live-Panel.

### 15.2 MCP-Server hinzufügen

Assistent-Dialog mit Suche im MCP-Registry. Manuell: Name, Kommando/URL, Transport, ENV-Variablen. Scope: User-global oder nur dieses Projekt. Stdio vs. Remote automatisch erkannt.

### 15.3 Tool-Explorer

Alle MCP-Tools visuell gelistet. Beschreibung, Parameter, Beispiel-Output. Test-Button. Tool per Drag & Drop einem Agent zuweisen.

### 15.4 Plugin-Manager

Claude Code Plugins = versionierte Bundles aus MCP-Servern + Skills + Definitionen. Install via URL, npm-Paket oder lokales Verzeichnis. Update/Deinstall. Abhängigkeiten visualisiert. Community-Marketplace via GitHub/npm.

---

## 18. Hooks-Editor

Alle Claude-Code-Hook-Typen visuell konfigurierbar:

**Hook-Typen:** `PreToolUse` · `PostToolUse` · `Notification` · `Stop` · `SubagentStop` · `WorktreeCreate` · `WorktreeRemove`

**Hook-Builder:** Ereignis wählen → Matcher (regex auf Tool-Name, Exit-Code, Output) → Aktion (Shell-Befehl). Matcher-Typen: `tool_name` equals/contains, `exit_code`, `output` contains.

**One-Click-Hooks (vorinstalliert):**
- Auto-Format nach Edit (prettier, black, gofmt)
- Tests nach Write ausführen
- Gefährliche Bash-Befehle blockieren
- Slack-Notification wenn Agent fertig
- npm install nach package.json-Änderung
- OTel-Event nach Tool-Call loggen
- Commit nach jeder Worktree-Fertigstellung

**Hook-Testing:** Dry-Run-Modus. Log-Ansicht: welche Hooks haben gefeuert, Input/Output.

Hooks sind die Brücke zwischen dem Agentic OS Event-Bus und der Claude-Code-CLI — was im Event-Bus als Reaktion definiert wird, landet häufig als Hook in der `.claude/settings.json`.

---

## 19. Git & Deployment

(Zusammengefasst aus früheren Sektionen — kein Widerspruch mehr.)

**Git-Panel:** Status, Branch, Staged/Unstaged, Diff-View, Blame. Commit-Assistent (Claude generiert Message). PR-Erstellung mit AI-generiertem Title und Description.

**Deployment:** CI/CD-Pipeline-Visualizer (GitHub Actions / GitLab CI als Flowchart). Pipeline-Generator für neue Projekte. Environment-Manager (local/staging/production als Cards). Deploy-Button direkt aus ClaudeStudio mit Post-Deploy Health-Check. Rollback-Assistent. Secret-Scanner (accidentell committete Keys in Git-History).

**Deployment-Risiko:** Vor jedem Deploy analysiert Claude den Diff und schätzt Risiko (Low/Medium/High) mit Begründung. Verbindet sich nahtlos mit Pre-Deploy-Checklist-Task.

---

## 20. Cost & Telemetry

### 18.1 Kosten-Dashboard

- Gesamtkosten heute / Woche / Monat — aufgeteilt nach Agent, Modell, Projekt, Skill, Task
- Model-Breakdown: Input/Output/Cache-Read/Cache-Creation-Token
- Estimated USD live während Agent läuft
- Budget-Alert: Push-Notification bei 80% des Tagesbudgets
- Cache-Hit-Rate (je höher desto günstiger)

### 18.2 Produktivitäts-Metriken (OpenTelemetry)

Commits pro Session · Lines of Code geändert · Tool-Acceptance-Rate · Sessions pro Tag · Durchschnittliche Task-Completion-Zeit · API-Latenz / Retry-Rate.

Exportierbar via OTLP zu Datadog, Honeycomb, Grafana, SigNoz, Langfuse oder lokalem Collector.

### 18.3 Admin API Integration

Für Team/Enterprise: `GET /v1/organizations/usage_report/claude_code` — Usage-Reports direkt in ClaudeStudio, aufgeteilt nach User, Budget pro User setzen, Modell-Freigaben verwalten.

---

## 21. Sicherheit & Permissions

### 19.1 Trust-Modus (globaler Default + per Projekt + per Agent)

Ein zentraler Modus-Schalter — immer sichtbar in der Titelleiste:

| Modus | Verhalten | Farbe |
|---|---|---|
| **Strict** 🔴 | Jede Tool-Aktion einzeln bestätigen | Rot |
| **Standard** 🟡 | Ask bei kritischen Aktionen (rm, sudo, Deploy), Auto bei sicheren | Gelb |
| **Auto** 🟢 | Alle Tools auto-approved, außer explizit blockierten | Grün |
| **YOLO** ⚡ | `--dangerously-skip-permissions` — kein Interrupt außer konfigurierten kritischen Gates | Lila |

YOLO: Einmalige Bestätigung beim Aktivieren ("Claude kann Dateien löschen, pushes machen, deployen — sicher?"). Danach keine weiteren Warnungen. Trust-Modus-Indikator immer in Titelleiste sichtbar.

**Kritische Gates** (wirken auch im YOLO-Modus, konfigurierbar):
- Push auf main/production-Branch
- `rm -rf` mit Pfad außerhalb des Projektverzeichnisses
- Credentials an externe URLs senden
- Deploy ohne bestandene Pre-Deploy-Checklist

### 19.2 Granulare Permissions

Checkbox-Matrix: Bash, Read, Write, Edit, WebSearch, je MCP-Tool → `always allow` / `ask` / `always deny`. Bash-Spezifisch: Regex-Allowlist + Blocklist. Subagenten haben eigenen Permission-Satz (können nie interaktiv fragen).

Unterscheidung: User-Level (`~/.claude/settings.json`) und Projekt-Level (`.claude/settings.json`). ClaudeStudio zeigt beide Ebenen transparent und merged sie korrekt.

### 19.3 Weitere Sicherheitsfeatures

- Prompt-Injection Guard: Hook der Outputs auf Injection-Pattern prüft
- MCP-Server Allowlist: Nur gelistete Server dürfen verwendet werden
- Audit-Log: Jede Agent-Aktion geloggt, unabhängig vom Trust-Modus
- Secret-Scanner: Git-History-Scan nach accidentell committeten Keys
- Dangerous-Command-Filter: Regex-Blockliste für Bash-Befehle (auch im YOLO-Modus konfigurierbar aktiv)
- `.claude/`-Ordner wird wie executable Code behandelt (Warnung bei Änderungen durch externe Prozesse)

---

## 22. Autonomer Build-Loop

ClaudeStudio wird nicht von Hand gebaut, sondern von einem selbstlaufenden Claude-Code-Loop der das Tool Feature für Feature implementiert und **jedes Feature im echten Betrieb verifiziert**. Die vollständige Spezifikation steht im Begleitdokument `ClaudeStudio_BuildLoop.md`. Hier die Kernidee:

### 22.1 Das Verifikations-Gesetz

> Ein Feature gilt NUR als fertig, wenn Claude im echten Betrieb bewiesen hat dass es funktioniert. "MCP-Server ist verbunden" zählt nicht — erst wenn Claude über den MCP-Server nachweislich eine echte Operation ausgeführt und das Ergebnis sinnvoll verwendet hat, mit Screenshot/Log als Beweis, ist das Feature fertig.

Das gilt für alle Subsysteme: Agent-A2A erst fertig wenn eine echte Übergabe im Log floss · Vector-DB erst fertig wenn ein echter Chunk eingebettet und per Suche mit korrektem Score gefunden wurde · Voice erst fertig wenn ein gesprochener Befehl transkribiert, ausgeführt und hörbar beantwortet wurde.

### 22.2 Loop-Struktur (Anthropic-Harness)

Basiert auf Anthropics offiziellem Long-Running-Agent-Harness (Nov 2025) plus Planner/Generator/Evaluator-Pattern:

- **Initializer** (einmal): erstellt `feature_list.json` (~280 Features, alle "failing"), `init.sh`, `claude-progress.txt`, Test-Harness
- **Loop pro Feature**: Orientierung → Planner wählt 1 Feature → Generator implementiert (im Worktree) → Evaluator testet skeptisch im echten Betrieb → bestanden: commit + "passing"; nicht bestanden: max 3 Fix-Versuche → Clean State & Handoff
- **Loop-Guards**: numerisches Iterations-Limit, Repetition-Detection, USD-Budget-Cap, Progress-Watchdog (3 Sessions kein Fortschritt → Eskalation)
- **Mistakes → Memory**: wiederholte Fehler werden als Regel in CLAUDE.md oder als Skill geschrieben

### 22.3 Test-Projekte

Der Loop baut vier echte Mini-Projekte zum Verifizieren: `todo-api` (MCP, Git, A2A) · `landing-page` (UI, Browser, Voice) · `invoice-app` (Task-Library, Compliance) · `data-pipeline` (Vector-DB, Memory, Brain-Graph).

### 22.4 Dogfooding

Sobald der Agentic-OS-Kern läuft, baut sich ClaudeStudio mit sich selbst weiter — der Build-Loop wird als Task in der eigenen Task-Library hinterlegt, der Supervisor steuert ihn, jedes neue Feature erscheint live im OS View. Das ist der ultimative Test: **Wenn ClaudeStudio sich selbst bauen kann, funktioniert es.**

---

## 23. Feature-Katalog (100 weitere Features)

Kompakt gelistet — alle in den vorherigen Sektionen kohärent eingebettet:

**Code-Qualität:** Auto-Loop bis Tests grün · /ultrareview Security Audit · Dependency CVE-Scan · Dead Code Detector · Duplikat-Scanner · Complexity-Heatmap · PR-Review-Agent · Commit-Qualitätsprüfung · Regressionsschutz-Modus · ADR-Generator · Tech-Debt-Tracker · API-Konsistenz-Checker

**Testing:** Test-Generierungs-Agent · Coverage-Dashboard · Snapshot-Manager · E2E-Recorder (Playwright) · Test-Priorisierung · Fuzzing-Agent · Performance-Regression-Detection · Accessibility-Test-Agent · Security-Test-Generator · Mock-Server-Generator

**DevOps:** CI/CD-Pipeline-Visualizer · Pipeline-Generator · Deployment-Risiko-Predictor · Environment-Manager · Rollback-Assistent · IaC-Validator (Terraform, Helm) · Docker-Optimizer · Secret-Scanner · Feature-Flag-Manager · DORA-Metriken · Monitoring-Integration (Sentry/Datadog) · Canary-Deploy-Steuerung

**Dokumentation:** Auto-Docs-Agent · README-Generator · Changelog-Writer · API-Portal · Architektur-Diagramm-Generator · Onboarding-Guide · Decision-Log · Comment-Qualitätsprüfer · Confluence/Notion-Sync

**UI & Design:** Screenshot-to-Code · Figma-to-Code (MCP) · Design-Mode mit visuellem Feedback-Loop · CSS-Variable-Extraktor · Responsive-Checker · Dark-Mode-Generator · Component-Library-Audit · Storybook-Generator · Icon-Manager

**Refactoring:** Framework-Migrations-Assistent · DB-Migration-Assistent · Sync-to-Async-Konverter · TypeScript-Migration · Monorepo-Wizard · i18n-Agent · Logging-Audit · Error-Handling-Audit · Code-Modernisierungs-Agent

**Multi-Model:** Model-Router · Extended Thinking Modus · Multi-Model-Vergleich · Kontext-Optimierungs-Assistent · Prompt-Optimizer · Agent-Performance-Benchmarks · Fallback-Chain · Computer-Use-Integration (eingebetteter Browser-Agent)

**Team-Collaboration:** Live-Session-Sharing · Session-Kommentare · Skill-Library-Git-Sync · Agent-Rollen-Templates · Code-Ownership-Map · Standup-Report-Generator · Sprint-Review-Assistent · Onboarding-Task-Sequenz · Pair-Mode

**Persönliche Produktivität:** Focus-Mode · Context-Checkpoint · Smart-Resume (nach Crash) · Daily-Task-Briefing · Shortcut-Cheatsheet (Cmd+/ Overlay) · Kontextwechsel-Assistent · Session-Notizpad · Arbeitszeit-Tracking (Toggl-Export) · Weekend-Modus · Persönliches KI-Changelog

**Integrationen:** GitHub-Issues-Sync (bidirektional) · Linear-Integration · Slack-Bot-Modus · Email-to-Task (Gmail MCP) · Vercel/Coolify-Deploy-Widget · Supabase-Dashboard · Browser-Preview (eingebettet, localhost) · Terminal-Multiplexer (mehrere Sessions parallel) · Lokales-LLM-Fallback (Ollama) · Plugin-Marketplace · Offline-Modus · Mobile Companion App (iOS/Android — Notifications, Status, einfache Prompts)

---

## 24. Roadmap

### Phase 0 — Build-Loop-Bootstrap (parallel, ab Tag 1)
Der autonome Build-Loop (Sektion 22, Dokument `ClaudeStudio_BuildLoop.md`) wird zuerst aufgesetzt: Initializer schreibt `feature_list.json`, Test-Harness und die vier Test-Projekte. Ab dann baut der Loop die folgenden Phasen großteils selbst — jedes Feature im echten Betrieb verifiziert.

### Phase 1 — Foundation (3 Monate)
Swift/Rust-Grundstruktur · IPC-Bridge · Claude Code CLI-Subprocess-Manager · Google-Analytics-Design-System (Tokens, KPI-Karten, Swift Charts) · Projekt-Anlage-Wizard · File-Explorer · CLAUDE.md Visual Editor · Session-Panel (Agent starten, Live-Output, stoppen) · Basic Trust-Modus-Schalter · Session-Archiv (SQLite) · Global CLAUDE.md Editor · Cost-Tracking

### Phase 2 — Power (3 Monate)
Agent Studio + Teams · Worktree-Manager · Hooks-Editor · MCP-Manager · Definition-Library + Sidebar · Cross-Project Memory · Vector-DB (Qdrant) einrichten · Skill-Manager · Prompt-Bibliothek · Git-Integration · OTel-Dashboard

### Phase 3 — Intelligence (3 Monate)
Voice Assistant (STT/TTS) · Brain View (Knowledge Graph) · Task-Library (vorgefertigte Tasks) · Co-Pilot (KI-Ratgeber) · Agentic OS (Supervisor, Event-Bus, A2A) · Task-Builder (eigene Tasks) · Monitor-Agenten · OS View (Mission Control) · Compliance-Task-Pack (DE/AT)

### Phase 4 — Ecosystem (3 Monate)
Plugin-Marketplace (ClaudeStudio-Plugins) · Task-Pack-Marketplace (Community) · Team/Enterprise-Features (Multi-User, Admin API) · Mobile Companion App (iOS) · CI/CD-Integration (GitHub Actions, GitLab CI) · Web-UI für headless/remote Claude Code · Linux/Windows-Port-Vorbereitung (Rust-Core-Extraktion)

---

## 25. Open-Source-Strategie

- **Lizenz**: MIT (Core — Swift-App + Rust-Core) · BSL optional für Enterprise-Addons
- **Repo-Struktur**:
  - `ClaudeStudio/` — Xcode-Projekt (Swift/SwiftUI)
  - `claudestudio-core/` — Rust Workspace (alle Crates)
  - `claudestudio-tasks/` — Community Task-Packs
  - `claudestudio-definitions/` — Community Definitions-Library
- **Community-Contributions**: Task-Templates, Definitions-Packs, MCP-Configs, Themes, Agent-Definitionen
- **Anthropic-Partnership** anstreben: offizielles Community-Tool

---

*ClaudeStudio Konzept v1.1 — Konsolidierte Fassung — Juni 2026*
*Stack: Swift/SwiftUI + Rust Core · macOS-native · Open Source · Google-Analytics-Design*
*Begleitdokument: ClaudeStudio_BuildLoop.md (autonomer Build-Loop mit Echtbetrieb-Verifikation)*
