#!/usr/bin/env python3
"""Verifikation der in Build-Batch 1 implementierten Features gegen den echten Core.

Neu im Core verdrahtet (core/crates/cs-cli/src/router.rs + cs-git + cs-sessions):
  file.create/rename/move/delete/duplicate/list/search/attach,
  git.commit (+ commit_message), worktree.add/remove/merge, FTS5-Query-Sanitizer.

Jeder Check führt eine ECHTE Operation gegen den laufenden Core aus und vergleicht
gegen das echte Dateisystem / echte git-Ausgabe. Kein Mock.
"""
from __future__ import annotations
import json, os, sqlite3, subprocess, sys, tempfile, shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
results: dict[str, dict] = {}
GENV = {**os.environ, "GIT_AUTHOR_NAME": "Batch Bot", "GIT_AUTHOR_EMAIL": "b@cs.test",
        "GIT_COMMITTER_NAME": "Batch Bot", "GIT_COMMITTER_EMAIL": "b@cs.test"}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, ensure_ascii=False)
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def git(repo, *a, check=True):
    r = subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, env=GENV)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(a)}: {r.stderr.strip()}")
    return r.stdout.strip()


def fresh_repo(name):
    repo = Path(tempfile.mkdtemp(prefix=f"cs-b1-{name}-"))
    git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("hello\n")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "init")
    return repo


