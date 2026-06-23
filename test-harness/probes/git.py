#!/usr/bin/env python3
"""Echte Verifikation der Git-Features (F072–F075).

Jeder Check führt eine reale Operation gegen den echten Rust-Core und/oder ein
echtes git-Repo unter test-harness/projects/ aus und schreibt Evidence nach
test-harness/evidence/<FID>/. Kein Mock, keine erfundenen Ergebnisse.

Aufruf:  python3 test-harness/probes/git.py
Ausgabe: JSON-Zeile  {"results": {fid: {"status": "...", "evidence": "...", "note": "..."}}}

Mapping Feature -> echte Core-IPC-Methode:
  F072  Git-Status-Panel       git.status + git.branch + git.log  (gegen Terminal verglichen)
  F073  Diff-View (staged)     git.diff {staged:true}             (gegen `git diff --staged`)
  F074  Commit-Assistent       KEINE IPC-Methode im Router  -> blocked
  F075  Secret-Scanner         im gesamten Code nicht vorhanden -> blocked
"""
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
PROJECTS = ROOT / "test-harness" / "projects"
results: dict[str, dict] = {}

# Deterministische Identität für die Wegwerf-Repos, damit Commits reproduzierbar sind.
GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Probe Bot",
    "GIT_AUTHOR_EMAIL": "probe@claudestudio.test",
    "GIT_COMMITTER_NAME": "Probe Bot",
    "GIT_COMMITTER_EMAIL": "probe@claudestudio.test",
}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def git(repo: Path, *args: str, check: bool = True) -> str:
    """Führt `git <args>` im Repo aus und gibt stdout (getrimmt) zurück."""
    out = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, env=GIT_ENV,
    )
    if check and out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout


def fresh_repo(name: str) -> Path:
    """Legt ein frisches echtes git-Repo unter test-harness/projects/<name> an."""
    import shutil
    repo = PROJECTS / name
    if repo.exists():
        shutil.rmtree(repo)
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    return repo


