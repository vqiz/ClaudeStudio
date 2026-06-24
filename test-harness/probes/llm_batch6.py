#!/usr/bin/env python3
"""Verifikation LLM-Batch 6 (echter Core, ECHTER claude):

  F200  DSGVO-Audit: der echte claude analysiert ein Projekt mit einem Datenschutz-Problem und meldet
        mindestens ein echtes DSGVO-Finding MIT Datei und Zeilennummer.
  F202  Reverse-Charge-Check: der echte claude erkennt korrekt, dass die Rechnungslogik KEINE
        Reverse-Charge-Behandlung für EU-B2B enthält.
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
    with P.running_core(home=Path.home(), library_dir=ROOT, log_path=Path("/tmp/llm6.log"),
                        env_extra={"CLAUDESTUDIO_CLAUDE_BIN": CLAUDE}) as ctx:
        c = P.Client(ctx["sock"], timeout=420)

        # F200 — DSGVO-Audit: Finding mit Datei + Zeile
        try:
            proj = Path(tempfile.mkdtemp(prefix="cs-f200-"))
            # klares DSGVO-Problem: personenbezogene Daten im Klartext geloggt, kein Consent
            (proj / "server.js").write_text(
                "const express = require('express');\n"
                "const app = express();\n"
                "app.post('/signup', (req, res) => {\n"
                "  console.log('New user email + IP:', req.body.email, req.ip);  // logs personal data\n"
                "  db.query(`INSERT INTO users (email) VALUES ('${req.body.email}')`);\n"
                "  res.send('ok');\n"
                "});\n")
            r = c.request("compliance.check", {"cwd": str(proj), "kind": "dsgvo"})
            rep = r["report"]
            assert isinstance(rep, dict) and rep.get("findings"), f"keine Findings: {rep}"
            withloc = [f for f in rep["findings"]
                       if f.get("file") and (f.get("line") not in (None, "", 0))]
            assert withloc, f"kein Finding mit Datei+Zeile: {rep['findings']}"
            blob = json.dumps(rep).lower()
            assert any(k in blob for k in ("personenbezog", "email", "consent", "einwillig", "log", "dsgvo", "ip")), \
                "kein datenschutzbezogenes Finding"
            record("F200", "pass", ev("F200", "compliance-dsgvo.json", rep),
                   f"DSGVO-Finding mit Datei+Zeile ({withloc[0].get('file')}:{withloc[0].get('line')})")
        except Exception as e:
            record("F200", "fail", note=str(e))

        # F202 — Reverse-Charge: fehlende EU-B2B-Behandlung erkannt
        try:
            proj = Path(tempfile.mkdtemp(prefix="cs-f202-"))
            # Rechnungslogik OHNE Reverse-Charge: fixe 19% MwSt für alle, auch EU-B2B
            (proj / "invoice.js").write_text(
                "function buildInvoice(items, customer) {\n"
                "  const net = items.reduce((s, i) => s + i.price, 0);\n"
                "  const vat = net * 0.19;  // immer 19% deutsche USt\n"
                "  return { net, vat, gross: net + vat, customer };\n"
                "}\n"
                "module.exports = { buildInvoice };\n")
            r = c.request("compliance.check", {"cwd": str(proj), "kind": "reverse_charge"})
            rep = r["report"]
            assert isinstance(rep, dict), f"kein Report: {rep}"
            blob = json.dumps(rep).lower()
            assert "reverse" in blob or "steuerschuldner" in blob or "13b" in blob, "Reverse-Charge nicht thematisiert"
            # erkennt korrekt, dass die Behandlung FEHLT
            assert rep.get("compliant") is False or rep.get("findings"), f"Mangel nicht erkannt: {rep}"
            record("F202", "pass", ev("F202", "compliance-reverse-charge.json", rep),
                   "fehlende Reverse-Charge-Behandlung für EU-B2B korrekt erkannt")
        except Exception as e:
            record("F202", "fail", note=str(e))

        c.close()
    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
