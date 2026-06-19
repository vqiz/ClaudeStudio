# Task Library

The **Task Library** is the community-extensible, shippable layer of ClaudeStudio. A *Task* is a packaged, parameterized, agent-driven workflow — a repeatable job ("run a DSGVO audit", "generate release notes", "scan for OWASP issues") that you can fill out like a form and run, schedule, or hand to the agentic Supervisor.

Every task is a single self-describing `*.task.json` file validated against [`schema/task.schema.json`](schema/task.schema.json) (JSON Schema draft 2020-12). The file is the **only** source of truth: it is rendered into the 6-tab Task Builder UI, executed by the Supervisor/Event-Bus layer, indexed into semantic memory, and shared as part of community packs.

```
tasks/
├── README.md                  ← you are here
├── schema/
│   └── task.schema.json       ← JSON Schema for a task
├── compliance/                ← German/EU legal & regulatory checks
├── code-quality/              ← security, coverage, CVEs, dead code
├── docs/                      ← README, changelog, API docs
└── deployment/                ← pre-deploy gates, release notes
```

---

## Why tasks (vs. skills, CLAUDE.md, raw prompts)

| Mechanism | What it is | When it loads |
|-----------|------------|---------------|
| **CLAUDE.md** | Always-on project/user instructions | Every turn, implicitly |
| **Skill** | A capability the model *decides* to invoke when relevant | On demand, model-chosen |
| **Definition** (`.def.md`) | A reusable prompt block you *inject* into a task or chat | When you reference it |
| **Task** (`.task.json`) | A full parameterized workflow with inputs, an agent envelope, steps, output and optional schedule | When you run, schedule, or trigger it |

A task is the heaviest, most structured unit: it owns its own model, tool allow-list, budget, isolation mode, form inputs and multi-step chain.

---

## The `.task.json` format

A task is a JSON object. The required top-level fields are `id`, `name`, `icon`, `category`, `tags`, `scope`, `agent`, `inputs`, `workflow`, `output`. `description`, `version`, `author` and `schedule` are optional.

```jsonc
{
  "id": "dsgvo-audit",
  "name": "DSGVO-Audit",
  "icon": "checkmark.shield",
  "category": "compliance",
  "tags": ["dsgvo", "gdpr", "datenschutz"],
  "scope": "global",
  "version": "1.0.0",
  "agent": {
    "model": "claude-opus-4-8",
    "allowed_tools": ["Read", "Grep", "Glob"],
    "budget_usd": 2.5,
    "isolation": "sandbox"
  },
  "inputs": [
    { "key": "target_dir", "label": "Zu prüfendes Verzeichnis", "type": "path", "required": true, "default": "." }
  ],
  "workflow": {
    "steps": [
      { "title": "Scan", "prompt": "Durchsuche {{target_dir}} nach personenbezogenen Daten ..." }
    ]
  },
  "output": { "type": "report", "path": "reports/dsgvo-{{date}}.md" }
}
```

> JSON only — **no comments and no trailing commas** in real `.task.json` files (the block above uses `jsonc` purely for illustration). The app rejects any file that does not validate against the schema.

### Field reference

