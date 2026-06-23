//! Echte-Embedding-Bruecke fuer die vector-db-Probe (test-harness).
//!
//! Liest auf stdin EINE JSON-Zeile `{"texts": ["...", "..."]}` und schreibt auf
//! stdout EINE JSON-Zeile `{"model": "all-MiniLM-L6-v2", "dim": 384,
//! "vectors": [[...], ...]}`. Es wird der EXAKT gleiche `BertEmbedder` (candle,
//! all-MiniLM-L6-v2) geladen, den auch der Core in Produktion benutzt — kein
//! Mock. Das Modell muss bereits im App-Cache liegen (kein Download hier).
//!
//! Aufruf:
//!   echo '{"texts":["hello"]}' | \
//!     cargo run -q -p cs-vector --features neural --example embed_cli

use std::io::{Read, Write};
use std::path::PathBuf;

use cs_vector::neural::{BertEmbedder, MODEL_DIM, MODEL_TAG};
use cs_vector::Embedder;

fn main() -> anyhow::Result<()> {
    // Modellverzeichnis im echten App-Cache; NICHT herunterladen, nur laden.
    let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
    let dir = PathBuf::from(home)
        .join(".claudestudio/models")
        .join(MODEL_TAG);
    let emb = BertEmbedder::load(&dir)?;

    // Eingabe einlesen.
    let mut buf = String::new();
    std::io::stdin().read_to_string(&mut buf)?;
    let input: serde_json::Value = serde_json::from_str(&buf)?;
    let texts: Vec<String> = input
        .get("texts")
        .and_then(|v| v.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|x| x.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default();

    // Echte Vektoren erzeugen.
    let vectors: Vec<Vec<f32>> = texts.iter().map(|t| emb.embed(t)).collect();

    let out = serde_json::json!({
        "model": MODEL_TAG,
        "dim": emb.dim(),
        "declared_dim": MODEL_DIM,
        "vectors": vectors,
    });
    let mut stdout = std::io::stdout();
    writeln!(stdout, "{out}")?;
    Ok(())
}
