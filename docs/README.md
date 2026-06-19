# ClaudeStudio Documentation

Welcome to the ClaudeStudio docs. ClaudeStudio is a native macOS application — a SwiftUI front-end backed by a Rust core sidecar — that turns Claude Code into a full **Agentic OS**: a polished GUI for every Claude Code capability, plus a permanent semantic memory, a Supervisor/Event-Bus agentic layer, a voice assistant, and a knowledge-graph Brain View.

> **Honesty note.** ClaudeStudio is an ambitious open-source project under active development. Throughout these docs, features are marked **Implemented** or **Planned**. See [roadmap.md](roadmap.md) and [faq.md](faq.md) for the current state.

---

## Start here

| Doc | What it covers |
| --- | --- |
| [Getting Started](getting-started.md) | Prerequisites, building the Rust core and the macOS app, first run, and the project wizard. |
| [Architecture](../ARCHITECTURE.md) | The deep technical design: Swift + Rust split, crate topology, IPC bridge, data layer, and the 6-layer context pipeline. |
| [FAQ](faq.md) | Honest answers: is it ready, platforms, why Swift+Rust, API keys, privacy, contributing. |

## Core systems

| Doc | What it covers |
| --- | --- |
| [Agentic OS](agentic-os.md) | Supervisor, Event-Bus, Agent-to-Agent (A2A), scheduler/priority queue, continuous-monitor agents, OS View mission control, and the visual rule editor. |
| [Context System](context-system.md) | Global & project `CLAUDE.md`, cross-project memory, the Definition Library (`.def.md`), inject mechanisms, and the active-context bar. |
| [Memory & Vector](memory-and-vector.md) | Qdrant collections, embeddings, the retrieval pipeline, SQLite archive guarantees, and privacy mode. |
| [Agents](agents.md) | The Agent Studio designer, Agent Teams, the Model Router, and fallback chains. |

## Features

| Doc | What it covers |
| --- | --- |
| [Voice](voice.md) | The STT/wake-word/TTS pipeline, latency targets, what the assistant knows, answer style, and voice actions. |
| [Brain View](brain-view.md) | Node types, edge types, the graph UI, and use cases. |
| [Tasks & Definitions](tasks-and-definitions.md) | The Task Library, `.task.json` structure, the 6-tab builder, scheduling, and the DE/AT compliance pack. |

## Operations & governance

| Doc | What it covers |
| --- | --- |
| [Security](security.md) | Trust modes (Strict/Standard/Auto/YOLO), critical gates, granular permissions, the secret scanner, the prompt-injection guard, and the audit log. |
| [Roadmap](roadmap.md) | The four phases: Foundation, Power, Intelligence, Ecosystem. |

---

## Repository map

```
ClaudeStudio/
├── ARCHITECTURE.md      ← deep technical reference
├── README.md            ← project overview
├── core/                ← Rust workspace (cs-* crates)
├── app/                 ← SwiftUI macOS application
├── docs/                ← you are here
├── tasks/               ← shipped .task.json definitions
└── definitions/         ← shipped .def.md entries
```

> Cross-references: the [Tasks & Definitions](tasks-and-definitions.md) doc links the `/tasks` and `/definitions` folders, and the [Context System](context-system.md) doc explains how `/definitions` is injected at runtime.
