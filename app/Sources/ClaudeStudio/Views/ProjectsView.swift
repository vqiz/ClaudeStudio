import SwiftUI
import AppKit

/// The Projects hub — your real folders. Add a folder, set its model/effort, edit
/// its CLAUDE.md / AGENTS.md, see live git status, and run sessions there.
struct ProjectsView: View {
    @Environment(AppState.self) private var appState

    var body: some View {
        Group {
            if appState.projects.isEmpty {
                emptyState
            } else {
                content
            }
        }
        .toolbar {
            ToolbarItem {
                Button(action: addProject) {
                    Label("Add Project", systemImage: "plus")
                }
                .help("Add a folder as a project")
            }
        }
        .navigationTitle("Projects")
    }

    private var emptyState: some View {
        ContentUnavailableView {
            Label("No projects yet", systemImage: "folder.badge.plus")
        } description: {
            Text("Add a folder to run Claude sessions there, edit its CLAUDE.md, and pick a per-project model.")
        } actions: {
            Button("Add Project…", action: addProject)
                .buttonStyle(.borderedProminent)
        }
    }

    @ViewBuilder
    private var content: some View {
        @Bindable var appState = appState
        HSplitView {
            List(selection: $appState.selectedProjectID) {
                ForEach(appState.projects) { project in
                    ProjectRow(project: project).tag(project.id)
                }
            }
            .frame(minWidth: 200, idealWidth: 250, maxWidth: 360)

            if let project = appState.selectedProject {
                ProjectDetail(project: project)
                    .frame(minWidth: 360)
            } else {
                ContentUnavailableView("Select a project", systemImage: "sidebar.squares.left")
                    .frame(minWidth: 360)
            }
        }
    }

    private func addProject() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "Add"
        panel.message = "Choose a project folder"
        if panel.runModal() == .OK, let url = panel.url {
            let project = appState.projectStore.add(path: url.path)
            appState.selectedProjectID = project.id
        }
    }
}

private struct ProjectRow: View {
    let project: Project

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "folder.fill").foregroundStyle(.tint)
            VStack(alignment: .leading, spacing: 1) {
                Text(project.name).font(.headline)
                Text(project.displayPath)
                    .font(.caption).foregroundStyle(.secondary)
                    .lineLimit(1).truncationMode(.middle)
            }
            Spacer()
            Text(project.model)
                .font(.caption2.monospaced())
                .foregroundStyle(.secondary)
        }
        .padding(.vertical, 3)
    }
}

private struct ProjectDetail: View {
    @Environment(AppState.self) private var appState
    let project: Project

    @State private var branch = ""
    @State private var changes: Int?
    @State private var worktrees: [ProjectWorktree] = []
    @State private var prompt = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                header
                cards
                runComposer

                GroupBox {
                    EditableFileView(path: project.claudeMdPath)
                } label: { Label("CLAUDE.md", systemImage: "doc.text") }

                GroupBox {
                    EditableFileView(path: project.agentsMdPath)
                } label: { Label("AGENTS.md", systemImage: "doc.text") }

                if !worktrees.isEmpty {
                    GroupBox {
                        VStack(alignment: .leading, spacing: 6) {
                            ForEach(worktrees) { worktree in
                                HStack(spacing: 8) {
                                    Image(systemName: "arrow.triangle.branch")
                                        .font(.caption).foregroundStyle(.tint)
                                    Text(worktree.branch).font(.callout.weight(.medium))
                                    Text(worktree.path)
                                        .font(.caption).foregroundStyle(.secondary)
                                        .lineLimit(1).truncationMode(.middle)
                                    Spacer()
                                }
                            }
                        }
                        .padding(6)
                    } label: {
                        Label("Worktrees (\(worktrees.count))", systemImage: "square.split.2x1")
                    }
                }
            }
            .padding(20)
        }
        .task(id: project.id) { await loadGit() }
    }

    private var header: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 3) {
                Text(project.name).font(.title2.bold())
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
    }

    private var cards: some View {
        HStack(spacing: 12) {
            infoCard("Branch", branch.isEmpty ? "—" : branch, "arrow.triangle.branch")
            infoCard("Changes", changes.map(String.init) ?? "—", "pencil.line")
            modelCard
        }
    }

    private var modelCard: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("Model · effort", systemImage: "cpu").font(.caption).foregroundStyle(.secondary)
            Picker("", selection: Binding(
                get: { project.model },
                set: { appState.projectStore.setModel(project.id, model: $0) }
            )) {
                ForEach(ModelTierOption.allCases) { Text($0.label).tag($0.rawValue) }
            }
            .labelsHidden()
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 10))
    }

    private func infoCard(_ title: String, _ value: String, _ symbol: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Label(title, systemImage: symbol).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.title3.weight(.semibold)).lineLimit(1)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 10))
    }

    private var runComposer: some View {
        HStack(spacing: 8) {
            TextField("Run a Claude session in this project…", text: $prompt)
                .textFieldStyle(.roundedBorder)
                .onSubmit(run)
            Button(action: run) {
                Label("Run", systemImage: "play.fill")
            }
            .buttonStyle(.borderedProminent)
            .disabled(prompt.trimmingCharacters(in: .whitespaces).isEmpty
                      || !appState.coreConnected
                      || appState.core.runningSessionId != nil)
        }
    }

    private func run() {
        let text = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        prompt = ""
        Task { await appState.core.startSession(prompt: text, cwd: project.path, model: project.model) }
    }

    private func loadGit() async {
        branch = ""
        changes = nil
        worktrees = []
        if let info = await appState.core.gitInfo(cwd: project.path) {
            branch = info.branch
            changes = info.changes
        }
        worktrees = await appState.core.worktrees(cwd: project.path)
    }
}
