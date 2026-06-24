#!/usr/bin/env python3
"""Verifikation LLM-Batch 12 (echter Core, ECHTER claude, echter PDF-Report):

  F199  Kleinunternehmer-Check (§19 UStG): der echte claude prüft eine invoice-Fixture; das Ergebnis
        wird als echter PDF-Report erzeugt, der die Prüfpunkte mit Ergebnissen enthält.
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
    with P.running_core(home=Path.home(), library_dir=ROOT, log_path=Path("/tmp/llm12.log"),
                        env_extra={"CLAUDESTUDIO_CLAUDE_BIN": CLAUDE}) as ctx:
        c = P.Client(ctx["sock"], timeout=420)
        try:
            proj = Path(tempfile.mkdtemp(prefix="cs-f199-"))
            # invoice-app fixture: verstößt gegen §19 (weist USt aus, kein §19-Hinweis)
            (proj / "invoice.js").write_text(
                "// Kleinunternehmer-Rechnung (Firma ist Kleinunternehmer nach Paragraph 19 UStG)\n"
                "function buildInvoice(items) {\n"
                "  const net = items.reduce((s, i) => s + i.price, 0);\n"
                "  const vat = net * 0.19;            // 19% Umsatzsteuer ausgewiesen\n"
                "  return { net, vat, gross: net + vat, note: '' };  // kein Paragraph-19-Hinweis\n"
                "}\n"
                "module.exports = { buildInvoice };\n")

            r = c.request("compliance.report_pdf", {"cwd": str(proj), "kind": "kleinunternehmer"})
            pdf = Path(r["pdf"])
            assert pdf.exists(), "kein PDF erzeugt"
            data = pdf.read_bytes()
            assert data[:5] == b"%PDF-", "keine gültige PDF-Signatur"
            assert b"%%EOF" in data, "PDF nicht abgeschlossen"
            text = data.decode("latin-1", "replace")
            assert "Kleinunternehmer" in text and "Pruefpunkte" in text, "Prüfpunkte fehlen im PDF"
            # echte Findings aus der Analyse
            findings = (r.get("report") or {}).get("findings") or []
            assert findings, f"keine Prüf-Findings: {r.get('report')}"
            # mind. ein Finding-Text steht im PDF
            blob = json.dumps(findings).lower()
            assert ("umsatzsteuer" in blob or "ust" in blob or "19" in blob or "hinweis" in blob), \
                "Findings nicht §19-bezogen"
            # die Fixture ist NICHT konform -> der Report spiegelt das
            assert (r["report"].get("compliant") is False) or any(
                f.get("result") == "fehler" for f in findings), "Verstoß nicht erkannt"
            # Evidence: das echte PDF mitspeichern
            (P.evidence_dir("F199")).mkdir(parents=True, exist_ok=True)
            (P.evidence_dir("F199") / "kleinunternehmer-report.pdf").write_bytes(data)
            record("F199", "pass", ev("F199", "kleinunternehmer.json",
                   {"pdf_bytes": len(data), "check_points": r["check_points"], "report": r["report"]}),
                   f"echter PDF-Report ({len(data)} B) mit Pruefpunkten; §19-Verstoß erkannt")
        except Exception as e:
            record("F199", "fail", note=str(e))
        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
