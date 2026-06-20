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
        // Start with the user's stored agents, or **empty** on first launch —
        // no agents are seeded automatically. The starter set is loaded on demand
        // via Settings → "Load default templates" (``loadDefaults()``). On a
        // decode failure we also show nothing and never overwrite the stored
        // bytes (no `save()` here), so a future migration can recover them.
        self.agents = Self.loadStored() ?? []
    }

    /// Append the shipped default agent templates that aren't already present
    /// (matched by name). Returns how many were added.
    @discardableResult
    func loadDefaults() -> Int {
        let existing = Set(agents.map(\.name))
        let toAdd = AgentTemplates.all.filter { !existing.contains($0.name) }
        for t in toAdd {
            agents.append(AgentDefinition(name: t.name, role: t.role, symbol: t.symbol,
                                          model: t.model, trustMode: t.trustMode,
                                          systemPrompt: t.systemPrompt))
        }
        if !toAdd.isEmpty { save() }
        return toAdd.count
    }

    /// Add a new agent — a blank one, or an editable copy of a shipped template.
    @discardableResult
    func add(from template: AgentDefinition? = nil) -> AgentDefinition {
        let agent: AgentDefinition
        if let t = template {
            agent = AgentDefinition(name: t.name, role: t.role, symbol: t.symbol,
                                    model: t.model, trustMode: t.trustMode, systemPrompt: t.systemPrompt)
        } else {
            agent = AgentDefinition(name: "New Agent", role: "Describe its job", symbol: "sparkles")
        }
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
}
