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
    }
}

// MARK: - Overview

private struct ProjectOverviewTab: View {
    @Environment(AppState.self) private var appState
    let project: Project

    @State private var branch = ""
    @State private var changes: Int?
    @State private var worktrees: [ProjectWorktree] = []

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
        .task(id: project.id) { await loadGit() }
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
            Picker("", selection: Binding(
                get: { project.model },
                set: { appState.projectStore.setModel(project.id, model: $0) }
            )) {
                ForEach(ModelTierOption.allCases) { Text($0.label).tag($0.rawValue) }
            }
            .labelsHidden()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .dsCard(padding: DS.s3, radius: DS.rMd, elevated: false)
    }

    private func loadGit() async {
        branch = ""; changes = nil; worktrees = []
        if let info = await appState.core.gitInfo(cwd: project.path) {
            branch = info.branch; changes = info.changes
        }
        worktrees = await appState.core.worktrees(cwd: project.path)
    }
}

// MARK: - Context

private struct ProjectContextTab: View {
    let project: Project
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                GroupBox {
                    EditableFileView(path: project.claudeMdPath, minHeight: 220)
                } label: { Label("CLAUDE.md", systemImage: "doc.text") }

                GroupBox {
                    EditableFileView(path: project.agentsMdPath, minHeight: 160)
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
    @State private var skills: [LibrarySkill] = []

    var body: some View {
        VStack(spacing: 0) {
            if !skills.isEmpty {
                SkillsPaletteView(skills: skills, onInsert: insert, onRun: runSkill)
                Divider()
            }
            LiveTranscriptView()
            Divider()
            HStack(spacing: 8) {
                TextField("Ask Claude, or click a skill to insert /command…", text: $prompt, axis: .vertical)
                    .textFieldStyle(.roundedBorder)
                    .lineLimit(1...4)
                    .onSubmit(run)
                Button(action: run) {
                    Image(systemName: "arrow.up.circle.fill").font(.title2)
                }
                .buttonStyle(.plain)
                .disabled(prompt.trimmingCharacters(in: .whitespaces).isEmpty
                          || !appState.coreConnected
                          || appState.core.runningSessionId != nil)
            }
            .padding(12)
            .background(.bar)
        }
        .task(id: project.path) { skills = await appState.core.skills(cwd: project.path) }
    }

    private func insert(_ command: String) {
        let token = "/\(command) "
        if prompt.isEmpty {
            prompt = token
        } else {
            prompt += (prompt.hasSuffix(" ") ? "" : " ") + token
        }
    }

    private func run() {
        let text = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, appState.core.runningSessionId == nil else { return }
        prompt = ""
        Task { await appState.core.startSession(prompt: text, cwd: project.path, model: project.model) }
    }

    private func runSkill(_ command: String) {
        guard appState.core.runningSessionId == nil else { return }
        Task { await appState.core.startSession(prompt: "/\(command)", cwd: project.path, model: project.model) }
    }
}

/// A wrapping palette of installed skills. Clicking a chip inserts its
/// `/command` into the composer; the play button runs it immediately.
struct SkillsPaletteView: View {
    let skills: [LibrarySkill]
    let onInsert: (String) -> Void
    let onRun: (String) -> Void

