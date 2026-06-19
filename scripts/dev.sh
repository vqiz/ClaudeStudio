#!/usr/bin/env bash
#
# ClaudeStudio dev launcher.
#
# Builds and starts the Rust core sidecar and the SwiftUI app together, waits for
# the IPC socket, wires up your `claude` CLI (so live sessions use your CLI login
# / subscription — never the Anthropic API), and tears the core down when the app
# exits or you press Ctrl-C.
#
#   ./scripts/dev.sh            # debug build (fast iteration)
#   ./scripts/dev.sh --release  # optimized build
#
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ---- args -----------------------------------------------------------------
PROFILE="debug"
CARGO_PROFILE_FLAG=""
SWIFT_PROFILE_FLAG=""
for arg in "$@"; do
  case "$arg" in
    --release)
      PROFILE="release"
      CARGO_PROFILE_FLAG="--release"
      SWIFT_PROFILE_FLAG="-c release"
      ;;
    -h|--help)
      cat <<'USAGE'
ClaudeStudio dev launcher — builds & runs the Rust core and the SwiftUI app
together, waits for the IPC socket, wires up your `claude` CLI (live sessions use
your CLI login / subscription — never the Anthropic API), and stops the core when
the app exits or you press Ctrl-C.

Usage:
  ./scripts/dev.sh            debug build (fast iteration)
  ./scripts/dev.sh --release  optimized build
  ./scripts/dev.sh --help     this help

Env overrides: RUST_LOG (default info), CLAUDESTUDIO_CLAUDE_BIN (auto-detected).
USAGE
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg (try --help)" >&2
      exit 2
      ;;
  esac
done

# ---- pretty output --------------------------------------------------------
bold="$(tput bold 2>/dev/null || true)"
dim="$(tput dim 2>/dev/null || true)"
red="$(tput setaf 1 2>/dev/null || true)"
green="$(tput setaf 2 2>/dev/null || true)"
reset="$(tput sgr0 2>/dev/null || true)"
say()  { echo "${bold}${green}▸${reset} $*"; }
warn() { echo "${bold}${red}!${reset} $*" >&2; }

# ---- prerequisites --------------------------------------------------------
command -v cargo >/dev/null 2>&1 || { warn "cargo not found — install Rust via https://rustup.rs"; exit 1; }
command -v swift >/dev/null 2>&1 || { warn "swift not found — install Xcode 16+ (Swift 6)"; exit 1; }

# Wire the `claude` CLI so live sessions use YOUR login (subscription), not the API.
if CLAUDE_BIN="$(command -v claude 2>/dev/null)"; then
  export CLAUDESTUDIO_CLAUDE_BIN="$CLAUDE_BIN"
  say "claude CLI: ${dim}$CLAUDE_BIN${reset} (sessions use your CLI login)"
else
  warn "'claude' not on PATH — everything works except live sessions."
  warn "Install + log in the CLI (\`claude /login\`), then re-run."
fi

# Shipped task/definition libraries + sensible logging.
export CLAUDESTUDIO_LIBRARY_DIR="$ROOT"
export RUST_LOG="${RUST_LOG:-info}"

CORE_LOG="${TMPDIR:-/tmp}/claudestudio-core.log"
SOCK="$HOME/.claudestudio/core.sock"

# ---- build & start the core ----------------------------------------------
say "Building Rust core (${PROFILE})…"
( cd "$ROOT/core" && cargo build -p cs-cli $CARGO_PROFILE_FLAG ) || { warn "core build failed"; exit 1; }
CORE_BIN="$ROOT/core/target/${PROFILE}/claudestudio-core"

rm -f "$SOCK"  # drop any stale socket so the wait below can't see a dead one
say "Starting core → logs: ${dim}tail -f $CORE_LOG${reset}"
"$CORE_BIN" >"$CORE_LOG" 2>&1 &
CORE_PID=$!

cleanup() {
  echo
  say "Shutting down…"
  kill "$CORE_PID" 2>/dev/null || true
  wait "$CORE_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# ---- wait for the socket --------------------------------------------------
say "Waiting for the core socket…"
for _ in $(seq 1 50); do
  [ -S "$SOCK" ] && break
  if ! kill -0 "$CORE_PID" 2>/dev/null; then
    warn "Core exited during startup. Last log lines:"
    tail -n 20 "$CORE_LOG" >&2
    exit 1
  fi
  sleep 0.1
done
if [ ! -S "$SOCK" ]; then
  warn "Socket did not appear within 5s. Last log lines:"
  tail -n 20 "$CORE_LOG" >&2
  exit 1
fi
say "Core ready: ${dim}$SOCK${reset}"

# ---- run the app in the foreground ---------------------------------------
say "Launching app (swift run)…  ${dim}Ctrl-C or quitting the app stops both.${reset}"
( cd "$ROOT/app" && swift run $SWIFT_PROFILE_FLAG ClaudeStudio )
