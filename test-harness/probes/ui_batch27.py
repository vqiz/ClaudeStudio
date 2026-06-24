#!/usr/bin/env python3
"""Verifikation F054 — Schnell-Aktionen im Rechtsklickmenü lösen die jeweils ECHTE Operation aus.

Die fünf Aktionen (An Session anhängen / Von Claude erklären / Als Asset markieren / In Monaco öffnen /
Im Finder zeigen) sind in der App als `.contextMenu` an den Datei-Zeilen (ProjectFilesTab) verdrahtet
und rufen `QuickActionRunner.perform(...)` auf. Die Rechtsklick-Geste ist — wie bei allen UI-Features —
durch einen direkten Aufruf desselben Code-Pfads ersetzt (AppDelegate-Seam CLAUDESTUDIO_RUN_QUICKACTIONS):
die App verbindet sich CROSS-PROCESS mit einem ECHTEN laufenden Core und führt alle fünf Aktionen aus.

Verifiziert wird der REALE Effekt jeder Aktion:
  - An Session anhängen → session.inject schreibt ein voice_injected_message-Event (event_log.jsonl).
  - Von Claude erklären → session.create legt eine echte Session "Erkläre <datei>" an (session.list).
  - Als Asset markieren → file.to_asset fügt einen Asset-Knoten in den Graphen (brain_graph.json).
  - In Monaco öffnen   → file.read lädt den Inhalt; MonacoOpenView rendert ihn (OCR-Nachweis, monaco.png).
  - Im Finder zeigen   → NSWorkspace.activateFileViewerSelecting mit der korrekten Datei-URL (result.json).
Zusätzlich: menu.png (Menü-Inhalt mit allen fünf Beschriftungen, OCR).
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
EVID = ROOT / "test-harness" / "evidence"
APP = ROOT / "app" / ".build" / "debug" / "ClaudeStudio"
results: dict[str, dict] = {}

MONACO_TOKEN = "MONACO-NACHWEIS-731"
CSV = f"spalte_alpha,spalte_beta\n{MONACO_TOKEN},wert-zwei\nzeile3,zeile3b\n"


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def ocr(png: Path) -> str:
    return subprocess.run(["tesseract", str(png), "stdout", "--psm", "6"],
                          capture_output=True, text=True).stdout.replace("\n", " ")


def main():
    if not APP.exists():
        print(json.dumps({"results": {"F054": {"status": "fail", "note": "App nicht gebaut"}}})); return
    try:
        (EVID / "F054").mkdir(parents=True, exist_ok=True)
        outdir = EVID / "F054"
        for stale in ("result.json", "menu.png", "monaco.png"):
            (outdir / stale).unlink(missing_ok=True)

        with P.running_core() as ctx:
            home, sock = ctx["home"], ctx["sock"]
            target = home / "data.csv"
            target.write_text(CSV)

            # Echte Session anlegen, in die "An Session anhängen" injiziert.
            c = P.Client(sock)
            created = c.request("session.create", {"title": "Arbeits-Session", "cwd": str(home)})
            sid = created["id"]
            c.close()

            # App-Seam: alle fünf Aktionen gegen DIESEN Core ausführen (Cross-Process-IPC).
            env = {**os.environ, "HOME": str(home),
                   "CLAUDESTUDIO_RUN_QUICKACTIONS": str(outdir),
                   "CLAUDESTUDIO_QA_SOCK": str(sock),
                   "CLAUDESTUDIO_QA_FILE": str(target),
                   "CLAUDESTUDIO_QA_SESSION": sid}
            subprocess.run([str(APP)], env=env, capture_output=True, timeout=40)

            res_path = outdir / "result.json"
            assert res_path.exists(), "App-Seam hat result.json nicht geschrieben (Verbindung fehlgeschlagen?)"
            res = json.loads(res_path.read_text())["results"]
            by_action = {r.get("action"): r for r in res if "action" in r}
            assert "error" not in {k for r in res for k in r}, f"Seam-Fehler: {res}"

            # 1) Jede Aktion meldet ok + die real ausgelöste Operation.
            assert by_action["attachToSession"]["ok"] and by_action["attachToSession"]["op"] == "session.inject", by_action["attachToSession"]
            assert by_action["explain"]["ok"] and by_action["explain"]["op"] == "session.create+inject", by_action["explain"]
            assert by_action["markAsset"]["ok"] and by_action["markAsset"]["op"] == "file.to_asset", by_action["markAsset"]
            assert by_action["openInMonaco"]["ok"] and by_action["openInMonaco"]["op"] == "file.read→monaco", by_action["openInMonaco"]
            rev = by_action["revealInFinder"]
            assert rev["ok"] and rev["op"] == "NSWorkspace.activateFileViewerSelecting", rev
            assert "data.csv" in rev["detail"].get("url", ""), rev

            # 2) REALE Seiteneffekte unabhängig im Core nachlesen.
            statedir = home / ".claudestudio"
            #  a) attach → Inject-Event im event_log
            log = (statedir / "event_log.jsonl").read_text() if (statedir / "event_log.jsonl").exists() else ""
            assert "Angehängte Datei" in log and str(target) in log, f"Inject-Event fehlt im Log: {log[:300]!r}"
            assert "Erkläre mir die Datei" in log, f"Erklär-Inject fehlt im Log: {log[:400]!r}"
            #  b) explain → echte Session "Erkläre data.csv" existiert
            c2 = P.Client(sock)
            sessions = c2.request("session.list", {"limit": 100, "offset": 0})["sessions"]
            c2.close()
            titles = [s.get("title", "") for s in sessions]
            assert any("Erkläre data.csv" in t for t in titles), f"Erklär-Session fehlt: {titles}"
            #  c) markAsset → Asset-Knoten im Graphen mit korrektem Pfad
            graph = json.loads((statedir / "brain_graph.json").read_text())
            assets = [n for n in graph.get("nodes", []) if n.get("type") == "asset"]
            node_id = by_action["markAsset"]["detail"].get("node_id", "")
            matched = [n for n in assets if n.get("id") == node_id and n.get("props", {}).get("path") == str(target)]
            assert matched, f"Asset-Knoten {node_id} mit Pfad {target} fehlt im Graphen: {assets}"

            # 3) Menü-Inhalt + Monaco-Render per OCR. Tesseract verrauscht Umlaute (ä→"aé", ö→"o") und
            # Icons; daher auf ASCII-Buchstaben+Space reduzieren und umlautfreie, je Eintrag eindeutige
            # Fragmente prüfen (deckt alle fünf Beschriftungen ab).
            import re
            raw_menu = ocr(outdir / "menu.png")
            menu_txt = re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", raw_menu.lower())).strip()
            for frag in ("an session", "claude erklaren", "asset markieren", "in monaco", "finder zeigen"):
                assert frag in menu_txt, f"Menü-Eintrag '{frag}' fehlt im OCR: {menu_txt!r} (roh: {raw_menu!r})"
            monaco_txt = ocr(outdir / "monaco.png")
            assert MONACO_TOKEN in monaco_txt and "monaco" in monaco_txt.lower(), \
                f"Monaco-Render zeigt den Dateiinhalt nicht: {monaco_txt[:200]!r}"

            record("F054", "pass", ev("F054", "quick-actions.json", {
                "operations": {a: {"ok": r["ok"], "op": r["op"], "detail": r["detail"]} for a, r in by_action.items()},
                "side_effects": {
                    "inject_event_logged": True,
                    "explain_session_title": next(t for t in titles if "Erkläre data.csv" in t),
                    "asset_node": matched[0],
                    "monaco_ocr": monaco_txt[:120], "menu_ocr": menu_txt[:160],
                },
                "screens": ["test-harness/evidence/F054/menu.png", "test-harness/evidence/F054/monaco.png"],
            }), "Alle 5 Rechtsklick-Aktionen lösen die echte Operation aus (Cross-Process gegen echten Core verifiziert)")
    except Exception as e:
        import traceback
        record("F054", "fail", note=f"{e} | {traceback.format_exc()[-400:]}")

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
