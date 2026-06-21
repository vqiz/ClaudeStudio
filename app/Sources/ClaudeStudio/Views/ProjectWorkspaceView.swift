import SwiftUI
import ClaudeStudioKit

/// The per-project workspace: a tabbed surface for one project — overview/git,
/// editable context files, a live session with a skills palette, the agentic
/// "brain" (run a saved agent with a command), and MCP servers.
struct ProjectWorkspaceView: View {
    @Environment(AppState.self) private var appState
    let project: Project

    enum Tab: String, CaseIterable, Identifiable {
        case overview, context, session, agents, mcp
        var id: String { rawValue }
        var title: String {
            switch self {
            case .overview: return "Overview"
            case .context: return "Context"
            case .session: return "Session"
            case .agents: return "Agents"
            case .mcp: return "MCP"
            }
        }
        var symbol: String {
            switch self {
            case .overview: return "square.text.square"
            case .context: return "doc.text"
            case .session: return "sparkles"
            case .agents: return "person.crop.rectangle.stack"
            case .mcp: return "puzzlepiece.extension"
            }
        }
    }

    @State private var tab: Tab = .overview

    var body: some View {
        VStack(spacing: 0) {
            Picker("", selection: $tab) {
                ForEach(Tab.allCases) { t in
                    Label(t.title, systemImage: t.symbol).tag(t)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .frame(maxWidth: .infinity)
            .background(.bar)
            Divider()

            switch tab {
            case .overview: ProjectOverviewTab(project: project)
            case .context: ProjectContextTab(project: project)
            case .session: ProjectSessionTab(project: project)
            case .agents: ProjectAgentsTab(project: project)
            case .mcp: ScrollView { MCPManagerView(cwd: project.path).padding(20) }
            }
        }
        .navigationTitle(project.name)
        // Land on the Session tab when arriving with an active/resumed conversation.
        .onAppear {
            if appState.core.liveClaudeSessionId != nil { tab = .session }
        }
    }
}

// MARK: - Overview

private struct ProjectOverviewTab: View {
    @Environment(AppState.self) private var appState
    let project: Project

    private var branch: String { appState.core.gitByCwd[project.path]?.branch ?? "" }
    private var changes: Int? { appState.core.gitByCwd[project.path]?.changes }
    private var worktrees: [ProjectWorktree] { appState.core.worktreesByCwd[project.path] ?? [] }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: DS.s4) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(project.name).font(.system(size: 26, weight: .bold))
                        Text(project.displayPath)
                            .font(.callout).foregroundStyle(.secondary).textSelection(.enabled)
                    }
                    Spacer()
                    Button(role: .destructive) {
                        appState.projectStore.remove(project.id)
                        appState.selectedProjectID = appState.projects.first?.id
                    } label: {
                        Label("Remove", systemImage: "trash")
                    }
                    .help("Remove from ClaudeStudio (your files are not deleted)")
                }

                HStack(spacing: DS.s3) {
                    StatTile(label: "Branch", value: branch.isEmpty ? "—" : branch,
                             symbol: "arrow.triangle.branch", tint: .brandIndigo)
                    StatTile(label: "Changes", value: changes.map(String.init) ?? "—",
                             symbol: "pencil.line", tint: .brandCoral)
                    modelCard
                }

                agentsCard

                if !worktrees.isEmpty {
                    SectionCard(title: "Worktrees (\(worktrees.count))", symbol: "square.split.2x1") {
                        VStack(alignment: .leading, spacing: 8) {
                            ForEach(worktrees) { wt in
                                HStack(spacing: 8) {
                                    Image(systemName: "arrow.triangle.branch").font(.caption).foregroundStyle(.tint)
                                    Text(wt.branch).font(.callout.weight(.medium))
                                    Text(wt.path).font(.caption).foregroundStyle(.secondary)
                                        .lineLimit(1).truncationMode(.middle)
                                    Spacer()
                                }
                            }
                        }
                    }
                }
            }
            .padding(DS.s4)
        }
        // Re-run when the core connects, so git/worktree data loads even if the
        // view first appeared while the core was still offline. Also re-sync the
        // CLAUDE.md agents block so edits made in Agent Studio propagate.
        .task(id: "\(project.id)#\(appState.coreConnected)") {
            await appState.core.gitInfo(cwd: project.path)
            await appState.core.worktrees(cwd: project.path)
            if !project.assignedAgentIDs.isEmpty {
                await appState.syncProjectAgentsToClaudeMd(project.id)
            }
        }
    }

    /// Assign Agent Studio agents to this project. Toggling one updates the
    /// project and writes the agents into CLAUDE.md in the background.
    private var agentsCard: some View {
        SectionCard(title: "Agents", symbol: "person.2.badge.gearshape") {
            VStack(alignment: .leading, spacing: 10) {
                Text("Assign agents from Agent Studio. Their instructions are written into this project's CLAUDE.md automatically, so every request here follows them.")
                    .font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                if appState.agentStore.agents.isEmpty {
                    Text("No agents yet — create them in Agent Studio.")
                        .font(.caption).foregroundStyle(.tertiary)
                } else {
                    ForEach(appState.agentStore.agents) { agent in
                        Toggle(isOn: agentBinding(agent)) {
                            HStack(spacing: 8) {
                                Image(systemName: agent.symbol).foregroundStyle(.tint).frame(width: 18)
                                VStack(alignment: .leading, spacing: 1) {
                                    Text(agent.name).font(.callout.weight(.medium))
                                    if !agent.role.isEmpty {
                                        Text(agent.role).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                                    }
                                }
                            }
                        }
                        .toggleStyle(.switch)
                    }
                }
            }
        }
    }

    private func agentBinding(_ agent: AgentDefinition) -> Binding<Bool> {
        Binding(
            get: { project.assignedAgentIDs.contains(agent.id) },
            set: { isOn in
                var ids = project.assignedAgentIDs
                if isOn {
                    if !ids.contains(agent.id) { ids.append(agent.id) }
                } else {
                    ids.removeAll { $0 == agent.id }
                }
                appState.projectStore.setAssignedAgents(project.id, agentIDs: ids)
                Task { await appState.syncProjectAgentsToClaudeMd(project.id) }
            }
        )
    }

    private var modelCard: some View {
        VStack(alignment: .leading, spacing: DS.s2) {
            HStack(spacing: 7) {
                Image(systemName: "cpu")
                    .font(.callout.weight(.semibold)).foregroundStyle(Color.brandViolet)
                    .frame(width: 26, height: 26)
                    .background(Color.brandViolet.opacity(0.14), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                Text("MODEL · EFFORT").font(.caption2.weight(.semibold)).foregroundStyle(.secondary).tracking(0.5)
            }
            Picker("Model", selection: Binding(
                get: { project.model },
                set: { appState.projectStore.setModel(project.id, model: $0) }
            )) {
                ForEach(ModelTierOption.allCases) { Text($0.label).tag($0.rawValue) }
            }
            Picker("Effort", selection: Binding(
                get: { project.effort },
                set: { appState.projectStore.setEffort(project.id, effort: $0) }
            )) {
                ForEach(EffortOption.allCases) { Text($0.label).tag($0.rawValue) }
            }
            .font(.caption)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .dsCard(padding: DS.s3, radius: DS.rMd, elevated: false)
    }
}

// MARK: - Context

private struct ProjectContextTab: View {
    let project: Project
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                GroupBox {
                    EditableFileView(path: project.claudeMdPath, minHeight: 220,
                                     template: ContextTemplates.claudeMd)
                } label: { Label("CLAUDE.md", systemImage: "doc.text") }

                GroupBox {
                    EditableFileView(path: project.agentsMdPath, minHeight: 200,
                                     template: ContextTemplates.agentsMd)
                } label: { Label("AGENTS.md", systemImage: "doc.text") }
            }
            .padding(20)
        }
    }
}

