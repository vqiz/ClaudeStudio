#!/usr/bin/env bash
# Echter Core + echte SwiftUI-App. Beweist App↔Core (F004) per per-PID lsof am
# Socket und macht einen Fenster-Screenshot (F014/F015).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EV="$ROOT/test-harness/evidence"
mkdir -p "$EV/F004" "$EV/F014" "$EV/F015"
SOCK="$HOME/.claudestudio/core.sock"
CORE="$ROOT/core/target/debug/claudestudio-core"
APP_BUNDLE="$ROOT/dist/ClaudeStudio.app"
CORE_LOG="$EV/F004/core.log"

export CLAUDESTUDIO_LIBRARY_DIR="$ROOT"; export RUST_LOG=info
[ -x "$HOME/.local/bin/claude" ] && export CLAUDESTUDIO_CLAUDE_BIN="$HOME/.local/bin/claude"

pkill -f claudestudio-core 2>/dev/null; osascript -e 'quit app "ClaudeStudio"' 2>/dev/null; sleep 1
rm -f "$SOCK"
"$CORE" >"$CORE_LOG" 2>&1 &
CORE_PID=$!
for _ in $(seq 1 50); do [ -S "$SOCK" ] && break; sleep 0.1; done
[ -S "$SOCK" ] || { echo "core socket missing"; kill $CORE_PID 2>/dev/null; exit 1; }
echo "core pid=$CORE_PID"

open "$APP_BUNDLE"
sleep 9
APP_PID=$(pgrep -f "ClaudeStudio.app/Contents/MacOS/ClaudeStudio" | head -1)
echo "app pid=$APP_PID"

# ---- F004: beide Prozesse halten Unix-Sockets; Core hat Listener + akzeptierte Verbindung ----
{
  echo "# F004 — App↔Core IPC-Verbindung (per-PID lsof am $SOCK)"
  echo "## Core ($CORE_PID) Unix-Socket-FDs (Listener + akzeptierte Verbindung):"
  lsof -p "$CORE_PID" -U 2>/dev/null
  echo "## App ($APP_PID) Unix-Socket-FDs (Client-Verbindung):"
  [ -n "$APP_PID" ] && lsof -p "$APP_PID" -U 2>/dev/null | grep -i unix
  echo "## Nur diese beiden Prozesse existieren am Socket:"
  pgrep -lf "ClaudeStudio|claudestudio-core"
} | tee "$EV/F004/lsof-socket.txt"

# ---- Fenster nach vorne + Screenshot ----
osascript -e 'tell application "System Events" to set frontmost of (first process whose name is "ClaudeStudio") to true' 2>/dev/null
sleep 1
osascript -e 'tell application "ClaudeStudio" to activate' 2>/dev/null
sleep 2
WINS=$(osascript -e 'tell application "System Events" to get name of windows of (first process whose name is "ClaudeStudio")' 2>/dev/null || echo "n/a")
echo "windows: $WINS" | tee "$EV/F014/windows.txt"
osascript -e 'tell application "System Events" to tell (first process whose frontmost is true) to get {name, position, size}' 2>/dev/null | tee "$EV/F014/frontmost.txt"
BOUNDS=$(osascript -e 'tell application "System Events" to tell (first process whose name is "ClaudeStudio") to get {position, size} of front window' 2>/dev/null || echo "")
echo "bounds: $BOUNDS" | tee -a "$EV/F014/windows.txt"

screencapture -x -o "$EV/F014/shell.png" 2>/dev/null && echo "shell.png full captured"
# zusätzlich Regionscapture exakt auf das Fensterrechteck, falls Bounds verfügbar
if [[ "$BOUNDS" =~ ([0-9-]+),\ ([0-9-]+),\ ([0-9]+),\ ([0-9]+) ]]; then
  X=${BASH_REMATCH[1]}; Y=${BASH_REMATCH[2]}; W=${BASH_REMATCH[3]}; H=${BASH_REMATCH[4]}
  screencapture -x -R"${X},${Y},${W},${H}" "$EV/F014/window.png" 2>/dev/null && echo "window.png region captured ($X,$Y,$W,$H)"
fi
cp "$EV/F014/window.png" "$EV/F015/tokens.png" 2>/dev/null || cp "$EV/F014/shell.png" "$EV/F015/tokens.png" 2>/dev/null

sleep 1
osascript -e 'quit app "ClaudeStudio"' 2>/dev/null; pkill -f "ClaudeStudio.app" 2>/dev/null
kill "$CORE_PID" 2>/dev/null; wait "$CORE_PID" 2>/dev/null
echo "done"
