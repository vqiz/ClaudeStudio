#![forbid(unsafe_code)]
//! # cs-hooks
//!
//! The hook engine for ClaudeStudio. Hooks let users attach shell commands to
//! lifecycle moments of a Claude Code session.
//!
//! - [`HookKind`] enumerates the lifecycle points — the standard Claude Code
//!   hooks (`PreToolUse`, `PostToolUse`, `Notification`, `Stop`,
//!   `SubagentStop`) plus ClaudeStudio's own worktree events
//!   (`WorktreeCreate`, `WorktreeRemove`).
//! - [`Matcher`] decides whether a hook applies to a given [`HookContext`]
//!   (matching on tool name, exit code, or output substring).
//! - [`HookAction`] is the command to run when a hook fires.
//! - [`HookEngine::matching`] returns every hook whose kind and matcher fit the
//!   provided context.
//!
//! This crate is pure data + matching logic: it does not execute commands
//! itself, so it builds and tests with no external dependencies.

use serde::{Deserialize, Serialize};
use thiserror::Error;

/// Errors produced by the hook engine.
#[derive(Debug, Error)]
pub enum Error {
    /// A hook definition could not be parsed.
    #[error("invalid hook definition: {0}")]
    Invalid(String),
}

/// Convenient result alias for this crate.
pub type Result<T> = std::result::Result<T, Error>;

/// The lifecycle point at which a hook may fire.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "PascalCase")]
pub enum HookKind {
    /// Before a tool is invoked.
    PreToolUse,
    /// After a tool returns.
    PostToolUse,
    /// When a notification is emitted.
    Notification,
    /// When the main agent stops.
    Stop,
    /// When a subagent stops.
    SubagentStop,
    /// When a ClaudeStudio worktree is created.
    WorktreeCreate,
    /// When a ClaudeStudio worktree is removed.
    WorktreeRemove,
}

/// Conditions a hook matches against. All present conditions must hold (logical
/// AND); an all-`None` matcher matches everything for its kind.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Matcher {
    /// Match when the tool name equals this exactly.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tool_name_equals: Option<String>,
    /// Match when the tool name contains this substring.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub tool_name_contains: Option<String>,
    /// Match when the exit code equals this value.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub exit_code: Option<i32>,
    /// Match when the output contains this substring.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub output_contains: Option<String>,
}

impl Matcher {
    /// Whether this matcher is satisfied by the given context.
    pub fn matches(&self, ctx: &HookContext) -> bool {
        if let Some(want) = &self.tool_name_equals {
            if ctx.tool_name.as_deref() != Some(want.as_str()) {
                return false;
            }
        }
        if let Some(sub) = &self.tool_name_contains {
            match &ctx.tool_name {
                Some(name) if name.contains(sub) => {}
                _ => return false,
            }
        }
        if let Some(code) = self.exit_code {
            if ctx.exit_code != Some(code) {
                return false;
            }
        }
        if let Some(sub) = &self.output_contains {
            match &ctx.output {
                Some(out) if out.contains(sub) => {}
                _ => return false,
            }
        }
        true
    }
}

/// The action a hook runs when it fires.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct HookAction {
    /// The shell command line to execute.
    pub command: String,
}

/// A single configured hook.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Hook {
    /// The lifecycle point this hook attaches to.
    pub kind: HookKind,
    /// The matcher gating the hook.
    #[serde(default)]
    pub matcher: Matcher,
    /// The action to run.
    pub action: HookAction,
}

/// The runtime context evaluated against [`Matcher`]s.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct HookContext {
    /// The lifecycle point currently being evaluated.
    pub kind: Option<HookKind>,
    /// The tool involved, if any.
    pub tool_name: Option<String>,
    /// The exit code of a finished tool, if any.
    pub exit_code: Option<i32>,
    /// The captured output, if any.
    pub output: Option<String>,
}

impl HookContext {
    /// Build a context for a given hook kind.
    pub fn for_kind(kind: HookKind) -> Self {
        Self {
            kind: Some(kind),
            ..Default::default()
        }
    }

    /// Builder: set the tool name.
    pub fn tool(mut self, name: impl Into<String>) -> Self {
        self.tool_name = Some(name.into());
        self
    }

