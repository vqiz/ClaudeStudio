# Changelog

All notable changes to ClaudeStudio are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html) once it reaches
a tagged release.

## [Unreleased]

### Added

- **Swift ⇄ Rust bridge.** A typed `CoreClient` / `CoreConnection` over a
  length-prefixed MessagePack Unix-socket protocol, with live connection status
  and config/budget surfaced in the UI.
- **Live Claude sessions**: `session.start` spawns the `claude` CLI (cwd +
  model aware), streams parsed `StreamEvent`s back as `session.event` frames,
  and records the transcript to the archive. The Session panel runs them with a
  prompt composer and a streamed transcript.
- **Real RPC surface** in the Rust sidecar: `config.get`/`config.set`,
  `context.budget`, `session.{start,list,get,search,create,stats}`,
  `git.{status,branch,worktrees,diff,log}`, `tasks.list`, `definitions.list`,
  `mcp.list`.
- **MCP Manager** and a live **Definition Library** view, both reading from the
  core when connected.
- **Live UI data**: the Archive shows the persisted SQLite session archive and
  the Task Library lists the shipped one-click workflows when connected.
- **Live event streaming**: a client sends `events.subscribe` and the core
  pushes `SystemEvent`s as `event` frames over the same connection; the OS View
  renders them in real time. Verified end-to-end.
- **Theming**: System / Light / Dark / Transparent (behind-window vibrancy),
  persisted and selectable from Settings → Appearance.
- **Brand**: a generated logo, macOS app icon (`.icns` + `.appiconset`), README
  banner, and a pure-SwiftUI `BrandMark`.
- **CI**: GitHub Actions running `cargo fmt`/`clippy -D warnings`/`test` and a
  macOS Swift build that runs the cross-language bridge test against the real
  sidecar.

### Fixed

- Socket path mismatch between the app (`core.sock`) and core (was `cs.sock`).
- Error envelopes reconciled to a dedicated `error` IPC kind carrying
  `{ code, message }`.
- `TrustMode` bridged between the SwiftUI enum and the core's
  `strict`/`standard`/`auto`/`yolo` identifiers.
- A deadlock in `IpcClient`: the blocking socket read loop now runs on a
  dedicated thread instead of the actor's executor.

[Unreleased]: https://github.com/vqiz/ClaudeStudio/commits/main
