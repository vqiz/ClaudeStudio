import SwiftUI

/// Agent Studio — author reusable agent presets (name, model/effort, trust,
/// system prompt) and run one against the selected project. Persisted.
struct AgentStudioView: View {
    @Environment(AppState.self) private var appState
    @State private var selected: AgentDefinition.ID?

    var body: some View {
        HSplitView {
            VStack(spacing: 0) {
                HStack {
                    Label("Agents", systemImage: "person.crop.rectangle.stack").font(.headline)
                    Spacer()
                    Menu {
                        Button("Blank agent", systemImage: "plus") {
                            selected = appState.agentStore.add().id
                        }
                        Divider()
                        Section("From template") {
                            ForEach(AgentTemplates.all) { template in
                                Button {
                                    selected = appState.agentStore.add(from: template).id
                                } label: {
                                    Label(template.name, systemImage: template.symbol)
                                }
                            }
                        }
                    } label: {
                        Image(systemName: "plus")
                    }
                    .menuStyle(.borderlessButton)
                    .menuIndicator(.hidden)
                    .fixedSize()
                    .help("New agent (blank or from a template)")
                }
                .padding(12)
                .background(.bar)

                List(appState.agentStore.agents, selection: $selected) { agent in
                    AgentRow(agent: agent).tag(agent.id)
                }
            }
            .frame(minWidth: 230, idealWidth: 280, maxWidth: 360, maxHeight: .infinity)

            if let agent = appState.agentStore.agents.first(where: { $0.id == selected }) {
                AgentDetail(agent: agent).id(agent.id)
                    .frame(minWidth: 360, maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ContentUnavailableView("Select an agent",
                                       systemImage: "person.crop.rectangle.stack")
                    .frame(minWidth: 360, maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .navigationTitle("Agent Studio")
        .onAppear { if selected == nil { selected = appState.agentStore.agents.first?.id } }
    }
}

private struct AgentRow: View {
    let agent: AgentDefinition

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: agent.symbol)
                .foregroundStyle(.white)
                .frame(width: 26, height: 26)
                .background(Color.brandIndigo.gradient, in: RoundedRectangle(cornerRadius: 7))
            VStack(alignment: .leading, spacing: 1) {
                Text(agent.name).font(.callout.weight(.medium))
                if !agent.role.isEmpty {
                    Text(agent.role).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
            }
            Spacer()
            Text(agent.model).font(.caption2.monospaced()).foregroundStyle(.secondary)
        }
        .padding(.vertical, 3)
    }
}

private struct AgentDetail: View {
    @Environment(AppState.self) private var appState
    @State private var draft: AgentDefinition
    @State private var task = ""

    init(agent: AgentDefinition) {
        _draft = State(initialValue: agent)
    }

    var body: some View {
        Form {
            Section("Identity") {
                TextField("Name", text: $draft.name)
                TextField("Role", text: $draft.role)
            }

            Section("Behaviour") {
                Picker("Model · effort", selection: $draft.model) {
                    ForEach(ModelTierOption.allCases) { Text($0.label).tag($0.rawValue) }
                }
                Picker("Trust mode", selection: $draft.trustMode) {
                    ForEach(TrustMode.allCases) { Label($0.label, systemImage: $0.symbol).tag($0) }
                }
            }

            Section("System prompt") {
                TextEditor(text: $draft.systemPrompt)
                    .font(.system(.callout, design: .monospaced))
                    .frame(minHeight: 140)
            }

            Section("Run") {
                TextField("Task for this agent…", text: $task, axis: .vertical)
                    .lineLimit(1...4)
                    .onSubmit(run)
                Button {
                    run()
                } label: {
                    Label(runLabel, systemImage: "play.fill")
                }
                .buttonStyle(.borderedProminent)
                .disabled(task.trimmingCharacters(in: .whitespaces).isEmpty
                          || appState.selectedProject == nil
                          || !appState.coreConnected
                          || appState.core.runningSessionId != nil)
                if appState.selectedProject == nil {
                    Text("Select a project (under Projects) to run an agent there.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
        }
        .formStyle(.grouped)
        .navigationTitle(draft.name)
        .onChange(of: draft) { _, newValue in appState.agentStore.update(newValue) }
        .toolbar {
            ToolbarItem {
                Button(role: .destructive) {
                    appState.agentStore.remove(draft.id)
                } label: {
                    Label("Delete", systemImage: "trash")
                }
                .help("Delete this agent")
            }
        }
    }

    private var runLabel: String {
        if let project = appState.selectedProject {
            return "Run on \(project.name)"
        }
        return "Run"
    }

    private func run() {
        guard let project = appState.selectedProject else { return }
        let userTask = task.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !userTask.isEmpty else { return }
        task = ""
        Task {
            await appState.core.startSession(prompt: userTask, cwd: project.path,
                                             model: draft.model, systemPrompt: draft.systemPrompt)
        }
    }
}
