import SwiftUI

/// The Agent Studio (Definitions section) — author and configure the agentic
/// layer: sub-agent roles, the supervisor policy, and the event-bus routing
/// that wires them together.
struct AgentStudioView: View {
    @State private var selectedAgent: AgentDefinition.ID?
    @State private var agents = AgentDefinition.samples

    var body: some View {
        HSplitView {
            List(agents, selection: $selectedAgent) { agent in
                AgentRow(agent: agent).tag(agent.id)
            }
            .frame(minWidth: 240, idealWidth: 280)
            .safeAreaInset(edge: .top) {
                HStack {
                    PageHeader(title: "Agent Studio", symbol: "person.crop.rectangle.stack")
                    Button {} label: { Image(systemName: "plus") }.buttonStyle(.borderless)
                }
                .padding(12).background(.bar)
            }

            if let agent = agents.first(where: { $0.id == selectedAgent }) ?? agents.first {
                AgentDetail(agent: agent)
            } else {
                ContentUnavailableView("Select an agent", systemImage: "person.crop.rectangle.stack")
            }
        }
    }
}

private struct AgentRow: View {
    let agent: AgentDefinition

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: agent.symbol)
                .foregroundStyle(.white)
                .frame(width: 26, height: 26)
                .background(agent.tint.gradient, in: RoundedRectangle(cornerRadius: 7))
            VStack(alignment: .leading, spacing: 1) {
                Text(agent.name).font(.callout.weight(.medium))
                Text(agent.role).font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 3)
    }
}

private struct AgentDetail: View {
    let agent: AgentDefinition

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                PageHeader(title: agent.name, symbol: agent.symbol, subtitle: agent.role)

                GroupBox("System Prompt") {
                    Text(agent.systemPrompt)
                        .font(.callout).textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading).padding(6)
                }
                GroupBox("Default Trust") {
                    HStack { TrustModeBadge(mode: agent.trustMode); Spacer() }.padding(6)
                }
                GroupBox("Tools & Skills") { ChipFlow(items: agent.tools, symbol: "wrench.and.screwdriver") }
                GroupBox("Triggers") {
                    VStack(alignment: .leading, spacing: 6) {
                        ForEach(agent.triggers, id: \.self) { trigger in
                            Label(trigger, systemImage: "bolt.horizontal").font(.caption)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading).padding(6)
                }
            }
            .padding(20)
        }
    }
}

/// A configured sub-agent role in the supervisor topology.
struct AgentDefinition: Identifiable, Hashable {
    let id = UUID()
    var name: String
    var role: String
    var symbol: String
    var tint: Color
    var trustMode: TrustMode
    var systemPrompt: String
    var tools: [String]
    var triggers: [String]

    static let samples: [AgentDefinition] = [
        AgentDefinition(
            name: "Supervisor",
            role: "Plans, delegates, and enforces budget",
            symbol: "eye",
            tint: .orange,
            trustMode: .guarded,
            systemPrompt: "You coordinate sub-agents, split tasks into worktrees, and pause any session that exceeds its budget or attempts a destructive action outside its allow-list.",
            tools: ["TaskCreate", "TaskStop", "Monitor"],
            triggers: ["session.started", "budget.threshold", "approval.required"]
        ),
        AgentDefinition(
            name: "Implementer",
            role: "Writes and edits code",
            symbol: "hammer",
            tint: .blue,
            trustMode: .guarded,
            systemPrompt: "You implement scoped changes with small, reviewable diffs and run the relevant tests before declaring a task complete.",
            tools: ["Read", "Edit", "Bash", "tdd"],
            triggers: ["task.assigned(implement)"]
        ),
        AgentDefinition(
            name: "Reviewer",
            role: "Audits diffs for correctness and security",
            symbol: "checkmark.shield",
            tint: .green,
            trustMode: .readOnly,
            systemPrompt: "You review the working diff for correctness, security, and simplification opportunities. You never modify files; you report findings.",
            tools: ["Read", "Grep", "code-review", "security-review"],
            triggers: ["diff.ready", "pre.commit"]
        ),
        AgentDefinition(
            name: "Librarian",
            role: "Maintains the semantic memory",
            symbol: "books.vertical",
            tint: .pink,
            trustMode: .autonomous,
            systemPrompt: "You consolidate session transcripts into durable memories, dedupe against existing vectors, and update the knowledge graph.",
            tools: ["graphify", "qdrant.upsert"],
            triggers: ["session.completed"]
        )
    ]
}
