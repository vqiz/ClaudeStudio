# FAQ

Honest answers to the common questions. ClaudeStudio is an ambitious open-source project under active development — this page tells you what that means in practice.

---

## Is it ready to use?

**Not yet — it's early.** ClaudeStudio is in active development against the [roadmap](roadmap.md). The architecture is settled and Phase 1 (the native shell, IPC bridge, session lifecycle, git, and the durable archive) is the current focus; the memory layer, Agentic OS, voice, and Brain View are later phases and largely **planned**.

Throughout the docs, features are marked **Implemented** / **Planned** so you always know what's real. If you want a polished daily driver today, watch the releases; if you want to help shape a flagship tool, now is a great time to jump in.

---

## What platforms are supported?

**macOS only**, by design. The front-end is native **SwiftUI/AppKit** — there's no cross-platform UI layer. macOS 14 (Sonoma) or later; Apple Silicon recommended, Intel supported.

The Rust **core** is portable in principle (and powers an optional headless `cs-cli`), but the shipping product is the native macOS app. A different OS would need its own native front-end.

---

## Why Swift + Rust instead of Electron?

Three reasons:

| Concern | Swift + Rust | Electron |
| --- | --- | --- |
| **Native feel & performance** | Real macOS UI, GPU-accelerated graph, low memory. | Bundled Chromium, heavier. |
| **A real systems core** | Rust owns process management, concurrency (Tokio), the vector/SQLite layer, and integrations — fast and safe. | JS runtime for systems work. |
| **Separation of concerns** | UI crashes can't take down state; the core is reusable (CLI, remote). | Tighter coupling. |

ClaudeStudio is a developer's daily driver that runs many concurrent sessions, monitors, and MCP servers while holding a permanent memory. That's a systems problem with a native-UX expectation — exactly the Swift + Rust sweet spot. See [ARCHITECTURE.md](../ARCHITECTURE.md) for the full rationale.

---

## Does it need an API key?

ClaudeStudio drives the **official Claude Code CLI** — so it uses whatever authentication your Claude Code CLI is already set up with. Install and authenticate the CLI first ([getting-started.md](getting-started.md#1-prerequisites)); ClaudeStudio doesn't add a second account system.

For **embeddings**, the default is a **local** `nomic-embed` model (no key, nothing leaves your machine). A remote embedding API is an optional fallback you can configure — and can disable entirely with [privacy mode](memory-and-vector.md#privacy-mode).

---

## What about privacy?

Privacy is a first-class design goal:

- The **SQLite archive is local** and **never deleted by the app**.
- **Embeddings default to on-device** (`nomic-embed`); the remote fallback is opt-in.
- **Privacy mode** lets you keep full memory, go local-only, keep archive-only (no vectors), or run fully ephemeral.
- The **secret scanner** strips credentials *before* anything is stored, embedded, or sent.
- The **prompt-injection guard** treats untrusted content as data, not commands.
- Everything security-relevant lands in an **append-only audit log**.

See [memory-and-vector.md](memory-and-vector.md#privacy-mode) and [security.md](security.md).

---

## Is it safe to let agents run autonomously?

Autonomy is gated. **Trust modes** (Strict → Standard → Auto → YOLO) set the default posture, but **critical gates** (destructive filesystem ops, force-push, deploys, secret exposure, config/permission changes, budget overruns) require explicit approval **in every mode — even YOLO**. Permissions are deny-by-default and allowlist-driven for commands, and every action is audited. See [security.md](security.md).

---

## Do I need Qdrant and a local model?

Not to start. ClaudeStudio works without them — you still get the native shell, sessions, git, and the full SQLite archive with keyword search. Semantic recall, cross-project memory, and the Brain View **turn on** when you connect Qdrant + an embedding backend. See [getting-started.md](getting-started.md#5-first-run).

---

## What's the difference between a Definition and a Task?

- A **Definition** (`.def.md`) is reusable *knowledge* — a term, convention, contract, or runbook — that gets injected into context on relevance. See [context-system.md](context-system.md#4-the-definition-library-defmd).
- A **Task** (`.task.json`) is a reusable *unit of work* — an agent + inputs + guardrails + optional schedule — that the Agentic OS runs. See [tasks-and-definitions.md](tasks-and-definitions.md).

Both live as plain files in the repo (`/definitions`, `/tasks`) so they version and share like code.

---

## How can I contribute?

ClaudeStudio is **MIT-licensed** and built in the open at <https://github.com/vqiz/ClaudeStudio>. Good ways to help:

- **Pick a roadmap item** — the [roadmap](roadmap.md) shows what's planned per phase; early phases need the most hands.
- **Core crates** — Rust work on the `cs-*` crates (see the [crate table](../ARCHITECTURE.md#3-rust-core--crate-topology)).
- **Front-end** — SwiftUI views and the IPC client mirror.
- **Content packs** — contribute definitions, tasks, or the kind of jurisdiction packs described in [tasks-and-definitions.md](tasks-and-definitions.md#5-the-deat-compliance-pack).
- **Docs & testing** — improve these docs, file issues, and try the build.

Open an issue to discuss anything substantial before a large PR, and keep changes consistent with the architecture and crate boundaries.

---

## See also

- [Getting Started](getting-started.md) · [Architecture](../ARCHITECTURE.md) · [Roadmap](roadmap.md) · [Security](security.md)
