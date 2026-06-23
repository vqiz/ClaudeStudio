#!/usr/bin/env python3
"""Verifikation Build-Batch 49 (echter Core, echtes curl, OTLP/HTTP-JSON):

  F263  PostToolUse OTel-Event: nach einem Tool-Call wird ein OTel-Span via OTLP an den Collector
        emittiert; der Collector hat einen Span mit Attribut tool=Edit.
  F283  OTLP-Export zu Collector: ein Session-Span wird via OTLP exportiert; der Collector hat den
        Span (service.name=claudestudio, session-Attribut).

Der OTLP-Collector wird durch einen lokalen OTLP/HTTP-JSON-Endpunkt (/v1/traces) ersetzt; getestet
werden der echte OTLP-Span-Aufbau + der echte HTTP-Export.
"""
from __future__ import annotations
import json, sys, threading, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
results: dict[str, dict] = {}
spans: list[dict] = []


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def parse_otlp(body: dict):
    out = []
    for rs in body.get("resourceSpans", []):
        svc = ""
        for a in rs.get("resource", {}).get("attributes", []):
            if a.get("key") == "service.name":
                svc = a.get("value", {}).get("stringValue", "")
        for ss in rs.get("scopeSpans", []):
            for sp in ss.get("spans", []):
                attrs = {a["key"]: a.get("value", {}).get("stringValue", "")
                         for a in sp.get("attributes", [])}
                out.append({"name": sp.get("name"), "service": svc, "attributes": attrs})
    return out


class Collector(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        if self.path.endswith("/v1/traces"):
            try:
                spans.extend(parse_otlp(json.loads(self.rfile.read(n).decode())))
            except Exception:
                pass
            self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
        else:
            self.send_response(404); self.end_headers()

    def log_message(self, *a):
        pass


def main():
    server = HTTPServer(("127.0.0.1", 0), Collector)
    endpoint = f"http://127.0.0.1:{server.server_address[1]}/v1/traces"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b49.log")) as ctx:
        c = P.Client(ctx["sock"], timeout=30)

        # F263 — Tool-Call-Span mit Attribut tool=Edit
        try:
            spans.clear()
            r = c.request("telemetry.export_span", {"endpoint": endpoint, "name": "tool.Edit",
                          "attributes": {"tool": "Edit", "file": "src/index.ts"}})
            assert r["posted_status"] == 200 and r["otlp"] is True
            for _ in range(20):
                if spans:
                    break
                time.sleep(0.05)
            sp = next(s for s in spans if s["attributes"].get("tool") == "Edit")
            assert sp["service"] == "claudestudio" and sp["attributes"]["file"] == "src/index.ts"
            record("F263", "pass", ev("F263", "otel-tool-span.json", {"span": sp, "posted": r["posted_status"]}),
                   "OTLP-Span mit Attribut tool=Edit beim Collector eingegangen")
        except Exception as e:
            record("F263", "fail", note=str(e))

        # F283 — Session-Span via OTLP exportiert
        try:
            spans.clear()
            r = c.request("telemetry.export_span", {"endpoint": endpoint, "name": "session.run",
                          "attributes": {"session.id": "sess-42", "total_cost_usd": "0.0123"}})
            assert r["posted_status"] == 200
            for _ in range(20):
                if spans:
                    break
                time.sleep(0.05)
            sp = next(s for s in spans if s["name"] == "session.run")
            assert sp["service"] == "claudestudio" and sp["attributes"]["session.id"] == "sess-42"
            record("F283", "pass", ev("F283", "otlp-session-span.json", {"span": sp, "posted": r["posted_status"]}),
                   "Session-Span via OTLP beim Collector eingegangen (service.name=claudestudio)")
        except Exception as e:
            record("F283", "fail", note=str(e))

        c.close()
    server.shutdown()
    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
