#![forbid(unsafe_code)]
//! # cs-agentic-os
//!
//! The Agentic OS core for ClaudeStudio. This crate provides the runtime
//! substrate that turns a collection of Claude Code agents into a coordinated,
//! event-driven operating system:
//!
//! - [`EventBus`] — a tokio-`broadcast`-backed publish/subscribe bus for
//!   [`SystemEvent`]s (git pushes, PR openings, failing tests, budget warnings,
//!   cron ticks, voice commands, agent completions, and more).
//! - [`PriorityQueue`] — a binary-heap task queue ordered by
//!   [`cs_types::Priority`] so that `Critical` work always preempts
//!   `Background` work.
//! - [`Supervisor`] — tracks the [`cs_types::AgentStatus`] of every running
//!   agent, detects stale agents that have produced no output for too long, and
//!   routes agent-to-agent (A2A) messages over per-agent `mpsc` channels.
//! - [`Rule`] — a small visual automation primitive (`when <event> then
//!   <actions>`) that powers the no-code rules UI.
//!
//! Everything here works with a plain `cargo build` and the default feature
//! set: no network services, no native libraries.

use std::collections::BinaryHeap;
use std::collections::HashMap;
use std::time::{Duration, Instant};

use cs_types::{AgentStatus, Priority};
use serde::{Deserialize, Serialize};
use thiserror::Error;
use tokio::sync::{broadcast, mpsc};

/// Errors produced by the agentic OS layer.
#[derive(Debug, Error)]
pub enum Error {
    /// The event bus had no active subscribers when a publish was attempted.
    #[error("no active subscribers on the event bus")]
    NoSubscribers,
    /// An A2A message was addressed to an agent that is not registered.
    #[error("unknown agent id: {0}")]
    UnknownAgent(String),
    /// The receiving end of an A2A channel has been dropped.
    #[error("agent channel closed: {0}")]
    ChannelClosed(String),
}

/// Convenient result alias for this crate.
pub type Result<T> = std::result::Result<T, Error>;

/// Events that flow through the [`EventBus`].
///
/// These are the triggers the Supervisor and the visual [`Rule`] engine react
/// to. The set intentionally mirrors the high-level lifecycle moments a
/// developer cares about.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum SystemEvent {
    /// Code was pushed to a remote.
    GitPush,
    /// A pull request was opened.
    PrOpened,
    /// A file on disk changed.
    FileChanged,
    /// A test run failed.
    TestFailed,
    /// The session budget crossed a warning threshold (percent consumed).
    BudgetWarning {
        /// Percentage of the budget consumed, 0-100.
        pct: u8,
    },
    /// A scheduled cron trigger fired.
    ScheduleCron,
    /// A voice command was recognized.
    VoiceCommand,
    /// An agent finished its work.
    AgentCompleted {
        /// The id of the agent that completed.
        id: String,
    },
    /// A one-click task button was pressed in the UI.
    TaskOneClick,
    /// A deployment failed.
    DeploymentFailed,
}

/// A publish/subscribe bus for [`SystemEvent`]s backed by a tokio broadcast
/// channel.
///
/// Cloning an `EventBus` is cheap and yields a handle to the *same* underlying
/// channel, so producers and the [`Supervisor`] can share one bus.
#[derive(Clone, Debug)]
pub struct EventBus {
    sender: broadcast::Sender<SystemEvent>,
}

impl EventBus {
    /// Create a new event bus with a reasonable default channel capacity.
    pub fn new() -> Self {
        let (sender, _rx) = broadcast::channel(1024);
        Self { sender }
    }

    /// Create a new event bus with a specific channel capacity.
    pub fn with_capacity(capacity: usize) -> Self {
        let (sender, _rx) = broadcast::channel(capacity.max(1));
        Self { sender }
    }

    /// Subscribe to the bus, returning a receiver that observes every event
    /// published *after* this call.
    pub fn subscribe(&self) -> broadcast::Receiver<SystemEvent> {
        self.sender.subscribe()
    }

