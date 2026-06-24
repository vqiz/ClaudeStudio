#!/usr/bin/env python3
"""Verifikation LLM-Batch 5 (echter Core, ECHTER claude):

  F241  Skill direkt testen: der echte claude führt das Shell-Kommando des Skills wirklich aus;
        das echte Ergebnis (inkl. berechnetem Wert) erscheint im Output.
  F347  Prompt-Optimizer: der echte claude verbessert einen schwachen Prompt deutlich (konkreter,
        länger, mit Format/Akzeptanzkriterien).
  F201  AGB-/Impressum-Checker: der echte claude analysiert ein Website-Fixture OHNE Impressum und
        meldet korrekt das fehlende Impressum als Finding.
"""
from __future__ import annotations
import json, os, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
CLAUDE = os.path.expanduser("~/.local/bin/claude")
results: dict[str, dict] = {}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def main():
    with P.running_core(home=Path.home(), library_dir=ROOT, log_path=Path("/tmp/llm5.log"),
                        env_extra={"CLAUDESTUDIO_CLAUDE_BIN": CLAUDE}) as ctx:
        c = P.Client(ctx["sock"], timeout=420)

        # F241 — Skill mit Shell-Kommando, echte Ausführung
        try:
            r = c.request("skills.test", {
                "body": "Dieses Skill prüft die Umgebung. Führe das Shell-Kommando aus:\n"
                        "`echo SKILL_F241_OK && echo summe=$((19 + 23))`"})
            out = r["output"]
            assert "SKILL_F241_OK" in out and "42" in out, f"Skill-Output ohne echtes Ergebnis: {out[:200]}"
            record("F241", "pass", ev("F241", "skill-test.json", {"output_tail": out[-300:]}),
                   "Skill-Shell-Kommando real ausgeführt (SKILL_F241_OK, summe=42 im Output)")
        except Exception as e:
            record("F241", "fail", note=str(e))

        # F347 — Prompt-Optimizer verbessert deutlich
        try:
            r = c.request("prompts.optimize", {"prompt": "mach die api besser"})
            opt = r["optimized"].lower()
            assert r["optimized_len"] >= r["original_len"] * 4, f"kaum optimiert: {r['optimized_len']} vs {r['original_len']}"
            markers = ["format", "test", "kriteri", "schritt", "endpoint", "akzeptanz", "ziel", "beispiel", "anforder"]
            hits = sum(1 for m in markers if m in opt)
            assert hits >= 2, f"optimierter Prompt nicht konkreter (marker hits={hits})"
            record("F347", "pass", ev("F347", "prompt-optimize.json",
                   {"original": r["original"], "optimized": r["optimized"],
                    "original_len": r["original_len"], "optimized_len": r["optimized_len"]}),
                   f"Prompt optimiert: {r['original_len']} -> {r['optimized_len']} Zeichen, konkreter ({hits} Marker)")
        except Exception as e:
            record("F347", "fail", note=str(e))

        # F201 — Impressum-Check meldet fehlendes Impressum
        try:
            proj = Path(tempfile.mkdtemp(prefix="cs-f201-"))
            (proj / "index.html").write_text(
                "<!doctype html><html><head><title>Mein Shop</title></head>"
                "<body><h1>Willkommen im Shop</h1><p>Tolle Produkte.</p>"
                "<a href='/products'>Produkte</a></body></html>\n")
            (proj / "about.html").write_text("<html><body><h1>Über uns</h1></body></html>\n")
            r = c.request("compliance.check", {"cwd": str(proj), "kind": "impressum"})
            rep = r["report"]
            assert isinstance(rep, dict) and rep.get("findings"), f"keine Findings: {rep}"
            blob = json.dumps(rep).lower()
            assert "impressum" in blob, "Impressum-Mangel nicht erkannt"
            assert rep.get("compliant") is False, f"fälschlich compliant: {rep.get('compliant')}"
            record("F201", "pass", ev("F201", "compliance-impressum.json", rep),
                   f"fehlendes Impressum erkannt ({len(rep['findings'])} Findings, compliant=False)")
        except Exception as e:
            record("F201", "fail", note=str(e))

        c.close()
    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
