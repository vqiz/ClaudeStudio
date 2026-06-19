# Security Policy

ClaudeStudio is a local-first developer tool: it drives the Claude Code CLI,
runs a Rust sidecar that talks to the app over a **user-only Unix domain
socket**, and stores its data on your machine. There is no ClaudeStudio server
that receives your code or conversations.

## Supported Versions

The project is in **pre-alpha**. Security fixes land on `main`; there is no
long-term-support branch yet.

| Version | Supported |
| ------- | --------- |
| `main`  | ✅        |
| tagged pre-releases | ⚠️ best effort |

## Reporting a Vulnerability

Please **do not open a public issue** for security-sensitive reports.

1. Use GitHub's [private vulnerability reporting](https://github.com/vqiz/ClaudeStudio/security/advisories/new)
   ("Report a vulnerability") on the repository's **Security** tab.
2. Include a description, reproduction steps, affected component
   (`app/`, `core/`, a specific crate), and impact.

We aim to acknowledge reports within a few days and to coordinate a fix and
disclosure timeline with you.

## Scope & Hardening Notes

Areas that are most security-relevant in this codebase:

- **IPC boundary** (`core/crates/cs-ipc`, `cs-cli`): the sidecar binds a socket
  under `~/.claudestudio/` and accepts length-prefixed MessagePack frames.
  Frames are capped at 16 MiB; unknown methods are rejected.
- **Process execution** (`cs-git`, `cs-mcp`, `cs-ssh`, `cs-claude`): these
  crates shell out to `git`, MCP servers, `ssh`/`scp`, and the `claude` CLI.
  Inputs that reach a command line should be treated as untrusted.
- **Trust modes & permissions**: the trust posture controls how autonomously
  agents may act. Review it before enabling `auto`/`yolo`.

Responsible disclosure is appreciated and credited.
