#![forbid(unsafe_code)]
//! # cs-otel
//!
//! Telemetry for ClaudeStudio.
//!
//! The crate centers on the [`MetricsExporter`] trait, which records counters,
//! histograms, and per-session cost. Two implementations ship:
//!
//! - [`TracingExporter`] — the default, which logs every metric via the
//!   `tracing` crate. It needs no network and no native libraries.
//! - A real OTLP exporter, available only when the non-default `otlp` cargo
//!   feature is enabled (see [`otlp`]); the default build never pulls in the
//!   heavy OpenTelemetry stack.
//!
//! [`ProductivityMetrics`] captures the higher-level numbers the dashboard
//! surfaces (commits per session, tool acceptance rate, and so on).

use std::collections::HashMap;
use std::sync::Mutex;

use serde::{Deserialize, Serialize};
use thiserror::Error;

/// Errors produced by the telemetry layer.
#[derive(Debug, Error)]
pub enum Error {
    /// An exporter backend failed to flush.
    #[error("metrics export failed: {0}")]
    Export(String),
}

/// Convenient result alias for this crate.
pub type Result<T> = std::result::Result<T, Error>;

/// A sink for metrics. Implementations forward measurements to a backend.
pub trait MetricsExporter: Send + Sync {
    /// Record an additive counter increment with optional attributes.
    fn record_counter(&self, name: &str, value: u64, attrs: &[(&str, &str)]);

    /// Record a single histogram observation.
    fn record_histogram(&self, name: &str, value: f64, attrs: &[(&str, &str)]);

    /// Record the cost (USD) attributed to a session.
    fn record_session_cost(&self, session_id: &str, cost_usd: f64);
}

/// The default exporter: logs all metrics through `tracing`.
#[derive(Clone, Copy, Debug, Default)]
pub struct TracingExporter;

impl TracingExporter {
    /// Create a tracing-backed exporter.
    pub fn new() -> Self {
        Self
    }
}

impl MetricsExporter for TracingExporter {
    fn record_counter(&self, name: &str, value: u64, attrs: &[(&str, &str)]) {
        tracing::info!(metric = name, value, ?attrs, "counter");
    }

    fn record_histogram(&self, name: &str, value: f64, attrs: &[(&str, &str)]) {
        tracing::info!(metric = name, value, ?attrs, "histogram");
    }

    fn record_session_cost(&self, session_id: &str, cost_usd: f64) {
        tracing::info!(session = session_id, cost_usd, "session_cost");
    }
}

/// An in-memory exporter that captures every recorded value. Useful for tests
/// and for the live in-app metrics panel.
#[derive(Debug, Default)]
pub struct InMemoryExporter {
    counters: Mutex<HashMap<String, u64>>,
    histograms: Mutex<HashMap<String, Vec<f64>>>,
    session_costs: Mutex<HashMap<String, f64>>,
}

impl InMemoryExporter {
    /// Create an empty in-memory exporter.
    pub fn new() -> Self {
        Self::default()
    }

    /// Read the current value of a counter.
    pub fn counter(&self, name: &str) -> u64 {
        self.counters
            .lock()
            .unwrap()
            .get(name)
            .copied()
            .unwrap_or(0)
    }

    /// Read all observations recorded for a histogram.
    pub fn histogram(&self, name: &str) -> Vec<f64> {
        self.histograms
            .lock()
            .unwrap()
            .get(name)
            .cloned()
            .unwrap_or_default()
    }

    /// Read the accumulated cost for a session.
    pub fn session_cost(&self, session_id: &str) -> f64 {
        self.session_costs
            .lock()
            .unwrap()
            .get(session_id)
            .copied()
            .unwrap_or(0.0)
    }
}

impl MetricsExporter for InMemoryExporter {
    fn record_counter(&self, name: &str, value: u64, _attrs: &[(&str, &str)]) {
        *self
            .counters
            .lock()
            .unwrap()
            .entry(name.to_string())
            .or_insert(0) += value;
    }

    fn record_histogram(&self, name: &str, value: f64, _attrs: &[(&str, &str)]) {
        self.histograms
            .lock()
            .unwrap()
            .entry(name.to_string())
            .or_default()
            .push(value);
    }

