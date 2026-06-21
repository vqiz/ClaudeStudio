//! Real neural sentence-embedder backed by a small transformer
//! (sentence-transformers/all-MiniLM-L6-v2, 384-dim).
//!
//! Everything here is **on-device**: the model runs on the local CPU via
//! `candle` (pure-Rust, statically linked — no external ONNX dylib to bundle
//! or codesign). It performs zero network calls at inference time and never
//! touches any hosted API, so it consumes no Claude/Anthropic tokens and is
//! fully compatible with ClaudeStudio's CLI-only constraint.
//!
//! The model weights are fetched once, on first use, with the system `curl`
//! (no HTTP crate, no API key) into a caller-provided cache directory.
//!
//! This whole module is gated behind the non-default `neural` cargo feature so
//! the crate still builds with no ML stack at all; callers fall back to
//! [`crate::HashEmbedder`] when the model is unavailable.

use std::path::{Path, PathBuf};
use std::process::Command;

use anyhow::{anyhow, Context, Result};
use candle_core::{DType, Device, Tensor};
use candle_nn::VarBuilder;
use candle_transformers::models::bert::{BertModel, Config};
use tokenizers::Tokenizer;

use crate::Embedder;

/// HuggingFace model id whose files we mirror locally.
pub const MODEL_ID: &str = "sentence-transformers/all-MiniLM-L6-v2";

/// Stable short name used for the on-disk cache folder and the embedding tag
/// stored next to each vector (so vectors from different embedders are never
/// compared).
pub const MODEL_TAG: &str = "all-MiniLM-L6-v2";

/// Embedding dimensionality this model produces.
pub const MODEL_DIM: usize = 384;

/// Longest token sequence we feed the model. Messages are truncated to this;
/// 256 wordpieces is ample for transcript snippets and keeps inference fast.
const MAX_TOKENS: usize = 256;

/// The three files we need from the model repo.
const FILES: [&str; 3] = ["config.json", "tokenizer.json", "model.safetensors"];

/// Ensure the MiniLM model files exist under `dir`, downloading any that are
/// missing with `curl`. Returns the directory on success.
///
/// Network access here is a one-time fetch of an open-source model from the
/// HuggingFace CDN — unrelated to the Anthropic API and using only the system
/// `curl` binary, so it respects the CLI-only rule.
pub fn ensure_model(dir: &Path) -> Result<PathBuf> {
    std::fs::create_dir_all(dir)
        .with_context(|| format!("creating model cache dir {}", dir.display()))?;
    for file in FILES {
        let dest = dir.join(file);
        // A present-but-truncated file (e.g. an interrupted earlier download)
        // would poison loading, so treat empty files as missing.
        let ok = std::fs::metadata(&dest)
            .map(|m| m.len() > 0)
            .unwrap_or(false);
        if ok {
            continue;
        }
        let url = format!("https://huggingface.co/{MODEL_ID}/resolve/main/{file}");
        let tmp = dir.join(format!("{file}.part"));
        let status = Command::new("curl")
            .args([
                "-fSL", // fail on HTTP errors, follow redirects, show errors
                "--retry", "3", "-o",
            ])
            .arg(&tmp)
            .arg(&url)
            .status()
            .with_context(|| format!("spawning curl for {url}"))?;
        if !status.success() {
            let _ = std::fs::remove_file(&tmp);
            return Err(anyhow!("curl failed to download {url} (status {status})"));
        }
        std::fs::rename(&tmp, &dest).with_context(|| format!("finalizing download of {file}"))?;
    }
    Ok(dir.to_path_buf())
}

/// A neural sentence-embedder. Construct with [`BertEmbedder::load`].
pub struct BertEmbedder {
    model: BertModel,
    tokenizer: Tokenizer,
    device: Device,
    dim: usize,
}

impl BertEmbedder {
    /// Load the model from a directory containing `config.json`,
    /// `tokenizer.json` and `model.safetensors` (see [`ensure_model`]).
    pub fn load(model_dir: &Path) -> Result<Self> {
        let device = Device::Cpu;
        let config: Config = serde_json::from_slice(
            &std::fs::read(model_dir.join("config.json")).context("reading config.json")?,
        )
        .context("parsing bert config")?;
        let tokenizer = Tokenizer::from_file(model_dir.join("tokenizer.json"))
            .map_err(|e| anyhow!("loading tokenizer: {e}"))?;
        let weights = model_dir.join("model.safetensors");
        // SAFETY: mmap of a local, trusted file we just verified exists.
        #[allow(unsafe_code)]
        let vb = unsafe {
            VarBuilder::from_mmaped_safetensors(&[weights], DType::F32, &device)
                .context("mmapping safetensors weights")?
        };
        let model = BertModel::load(vb, &config).context("constructing bert model")?;
        Ok(Self {
            model,
            tokenizer,
            device,
            dim: config.hidden_size,
        })
    }

    /// Embed `text`, returning a `Result` so callers can distinguish a real
    /// failure from a zero vector.
    pub fn try_embed(&self, text: &str) -> Result<Vec<f32>> {
        let encoding = self
            .tokenizer
            .encode(text, true)
            .map_err(|e| anyhow!("tokenizing: {e}"))?;
        let mut ids: Vec<u32> = encoding.get_ids().to_vec();
        ids.truncate(MAX_TOKENS);
        if ids.is_empty() {
            return Ok(vec![0.0; self.dim]);
        }
        let n = ids.len();
        let input_ids = Tensor::new(ids.as_slice(), &self.device)?.unsqueeze(0)?;
        let token_type_ids = input_ids.zeros_like()?;
        let attention_mask = input_ids.ones_like()?;
        let hidden = self
            .model
            .forward(&input_ids, &token_type_ids, Some(&attention_mask))?;
        // Mean-pool over the token axis (batch size 1, no padding so the mask
        // is all ones) → a single sentence vector, then L2-normalize so cosine
        // similarity is a plain dot product.
        let summed = hidden.sum(1)?; // [1, hidden]
        let mean = (summed / (n as f64))?.squeeze(0)?;
        let mut v: Vec<f32> = mean.to_vec1()?;
        let norm = v.iter().map(|x| x * x).sum::<f32>().sqrt();
        if norm > 0.0 {
            for x in &mut v {
                *x /= norm;
            }
        }
        Ok(v)
    }
}

impl Embedder for BertEmbedder {
    fn embed(&self, text: &str) -> Vec<f32> {
        self.try_embed(text).unwrap_or_else(|_| vec![0.0; self.dim])
    }

    fn dim(&self) -> usize {
        self.dim
    }
}
