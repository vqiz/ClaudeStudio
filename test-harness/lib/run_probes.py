#!/usr/bin/env python3
"""Zentraler Probe-Runner — die EINE Wahrheit fürs Markieren.

Führt jedes Probe-Modul unter test-harness/probes/ neu aus (egal wer es geschrieben
hat), parst dessen {"results": {...}}-Ausgabe, aggregiert alle Feature-Ergebnisse
und setzt passes=true NUR für Features, die beim zentralen Re-Run echt "pass" sind.

So hängt das Markieren nicht am Selbstreport der Autoren-Agenten, sondern an einem
reproduzierten echten Lauf.

  python3 test-harness/lib/run_probes.py            # alle Module, markieren
  python3 test-harness/lib/run_probes.py --dry      # nur ausführen, nicht markieren
  python3 test-harness/lib/run_probes.py git mcp    # nur diese Module
"""
from __future__ import annotations
import json, subprocess, sys, glob, os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cs_probe as P

PROBES = P.ROOT / "test-harness" / "probes"
SKIP = {"__init__"}


def run_module(path: Path, timeout=600):
    try:
        out = subprocess.run([sys.executable, str(path)], capture_output=True, text=True,
                             timeout=timeout, cwd=str(P.ROOT))
    except subprocess.TimeoutExpired:
        return None, f"TIMEOUT after {timeout}s"
    # Letzte JSON-Zeile mit "results" suchen
    res = None
    for line in reversed(out.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and '"results"' in line:
            try:
                res = json.loads(line)["results"]; break
            except Exception:
                continue
    if res is None:
        return None, f"no results JSON (exit {out.returncode}). stderr tail: {out.stderr.strip()[-300:]}"
    return res, None


def main():
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry" in sys.argv
    mods = sorted(glob.glob(str(PROBES / "*.py")))
    mods = [m for m in mods if Path(m).stem not in SKIP and Path(m).stem != "foundation"]
    if argv:
        mods = [m for m in mods if Path(m).stem in argv]

    agg = {}
    report = {}
    for m in mods:
        name = Path(m).stem
        res, err = run_module(Path(m))
        if err:
            report[name] = {"error": err}
            print(f"  ✗ {name}: {err}")
            continue
        counts = {"pass": 0, "fail": 0, "blocked": 0, "other": 0}
        for fid, info in res.items():
            st = info.get("status", "other")
            counts[st if st in counts else "other"] += 1
            agg[fid] = info
        report[name] = counts
        print(f"  ✓ {name}: pass={counts['pass']} fail={counts['fail']} blocked={counts['blocked']}")

    passing = sorted(fid for fid, i in agg.items() if i.get("status") == "pass")
    blocked = sorted(fid for fid, i in agg.items() if i.get("status") == "blocked")
    failing = sorted(fid for fid, i in agg.items() if i.get("status") == "fail")

    # Report ablegen
    (PROBES.parent / "evidence" / "_probe-report.json").write_text(
        json.dumps({"report": report, "results": agg}, ensure_ascii=False, indent=2))

    print(f"\nAGG: pass={len(passing)} fail={len(failing)} blocked={len(blocked)}")
    if not dry:
        n = P.mark_passing(passing)
        d = P.load_features()
        print(f"newly marked passing: {n}")
        print(f"TOTAL passing: {sum(f['passes'] for f in d)}/{len(d)}")
    else:
        print("(dry run — nichts markiert)")
    print("PASS:", " ".join(passing))
    print("FAIL:", " ".join(failing))
    print("BLOCKED:", " ".join(blocked))


if __name__ == "__main__":
    main()
