#![forbid(unsafe_code)]
//! `cs-git` — the Git and worktree manager for ClaudeStudio.
//!
//! Rather than linking a native git library (`git2`/`libgit2`), this crate
//! shells out to the user's installed `git` binary via [`tokio::process`]. That
//! keeps the build free of system dependencies and matches whatever git the
//! developer already uses (credentials, hooks, config and all).
//!
//! The behaviour is defined by the [`GitBackend`] trait; [`SystemGit`] is the
//! default implementation. Output parsing is factored into free functions
//! ([`parse_porcelain_status`], [`parse_worktree_list`],
//! [`generate_conventional_commit_message`]) so it can be unit-tested
//! deterministically without invoking git at all.
//!
//! ```
//! use cs_git::{parse_porcelain_status, FileState};
//!
//! let entries = parse_porcelain_status("?? new.txt\n M src/lib.rs\nA  added.rs\n");
//! assert_eq!(entries.len(), 3);
//! assert_eq!(entries[0].state, FileState::Untracked);
//! ```

use std::path::{Path, PathBuf};

use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use tokio::process::Command;

/// Errors produced by the git layer.
#[derive(Debug, thiserror::Error)]
pub enum Error {
    /// The `git` process could not be spawned (e.g. binary not found).
    #[error("failed to spawn git: {0}")]
    Spawn(#[from] std::io::Error),
    /// `git` ran but exited non-zero. Contains the captured stderr.
    #[error("git exited with status {code:?}: {stderr}")]
    Command {
        /// Process exit code, if available.
        code: Option<i32>,
        /// Captured standard error.
        stderr: String,
    },
    /// Git produced output we could not parse.
    #[error("could not parse git output: {0}")]
    Parse(String),
}

/// Convenience result alias used throughout this crate.
pub type Result<T> = std::result::Result<T, Error>;

/// The working-tree state of a single path, from `git status --porcelain`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FileState {
    /// New file not yet tracked by git.
    Untracked,
    /// Tracked file with staged or unstaged modifications.
    Modified,
    /// Newly added (staged) file.
    Added,
    /// Deleted file.
    Deleted,
    /// Renamed file.
    Renamed,
    /// A file with merge conflicts.
    Conflicted,
    /// Any other state we don't model explicitly.
    Other,
}

/// A single entry from a porcelain status listing.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StatusEntry {
    /// Classified state of the path.
    pub state: FileState,
    /// The two raw porcelain status characters (e.g. " M", "??", "A ").
    pub raw: String,
    /// Path relative to the repository root.
    pub path: String,
}

/// The status of a worktree relative to its branch.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum WorktreeStatus {
    /// A normal, attached worktree.
    Normal,
    /// A bare repository worktree.
    Bare,
    /// A detached-HEAD worktree.
    Detached,
    /// A worktree whose backing directory is missing / locked.
    Prunable,
}

/// Metadata describing a single git worktree.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorktreeInfo {
    /// Absolute path to the worktree directory.
    pub path: PathBuf,
    /// Checked-out commit hash, if known.
    pub head: Option<String>,
    /// Checked-out branch (short name), if any.
    pub branch: Option<String>,
    /// Status classification.
    pub status: WorktreeStatus,
}

/// A single commit from `git log`.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct CommitInfo {
    /// Full commit hash.
    pub hash: String,
    /// Author name.
    pub author: String,
    /// Authored date (`YYYY-MM-DD`).
    pub date: String,
    /// Commit subject (first line of the message).
    pub subject: String,
}

/// Operations on a git repository. Implemented by [`SystemGit`].
#[async_trait]
pub trait GitBackend: Send + Sync {
    /// Parsed working-tree status (`git status --porcelain`).
    async fn status(&self) -> Result<Vec<StatusEntry>>;

    /// The current branch's short name (`git rev-parse --abbrev-ref HEAD`).
    async fn current_branch(&self) -> Result<String>;

    /// Create a worktree at `path` checking out (creating) `branch`.
    async fn create_worktree(&self, path: &Path, branch: &str) -> Result<()>;

    /// List existing worktrees (`git worktree list --porcelain`).
    async fn list_worktrees(&self) -> Result<Vec<WorktreeInfo>>;

    /// Remove the worktree at `path`.
    async fn remove_worktree(&self, path: &Path) -> Result<()>;

    /// The unified diff of the working tree; `staged` selects `git diff --cached`.
    async fn diff(&self, staged: bool) -> Result<String>;

    /// Recent commits, newest first (`limit` clamped to `1..=200`).
    async fn log(&self, limit: u32) -> Result<Vec<CommitInfo>>;

    /// Stage all changes and commit with `message`; returns the new commit hash.
    async fn commit(&self, message: &str) -> Result<String>;
}