    /// Publish an event to all current subscribers.
    ///
    /// Returns the number of subscribers the event was delivered to, or
    /// [`Error::NoSubscribers`] if there were none.
    pub fn publish(&self, event: SystemEvent) -> Result<usize> {
        self.sender.send(event).map_err(|_| Error::NoSubscribers)
    }

    /// Number of currently active subscribers.
    pub fn subscriber_count(&self) -> usize {
        self.sender.receiver_count()
    }
}

impl Default for EventBus {
    fn default() -> Self {
        Self::new()
    }
}

/// A unit of work scheduled on the [`PriorityQueue`].
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Task {
    /// Unique identifier for the task.
    pub id: String,
    /// Human-readable description.
    pub description: String,
    /// The scheduling priority.
    pub priority: Priority,
    /// Monotonic sequence used to break ties in FIFO order.
    #[serde(default)]
    seq: u64,
}

impl Task {
    /// Create a new task with the given id, description, and priority.
    pub fn new(
        id: impl Into<String>,
        description: impl Into<String>,
        priority: Priority,
    ) -> Self {
        Self {
            id: id.into(),
            description: description.into(),
            priority,
            seq: 0,
        }
    }
}

impl PartialEq for Task {
    fn eq(&self, other: &Self) -> bool {
        self.priority == other.priority && self.seq == other.seq
    }
}
impl Eq for Task {}

impl Ord for Task {
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        // Higher priority first; among equal priorities, lower seq (older) first.
        self.priority
            .cmp(&other.priority)
            .then_with(|| other.seq.cmp(&self.seq))
    }
}
impl PartialOrd for Task {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}

/// A max-heap priority queue of [`Task`]s.
///
/// `pop` always returns the highest-priority task, with FIFO ordering among
/// tasks of equal priority. `Critical` work is therefore always dispatched
/// before `Background` work.
#[derive(Debug, Default)]
pub struct PriorityQueue {
    heap: BinaryHeap<Task>,
    next_seq: u64,
}

impl PriorityQueue {
    /// Create an empty priority queue.
    pub fn new() -> Self {
        Self::default()
    }

    /// Push a task onto the queue, stamping it with the next sequence number
    /// so that ties are broken in insertion order.
    pub fn push(&mut self, mut task: Task) {
        task.seq = self.next_seq;
        self.next_seq += 1;
        self.heap.push(task);
    }

    /// Pop the highest-priority task, if any.
    pub fn pop(&mut self) -> Option<Task> {
        self.heap.pop()
    }

    /// Peek at the highest-priority task without removing it.
    pub fn peek(&self) -> Option<&Task> {
        self.heap.peek()
    }

    /// Number of queued tasks.
    pub fn len(&self) -> usize {
        self.heap.len()
    }

    /// Whether the queue is empty.
    pub fn is_empty(&self) -> bool {
        self.heap.is_empty()
    }
}

/// A handle the [`Supervisor`] keeps for each running agent.
#[derive(Debug)]
pub struct AgentHandle {
    /// The agent's unique id.
    pub id: String,
    /// The agent's current status.
    pub status: AgentStatus,
    /// When the agent last produced output.
    last_output: Instant,
    /// A2A inbound message sender for this agent.
    inbox: mpsc::UnboundedSender<A2AMessage>,
}

/// A message routed from one agent to another.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct A2AMessage {
    /// Sending agent id.
    pub from: String,
    /// Receiving agent id.
    pub to: String,
    /// Opaque JSON payload.
    pub payload: serde_json::Value,
}

/// Tracks agents, detects stale ones, and routes A2A messages.
#[derive(Debug, Default)]
pub struct Supervisor {
    agents: HashMap<String, AgentHandle>,
}

impl Supervisor {
    /// Create an empty supervisor.
    pub fn new() -> Self {
        Self::default()
    }

