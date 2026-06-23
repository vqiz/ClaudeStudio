#!/usr/bin/env bash
#
# ClaudeStudio — init.sh
#
# Idempotentes Bootstrap-Script für die komplette Dev-/Test-Umgebung des
# autonomen Build-Loops. Mehrfach ausführbar ohne Schaden. Fährt alle
# Komponenten hoch und macht am Ende einen Health-Check aller Subsysteme.
#
#   ./init.sh            # baut Core (debug), startet Qdrant, baut Swift-App
#   ./init.sh --release  # optimierte Builds
#   ./init.sh --no-swift # Swift-Build überspringen (z.B. auf CI ohne Xcode)
#   ./init.sh --health   # nur Health-Check, nichts bauen/starten
#
# Health-Check prüft: Rust-Core-Binary, claude-CLI, Qdrant (:6333),
# Swift-Toolchain, ~/.claudestudio-Layout. Exit 0 = alles grün.
#
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$SCRIPT_DIR"

# ---- args -----------------------------------------------------------------
PROFILE="debug"
CARGO_PROFILE_FLAG=""
SWIFT_PROFILE_FLAG=""
DO_SWIFT=1
HEALTH_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --release) PROFILE="release"; CARGO_PROFILE_FLAG="--release"; SWIFT_PROFILE_FLAG="-c release" ;;
    --no-swift) DO_SWIFT=0 ;;
    --health) HEALTH_ONLY=1 ;;
    -h|--help)
      sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "Unbekanntes Argument: $arg (siehe --help)" >&2; exit 2 ;;
  esac
done

# ---- hübsche Ausgabe ------------------------------------------------------
bold="$(tput bold 2>/dev/null || true)"
dim="$(tput dim 2>/dev/null || true)"
red="$(tput setaf 1 2>/dev/null || true)"
green="$(tput setaf 2 2>/dev/null || true)"
yellow="$(tput setaf 3 2>/dev/null || true)"
reset="$(tput sgr0 2>/dev/null || true)"
say()  { echo "${bold}${green}▸${reset} $*"; }
warn() { echo "${bold}${yellow}!${reset} $*" >&2; }
err()  { echo "${bold}${red}✗${reset} $*" >&2; }

# ---- Konfiguration --------------------------------------------------------
QDRANT_CONTAINER="claudestudio-qdrant"
QDRANT_IMAGE="qdrant/qdrant:latest"
QDRANT_PORT=6333
QDRANT_GRPC_PORT=6334
QDRANT_DATA="$ROOT/qdrant-data"
CORE_BIN="$ROOT/core/target/${PROFILE}/claudestudio-core"
CS_HOME="$HOME/.claudestudio"

# Health-Status sammeln wir in dieser Liste: "NAME|OK|Detail"
declare -a HEALTH

record() { HEALTH+=("$1|$2|$3"); }

# ---------------------------------------------------------------------------
# 1. ~/.claudestudio-Layout sicherstellen (idempotent)
# ---------------------------------------------------------------------------
bootstrap_layout() {
  say "Stelle ~/.claudestudio-Layout sicher…"
  mkdir -p "$CS_HOME/memory/projects" "$CS_HOME/definitions" "$CS_HOME/tasks"
  [ -f "$CS_HOME/memory/global.md" ] || printf '# Cross-Project Memory\n\n_(wird von ClaudeStudio gepflegt)_\n' > "$CS_HOME/memory/global.md"
  [ -f "$CS_HOME/settings.json" ] || printf '{\n  "trust_mode": "standard",\n  "daily_budget_usd": 20.0\n}\n' > "$CS_HOME/settings.json"
  mkdir -p "$QDRANT_DATA"
}

# ---------------------------------------------------------------------------
# 2. Rust-Core bauen (idempotent — cargo cached)
# ---------------------------------------------------------------------------
build_core() {
  if ! command -v cargo >/dev/null 2>&1; then
    err "cargo nicht gefunden — Rust via https://rustup.rs installieren"
    record "rust-core" "FAIL" "cargo fehlt"; return 1
  fi
  say "Baue Rust-Core (${PROFILE})…"
  if ( cd "$ROOT/core" && cargo build -p cs-cli $CARGO_PROFILE_FLAG ); then
    say "Core-Binary: ${dim}$CORE_BIN${reset}"
  else
    err "Core-Build fehlgeschlagen"; record "rust-core" "FAIL" "cargo build error"; return 1
  fi
}

