#!/usr/bin/env python3
"""Echte Verifikation der Worktree-Features (F064–F071).

Jeder Check führt — soweit headless möglich — eine reale Operation gegen den
echten Core und/oder ein echtes git-Repo aus und schreibt Evidence nach
test-harness/evidence/<FID>/. Kein Mock.

Wichtige Architektur-Erkenntnis (im Core verifiziert, siehe
core/crates/cs-cli/src/router.rs handle/handle_blocking):
  - Der Core exponiert für Worktrees GENAU EINE IPC-Methode: `git.worktrees`
    (Liste, backed by cs_git::SystemGit::list_worktrees).
  - Es gibt KEINE IPC-Methode für worktree-add, worktree-remove, git-merge oder
    ein Max-Parallel-Limit. Die cs-git-Crate besitzt zwar create_worktree /
    remove_worktree, diese sind aber NICHT an die IPC-Schicht angeschlossen.
  - F067 (Worktree-CLAUDE.md überschreibt Session-Kontext) braucht einen echten
    laufenden Claude-Agenten; der WorktreeOverride-Layer im ContextAssembler
    arbeitet headless nur mit festen Token-Schätzungen, nicht mit echtem
    CLAUDE.md-Inhalt im Session-Kontext.

Daraus folgt ehrlich:
  - F064  -> echt über `git.worktrees` verifizierbar (PASS bei Erfolg).
  - F065/F066/F069/F071 -> "Button/Assistent führt echtes git aus": KEIN
    IPC-Pfad im Core. Reines GUI-Feature ohne headless-prüfbaren Core-Pfad
    -> blocked. (Die zugrundeliegende git-Mechanik wird, wo sinnvoll, als
    Supporting-Evidence real ausgeführt, aber das Feature selbst bleibt blocked.)
  - F067 -> braucht echten Claude-Agenten + fehlender Kontext-IPC -> blocked.
  - F068 -> UI-Statusfarben, nur per GUI-Screenshot prüfbar -> blocked.
  - F070 -> Max-Parallel-Limit: weder Config noch IPC im Core -> blocked.

Aufruf:  python3 test-harness/probes/worktree.py
Ausgabe: JSON-Zeile  {"results": {fid: {"status": "...", "evidence": "...", "note": "..."}}}
"""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

ROOT = P.ROOT
results: dict[str, dict] = {}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def git(repo: Path, *args: str) -> str:
    """Führt git im Repo aus und liefert stdout (wirft bei Fehler)."""
    out = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout


