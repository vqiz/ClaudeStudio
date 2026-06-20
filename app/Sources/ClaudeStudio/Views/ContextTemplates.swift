import Foundation

/// Default starter templates ClaudeStudio offers for a project's context files,
/// so a new `AGENTS.md` / `CLAUDE.md` begins from a sensible, opinionated shape
/// instead of a blank page.
enum ContextTemplates {
    /// A default `AGENTS.md` — operating instructions for AI coding agents in a
    /// repository (overview, how to build/test, conventions, definition of done).
    static let agentsMd = """
    # AGENTS.md

    Operating guide for AI coding agents (Claude) working in this repository.
    Keep it short, concrete, and current — it is injected as context for every run.

    ## Project overview
    <!-- One paragraph: what this project is, its primary language/framework, and
         the layout of the important directories. -->

    ## Setup
    ```bash
    # Install dependencies / bootstrap the environment
    ```

    ## Build & run
    ```bash
    # Build the project and run it locally
    ```

    ## Test
    ```bash
    # Run the test suite — ALWAYS run this before declaring a task complete
    ```

    ## Conventions
    - **Code style:** <!-- formatter/linter, naming, file organisation -->
    - **Commits / branches:** <!-- message format, branch naming -->
    - **Do not touch:** <!-- generated files, vendored code, secrets -->

    ## How an agent should work here
    - Make small, reviewable changes; explain what changed and why.
    - Prefer the project's existing patterns over introducing new dependencies.
    - When unsure about scope or a destructive action, stop and ask.

    ## Definition of done
    - [ ] The change is scoped to the request
    - [ ] Tests pass and lint/format is clean
    - [ ] Behaviour is verified (not just compiled)
    """

    /// A default `CLAUDE.md` — high-level guidance and guardrails for Claude in
    /// this project (complements `AGENTS.md`).
    static let claudeMd = """
    # CLAUDE.md

    Project-specific guidance for Claude. (See also `AGENTS.md` for build/test
    commands and the definition of done.)

    ## Context
    <!-- What this project does, who it's for, and any domain terms worth knowing. -->

    ## Guardrails
    - <!-- e.g. never edit files under `dist/`; never commit secrets -->
    - <!-- e.g. ask before changing the public API or the database schema -->

    ## Preferences
    - <!-- Tone, verbosity, and how you like changes proposed/explained. -->
    """
}
