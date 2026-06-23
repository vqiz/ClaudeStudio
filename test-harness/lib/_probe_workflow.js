export const meta = {
  name: 'claudestudio-probe-authoring',
  description: 'Fan out real IPC-probe authoring across core-backed feature categories',
  phases: [{ title: 'Probe', detail: 'one agent per category authors + runs a real probe module' }],
}

const SHARED = `
Du verifizierst ClaudeStudio-Features im ECHTEN Betrieb gegen den realen Rust-Core.
KEINE Mocks, KEINE erfundenen Ergebnisse. Du schreibst ein Python-Probe-Modul, das
echte Operationen gegen den laufenden Core ausführt und Evidence sammelt.

REFERENZ-TEMPLATE (genau diesem Stil folgen): test-harness/probes/foundation.py
IPC-BIBLIOTHEK: test-harness/lib/cs_probe.py — nutze:
  import sys; sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
  import cs_probe as P
  with P.running_core(library_dir=P.ROOT, log_path=Path("/tmp/<cat>.log")) as ctx:
      c = P.Client(ctx["sock"])
      r = c.request("<method>", {<payload>})   # wirft P.RemoteError bei kind==error
  P.write_evidence(fid, "name.json", inhalt) -> schreibt evidence/<fid>/name.json
  P.ROOT = Repo-Root.   Mehrere Cores parallel sind ok (eigener Socket je HOME).

EMPIRISCH VERIFIZIERTE METHODEN-SHAPES (gegen den echten Core bestätigt):
- ping {} -> {pong:true}
- config.get {} -> {trust_mode, default_model, daily_budget_usd, context_token_budget, vector:{...}}
- config.set {"<key>":<val>} -> ok   (z.B. {"trust_mode":"auto"})
- context.budget {} -> {granted_total, layers:[{layer, requested_tokens, granted_tokens, truncated}]}
- library.load_defaults {} -> {tasks:N, definitions:N}   (installiert mitgelieferte Libs nach ~/.claudestudio)
- tasks.list {} -> {tasks:[{name, category, description, ...}]}    tasks.create {<task-json>}   tasks.delete {...}
- definitions.list {} -> {definitions:[{name, category, path, ...}]}   definitions.create {...}   definitions.delete {...}
- skills.list {} -> {skills:[{command, description, name, path, scope}]}   skills.create/install/uninstall
- plugins.list {} -> {plugins:[]}   plugins.marketplace_list {} -> {marketplaces:[]}   plugins.install/uninstall/set_enabled/marketplace_add
- mcp.list {cwd} -> {servers:[{name, transport, target, args, env, scope, source}]}   mcp.list_all   mcp.upsert {name,command,args,transport,scope,cwd}   mcp.remove {name,scope,cwd}
- hooks.list {} -> {hooks:[]}
- file.read {path} -> {content, exists, path}      file.write {path, content} -> {ok, bytes, path}
- git.status {cwd} -> {entries:[{path, raw, state}]}   git.branch {cwd} -> {branch}   git.log {cwd, limit?} -> {commits:[{hash, author, date, subject}]}   git.worktrees {cwd} -> {worktrees:[{branch, head, path}]}   git.diff {cwd, ...}
- session.create {title, cwd} -> {id}   session.list -> {sessions:[...]}   session.get {id}   session.messages {id} -> {messages:[]}   session.search {query} -> {hits:[]}   session.stats -> {sessions, messages, events, tool_calls, file_diffs}   session.stop {session_id}
- events.subscribe -> Event-Stream (c.subscribe_events())

UNBEKANNTE METHODE/PAYLOAD? Zwei legitime Wege: (a) core/crates/cs-cli/src/router.rs lesen
(Handler-fn deiner Kategorie), oder (b) empirisch: Methode mit {} aufrufen und die
Fehlermeldung "[400] missing 'X'" lesen, um Pflichtfelder zu entdecken.

EHERNE EHRLICHKEITSREGELN:
- status "pass" NUR wenn eine echte Assertion gegen den echten Core hielt. Die Evidence-Datei
  MUSS die echte Request + echte Response enthalten.
- Wenn ein Feature Fähigkeiten braucht, die headless NICHT verfügbar sind — interaktives
  GUI-Klicken/Screenshots, echte externe Dienste mit Credentials (GitHub-API, Slack, Linear,
  Gmail, ElevenLabs, Deepgram), Audio-Hardware, Browser-Computer-Use, ein echter laufender
  Claude-Agent — dann status "blocked" mit präzisem Grund. NIEMALS pass faken.
- Wenn eine Operation real fehlschlägt (Core-Fehler, falsches Verhalten): status "fail" mit der echten Fehlermeldung.

AUSGABE-VERTRAG (zwingend): Dein Probe-Modul MUSS als allerletzte Zeile genau das hier nach stdout drucken:
  print(json.dumps({"results": results}))
wobei results = {"<FID>": {"status": "pass|fail|blocked", "evidence": "<repo-relativer-pfad-oder-leer>", "note": "<kurz>"}, ...}
Schreibe NICHTS in feature_list.json — das Markieren passiert zentral.
`;

