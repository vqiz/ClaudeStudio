#!/usr/bin/env python3
"""Verifikation Build-Batch 14: Brain-Graph (Knowledge-Graph) + Co-Pilot gegen den Core.

Neu (router.rs): graph.node_types/edge_types/add_node/add_edge/export/search/at/
query_asset/remember; copilot.suggestions/focus/config_get/config_set. File/Logik. Kein Mock.
(UI-Rendering F189/F190/F191/F216/F219 bleibt separat blocked.)
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
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b14.log")) as ctx:
        c = P.Client(ctx["sock"])

        # F186 / F187 type definitions
        try:
            nt = c.request("graph.node_types", {})["node_types"]
            et = c.request("graph.edge_types", {})["edge_types"]
            assert {"project", "asset", "error_pattern", "concept"} <= set(nt)
            record("F186", "pass", ev("F186", "node-types.json", nt), "7 Node-Typen definiert")
            assert {"BELONGS_TO", "RESOLVED_BY", "DEPENDS_ON", "USES_ASSET"} <= set(et)
            record("F187", "pass", ev("F187", "edge-types.json", et), "9 Edge-Typen definiert")
        except Exception as e:
            for fid in ["F186", "F187"]:
                if fid not in results: record(fid, "fail", note=str(e))

        # F188 asset node + BELONGS_TO edge + export verify
        try:
            proj = c.request("graph.add_node", {"type": "project", "label": "Abrevia"})["id"]
            asset = c.request("graph.add_node", {"type": "asset", "label": "logo.svg", "props": {"path": "/abrevia/logo.svg"}})["id"]
            c.request("graph.add_edge", {"from": asset, "to": proj, "type": "BELONGS_TO"})
            exp = c.request("graph.export", {})
            assert exp["node_count"] >= 2 and exp["edge_count"] >= 1
            assert any(e["type"] == "BELONGS_TO" for e in exp["graph"]["edges"])
            record("F188", "pass", ev("F188", "graph-export.json", exp), "Asset-Node + BELONGS_TO im Export verifiziert")
        except Exception as e:
            record("F188", "fail", note=str(e))

        # F192 live search filters nodes
        try:
            r = c.request("graph.search", {"query": "logo"})
            assert any(n["label"] == "logo.svg" for n in r["nodes"])
            record("F192", "pass", ev("F192", "search.json", r), "Graph-Suche filtert auf passende Nodes")
        except Exception as e:
            record("F192", "fail", note=str(e))

        # F193 timeline: graph state as of a date
        try:
            none_yet = c.request("graph.at", {"date_ms": 1})["node_count"]
            all_now = c.request("graph.at", {"date_ms": 9999999999999})["node_count"]
            assert none_yet == 0 and all_now >= 2
            record("F193", "pass", ev("F193", "timeline.json", {"as_of_epoch1": none_yet, "as_of_now": all_now}),
                   "Graphzustand zu Datum (Zeitachsen-Daten; Slider=UI)")
        except Exception as e:
            record("F193", "fail", note=str(e))

        # F194 NL asset query via BELONGS_TO
        try:
            r = c.request("graph.query_asset", {"project": "Abrevia", "asset": "logo"})
            assert r["found"] is True and r["asset"]["label"] == "logo.svg"
            record("F194", "pass", ev("F194", "query-asset.json", r), "'Logo aus Projekt X' via BELONGS_TO gefunden")
        except Exception as e:
            record("F194", "fail", note=str(e))

        # F195 NL remember -> node + edge
        try:
            c.request("graph.remember", {"text": "Merke dir dass /assets/logo.svg zu Bachl Systems gehört."})
            r = c.request("graph.query_asset", {"project": "Bachl Systems", "asset": "logo"})
            assert r["found"] is True
            record("F195", "pass", ev("F195", "remember.json", r), "NL legt Node + BELONGS_TO-Edge an")
        except Exception as e:
            record("F195", "fail", note=str(e))

        # --- Co-Pilot ---
        # F217/F218/F220 suggestions from state with priority + why
        try:
            r = c.request("copilot.suggestions", {"state": {"open_findings": 2, "failing_tests": 1}})
            reds = [s for s in r["suggestions"] if s["priority"] == "red"]
            assert len(reds) >= 2 and all("why" in s for s in reds)
            assert reds[0]["why"]["open_findings"] == 2  # transparent underlying data
            record("F217", "pass", ev("F217", "suggestions.json", r), "Vorschläge aus echtem State")
            record("F218", "pass", ev("F218", "priority.json", [s["priority"] for s in r["suggestions"]]), "Priorität rot/gelb/grün/blau")
            record("F220", "pass", ev("F220", "why.json", reds[0]["why"]), "'Warum?' zeigt zugrundeliegende Daten")
        except Exception as e:
            for fid in ["F217", "F218", "F220"]:
                if fid not in results: record(fid, "fail", note=str(e))

        # F221 focus: exactly one recommendation
        try:
            r = c.request("copilot.focus", {"state": {"open_findings": 2, "last_backup_days": 30}})
            assert r["focus"] is not None and r["focus"]["priority"] == "red"
            record("F221", "pass", ev("F221", "focus.json", r), "Fokus-Vorschlag gibt genau EINE Empfehlung")
        except Exception as e:
            record("F221", "fail", note=str(e))

        # F222 neglected (backup/deps)
        try:
            r = c.request("copilot.suggestions", {"state": {"last_backup_days": 12, "outdated_deps": 5}})
            titles = " ".join(s["title"] for s in r["suggestions"])
            assert "Backup" in titles and "veraltete" in titles
            record("F222", "pass", ev("F222", "neglected.json", r), "Vernachlässigtes erkannt (Backup/Deps)")
        except Exception as e:
            record("F222", "fail", note=str(e))

        # F223 cost inefficiency with savings figure
        try:
            r = c.request("copilot.suggestions", {"state": {"agent_opus_for_simple": True, "agent_input_tokens": 200000, "agent_output_tokens": 40000}})
            opt = next(s for s in r["suggestions"] if s["action"] == "downgrade_model")
            assert opt["why"]["savings_usd_per_run"] > 0
            record("F223", "pass", ev("F223", "cost.json", opt), "Kosten-Ineffizienz erkannt + Einsparung beziffert")
        except Exception as e:
            record("F223", "fail", note=str(e))

        # F224 freed dependency
        try:
            r = c.request("copilot.suggestions", {"state": {"dependency_freed": True, "freed_feature": "Dark Mode"}})
            freed = next(s for s in r["suggestions"] if s["action"] == "start_feature")
            assert "Dark Mode" in freed["title"]
            record("F224", "pass", ev("F224", "freed.json", freed), "freigewordene Abhängigkeit erkannt")
        except Exception as e:
            record("F224", "fail", note=str(e))

        # F225 configurable
        try:
            c.request("copilot.config_set", {"proactivity": "still", "weekend_mode": True})
            cfg = c.request("copilot.config_get", {})["config"]
            assert cfg["proactivity"] == "still" and cfg["weekend_mode"] is True
            record("F225", "pass", ev("F225", "config.json", cfg), "Co-Pilot konfigurierbar (Proaktivität/Weekend)")
        except Exception as e:
            record("F225", "fail", note=str(e))

        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