- **`id`** — globally unique kebab-case id. Used for storage keys, memory indexing and deep links.
- **`name`**, **`description`**, **`icon`** — library card presentation. `icon` is an SF Symbols name (preferred on macOS) or an emoji.
- **`category`** — one of `compliance`, `code-quality`, `docs`, `deployment`, `research`, `automation`, `data`, `other`. Drives the sidebar grouping.
- **`tags`** — free-form, searchable, also fed to semantic memory.
- **`scope`** — `project` (lives in the project's `.claudestudio` dir), `user` (personal, cross-project), or `global` (ships with the app or an installed pack).
- **`agent`** — the execution envelope (see below).
- **`inputs`** — the form fields (see below).
- **`workflow.steps`** — the ordered agent turns (see below).
- **`output`** — what is produced and what runs afterwards.
- **`schedule`** — optional automation (see Scheduling & triggers).

### `agent` — the execution envelope

| Field | Meaning |
|-------|---------|
| `model` | Default model: `claude-opus-4-8`, `claude-sonnet-4-5`, `claude-haiku-4-5`, or `inherit` (use the project default). Steps can override. |
| `allowed_tools` | Tool allow-list, mapped 1:1 onto Claude Code permissions (`Read`, `Grep`, `Glob`, `Bash`, `Edit`, `Write`, `WebSearch`, `WebFetch`, …). `[]` means pure reasoning. |
| `budget_usd` | Soft USD cap for one run; the Supervisor pauses for confirmation when exceeded. |
| `isolation` | `worktree` (dedicated git worktree), `sandbox` (read-only / no network), or `inplace` (directly in the working dir). |

Choose the **least privilege** that still lets the task succeed. Read-only audits use `sandbox` + read tools only; tasks that write code use `worktree` so nothing touches the user's working tree until they approve.

### `inputs` — the form

Each input renders one field in the Builder's *Inputs* tab and in the run dialog. Collected values are substituted into prompts and output paths as `{{key}}`.

| `type` | Widget |
|--------|--------|
| `string` | single-line text |
| `text` | multi-line text area |
| `number` | numeric stepper |
| `boolean` | toggle |
| `select` | single-choice dropdown (requires `options`) |
| `multiselect` | multi-choice (requires `options`) |
| `path` | file/dir picker |
| `url` | URL field with validation |
| `date` | date picker |

`select`/`multiselect` options are `{ "value": "...", "label": "..." }` pairs. Mark blocking fields with `"required": true`.

### `workflow.steps` — the chain

An ordered list of agent turns. Each step needs a `prompt` and may declare `id`, `title`, a per-step `model`, and a per-step `tools` subset. Prompts reference inputs (`{{target_dir}}`) and the outputs of earlier steps by their `id`. Keep cheap, mechanical steps on `claude-haiku-4-5` and reserve `claude-opus-4-8` for synthesis and judgement.

### `output`

`type` is one of `markdown`, `json`, `report`, `file`, `patch`, `comment`, `none`. `path` is an optional output template (supports `{{key}}` and the built-in `{{date}}`). `post_hook` is an optional shell command or Claude Code hook id run after the artifact lands (open the file, run a formatter, send a notification).

---

## How the 6-tab Builder maps to the file

The Task Builder is six tabs; each writes a slice of the same `.task.json`:

| Tab | Writes |
|-----|--------|
| **1 · Overview** | `id`, `name`, `description`, `icon`, `category`, `tags`, `scope`, `version`, `author` |
| **2 · Agent** | `agent.model`, `agent.allowed_tools`, `agent.budget_usd`, `agent.isolation` |
| **3 · Inputs** | `inputs[]` |
| **4 · Workflow** | `workflow.steps[]` |
| **5 · Output** | `output.type`, `output.path`, `output.post_hook` |
| **6 · Schedule** | `schedule` |

Editing the raw JSON and editing the Builder are fully equivalent and round-trip losslessly.

---

## Scheduling & triggers

The optional `schedule` block wires a task into the automation layer.

- **`trigger: "manual"`** — run-on-demand only (the default if no `schedule` is present).
- **`trigger: "cron"`** — fires on a standard 5-field cron expression in `schedule.cron` (e.g. `"0 6 * * 1"` = Mondays 06:00). Registered with the cron system; runs in the configured `isolation`.
- **`trigger: "on_event"`** — subscribes to an Event-Bus topic in `schedule.event` (e.g. `git.post_commit`, `file.changed`, `pr.opened`). The Supervisor invokes the task when the event publishes.

`schedule.enabled` toggles the registration without deleting the configuration. Scheduled and event-driven runs honour the same `agent` envelope (model, tools, budget, isolation) as manual runs.

---

## Authoring & validating a task

1. Copy the closest existing task or start from the Builder.
2. Keep `id` unique and kebab-case; bump `version` on every change.
3. Request the **minimum** tools and the cheapest viable `isolation`.
4. Validate before shipping — any JSON-Schema validator works:
   ```bash
   # using ajv-cli
   npx ajv-cli validate -s schema/task.schema.json -d "compliance/*.task.json" --spec=draft2020
   ```
5. Write genuinely useful, specific prompts. Reference every declared input at least once; never leave a `{{placeholder}}` that no input supplies.
