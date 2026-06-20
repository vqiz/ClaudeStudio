import Foundation
import Observation

/// A reusable agent preset: a name, a model/effort, a trust posture, and a system
/// prompt. Running an agent starts a real session whose prompt is the system
/// prompt plus your task. Persisted across launches via ``AgentStore``.
struct AgentDefinition: Identifiable, Hashable, Codable, Sendable {
    let id: UUID
    var name: String
    var role: String
    var symbol: String
    var model: String          // haiku | sonnet | opus
    var trustMode: TrustMode
    var systemPrompt: String

    init(id: UUID = UUID(),
         name: String,
         role: String = "",
         symbol: String = "person.fill",
         model: String = "sonnet",
         trustMode: TrustMode = .guarded,
         systemPrompt: String = "") {
        self.id = id
        self.name = name
        self.role = role
        self.symbol = symbol
        self.model = model
        self.trustMode = trustMode
        self.systemPrompt = systemPrompt
    }
}

@Observable
@MainActor
final class AgentStore {
    private(set) var agents: [AgentDefinition]

    private static let storageKey = "claudestudio.agents"

    init() {
        switch Self.loadStored() {
        case .some(let stored):
            // Stored data exists (even if empty) — adopt it verbatim.
            self.agents = stored
        case .none where UserDefaults.standard.data(forKey: Self.storageKey) == nil:
            // Truly first launch: seed the starter presets and persist them.
            self.agents = Self.defaults
            save()
        case .none:
            // Data is present but undecodable (e.g. a schema change). Show the
            // defaults in memory but DO NOT overwrite the stored bytes, so a
            // future migration can still recover the user's real agents.
            self.agents = Self.defaults
        }
    }

    @discardableResult
    func add() -> AgentDefinition {
        let agent = AgentDefinition(name: "New Agent", role: "Describe its job", symbol: "sparkles")
        agents.append(agent)
        save()
        return agent
    }

    func remove(_ id: AgentDefinition.ID) {
        agents.removeAll { $0.id == id }
        save()
    }

    /// Replace an agent by id (called as the inspector edits fields).
    func update(_ agent: AgentDefinition) {
        guard let index = agents.firstIndex(where: { $0.id == agent.id }) else { return }
        agents[index] = agent
        save()
    }

    func binding(for id: AgentDefinition.ID) -> AgentDefinition? {
        agents.first { $0.id == id }
    }

    private func save() {
        if let data = try? JSONEncoder().encode(agents) {
            UserDefaults.standard.set(data, forKey: Self.storageKey)
        }
    }

    /// Returns the decoded agents, or `nil` when there is no stored data **or**
    /// the stored data fails to decode. The caller disambiguates the two by
    /// checking for the raw key before deciding whether to overwrite.
    private static func loadStored() -> [AgentDefinition]? {
        guard let data = UserDefaults.standard.data(forKey: storageKey) else { return nil }
        return try? JSONDecoder().decode([AgentDefinition].self, from: data)
    }

    /// Sensible, runnable starter agents (not placeholders — real presets).
    static let defaults: [AgentDefinition] = [
        AgentDefinition(
            name: "Implementer",
            role: "Writes and edits code",
            symbol: "hammer.fill",
            model: "sonnet",
            trustMode: .guarded,
            systemPrompt: "You implement scoped changes with small, reviewable diffs. Run the relevant tests before declaring a task complete, and explain what you changed."
        ),
        AgentDefinition(
            name: "Reviewer",
            role: "Audits diffs for correctness & security",
            symbol: "checkmark.shield.fill",
            model: "opus",
            trustMode: .readOnly,
            systemPrompt: "Review the working diff for correctness, security, and simplification opportunities. Do not modify files — report concrete findings with file:line references."
        ),
        AgentDefinition(
            name: "Researcher",
            role: "Investigates and summarises",
            symbol: "magnifyingglass",
            model: "sonnet",
            trustMode: .guarded,
            systemPrompt: "Investigate the question across the codebase and summarise concisely. Cite files and line numbers; do not change anything."
        ),
    ]
}
