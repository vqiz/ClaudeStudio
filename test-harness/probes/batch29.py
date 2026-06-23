#!/usr/bin/env python3
"""Verifikation Build-Batch 29 (echter Core, kein Mock):

  F189  Force-Directed-Layout: 10 Nodes / 12 Edges werden überlappungsfrei verteilt
        (jedes Knotenpaar weiter auseinander als die Summe ihrer Radien).
  F190  Node-Radius proportional zur Verbindungszahl: der stark verbundene Node (6 Kanten)
        ist sichtbar größer als ein schwach verbundener (1 Kante).
  F216  Co-Pilot-Vorschlagskarte trägt Titel, Begründungstext und Aktions-Button.
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
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b29.log")) as ctx:
        c = P.Client(ctx["sock"], timeout=30)

        # Graph: n0 ist Hub mit 6 Kanten, n6 ist Blatt mit 1 Kante.
        nodes = [{"id": f"n{i}"} for i in range(10)]
        edges = [["n0", "n1"], ["n0", "n2"], ["n0", "n3"], ["n0", "n4"], ["n0", "n5"], ["n0", "n6"],
                 ["n7", "n8"], ["n8", "n9"], ["n1", "n7"], ["n2", "n8"], ["n9", "n3"], ["n4", "n5"]]
        layout = c.request("graph.layout", {"nodes": nodes, "edges": edges})

        # F189 — alle Nodes/Edges vorhanden + überlappungsfrei
        try:
            ln = layout["nodes"]
            assert layout["node_count"] == 10 and layout["edge_count"] == 12
            overlaps = []
            for i in range(len(ln)):
                for j in range(i + 1, len(ln)):
                    a, b = ln[i], ln[j]
                    d = ((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2) ** 0.5
                    if d <= a["radius"] + b["radius"]:
                        overlaps.append((a["id"], b["id"], round(d, 1)))
            assert not overlaps, f"Überlappungen: {overlaps}"
            record("F189", "pass", ev("F189", "layout.json",
                   {"node_count": 10, "edge_count": 12, "min_distance": round(layout["min_distance"], 1),
                    "sample": ln[:3]}),
                   f"10 Nodes/12 Edges überlappungsfrei verteilt (min-Abstand {layout['min_distance']:.0f})")
        except Exception as e:
            record("F189", "fail", note=str(e))

        # F190 — Radius ~ Grad: Hub (6 Kanten) > Blatt (1 Kante)
        try:
            by = {nd["id"]: nd for nd in layout["nodes"]}
            hub, leaf = by["n0"], by["n6"]
            assert hub["degree"] == 6 and leaf["degree"] == 1
            assert hub["radius"] > leaf["radius"]
            record("F190", "pass", ev("F190", "node-size.json",
                   {"hub": {"degree": hub["degree"], "radius": hub["radius"]},
                    "leaf": {"degree": leaf["degree"], "radius": leaf["radius"]}}),
                   f"Hub-Node (Grad 6, r={hub['radius']}) größer als Blatt (Grad 1, r={leaf['radius']})")
        except Exception as e:
            record("F190", "fail", note=str(e))

        # F216 — Co-Pilot-Vorschlagskarte: Titel + Begründung + Aktion
        try:
            r = c.request("copilot.suggestions", {"state": {"open_findings": 2, "failing_tests": 1}})
            cards = r["suggestions"]
            assert r["count"] >= 2
            card = next(c2 for c2 in cards if c2["action"] == "fix_findings")
            assert card["title"].strip() and card["reason"].strip()
            assert card["action"] and card["action_label"].strip()
            record("F216", "pass", ev("F216", "copilot-card.json", {"card": card, "all": cards}),
                   f"Karte: Titel='{card['title'][:30]}…', Begründung vorhanden, Button='{card['action_label']}'")
        except Exception as e:
            record("F216", "fail", note=str(e))

        c.close()
    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