/// Default [`GitBackend`] that invokes the system `git` binary.
#[derive(Debug, Clone)]
pub struct SystemGit {
    repo: PathBuf,
    git_bin: String,
}

impl SystemGit {
    /// Create a backend operating on the repository rooted at `repo`.
    #[must_use]
    pub fn new(repo: impl Into<PathBuf>) -> Self {
        Self {
            repo: repo.into(),
            git_bin: "git".to_string(),
        }
    }

    /// Override the git executable name/path (mainly for tests).
    #[must_use]
    pub fn with_git_bin(mut self, bin: impl Into<String>) -> Self {
        self.git_bin = bin.into();
        self
    }

    /// Run `git <args>` in the repo and return captured stdout as a `String`.
    async fn run(&self, args: &[&str]) -> Result<String> {
        let output = Command::new(&self.git_bin)
            .arg("-C")
            .arg(&self.repo)
            .args(args)
            .output()
            .await?;
        if !output.status.success() {
            return Err(Error::Command {
                code: output.status.code(),
                stderr: String::from_utf8_lossy(&output.stderr).trim().to_string(),
            });
        }
        Ok(String::from_utf8_lossy(&output.stdout).to_string())
    }

    /// Heuristically generate a Conventional Commit message from a diff.
    ///
    /// No network or LLM call — see [`generate_conventional_commit_message`].
    #[must_use]
    pub fn generate_conventional_commit_message(&self, diff: &str) -> String {
        generate_conventional_commit_message(diff)
    }
}

#[async_trait]
impl GitBackend for SystemGit {
    async fn status(&self) -> Result<Vec<StatusEntry>> {
        let out = self.run(&["status", "--porcelain"]).await?;
        Ok(parse_porcelain_status(&out))
    }

    async fn current_branch(&self) -> Result<String> {
        let out = self.run(&["rev-parse", "--abbrev-ref", "HEAD"]).await?;
        Ok(out.trim().to_string())
    }

    async fn create_worktree(&self, path: &Path, branch: &str) -> Result<()> {
        let path_str = path.to_string_lossy();
        self.run(&["worktree", "add", "-b", branch, &path_str])
            .await?;
        Ok(())
    }

    async fn list_worktrees(&self) -> Result<Vec<WorktreeInfo>> {
        let out = self.run(&["worktree", "list", "--porcelain"]).await?;
        parse_worktree_list(&out)
    }

    async fn remove_worktree(&self, path: &Path) -> Result<()> {
        let path_str = path.to_string_lossy();
        self.run(&["worktree", "remove", &path_str]).await?;
        Ok(())
    }

    async fn diff(&self, staged: bool) -> Result<String> {
        let mut args = vec!["diff"];
        if staged {
            args.push("--cached");
        }
        self.run(&args).await
    }

    async fn log(&self, limit: u32) -> Result<Vec<CommitInfo>> {
        let n = format!("-{}", limit.clamp(1, 200));
        let out = self
            .run(&[
                "log",
                n.as_str(),
                "--date=short",
                "--pretty=format:%H%x1f%an%x1f%ad%x1f%s",
            ])
            .await?;
        Ok(parse_git_log(&out))
    }

    async fn commit(&self, message: &str) -> Result<String> {
        self.run(&["add", "-A"]).await?;
        self.run(&["commit", "-m", message]).await?;
        let head = self.run(&["rev-parse", "HEAD"]).await?;
        Ok(head.trim().to_string())
    }
}

/// Parse `git log --pretty=format:%H%x1f%an%x1f%ad%x1f%s` output (unit-separator
/// delimited) into [`CommitInfo`] records. Pure and deterministic.
#[must_use]
pub fn parse_git_log(output: &str) -> Vec<CommitInfo> {
    output
        .lines()
        .filter(|line| !line.trim().is_empty())
        .filter_map(|line| {
            let mut parts = line.split('\u{1f}');
            let hash = parts.next()?.to_string();
            if hash.is_empty() {
                return None;
            }
            Some(CommitInfo {
                hash,
                author: parts.next().unwrap_or("").to_string(),
                date: parts.next().unwrap_or("").to_string(),
                subject: parts.next().unwrap_or("").to_string(),
            })
        })
        .collect()
}

/// Classify the two-character porcelain status code into a [`FileState`].
fn classify_status(code: &str) -> FileState {
    let bytes = code.as_bytes();
    let x = bytes.first().copied().unwrap_or(b' ');
    let y = bytes.get(1).copied().unwrap_or(b' ');
    if x == b'?' && y == b'?' {
        return FileState::Untracked;
    }
    if x == b'U' || y == b'U' || (x == b'A' && y == b'A') || (x == b'D' && y == b'D') {
        return FileState::Conflicted;
    }
    // Prefer the staged (X) column, fall back to the worktree (Y) column.
    let pick = if x != b' ' { x } else { y };
    match pick {
        b'M' => FileState::Modified,
        b'A' => FileState::Added,
        b'D' => FileState::Deleted,
        b'R' => FileState::Renamed,
        _ => FileState::Other,
    }
}