def make_repo(base: Path) -> Path:
    """Legt ein echtes git-Repo mit einem Initial-Commit unter base/todo-api an."""
    repo = base / "todo-api"
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "probe@claudestudio.test")
    git(repo, "config", "user.name", "Worktree Probe")
    (repo / "README.md").write_text("# todo-api\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "chore: initial commit")
    return repo


def main():
    base = Path(tempfile.mkdtemp(prefix="cs-worktree-probe-"))
    try:
        repo = make_repo(base)

        # Echten Worktree feature/x anlegen (entspricht dem real_world_test-Setup
        # aus F064: `git worktree add ../todo-api-wt feature/x`).
        wt_x = base / "todo-api-wt"
        git(repo, "worktree", "add", "-b", "feature/x", str(wt_x))

        # ---- F064: Worktree-Liste über die echte IPC-Methode `git.worktrees` ----
        log = ROOT / "test-harness/evidence/_worktree-core.log"
        with P.running_core(library_dir=ROOT, log_path=log) as ctx:
            c = P.Client(ctx["sock"])
            try:
                # Ground truth direkt von git.
                cli_porcelain = git(repo, "worktree", "list", "--porcelain")
                cli_human = git(repo, "worktree", "list")

                # Echter IPC-Call gegen den laufenden Core.
                res = c.request("git.worktrees", {"cwd": str(repo)})
                wts = res.get("worktrees", [])

                # Branches/Pfad aus der IPC-Antwort sammeln.
                ipc_branches = {w.get("branch") for w in wts}
                ipc_paths = {os.path.realpath(w.get("path", "")) for w in wts}

                # git's Wahrheit über die Branches.
                cli_branches = set()
                cli_paths = set()
                for blk in cli_porcelain.split("\n\n"):
                    for line in blk.splitlines():
                        if line.startswith("worktree "):
                            cli_paths.add(os.path.realpath(line[len("worktree "):].strip()))
                        if line.startswith("branch "):
                            short = line[len("branch "):].strip().rsplit("/", 1)[-1]
                            cli_branches.add(short)

                # Assertions: der neue Worktree feature/x muss mit korrektem Branch
                # UND korrektem Pfad in der IPC-Liste auftauchen, und IPC == git.
                assert "x" in ipc_branches, (
                    f"feature/x (kurz 'x') fehlt in IPC-Branches {ipc_branches}")
                assert os.path.realpath(str(wt_x)) in ipc_paths, (
                    f"Worktree-Pfad {wt_x} fehlt in IPC-Pfaden {ipc_paths}")
                assert ipc_branches == cli_branches, (
                    f"IPC-Branches {ipc_branches} != git-Branches {cli_branches}")
                assert ipc_paths == cli_paths, (
                    f"IPC-Pfade {ipc_paths} != git-Pfade {cli_paths}")

                e = ev("F064", "worktrees.json", json.dumps({
                    "request": {"method": "git.worktrees", "payload": {"cwd": str(repo)}},
                    "ipc_response": res,
                    "git_worktree_list_porcelain": cli_porcelain,
                    "git_worktree_list_human": cli_human,
                    "ipc_branches": sorted(b for b in ipc_branches if b),
                    "git_branches": sorted(cli_branches),
                    "paths_match": True,
                }, indent=2, ensure_ascii=False))
                record("F064", "pass", e,
                       f"git.worktrees == git worktree list ({len(wts)} Einträge, feature/x present)")
            except Exception as e:
                record("F064", "fail", note=str(e))

            # ---- F067: Worktree-CLAUDE.md überschreibt Session-Kontext --------
            # Setup-Evidence sammeln (Marker-Regel real anlegen), aber ehrlich
            # blockieren: es gibt KEINE IPC-Methode, die ein Worktree-CLAUDE.md in
            # den Session-Kontext eines echten Claude-Agenten assembliert. Der
            # WorktreeOverride-Layer in context.budget nutzt nur Token-Schätzungen.
            try:
                (repo / "CLAUDE.md").write_text("# Root\nUse 2 spaces.\n")
                marker = "MARKER_WT_ONLY_RULE_4711: always answer with the codeword PINEAPPLE."
                (wt_x / "CLAUDE.md").write_text(f"# Worktree feature/x\n{marker}\n")
                budget = c.request("context.budget", {})
                layer_labels = [l.get("layer") for l in budget.get("layers", [])]
                has_wt_layer = "worktree_override" in layer_labels
                e = ev("F067", "blocked.json", json.dumps({
                    "reason": ("Kein IPC-Pfad assembliert ein Worktree-CLAUDE.md in den "
                               "Session-Kontext eines echten Claude-Agenten. context.budget "
                               "liefert nur Token-Schätzungen pro Layer, nicht den realen "
                               "CLAUDE.md-Inhalt. Verifikation braucht einen laufenden Claude-"
                               "Agenten (headless nicht verfügbar)."),
                    "worktree_claude_md": str(wt_x / "CLAUDE.md"),
                    "marker_rule": marker,
                    "context_budget_layers": layer_labels,
                    "worktree_override_layer_present_but_content_free": has_wt_layer,
                }, indent=2, ensure_ascii=False))
                record("F067", "blocked", e,
                       "kein Kontext-IPC für Worktree-CLAUDE.md; braucht echten Claude-Agenten")
            except Exception as e:
                record("F067", "blocked", note=f"setup/blocked: {e}")

            c.close()

        # ---- F065: Worktree erstellen per Button -> echtes git worktree add ----
        # Im Core ist KEINE IPC-Methode dafür angeschlossen (router.rs exponiert nur
        # git.worktrees=list). Das Feature ist ein GUI-Button ohne headless-prüfbaren
        # Core-Pfad. Supporting-Evidence: die zugrundeliegende git-Mechanik wird real
        # ausgeführt, das Feature selbst bleibt aber blocked.
        try:
            wt_login = base / "todo-api-login"
            git(repo, "worktree", "add", "-b", "feature/login", str(wt_login))
            listing = git(repo, "worktree", "list")
            in_list = "feature/login" in listing
            dir_exists = wt_login.is_dir()
            e = ev("F065", "blocked.json", json.dumps({
                "reason": ("Keine IPC-Methode im Core für worktree-add (router.rs handle/"
                           "handle_blocking exponiert nur git.worktrees=list). Der 'Neuer "
                           "Worktree'-Button hat keinen headless-prüfbaren Core-Pfad; "
                           "Verifikation erfordert GUI-Klick + Screenshot."),
                "supporting_evidence_real_git_worktree_add": {
                    "cmd": f"git -C {repo} worktree add -b feature/login {wt_login}",
                    "git_worktree_list": listing,
                    "feature_login_in_list": in_list,
                    "directory_physically_exists": dir_exists,
                },
            }, indent=2, ensure_ascii=False))
            record("F065", "blocked", e,
                   "kein worktree-add IPC im Core; GUI-Button, nur per Screenshot prüfbar")
        except Exception as e:
            record("F065", "blocked", note=f"kein IPC; setup: {e}")

        # ---- F066: Worktree löschen per Button -> echtes git worktree remove ----
        try:
            wt_login = base / "todo-api-login"
            before = git(repo, "worktree", "list")
            git(repo, "worktree", "remove", str(wt_login))
            after = git(repo, "worktree", "list")
            removed = ("feature/login" in before) and ("feature/login" not in after) and (not wt_login.exists())
            e = ev("F066", "blocked.json", json.dumps({
                "reason": ("Keine IPC-Methode im Core für worktree-remove. Der 'Löschen'-"
                           "Button hat keinen headless-prüfbaren Core-Pfad; Verifikation "
                           "erfordert GUI-Klick + Bestätigungsdialog + Screenshot."),
                "supporting_evidence_real_git_worktree_remove": {
                    "git_worktree_list_before": before,
                    "git_worktree_list_after": after,
                    "removed_and_dir_gone": removed,
                },
            }, indent=2, ensure_ascii=False))
            record("F066", "blocked", e,
                   "kein worktree-remove IPC im Core; GUI-Button, nur per Screenshot prüfbar")
        except Exception as e:
            record("F066", "blocked", note=f"kein IPC; setup: {e}")

        # ---- F068: Status-Farben der Worktrees ---------------------------------
        try:
            e = ev("F068", "blocked.json", json.dumps({
                "reason": ("Statusfarben (grün/gelb/weiß/rot) sind reine SwiftUI-Darstellung "
                           "im Worktree-Panel. Headless nicht prüfbar — erfordert GUI-"
                           "Screenshot mit drei laufenden Zuständen (arbeitend/idle/Fehler)."),
            }, indent=2, ensure_ascii=False))
            record("F068", "blocked", e, "UI-Statusfarben, nur per GUI-Screenshot prüfbar")
        except Exception as e:
            record("F068", "blocked", note=str(e))

        # ---- F069: Merge-Assistent merged Worktree-Branch per echtem git merge -
        # Keine merge-IPC im Core. Supporting-Evidence: realer git-merge der Mechanik.
        try:
            git(wt_x, "config", "user.email", "probe@claudestudio.test")
            git(wt_x, "config", "user.name", "Worktree Probe")
            # Änderung in feature/x committen (im Worktree). Vorher alles staged
            # committen (z.B. das in F067 angelegte Worktree-CLAUDE.md), damit der
            # Supporting-Merge nicht an untracked Files scheitert.
            (wt_x / "feature_x.txt").write_text("change from feature/x\n")
            git(wt_x, "add", "-A")
            git(wt_x, "commit", "-m", "feat: add feature_x file")
            fx_hash = git(wt_x, "rev-parse", "--short", "HEAD").strip()
            # Untracked Root-CLAUDE.md (in F067 angelegt) entfernen, damit der
            # Supporting-Merge nicht an "untracked working tree files would be
            # overwritten" scheitert.
            root_claude = repo / "CLAUDE.md"
            if root_claude.exists() and "CLAUDE.md" in git(repo, "status", "--porcelain"):
                root_claude.unlink()
            # Merge nach main im Haupt-Repo.
            git(repo, "merge", "--no-ff", "feature/x", "-m", "merge feature/x")
            log_main = git(repo, "log", "main", "--oneline")
            merged = fx_hash in log_main or "add feature_x file" in log_main
            e = ev("F069", "blocked.json", json.dumps({
                "reason": ("Keine IPC-Methode im Core für git-merge / Merge-Assistent. Das "
                           "Feature ist ein UI-Assistent ohne headless-prüfbaren Core-Pfad; "
                           "Verifikation erfordert GUI-Bedienung + Screenshot."),
                "supporting_evidence_real_git_merge": {
                    "feature_x_commit": fx_hash,
                    "git_log_main_oneline": log_main,
                    "commit_from_feature_x_in_main": merged,
                },
            }, indent=2, ensure_ascii=False))
            record("F069", "blocked", e,
                   "kein merge IPC im Core; UI-Assistent, nur per Screenshot prüfbar")
        except Exception as e:
            record("F069", "blocked", note=f"kein IPC; setup: {e}")

        # ---- F070: Max-Parallel-Limit (Standard 4) -----------------------------
        # Weder Config-Feld noch IPC/Enforcement im Core gefunden. Reines (noch
        # nicht implementiertes) UI/Policy-Feature -> blocked.
        try:
            e = ev("F070", "blocked.json", json.dumps({
                "reason": ("Kein Max-Parallel-Limit im Core: weder ein Config-Feld "
                           "(config.get liefert kein max_parallel) noch eine IPC-/"
                           "Enforcement-Logik. Es gibt überhaupt keine worktree-add-IPC, "
                           "die ein Limit erzwingen könnte. Verifikation erfordert die "
                           "GUI-Limit-Warnung (Screenshot) — und das Feature ist im Core "
                           "nicht vorhanden."),
            }, indent=2, ensure_ascii=False))
            record("F070", "blocked", e,
                   "kein max_parallel im Core (keine Config/IPC/Enforcement)")
        except Exception as e:
            record("F070", "blocked", note=str(e))

        # ---- F071: Zwei parallele Worktrees konfliktfrei nach main mergen -------
        try:
            wt_a = base / "todo-api-a"
            wt_b = base / "todo-api-b"
            git(repo, "worktree", "add", "-b", "feature/a", str(wt_a))
            git(repo, "worktree", "add", "-b", "feature/b", str(wt_b))
            for wt, fname, branch in ((wt_a, "file_a.txt", "feature/a"),
                                      (wt_b, "file_b.txt", "feature/b")):
                git(wt, "config", "user.email", "probe@claudestudio.test")
                git(wt, "config", "user.name", "Worktree Probe")
                (wt / fname).write_text(f"content from {branch}\n")
                git(wt, "add", "-A")
                git(wt, "commit", "-m", f"feat: add {fname}")
            a_hash = git(wt_a, "rev-parse", "--short", "HEAD").strip()
            b_hash = git(wt_b, "rev-parse", "--short", "HEAD").strip()
            git(repo, "merge", "--no-ff", "feature/a", "-m", "merge feature/a")
            git(repo, "merge", "--no-ff", "feature/b", "-m", "merge feature/b")
            log_main = git(repo, "log", "main", "--oneline")
            status = git(repo, "status", "--porcelain")
            both_in = ("add file_a.txt" in log_main) and ("add file_b.txt" in log_main)
            clean = status.strip() == ""
            e = ev("F071", "blocked.json", json.dumps({
                "reason": ("Keine Merge-IPC im Core (siehe F069). Das Feature 'beide via "
                           "Merge-Assistent mergen' ist ein UI-Flow ohne headless-prüfbaren "
                           "Core-Pfad; Verifikation erfordert GUI-Bedienung + Screenshot."),
                "supporting_evidence_real_two_branch_merge": {
                    "feature_a_commit": a_hash,
                    "feature_b_commit": b_hash,
                    "git_log_main_oneline": log_main,
                    "both_commits_in_main": both_in,
                    "git_status_clean": clean,
                },
            }, indent=2, ensure_ascii=False))
            record("F071", "blocked", e,
                   "kein merge IPC im Core; UI-Flow, nur per Screenshot prüfbar")
        except Exception as e:
            record("F071", "blocked", note=f"kein IPC; setup: {e}")

    finally:
        shutil.rmtree(base, ignore_errors=True)

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
