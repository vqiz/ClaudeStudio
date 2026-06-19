# Definition Library

A **Definition** is a small, reusable prompt block — a single, named, versioned unit of guidance that you *inject* into a chat, a task step, or an agent's system context on demand. Where a [Task](../tasks/README.md) is a full parameterized workflow, a definition is one well-crafted paragraph-to-page of instruction that encodes a standard, a convention, or a recipe you want Claude to follow consistently.

Definitions are written as `*.def.md` files: YAML frontmatter with metadata, followed by a Markdown body that *is* the prompt.

```
definitions/
├── README.md                 ← you are here
├── loading-systems/          ← media/asset loading recipes
├── code-standards/           ← language & error-handling conventions
├── ui-design/                ← design-system guidance
└── performance/              ← optimization playbooks
```

---

## Why definitions exist (and how they differ from skills & CLAUDE.md)

ClaudeStudio has several layers of "guidance", each with a different loading model. Definitions occupy the sweet spot between a one-off pasted prompt and a heavyweight always-on instruction.

| Layer | Granularity | Who decides to load it | When it is in context |
|-------|-------------|------------------------|------------------------|
| **CLAUDE.md** | Project/user-wide | The system | Every turn, always |
| **Skill** | A capability | The model | When the model judges it relevant |
| **Definition (`.def.md`)** | One convention/recipe | **You** | Only when you inject it |
| **Task (`.task.json`)** | A whole workflow | You (run/schedule) | For the duration of a run |

Key distinctions:

- **vs. CLAUDE.md** — CLAUDE.md is unconditional and applies to *everything*. A definition is opt-in and scoped: you pull in `error-handling.def.md` only while touching error paths, instead of bloating every prompt with every standard. This keeps the always-on context lean.
- **vs. Skills** — A skill is an *agent capability* the model invokes autonomously and can contain executable logic. A definition is *pure prompt text* that you (the human, or a task) deliberately inject. Definitions never "fire" on their own.
- **vs. raw pasted prompts** — A definition is named, versioned, tagged, scoped, and reusable across projects, so a standard is written once and stays consistent everywhere.

---

## The `.def.md` format

A definition is a Markdown file with a YAML frontmatter header followed by the body.

```markdown
---
name: Error Handling Standard
category: code-standards
tags: [errors, exceptions, resilience]
scope: user
tokens: 280
version: 1.0.0
---

When writing error-handling code, follow these rules:

1. Fail loud in development, fail safe in production.
2. ...
```

### Frontmatter fields

| Field | Required | Meaning |
|-------|----------|---------|
| `name` | yes | Human-readable title shown in the definition picker. |
| `category` | yes | Grouping for the sidebar (matches the folder, e.g. `code-standards`, `ui-design`, `performance`, `loading-systems`). |
| `tags` | yes | Searchable keywords; also fed to semantic memory. |
| `scope` | yes | `project`, `user`, or `global` — same meaning as for tasks (project-local, personal/cross-project, or shipped/installed). |
| `tokens` | yes | Approximate token cost of the body. The injector uses this to budget context; keep definitions small. |
| `version` | yes | Semantic version; bump on every edit so injected copies can be refreshed. |

### The body

Everything after the closing `---` is the prompt that gets injected verbatim. Write it as direct instruction to the model. Good definitions are:

- **Self-contained** — they make sense without their filename or surrounding context.
- **Prescriptive** — they state *do this / avoid that*, not background theory.
- **Compact** — a definition that grows past a few hundred tokens should probably become a Skill or a Task.

---

## Inject mechanisms

Definitions are *injected*, never auto-loaded. ClaudeStudio offers four ways to do it:

1. **`@`-mention in chat** — type `@error-handling` (or pick from the inserter) and the body is prepended to your message for that turn.
2. **Task step reference** — a task step can list definitions to prepend to its prompt before the agent runs (so a "refactor" step always carries your code standards).
3. **Pinned to a session** — pin one or more definitions and they ride along as additional system context for the whole session, then drop off when unpinned.
4. **Agent profile** — the Supervisor can attach a fixed set of definitions to a named agent so every run of that agent inherits the same conventions.

In all cases the `tokens` field lets the injector warn you before you blow your context budget, and the `version` lets it offer to update a stale pinned copy.

---

## Authoring guidelines

- One concern per file. `typescript-strict` and `error-handling` are separate definitions, even though both are "code standards".
- Keep `tokens` honest (roughly `characters / 4`); re-estimate when you edit.
- Prefer imperative voice and concrete rules over prose.
- Use `scope: global` only for genuinely universal guidance; team- or project-specific rules belong in `project`.
- Bump `version` on every meaningful change.