// MARK: - Session + skills palette

private struct ProjectSessionTab: View {
    @Environment(AppState.self) private var appState
    let project: Project
    @State private var prompt = ""
    /// Definitions the user applied as context for the next run: path → (name, body).
    @State private var appliedDefs: [String: (name: String, body: String)] = [:]

    /// Installed skills for this project, read instantly from the prefetch cache.
    private var skills: [LibrarySkill] { appState.core.skillsByCwd[project.path] ?? [] }

    var body: some View {
        HSplitView {
            SessionLibrarySidebar(
                skills: skills,
                appliedDefPaths: Set(appliedDefs.keys),
                onInsertSkill: { insert("/\($0) ") },
                onRunSkill: runSkill,
                onInsertTask: insertTask,
                onRunTask: runTask,
                onToggleDefinition: toggleDefinition
            )
            .frame(minWidth: 230, idealWidth: 270, maxWidth: 340, maxHeight: .infinity)

            VStack(spacing: 0) {
                ModelEffortBar(project: project)
                Divider()
                if !appState.core.backgroundTasks.isEmpty {
                    BackgroundTasksPanel(onAddToChat: { insert($0) })
                    Divider()
                }
                LiveTranscriptView()
                if appState.core.runningSessionId != nil {
                    RunningStatusBar(effort: project.effort)
                }
                if !appliedDefs.isEmpty { appliedBar }
                Divider()
                composer
            }
            .frame(minWidth: 380, maxWidth: .infinity, maxHeight: .infinity)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        // Keyed on connection state too, so skills load once the core comes up
        // even if this view appeared while it was offline.
        .task(id: "\(project.path)#\(appState.coreConnected)") {
            await appState.core.skills(cwd: project.path)
        }
    }

    private var appliedBar: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                Text("CONTEXT").font(.caption2.weight(.bold)).foregroundStyle(.secondary).tracking(0.5)
                ForEach(appliedDefs.sorted(by: { $0.key < $1.key }), id: \.key) { path, def in
                    HStack(spacing: 4) {
                        Image(systemName: "books.vertical.fill").font(.caption2)
                        Text(def.name).font(.caption2.weight(.medium)).lineLimit(1)
                        Button { appliedDefs[path] = nil } label: { Image(systemName: "xmark.circle.fill") }
                            .buttonStyle(.plain).foregroundStyle(.secondary)
                    }
                    .padding(.horizontal, 8).padding(.vertical, 4)
                    .background(Color.brandIndigo.opacity(0.16), in: Capsule())
                    .foregroundStyle(Color.brandIndigo)
                }
            }
            .padding(.horizontal, 12).padding(.vertical, 6)
        }
        .background(.bar)
    }

    private var composer: some View {
        HStack(spacing: 8) {
            if appState.core.liveClaudeSessionId != nil && appState.core.runningSessionId == nil {
                Button { appState.core.newChat() } label: {
                    Image(systemName: "square.and.pencil").font(.title3)
                }
                .buttonStyle(.plain)
                .help("New chat — clear this conversation")
            }
            TextField(placeholder, text: $prompt, axis: .vertical)
                .textFieldStyle(.roundedBorder)
                .lineLimit(1...5)
                .onSubmit(run)
                .disabled(appState.core.runningSessionId != nil)
            if appState.core.runningSessionId != nil {
                StopButton()
            } else {
                Button(action: run) {
                    Image(systemName: "arrow.up.circle.fill").font(.title)
                }
                .buttonStyle(.plain)
                .disabled(prompt.trimmingCharacters(in: .whitespaces).isEmpty || !appState.coreConnected)
            }
        }
        .padding(12)
        .background(.bar)
    }

    private var placeholder: String {
        if appState.agentsEnabled(for: project) {
            return "Dispatch a task to your agents — runs in the background…"
        }
        return appState.core.liveClaudeSessionId != nil
            ? "Reply to continue the conversation…"
            : "Ask Claude, or apply a skill / task / definition…"
    }

    private var appliedSystemPrompt: String? {
        let joined = appliedDefs.values.map(\.body).joined(separator: "\n\n---\n\n")
        return joined.isEmpty ? nil : joined
    }

    private func insert(_ token: String) {
        if prompt.isEmpty {
            prompt = token
        } else {
            prompt += (prompt.hasSuffix(" ") || prompt.hasSuffix("\n") ? "" : " ") + token
        }
    }

    private func insertTask(_ task: LibraryTask) {
        let detail = task.summary.isEmpty ? "" : ": \(task.summary)"
        insert("Run the \"\(task.name)\" task\(detail)\n")
    }

    private func toggleDefinition(_ def: LibraryDefinition) {
        if appliedDefs[def.path] != nil {
            appliedDefs[def.path] = nil
            return
        }
        Task {
            if let c = await appState.core.readFile(def.path), c.exists {
                appliedDefs[def.path] = (def.name.isEmpty ? "definition" : def.name, c.content)
            }
        }
    }

    private func run() {
        let text = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        // Assigned-agent workflow: when agents are configured for this project,
        // a request is dispatched to run in the background as its own session
        // (which delegates to the configured sub-agents). The composer frees up
        // immediately, so you can keep sending — several tasks run at once.
        if appState.agentsEnabled(for: project) {
            prompt = ""
            Task { await appState.dispatchProjectTask(prompt: text, project: project) }
            return
        }
        // Plain foreground conversation (no agents configured).
        guard appState.core.runningSessionId == nil else { return }
        prompt = ""
        let continuing = appState.core.liveClaudeSessionId != nil
        Task {
            await appState.core.startSession(
                prompt: text, cwd: project.path, model: project.model,
                systemPrompt: continuing ? nil : appliedSystemPrompt,
                effort: project.effort, origin: "session", append: continuing)
        }
    }

    private func runSkill(_ command: String) {
        guard appState.core.runningSessionId == nil else { return }
        Task { await appState.core.startSession(prompt: "/\(command)", cwd: project.path,
                                                model: project.model, systemPrompt: appliedSystemPrompt,
                                                effort: project.effort, origin: "skill") }
    }

    private func runTask(_ task: LibraryTask) {
        guard appState.core.runningSessionId == nil else { return }
        let detail = task.summary.isEmpty ? "" : " \(task.summary)"
        Task { await appState.core.startSession(prompt: "Run the \"\(task.name)\" task on this project.\(detail)",
                                                cwd: project.path, model: project.model,
                                                systemPrompt: appliedSystemPrompt, effort: project.effort,
                                                origin: "task") }
    }
}

