import Foundation
import Observation

/// A workspace ClaudeStudio manages: a real folder on disk plus the model/effort
/// to run sessions there. Persisted across launches via ``ProjectStore``.
struct Project: Identifiable, Hashable, Codable, Sendable {
    let id: UUID
    var name: String
    /// Absolute folder path (the session working directory).
    var path: String
    /// Per-project model / reasoning effort: `haiku`, `sonnet`, or `opus`.
    var model: String
    var addedAt: Date

    init(id: UUID = UUID(), name: String, path: String, model: String = "sonnet", addedAt: Date = .now) {
        self.id = id
        self.name = name
        self.path = path
        self.model = model
        self.addedAt = addedAt
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

/// The model tiers offered as a project's effort level.
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
}