    /// Register an agent, returning the receiving end of its A2A inbox.
    pub fn register(&mut self, id: impl Into<String>) -> mpsc::UnboundedReceiver<A2AMessage> {
        let id = id.into();
        let (tx, rx) = mpsc::unbounded_channel();
        self.agents.insert(
            id.clone(),
            AgentHandle {
                id,
                status: AgentStatus::Idle,
                last_output: Instant::now(),
                inbox: tx,
            },
        );
        rx
    }

    /// Update an agent's status and mark that it just produced output.
    pub fn set_status(&mut self, id: &str, status: AgentStatus) -> Result<()> {
        let handle = self
            .agents
            .get_mut(id)
            .ok_or_else(|| Error::UnknownAgent(id.to_string()))?;
        handle.status = status;
        handle.last_output = Instant::now();
        Ok(())
    }

    /// Look up an agent's current status.
    pub fn status(&self, id: &str) -> Option<AgentStatus> {
        self.agents.get(id).map(|h| h.status)
    }

    /// Return the ids of agents that are still `Running` but have produced no
    /// output for at least `max_idle`.
    pub fn stale_agents(&self, max_idle: Duration) -> Vec<String> {
        let now = Instant::now();
        self.agents
            .values()
            .filter(|h| {
                h.status == AgentStatus::Running
                    && now.duration_since(h.last_output) >= max_idle
            })
            .map(|h| h.id.clone())
            .collect()
    }

    /// Route an A2A message to its addressee.
    pub fn route(&self, msg: A2AMessage) -> Result<()> {
        let handle = self
            .agents
            .get(&msg.to)
            .ok_or_else(|| Error::UnknownAgent(msg.to.clone()))?;
        let to = msg.to.clone();
        handle.inbox.send(msg).map_err(|_| Error::ChannelClosed(to))
    }

    /// Number of registered agents.
    pub fn agent_count(&self) -> usize {
        self.agents.len()
    }
}

/// A predicate matching against a [`SystemEvent`] for the visual rule engine.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(tag = "match", rename_all = "snake_case")]
pub enum EventMatch {
    /// Matches any event.
    Any,
    /// Matches a git push.
    GitPush,
    /// Matches a PR opening.
    PrOpened,
    /// Matches a failing test.
    TestFailed,
    /// Matches a deployment failure.
    DeploymentFailed,
    /// Matches a budget warning at or above the given percentage.
    BudgetAtLeast {
        /// Minimum percent of budget consumed.
        pct: u8,
    },
    /// Matches a voice command.
    VoiceCommand,
    /// Matches a cron tick.
    ScheduleCron,
}

impl EventMatch {
    /// Whether this matcher fires for the given event.
    pub fn matches(&self, event: &SystemEvent) -> bool {
        match (self, event) {
            (EventMatch::Any, _) => true,
            (EventMatch::GitPush, SystemEvent::GitPush) => true,
            (EventMatch::PrOpened, SystemEvent::PrOpened) => true,
            (EventMatch::TestFailed, SystemEvent::TestFailed) => true,
            (EventMatch::DeploymentFailed, SystemEvent::DeploymentFailed) => true,
            (EventMatch::VoiceCommand, SystemEvent::VoiceCommand) => true,
            (EventMatch::ScheduleCron, SystemEvent::ScheduleCron) => true,
            (EventMatch::BudgetAtLeast { pct }, SystemEvent::BudgetWarning { pct: got }) => {
                got >= pct
            }
            _ => false,
        }
    }
}

/// An action a [`Rule`] performs when it fires.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(tag = "action", rename_all = "snake_case")]
pub enum Action {
    /// Spawn an agent with the given prompt.
    SpawnAgent {
        /// The prompt to run.
        prompt: String,
    },
    /// Run a shell command.
    RunCommand {
        /// The command line to execute.
        command: String,
    },
    /// Send a notification with the given message.
    Notify {
        /// The notification body.
        message: String,
    },
    /// Enqueue a task at the given priority.
    EnqueueTask {
        /// Task description.
        description: String,
        /// Scheduling priority.
        priority: Priority,
    },
}