// Core-backed categories with their feature IDs (literal — no args plumbing).
const cats = [
  { category: 'session-archive', ids: ['F151','F152','F153','F154','F155','F156','F157','F158','F159','F160','F161','F162','F163','F164','F165'] },
  { category: 'git', ids: ['F072','F073','F074','F075'] },
  { category: 'worktree', ids: ['F064','F065','F066','F067','F068','F069','F070','F071'] },
  { category: 'file-explorer', ids: ['F046','F047','F048','F049','F050','F051','F052','F053','F054','F055','F056','F057','F058','F059','F060','F061','F062','F063'] },
  { category: 'definitions', ids: ['F097','F098','F099','F100','F101','F102','F103','F104','F105'] },
  { category: 'context-system', ids: ['F076','F077','F078','F079','F080','F081','F082','F083','F084','F085','F086'] },
  { category: 'memory', ids: ['F087','F088','F089','F090','F091','F092','F093','F094','F095','F096'] },
  { category: 'task-library', ids: ['F196','F197','F198','F199','F200','F201','F202','F203','F204','F205','F206','F207','F208','F209','F210','F211','F212','F213','F214','F215'] },
  { category: 'mcp', ids: ['F248','F249','F250','F251','F252','F253','F254','F255'] },
  { category: 'hooks', ids: ['F256','F257','F258','F259','F260','F261','F262','F263','F264','F265','F266'] },
  { category: 'security', ids: ['F286','F287','F288','F289','F290','F291','F292','F293','F294','F295','F296','F297','F298','F299'] },
  { category: 'vector-db', ids: ['F166','F167','F168','F169','F170','F171','F172','F173','F174','F175','F176','F177','F178','F179','F180','F181','F182','F183','F184','F185'] },
  { category: 'prompt-studio', ids: ['F239','F240','F241','F242','F243','F244','F245','F246','F247'] },
  { category: 'cost-telemetry', ids: ['F277','F278','F279','F280','F281','F282','F283','F284','F285'] },
  { category: 'agentic-os', ids: ['F300','F301','F302','F303','F304','F305','F306','F307','F308','F309','F310','F311','F312','F313','F314','F315'] },
];

const RESULT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['category', 'probe_file', 'summary'],
  properties: {
    category: { type: 'string' },
    probe_file: { type: 'string', description: 'repo-relativer Pfad des geschriebenen Probe-Moduls' },
    summary: {
      type: 'object',
      additionalProperties: false,
      required: ['pass', 'fail', 'blocked'],
      properties: {
        pass: { type: 'array', items: { type: 'string' } },
        fail: { type: 'array', items: { type: 'string' } },
        blocked: { type: 'array', items: { type: 'string' } },
      },
    },
    note: { type: 'string' },
  },
};

phase('Probe');
const results = await parallel(cats.map((cat) => async () => {
  const slug = cat.category.replace(/[^a-z0-9]+/g, '_');
  const prompt = `${SHARED}

DEINE KATEGORIE: "${cat.category}"  (Feature-IDs: ${cat.ids.join(', ')})

1. Lies feature_list.json und filtere die Objekte mit category=="${cat.category}". Für jedes:
   description + real_world_test studieren.
2. Schreibe das Probe-Modul nach EXAKT: test-harness/probes/${slug}.py
   - Folge dem Stil von test-harness/probes/foundation.py (record/ev-Helfer, ein laufender Core, JSON-Ausgabe).
   - Für JEDES Feature deiner Kategorie: führe den real_world_test so getreu wie möglich gegen den
     echten Core aus (IPC-Methoden, Dateisystem, echtes git-Repo unter P.ROOT). Assertion + Evidence.
   - Headless nicht verifizierbar -> status "blocked" mit Grund. Niemals faken.
3. Führe das Modul aus: python3 test-harness/probes/${slug}.py  — repariere Fehler bis es sauber läuft
   und den results-JSON druckt. Stelle sicher, dass die pass-Features ECHTE Evidence-Dateien erzeugt haben.
4. Gib das Struktur-Ergebnis zurück (category, probe_file, summary mit pass/fail/blocked-FID-Listen, note).

Sei gründlich und ehrlich. Lieber ehrlich "blocked" als ein durchgewunkenes kaputtes Feature.`;

  const r = await agent(prompt, {
    label: `probe:${cat.category}`,
    phase: 'Probe',
    agentType: 'general-purpose',
    schema: RESULT_SCHEMA,
  });
  return r;
}));

return results.filter(Boolean);