def main():
    with P.running_core(library_dir=ROOT, log_path=Path("/tmp/batch1.log")) as ctx:
        c = P.Client(ctx["sock"])
        d = Path(tempfile.mkdtemp(prefix="cs-b1-files-"))

        # ---- F047 create ----
        try:
            r = c.request("file.create", {"path": str(d / "new.txt"), "content": "x"})
            assert r["created"] and (d / "new.txt").read_text() == "x"
            record("F047", "pass", ev("F047", "create.json", r), "Datei real angelegt")
        except Exception as e:
            record("F047", "fail", note=str(e))

        # ---- F048 rename ----
        try:
            r = c.request("file.rename", {"from": str(d / "new.txt"), "to": str(d / "ren.txt")})
            assert (d / "ren.txt").exists() and not (d / "new.txt").exists()
            record("F048", "pass", ev("F048", "rename.json", r), "Datei real umbenannt")
        except Exception as e:
            record("F048", "fail", note=str(e))

        # ---- F049 move ----
        try:
            (d / "sub").mkdir(exist_ok=True)
            r = c.request("file.move", {"from": str(d / "ren.txt"), "to": str(d / "sub" / "ren.txt")})
            assert (d / "sub" / "ren.txt").exists() and not (d / "ren.txt").exists()
            record("F049", "pass", ev("F049", "move.json", r), "Datei real in Unterordner verschoben (D&D-Trigger ist UI)")
        except Exception as e:
            record("F049", "fail", note=str(e))

        # ---- F051 duplicate ----
        try:
            r = c.request("file.duplicate", {"from": str(d / "sub" / "ren.txt")})
            assert Path(r["to"]).exists() and "copy" in Path(r["to"]).name
            record("F051", "pass", ev("F051", "duplicate.json", r), "echte Kopie mit Suffix")
        except Exception as e:
            record("F051", "fail", note=str(e))

        # ---- F050 delete ----
        try:
            target = str(d / "sub" / "ren.txt")
            r = c.request("file.delete", {"path": target})
            assert not Path(target).exists()
            record("F050", "pass", ev("F050", "delete.json", r), "Datei real entfernt")
        except Exception as e:
            record("F050", "fail", note=str(e))

        # ---- F046 recursive tree via file.list ----
        try:
            (d / "tree/a/b").mkdir(parents=True, exist_ok=True)
            (d / "tree/a/b/leaf.txt").write_text("leaf")
            (d / "tree/top.txt").write_text("top")

            def walk(path):
                r = c.request("file.list", {"path": path})
                node = {"path": path, "children": []}
                for e in r["entries"]:
                    node["children"].append(walk(e["path"]) if e["is_dir"] else {"file": e["name"]})
                return node
            tree = walk(str(d / "tree"))
            # assert the nested leaf is reachable through recursive listing
            flat = json.dumps(tree)
            assert "leaf.txt" in flat and "top.txt" in flat
            record("F046", "pass", ev("F046", "tree.json", tree), "rekursiver Baum über file.list aufgebaut")
        except Exception as e:
            record("F046", "fail", note=str(e))

        # ---- F053 protected paths blocked on attach ----
        try:
            (d / ".env").write_text("SECRET=abc123")
            (d / "ok.txt").write_text("safe")
            blocked = False
            try:
                c.request("file.attach", {"path": str(d / ".env")})
            except P.RemoteError as re:
                blocked = "protected" in re.message.lower()
            ok = c.request("file.attach", {"path": str(d / "ok.txt")})
            assert blocked, ".env was NOT blocked"
            assert ok["content"] == "safe"
            record("F053", "pass", ev("F053", "protected.json",
                   {".env_attach": "REFUSED (protected)", "ok.txt_attach": ok}),
                   ".env blockiert, normale Datei erlaubt")
        except Exception as e:
            record("F053", "fail", note=str(e))

        # ---- F061 ripgrep content search ----
        try:
            repo = fresh_repo("search")
            (repo / "code.txt").write_text("line one\nthe needle is here\nline three\n")
            r = c.request("file.search", {"cwd": str(repo), "query": "needle"})
            hit = next((m for m in r["matches"] if "needle" in m["text"]), None)
            assert hit and hit["line"] == 2
            record("F061", "pass", ev("F061", "search.json", r), f"Treffer mit Datei+Zeile (tool={r['tool']})")
            shutil.rmtree(repo, ignore_errors=True)
        except Exception as e:
            record("F061", "fail", note=str(e))

        # ---- F074 commit assistant ----
        try:
            repo = fresh_repo("commit")
            (repo / "b.txt").write_text("new feature line\n")
            git(repo, "add", "-A")
            msg = c.request("git.commit_message", {"cwd": str(repo)})["message"]
            r = c.request("git.commit", {"cwd": str(repo)})
            head = git(repo, "rev-parse", "HEAD")
            real_subject = git(repo, "log", "-1", "--pretty=%s")
            assert r["hash"] == head and r["message"] == real_subject and msg
            record("F074", "pass", ev("F074", "commit.json",
                   {"generated_message": msg, "commit": r, "head": head, "subject": real_subject}),
                   f"Conventional-Commit '{r['message']}' erzeugt + committet")
            shutil.rmtree(repo, ignore_errors=True)
        except Exception as e:
            record("F074", "fail", note=str(e))

        # ---- F065/F066/F069/F071 worktrees ----
        try:
            repo = fresh_repo("wt")
            wt = str(repo) + "-wtA"
            r_add = c.request("worktree.add", {"cwd": str(repo), "path": wt, "branch": "feature/a"})
            wts = git(repo, "worktree", "list", "--porcelain")
            assert wt in wts and Path(wt).exists()
            record("F065", "pass", ev("F065", "add.json", {"ipc": r_add, "git_worktree_list": wts}),
                   "git worktree add real ausgeführt")
            # commit on the worktree branch, then merge
            (Path(wt) / "c.txt").write_text("c\n")
            git(wt, "add", "-A"); git(wt, "commit", "-qm", "feat: c")
            r_merge = c.request("worktree.merge", {"cwd": str(repo), "branch": "feature/a"})
            assert (repo / "c.txt").exists(), "merge did not bring c.txt into main"
            record("F069", "pass", ev("F069", "merge.json", r_merge), "git merge des Worktree-Branch real")
            r_rm = c.request("worktree.remove", {"cwd": str(repo), "path": wt})
            assert wt not in git(repo, "worktree", "list", "--porcelain")
            record("F066", "pass", ev("F066", "remove.json", r_rm), "git worktree remove real")
        except Exception as e:
            for fid in ["F065", "F066", "F069"]:
                if fid not in results:
                    record(fid, "fail", note=str(e))

        # ---- F071 two parallel worktrees both merged, no conflict ----
        try:
            repo = fresh_repo("wt2")
            wta, wtb = str(repo) + "-A", str(repo) + "-B"
            c.request("worktree.add", {"cwd": str(repo), "path": wta, "branch": "feat/a"})
            c.request("worktree.add", {"cwd": str(repo), "path": wtb, "branch": "feat/b"})
            (Path(wta) / "fa.txt").write_text("a\n"); git(wta, "add", "-A"); git(wta, "commit", "-qm", "feat: a")
            (Path(wtb) / "fb.txt").write_text("b\n"); git(wtb, "add", "-A"); git(wtb, "commit", "-qm", "feat: b")
            c.request("worktree.merge", {"cwd": str(repo), "branch": "feat/a"})
            c.request("worktree.merge", {"cwd": str(repo), "branch": "feat/b"})
            assert (repo / "fa.txt").exists() and (repo / "fb.txt").exists()
            assert git(repo, "status", "--porcelain") == ""  # clean, no conflict
            log = git(repo, "log", "--oneline")
            record("F071", "pass", ev("F071", "two-worktrees.json",
                   {"both_files_present": True, "clean": True, "log": log}),
                   "zwei parallele Worktrees konfliktfrei nach main gemerged")
        except Exception as e:
            record("F071", "fail", note=str(e))

        # ---- F159 FTS multi-word query no longer crashes + matches ----
        try:
            # create a REAL session so the FTS JOIN to sessions resolves
            sid = c.request("session.create", {"title": "FTS", "cwd": str(ROOT)})["id"]
            db = ctx["home"] / ".claudestudio/sessions.db"
            con = sqlite3.connect(str(db))
            con.execute("INSERT INTO transcript_fts (session_id, source, body) VALUES (?,?,?)",
                        (sid, "message", "please check the Healthcheck-Endpoint latency now"))
            con.commit(); con.close()
            # previously this threw [452] sqlite error: no such column: Endpoint
            r = c.request("session.search", {"query": "Healthcheck-Endpoint"})
            hit = next((h for h in r["hits"] if h.get("session_id") == sid), None)
            assert hit is not None, f"multi-word query returned no hit: {r}"
            record("F159", "pass", ev("F159", "fts-multiword.json",
                   {"query": "Healthcheck-Endpoint", "hit_session": sid, "result": r}),
                   "Mehrwort/Bindestrich-Query liefert Treffer ohne SQLite-Crash")
        except Exception as e:
            record("F159", "fail", note=str(e))

        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