    /// Builder: set the exit code.
    pub fn exit(mut self, code: i32) -> Self {
        self.exit_code = Some(code);
        self
    }

    /// Builder: set the output.
    pub fn output(mut self, out: impl Into<String>) -> Self {
        self.output = Some(out.into());
        self
    }
}

/// Holds a collection of hooks and resolves which fire for a given context.
#[derive(Clone, Debug, Default)]
pub struct HookEngine {
    hooks: Vec<Hook>,
}

impl HookEngine {
    /// Create an empty engine.
    pub fn new() -> Self {
        Self::default()
    }

    /// Construct an engine from a list of hooks.
    pub fn with_hooks(hooks: Vec<Hook>) -> Self {
        Self { hooks }
    }

    /// Register a hook.
    pub fn register(&mut self, hook: Hook) {
        self.hooks.push(hook);
    }

    /// All hooks whose [`HookKind`] is `kind` and whose matcher is satisfied by
    /// `ctx`, in registration order.
    pub fn matching(&self, kind: HookKind, ctx: &HookContext) -> Vec<&Hook> {
        self.hooks
            .iter()
            .filter(|h| h.kind == kind && h.matcher.matches(ctx))
            .collect()
    }

    /// Number of registered hooks.
    pub fn len(&self) -> usize {
        self.hooks.len()
    }

    /// Whether no hooks are registered.
    pub fn is_empty(&self) -> bool {
        self.hooks.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn matcher_matches_on_tool_name() {
        let m = Matcher {
            tool_name_equals: Some("Bash".into()),
            ..Default::default()
        };
        assert!(m.matches(&HookContext::for_kind(HookKind::PreToolUse).tool("Bash")));
        assert!(!m.matches(&HookContext::for_kind(HookKind::PreToolUse).tool("Edit")));
    }

    #[test]
    fn matcher_combines_conditions_with_and() {
        let m = Matcher {
            tool_name_contains: Some("Write".into()),
            exit_code: Some(0),
            ..Default::default()
        };
        let ctx = HookContext::for_kind(HookKind::PostToolUse)
            .tool("WriteFile")
            .exit(0);
        assert!(m.matches(&ctx));
        // Wrong exit code fails the AND.
        let ctx2 = HookContext::for_kind(HookKind::PostToolUse)
            .tool("WriteFile")
            .exit(1);
        assert!(!m.matches(&ctx2));
    }

    #[test]
    fn empty_matcher_matches_everything() {
        let m = Matcher::default();
        assert!(m.matches(&HookContext::for_kind(HookKind::Stop)));
    }

    #[test]
    fn engine_filters_by_kind_and_matcher() {
        let engine = HookEngine::with_hooks(vec![
            Hook {
                kind: HookKind::PreToolUse,
                matcher: Matcher {
                    tool_name_equals: Some("Bash".into()),
                    ..Default::default()
                },
                action: HookAction {
                    command: "echo pre".into(),
                },
            },
            Hook {
                kind: HookKind::PostToolUse,
                matcher: Matcher::default(),
                action: HookAction {
                    command: "echo post".into(),
                },
            },
        ]);
        let hits = engine.matching(
            HookKind::PreToolUse,
            &HookContext::for_kind(HookKind::PreToolUse).tool("Bash"),
        );
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].action.command, "echo pre");
        // PostToolUse hook should not appear under PreToolUse.
        let none = engine.matching(
            HookKind::PreToolUse,
            &HookContext::for_kind(HookKind::PreToolUse).tool("Edit"),
        );
        assert!(none.is_empty());
    }

    #[test]
    fn hook_definition_serde_round_trip() {
        let hook = Hook {
            kind: HookKind::PostToolUse,
            matcher: Matcher {
                output_contains: Some("error".into()),
                ..Default::default()
            },
            action: HookAction {
                command: "notify-send failed".into(),
            },
        };
        let json = serde_json::to_string(&hook).unwrap();
        let back: Hook = serde_json::from_str(&json).unwrap();
        assert_eq!(hook, back);
        // Worktree kinds serialize in PascalCase.
        let wt = serde_json::to_string(&HookKind::WorktreeCreate).unwrap();
        assert_eq!(wt, "\"WorktreeCreate\"");
    }
}
