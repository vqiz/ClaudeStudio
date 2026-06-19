#![forbid(unsafe_code)]
//! # cs-mcp
//!
//! Model Context Protocol (MCP) server lifecycle management for ClaudeStudio.
//!
//! This crate models the configuration of MCP servers ([`McpServerConfig`] with
//! its [`Transport`] and [`Scope`]), parses Claude-style `mcpServers` JSON
//! blocks into a `Vec<McpServerConfig>` ([`parse_mcp_config`]), and tracks the
//! [`ServerStatus`] of each managed server via the [`McpManager`].
//!
//! Spawning of stdio servers is modeled through `tokio::process` and only
//! occurs when [`McpManager::start`] is explicitly invoked; a plain `cargo
//! build` with default features pulls in no network services or native
//! libraries.

use std::collections::HashMap;
use std::process::Stdio;

use serde::{Deserialize, Serialize};
use thiserror::Error;
use tokio::process::Command;

/// Errors produced by the MCP manager.
#[derive(Debug, Error)]
pub enum Error {
    /// The MCP configuration JSON was malformed.
    #[error("invalid mcp config: {0}")]
    Config(String),
    /// A server with the given name is not known to the manager.
    #[error("unknown mcp server: {0}")]
    UnknownServer(String),
    /// The server transport does not support being spawned as a process.
    #[error("server '{0}' uses a non-stdio transport and cannot be spawned")]
    NotSpawnable(String),
    /// The underlying process failed to start.
    #[error("failed to spawn mcp server '{0}': {1}")]
    Spawn(String, String),
}

/// Convenient result alias for this crate.
pub type Result<T> = std::result::Result<T, Error>;

/// How a single MCP server is reached.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum Transport {
    /// A locally spawned process speaking MCP over stdio.
    Stdio {
        /// Executable to run.
        command: String,
        /// Arguments passed to the executable.
        #[serde(default)]
        args: Vec<String>,
        /// Extra environment variables.
        #[serde(default)]
        env: HashMap<String, String>,
    },
    /// A remote server over Server-Sent Events.
    Sse {
        /// The SSE endpoint URL.
        url: String,
    },
    /// A remote server over streamable HTTP.
    Http {
        /// The HTTP endpoint URL.
        url: String,
    },
}

/// The visibility scope a server is configured at.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum Scope {
    /// Available only in the current project.
    #[default]
    Local,
    /// Shared with the project (e.g. checked into `.mcp.json`).
    Project,
    /// Available to the user across all projects.
    User,
}

/// The full configuration of one MCP server.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct McpServerConfig {
    /// Unique server name.
    pub name: String,
    /// How to reach the server.
    pub transport: Transport,
    /// Visibility scope.
    #[serde(default)]
    pub scope: Scope,
}

/// The lifecycle state of a managed MCP server.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ServerStatus {
    /// Configured but not started.
    Stopped,
    /// In the process of starting.
    Starting,
    /// Running and connected.
    Running,
    /// Terminated with an error.
    Failed,
}

/// Internal representation of the `claude`-style mcp JSON entry, where the
/// transport fields are flattened alongside an optional `type`.
#[derive(Deserialize)]
struct RawEntry {
    #[serde(default)]
    r#type: Option<String>,
    #[serde(default)]
    command: Option<String>,
    #[serde(default)]
    args: Vec<String>,
    #[serde(default)]
    env: HashMap<String, String>,
    #[serde(default)]
    url: Option<String>,
}

#[derive(Deserialize)]
struct RawConfig {
    #[serde(rename = "mcpServers", default)]
    servers: HashMap<String, RawEntry>,
}

/// Parse a Claude-style MCP configuration document into server configs.
///
/// Accepts the common shape:
/// ```json
/// { "mcpServers": { "name": { "command": "npx", "args": ["..."] } } }
/// ```
/// Entries with a `url` (and no `command`) are treated as SSE unless their
/// `type` says `http`.
pub fn parse_mcp_config(json: &str) -> Result<Vec<McpServerConfig>> {
    let raw: RawConfig = serde_json::from_str(json).map_err(|e| Error::Config(e.to_string()))?;
    let mut out = Vec::with_capacity(raw.servers.len());
    for (name, entry) in raw.servers {
        let transport = match (entry.r#type.as_deref(), entry.command, entry.url) {
            (Some("http"), _, Some(url)) => Transport::Http { url },
            (Some("sse"), _, Some(url)) => Transport::Sse { url },
            (_, Some(command), _) => Transport::Stdio {
                command,
                args: entry.args,
                env: entry.env,
            },
            (_, None, Some(url)) => Transport::Sse { url },
            _ => {
                return Err(Error::Config(format!(
                    "server '{name}' has neither a command nor a url"
                )))
            }
        };
        out.push(McpServerConfig {
            name,
            transport,
            scope: Scope::default(),
        });
    }
    // Deterministic ordering for stable tests / UI.
    out.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(out)
}

