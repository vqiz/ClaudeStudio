#!/usr/bin/env python3
"""Verifikation Build-Batch 38 (echter Core, echtes git, kein Mock):

  F161  Archiv-Diff-Ansicht: ein gespeicherter Unified-Diff wird farbcodiert — hinzugefügte
        Zeilen grün, entfernte rot.
  F191  Brain-Graph Node-Detail: graph.node_detail liefert für einen Knoten (z.B. Asset 'Logo')
        Typ, Metadaten und alle ein-/ausgehenden Kanten.
"""
from __future__ import annotations
import json, os, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
results: dict[str, dict] = {}
GENV = {**os.environ, "GIT_AUTHOR_NAME": "B38", "GIT_AUTHOR_EMAIL": "b38@cs.test",
        "GIT_COMMITTER_NAME": "B38", "GIT_COMMITTER_EMAIL": "b38@cs.test"}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, env=GENV)


def nid(resp):
    return resp.get("id") or resp.get("node_id")


def main():
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b38.log")) as ctx:
        c = P.Client(ctx["sock"], timeout=15)

        # F161 — Unified-Diff farbcodiert rendern
        try:
            repo = Path(tempfile.mkdtemp(prefix="cs-b38-diff-"))
            git(repo, "init", "-q", "-b", "main")
            (repo / "app.py").write_text("a = 1\nb = 2\nc = 3\n")
            git(repo, "add", "-A"); git(repo, "commit", "-qm", "init")
            (repo / "app.py").write_text("a = 1\nb = 22\nc = 3\nd = 4\n")  # b geändert, d hinzugefügt
            diff = git(repo, "diff", "--", "app.py").stdout
            r = c.request("diff.render", {"diff": diff})
            lines = r["lines"]
            adds = [ln for ln in lines if ln["type"] == "add"]
            dels = [ln for ln in lines if ln["type"] == "remove"]
            assert adds and dels
            assert all(ln["color"] == "green" for ln in adds)
            assert all(ln["color"] == "red" for ln in dels)
            assert any("d = 4" in ln["text"] for ln in adds)      # neue Zeile grün
            assert any("b = 2" in ln["text"] for ln in dels)      # alte Zeile rot
            assert r["added"] == len(adds) and r["removed"] == len(dels)
            record("F161", "pass", ev("F161", "diff-render.json",
                   {"added": r["added"], "removed": r["removed"],
                    "add_sample": adds[0], "remove_sample": dels[0]}),
                   f"Diff farbcodiert: {r['added']} grün (+), {r['removed']} rot (-)")
        except Exception as e:
            record("F161", "fail", note=str(e))

        # F191 — Graph Node-Detail mit Typ, Metadaten und Kanten
        try:
            proj = nid(c.request("graph.add_node", {"type": "project", "label": "todo-api"}))
            logo = nid(c.request("graph.add_node",
                       {"type": "asset", "label": "Logo", "props": {"path": "assets/logo.svg", "kind": "svg"}}))
            comp = nid(c.request("graph.add_node", {"type": "concept", "label": "Header-Component"}))
            c.request("graph.add_edge", {"type": "BELONGS_TO", "from": logo, "to": proj})
            c.request("graph.add_edge", {"type": "USES_ASSET", "from": comp, "to": logo})

            d = c.request("graph.node_detail", {"label": "Logo"})
            assert d["found"] and d["type"] == "asset"
            assert d["props"]["path"] == "assets/logo.svg"
            out_labels = {(e["to_label"], e["type"]) for e in d["outgoing"]}
            in_labels = {(e["from_label"], e["type"]) for e in d["incoming"]}
            assert ("todo-api", "BELONGS_TO") in out_labels       # ausgehende Kante
            assert ("Header-Component", "USES_ASSET") in in_labels  # eingehende Kante
            assert d["edge_count"] == 2
            record("F191", "pass", ev("F191", "node-detail.json", d),
                   "Node 'Logo': Typ=asset, Metadaten + 1 aus-/1 eingehende Kante")
        except Exception as e:
            record("F191", "fail", note=str(e))

        c.close()
    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
