//! Manual end-to-end check of the neural embedder: downloads the model (once,
//! into the real app cache so it pre-warms it), then ranks a few documents
//! against a query that shares almost no keywords with the right answer.
//!
//! Run: `cargo run -p cs-vector --features neural --example semantic_demo`

use cs_vector::cosine_similarity;
use cs_vector::neural::{ensure_model, BertEmbedder, MODEL_TAG};
use cs_vector::Embedder;
use std::path::PathBuf;

fn main() -> anyhow::Result<()> {
    let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
    let dir = PathBuf::from(home)
        .join(".claudestudio/models")
        .join(MODEL_TAG);
    eprintln!("ensuring model in {} …", dir.display());
    ensure_model(&dir)?;
    let emb = BertEmbedder::load(&dir)?;
    eprintln!("loaded {MODEL_TAG} (dim {})", emb.dim());

    let docs = [
        "We debugged the Unix domain socket handshake between the core and the app",
        "The chocolate cake recipe needs two eggs and a cup of sugar",
        "Renamed the SwiftUI archive view and added a resume button",
    ];
    let dvecs: Vec<Vec<f32>> = docs.iter().map(|d| emb.embed(d)).collect();

    // Note: almost no word overlap with doc 0 ("socket/handshake/core/app").
    let query = "fixing the inter-process connection setup";
    let qv = emb.embed(query);

    let mut scored: Vec<(f32, &str)> = docs
        .iter()
        .zip(&dvecs)
        .map(|(d, v)| (cosine_similarity(&qv, v), *d))
        .collect();
    scored.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap());

    println!("\nquery: {query:?}\n");
    for (score, doc) in &scored {
        println!("  {score:.3}  {doc}");
    }
    println!(
        "\ntop match is doc 0 (the IPC/socket one): {}",
        scored[0].1 == docs[0]
    );
    Ok(())
}
