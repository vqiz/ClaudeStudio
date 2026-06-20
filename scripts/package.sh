#!/usr/bin/env bash
#
# Build a distributable, double-clickable ClaudeStudio.app.
#
# Produces dist/ClaudeStudio.app — a real macOS bundle with the SwiftUI app, the
# Rust core sidecar, the app icon, and the shipped task/definition libraries
# embedded. The app starts the core itself, so the bundle is self-contained.
#
#   ./scripts/package.sh          # build dist/ClaudeStudio.app
#   ./scripts/package.sh --dmg    # …and a dist/ClaudeStudio.dmg
#
# It is ad-hoc code-signed (so it runs locally without an Apple Developer
# account). For distribution to others you'd sign + notarize with a Developer ID.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DIST="$ROOT/dist"
APP="$DIST/ClaudeStudio.app"
VERSION="0.1.0"
MAKE_DMG="no"
[ "${1:-}" = "--dmg" ] && MAKE_DMG="yes"

bold="$(tput bold 2>/dev/null || true)"; green="$(tput setaf 2 2>/dev/null || true)"; reset="$(tput sgr0 2>/dev/null || true)"
say() { echo "${bold}${green}▸${reset} $*"; }

export PATH="$HOME/.cargo/bin:$PATH"
command -v cargo >/dev/null 2>&1 || { echo "cargo not found — install Rust"; exit 1; }
command -v swift >/dev/null 2>&1 || { echo "swift not found — install Xcode 16+"; exit 1; }

# ---- build (release) ------------------------------------------------------
say "Building Rust core (release)…"
( cd "$ROOT/core" && cargo build --release -p cs-cli )
CORE_BIN="$ROOT/core/target/release/claudestudio-core"

say "Building app (release)…"
( cd "$ROOT/app" && swift build -c release )
APP_BIN="$ROOT/app/.build/release/ClaudeStudio"

# ---- assemble the bundle --------------------------------------------------
say "Assembling $APP …"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cp "$APP_BIN" "$APP/Contents/MacOS/ClaudeStudio"
cp "$CORE_BIN" "$APP/Contents/MacOS/claudestudio-core"
chmod +x "$APP/Contents/MacOS/ClaudeStudio" "$APP/Contents/MacOS/claudestudio-core"

cp "$ROOT/app/Resources/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
cp -R "$ROOT/tasks" "$APP/Contents/Resources/tasks"
cp -R "$ROOT/definitions" "$APP/Contents/Resources/definitions"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key><string>ClaudeStudio</string>
  <key>CFBundleIdentifier</key><string>dev.claudestudio.app</string>
  <key>CFBundleName</key><string>ClaudeStudio</string>
  <key>CFBundleDisplayName</key><string>ClaudeStudio</string>
  <key>CFBundleShortVersionString</key><string>${VERSION}</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSApplicationCategoryType</key><string>public.app-category.developer-tools</string>
  <key>NSHumanReadableCopyright</key><string>ClaudeStudio — MIT licensed.</string>
</dict>
</plist>
PLIST

# ---- sign (ad-hoc) --------------------------------------------------------
say "Ad-hoc code-signing…"
# Strip extended attributes (quarantine / Finder info) that make codesign fail
# with "resource fork, Finder information, or similar detritus not allowed".
xattr -cr "$APP" 2>/dev/null || true
codesign --force --deep --sign - "$APP" 2>/dev/null || codesign --force --sign - "$APP"

say "Built ${bold}$APP${reset} (v${VERSION})"
du -sh "$APP" | awk '{print "   size: "$1}'

# ---- optional DMG ---------------------------------------------------------
if [ "$MAKE_DMG" = "yes" ]; then
  say "Creating DMG…"
  DMG="$DIST/ClaudeStudio.dmg"
  rm -f "$DMG"
  STAGE="$(mktemp -d)"
  cp -R "$APP" "$STAGE/"
  ln -s /Applications "$STAGE/Applications"
  hdiutil create -volname "ClaudeStudio" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
  rm -rf "$STAGE"
  say "Built ${bold}$DMG${reset}"
fi

echo
echo "Run it:   open \"$APP\""
echo "Note: first launch of an ad-hoc-signed app may need right-click → Open."
