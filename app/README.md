# Claude Studio — macOS App

The native macOS front-end for Claude Studio: a SwiftUI application that is a
complete GUI and "Agentic OS" for Claude Code. It surfaces projects, worktrees,
live agent sessions, a Supervisor/Event-Bus mission control, a semantic
knowledge-graph "Brain View", a reusable Task Library, and a voice assistant —
all talking to the Rust core sidecar over a Unix domain socket.

This package is **dependency-free** and builds with the Swift Package Manager
(no Xcode project required). It targets **Swift 6** and **macOS 14+** using only
Apple frameworks (SwiftUI, AppKit, Foundation, Observation).

## Build & Run

```bash
cd app
swift build                 # compile the executable + ClaudeStudioKit library
swift run ClaudeStudio      # launch the app
swift test                  # run the ClaudeStudioKit (MessagePack/IPC) tests
```

> The app launches with realistic sample data, so every view is populated even
> when the Rust core is offline. The title-bar "Core offline" indicator flips to
> "Core connected" once an `IpcClient` session is established (toggleable in
> Settings for demos).

## Targets

| Target              | Kind             | Responsibility |
| ------------------- | ---------------- | -------------- |
| `ClaudeStudio`      | executable       | The SwiftUI app: views, observable state models, sample data. |
| `ClaudeStudioKit`   | library          | Transport + protocol layer shared with the Rust core (IPC client, MessagePack codec, envelope types). |
| `ClaudeStudioKitTests` | test          | Round-trip tests for the MessagePack codec and envelope encoding. |

### App architecture (`Sources/ClaudeStudio`)

- **`ClaudeStudioApp.swift`** — `@main` `App` with a single `WindowGroup`
  hosting `RootView`, plus a `Settings` scene and `Agent`/`File` command menus.
  The shared `AppState` is created once and injected via `.environment`.
- **`Views/RootView.swift`** — the shell: a `NavigationSplitView` with a
  **sidebar** (Workspace + Definitions sections) and a content column that
  switches on the selected `SidebarItem`. The title bar carries the **voice mic
  indicator** and the **TrustMode badge**.
- **`Views/`** — one file per destination:
  - `ProjectsView` — projects list → inspector → docked live session panel.
  - `OSView` — mission-control grid of session cards + live event-bus stream.
  - `BrainView` — `Canvas`-rendered force-directed knowledge graph (placeholder
    layout with a breathing animation; ready to swap in a real simulation).
  - `ArchiveView` — searchable list of completed sessions.
  - `TaskLibraryView` — grid of reusable task-preset cards.
  - `SessionPanelView` — live transcript with **collapsible tool calls** and a
    **cost counter** / budget bar.
  - `SettingsView`, `ContextView` (effective context: AGENTS.md, hooks, injected
    memory), `AgentStudioView` (sub-agent topology + supervisor policy).
- **`Models/`** — `@Observable` state:
  - `AppState` — top-level model: projects, sessions, global `TrustMode`, and the
    simulated supervisor **event bus** (`AsyncStream`).
  - `AgentSession` + `CostTracker` — a live session whose transcript grows from a
    simulated `AsyncStream` of `SessionEvent`s; the cost tracker updates live.
  - `Project` / `Worktree`, `SessionEvent` / `ToolCall`, `TrustMode`
    (mirrors the Rust enum), and `SampleData` (Task Library, Archive, Voice Log,
    knowledge graph).

All async work uses Swift structured concurrency. The simulated streams are
finite/cancellable and are wired so they can be replaced 1:1 by mapping
`IpcClient.events` into the same `SessionEvent` / `BusEvent` types.

## Talking to the Rust core (`Sources/ClaudeStudioKit`)

The app communicates with the Rust core sidecar over a **Unix domain socket**
(default `~/.claudestudio/core.sock`) using **length-prefixed MessagePack
frames**:

```text
┌──────────────┬─────────────────────────────────┐
│ u32 (BE) len │ MessagePack-encoded IpcEnvelope  │
└──────────────┴─────────────────────────────────┘
```

- **`IpcProtocol.swift`** — `IpcEnvelope { id, kind, method, payload }` mirroring
  the Rust struct, the `IpcKind` enum (`request`/`response`/`event`/`error`), the
  default socket path, and `IpcError`.
- **`MessagePack.swift`** — a small, hand-rolled, dependency-free MessagePack
  encoder/decoder covering exactly the value shapes the envelope needs
  (nil/bool/int/uint/double/string/binary/array/map), exposed as `MsgPackValue`.
  Map keys are encoded in sorted order for reproducible frames.
- **`IpcClient.swift`** — an `actor` that owns the socket. It:
  - `connect()` / `disconnect()` to the core,
  - frames and `send(_:)`s requests, awaiting the correlated `response`
    (or throwing `IpcError.remote` on an `error` envelope),
  - `notify(method:payload:)` for fire-and-forget events,
  - runs a background read loop that decodes inbound frames and yields `event`
    envelopes through a public `AsyncStream<IpcEnvelope>`.

> The socket transport uses POSIX `recv`/`send` directly; all mutable state lives
> on the actor, so the client is data-race-free under Swift 6 strict concurrency.

### Wiring the UI to the core (next step)

`AppState.startEventBus()` and `AgentSession.startSimulatedStream()` currently
consume scripted `AsyncStream`s. To go live, construct an `IpcClient`, `connect()`,
and `for await envelope in client.events { … }`, mapping each envelope's
`method` + `payload` (e.g. `"session.event"`, `"supervisor.bus"`) into a
`SessionEvent` / `BusEvent` and appending it exactly as the simulators do today.