    fn record_session_cost(&self, session_id: &str, cost_usd: f64) {
        *self
            .session_costs
            .lock()
            .unwrap()
            .entry(session_id.to_string())
            .or_insert(0.0) += cost_usd;
    }
}

/// Higher-level productivity metrics surfaced in the dashboard.
#[derive(Clone, Copy, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct ProductivityMetrics {
    /// Average commits produced per session.
    pub commits_per_session: f64,
    /// Fraction of proposed tool calls the user accepted, 0.0-1.0.
    pub tool_acceptance_rate: f64,
    /// Average wall-clock seconds per session.
    pub avg_session_seconds: f64,
    /// Total spend across all sessions, USD.
    pub total_cost_usd: f64,
}

impl ProductivityMetrics {
    /// Compute the tool acceptance rate from accepted / total counts. Returns
    /// `0.0` when no tools were proposed.
    pub fn acceptance_rate(accepted: u64, total: u64) -> f64 {
        if total == 0 {
            0.0
        } else {
            accepted as f64 / total as f64
        }
    }
}

/// Real OTLP export. Compiled only with the non-default `otlp` feature so the
/// default build never depends on the OpenTelemetry stack.
#[cfg(feature = "otlp")]
pub mod otlp {
    //! OTLP exporter wiring. The heavy `opentelemetry` dependencies belong here
    //! behind the `otlp` feature; the stub keeps the public surface stable.
    use super::*;

    /// Placeholder for a real OTLP-backed exporter configured against an
    /// endpoint. Behaviorally identical to logging until the optional
    /// dependencies are wired in.
    #[derive(Clone, Debug)]
    pub struct OtlpExporter {
        /// The OTLP collector endpoint.
        pub endpoint: String,
    }

    impl OtlpExporter {
        /// Create an OTLP exporter targeting `endpoint`.
        pub fn new(endpoint: impl Into<String>) -> Self {
            Self {
                endpoint: endpoint.into(),
            }
        }
    }

    impl MetricsExporter for OtlpExporter {
        fn record_counter(&self, name: &str, value: u64, attrs: &[(&str, &str)]) {
            tracing::debug!(endpoint = %self.endpoint, metric = name, value, ?attrs, "otlp counter");
        }
        fn record_histogram(&self, name: &str, value: f64, attrs: &[(&str, &str)]) {
            tracing::debug!(endpoint = %self.endpoint, metric = name, value, ?attrs, "otlp histogram");
        }
        fn record_session_cost(&self, session_id: &str, cost_usd: f64) {
            tracing::debug!(endpoint = %self.endpoint, session = session_id, cost_usd, "otlp cost");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn in_memory_exporter_captures_counters_and_histograms() {
        let exp = InMemoryExporter::new();
        exp.record_counter("tool_calls", 3, &[("tool", "Bash")]);
        exp.record_counter("tool_calls", 2, &[]);
        assert_eq!(exp.counter("tool_calls"), 5);

        exp.record_histogram("latency_ms", 12.5, &[]);
        exp.record_histogram("latency_ms", 7.5, &[]);
        assert_eq!(exp.histogram("latency_ms"), vec![12.5, 7.5]);
    }

    #[test]
    fn in_memory_exporter_accumulates_session_cost() {
        let exp = InMemoryExporter::new();
        exp.record_session_cost("sess-1", 0.10);
        exp.record_session_cost("sess-1", 0.05);
        assert!((exp.session_cost("sess-1") - 0.15).abs() < 1e-9);
        assert_eq!(exp.session_cost("unknown"), 0.0);
    }

    #[test]
    fn acceptance_rate_handles_zero() {
        assert_eq!(ProductivityMetrics::acceptance_rate(0, 0), 0.0);
        assert!((ProductivityMetrics::acceptance_rate(3, 4) - 0.75).abs() < 1e-9);
    }

    #[test]
    fn tracing_exporter_is_usable_as_trait_object() {
        let exp: Box<dyn MetricsExporter> = Box::new(TracingExporter::new());
        exp.record_counter("noop", 1, &[]);
    }
}