def main():
    PROJECTS.mkdir(parents=True, exist_ok=True)

    # ===================================================================
    # Repo-Fixtures vorbereiten (echte git-Repos, echte Commits)
    # ===================================================================
    # --- invoice-app: ein Commit als Basis, dann staged + unstaged Änderung
    inv = fresh_repo("invoice-app")
    (inv / "invoice.ts").write_text(
        "export interface Invoice {\n  id: string;\n  total: number;\n}\n"
    )
    (inv / "README.md").write_text("# invoice-app\n")
    git(inv, "add", "-A")
    git(inv, "commit", "-q", "-m", "chore: initial invoice-app skeleton")
    base_commit = git(inv, "rev-parse", "HEAD").strip()
    base_branch = git(inv, "rev-parse", "--abbrev-ref", "HEAD").strip()

    # invoice.ts ändern + stagen (staged), README.md ändern und NICHT stagen (unstaged)
    (inv / "invoice.ts").write_text(
        "export interface Invoice {\n  id: string;\n  total: number;\n"
        "  currency: string;\n}\n\n"
        "export function formatTotal(i: Invoice): string {\n"
        "  return `${i.total} ${i.currency}`;\n}\n"
    )
    git(inv, "add", "invoice.ts")
    (inv / "README.md").write_text("# invoice-app\n\nStripe invoice service.\n")
    # README.md bleibt unstaged

    # ===================================================================
    # Ein laufender Core für alle IPC-Checks
    # ===================================================================
    log = ROOT / "test-harness/evidence/_git-core.log"
    with P.running_core(library_dir=ROOT, log_path=log) as ctx:
        c = P.Client(ctx["sock"])

        # ---------------------------------------------------------------
        # F072: Git-Status-Panel — staged, unstaged, Branch, letzter Commit
        # Gegen Terminal `git status --porcelain`, `git rev-parse`, `git log -1`.
        # ---------------------------------------------------------------
        try:
            st = c.request("git.status", {"cwd": str(inv)})
            br = c.request("git.branch", {"cwd": str(inv)})
            lg = c.request("git.log", {"cwd": str(inv), "limit": 1})
            entries = st.get("entries", [])
            by_path = {e["path"]: e for e in entries}

            # Terminal-Wahrheit
            term_status = git(inv, "status", "--porcelain")
            term_branch = git(inv, "rev-parse", "--abbrev-ref", "HEAD").strip()
            term_head = git(inv, "rev-parse", "HEAD").strip()
            term_subject = git(inv, "log", "-1", "--format=%s").strip()

            # invoice.ts ist gestaged: X-Spalte (raw[0]) != ' '
            inv_e = by_path.get("invoice.ts")
            assert inv_e is not None, f"invoice.ts fehlt im Status: {entries}"
            assert inv_e["raw"][0] != " ", f"invoice.ts nicht als staged erkannt: raw={inv_e['raw']!r}"

            # README.md ist NICHT gestaged: Y-Spalte (raw[1]) != ' ', X-Spalte == ' '
            rd_e = by_path.get("README.md")
            assert rd_e is not None, f"README.md fehlt im Status: {entries}"
            assert rd_e["raw"][0] == " " and rd_e["raw"][1] != " ", \
                f"README.md nicht als unstaged erkannt: raw={rd_e['raw']!r}"

            # Branch + letzter Commit gegen Terminal
            assert br.get("branch") == term_branch == base_branch, \
                f"Branch mismatch: core={br.get('branch')} term={term_branch}"
            commits = lg.get("commits", [])
            assert commits, "git.log lieferte keine Commits"
            core_head = commits[0]["hash"]
            assert core_head == term_head == base_commit, \
                f"HEAD-Hash mismatch: core={core_head} term={term_head}"
            assert commits[0]["subject"] == term_subject, \
                f"Subject mismatch: core={commits[0]['subject']!r} term={term_subject!r}"

            content = {
                "core_git.status.entries": entries,
                "core_git.branch": br,
                "core_git.log[0]": commits[0],
                "terminal_git_status_porcelain": term_status,
                "terminal_branch": term_branch,
                "terminal_HEAD": term_head,
                "terminal_log-1_subject": term_subject,
                "assertions": {
                    "invoice.ts_staged": True,
                    "README.md_unstaged": True,
                    "branch_matches_terminal": True,
                    "head_hash_matches_terminal": True,
                    "subject_matches_terminal": True,
                },
            }
            e = ev("F072", "status-panel.json",
                   json.dumps(content, indent=2, ensure_ascii=False))
            record("F072", "pass", e,
                   f"staged=invoice.ts unstaged=README.md branch={term_branch} head={term_head[:8]}")
        except Exception as e:
            record("F072", "fail", note=f"{type(e).__name__}: {e}")

        # ---------------------------------------------------------------
        # F073: Diff-View vor dem Commit — echter Diff der gestageten Änderung
        # git.diff {staged:true} muss exakt `git diff --staged` entsprechen.
        # ---------------------------------------------------------------
        try:
            core_diff = c.request("git.diff", {"cwd": str(inv), "staged": True})
            core_text = core_diff.get("diff", "")
            term_text = git(inv, "diff", "--staged")

            # Exakter Vergleich des Diff-Texts
            assert core_text == term_text, "Diff-Text weicht von `git diff --staged` ab"
            # Außerdem muss der Diff nur invoice.ts betreffen (README ist unstaged)
            assert "invoice.ts" in core_text, "invoice.ts nicht im staged Diff"
            assert "README.md" not in core_text, "README.md unerwartet im staged Diff (sollte unstaged sein)"

            # Hinzugefügte / entfernte Zeilen aus beiden Quellen zählen und vergleichen
            def diff_lines(text):
                added = [l for l in text.splitlines()
                         if l.startswith("+") and not l.startswith("+++")]
                removed = [l for l in text.splitlines()
                           if l.startswith("-") and not l.startswith("---")]
                return added, removed
            c_add, c_rem = diff_lines(core_text)
            t_add, t_rem = diff_lines(term_text)
            assert (c_add, c_rem) == (t_add, t_rem), "added/removed-Zeilen ungleich"
            assert c_add, "es wurden keine hinzugefügten Zeilen erkannt"

            content = {
                "request": {"method": "git.diff", "payload": {"cwd": str(inv), "staged": True}},
                "core_response_staged_flag": core_diff.get("staged"),
                "core_diff_equals_terminal": True,
                "added_lines": c_add,
                "removed_lines": c_rem,
                "core_diff": core_text,
                "terminal_git_diff_staged": term_text,
            }
            e = ev("F073", "staged-diff.json",
                   json.dumps(content, indent=2, ensure_ascii=False))
            record("F073", "pass", e,
                   f"git.diff staged == `git diff --staged`, +{len(c_add)}/-{len(c_rem)} Zeilen")
        except Exception as e:
            record("F073", "fail", note=f"{type(e).__name__}: {e}")

        # ---------------------------------------------------------------
        # F074: Commit-Assistent — generiert Conventional-Commit + echter Commit
        #
        # Befund: Der Core stellt KEINE IPC-Methode dafür bereit. `commit` und
        # `generate_conventional_commit_message` existieren im cs-git-Crate, sind
        # aber NICHT im Router (core/crates/cs-cli/src/router.rs) verdrahtet — die
        # vollständige Methodenliste enthält nur git.status/branch/worktrees/diff/log.
        # Über den laufenden Core ist das Feature damit nicht ausführbar.
        # Wir beweisen das Fehlen empirisch durch einen Methodenaufruf.
        # ---------------------------------------------------------------
        try:
            probe_methods = ["git.commit", "git.commit_assistant", "git.generate_commit_message"]
            outcomes = {}
            for m in probe_methods:
                try:
                    r = c.request(m, {"cwd": str(inv)})
                    outcomes[m] = {"reached": True, "response": r}
                except P.RemoteError as re:
                    outcomes[m] = {"reached": False, "code": re.code, "message": re.message}
            # "unknown method" o.ä. -> keine der Methoden existiert
            none_reached = all(not o.get("reached") for o in outcomes.values())
            assert none_reached, f"unerwartet erreichbare Commit-Methode: {outcomes}"
            content = {
                "finding": "Kein git.commit / Commit-Assistent im IPC-Router verdrahtet.",
                "router_method_list": [
                    "git.status", "git.branch", "git.worktrees", "git.diff", "git.log",
                ],
                "code_note": (
                    "SystemGit::commit und generate_conventional_commit_message existieren in "
                    "core/crates/cs-git/src/lib.rs, werden aber von keinem Router-Handler aufgerufen."
                ),
                "probe_outcomes": outcomes,
            }
            e = ev("F074", "commit-assistant-blocked.json",
                   json.dumps(content, indent=2, ensure_ascii=False))
            record("F074", "blocked", e,
                   "Keine IPC-Methode für Commit-Assistent im Core-Router; Feature headless nicht ausführbar.")
        except Exception as e:
            # Falls eine Methode doch existiert, war meine Annahme falsch -> fail mit Beleg
            record("F074", "fail", note=f"unerwartet: {type(e).__name__}: {e}")

        # ---------------------------------------------------------------
        # F075: Secret-Scanner über die Git-History
        #
        # Befund: Im gesamten Code (Rust-Core wie Swift-App) existiert KEIN
        # Secret-Scanner. Der einzige Treffer für "Secret-Scanner" ist die
        # Spezifikation in feature_list.json selbst — keine Implementierung,
        # keine IPC-Methode, keine Erkennungslogik (kein AKIA/Entropie/Regex-Scan).
        # Wir committen das echte Secret ins echte Repo (damit die History real
        # existiert) und belegen, dass der Core keine Scan-Methode anbietet.
        # ---------------------------------------------------------------
        try:
            dp = fresh_repo("data-pipeline")
            secret_line = "AWS_SECRET_ACCESS_KEY=AKIAEXAMPLEKEY1234567"
            (dp / "config.env").write_text(secret_line + "\n")
            git(dp, "add", "-A")
            git(dp, "commit", "-q", "-m", "add pipeline config")
            secret_commit = git(dp, "rev-parse", "HEAD").strip()
            # History enthält das Secret wirklich:
            history_grep = git(dp, "log", "-p", "-S", "AKIAEXAMPLEKEY1234567",
                               "--format=%H").strip()
            assert secret_commit in history_grep, "Secret nicht in der echten History auffindbar"

            probe_methods = [
                "secrets.scan", "secret.scan", "git.secret_scan", "git.scan_secrets",
                "security.scan_secrets", "scan.secrets",
            ]
            outcomes = {}
            for m in probe_methods:
                try:
                    r = c.request(m, {"cwd": str(dp)})
                    outcomes[m] = {"reached": True, "response": r}
                except P.RemoteError as re:
                    outcomes[m] = {"reached": False, "code": re.code, "message": re.message}
            none_reached = all(not o.get("reached") for o in outcomes.values())
            assert none_reached, f"unerwartet erreichbare Scan-Methode: {outcomes}"

            content = {
                "finding": "Kein Secret-Scanner im Code vorhanden (weder Core noch App).",
                "evidence_of_absence": (
                    "grep -rniE 'secret.?scan|AKIA|entropy|gitleaks|trufflehog' über *.rs/*.swift "
                    "liefert nur die Spezifikation in feature_list.json, keinen Code."
                ),
                "real_repo": str(dp.relative_to(ROOT)),
                "committed_secret": secret_line,
                "secret_commit_hash": secret_commit,
                "secret_present_in_git_history": True,
                "scan_probe_outcomes": outcomes,
            }
            e = ev("F075", "secret-scan-blocked.json",
                   json.dumps(content, indent=2, ensure_ascii=False))
            record("F075", "blocked", e,
                   "Secret-Scanner ist nicht implementiert (keine Scan-Logik/IPC); Secret real in History, aber nichts scannt es.")
        except Exception as e:
            record("F075", "fail", note=f"{type(e).__name__}: {e}")

        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
