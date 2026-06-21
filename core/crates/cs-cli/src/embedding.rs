//! Construction of the semantic-search embedder shared by the core and the
//! built-in MCP server.
//!
//! Two entry points with deliberately different policies:
//!
//! * [`ensure_and_load`] — used by the long-lived core. May download the model
//!   (once) before loading it, so it is run on a background thread and the
//!   socket never waits on it.
//! * [`load_cached`] — used by the short-lived `mcp` subprocess that Claude
//!   spawns. It loads the model *only if already cached* and never downloads,
//!   so the MCP handshake is never blocked by a 90 MB fetch. Until the core has
//!   populated the cache, MCP search transparently falls back to FTS.
//!
//! Both fall back to the dependency-free [`HashEmbedder`] when the neural model
//! is unavailable, so search always works.

use std::path::Path;
use std::sync::Arc;

use cs_vector::neural::{self, BertEmbedder};
use cs_vector::{Embedder, HashEmbedder};

/// Model tag stored next to vectors produced by the [`HashEmbedder`] fallback,
/// so they are never compared against neural vectors.
pub const HASH_TAG: &str = "hash-768";

/// Directory under the state dir where model weights are cached.
fn model_dir(state_dir: &Path) -> std::path::PathBuf {
    state_dir.join("models").join(neural::MODEL_TAG)
}

/// Trim `text` to a short single-line snippet for storage alongside a vector.
#[must_use]
pub fn snippet(text: &str) -> String {
    let collapsed = text.split_whitespace().collect::<Vec<_>>().join(" ");
    if collapsed.chars().count() <= 160 {
        collapsed
    } else {
        let mut s: String = collapsed.chars().take(160).collect();
        s.push('…');
        s
    }
}

/// Ensure the neural model is present (downloading once if needed) and load it.
/// Falls back to the hash embedder on any failure. Slow on first run — call off
/// the hot path.
#[must_use]
pub fn ensure_and_load(state_dir: &Path) -> (Arc<dyn Embedder>, String) {
    let dir = model_dir(state_dir);
    match neural::ensure_model(&dir).and_then(|d| BertEmbedder::load(&d)) {
        Ok(model) => {
            tracing::info!(
                model = neural::MODEL_TAG,
                dim = model.dim(),
                "semantic embedder ready"
            );
            (Arc::new(model), neural::MODEL_TAG.to_string())
        }
        Err(e) => {
            tracing::warn!(error = %e, "neural embedder unavailable; using hash fallback");
            (Arc::new(HashEmbedder::default()), HASH_TAG.to_string())
        }
    }
}

/// Load the neural model only if its files are already cached; never downloads.
/// Falls back to the hash embedder otherwise. Safe to call at process start.
#[must_use]
pub fn load_cached(state_dir: &Path) -> (Arc<dyn Embedder>, String) {
    let dir = model_dir(state_dir);
    if dir.join("model.safetensors").exists() {
        if let Ok(model) = BertEmbedder::load(&dir) {
            return (Arc::new(model), neural::MODEL_TAG.to_string());
        }
    }
    (Arc::new(HashEmbedder::default()), HASH_TAG.to_string())
}
