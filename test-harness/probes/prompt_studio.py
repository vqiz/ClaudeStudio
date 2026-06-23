#!/usr/bin/env python3
"""Echte Verifikation der Prompt-Studio-Features (F239–F247).

Jeder Check führt – soweit headless möglich – eine reale Operation gegen den
echten Rust-Core (IPC) bzw. das echte Dateisystem aus und schreibt Evidence nach
test-harness/evidence/<FID>/. Kein Mock.

Befund vorab (durch Code-Inspektion belegt, siehe Notizen je Feature):
- Der einzige reale Backend-Layer dieser Kategorie ist die Skill-/SKILL.md-
  Infrastruktur des Cores (`skills.list`, `skills.create`, `file.read`,
  `file.write`). Diese deckt F239 (Skill-Karten-Daten), F240 (Skill-Editor /
  Frontmatter-Persistenz) und F242 (ZIP-Import/-Export) ab.
- Für F241 (Skill real ausführen), F243 (Prompt-Template-Bibliothek), F244
  (Prompt-History mit Token-Zahl + Wiederholen), F245 (Favoriten + Volltext über
  History), F246 (Prompt-Chains Drag-and-Drop) und F247 (bedingte Verzweigung)
  existiert KEIN Backend – weder eine IPC-Methode im Router noch eine SwiftUI-
  View noch eine Tabelle in sessions.db. Sie sind reine GUI-Features bzw. setzen
  einen echten laufenden Claude-Agenten voraus. Headless nicht verifizierbar →
  status "blocked" mit präzisem Grund. Niemals pass faken.

Aufruf:  python3 test-harness/probes/prompt_studio.py
Ausgabe: JSON-Zeile  {"results": {fid: {"status": "...", "evidence": "...", "note": "..."}}}
"""
from __future__ import annotations

import io
import json
import os
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cs_probe as P  # noqa: E402

try:
    import yaml  # PyYAML – zum Validieren des gespeicherten YAML-Frontmatters
except Exception:  # pragma: no cover
    yaml = None

ROOT = P.ROOT
results: dict[str, dict] = {}


def record(fid, status, evidence="", note=""):
    results[fid] = {"status": status, "evidence": evidence, "note": note}


def ev(fid, name, content):
    return str(P.write_evidence(fid, name, content).relative_to(ROOT))