/// A compact bar to change the model tier and reasoning effort for this
/// project's sessions, live. Both persist to the project immediately.
struct ModelEffortBar: View {
    @Environment(AppState.self) private var appState
    let project: Project

    var body: some View {
        HStack(spacing: 10) {
            menu(title: "Model", symbol: "cpu", value: project.model,
                 options: ModelTierOption.allCases.map { ($0.rawValue, $0.label, $0.short) },
                 set: { appState.projectStore.setModel(project.id, model: $0) })
            menu(title: "Effort", symbol: "gauge.with.dots.needle.67percent", value: project.effort,
                 options: EffortOption.allCases.map { ($0.rawValue, $0.label, $0.short) },
                 set: { appState.projectStore.setEffort(project.id, effort: $0) })
            Spacer()
        }
        .padding(.horizontal, 12).padding(.vertical, 8)
        .background(.bar)
    }

    private func menu(title: String, symbol: String, value: String,
                      options: [(id: String, label: String, short: String)],
                      set: @escaping (String) -> Void) -> some View {
        Menu {
            ForEach(options, id: \.id) { opt in
                Button { set(opt.id) } label: {
                    if value == opt.id {
                        Label(opt.label, systemImage: "checkmark")
                    } else {
                        Text(opt.label)
                    }
                }
            }
        } label: {
            HStack(spacing: 5) {
                Image(systemName: symbol).foregroundStyle(.brandRich)
                Text(title).font(.caption.weight(.medium)).foregroundStyle(.secondary)
                Text(options.first { $0.id == value }?.short ?? value.capitalized)
                    .font(.caption.weight(.semibold))
                Image(systemName: "chevron.up.chevron.down").font(.caption2).foregroundStyle(.secondary)
            }
            .padding(.horizontal, 10).padding(.vertical, 5)
            .background(.background.secondary, in: Capsule())
            .overlay(Capsule().strokeBorder(.primary.opacity(0.08), lineWidth: 1))
            .contentShape(Capsule())
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        .help("Change \(title.lowercased()) for this project's sessions")
    }
}

/// The Session tab's left library: installed Skills, the Task library, and the
/// Definition library — each appliable to the running session. Skills/tasks
/// insert into the composer (or run via ▶); definitions toggle on as context.
private struct SessionLibrarySidebar: View {
    @Environment(AppState.self) private var appState
    let skills: [LibrarySkill]
    let appliedDefPaths: Set<String>
    let onInsertSkill: (String) -> Void
    let onRunSkill: (String) -> Void
    let onInsertTask: (LibraryTask) -> Void
    let onRunTask: (LibraryTask) -> Void
    let onToggleDefinition: (LibraryDefinition) -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: DS.s3) {
                group("Skills", "wand.and.stars", count: skills.count) {
                    ForEach(skills) { skill in
                        applyRow(title: "/\(skill.command)", subtitle: skill.description,
                                 accent: skill.scope == "project" ? .brandCoral : .brandIndigo,
                                 info: skill.description,
                                 onTap: { onInsertSkill(skill.command) }, onRun: { onRunSkill(skill.command) })
                    }
                }
                group("Tasks", "square.grid.2x2", count: appState.core.tasks.count) {
                    ForEach(appState.core.tasks) { task in
                        applyRow(title: task.name, subtitle: task.summary,
                                 accent: task.writable ? .brandViolet : .secondary,
                                 onTap: { onInsertTask(task) }, onRun: { onRunTask(task) })
                    }
                }
                group("Definitions", "books.vertical", count: appState.core.definitions.count) {
                    ForEach(appState.core.definitions) { def in
                        toggleRow(title: def.name.isEmpty ? "(unnamed)" : def.name,
                                  subtitle: def.category,
                                  applied: appliedDefPaths.contains(def.path),
                                  onToggle: { onToggleDefinition(def) })
                    }
                }
            }
            .padding(12)
        }
        .background(.bar.opacity(0.35))
    }

    @ViewBuilder
    private func group<Content: View>(_ title: String, _ symbol: String, count: Int,
                                      @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Image(systemName: symbol).font(.caption2.weight(.bold)).foregroundStyle(.brandRich)
                Text(title.uppercased()).font(.caption2.weight(.bold)).tracking(0.6).foregroundStyle(.secondary)
                Spacer()
                Text("\(count)").font(.caption2).foregroundStyle(.tertiary)
            }
            if count == 0 {
                Text("None").font(.caption2).foregroundStyle(.tertiary).padding(.leading, 2)
            } else {
                content()
            }
        }
    }

    private func applyRow(title: String, subtitle: String, accent: Color,
                          info: String? = nil,
                          onTap: @escaping () -> Void, onRun: @escaping () -> Void) -> some View {
        HStack(spacing: 6) {
            Button(action: onTap) {
                VStack(alignment: .leading, spacing: 1) {
                    Text(title).font(.caption.weight(.semibold)).lineLimit(1)
                    if !subtitle.isEmpty {
                        Text(subtitle).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if let info {
                InfoPopoverButton(title: title, explanation: info)
            }
            Button(action: onRun) { Image(systemName: "play.circle.fill").foregroundStyle(accent) }
                .buttonStyle(.plain)
                .disabled(appState.core.runningSessionId != nil)
                .help("Run now")
        }
        .padding(.horizontal, 9).padding(.vertical, 7)
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: DS.rSm, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: DS.rSm, style: .continuous).strokeBorder(.primary.opacity(0.06), lineWidth: 1))
    }

    private func toggleRow(title: String, subtitle: String, applied: Bool,
                           onToggle: @escaping () -> Void) -> some View {
        Button(action: onToggle) {
            HStack(spacing: 8) {
                Image(systemName: applied ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(applied ? Color.brandIndigo : .secondary)
                VStack(alignment: .leading, spacing: 1) {
                    Text(title).font(.caption.weight(.semibold)).lineLimit(1)
                    if !subtitle.isEmpty {
                        Text(subtitle).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                    }
                }
                Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
            .padding(.horizontal, 9).padding(.vertical, 7)
            .background((applied ? Color.brandIndigo.opacity(0.12) : Color.clear), in: RoundedRectangle(cornerRadius: DS.rSm, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: DS.rSm, style: .continuous).strokeBorder(.primary.opacity(0.06), lineWidth: 1))
        }
        .buttonStyle(.plain)
        .help(applied ? "Applied as context — click to remove" : "Apply this definition as context")
    }
}

/// A small ⓘ button that reveals a short explanation in a popover. Used next to
/// each skill so the user can read what it does without running it.
private struct InfoPopoverButton: View {
    let title: String
    let explanation: String
    @State private var show = false

    var body: some View {
        Button { show.toggle() } label: {
            Image(systemName: "info.circle")
                .font(.caption)
                .foregroundStyle(.secondary)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help("What this skill does")
        .popover(isPresented: $show, arrowEdge: .trailing) {
            VStack(alignment: .leading, spacing: 6) {
                Text(title).font(.caption.weight(.bold))
                Text(explanation.isEmpty ? "No description provided for this skill." : explanation)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(12)
            .frame(width: 260)
        }
    }
}

/// Lists the project's background "assigned-agent" tasks — running concurrently
/// with the main conversation. Each row shows status, the assigned agent, how
/// many sub-agents it spawned, and (expanded) its result, which can be added
/// back into the chat.
private struct BackgroundTasksPanel: View {
    @Environment(AppState.self) private var appState
    let onAddToChat: (String) -> Void
    @State private var expanded: Set<String> = []

    private var tasks: [BackgroundTask] { appState.core.backgroundTasks }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 6) {
                Image(systemName: "person.2.badge.gearshape").font(.caption2.weight(.bold)).foregroundStyle(.brandRich)
                Text("BACKGROUND TASKS").font(.caption2.weight(.bold)).tracking(0.5).foregroundStyle(.secondary)
                Spacer()
                let running = tasks.filter { $0.status == .running }.count
                Text(running > 0 ? "\(running) running · \(tasks.count) total" : "\(tasks.count) total")
                    .font(.caption2).foregroundStyle(.tertiary)
            }
            .padding(.horizontal, 12).padding(.vertical, 6)
            ScrollView {
                VStack(spacing: 6) {
                    ForEach(tasks) { task in row(task) }
                }
                .padding(.horizontal, 10).padding(.bottom, 8)
            }
            .frame(maxHeight: 220)
        }
        .background(.bar.opacity(0.4))
    }

    @ViewBuilder
    private func row(_ task: BackgroundTask) -> some View {
        let isOpen = expanded.contains(task.id)
        VStack(alignment: .leading, spacing: 5) {
            HStack(spacing: 8) {
                statusIcon(task.status).frame(width: 16)
                VStack(alignment: .leading, spacing: 1) {
                    Text(task.title).font(.caption.weight(.semibold)).lineLimit(1)
                    HStack(spacing: 6) {
                        Text(task.agentName).font(.caption2.weight(.medium)).foregroundStyle(.tint)
                        if task.subAgentCount > 0 {
                            Label("\(task.subAgentCount)", systemImage: "arrow.triangle.branch")
                                .font(.caption2).foregroundStyle(.secondary)
                        }
                        if task.costUSD > 0 {
                            Text(String(format: "$%.4f", task.costUSD)).font(.caption2).foregroundStyle(.tertiary)
                        }
                    }
                }
                Spacer()
                if task.status == .running {
                    Button { appState.core.stopBackgroundTask(task.id) } label: {
                        Image(systemName: "stop.circle")
                    }.buttonStyle(.plain).foregroundStyle(.secondary).help("Stop this task")
                } else {
                    Button { appState.core.dismissBackgroundTask(task.id) } label: {
                        Image(systemName: "xmark.circle")
                    }.buttonStyle(.plain).foregroundStyle(.secondary).help("Dismiss")
                }
                Button { toggle(task.id) } label: {
                    Image(systemName: isOpen ? "chevron.up" : "chevron.down").font(.caption2)
                }.buttonStyle(.plain).foregroundStyle(.secondary)
            }
            if isOpen {
                if task.result.isEmpty {
                    Text(task.status == .running ? "Working…" : "No output.")
                        .font(.caption2).foregroundStyle(.tertiary)
                } else {
                    Text(task.result).font(.caption2).foregroundStyle(.secondary)
                        .textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                    if task.status != .running {
                        Button("Add result to chat") { onAddToChat(task.result) }
                            .font(.caption2).buttonStyle(.borderless)
                    }
                }
            }
        }
        .padding(8)
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: DS.rSm, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: DS.rSm, style: .continuous).strokeBorder(.primary.opacity(0.06), lineWidth: 1))
    }

    @ViewBuilder
    private func statusIcon(_ status: BackgroundTask.Status) -> some View {
        switch status {
        case .running: ProgressView().controlSize(.small)
        case .done: Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
        case .failed: Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.red)
        case .stopped: Image(systemName: "stop.circle.fill").foregroundStyle(.secondary)
        }
    }

    private func toggle(_ id: String) {
        if expanded.contains(id) { expanded.remove(id) } else { expanded.insert(id) }
    }
}

