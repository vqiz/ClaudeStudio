# Run ClaudeStudio without Xcode

You don't need Xcode to build and run ClaudeStudio — the Swift 6 command-line
toolchain is enough, and the app starts the Rust core itself (no second
terminal). Xcode is only nicer for a signed `.app` with the icon and for running
the XCTest suite.

## Prerequisites

```bash
swift --version     # Swift 6 (Xcode 16 toolchain or the standalone Swift 6 toolchain)
cargo --version     # Rust, via https://rustup.rs
claude --version    # the Claude CLI — log in once: `claude /login`, then test `claude -p "hi"`
```

Live sessions use **your `claude` CLI login** (e.g. a Pro/Max subscription) —
ClaudeStudio never calls the Anthropic API or injects a key.

## Option A — one command (recommended)

From the repo root:

```bash
./scripts/dev.sh            # debug build, fast iteration
./scripts/dev.sh --release  # optimized build
```

This builds + runs the core and the app together, wires up your `claude` CLI,
and stops the core when you quit (Ctrl-C or quitting the app). Core logs:
`tail -f /tmp/claudestudio-core.log`.

## Option B — two explicit steps

```bash
# 1. Build the core once (the app locates + starts it from here).
cd core && cargo build --release -p cs-cli

# 2. Launch the app — it spawns the core automatically and connects.
cd ../app && swift run ClaudeStudio
```

## In the app

1. The title bar shows **Core connected** within a couple of seconds (if not,
   open **Settings → Rust Core → Connect**).
2. Type a prompt in the **Session** panel and send it — the core runs `claude`
   and streams the result back; the transcript is saved to the archive.
3. Try **Settings → Appearance** for Light / Dark / Transparent, and browse the
   **Archive**, **Task Library**, **MCP Servers**, and **OS View**.

## Quitting

`⌘Q` (or closing the window) quits the app, which **stops the core it started**.
With `swift run`, `Ctrl-C` in the terminal also works.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Title bar stays **Core offline** / "Could not start the core" | Build the core once: `cd core && cargo build --release -p cs-cli`. |
| A live session does nothing / errors | Ensure `claude -p "hi"` works in a terminal (`claude /login`). |
| `claude` not found by the app | The app adds `~/.local/bin`, `/opt/homebrew/bin`, `/usr/local/bin` to the core's PATH; if yours is elsewhere, set `CLAUDESTUDIO_CLAUDE_BIN=/path/to/claude`. |
| Want to point at a specific core binary | Set `CLAUDESTUDIO_CORE_BIN=/path/to/claudestudio-core`. |
| See what the core is doing | Run the core yourself with logs: `RUST_LOG=debug cargo run -p cs-cli`, then launch the app (it reuses the running core). |