/// Tracks configured MCP servers and their [`ServerStatus`].
#[derive(Debug, Default)]
pub struct McpManager {
    servers: HashMap<String, McpServerConfig>,
    status: HashMap<String, ServerStatus>,
}

impl McpManager {
    /// Create an empty manager.
    pub fn new() -> Self {
        Self::default()
    }

    /// Register a server configuration in the `Stopped` state.
    pub fn add(&mut self, config: McpServerConfig) {
        self.status
            .insert(config.name.clone(), ServerStatus::Stopped);
        self.servers.insert(config.name.clone(), config);
    }

    /// Look up a server's current status.
    pub fn status(&self, name: &str) -> Option<ServerStatus> {
        self.status.get(name).copied()
    }

    /// Number of registered servers.
    pub fn len(&self) -> usize {
        self.servers.len()
    }

    /// Whether no servers are registered.
    pub fn is_empty(&self) -> bool {
        self.servers.is_empty()
    }

    /// Spawn a stdio MCP server, transitioning it to `Running` on success or
    /// `Failed` on error. Non-stdio transports return [`Error::NotSpawnable`].
    pub async fn start(&mut self, name: &str) -> Result<()> {
        let config = self
            .servers
            .get(name)
            .ok_or_else(|| Error::UnknownServer(name.to_string()))?
            .clone();

        match &config.transport {
            Transport::Stdio { command, args, env } => {
                self.status.insert(name.to_string(), ServerStatus::Starting);
                let mut cmd = Command::new(command);
                cmd.args(args)
                    .envs(env)
                    .stdin(Stdio::piped())
                    .stdout(Stdio::piped())
                    .stderr(Stdio::null());
                match cmd.spawn() {
                    Ok(mut child) => {
                        self.status.insert(name.to_string(), ServerStatus::Running);
                        tokio::spawn(async move {
                            let _ = child.wait().await;
                        });
                        Ok(())
                    }
                    Err(e) => {
                        self.status.insert(name.to_string(), ServerStatus::Failed);
                        Err(Error::Spawn(name.to_string(), e.to_string()))
                    }
                }
            }
            _ => Err(Error::NotSpawnable(name.to_string())),
        }
    }

    /// Mark a server as stopped (used by the UI / on disconnect).
    pub fn mark_stopped(&mut self, name: &str) -> Result<()> {
        if !self.servers.contains_key(name) {
            return Err(Error::UnknownServer(name.to_string()));
        }
        self.status.insert(name.to_string(), ServerStatus::Stopped);
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn deserialize_sample_mcp_config() {
        let json = r#"{
            "mcpServers": {
                "filesystem": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
                },
                "remote": {
                    "type": "sse",
                    "url": "https://example.com/sse"
                }
            }
        }"#;
        let configs = parse_mcp_config(json).unwrap();
        assert_eq!(configs.len(), 2);
        // Sorted by name: "filesystem" then "remote".
        assert_eq!(configs[0].name, "filesystem");
        match &configs[0].transport {
            Transport::Stdio { command, args, .. } => {
                assert_eq!(command, "npx");
                assert_eq!(args.len(), 3);
            }
            other => panic!("expected stdio, got {other:?}"),
        }
        match &configs[1].transport {
            Transport::Sse { url } => assert_eq!(url, "https://example.com/sse"),
            other => panic!("expected sse, got {other:?}"),
        }
    }

    #[test]
    fn invalid_config_errors() {
        assert!(parse_mcp_config("{ not json").is_err());
        // A server with neither command nor url is rejected.
        let json = r#"{"mcpServers":{"bad":{}}}"#;
        assert!(parse_mcp_config(json).is_err());
    }

    #[test]
    fn status_transitions_via_manager() {
        let mut mgr = McpManager::new();
        mgr.add(McpServerConfig {
            name: "fs".into(),
            transport: Transport::Stdio {
                command: "npx".into(),
                args: vec![],
                env: HashMap::new(),
            },
            scope: Scope::Project,
        });
        assert_eq!(mgr.status("fs"), Some(ServerStatus::Stopped));
        mgr.mark_stopped("fs").unwrap();
        assert_eq!(mgr.status("fs"), Some(ServerStatus::Stopped));
        assert!(mgr.mark_stopped("missing").is_err());
        assert_eq!(mgr.len(), 1);
    }

    #[tokio::test]
    async fn start_rejects_non_stdio_transport() {
        let mut mgr = McpManager::new();
        mgr.add(McpServerConfig {
            name: "http".into(),
            transport: Transport::Http {
                url: "https://example.com".into(),
            },
            scope: Scope::User,
        });
        assert!(matches!(
            mgr.start("http").await,
            Err(Error::NotSpawnable(_))
        ));
    }
}