/// The streamed transcript of the currently-running live Claude session.
struct LiveTranscriptView: View {
    @Environment(AppState.self) private var appState

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 10) {
                if appState.core.liveSession.isEmpty {
                    ContentUnavailableView(
                        "No live output yet",
                        systemImage: "text.cursor",
                        description: Text("Run a prompt, skill, or agent — the core spawns the Claude CLI and streams here.")
                    )
                    .padding(.top, 36)
                }
                ForEach(appState.core.liveSession) { item in
                    LiveTranscriptRow(item: item).id(item.id)
                }
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        // Stick to the newest message as output streams in, but leave the user
        // alone the moment they scroll up to read earlier output.
        .defaultScrollAnchor(.bottom)
        .frame(minHeight: 200)
    }
}

private struct LiveTranscriptRow: View {
    let item: LiveSessionEvent

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: item.symbol)
                .font(.caption2.weight(.bold)).foregroundStyle(.white)
                .frame(width: 24, height: 24)
                .background(color.gradient, in: RoundedRectangle(cornerRadius: 7, style: .continuous))
            content
            Spacer(minLength: 0)
        }
    }

    @ViewBuilder
    private var content: some View {
        switch item.kind {
        case "user":
            VStack(alignment: .leading, spacing: 2) {
                Text("You").font(.caption2.weight(.semibold)).foregroundStyle(.secondary)
                Text(item.text).font(.callout.weight(.medium)).textSelection(.enabled)
            }
        case "tool_use":
            VStack(alignment: .leading, spacing: 3) {
                Text(item.text).font(.callout.weight(.semibold))
                if let detail = item.detail, !detail.isEmpty {
                    Text(detail)
                        .font(.system(.caption, design: .monospaced))
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                        .lineLimit(4)
                        .padding(.horizontal, 8).padding(.vertical, 5)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 6))
                }
            }
        case "tool_result":
            Text(item.text)
                .font(.system(.caption, design: .monospaced))
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
                .lineLimit(8)
        case "result":
            Text(item.text).font(.caption.weight(.medium)).foregroundStyle(.green)
        case "error":
            Text(item.text).font(.callout).foregroundStyle(.red).textSelection(.enabled)
        default:
            Text(item.text).font(.callout).textSelection(.enabled)
        }
    }

    private var color: Color {
        switch item.kind {
        case "user": return .blue
        case "assistant_text": return .brandViolet
        case "tool_use": return .brandIndigo
        case "tool_result": return .gray
        case "result": return .green
        case "error": return .red
        default: return .secondary
        }
    }
}

