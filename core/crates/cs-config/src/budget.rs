//! The six-layer context budget.
//!
//! ClaudeStudio composes a prompt from six prioritized layers. Higher-priority
//! layers are filled first; once the configured token budget is exhausted, the
//! remaining layers are reported as truncated. The [`ContextAssembler`] computes
//! per-layer estimates and an overall [`ContextBudget`] without performing any
//! network calls — the vector-retrieval layer is supplied as a caller-provided
//! estimate (a placeholder filled in from the real vector store).

use crate::memory::{estimate_tokens, MemoryDoc};

/// The six context layers, ordered from highest to lowest priority.
///
/// Discriminants double as the fill order: layer `0` is filled first.
#[derive(Copy, Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
#[repr(u8)]
pub enum LayerKind {
    /// Global `~/.claude/CLAUDE.md` — user-wide standing instructions.
    GlobalClaudeMd = 0,
    /// Cross-project semantic memory carried between repositories.
    CrossProjectMemory = 1,
    /// The current project's `CLAUDE.md`.
    ProjectClaudeMd = 2,
    /// Results retrieved from the vector store for the active query.
    VectorRetrieval = 3,
    /// Active definitions: open files, selected symbols, current task.
    ActiveDefinitions = 4,
    /// Worktree-level overrides that win over project defaults.
    WorktreeOverride = 5,
}

impl LayerKind {
    /// All six layers in priority (fill) order.
    pub const ALL: [LayerKind; 6] = [
        LayerKind::GlobalClaudeMd,
        LayerKind::CrossProjectMemory,
        LayerKind::ProjectClaudeMd,
        LayerKind::VectorRetrieval,
        LayerKind::ActiveDefinitions,
        LayerKind::WorktreeOverride,
    ];

    /// A short human-readable label for this layer.
    pub fn label(self) -> &'static str {
        match self {
            LayerKind::GlobalClaudeMd => "global_claude_md",
            LayerKind::CrossProjectMemory => "cross_project_memory",
            LayerKind::ProjectClaudeMd => "project_claude_md",
            LayerKind::VectorRetrieval => "vector_retrieval",
            LayerKind::ActiveDefinitions => "active_definitions",
            LayerKind::WorktreeOverride => "worktree_override",
        }
    }
}

/// A single layer's contribution to the assembled context.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ContextLayer {
    /// Which layer this is.
    pub kind: LayerKind,
    /// Estimated tokens the layer *wants* to contribute.
    pub requested_tokens: usize,
    /// Estimated tokens the layer is *allowed* to contribute after budgeting.
    pub granted_tokens: usize,
}

impl ContextLayer {
    /// Whether the layer was cut down to fit the remaining budget.
    pub fn was_truncated(&self) -> bool {
        self.granted_tokens < self.requested_tokens
    }
}

/// The result of assembling all six layers against a token budget.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ContextBudget {
    /// The configured total token budget.
    pub total_budget: usize,
    /// Per-layer estimates in priority order.
    pub layers: Vec<ContextLayer>,
}

impl ContextBudget {
    /// Sum of all granted tokens across layers.
    pub fn granted_total(&self) -> usize {
        self.layers.iter().map(|l| l.granted_tokens).sum()
    }

    /// Sum of all requested tokens across layers (before budgeting).
    pub fn requested_total(&self) -> usize {
        self.layers.iter().map(|l| l.requested_tokens).sum()
    }

    /// Remaining headroom in the budget after granting.
    pub fn remaining(&self) -> usize {
        self.total_budget.saturating_sub(self.granted_total())
    }

    /// Look up a single layer by kind.
    pub fn layer(&self, kind: LayerKind) -> Option<&ContextLayer> {
        self.layers.iter().find(|l| l.kind == kind)
    }
}

/// Computes the six-layer [`ContextBudget`] from per-layer inputs.
///
/// Inputs are token *requests* per layer; the assembler grants them greedily in
/// priority order until the total budget is exhausted, after which subsequent
/// layers are granted whatever fits (possibly zero).
#[derive(Clone, Debug)]
pub struct ContextAssembler {
    total_budget: usize,
    requests: [usize; 6],
}

impl ContextAssembler {
    /// Create an assembler with the given total token budget and all layer
    /// requests initialized to zero.
    pub fn new(total_budget: usize) -> Self {
        Self {
            total_budget,
            requests: [0; 6],
        }
    }

    /// Set the requested token estimate for a layer, returning `self` for
    /// builder-style chaining.
    pub fn with_layer(mut self, kind: LayerKind, requested_tokens: usize) -> Self {
        self.requests[kind as usize] = requested_tokens;
        self
    }

    /// Convenience: set a layer's request from a parsed [`MemoryDoc`].
    pub fn with_memory(self, kind: LayerKind, doc: &MemoryDoc) -> Self {
        self.with_layer(kind, doc.estimated_tokens)
    }

    /// Convenience: set a layer's request by estimating tokens from raw text.
    pub fn with_text(self, kind: LayerKind, text: &str) -> Self {
        self.with_layer(kind, estimate_tokens(text))
    }

    /// Compute the final [`ContextBudget`], granting tokens greedily in
    /// [`LayerKind::ALL`] (priority) order.
    pub fn assemble(&self) -> ContextBudget {
        let mut remaining = self.total_budget;
        let mut layers = Vec::with_capacity(6);
        for kind in LayerKind::ALL {
            let requested = self.requests[kind as usize];
            let granted = requested.min(remaining);
            remaining -= granted;
            layers.push(ContextLayer {
                kind,
                requested_tokens: requested,
                granted_tokens: granted,
            });
        }
        ContextBudget {
            total_budget: self.total_budget,
            layers,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn assemble_fits_everything_within_budget() {
        let budget = ContextAssembler::new(1000)
            .with_layer(LayerKind::GlobalClaudeMd, 100)
            .with_layer(LayerKind::ProjectClaudeMd, 200)
            .with_layer(LayerKind::VectorRetrieval, 300)
            .assemble();
        assert_eq!(budget.granted_total(), 600);
        assert_eq!(budget.remaining(), 400);
        assert!(!budget
            .layer(LayerKind::ProjectClaudeMd)
            .unwrap()
            .was_truncated());
    }

    #[test]
    fn lower_priority_layers_are_truncated_first() {
        // Total 250: global(100)+cross(100) consume 200, project gets 50 of 100.
        let budget = ContextAssembler::new(250)
            .with_layer(LayerKind::GlobalClaudeMd, 100)
            .with_layer(LayerKind::CrossProjectMemory, 100)
            .with_layer(LayerKind::ProjectClaudeMd, 100)
            .with_layer(LayerKind::VectorRetrieval, 100)
            .assemble();
        assert_eq!(
            budget
                .layer(LayerKind::GlobalClaudeMd)
                .unwrap()
                .granted_tokens,
            100
        );
        assert_eq!(
            budget
                .layer(LayerKind::ProjectClaudeMd)
                .unwrap()
                .granted_tokens,
            50
        );
        assert!(budget
            .layer(LayerKind::ProjectClaudeMd)
            .unwrap()
            .was_truncated());
        assert_eq!(
            budget
                .layer(LayerKind::VectorRetrieval)
                .unwrap()
                .granted_tokens,
            0
        );
        assert_eq!(budget.remaining(), 0);
    }

    #[test]
    fn all_six_layers_always_present() {
        let budget = ContextAssembler::new(10).assemble();
        assert_eq!(budget.layers.len(), 6);
        for kind in LayerKind::ALL {
            assert!(budget.layer(kind).is_some());
        }
    }
}
