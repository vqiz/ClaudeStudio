#!/usr/bin/env python3
"""Verifikation Build-Batch 4: Definition-Injektion + Auto-Suggest gegen den Core.

Neu (router.rs): context.assemble lädt benannte Definitionen in die Ebene
active_definitions; definitions.suggest schlägt anhand von Prompt-Keywords passende
Definitionen vor. Echte .def.md-Bibliothek, echter Core. Kein Mock.
"""
from __future__ import annotations
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
results: dict[str, dict] = {}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def main():
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b4.log")) as ctx:
        c = P.Client(ctx["sock"])
        c.request("library.load_defaults", {})  # install shipped .def.md into the state dir
        defname = "Video Frame Loading"

        # F099: inject a definition -> active_definitions layer carries it with tokens
        try:
            a = c.request("context.assemble", {"cwd": str(ROOT), "definitions": [defname]})
            ad = next(l for l in a["layers"] if l["layer"] == "active_definitions")
            assert ad["tokens"] > 0 and "Video Frame Loading" in ad["content"]
            assert "Video Frame Loading" in a["assembled_text"]
            record("F099", "pass", ev("F099", "inject.json",
                   {"active_definitions_tokens": ad["tokens"], "content_excerpt": ad["content"][:200]}),
                   "Definition injiziert (Token-Zähler real; Drag&Drop/blauer Block=UI)")
        except Exception as e:
            record("F099", "fail", note=str(e))

        # F102: active-context data shows the active blocks; toggling removes them
        try:
            both = c.request("context.assemble", {"cwd": str(ROOT),
                             "definitions": [defname, "Error Handling Standard"]})
            ad = next(l for l in both["layers"] if l["layer"] == "active_definitions")
            assert "Video Frame Loading" in ad["content"] and "Error Handling Standard" in ad["content"]
            removed = c.request("context.assemble", {"cwd": str(ROOT), "definitions": [defname],
                                "layers": {"active_definitions": False}})
            ad2 = next(l for l in removed["layers"] if l["layer"] == "active_definitions")
            assert ad2["enabled"] is False and "Video Frame Loading" not in removed["assembled_text"]
            record("F102", "pass", ev("F102", "active-bar.json",
                   {"two_active_blocks": True, "after_remove_absent": True}),
                   "aktive Definition-Blöcke + Entfernen (X-Button=UI)")
        except Exception as e:
            record("F102", "fail", note=str(e))

        # F101: auto-suggest from prompt keywords
        try:
            s = c.request("definitions.suggest", {"prompt": "how should I handle video loading and performance?"})
            names = [x["name"] for x in s["suggestions"]]
            assert "Video Frame Loading" in names
            none = c.request("definitions.suggest", {"prompt": "quarterly tax filing deadlines"})
            record("F101", "pass", ev("F101", "suggest.json", {"matched": names, "unrelated": none["suggestions"]}),
                   "Keyword-basierter Definition-Vorschlag")
        except Exception as e:
            record("F101", "fail", note=str(e))

        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