// MARK: - Agents (live sub-agents Claude is running)

/// Shows the sub-agents Claude spawns *itself* during a session (via the Task
/// tool, guided by CLAUDE.md / context) — live, with their status — rather than
/// any predefined presets. Give Claude a task below and watch the agents appear.
private struct ProjectAgentsTab: View {
    @Environment(AppState.self) private var appState
    let project: Project
    @State private var prompt = ""

    var body: some View {
        VStack(spacing: 0) {
            ModelEffortBar(project: project)
            Divider()
            LiveAgentsPanel()
            Divider()
            LiveTranscriptView()
            if appState.core.runningSessionId != nil {
                RunningStatusBar(effort: project.effort)
            }
            Divider()
            HStack(spacing: 8) {
                TextField("Give Claude a task — it spawns the sub-agents it needs…",
                          text: $prompt, axis: .vertical)
                    .textFieldStyle(.roundedBorder)
                    .lineLimit(1...4)
                    .onSubmit(run)
                    .disabled(appState.core.runningSessionId != nil)
                if appState.core.runningSessionId != nil {
                    StopButton()
                } else {
                    Button(action: run) { Label("Run", systemImage: "play.fill") }
                        .buttonStyle(.brand)
                        .disabled(prompt.trimmingCharacters(in: .whitespaces).isEmpty
                                  || !appState.coreConnected)
                }
            }
            .padding(12)
            .background(.bar)
        }
    }

