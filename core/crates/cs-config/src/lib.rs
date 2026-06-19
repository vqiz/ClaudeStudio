#![forbid(unsafe_code)]
//! # cs-config
//!
//! Application configuration and context assembly for ClaudeStudio.
//!
//! This crate is responsible for three things:
//!
//! 1. **[`AppConfig`]** — the typed model of `settings.json` (trust mode, daily
//!    budget, voice config, vector config). Loaded with [`AppConfig::load_or_default`].
//! 2. **Memory parsing** — reading the global `~/.claude/CLAUDE.md`, project
//!    `CLAUDE.md`, and cross-project memory markdown into plain text layers.
//! 3. **[`ContextAssembler`]** — computing the six-layer context budget, returning
//!    a per-layer token estimate so the orchestrator can decide what to include
//!    in a prompt window.
//!
//! It deliberately performs only filesystem reads and string processing; it never
//! talks to a network service. The vector-retrieval layer is represented as a
//! placeholder estimate that the caller fills in from the real vector store.

mod budget;
mod memory;

pub use budget::{ContextAssembler, ContextBudget, ContextLayer, LayerKind};
pub use memory::{estimate_tokens, parse_markdown_memory, read_markdown_memory, MemoryDoc};

use serde::{Deserialize, Serialize};
use std::path::Path;

/// Errors produced by configuration loading and context assembly.
#[derive(Debug, thiserror::Error)]
pub enum Error {
    /// An I/O error occurred while reading a config or memory file.
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),

    /// A settings file existed but could not be parsed as JSON.
    #[error("invalid settings json: {0}")]
    Json(#[from] serde_json::Error),
}

/// Convenience result type for this crate.
pub type Result<T> = std::result::Result<T, Error>;

/// Configuration for the voice assistant subsystem.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct VoiceConfig {
    /// Whether the voice assistant is enabled at all.
    pub enabled: bool,
    /// Wake phrase that activates voice capture (e.g. `"hey claude"`).
    pub wake_word: String,
    /// BCP-47 language tag for speech recognition, e.g. `"en-US"`.
    pub language: String,
    /// Identifier of the text-to-speech voice to use for responses.
    pub tts_voice: String,
}

impl Default for VoiceConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            wake_word: "hey claude".to_string(),
            language: "en-US".to_string(),
            tts_voice: "system".to_string(),
        }
    }
}

/// Configuration for the semantic-memory vector store.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct VectorConfig {
    /// Embedding dimensionality the store expects.
    pub dimensions: usize,
    /// Number of nearest neighbours to retrieve per query by default.
    pub top_k: usize,
    /// Optional URL of an external Qdrant instance. When `None`, the in-memory
    /// cosine-similarity backend (see `cs-vector`) is used.
    pub qdrant_url: Option<String>,
    /// Name of the collection used to store ClaudeStudio embeddings.
    pub collection: String,
}

impl Default for VectorConfig {
    fn default() -> Self {
        Self {
            dimensions: 1536,
            top_k: 8,
            qdrant_url: None,
            collection: "claudestudio".to_string(),
        }
    }
}

/// The top-level application configuration, mirroring `settings.json`.
///
/// Unknown fields are ignored on load and missing fields fall back to
/// [`Default`], so older and newer settings files remain compatible.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct AppConfig {
    /// How autonomously ClaudeStudio may act.
    pub trust_mode: cs_types::TrustMode,
    /// Default model tier for new sessions.
    pub default_model: cs_types::ModelTier,
    /// Soft daily spend cap in US dollars. `0.0` means "no limit".
    pub daily_budget_usd: f64,
    /// Total token budget used when assembling prompt context.
    pub context_token_budget: usize,
    /// Voice assistant configuration.
    pub voice: VoiceConfig,
    /// Vector / semantic-memory configuration.
    pub vector: VectorConfig,
}

impl Default for AppConfig {
    fn default() -> Self {
        Self {
            trust_mode: cs_types::TrustMode::default(),
            default_model: cs_types::ModelTier::default(),
            daily_budget_usd: 10.0,
            context_token_budget: 180_000,
            voice: VoiceConfig::default(),
            vector: VectorConfig::default(),
        }
    }
}

impl AppConfig {
    /// File name used for the on-disk settings within a config directory.
    pub const FILE_NAME: &'static str = "settings.json";

    /// Load configuration from `<dir>/settings.json`, falling back to defaults.
    ///
    /// This never fails: if the file is missing, unreadable, or malformed, a
    /// warning is logged and [`AppConfig::default`] is returned. Use
    /// [`AppConfig::try_load`] when you need to distinguish those cases.
    pub fn load_or_default(dir: &Path) -> Self {
        match Self::try_load(dir) {
            Ok(Some(cfg)) => cfg,
            Ok(None) => {
                tracing::debug!(?dir, "no settings.json found; using defaults");
                Self::default()
            }
            Err(err) => {
                tracing::warn!(%err, ?dir, "failed to load settings.json; using defaults");
                Self::default()
            }
        }
    }

    /// Attempt to load configuration, returning `Ok(None)` if the file is absent.
    pub fn try_load(dir: &Path) -> Result<Option<Self>> {
        let path = dir.join(Self::FILE_NAME);
        if !path.exists() {
            return Ok(None);
        }
        let raw = std::fs::read_to_string(&path)?;
        let cfg: AppConfig = serde_json::from_str(&raw)?;
        Ok(Some(cfg))
    }

    /// Serialize and write this configuration to `<dir>/settings.json`,
    /// creating the directory if needed.
    pub fn save(&self, dir: &Path) -> Result<()> {
        std::fs::create_dir_all(dir)?;
        let path = dir.join(Self::FILE_NAME);
        let json = serde_json::to_string_pretty(self)?;
        std::fs::write(path, json)?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_config_is_standard_trust() {
        let cfg = AppConfig::default();
        assert_eq!(cfg.trust_mode, cs_types::TrustMode::Standard);
        assert_eq!(cfg.default_model, cs_types::ModelTier::Sonnet);
        assert!(cfg.context_token_budget > 0);
        assert!(!cfg.voice.enabled);
        assert_eq!(cfg.vector.dimensions, 1536);
    }

    #[test]
    fn load_or_default_falls_back_when_missing() {
        // A path that (essentially certainly) has no settings.json.
        let dir = std::env::temp_dir().join("cs-config-nonexistent-xyz");
        let cfg = AppConfig::load_or_default(&dir);
        assert_eq!(cfg, AppConfig::default());
    }

    #[test]
    fn roundtrip_save_and_load() {
        let dir = std::env::temp_dir().join(format!("cs-config-test-{}", std::process::id()));
        let cfg = AppConfig {
            trust_mode: cs_types::TrustMode::Auto,
            daily_budget_usd: 42.5,
            ..AppConfig::default()
        };
        cfg.save(&dir).expect("save");
        let loaded = AppConfig::load_or_default(&dir);
        assert_eq!(loaded.trust_mode, cs_types::TrustMode::Auto);
        assert_eq!(loaded.daily_budget_usd, 42.5);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn partial_json_uses_defaults_for_missing_fields() {
        let cfg: AppConfig = serde_json::from_str(r#"{"trust_mode":"yolo"}"#).unwrap();
        assert_eq!(cfg.trust_mode, cs_types::TrustMode::Yolo);
        // Everything else should be defaulted.
        assert_eq!(cfg.default_model, cs_types::ModelTier::Sonnet);
        assert_eq!(cfg.vector, VectorConfig::default());
    }
}
