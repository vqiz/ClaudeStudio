# ClaudeStudio Core

The **Rust core** of [ClaudeStudio](https://github.com/vqiz/ClaudeStudio) — a fast,
self-contained sidecar that powers the native macOS application. The SwiftUI
front-end talks to this process over a length-prefixed MessagePack protocol on a
Unix domain socket; the core owns sessions, configuration, semantic memory, the
agentic event bus, Git/worktree orchestration, MCP, hooks, telemetry, and SSH.

It is built as a **Cargo workspace** (`edition = "2021"`, `resolver = "2"`,
`rust-version = "1.75"`).

## Design rules

* **Builds out of the box.** `cargo build` works with *default features only* and
  needs **no system libraries** — no running Qdrant, no `libssh2`, no OpenSSL, no
  native `git2`. SQLite is compiled in via `rusqlite`'s `bundled` feature
  (FTS5 included).
* **Heavy backends are traits.** Anything that would normally require an external
  service (vector DB, OTLP exporter, native SSH) is modeled as a trait with a
  *working* default implementation — an in-memory cosine-similarity store, a
  process-shelling Git/SSH driver, or a log/no-op exporter. Real backends live
  behind non-default Cargo features.
* **Shared types are canonical.** Cross-crate types live in `cs-types`; every
  other crate imports from it.
* **Quality bar.** Each crate forbids `unsafe`, carries crate- and item-level
  docs, defines a `thiserror` `Error` + `Result<T>`, and ships unit tests.

## Crate map

| Crate            | Kind   | Responsibility                                                                 |
| ---------------- | ------ | ------------------------------------------------------------------------------ |
| `cs-types`       | lib    | Shared enums & structs (`TrustMode`, `ModelTier`, `Priority`, `IpcEnvelope`, …) |
| `cs-config`      | lib    | `AppConfig` (settings.json), CLAUDE.md/memory parsing, 6-layer context budget   |
| `cs-ipc`         | lib    | Length-prefixed MessagePack frame codec + async `FrameReader`/`FrameWriter`      |
| `cs-sessions`    | lib    | SQLite-backed session/message store (in-memory or on-disk)                      |
| `cs-vector`      | lib    | Vector store trait + in-memory cosine-similarity default impl                   |
| `cs-git`         | lib    | Git & worktree orchestration by shelling out to the `git` binary               |
| `cs-agentic-os`  | lib    | `EventBus` + `SystemEvent`, the Supervisor/agentic layer primitives             |
| `cs-claude`      | lib    | Claude Code process orchestration & streaming                                   |
| `cs-mcp`         | lib    | MCP server registry & lifecycle                                                 |
| `cs-hooks`       | lib    | Hook discovery, matching & execution                                            |
| `cs-otel`        | lib    | Telemetry trait + log/no-op exporter default impl                               |
| `cs-ssh`         | lib    | Remote execution by shelling out to the `ssh` binary                            |
| `cs-cli`         | bin    | `claudestudio-core` sidecar: loads config, opens session store, EventBus, binds UDS |

> Crates other than the ones above (e.g. `cs-claude`, `cs-mcp`, `cs-hooks`,
> `cs-otel`, `cs-ssh`, `cs-vector`, `cs-git`, `cs-sessions`) are owned by their
> respective agents; this README documents the full intended layout.

## Building & testing

```bash
# From the `core/` directory:
cargo build            # default features only — no system deps required
cargo test             # runs every crate's unit tests
cargo fmt --all        # rustfmt (edition 2021, max_width = 100)
cargo clippy --all-targets
cargo deny check       # advisories + license policy (requires cargo-deny)
```

## Running the sidecar

```bash
cargo run -p cs-cli                       # binds ~/.claudestudio/core.sock
cargo run -p cs-cli -- /tmp/custom.sock   # custom socket path
```

The binary is named `claudestudio-core`. It loads `AppConfig::load_or_default`,
opens an in-memory `SessionStore`, creates an `EventBus`, binds a Unix domain
socket, and dispatches `IpcEnvelope` frames (`ping`, `config.get`,
`context.budget`, …) until `Ctrl-C`.

## License

MIT — see [`LICENSE`](../LICENSE).