    private func run() {
        let text = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, appState.core.runningSessionId == nil else { return }
        prompt = ""
        Task {
            await appState.core.startSession(prompt: text, cwd: project.path,
                                             model: project.model, effort: project.effort,
                                             origin: "session")
        }
    }
}

/// The live list of sub-agents Claude has spawned in the current session.
/// Clicking one reveals its prompt and (when finished) its result.
struct LiveAgentsPanel: View {
    @Environment(AppState.self) private var appState
    @State private var expanded: String?

    private var agents: [LiveAgent] { appState.core.liveAgents }
    private var runningCount: Int { agents.filter { $0.status == .running }.count }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 7) {
                Image(systemName: "person.2.fill").font(.callout.weight(.semibold)).foregroundStyle(.brandRich)
                Text("SUB-AGENTS").font(.caption2.weight(.bold)).tracking(0.6).foregroundStyle(.secondary)
                Spacer()
                if runningCount > 0 {
                    HStack(spacing: 5) {
                        ProgressView().controlSize(.small)
                        Text("\(runningCount) running").font(.caption2).foregroundStyle(.secondary)
                    }
                } else {
                    Text("\(agents.count)").font(.caption2).foregroundStyle(.tertiary)
                }
            }
            if agents.isEmpty {
                Text("When Claude spawns sub-agents (the Task tool) — guided by your CLAUDE.md / context — they appear here live. Click one to see its prompt and result.")
                    .font(.caption).foregroundStyle(.secondary)
            } else {
                ScrollView {
                    VStack(spacing: 6) {
                        ForEach(agents) { agent in row(agent) }
                    }
                }
                .frame(maxHeight: 240)
            }
        }
        .padding(12)
        .background(.bar)
    }

    private func row(_ agent: LiveAgent) -> some View {
        let isOpen = expanded == agent.id
        return VStack(alignment: .leading, spacing: 8) {
            Button {
                withAnimation(.easeInOut(duration: 0.18)) { expanded = isOpen ? nil : agent.id }
            } label: {
                HStack(alignment: .top, spacing: 9) {
                    statusIcon(agent.status)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(agent.kind).font(.callout.weight(.semibold))
                        if !agent.task.isEmpty {
                            Text(agent.task).font(.caption).foregroundStyle(.secondary).lineLimit(isOpen ? nil : 2)
                        }
                    }
                    Spacer(minLength: 0)
                    statusBadge(agent.status)
                    Image(systemName: isOpen ? "chevron.up" : "chevron.down")
                        .font(.caption2).foregroundStyle(.tertiary)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if isOpen {
                if !agent.prompt.isEmpty {
                    chatBlock("Prompt", agent.prompt, "arrow.up.right", .brandIndigo)
                }
                switch agent.status {
                case .running:
                    Label("Working…", systemImage: "ellipsis").font(.caption).foregroundStyle(.secondary)
                case .stopped:
                    Label("Stopped before it finished.", systemImage: "stop.circle").font(.caption).foregroundStyle(.orange)
                case .done:
                    chatBlock("Result", agent.result.isEmpty ? "(no output)" : agent.result,
                              "checkmark.seal", .green)
                }
            }
        }
        .padding(.horizontal, 10).padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: DS.rSm, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: DS.rSm, style: .continuous).strokeBorder(.primary.opacity(0.06), lineWidth: 1))
    }

    private func chatBlock(_ title: String, _ body: String, _ symbol: String, _ tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Label(title, systemImage: symbol).font(.caption2.weight(.semibold)).foregroundStyle(tint)
            ScrollView {
                Text(body)
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxHeight: 160)
            .padding(8)
            .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 7))
        }
    }

    @ViewBuilder
    private func statusIcon(_ status: LiveAgent.Status) -> some View {
        switch status {
        case .running: ProgressView().controlSize(.small).frame(width: 18, height: 18)
        case .done: Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
        case .stopped: Image(systemName: "stop.circle.fill").foregroundStyle(.orange)
        }
    }

    private func statusBadge(_ status: LiveAgent.Status) -> some View {
        let (text, color): (String, Color)
        switch status {
        case .running: (text, color) = ("running", .brandIndigo)
        case .done: (text, color) = ("done", .green)
        case .stopped: (text, color) = ("stopped", .orange)
        }
        return Text(text)
            .font(.caption2.weight(.semibold))
            .padding(.horizontal, 7).padding(.vertical, 2)
            .background(color.opacity(0.16), in: Capsule())
            .foregroundStyle(color)
    }
}

