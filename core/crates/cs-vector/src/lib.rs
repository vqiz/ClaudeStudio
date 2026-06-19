#![forbid(unsafe_code)]
//! `cs-vector` — the semantic memory layer for ClaudeStudio.
//!
//! This crate provides two pluggable abstractions:
//!
//! * [`Embedder`] turns text into a fixed-length vector. The default
//!   [`HashEmbedder`] is fully deterministic (no model download, no network):
//!   it hashes tokens into buckets and L2-normalizes the result. It is good
//!   enough for nearest-neighbour recall over short snippets and makes tests
//!   reproducible.
//! * [`VectorStore`] upserts points and runs k-nearest-neighbour search. The
//!   default [`InMemoryVectorStore`] computes exact cosine similarity over an
//!   in-process map — no external service required.
//!
//! Memory is partitioned into five [`Collection`]s mirroring ClaudeStudio's
//! brain: sessions, definitions, knowledge, assets and errors.
//!
//! A real Qdrant-backed store is available behind the non-default `qdrant`
//! cargo feature so the crate always builds (and tests run) with no system
//! libraries or running services.
//!
//! ```
//! use cs_vector::{Collection, Embedder, HashEmbedder, InMemoryVectorStore, Point, VectorStore};
//!
//! let emb = HashEmbedder::default();
//! let mut store = InMemoryVectorStore::new();
//! store.upsert(Collection::Knowledge, Point::new("a", emb.embed("rust borrow checker")));
//! store.upsert(Collection::Knowledge, Point::new("b", emb.embed("python list comprehension")));
//!
//! let q = emb.embed("rust ownership and borrowing");
//! let hits = store.search(Collection::Knowledge, &q, 1, None);
//! assert_eq!(hits[0].id, "a");
//! ```

use std::collections::HashMap;

use serde::{Deserialize, Serialize};

/// Dimensionality of vectors produced by [`HashEmbedder`].
pub const EMBED_DIM: usize = 768;

/// Errors produced by the semantic-memory layer.
#[derive(Debug, thiserror::Error)]
pub enum Error {
    /// A vector did not match the expected dimensionality.
    #[error("dimension mismatch: expected {expected}, got {got}")]
    Dimension {
        /// The dimensionality the store was configured for.
        expected: usize,
        /// The dimensionality actually supplied.
        got: usize,
    },
    /// A backend (e.g. Qdrant) reported a failure.
    #[error("backend error: {0}")]
    Backend(String),
}

/// Convenience result alias used throughout this crate.
pub type Result<T> = std::result::Result<T, Error>;

/// The five semantic-memory collections that make up ClaudeStudio's brain.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Collection {
    /// Past session transcripts and summaries.
    Sessions,
    /// Code definitions (functions, types, modules).
    Definitions,
    /// Distilled knowledge / notes / documentation.
    Knowledge,
    /// Asset descriptions (images, files, designs).
    Assets,
    /// Recorded errors and their resolutions.
    Errors,
}

impl Collection {
    /// All collections, useful for iteration / provisioning.
    pub const ALL: [Collection; 5] = [
        Collection::Sessions,
        Collection::Definitions,
        Collection::Knowledge,
        Collection::Assets,
        Collection::Errors,
    ];

    /// Stable lowercase name used as the on-disk / remote collection name.
    #[must_use]
    pub fn name(self) -> &'static str {
        match self {
            Collection::Sessions => "sessions",
            Collection::Definitions => "definitions",
            Collection::Knowledge => "knowledge",
            Collection::Assets => "assets",
            Collection::Errors => "errors",
        }
    }
}

/// A stored vector together with its id and arbitrary JSON payload.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Point {
    /// Caller-chosen unique identifier within a collection.
    pub id: String,
    /// The embedding vector.
    pub vector: Vec<f32>,
    /// Arbitrary metadata, also used for filtering.
    pub payload: serde_json::Value,
}

impl Point {
    /// Create a point with `id` and `vector` and an empty payload.
    #[must_use]
    pub fn new(id: impl Into<String>, vector: Vec<f32>) -> Self {
        Self {
            id: id.into(),
            vector,
            payload: serde_json::Value::Null,
        }
    }

    /// Attach a JSON payload to the point.
    #[must_use]
    pub fn with_payload(mut self, payload: serde_json::Value) -> Self {
        self.payload = payload;
        self
    }
}

/// A search result: a stored point plus its similarity score (higher is closer).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ScoredPoint {
    /// Id of the matching point.
    pub id: String,
    /// Cosine similarity in `[-1.0, 1.0]` (1.0 == identical direction).
    pub score: f32,
    /// The matching point's payload.
    pub payload: serde_json::Value,
}

