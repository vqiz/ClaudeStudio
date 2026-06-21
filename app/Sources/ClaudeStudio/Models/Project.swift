import Foundation
import Observation

/// A workspace ClaudeStudio manages: a real folder on disk plus the model/effort
/// to run sessions there. Persisted across launches via ``ProjectStore``.
struct Project: Identifiable, Hashable, Codable, Sendable {
    let id: UUID
    var name: String
    /// Absolute folder path (the session working directory).
    var path: String
    /// Per-project model tier: `haiku`, `sonnet`, or `opus`.
    var model: String
    /// Per-project reasoning effort (`--effort`): low/medium/high/xhigh/max.
    var effort: String
    var addedAt: Date

    init(id: UUID = UUID(), name: String, path: String,
         model: String = "sonnet", effort: String = "medium", addedAt: Date = .now) {
        self.id = id
        self.name = name
        self.path = path
        self.model = model
        self.effort = effort
        self.addedAt = addedAt
    }

    enum CodingKeys: String, CodingKey { case id, name, path, model, effort, addedAt }

    /// Tolerant decoder so projects saved before `effort` existed still load
    /// (the missing field defaults instead of failing the whole list).
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(UUID.self, forKey: .id)
        name = try c.decode(String.self, forKey: .name)
        path = try c.decode(String.self, forKey: .path)
        model = try c.decodeIfPresent(String.self, forKey: .model) ?? "sonnet"
        effort = try c.decodeIfPresent(String.self, forKey: .effort) ?? "medium"
        addedAt = try c.decodeIfPresent(Date.self, forKey: .addedAt) ?? .now
    }

    /// `~`-abbreviated path for display.
    var displayPath: String {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return path.hasPrefix(home) ? "~" + path.dropFirst(home.count) : path
    }

    /// Conventional context-file paths under this project.
    var claudeMdPath: String { (path as NSString).appendingPathComponent("CLAUDE.md") }
    var agentsMdPath: String { (path as NSString).appendingPathComponent("AGENTS.md") }
}

/// Persists the user's projects (real folders) to `UserDefaults`. No sample data:
/// the list starts empty and the user adds their own folders.
@Observable
@MainActor
final class ProjectStore {
    private(set) var projects: [Project]

    private static let storageKey = "claudestudio.projects"

    init() {
        self.projects = Self.load()
    }

    /// Add a folder as a project (de-duplicated by path); returns the project.
    @discardableResult
    func add(path: String) -> Project {
        if let existing = projects.first(where: { $0.path == path }) { return existing }
        let project = Project(name: URL(fileURLWithPath: path).lastPathComponent, path: path)
        projects.append(project)
        save()
        return project
    }

    func remove(_ id: Project.ID) {
        projects.removeAll { $0.id == id }
        save()
    }

    func setModel(_ id: Project.ID, model: String) {
        guard let index = projects.firstIndex(where: { $0.id == id }) else { return }
        projects[index].model = model
        save()
    }

    func setEffort(_ id: Project.ID, effort: String) {
        guard let index = projects.firstIndex(where: { $0.id == id }) else { return }
        projects[index].effort = effort
        save()
    }

    private func save() {
        if let data = try? JSONEncoder().encode(projects) {
            UserDefaults.standard.set(data, forKey: Self.storageKey)
        }
    }

    private static func load() -> [Project] {
        guard let data = UserDefaults.standard.data(forKey: storageKey),
              let list = try? JSONDecoder().decode([Project].self, from: data) else { return [] }
        return list
    }
}

/// A git worktree of a project (from `git.worktrees`).
struct ProjectWorktree: Identifiable, Sendable, Hashable {
    var id: String { path }
    let branch: String
    let path: String
}

/// The model tiers offered for a project / session.
enum ModelTierOption: String, CaseIterable, Identifiable {
    case haiku, sonnet, opus
    var id: String { rawValue }
    var label: String {
        switch self {
        case .haiku: return "Haiku · fast"
        case .sonnet: return "Sonnet · balanced"
        case .opus: return "Opus · deep"
        }
    }
    var short: String { rawValue.capitalized }
}

/// The reasoning-effort levels offered in the UI. `low…max` map 1:1 to the
/// `claude` CLI's `--effort`; `ultra` is a ClaudeStudio addition that runs at
/// the CLI maximum **and** injects an ultrathink directive for the deepest
/// reasoning (see ``cliValue`` / ``promptDirective``).
enum EffortOption: String, CaseIterable, Identifiable {
    case low, medium, high, xhigh, max, ultra
    var id: String { rawValue }
    var label: String {
        switch self {
        case .low: return "Low · quickest"
        case .medium: return "Medium · default"
        case .high: return "High · deeper"
        case .xhigh: return "X-High · harder"
        case .max: return "Max · exhaustive"
        case .ultra: return "Ultra · deepest (max + ultrathink)"
        }
    }
    var short: String {
        switch self {
        case .xhigh: return "X-High"
        case .ultra: return "Ultra"
        default: return rawValue.capitalized
        }
    }

    /// The value sent to `claude --effort`. `ultra` has no CLI level of its own,
    /// so it runs at `max`.
    var cliValue: String { self == .ultra ? "max" : rawValue }

    /// A directive appended to the prompt for this level (only `ultra`), giving
    /// the model an explicit cue to reason as deeply as possible.
    var promptDirective: String? { self == .ultra ? "ultrathink" : nil }
}
