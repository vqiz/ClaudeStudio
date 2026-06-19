# Getting Started

This guide walks you from zero to a running ClaudeStudio: installing prerequisites, building the Rust core and the macOS app, the first launch, and creating your first project with the wizard.

> **Status.** The build steps below describe the intended developer workflow. Some components are still landing (see [roadmap.md](roadmap.md)); where a step depends on a planned component it is marked **(planned)**.

---

## 1. Prerequisites

ClaudeStudio targets **macOS** (the front-end is native SwiftUI/AppKit).

| Requirement | Version | Notes |
| --- | --- | --- |
| macOS | 14 (Sonoma) or later | Apple Silicon recommended; Intel supported. |
| Xcode | 15+ | Provides the Swift toolchain and SwiftUI SDK. |
| Rust | stable, 1.78+ | Install via [rustup](https://rustup.rs). The core is a Cargo workspace. |
| Claude Code CLI | latest | ClaudeStudio drives the official Claude Code CLI. Install and authenticate it first. |
| Qdrant | 1.9+ | For semantic memory. Run locally via Docker or the native binary. **(optional at first run)** |
| Ollama or local embed model | latest | Hosts `nomic-embed` for local embeddings. A remote embedding API is the fallback. **(optional)** |

Verify the toolchains:

```bash
rustc --version
cargo --version
xcodebuild -version
claude --version    # the Claude Code CLI
```

---

## 2. Get the source

```bash
git clone https://github.com/vqiz/ClaudeStudio.git
cd ClaudeStudio
```

Repository layout:

```
ClaudeStudio/
├── core/    ← Rust workspace (the sidecar + cs-cli)
├── app/     ← SwiftUI macOS application
├── docs/    ← documentation
├── tasks/   ← shipped task definitions
└── definitions/  ← shipped definition entries
```

---

## 3. Build the Rust core

The core is the brain: it owns state, persistence, the Agentic OS, and every integration. Build it first.

```bash
cd core
cargo build --release
```

This produces:

- `claudestudio-core` — the sidecar binary the app launches and supervises.
- `cs-cli` — an optional headless binary that exposes core capabilities for scripting/CI **(planned)**.

Run the test suite to confirm a healthy workspace:

```bash
cargo test --workspace
```

For a faster inner loop during development, use a debug build (`cargo build`) and point the app at it (see step 5).

---

## 4. Build the macOS app

```bash
cd ../app
# Open in Xcode:
open ClaudeStudio.xcodeproj
# …or build from the command line:
xcodebuild -scheme ClaudeStudio -configuration Release build
```

The app bundles or locates the `claudestudio-core` binary and launches it as a child process, then connects over the local Unix socket (see [ARCHITECTURE.md](../ARCHITECTURE.md#4-the-ipc-bridge)).

---

## 5. First run

```mermaid
flowchart LR
    Launch[Launch app] --> Spawn[App spawns core sidecar]
    Spawn --> Socket[Connect over Unix socket]
    Socket --> Health[Health check + handshake]
    Health --> Onboard[Onboarding / preflight]
    Onboard --> Wizard[Project wizard]
```

On first launch ClaudeStudio runs a short **preflight**:

1. **Locate the Claude Code CLI** and confirm it is authenticated.
2. **Choose a trust mode** — defaults to **Standard** (see [security.md](security.md)). You can change it any time.
3. **Optional: connect Qdrant + embeddings** for semantic memory. If you skip this, ClaudeStudio still keeps the full SQLite archive; semantic recall simply turns on later when you connect a vector backend.
4. **Privacy mode** — choose whether conversations are vectorized for recall (see [memory-and-vector.md](memory-and-vector.md#privacy-mode)).

---

## 6. The project wizard

Add your first project to start working. The wizard walks through:

| Step | What it asks | Result |
| --- | --- | --- |
| **1. Source** | Path to an existing repo, or clone a URL. | Project root registered. |
| **2. Worktrees** | Whether to enable git worktrees for parallel branches. | `cs-git` sets up worktree management. |
| **3. Context** | Detects/creates a project `CLAUDE.md` (or `AGENTS.md`). | Project memory layer wired up. |
| **4. Definitions** | Imports any `.def.md` files from `/definitions`. | Definition Library populated. |
| **5. Indexing** | Optionally embed code & docs into Qdrant. | `code_chunks` / `documents` collections seeded. |
| **6. MCP & Hooks** | Detects existing MCP servers and hooks in the repo. | Registered in the MCP and Hooks panels. |

When the wizard finishes you land in the project workspace with a session panel, file/diff views, the Agentic OS view, and the Brain View available from the sidebar.

---

## 7. Your first session

1. Open the **Session** panel.
2. Type a prompt (or use [voice](voice.md)).
3. Watch the response stream token-by-token; tool calls surface as permission prompts according to your trust mode.
4. Every turn is written to the **append-only SQLite archive** and (if enabled) embedded for semantic recall.

---

## Next steps

- [Context System](context-system.md) — set up `CLAUDE.md` and the Definition Library.
- [Agentic OS](agentic-os.md) — turn on monitors and the Supervisor.
- [Agents](agents.md) — design custom agents and teams.
- [Security](security.md) — tune trust modes and permissions.
- [Troubleshooting & FAQ](faq.md).
