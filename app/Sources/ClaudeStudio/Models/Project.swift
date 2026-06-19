import Foundation

/// A workspace Claude Studio manages: a git repository, its worktrees, and the
/// agentic configuration (skills, hooks, MCP servers) attached to it.
struct Project: Identifiable, Hashable, Sendable {
    let id: UUID
    var name: String
    var path: String
    var branch: String
    var trustMode: TrustMode
    var worktrees: [Worktree]
    var activeSessionCount: Int
    var skills: [String]
    var mcpServers: [String]
    var lastActivity: Date

    init(id: UUID = UUID(),
         name: String,
         path: String,
         branch: String,
         trustMode: TrustMode,
         worktrees: [Worktree] = [],
         activeSessionCount: Int = 0,
         skills: [String] = [],
         mcpServers: [String] = [],
         lastActivity: Date = .now) {
        self.id = id
        self.name = name
        self.path = path
        self.branch = branch
        self.trustMode = trustMode
        self.worktrees = worktrees
        self.activeSessionCount = activeSessionCount
        self.skills = skills
        self.mcpServers = mcpServers
        self.lastActivity = lastActivity
    }
}

/// A git worktree under a project, where an isolated agent session can run.
struct Worktree: Identifiable, Hashable, Sendable {
    let id: UUID
    var branch: String
    var path: String
    var isDirty: Bool
    var aheadBy: Int

    init(id: UUID = UUID(), branch: String, path: String, isDirty: Bool = false, aheadBy: Int = 0) {
        self.id = id
        self.branch = branch
        self.path = path
        self.isDirty = isDirty
        self.aheadBy = aheadBy
    }
}

extension Project {
    /// Representative sample projects so the UI is populated on first launch
    /// before the Rust core streams real data over IPC.
    static let samples: [Project] = [
        Project(
            name: "claude-studio",
            path: "~/dev/claude-studio",
            branch: "main",
            trustMode: .guarded,
            worktrees: [
                Worktree(branch: "feat/brain-view", path: "~/dev/.wt/brain", isDirty: true, aheadBy: 4),
                Worktree(branch: "fix/ipc-framing", path: "~/dev/.wt/ipc", aheadBy: 1)
            ],
            activeSessionCount: 2,
            skills: ["graphify", "code-review", "tdd"],
            mcpServers: ["qdrant", "filesystem", "github"],
            lastActivity: Date(timeIntervalSinceNow: -120)
        ),
        Project(
            name: "atlas-api",
            path: "~/dev/atlas-api",
            branch: "release/2.4",
            trustMode: .autonomous,
            worktrees: [
                Worktree(branch: "chore/migrate-axum", path: "~/dev/.wt/axum", isDirty: true, aheadBy: 12)
            ],
            activeSessionCount: 1,
            skills: ["security-review", "diagnose"],
            mcpServers: ["postgres", "sentry"],
            lastActivity: Date(timeIntervalSinceNow: -900)
        ),
        Project(
            name: "marketing-site",
            path: "~/dev/marketing-site",
            branch: "main",
            trustMode: .readOnly,
            skills: ["web-design", "seo-audit"],
            mcpServers: ["vercel"],
            lastActivity: Date(timeIntervalSinceNow: -7_200)
        ),
        Project(
            name: "research-notebook",
            path: "~/dev/research-notebook",
            branch: "exp/embeddings",
            trustMode: .unleashed,
            worktrees: [
                Worktree(branch: "exp/embeddings", path: "~/dev/research-notebook", isDirty: true, aheadBy: 31)
            ],
            activeSessionCount: 1,
            skills: ["deep-research"],
            mcpServers: ["qdrant", "arxiv"],
            lastActivity: Date(timeIntervalSinceNow: -45)
        )
    ]
}
