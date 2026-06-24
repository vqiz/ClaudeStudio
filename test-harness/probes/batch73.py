#!/usr/bin/env python3
"""Verifikation Build-Batch 76 (echter Core, ECHTES nomic-embed-text, echtes curl/HTTP):

  F166  Lokales Embedding-Modell nomic-embed-text einbinden: der Core sendet Text an einen
        nomic-embed-text-Embedding-Endpoint und erhält einen 768-dimensionalen Vektor zurück;
        semantisch verwandter Text liegt näher als unverwandter (echte Embeddings).
  F167  Fallback auf OpenAI text-embedding-3-small, wenn der lokale nomic-Dienst nicht erreichbar
        ist: provider=auto schaltet bei totem nomic_url transparent auf den OpenAI-Endpoint um
        (1536-dim, OpenAI-Antwortformat) und markiert fallback=true.

nomic-embed-text läuft ECHT (transformers + torch, 768-dim) als lokaler Embedding-Dienst; der
OpenAI-Endpoint ist ein Format-Substitut (kein API-Key) — getestet wird die echte Einbindung
(HTTP-Call des Core, 768-dim, Semantik) und die echte Fallback-Logik.
"""
from __future__ import annotations
import json, math, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
results: dict[str, dict] = {}

# ---- echtes nomic-embed-text laden (einmalig) ----
import torch  # noqa: E402
import torch.nn.functional as F  # noqa: E402
from transformers import AutoTokenizer, AutoModel  # noqa: E402

_TOK = AutoTokenizer.from_pretrained("nomic-ai/nomic-embed-text-v1.5")
_MODEL = AutoModel.from_pretrained("nomic-ai/nomic-embed-text-v1.5", trust_remote_code=True)
_MODEL.eval()


def nomic_embed(text: str) -> list[float]:
    enc = _TOK(["search_document: " + text], padding=True, truncation=True, return_tensors="pt")
    with torch.no_grad():
        out = _MODEL(**enc)
    tok_emb = out[0]
    mask = enc["attention_mask"].unsqueeze(-1).expand(tok_emb.size()).float()
    emb = (tok_emb * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
    emb = F.normalize(emb, p=2, dim=1)
    return emb[0].tolist()


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


class NomicService(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
        vec = nomic_embed(body.get("text", ""))
        out = json.dumps({"embedding": vec, "model": "nomic-embed-text", "dim": len(vec)}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.end_headers(); self.wfile.write(out)

    def log_message(self, *a):
        pass


class OpenAIMock(BaseHTTPRequestHandler):
    """OpenAI-Embeddings-Format-Substitut: deterministischer 1536-dim Vektor."""
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n).decode() or "{}") if n else {}
        text = body.get("input", "")
        vec = [((hash((text, i)) % 1000) / 1000.0 - 0.5) for i in range(1536)]
        out = json.dumps({"object": "list", "model": body.get("model"),
                          "data": [{"object": "embedding", "index": 0, "embedding": vec}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.end_headers(); self.wfile.write(out)

    def log_message(self, *a):
        pass


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b)) / (
        math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)) + 1e-12)


def main():
    nomic_srv = HTTPServer(("127.0.0.1", 0), NomicService)
    nomic_url = f"http://127.0.0.1:{nomic_srv.server_address[1]}/embed"
    threading.Thread(target=nomic_srv.serve_forever, daemon=True).start()
    openai_srv = HTTPServer(("127.0.0.1", 0), OpenAIMock)
    openai_url = f"http://127.0.0.1:{openai_srv.server_address[1]}/v1/embeddings"
    threading.Thread(target=openai_srv.serve_forever, daemon=True).start()

    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b73.log")) as ctx:
        c = P.Client(ctx["sock"], timeout=120)

        # ---- F166: echtes nomic, 768-dim + Semantik ----
        try:
            doc = c.request("embedding.embed",
                            {"text": "Stripe payment integration for online checkout",
                             "provider": "nomic", "nomic_url": nomic_url})
            assert doc["provider_used"] == "nomic" and doc["dim"] == 768, doc
            rel = c.request("embedding.embed",
                            {"text": "accepting credit card payments via an API",
                             "provider": "nomic", "nomic_url": nomic_url})["embedding"]
            unrel = c.request("embedding.embed",
                              {"text": "tomorrow's weather forecast in Berlin",
                               "provider": "nomic", "nomic_url": nomic_url})["embedding"]
            sim_rel = cosine(doc["embedding"], rel)
            sim_unrel = cosine(doc["embedding"], unrel)
            assert sim_rel > sim_unrel + 0.05, f"keine sinnvolle Semantik: rel={sim_rel:.3f} unrel={sim_unrel:.3f}"
            record("F166", "pass", ev("F166", "nomic-embed.json",
                   {"dim": doc["dim"], "provider": doc["provider_used"],
                    "sim_related": round(sim_rel, 3), "sim_unrelated": round(sim_unrel, 3),
                    "vector_head": doc["embedding"][:6]}),
                   f"nomic-embed-text: 768-dim; verwandt {sim_rel:.3f} > unverwandt {sim_unrel:.3f}")
        except Exception as e:
            record("F166", "fail", note=str(e))

        # ---- F167: nomic tot -> Fallback auf OpenAI (1536-dim) ----
        try:
            dead = "http://127.0.0.1:1/embed"  # garantiert nicht erreichbar
            r = c.request("embedding.embed",
                          {"text": "Stripe payment integration", "provider": "auto",
                           "nomic_url": dead, "openai_url": openai_url})
            assert r["provider_used"] == "openai", f"kein Fallback ausgelöst: {r['provider_used']}"
            assert r["fallback"] is True, "fallback-Flag nicht gesetzt"
            assert r["dim"] == 1536 and r["model"] == "text-embedding-3-small", r
            record("F167", "pass", ev("F167", "openai-fallback.json",
                   {"provider_used": r["provider_used"], "fallback": r["fallback"],
                    "dim": r["dim"], "model": r["model"]}),
                   "lokales nomic nicht erreichbar -> Fallback auf OpenAI text-embedding-3-small (1536-dim)")
        except Exception as e:
            record("F167", "fail", note=str(e))

        c.close()

    nomic_srv.shutdown(); openai_srv.shutdown()
    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