/// A CLI-style "working" status bar: a spinner, a rotating verb, the elapsed
/// time, and the current reasoning effort — shown while a run is in flight.
struct RunningStatusBar: View {
    var effort: String?

    @State private var elapsed = 0
    @State private var verbIndex = 0
    private let verbs = ["Thinking", "Pondering", "Working", "Cooking",
                         "Crunching", "Reasoning", "Synthesizing", "Almost done"]
    private let tick = Timer.publish(every: 1, on: .main, in: .common).autoconnect()

    var body: some View {
        HStack(spacing: 8) {
            ProgressView().controlSize(.small)
            Text("\(verbs[verbIndex % verbs.count])…")
                .font(.callout.weight(.semibold)).foregroundStyle(.brandRich)
            Text("·").foregroundStyle(.tertiary)
            Text("\(elapsed)s").font(.callout.monospacedDigit()).foregroundStyle(.secondary)
            if let effort, !effort.isEmpty {
                Text("· \(effort) effort").font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Text("Stop to interrupt").font(.caption2).foregroundStyle(.tertiary)
        }
        .padding(.horizontal, 12).padding(.vertical, 7)
        .frame(maxWidth: .infinity)
        .background(.bar)
        .onReceive(tick) { _ in
            elapsed += 1
            if elapsed.isMultiple(of: 4) { verbIndex += 1 }
        }
    }
}

/// A red Stop button that kills the running session via the core.
struct StopButton: View {
    @Environment(AppState.self) private var appState
    var body: some View {
        Button(role: .destructive) {
            Task { await appState.core.stopSession() }
        } label: {
            Label("Stop", systemImage: "stop.fill")
        }
        .buttonStyle(.bordered)
        .tint(.red)
        .help("Stop the running session")
    }
}
