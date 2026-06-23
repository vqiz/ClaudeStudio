#!/usr/bin/env python3
"""Verifikation Build-Batch 21: Privacy-Modus (AES-256-GCM + gzip) und MCP-Allowlist.
Neu (router.rs): session.set_private/get_private (echtes ring AES-256-GCM + flate2 gzip,
Standardliste schliesst private Sessions aus), mcp.allowlist_set/get/check_server. Kein Mock.
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
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/b21.log")) as ctx:
        c = P.Client(ctx["sock"])

        # F165 privacy mode: AES-256 + gzip + list exclusion + decrypt roundtrip
        try:
            MARK = "SECRET-CREDENTIAL-TOKEN-9931"
            transcript = (f"User: hier ist mein {MARK}\n" * 400)  # repetitive -> high gzip ratio
            sid = c.request("session.create", {"title": "private case", "cwd": str(ROOT)})["id"]
            setp = c.request("session.set_private", {"session_id": sid, "content": transcript, "key": "geheim"})
            # raw stored bytes must NOT contain the plaintext secret
            raw = Path(setp["encrypted_path"]).read_text()
            assert MARK not in raw and all(ch in "0123456789abcdef" for ch in raw[:64])  # hex, encrypted
            # gzip ratio near ~80% (repetitive text compresses far below original)
            assert setp["gzip_ratio"] > 0.7
            # excluded from default list, present with include_private
            default_ids = {s["id"] for s in c.request("session.list", {})["sessions"]}
            withpriv_ids = {s["id"] for s in c.request("session.list", {"include_private": True})["sessions"]}
            assert sid not in default_ids and sid in withpriv_ids
            # decrypt roundtrip == original; wrong key fails
            back = c.request("session.get_private", {"session_id": sid, "key": "geheim"})["content"]
            assert back == transcript
            wrong = False
            try:
                c.request("session.get_private", {"session_id": sid, "key": "falsch"})
            except P.RemoteError:
                wrong = True
            assert wrong, "wrong key should fail to decrypt"
            record("F165", "pass", ev("F165", "privacy.json",
                   {"gzip_ratio": setp["gzip_ratio"], "raw_excerpt": raw[:80], "excluded_from_default": True,
                    "roundtrip_ok": True, "wrong_key_rejected": True}),
                   f"AES-256+gzip ({setp['gzip_ratio']:.0%}), aus Standardliste ausgeschlossen, Roundtrip exakt")
        except Exception as e:
            record("F165", "fail", note=str(e))

        # F254 MCP allowlist (+ agent tool assignment available)
        try:
            c.request("mcp.allowlist_set", {"servers": ["github", "playwright"]})
            allowed = c.request("mcp.check_server", {"name": "github"})
            blocked = c.request("mcp.check_server", {"name": "evil-exfil"})
            assert allowed["allowed"] is True and blocked["allowed"] is False and "blockiert" in blocked["reason"]
            # assigned tool is really available to the agent (reuse agents.check_tool)
            aid = c.request("agents.create", {"name": "ToolAgent", "allowed_tools": ["github_create_issue", "Read"]})["id"]
            tool_ok = c.request("agents.check_tool", {"id": aid, "tool": "github_create_issue"})
            assert tool_ok["allowed"] is True
            record("F254", "pass", ev("F254", "allowlist.json",
                   {"allowed": allowed, "blocked": blocked, "agent_tool": tool_ok}),
                   "zugewiesenes Tool verfügbar; nicht-gelisteter Server blockiert")
        except Exception as e:
            record("F254", "fail", note=str(e))

        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