/// A visual automation rule: *when* an event matches, *then* run actions.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Rule {
    /// The condition that triggers this rule.
    pub when: EventMatch,
    /// The actions to run when the condition is met.
    pub then: Vec<Action>,
}

impl Rule {
    /// Create a new rule.
    pub fn new(when: EventMatch, then: Vec<Action>) -> Self {
        Self { when, then }
    }

    /// Evaluate the rule against an event, returning the actions to run (empty
    /// if the rule does not match).
    pub fn evaluate(&self, event: &SystemEvent) -> &[Action] {
        if self.when.matches(event) {
            &self.then
        } else {
            &[]
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn priority_queue_pops_critical_before_background() {
        let mut q = PriorityQueue::new();
        q.push(Task::new("a", "background work", Priority::Background));
        q.push(Task::new("b", "critical work", Priority::Critical));
        q.push(Task::new("c", "normal work", Priority::Normal));

        assert_eq!(q.pop().unwrap().id, "b"); // Critical
        assert_eq!(q.pop().unwrap().id, "c"); // Normal
        assert_eq!(q.pop().unwrap().id, "a"); // Background
        assert!(q.pop().is_none());
    }

    #[test]
    fn priority_queue_is_fifo_within_same_priority() {
        let mut q = PriorityQueue::new();
        q.push(Task::new("first", "x", Priority::Normal));
        q.push(Task::new("second", "y", Priority::Normal));
        assert_eq!(q.pop().unwrap().id, "first");
        assert_eq!(q.pop().unwrap().id, "second");
    }

    #[tokio::test]
    async fn event_bus_publish_subscribe_delivers() {
        let bus = EventBus::new();
        let mut rx = bus.subscribe();
        bus.publish(SystemEvent::GitPush).unwrap();
        let got = rx.recv().await.unwrap();
        assert_eq!(got, SystemEvent::GitPush);
    }

    #[test]
    fn event_bus_publish_without_subscribers_errors() {
        let bus = EventBus::new();
        assert!(matches!(
            bus.publish(SystemEvent::GitPush),
            Err(Error::NoSubscribers)
        ));
    }

    #[test]
    fn rule_matcher_fires_on_matching_event() {
        let rule = Rule::new(
            EventMatch::TestFailed,
            vec![Action::Notify {
                message: "tests failed".into(),
            }],
        );
        assert_eq!(rule.evaluate(&SystemEvent::TestFailed).len(), 1);
        assert_eq!(rule.evaluate(&SystemEvent::GitPush).len(), 0);
    }

    #[test]
    fn budget_matcher_respects_threshold() {
        let m = EventMatch::BudgetAtLeast { pct: 80 };
        assert!(m.matches(&SystemEvent::BudgetWarning { pct: 90 }));
        assert!(m.matches(&SystemEvent::BudgetWarning { pct: 80 }));
        assert!(!m.matches(&SystemEvent::BudgetWarning { pct: 50 }));
    }

    #[test]
    fn supervisor_detects_stale_agents() {
        let mut sup = Supervisor::new();
        let _rx = sup.register("agent-1");
        sup.set_status("agent-1", AgentStatus::Running).unwrap();
        // With a zero idle threshold the running agent is immediately stale.
        let stale = sup.stale_agents(Duration::from_secs(0));
        assert_eq!(stale, vec!["agent-1".to_string()]);
        // A huge threshold should report none.
        assert!(sup.stale_agents(Duration::from_secs(3600)).is_empty());
    }

    #[tokio::test]
    async fn supervisor_routes_a2a_messages() {
        let mut sup = Supervisor::new();
        let mut rx = sup.register("worker");
        sup.route(A2AMessage {
            from: "lead".into(),
            to: "worker".into(),
            payload: serde_json::json!({"do": "thing"}),
        })
        .unwrap();
        let msg = rx.recv().await.unwrap();
        assert_eq!(msg.from, "lead");
    }
}
