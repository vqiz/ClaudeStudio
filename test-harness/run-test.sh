#!/usr/bin/env bash
#
# test-harness/run-test.sh — führt EINEN einzelnen Feature-Test aus.
#
# Liest feature_list.json, sucht das Feature mit der gegebenen ID, druckt
# Beschreibung + real_world_test-Schritte + geforderte Evidence und legt das
# Evidence-Verzeichnis an. So hat der Evaluator-Agent einen klaren, gleichen
# Ausgangspunkt für jeden Test.
#
#   ./test-harness/run-test.sh F008
#   ./test-harness/run-test.sh F008 --evidence-dir custom/
#
# Exit 0 = Feature gefunden + Evidence-Verzeichnis bereit.
# Exit 1 = Feature-ID nicht gefunden.
#
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
FEATURES="$ROOT/feature_list.json"

FID="${1:-}"
if [ -z "$FID" ]; then
  echo "Usage: $0 <FEATURE-ID>   z.B. $0 F008" >&2
  exit 2
fi

if [ ! -f "$FEATURES" ]; then
  echo "feature_list.json nicht gefunden ($FEATURES) — zuerst init-build laufen lassen." >&2
  exit 2
fi

EVIDENCE_DIR="$SCRIPT_DIR/evidence/$FID"

# Erst prüfen ob das Feature existiert — Evidence-Verzeichnis erst danach anlegen,
# damit ein Tippfehler in der ID keine Geister-Ordner hinterlässt.
python3 - "$FEATURES" "$FID" "$EVIDENCE_DIR" <<'PY'
import json, sys
features_path, fid, evidence_dir = sys.argv[1], sys.argv[2], sys.argv[3]
with open(features_path) as f:
    features = json.load(f)
feat = next((x for x in features if x.get("id") == fid), None)
if feat is None:
    print(f"✗ Feature {fid} nicht in feature_list.json gefunden.", file=sys.stderr)
    sys.exit(1)

bar = "─" * 70
print(bar)
print(f"  {feat['id']}  [{feat['category']}]  priority={feat['priority']}  passes={feat['passes']}")
print(bar)
print(f"Beschreibung:\n  {feat['description']}\n")
deps = feat.get("depends_on") or []
print(f"depends_on: {', '.join(deps) if deps else '—'}\n")
print("Real-World-Test (EXAKT ausführen, nichts vereinfachen):")
for i, step in enumerate(feat["real_world_test"], 1):
    print(f"  {i}. {step}")
print()
print(f"Geforderte Evidence:\n  {feat['evidence_required']}")
print(f"\n→ Lege Evidence ab unter: {evidence_dir}/")
print(bar)
PY
rc=$?
[ $rc -eq 0 ] || exit $rc

mkdir -p "$EVIDENCE_DIR"
echo
echo "Evidence-Verzeichnis bereit: $EVIDENCE_DIR"
echo "Nach bestandenem Test: in feature_list.json NUR \"passes\": true für $FID setzen."
