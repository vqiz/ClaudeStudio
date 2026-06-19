#![forbid(unsafe_code)]
//! # cs-ssh
//!
//! A minimal SSH client for ClaudeStudio's voice "log into the server" feature.
//!
//! Rather than linking a native SSH library, this crate **shells out to the
//! system `ssh` and `scp` binaries**. That keeps the default build free of
//! `libssh2`/OpenSSL and lets it honor the user's existing SSH configuration
//! (keys, agents, `~/.ssh/config`).
//!
//! The argv construction is factored into the pure functions [`ssh_argv`] and
//! [`scp_argv`] so that command building is fully unit-testable without ever
//! opening a connection. [`SshClient`] is the async trait the rest of the app
//! depends on; [`SystemSsh`] is the default implementation that runs the
//! binaries via `tokio::process`.

use std::process::Stdio;

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use thiserror::Error;
use tokio::process::Command;

/// Errors produced by the SSH layer.
#[derive(Debug, Error)]
pub enum Error {
    /// The `ssh`/`scp` process failed to spawn.
    #[error("failed to spawn ssh/scp: {0}")]
    Spawn(String),
    /// The remote command exited with a non-zero status.
    #[error("remote command failed (exit {code:?}): {stderr}")]
    RemoteFailure {
        /// The process exit code, if available.
        code: Option<i32>,
        /// Captured standard error.
        stderr: String,
    },
}

/// Convenient result alias for this crate.
pub type Result<T> = std::result::Result<T, Error>;

/// How a target host is addressed.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Target {
    /// The remote host (name or IP).
    pub host: String,
    /// The login user.
    pub user: String,
    /// Optional non-default port.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub port: Option<u16>,
}

impl Target {
    /// Create a target for `user@host` on the default port.
    pub fn new(user: impl Into<String>, host: impl Into<String>) -> Self {
        Self {
            user: user.into(),
            host: host.into(),
            port: None,
        }
    }

    /// Set a non-default port.
    pub fn with_port(mut self, port: u16) -> Self {
        self.port = Some(port);
        self
    }

    /// The `user@host` spec used on the command line.
    pub fn spec(&self) -> String {
        format!("{}@{}", self.user, self.host)
    }
}

/// The captured result of a remote command.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CommandOutput {
    /// Exit code, if the process produced one.
    pub code: Option<i32>,
    /// Captured standard output.
    pub stdout: String,
    /// Captured standard error.
    pub stderr: String,
}

/// Build the argv for running `cmd` on `target` via `ssh`.
///
/// Pure and connection-free so it can be asserted in tests. Uses
/// `BatchMode=yes` to fail fast instead of prompting interactively, which suits
/// the unattended voice-assistant flow.
pub fn ssh_argv(target: &Target, cmd: &str) -> Vec<String> {
    let mut argv = vec!["-o".to_string(), "BatchMode=yes".to_string()];
    if let Some(port) = target.port {
        argv.push("-p".to_string());
        argv.push(port.to_string());
    }
    argv.push(target.spec());
    argv.push(cmd.to_string());
    argv
}

/// Build the argv for uploading `local` to `remote` on `target` via `scp`.
///
/// Note that `scp` uses `-P` (uppercase) for the port, unlike `ssh`.
pub fn scp_argv(target: &Target, local: &str, remote: &str) -> Vec<String> {
    let mut argv = vec!["-o".to_string(), "BatchMode=yes".to_string()];
    if let Some(port) = target.port {
        argv.push("-P".to_string());
        argv.push(port.to_string());
    }
    argv.push(local.to_string());
    argv.push(format!("{}:{}", target.spec(), remote));
    argv
}

/// An SSH client abstraction.
#[async_trait]
pub trait SshClient: Send + Sync {
    /// Run `cmd` on the remote host and return its captured output.
    async fn run_command(&self, target: &Target, cmd: &str) -> Result<CommandOutput>;

    /// Upload a local file to a remote path.
    async fn upload(&self, target: &Target, local: &str, remote: &str) -> Result<()>;
}

/// Default [`SshClient`] that shells out to the system `ssh`/`scp` binaries.
#[derive(Clone, Debug)]
pub struct SystemSsh {
    /// The `ssh` binary to run.
    pub ssh_bin: String,
    /// The `scp` binary to run.
    pub scp_bin: String,
}

impl Default for SystemSsh {
    fn default() -> Self {
        Self {
            ssh_bin: "ssh".to_string(),
            scp_bin: "scp".to_string(),
        }
    }
}

impl SystemSsh {
    /// Create a client using the system `ssh`/`scp` on `PATH`.
    pub fn new() -> Self {
        Self::default()
    }
}

#[async_trait]
impl SshClient for SystemSsh {
    async fn run_command(&self, target: &Target, cmd: &str) -> Result<CommandOutput> {
        let argv = ssh_argv(target, cmd);
        let output = Command::new(&self.ssh_bin)
            .args(&argv)
            .stdin(Stdio::null())
            .output()
            .await
            .map_err(|e| Error::Spawn(e.to_string()))?;
        Ok(CommandOutput {
            code: output.status.code(),
            stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
            stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
        })
    }

    async fn upload(&self, target: &Target, local: &str, remote: &str) -> Result<()> {
        let argv = scp_argv(target, local, remote);
        let output = Command::new(&self.scp_bin)
            .args(&argv)
            .stdin(Stdio::null())
            .output()
            .await
            .map_err(|e| Error::Spawn(e.to_string()))?;
        if output.status.success() {
            Ok(())
        } else {
            Err(Error::RemoteFailure {
                code: output.status.code(),
                stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
            })
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ssh_argv_basic() {
        let t = Target::new("deploy", "example.com");
        let argv = ssh_argv(&t, "uptime");
        assert_eq!(
            argv,
            vec!["-o", "BatchMode=yes", "deploy@example.com", "uptime"]
        );
    }

    #[test]
    fn ssh_argv_with_port_uses_lowercase_p() {
        let t = Target::new("root", "10.0.0.1").with_port(2222);
        let argv = ssh_argv(&t, "ls -la");
        assert!(argv.contains(&"-p".to_string()));
        assert!(argv.contains(&"2222".to_string()));
        // command is the last argument, verbatim.
        assert_eq!(argv.last().unwrap(), "ls -la");
    }

    #[test]
    fn scp_argv_uses_uppercase_p_and_target_path() {
        let t = Target::new("deploy", "example.com").with_port(2222);
        let argv = scp_argv(&t, "./build.tar.gz", "/srv/app/build.tar.gz");
        assert!(argv.contains(&"-P".to_string()));
        assert!(argv.contains(&"2222".to_string()));
        assert_eq!(argv[argv.len() - 2], "./build.tar.gz");
        assert_eq!(
            argv.last().unwrap(),
            "deploy@example.com:/srv/app/build.tar.gz"
        );
    }

    #[test]
    fn target_spec_formats_user_at_host() {
        assert_eq!(Target::new("u", "h").spec(), "u@h");
    }
}