/// Parse the output of `git status --porcelain` into structured entries.
///
/// Pure and deterministic — does not invoke git.
#[must_use]
pub fn parse_porcelain_status(output: &str) -> Vec<StatusEntry> {
    let mut entries = Vec::new();
    for line in output.lines() {
        if line.len() < 3 {
            continue;
        }
        let code = &line[..2];
        let rest = &line[3..];
        // Rename entries look like "R  old -> new"; keep the new path.
        let path = rest.split(" -> ").last().unwrap_or(rest).to_string();
        entries.push(StatusEntry {
            state: classify_status(code),
            raw: code.to_string(),
            path,
        });
    }
    entries
}

/// Parse the output of `git worktree list --porcelain` into [`WorktreeInfo`]s.
///
/// Pure and deterministic — does not invoke git.
pub fn parse_worktree_list(output: &str) -> Result<Vec<WorktreeInfo>> {
    let mut worktrees = Vec::new();
    let mut current: Option<WorktreeInfo> = None;

    let flush = |cur: &mut Option<WorktreeInfo>, out: &mut Vec<WorktreeInfo>| {
        if let Some(w) = cur.take() {
            out.push(w);
        }
    };

    for line in output.lines() {
        if line.is_empty() {
            flush(&mut current, &mut worktrees);
            continue;
        }
        if let Some(path) = line.strip_prefix("worktree ") {
            flush(&mut current, &mut worktrees);
            current = Some(WorktreeInfo {
                path: PathBuf::from(path.trim()),
                head: None,
                branch: None,
                status: WorktreeStatus::Normal,
            });
        } else if let Some(head) = line.strip_prefix("HEAD ") {
            if let Some(w) = current.as_mut() {
                w.head = Some(head.trim().to_string());
            }
        } else if let Some(branch) = line.strip_prefix("branch ") {
            if let Some(w) = current.as_mut() {
                // git emits e.g. "refs/heads/main"; keep the short name.
                let short = branch
                    .trim()
                    .rsplit('/')
                    .next()
                    .unwrap_or(branch)
                    .to_string();
                w.branch = Some(short);
            }
        } else if line.trim() == "bare" {
            if let Some(w) = current.as_mut() {
                w.status = WorktreeStatus::Bare;
            }
        } else if line.trim() == "detached" {
            if let Some(w) = current.as_mut() {
                w.status = WorktreeStatus::Detached;
            }
        } else if line.starts_with("prunable") {
            if let Some(w) = current.as_mut() {
                w.status = WorktreeStatus::Prunable;
            }
        }
        // Other attribute lines (locked, etc.) are ignored.
    }
    flush(&mut current, &mut worktrees);

    if worktrees.is_empty() && !output.trim().is_empty() {
        return Err(Error::Parse(
            "no worktree entries found in output".to_string(),
        ));
    }
    Ok(worktrees)
}

/// Heuristically derive a Conventional Commit message from a unified diff.
///
/// This uses simple, transparent rules (no API call): it infers a `type`
/// (`feat`/`fix`/`docs`/`test`/`chore`), an optional `scope` from the most
/// commonly touched top-level directory, and a short summary of how many files
/// changed. The result is deterministic for a given diff.
#[must_use]
pub fn generate_conventional_commit_message(diff: &str) -> String {
    let mut changed_files: Vec<String> = Vec::new();
    let mut added_lines = 0usize;
    let mut removed_lines = 0usize;

    for line in diff.lines() {
        if let Some(rest) = line.strip_prefix("+++ b/") {
            changed_files.push(rest.trim().to_string());
        } else if line.starts_with('+') && !line.starts_with("+++") {
            added_lines += 1;
        } else if line.starts_with('-') && !line.starts_with("---") {
            removed_lines += 1;
        }
    }

    let lower = diff.to_lowercase();
    let all_docs = !changed_files.is_empty()
        && changed_files
            .iter()
            .all(|f| f.ends_with(".md") || f.contains("docs/") || f.ends_with(".txt"));
    let all_tests = !changed_files.is_empty()
        && changed_files
            .iter()
            .all(|f| f.contains("test") || f.contains("/tests/") || f.ends_with("_test.rs"));

    let kind = if all_docs {
        "docs"
    } else if all_tests {
        "test"
    } else if lower.contains("fix") || lower.contains("bug") || removed_lines > added_lines {
        "fix"
    } else if added_lines == 0 && removed_lines == 0 {
        "chore"
    } else {
        "feat"
    };

    // Infer a scope from the most common top-level directory among changes.
    let scope = infer_scope(&changed_files);

    let summary = match changed_files.len() {
        0 => "update repository".to_string(),
        1 => {
            let f = &changed_files[0];
            let name = Path::new(f)
                .file_stem()
                .and_then(|s| s.to_str())
                .unwrap_or(f);
            format!("update {name}")
        }
        n => format!("update {n} files"),
    };

    match scope {
        Some(s) => format!("{kind}({s}): {summary}"),
        None => format!("{kind}: {summary}"),
    }
}