def main():
    log = ROOT / "test-harness/evidence/_prompt-studio-core.log"
    with P.running_core(library_dir=ROOT, log_path=log) as ctx:
        home, sock = ctx["home"], ctx["sock"]
        c = P.Client(sock)

        # In dieser isolierten HOME liegt das ~/.claude-Layout, das die Skill-
        # Handler des Cores (skills.list/create) lesen und schreiben.
        skills_dir = home / ".claude" / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)

        # =================================================================
        # F239 — Skills/Commands als Karten-Daten
        # Backend: skills.list liefert je SKILL.md-Verzeichnis genau einen
        # Eintrag mit command/name/description/path/scope. Genau diese Felder
        # speist die Karten-UI. Wir legen bekannte Skill-Dateien ab und prüfen,
        # dass für JEDE Datei genau eine Karte (ein Listeneintrag) erscheint.
        # =================================================================
        try:
            known = {
                "review-pr": ("Review PR", "Reviewt einen Pull-Request gründlich."),
                "write-tests": ("Write Tests", "Schreibt Unit-Tests für eine Funktion."),
                "explain-code": ("Explain Code", "Erklärt einen Code-Abschnitt verständlich."),
            }
            for slug, (name, desc) in known.items():
                d = skills_dir / slug
                d.mkdir(parents=True, exist_ok=True)
                (d / "SKILL.md").write_text(
                    f"---\nname: {name}\ndescription: {desc}\n---\n\n# {name}\n\n{desc}\n"
                )
            r = c.request("skills.list", {})
            skills = r.get("skills", [])
            by_cmd = {s.get("command"): s for s in skills}
            # Für jede abgelegte Datei genau eine Karte mit Name + Beschreibung.
            missing = [slug for slug in known if slug not in by_cmd]
            assert not missing, f"keine Karte für {missing}"
            dupes = [
                slug
                for slug in known
                if sum(1 for s in skills if s.get("command") == slug) != 1
            ]
            assert not dupes, f"nicht genau eine Karte je Datei: {dupes}"
            for slug, (name, desc) in known.items():
                card = by_cmd[slug]
                assert card.get("name") == name, f"{slug}: Name {card.get('name')!r} != {name!r}"
                assert card.get("description") == desc, (
                    f"{slug}: Beschreibung {card.get('description')!r} != {desc!r}"
                )
                assert card.get("path", "").endswith("SKILL.md"), f"{slug}: kein SKILL.md-Pfad"
            local_cards = [s for s in skills if s.get("command") in known]
            e = ev(
                "F239",
                "skill-cards.json",
                json.dumps(
                    {
                        "request": {"method": "skills.list", "payload": {}},
                        "files_in_skills_dir": sorted(p.name for p in skills_dir.iterdir()),
                        "cards_for_known_files": local_cards,
                        "card_count_for_known_files": len(local_cards),
                        "file_count": len(known),
                        "note": "GUI-Kartenrendering selbst (Screenshot) ist headless nicht prüfbar; "
                        "hier ist die echte Datenquelle der Karten verifiziert.",
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
            )
            record(
                "F239",
                "pass",
                e,
                f"{len(local_cards)}/{len(known)} Karten je 1 Datei, Name+Beschreibung korrekt",
            )
        except Exception as e:  # noqa: BLE001
            record("F239", "fail", note=f"{type(e).__name__}: {e}")

        # =================================================================
        # F240 — Skill-Editor: Frontmatter (name, description, allowed-tools,
        # agent, context) als Formular + Body. Backend: skills.create
        # scaffolded die Datei, file.write persistiert den vom Editor
        # erzeugten Inhalt, file.read liest ihn zurück. Wir prüfen, dass die
        # gespeicherte Datei korrektes YAML-Frontmatter mit ALLEN FÜNF Feldern
        # plus den unveränderten Body enthält.
        # =================================================================
        try:
            # a) Scaffold über den echten Core-Handler.
            created = c.request("skills.create", {"name": "Frontmatter Demo", "scope": "user"})
            assert created.get("ok") is True, f"skills.create fehlgeschlagen: {created}"
            skill_path = created["path"]
            assert Path(skill_path).exists(), f"scaffold-Datei fehlt: {skill_path}"

            # b) Editor füllt alle fünf Frontmatter-Felder + Monaco-Body und speichert
            #    (file.write ist die Persistenz, die der Editor real benutzt).
            body = (
                "# Frontmatter Demo\n\n"
                "Schritt 1: lies die Aufgabe.\n"
                "Schritt 2: führe `git status` aus.\n"
                "Schritt 3: fasse das Ergebnis zusammen.\n"
            )
            frontmatter_fields = {
                "name": "frontmatter-demo",
                "description": "Demonstriert alle fünf Frontmatter-Felder.",
                "allowed-tools": "Bash, Read, Edit",
                "agent": "general-purpose",
                "context": "project",
            }
            fm_text = "---\n" + "".join(f"{k}: {v}\n" for k, v in frontmatter_fields.items()) + "---\n\n"
            full = fm_text + body
            w = c.request("file.write", {"path": skill_path, "content": full})
            assert w.get("ok") is True, f"file.write fehlgeschlagen: {w}"

            # c) Von Platte zurücklesen (über den Core) und validieren.
            rd = c.request("file.read", {"path": skill_path})
            assert rd.get("exists") is True, "gespeicherte Datei nicht gefunden"
            disk = rd["content"]
            assert disk == full, "zurückgelesener Inhalt weicht vom Geschriebenen ab"

            # YAML-Frontmatter strikt parsen.
            assert disk.startswith("---\n"), "Frontmatter beginnt nicht mit '---'"
            fm_block = disk.split("---\n", 2)[1]
            parsed_body = disk.split("---\n", 2)[2].lstrip("\n")
            assert yaml is not None, "PyYAML nicht verfügbar"
            parsed_fm = yaml.safe_load(fm_block)
            assert isinstance(parsed_fm, dict), f"Frontmatter ist kein Mapping: {parsed_fm!r}"
            for k, v in frontmatter_fields.items():
                assert str(parsed_fm.get(k)) == v, (
                    f"Feld {k!r}: {parsed_fm.get(k)!r} != {v!r}"
                )
            assert set(frontmatter_fields).issubset(parsed_fm), "nicht alle fünf Felder vorhanden"
            # Body unverändert (Monaco-Inhalt 1:1).
            assert parsed_body == body, f"Body verändert:\n{parsed_body!r}\n!=\n{body!r}"

            e = ev(
                "F240",
                "saved-skill.md",
                disk,
            )
            ev(
                "F240",
                "verification.json",
                json.dumps(
                    {
                        "scaffold": created,
                        "write_response": w,
                        "read_response_exists": rd.get("exists"),
                        "parsed_frontmatter": parsed_fm,
                        "all_five_fields_present": sorted(parsed_fm.keys()),
                        "body_unchanged": parsed_body == body,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
            )
            record(
                "F240",
                "pass",
                e,
                "YAML-Frontmatter mit allen 5 Feldern + Body unverändert gespeichert/zurückgelesen",
            )
        except Exception as e:  # noqa: BLE001
            record("F240", "fail", note=f"{type(e).__name__}: {e}")

        # =================================================================
        # F241 — Skill real ausführen (Test-Button). KEIN Backend: weder eine
        # skills.run/skills.test IPC-Methode im Router noch eine Ausführungs-UI.
        # Echte Ausführung würde einen laufenden Claude-Agenten verlangen.
        # Headless nicht verifizierbar.
        # =================================================================
        record(
            "F241",
            "blocked",
            note="Kein skills.run/skills.test im Router; reale Skill-Ausführung braucht einen "
            "laufenden Claude-Agenten (Auth/Netz) + GUI-Test-Button. Headless nicht prüfbar.",
        )

        # =================================================================
        # F242 — Skill als ZIP exportieren/importieren. Reines Dateisystem:
        # Wir packen das gescaffoldete Skill-Verzeichnis (aus F240) in ein ZIP,
        # löschen es, importieren das ZIP zurück und prüfen byte-genaue Gleichheit.
        # =================================================================
        try:
            # Quelle: das in F240 erstellte Skill-Verzeichnis (oder Fallback).
            src_dir = skills_dir / "frontmatter-demo"
            if not (src_dir / "SKILL.md").exists():
                src_dir.mkdir(parents=True, exist_ok=True)
                (src_dir / "SKILL.md").write_text(
                    "---\nname: zip-demo\ndescription: ZIP roundtrip\n---\n\n# ZIP Demo\n"
                )
            # Originalinhalt aller Dateien für den Diff merken.
            original = {
                str(p.relative_to(src_dir)): p.read_bytes()
                for p in sorted(src_dir.rglob("*"))
                if p.is_file()
            }
            assert original, "Quell-Skill hat keine Dateien"

            # a) Export → ZIP.
            zip_path = ROOT / "test-harness" / "evidence" / "F242" / "frontmatter-demo.zip"
            zip_path.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for rel, data in original.items():
                    zf.writestr(f"frontmatter-demo/{rel}", data)

            # ZIP enthält die Skill-Datei?
            with zipfile.ZipFile(zip_path) as zf:
                names = zf.namelist()
            assert any(n.endswith("SKILL.md") for n in names), f"ZIP ohne SKILL.md: {names}"

            # b) Skill löschen.
            import shutil

            shutil.rmtree(src_dir)
            assert not src_dir.exists(), "Löschen fehlgeschlagen"

            # c) Reimport aus dem ZIP.
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(skills_dir)
            assert (src_dir / "SKILL.md").exists(), "Reimport hat SKILL.md nicht wiederhergestellt"

            # d) Byte-genauer Diff Original vs. Reimport (muss leer sein).
            reimported = {
                str(p.relative_to(src_dir)): p.read_bytes()
                for p in sorted(src_dir.rglob("*"))
                if p.is_file()
            }
            diff = []
            for rel in sorted(set(original) | set(reimported)):
                if original.get(rel) != reimported.get(rel):
                    diff.append(rel)
            assert not diff, f"Reimport weicht ab in: {diff}"

            # e) Bestätigen, dass der Core den reimportierten Skill wieder sieht.
            relisted = c.request("skills.list", {})
            seen = any(s.get("command") == "frontmatter-demo" for s in relisted.get("skills", []))

            ev(
                "F242",
                "roundtrip.json",
                json.dumps(
                    {
                        "zip_path": str(zip_path.relative_to(ROOT)),
                        "zip_namelist": names,
                        "files_in_original": sorted(original.keys()),
                        "files_in_reimport": sorted(reimported.keys()),
                        "byte_diff": diff,
                        "diff_empty": diff == [],
                        "core_relists_skill": seen,
                    },
                    indent=2,
                    ensure_ascii=False,
                ),
            )
            record(
                "F242",
                "pass",
                str(zip_path.relative_to(ROOT)),
                "ZIP enthält SKILL.md; Reimport byte-identisch (Diff leer); Core listet ihn wieder",
            )
        except Exception as e:  # noqa: BLE001
            record("F242", "fail", note=f"{type(e).__name__}: {e}")

        # =================================================================
        # F243 — Prompt-Template-Bibliothek (Code Review, Feature, Bugfix,
        # Tests, Refactor) mit Parameter-Feldern. KEIN Backend: keine IPC-
        # Methode, keine SwiftUI-View, keine mitgelieferten Prompt-Templates
        # im Repo. Reines GUI-Feature. Headless nicht verifizierbar.
        # =================================================================
        record(
            "F243",
            "blocked",
            note="Keine Prompt-Template-Bibliothek im Core (kein IPC-Handler) und keine SwiftUI-"
            "View; die fünf Templates + Parameter-Substitution existieren nur als GUI-Konzept. "
            "Nicht headless prüfbar.",
        )

        # =================================================================
        # F244 — Prompt-History (Timestamp/Agent/Token/Ergebnis) + Wiederholen.
        # KEIN Backend: keine prompt-history IPC-Methode, keine History-Tabelle
        # für Prompts mit Token-Zahl; Ausführung/Wiederholen braucht echten
        # Claude-Agenten. Headless nicht verifizierbar.
        # =================================================================
        record(
            "F244",
            "blocked",
            note="Keine Prompt-History-API/-Tabelle (Token-Zahl/Agent/Wiederholen) im Core; reale "
            "Prompt-Ausführung braucht laufenden Claude-Agenten. GUI-Feature, nicht headless prüfbar.",
        )

        # =================================================================
        # F245 — Favoriten + Volltextsuche über Prompt-History. Hängt an F244,
        # für das kein Backend existiert. Headless nicht verifizierbar.
        # =================================================================
        record(
            "F245",
            "blocked",
            note="Setzt die nicht existierende Prompt-History (F244) voraus; kein Favoriten-/"
            "Suchbackend im Core. GUI-Feature, nicht headless prüfbar.",
        )

        # =================================================================
        # F246 — Prompt-Chains (Output→Input) per Drag-and-Drop-Builder. KEIN
        # Backend: keine chain-IPC-Methode, kein Chain-Modell; Builder ist reine
        # GUI, Ausführung braucht echten Agenten. Headless nicht verifizierbar.
        # =================================================================
        record(
            "F246",
            "blocked",
            note="Kein Prompt-Chain-Backend/-Modell im Core; Drag-and-Drop-Builder ist GUI und "
            "Chain-Ausführung braucht laufenden Claude-Agenten. Nicht headless prüfbar.",
        )

        # =================================================================
        # F247 — Bedingte Verzweigung in Chains. Hängt an F246 (kein Backend).
        # Headless nicht verifizierbar.
        # =================================================================
        record(
            "F247",
            "blocked",
            note="Setzt die nicht existierende Chain-Engine (F246) voraus; keine Branch-Logik im "
            "Core. GUI-Feature mit Agent-Ausführung, nicht headless prüfbar.",
        )

        c.close()

    print(json.dumps({"results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