# ---------------------------------------------------------------------------
# 3. Qdrant-Container starten (idempotent — reuse falls vorhanden)
# ---------------------------------------------------------------------------
start_qdrant() {
  if ! command -v docker >/dev/null 2>&1; then
    warn "docker nicht gefunden — Qdrant wird übersprungen (Vector-DB-Features nicht testbar)"
    record "qdrant" "SKIP" "docker fehlt"; return 0
  fi
  if ! docker info >/dev/null 2>&1; then
    warn "Docker-Daemon läuft nicht — Qdrant wird übersprungen"
    record "qdrant" "SKIP" "docker daemon down"; return 0
  fi

  if docker ps --format '{{.Names}}' | grep -qx "$QDRANT_CONTAINER"; then
    say "Qdrant läuft bereits (${dim}$QDRANT_CONTAINER${reset})"
  elif docker ps -a --format '{{.Names}}' | grep -qx "$QDRANT_CONTAINER"; then
    say "Starte vorhandenen Qdrant-Container…"
    docker start "$QDRANT_CONTAINER" >/dev/null
  else
    say "Starte neuen Qdrant-Container (${dim}$QDRANT_IMAGE${reset})…"
    docker run -d --name "$QDRANT_CONTAINER" \
      -p "${QDRANT_PORT}:6333" -p "${QDRANT_GRPC_PORT}:6334" \
      -v "$QDRANT_DATA:/qdrant/storage" \
      "$QDRANT_IMAGE" >/dev/null || { err "Qdrant-Start fehlgeschlagen"; record "qdrant" "FAIL" "docker run error"; return 1; }
  fi
}

# ---------------------------------------------------------------------------
# 4. Swift-App bauen (idempotent)
# ---------------------------------------------------------------------------
build_app() {
  [ "$DO_SWIFT" -eq 1 ] || { warn "Swift-Build übersprungen (--no-swift)"; record "swift-app" "SKIP" "--no-swift"; return 0; }
  if ! command -v swift >/dev/null 2>&1; then
    err "swift nicht gefunden — Xcode 16+ installieren"; record "swift-app" "FAIL" "swift fehlt"; return 1
  fi
  say "Baue Swift-App (${PROFILE})…"
  if ( cd "$ROOT/app" && swift build $SWIFT_PROFILE_FLAG --product ClaudeStudio ); then
    :
  else
    err "Swift-Build fehlgeschlagen"; record "swift-app" "FAIL" "swift build error"; return 1
  fi
}

# ---------------------------------------------------------------------------
# 5. Health-Check aller Komponenten
# ---------------------------------------------------------------------------
health_check() {
  say "Health-Check…"

  # Rust-Core-Binary
  if [ -x "$CORE_BIN" ]; then record "rust-core" "OK" "$CORE_BIN"
  else record "rust-core" "FAIL" "Binary fehlt ($CORE_BIN)"; fi

  # claude-CLI
  if command -v claude >/dev/null 2>&1; then
    record "claude-cli" "OK" "$(claude --version 2>/dev/null | head -1)"
  else
    record "claude-cli" "WARN" "claude nicht auf PATH (Live-Sessions nicht testbar)"
  fi

  # Swift-Toolchain
  if command -v swift >/dev/null 2>&1; then record "swift" "OK" "$(swift --version 2>/dev/null | head -1)"
  else record "swift" "WARN" "swift nicht auf PATH"; fi

  # Qdrant erreichbar?
  if command -v curl >/dev/null 2>&1; then
    qok=0
    for _ in $(seq 1 30); do
      if curl -fsS "http://localhost:${QDRANT_PORT}/healthz" >/dev/null 2>&1 \
         || curl -fsS "http://localhost:${QDRANT_PORT}/" >/dev/null 2>&1; then qok=1; break; fi
      sleep 0.5
    done
    if [ "$qok" -eq 1 ]; then record "qdrant" "OK" "http://localhost:${QDRANT_PORT}"
    else
      # Falls Qdrant bewusst übersprungen wurde, SKIP-Eintrag steht schon — nicht überschreiben
      if ! printf '%s\n' "${HEALTH[@]}" | grep -q '^qdrant|SKIP'; then
        record "qdrant" "FAIL" "nicht erreichbar auf :${QDRANT_PORT}"
      fi
    fi
  else
    record "qdrant" "WARN" "curl fehlt — nicht prüfbar"
  fi

  # ~/.claudestudio-Layout
  if [ -d "$CS_HOME/memory" ] && [ -d "$CS_HOME/definitions" ] && [ -d "$CS_HOME/tasks" ]; then
    record "cs-home" "OK" "$CS_HOME"
  else
    record "cs-home" "FAIL" "Layout unvollständig"; fi

  # ---- Tabelle ausgeben ----
  echo
  echo "${bold}Komponente        Status   Detail${reset}"
  echo "──────────────────────────────────────────────────────────────"
  local fails=0
  for row in "${HEALTH[@]}"; do
    IFS='|' read -r name status detail <<< "$row"
    local color="$green"
    case "$status" in
      FAIL) color="$red"; fails=$((fails+1)) ;;
      WARN|SKIP) color="$yellow" ;;
    esac
    printf "%-17s ${color}%-7s${reset} %s\n" "$name" "$status" "$detail"
  done
  echo "──────────────────────────────────────────────────────────────"
  if [ "$fails" -gt 0 ]; then
    err "$fails kritische(r) Health-Check-Fehler."
    return 1
  fi
  say "Alle kritischen Komponenten ${green}grün${reset}."
  return 0
}

# ---------------------------------------------------------------------------
# Ablauf
# ---------------------------------------------------------------------------
bootstrap_layout
if [ "$HEALTH_ONLY" -eq 0 ]; then
  build_core   || true
  start_qdrant || true
  build_app    || true
fi
health_check
exit $?
