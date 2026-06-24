#!/bin/bash
# Stub-`claude` für Headless-Orchestrierungstests. Emittiert das ECHTE
# stream-json-Protokoll, das der Core (cs-claude) zeilenweise parst — testet
# damit Spawn, Live-Streaming und Stop/Kill der App, OHNE ein echtes LLM (dessen
# Inhalt für F117/F118 irrelevant ist). Ignoriert alle CLI-Args bis auf das
# Schlüsselwort LONGRUN im Prompt (dann eine lange, per session.stop killbare
# Phase). Jede Zeile ist ein eigener write() → der Core sieht sie inkrementell.
# Eigene PID festhalten (für den Stop-Test), damit der Subprozess exakt
# identifiziert werden kann — bleibt über `exec` hinweg gleich.
[ -n "$CS_STUB_PIDFILE" ] && printf '%s' "$$" > "$CS_STUB_PIDFILE"
printf '%s\n' "{\"type\":\"system\",\"session_id\":\"stub-$$\"}"
# Im STREAM-Modus (F136) KEIN voriger assistant_text — dessen record_message lädt das
# neuronale Modell (blockiert ~Sekunden) und würde die nachfolgenden Token-Deltas batchen.
case "$*" in
  *STREAM*) ;;
  *) printf '%s\n' '{"type":"assistant","message":{"content":[{"type":"text","text":"Schritt 1: Analyse"}]}}' ;;
esac
# 'Ab hier weitermachen' (F163): wurde --resume <id> übergeben, bestätigt der Stub
# die Fortsetzung des vorherigen Kontexts, damit der Resume-Pfad nachweisbar ist.
prev=""; resume_id=""
for a in "$@"; do [ "$prev" = "--resume" ] && resume_id="$a"; prev="$a"; done
[ -n "$resume_id" ] && printf '%s\n' "{\"type\":\"assistant\",\"message\":{\"content\":[{\"type\":\"text\",\"text\":\"RESUMED:$resume_id\"}]}}"
# 'EDITFILE' im Prompt: der Agent erzeugt im cwd eine echte Datei-Änderung
# (Substrat für den Post-Run-Hook 'git commit', F116).
case "$*" in
  *EDITFILE*) printf 'agent change %s\n' "$$" > agent_edit.txt ;;
esac
case "$*" in
  *STREAM*)
    # 'STREAM' (F136): Token-für-Token-Streaming via content_block_delta. Python (mit
    # explizitem flush) garantiert UNGEPUFFERTE, inkrementelle writes — der Output
    # erscheint zeichenweise (nicht erst am Ende). bash-printf wäre pipe-gepuffert.
    python3 -c '
import sys, time, json
for tok in ["Der ", "DELETE", "-Endpoint ", "wurde ", "hinzugefuegt", "."]:
    sys.stdout.write(json.dumps({"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":tok}}}) + "\n")
    sys.stdout.flush()
    time.sleep(0.25)
'
    ;;
  *LONGRUN*)
    # exec ersetzt die Shell durch sleep (gleiche PID) — kein Kind-Prozess hält
    # die stdout-Pipe offen, daher EOF + Kill sofort beim session.stop.
    exec sleep 10 ;;
  *)
    sleep 0.3
    printf '%s\n' '{"type":"assistant","message":{"content":[{"type":"text","text":"Schritt 2: Implementierung"}]}}'
    sleep 0.3
    printf '%s\n' '{"type":"assistant","message":{"content":[{"type":"text","text":"Schritt 3: Abschluss"}]}}'
    ;;
esac
printf '%s\n' '{"type":"result","cost_usd":0.0123,"is_error":false}'