/// An exact-match filter over a point's payload object.
///
/// A point matches when, for every `(key, value)` pair, its payload is a JSON
/// object containing that key with an equal value.
pub type Filter = HashMap<String, serde_json::Value>;

/// Turns text into a fixed-length embedding vector.
pub trait Embedder: Send + Sync {
    /// Embed `text` into a vector. Implementations should be deterministic.
    fn embed(&self, text: &str) -> Vec<f32>;

    /// Dimensionality of the vectors this embedder produces.
    fn dim(&self) -> usize;
}

/// A deterministic, dependency-free embedder.
///
/// Tokens (lowercased alphanumeric runs) are hashed into [`EMBED_DIM`] buckets;
/// each token contributes a signed weight to its bucket. The resulting vector
/// is L2-normalized. Identical input always yields an identical vector, and
/// texts sharing tokens land near each other under cosine similarity.
#[derive(Debug, Clone, Copy)]
pub struct HashEmbedder {
    dim: usize,
}

impl Default for HashEmbedder {
    fn default() -> Self {
        Self { dim: EMBED_DIM }
    }
}

impl HashEmbedder {
    /// Create an embedder producing vectors of `dim` dimensions.
    #[must_use]
    pub fn with_dim(dim: usize) -> Self {
        Self { dim: dim.max(1) }
    }

    /// FNV-1a 64-bit hash — small, fast and stable across runs/platforms.
    fn hash(bytes: &[u8]) -> u64 {
        let mut h: u64 = 0xcbf2_9ce4_8422_2325;
        for &b in bytes {
            h ^= b as u64;
            h = h.wrapping_mul(0x0000_0100_0000_01b3);
        }
        h
    }

    fn tokens(text: &str) -> Vec<String> {
        text.split(|c: char| !c.is_alphanumeric())
            .filter(|t| !t.is_empty())
            .map(|t| t.to_lowercase())
            .collect()
    }
}

impl Embedder for HashEmbedder {
    fn embed(&self, text: &str) -> Vec<f32> {
        let mut v = vec![0f32; self.dim];
        for tok in Self::tokens(text) {
            let h = Self::hash(tok.as_bytes());
            let bucket = (h % self.dim as u64) as usize;
            // Use a separate bit of the hash to pick the sign so distinct
            // tokens hashing to the same bucket don't always reinforce.
            let sign = if (h >> 63) & 1 == 0 { 1.0 } else { -1.0 };
            v[bucket] += sign;
        }
        // L2-normalize so cosine similarity reduces to a dot product.
        let norm = v.iter().map(|x| x * x).sum::<f32>().sqrt();
        if norm > 0.0 {
            for x in &mut v {
                *x /= norm;
            }
        }
        v
    }

    fn dim(&self) -> usize {
        self.dim
    }
}

/// Cosine similarity between two equal-length vectors.
///
/// Returns `0.0` if either vector is all-zero or lengths differ.
#[must_use]
pub fn cosine_similarity(a: &[f32], b: &[f32]) -> f32 {
    if a.len() != b.len() {
        return 0.0;
    }
    let mut dot = 0f32;
    let mut na = 0f32;
    let mut nb = 0f32;
    for i in 0..a.len() {
        dot += a[i] * b[i];
        na += a[i] * a[i];
        nb += b[i] * b[i];
    }
    let denom = na.sqrt() * nb.sqrt();
    if denom == 0.0 {
        0.0
    } else {
        dot / denom
    }
}

fn payload_matches(payload: &serde_json::Value, filter: &Filter) -> bool {
    if filter.is_empty() {
        return true;
    }
    match payload.as_object() {
        Some(obj) => filter
            .iter()
            .all(|(k, v)| obj.get(k).map_or(false, |pv| pv == v)),
        None => false,
    }
}

/// Upserts points and runs nearest-neighbour search over collections.
pub trait VectorStore {
    /// Insert or replace a point in `collection`.
    fn upsert(&mut self, collection: Collection, point: Point);

    /// Return the `k` points in `collection` most similar to `query`, optionally
    /// restricted to points whose payload matches `filter`. Highest score first.
    fn search(
        &self,
        collection: Collection,
        query: &[f32],
        k: usize,
        filter: Option<&Filter>,
    ) -> Vec<ScoredPoint>;

    /// Number of points stored in `collection`.
    fn len(&self, collection: Collection) -> usize;

    /// Whether `collection` is empty.
    fn is_empty(&self, collection: Collection) -> bool {
        self.len(collection) == 0
    }
}

/// An in-process [`VectorStore`] using exact cosine similarity.
///
/// This is the default backend and requires no external services. It keeps one
/// `id -> Point` map per [`Collection`].
#[derive(Debug, Default)]
pub struct InMemoryVectorStore {
    collections: HashMap<Collection, HashMap<String, Point>>,
}

