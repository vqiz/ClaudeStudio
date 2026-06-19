//! Parsing of CLAUDE.md and cross-project memory markdown into plain layers.
//!
//! ClaudeStudio reads a handful of markdown documents — the global
//! `~/.claude/CLAUDE.md`, project-level `CLAUDE.md`, and free-form memory notes —
//! and folds them into the prompt context. This module turns raw markdown into a
//! lightweight [`MemoryDoc`] with a coarse token estimate.

use std::path::Path;

/// A parsed memory/markdown document plus a coarse token estimate.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MemoryDoc {
    /// The document's textual content (markdown preserved as-is).
    pub content: String,
    /// Estimated token count (~4 chars/token heuristic).
    pub estimated_tokens: usize,
    /// Number of markdown headings discovered (`#`-prefixed lines).
    pub heading_count: usize,
}

impl MemoryDoc {
    /// Whether this document carries no usable content.
    pub fn is_empty(&self) -> bool {
        self.content.trim().is_empty()
    }
}

/// Approximate token count for a string using the common ~4-chars-per-token
/// heuristic. This is intentionally cheap; exact tokenization happens elsewhere.
pub fn estimate_tokens(text: &str) -> usize {
    // Count over characters rather than bytes so multi-byte text isn't inflated.
    let chars = text.chars().count();
    chars.div_ceil(4)
}

/// Parse a markdown memory string into a [`MemoryDoc`].
///
/// Heading detection is line-based: a line is a heading if, after trimming
/// leading whitespace, it starts with `#`.
pub fn parse_markdown_memory(raw: &str) -> MemoryDoc {
    let heading_count = raw
        .lines()
        .filter(|line| line.trim_start().starts_with('#'))
        .count();
    MemoryDoc {
        estimated_tokens: estimate_tokens(raw),
        heading_count,
        content: raw.to_string(),
    }
}

/// Read a markdown memory document from disk, returning an empty [`MemoryDoc`]
/// when the file is absent or unreadable.
pub fn read_markdown_memory(path: &Path) -> MemoryDoc {
    match std::fs::read_to_string(path) {
        Ok(raw) => parse_markdown_memory(&raw),
        Err(_) => parse_markdown_memory(""),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn estimate_tokens_is_roughly_quarter_chars() {
        assert_eq!(estimate_tokens(""), 0);
        assert_eq!(estimate_tokens("abcd"), 1);
        assert_eq!(estimate_tokens("abcde"), 2); // ceil(5/4)
    }

    #[test]
    fn parse_counts_headings_and_tokens() {
        let md = "# Title\nsome body text here\n## Section\nmore";
        let doc = parse_markdown_memory(md);
        assert_eq!(doc.heading_count, 2);
        assert!(doc.estimated_tokens > 0);
        assert!(!doc.is_empty());
    }

    #[test]
    fn empty_markdown_is_empty_doc() {
        let doc = parse_markdown_memory("   \n  ");
        assert!(doc.is_empty());
        assert_eq!(doc.heading_count, 0);
    }
}
