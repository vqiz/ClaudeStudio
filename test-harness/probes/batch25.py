#!/usr/bin/env python3
"""Verifikation Build-Batch 25: CI/CD-Pipeline-Visualizer (F269), Definition-Vektor-
Auffindung+Injektion (F105). Echter Core + echtes embed_cli. Kein Mock.
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
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b25.log")) as ctx:
        c = P.Client(ctx["sock"], timeout=90)

        # F269 pipeline visualizer parses jobs + needs edges from a workflow YAML
        try:
            yml = (
                "name: CI\n"
                "on: [push]\n"
                "jobs:\n"
                "  build:\n"
                "    runs-on: ubuntu-latest\n"
                "    steps:\n      - run: make\n"
                "  test:\n"
                "    needs: build\n"
                "    runs-on: ubuntu-latest\n"
                "    steps:\n      - run: make test\n"
                "  deploy:\n"
                "    needs: [test]\n"
                "    runs-on: ubuntu-latest\n"
                "    steps:\n      - run: make deploy\n"
            )
            r = c.request("pipeline.visualize", {"content": yml})
            jobs = set(r["jobs"])
            edges = {tuple(e) for e in r["edges"]}
            assert {"build", "test", "deploy"} <= jobs
            assert ("build", "test") in edges and ("test", "deploy") in edges
            record("F269", "pass", ev("F269", "pipeline-graph.json", r),
                   "jeder Job als Knoten, needs-Abhaengigkeiten als Kanten (build->test->deploy)")
        except Exception as e:
            record("F269", "fail", note=str(e))

        # F105 vector-find a definition + inject into the Ebene-5 (active_definitions) block
        try:
            deftext = ("Video Frame Loading Definition VECMARK_F105: Videos werden mit Poster-Frame "
                       "und Lazy-Loading per IntersectionObserver geladen, Format-Prio WebM dann MP4.")
            c.request("knowledge.teach", {"text": deftext, "collection": "definitions", "source": "video-loading"})
            # noise definition so the search has to discriminate
            c.request("knowledge.teach", {"text": "Error Handling Standard: immer try/catch und Logging.",
                                          "collection": "definitions", "source": "error-handling"})
            r = c.request("definitions.vector_inject", {"query": "Füge die Video-Loading-Definition hinzu"})
            assert r["found"] is True and r["score"] > 0.7 and r["definition"] == "video-loading"
            assert "VECMARK_F105" in r["ebene5_block"] and "active_definitions" in r["ebene5_block"]
            record("F105", "pass", ev("F105", "vector-inject.json", r),
                   f"Vector-Suche liefert 'video-loading' (Score {r['score']:.3f}>0.7), VECMARK_F105 in Ebene5")
        except Exception as e:
            record("F105", "fail", note=str(e))

        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