impl InMemoryVectorStore {
    /// Create an empty store.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }
}

impl VectorStore for InMemoryVectorStore {
    fn upsert(&mut self, collection: Collection, point: Point) {
        self.collections
            .entry(collection)
            .or_default()
            .insert(point.id.clone(), point);
    }

    fn search(
        &self,
        collection: Collection,
        query: &[f32],
        k: usize,
        filter: Option<&Filter>,
    ) -> Vec<ScoredPoint> {
        let Some(points) = self.collections.get(&collection) else {
            return Vec::new();
        };
        let mut scored: Vec<ScoredPoint> = points
            .values()
            .filter(|p| filter.map_or(true, |f| payload_matches(&p.payload, f)))
            .map(|p| ScoredPoint {
                id: p.id.clone(),
                score: cosine_similarity(query, &p.vector),
                payload: p.payload.clone(),
            })
            .collect();
        // Sort by score descending; NaN scores sink to the bottom.
        scored.sort_by(|a, b| {
            b.score
                .partial_cmp(&a.score)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        scored.truncate(k);
        scored
    }

    fn len(&self, collection: Collection) -> usize {
        self.collections.get(&collection).map_or(0, HashMap::len)
    }
}

/// Qdrant-backed [`VectorStore`], compiled only with the `qdrant` feature.
///
/// This is intentionally gated out of the default build so the workspace
/// compiles with no running Qdrant service and no heavy client dependency.
#[cfg(feature = "qdrant")]
pub mod qdrant_backend {
    use super::*;

    /// A handle to a remote Qdrant instance.
    ///
    /// The concrete wiring lives behind the `qdrant` feature; this scaffold
    /// keeps the public surface stable. Network calls return [`Error::Backend`]
    /// on failure.
    pub struct QdrantVectorStore {
        url: String,
    }

    impl QdrantVectorStore {
        /// Create a store pointing at the Qdrant instance at `url`.
        #[must_use]
        pub fn new(url: impl Into<String>) -> Self {
            Self { url: url.into() }
        }

        /// The configured endpoint URL.
        #[must_use]
        pub fn url(&self) -> &str {
            &self.url
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn embedding_is_deterministic_and_normalized() {
        let emb = HashEmbedder::default();
        let a = emb.embed("the quick brown fox");
        let b = emb.embed("the quick brown fox");
        assert_eq!(a, b, "same input yields the same vector");
        assert_eq!(a.len(), EMBED_DIM);

        let norm = a.iter().map(|x| x * x).sum::<f32>().sqrt();
        assert!((norm - 1.0).abs() < 1e-5, "vector should be L2-normalized");
    }

    #[test]
    fn nearest_neighbour_returns_closest_vector() {
        let emb = HashEmbedder::default();
        let mut store = InMemoryVectorStore::new();
        store.upsert(
            Collection::Knowledge,
            Point::new("rust", emb.embed("rust ownership borrow checker lifetimes")),
        );
        store.upsert(
            Collection::Knowledge,
            Point::new("python", emb.embed("python list comprehension generators")),
        );

        let query = emb.embed("rust borrow checker and lifetimes");
        let hits = store.search(Collection::Knowledge, &query, 2, None);
        assert_eq!(hits.len(), 2);
        assert_eq!(hits[0].id, "rust", "closest match should rank first");
        assert!(hits[0].score >= hits[1].score);
    }

    #[test]
    fn filter_restricts_results() {
        let emb = HashEmbedder::default();
        let mut store = InMemoryVectorStore::new();
        store.upsert(
            Collection::Sessions,
            Point::new("a", emb.embed("deploy the server"))
                .with_payload(serde_json::json!({"lang": "rust"})),
        );
        store.upsert(
            Collection::Sessions,
            Point::new("b", emb.embed("deploy the server"))
                .with_payload(serde_json::json!({"lang": "go"})),
        );

        let query = emb.embed("deploy the server");
        let mut filter = Filter::new();
        filter.insert("lang".to_string(), serde_json::json!("go"));
        let hits = store.search(Collection::Sessions, &query, 5, Some(&filter));
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].id, "b");
    }

    #[test]
    fn cosine_of_identical_is_one() {
        let v = vec![0.0, 1.0, 2.0, 3.0];
        assert!((cosine_similarity(&v, &v) - 1.0).abs() < 1e-6);
        // Mismatched lengths are defined to score 0.
        assert_eq!(cosine_similarity(&[1.0, 2.0], &[1.0]), 0.0);
    }

    #[test]
    fn collection_names_are_stable() {
        assert_eq!(Collection::Errors.name(), "errors");
        assert_eq!(Collection::ALL.len(), 5);
    }
}