fn infer_scope(files: &[String]) -> Option<String> {
    use std::collections::HashMap;
    let mut counts: HashMap<&str, usize> = HashMap::new();
    for f in files {
        if let Some((top, _)) = f.split_once('/') {
            *counts.entry(top).or_default() += 1;
        }
    }
    counts
        .into_iter()
        .max_by(|a, b| a.1.cmp(&b.1).then_with(|| b.0.cmp(a.0)))
        .map(|(scope, _)| scope.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_porcelain_status_states() {
        let out = "?? new.txt\n M src/lib.rs\nA  staged.rs\nD  gone.rs\nR  old.rs -> renamed.rs\nUU conflict.rs\n";
        let entries = parse_porcelain_status(out);
        assert_eq!(entries.len(), 6);
        assert_eq!(entries[0].state, FileState::Untracked);
        assert_eq!(entries[0].path, "new.txt");
        assert_eq!(entries[1].state, FileState::Modified);
        assert_eq!(entries[2].state, FileState::Added);
        assert_eq!(entries[3].state, FileState::Deleted);
        assert_eq!(entries[4].state, FileState::Renamed);
        assert_eq!(entries[4].path, "renamed.rs", "rename keeps the new path");
        assert_eq!(entries[5].state, FileState::Conflicted);
    }

    #[test]
    fn parses_worktree_list_porcelain() {
        let out = "\
worktree /home/me/project
HEAD abc123def
branch refs/heads/main

worktree /home/me/project-feature
HEAD 999fff
branch refs/heads/feature/login
";
        let wts = parse_worktree_list(out).unwrap();
        assert_eq!(wts.len(), 2);
        assert_eq!(wts[0].path, PathBuf::from("/home/me/project"));
        assert_eq!(wts[0].branch.as_deref(), Some("main"));
        assert_eq!(wts[0].head.as_deref(), Some("abc123def"));
        assert_eq!(wts[1].branch.as_deref(), Some("login"));
        assert_eq!(wts[1].status, WorktreeStatus::Normal);
    }

    #[test]
    fn detects_detached_and_bare_worktrees() {
        let out = "\
worktree /repo/bare
bare

worktree /repo/detached
HEAD deadbeef
detached
";
        let wts = parse_worktree_list(out).unwrap();
        assert_eq!(wts.len(), 2);
        assert_eq!(wts[0].status, WorktreeStatus::Bare);
        assert_eq!(wts[1].status, WorktreeStatus::Detached);
    }

    #[test]
    fn commit_message_classifies_docs_and_feat() {
        let docs_diff = "+++ b/docs/guide.md\n+new line\n";
        let msg = generate_conventional_commit_message(docs_diff);
        assert!(msg.starts_with("docs"), "got: {msg}");
        assert!(msg.contains("docs"), "scope should be docs: {msg}");

        let feat_diff = "+++ b/src/feature.rs\n+fn new_thing() {}\n+let a = 1;\n";
        let msg = generate_conventional_commit_message(feat_diff);
        assert!(msg.starts_with("feat(src):"), "got: {msg}");
        assert!(msg.contains("update feature"));
    }

    #[test]
    fn empty_worktree_output_is_ok() {
        assert!(parse_worktree_list("").unwrap().is_empty());
    }

    #[test]
    fn parses_unit_separated_git_log() {
        let out = "abc123\u{1f}Ada\u{1f}2026-06-19\u{1f}feat: add widget\n\
                   def456\u{1f}Linus\u{1f}2026-06-18\u{1f}fix: off-by-one\n";
        let commits = parse_git_log(out);
        assert_eq!(commits.len(), 2);
        assert_eq!(commits[0].hash, "abc123");
        assert_eq!(commits[0].author, "Ada");
        assert_eq!(commits[0].date, "2026-06-19");
        assert_eq!(commits[0].subject, "feat: add widget");
        assert_eq!(commits[1].subject, "fix: off-by-one");
        assert!(parse_git_log("").is_empty());
    }
}
