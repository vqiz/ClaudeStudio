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
            .frame(minWidth: 200, idealWidth: 250, maxWidth: 360, maxHeight: .infinity)

            if let project = appState.selectedProject {
                ProjectWorkspaceView(project: project)
                    .id(project.id)
                    .frame(minWidth: 420, maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ContentUnavailableView("Select a project", systemImage: "sidebar.squares.left")
                    .frame(minWidth: 360, maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
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
