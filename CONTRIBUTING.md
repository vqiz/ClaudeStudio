# Contributing to ClaudeStudio

First off, thank you for considering a contribution to **ClaudeStudio** — a native
macOS application (SwiftUI front-end + Rust core sidecar) that turns Claude Code
into a complete GUI and "Agentic OS". Contributions of every size are welcome,
from typo fixes to whole new subsystems.

This document explains how to set up your environment, the repository layout, the
commands we expect to pass before review, our commit and PR conventions, and how to
find a good first task.

---

## Table of contents

- [Code of Conduct](#code-of-conduct)
- [Prerequisites](#prerequisites)
- [Getting started](#getting-started)
- [Repository layout](#repository-layout)
- [Building and testing](#building-and-testing)
  - [Rust core](#rust-core-core)
  - [Swift app](#swift-app-app)
- [Commit conventions](#commit-conventions)
- [Sign-off (DCO)](#sign-off-dco)
- [Pull request process](#pull-request-process)
- [Good first issues](#good-first-issues)
- [Reporting bugs and requesting features](#reporting-bugs-and-requesting-features)
- [Security issues](#security-issues)
- [License](#license)

---

## Code of Conduct

This project and everyone participating in it is governed by the
[Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you
are expected to uphold it. Please report unacceptable behavior to the contact
listed in that document.

---

## Prerequisites

ClaudeStudio is a macOS-first project. You will need:

| Tool                | Minimum version | Notes                                                         |
| ------------------- | --------------- | ------------------------------------------------------------- |
| macOS               | 14 (Sonoma)     | The SwiftUI front-end targets macOS 14+.                      |
| Rust                | stable (latest) | Install via [rustup](https://rustup.rs). Includes `cargo`.    |
| `rustfmt`, `clippy` | bundled         | `rustup component add rustfmt clippy`                         |
| Swift               | 6.0+            | Ships with the matching Xcode / Command Line Tools.           |
| Xcode               | 16+             | Provides the Swift 6 toolchain and `swift` CLI.               |
| Claude Code CLI     | latest          | See <https://docs.claude.com/claude-code>. Required at runtime. |
| Qdrant              | latest          | Optional locally; used for the semantic memory subsystem.     |

Verify your toolchain:

```sh
rustc --version        # expect a recent stable release
cargo --version
swift --version        # expect Swift 6.x
claude --version       # Claude Code CLI
```

---

## Getting started

```sh
# 1. Fork the repository on GitHub, then clone your fork
git clone https://github.com/<your-username>/ClaudeStudio.git
cd ClaudeStudio

# 2. Add the upstream remote so you can stay in sync
git remote add upstream https://github.com/vqiz/ClaudeStudio.git

# 3. Create a topic branch off main
git switch -c feat/short-description

# 4. Build everything once to confirm your environment is healthy
( cd core && cargo build --workspace )
( cd app  && swift build )
```

Keep your branch up to date with `git fetch upstream && git rebase upstream/main`.

---

## Repository layout

```text
ClaudeStudio/
├── core/        # Rust workspace — the sidecar core (event bus, supervisor,
│                # memory/Qdrant + SQLite, Claude Code orchestration, IPC).
├── app/         # Swift 6 / SwiftUI macOS application (the native UI front-end).
├── docs/        # Architecture notes, design docs, and user-facing documentation.
├── .github/     # CI/CD workflows, issue/PR templates, and repo automation.
├── LICENSE
├── CONTRIBUTING.md
├── CODE_OF_CONDUCT.md
├── SECURITY.md
└── CHANGELOG.md
```

The Rust core and the Swift app communicate over a local IPC channel (a Unix
domain socket). When you change the IPC contract, update **both** sides in the same
pull request.

---

## Building and testing

Before opening a PR, please run the same checks that CI runs. The commands below
mirror `.github/workflows/rust.yml` and `.github/workflows/swift.yml`.

### Rust core (`core/`)

All commands run from inside the `core/` directory:

```sh
cd core

# Format — CI runs this with --check, so format before committing
cargo fmt

# Lint — warnings are treated as errors in CI
cargo clippy --workspace --all-targets -- -D warnings

# Build the full workspace
cargo build --workspace

# Run the test suite
cargo test --workspace
```

A convenient pre-push sweep:

```sh
cargo fmt --check && \
cargo clippy --workspace --all-targets -- -D warnings && \
cargo build --workspace && \
cargo test --workspace
```

### Swift app (`app/`)

All commands run from inside the `app/` directory:

```sh
cd app

swift --version
swift build
swift test    # the suite may be empty during early scaffolding — that is fine
```

---

## Commit conventions

We use [**Conventional Commits**](https://www.conventionalcommits.org/en/v1.0.0/).
This keeps history readable and powers automated changelog generation.

Format:

```text
<type>(<optional scope>): <short, imperative summary>

<optional body>

<optional footer(s)>
```

Common types:

| Type       | Use for                                                            |
| ---------- | ------------------------------------------------------------------ |
| `feat`     | A new user-facing feature.                                         |
| `fix`      | A bug fix.                                                         |
| `docs`     | Documentation only changes.                                        |
| `refactor` | A code change that neither fixes a bug nor adds a feature.         |
| `perf`     | A change that improves performance.                                |
| `test`     | Adding or correcting tests.                                        |
| `build`    | Build system, dependencies, or tooling changes.                    |
| `ci`       | CI configuration and scripts.                                      |
| `chore`    | Maintenance that does not fit the above.                           |

Suggested scopes: `core`, `app`, `memory`, `supervisor`, `voice`, `brain`, `ipc`,
`ci`, `docs`.

Examples:

```text
feat(supervisor): add event-bus backpressure handling
fix(memory): prevent duplicate Qdrant point IDs on re-index
docs(contributing): clarify Swift 6 toolchain requirement
```

Breaking changes: append `!` after the type/scope and add a `BREAKING CHANGE:`
footer, e.g. `feat(ipc)!: rename the session handshake message`.

---

## Sign-off (DCO)

ClaudeStudio uses the [Developer Certificate of Origin](https://developercertificate.org/).
By signing off, you certify that you wrote the patch or otherwise have the right to
contribute it under the project's MIT license.

Add a sign-off line to every commit by using `-s`:

```sh
git commit -s -m "fix(core): handle empty session id"
```

This appends a trailer:

```text
Signed-off-by: Your Name <you@example.com>
```

Make sure the name and email match your Git configuration. If you forgot to sign off,
amend with `git commit --amend -s` (or rebase and re-sign for multiple commits).

---

## Pull request process

1. **Open an issue first** for anything non-trivial so we can align on the approach.
2. Work on a **topic branch** in your fork, not on `main`.
3. Keep PRs **focused** — one logical change per PR. Split unrelated work.
4. Ensure all **CI checks pass** locally (see [Building and testing](#building-and-testing)).
5. **Update documentation** and the `[Unreleased]` section of
   [CHANGELOG.md](CHANGELOG.md) when your change is user-visible.
6. Fill out the **pull request template** completely.
7. **Sign off** every commit (DCO).
8. Mark the PR as a **draft** while it is in progress; mark it ready for review when
   CI is green.
9. A maintainer (see [`.github/CODEOWNERS`](.github/CODEOWNERS)) will review. Address
   feedback by pushing additional commits; we squash on merge.

Reviews focus on correctness, clarity, consistency with the existing architecture,
and test coverage. Be patient and kind — so will we.

---

## Good first issues

New here? Look for issues labeled
[`good first issue`](https://github.com/vqiz/ClaudeStudio/labels/good%20first%20issue)
and [`help wanted`](https://github.com/vqiz/ClaudeStudio/labels/help%20wanted).
These are scoped to be approachable without deep knowledge of the whole codebase.

Good first contributions also include:

- Improving documentation, examples, and inline comments.
- Adding tests to the Rust core or the Swift app.
- Tightening error messages and logging.
- Fixing `clippy` or `swift` warnings.

If you are unsure where to start, open a discussion or comment on an issue and we
will help you find a good entry point.

---

## Reporting bugs and requesting features

Please use the issue forms:

- [Bug report](https://github.com/vqiz/ClaudeStudio/issues/new?template=bug_report.yml)
- [Feature request](https://github.com/vqiz/ClaudeStudio/issues/new?template=feature_request.yml)

Include reproduction steps, your environment (macOS, Rust, Swift, Claude Code CLI
versions), and logs where relevant.

---

## Security issues

**Do not** open public issues for security vulnerabilities. ClaudeStudio executes
agent commands and handles credentials, so we take security seriously. Please follow
the private reporting process described in [SECURITY.md](SECURITY.md).

---

## License

By contributing, you agree that your contributions will be licensed under the
[MIT License](LICENSE) that covers the project.