    private let columns = [GridItem(.adaptive(minimum: 180), spacing: 8, alignment: .leading)]

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("Skills · \(skills.count)", systemImage: "wand.and.stars")
                .font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            ScrollView {
                LazyVGrid(columns: columns, alignment: .leading, spacing: 8) {
                    ForEach(skills) { skill in
                        HStack(spacing: 6) {
                            Button { onInsert(skill.command) } label: {
                                VStack(alignment: .leading, spacing: 1) {
                                    Text("/\(skill.command)").font(.caption.weight(.semibold)).lineLimit(1)
                                    if !skill.description.isEmpty {
                                        Text(skill.description).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                                    }
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                            }
                            .buttonStyle(.plain)
                            .help(skill.description.isEmpty ? "Insert /\(skill.command)" : skill.description)
                            Button { onRun(skill.command) } label: {
                                Image(systemName: "play.circle.fill").foregroundStyle(.tint)
                            }
                            .buttonStyle(.plain)
                            .help("Run /\(skill.command) now")
                        }
                        .padding(.horizontal, 11).padding(.vertical, 8)
                        .background(.background.secondary, in: RoundedRectangle(cornerRadius: DS.rSm, style: .continuous))
                        .overlay(
                            RoundedRectangle(cornerRadius: DS.rSm, style: .continuous)
                                .strokeBorder(.primary.opacity(0.06), lineWidth: 1)
                        )
                        .overlay(alignment: .topTrailing) {
                            if skill.scope == "project" {
                                Circle().fill(Color.brandCoral).frame(width: 6, height: 6).padding(5)
                            }
                        }
                    }
                }
            }
            .frame(maxHeight: 140)
        }
        .padding(12)
        .background(.bar)
    }
}

/// The streamed transcript of the currently-running live Claude session.
struct LiveTranscriptView: View {
    @Environment(AppState.self) private var appState

    var body: some View {
        ScrollViewReader { proxy in
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
            }
            .onChange(of: appState.core.liveSession.count) { _, _ in
                if let last = appState.core.liveSession.last {
                    withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
                }
            }
        }
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

// MARK: - Agents (the brain)

private struct ProjectAgentsTab: View {
    @Environment(AppState.self) private var appState
    let project: Project
    @State private var selected: AgentDefinition.ID?
    @State private var command = ""

    private var agent: AgentDefinition? {
        appState.agentStore.agents.first { $0.id == selected }
    }

    var body: some View {
        VStack(spacing: 0) {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(appState.agentStore.agents) { a in
                        Button { selected = a.id } label: {
                            HStack(spacing: 6) {
                                Image(systemName: a.symbol)
                                Text(a.name).font(.callout.weight(.medium))
                            }
                            .padding(.horizontal, 10).padding(.vertical, 6)
                            .background(selected == a.id ? Color.accentColor.opacity(0.18) : Color(.quaternaryLabelColor).opacity(0.4),
                                        in: Capsule())
                            .overlay(Capsule().strokeBorder(selected == a.id ? Color.accentColor : .clear, lineWidth: 1))
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(12)
            }
            .background(.bar)
            Divider()

            if let agent {
                if !agent.role.isEmpty {
                    Text(agent.role).font(.caption).foregroundStyle(.secondary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 12).padding(.top, 8)
                }
                LiveTranscriptView()
                Divider()
                HStack(spacing: 8) {
                    TextField("Command for \(agent.name)…", text: $command, axis: .vertical)
                        .textFieldStyle(.roundedBorder)
                        .lineLimit(1...4)
                        .onSubmit { run(agent) }
                    Button { run(agent) } label: {
                        Label("Run", systemImage: "play.fill")
                    }
                    .buttonStyle(.brand)
                    .disabled(command.trimmingCharacters(in: .whitespaces).isEmpty
                              || !appState.coreConnected
                              || appState.core.runningSessionId != nil)
                }
                .padding(12)
                .background(.bar)
            } else {
                ContentUnavailableView("Pick an agent",
                                       systemImage: "person.crop.rectangle.stack",
                                       description: Text("Choose a saved agent (author them in Agent Studio), then give it a command to run in this project."))
            }
        }
        .onAppear { if selected == nil { selected = appState.agentStore.agents.first?.id } }
    }

    private func run(_ agent: AgentDefinition) {
        let text = command.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, appState.core.runningSessionId == nil else { return }
        command = ""
        Task {
            await appState.core.startSession(prompt: text, cwd: project.path,
                                             model: agent.model, systemPrompt: agent.systemPrompt)
        }
    }
}
