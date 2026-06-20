import SwiftUI
import ClaudeStudioKit

/// The Task Library — browse, create, edit, run, and delete one-click task
/// workflows. Shipped tasks are read-only (duplicate to customise); your own
/// tasks live in the writable user library and edit in place.
struct TaskLibraryView: View {
    @Environment(AppState.self) private var appState

    private var items: [LibraryBrowserItem] {
        appState.core.tasks.map {
            LibraryBrowserItem(
                path: $0.path, name: $0.name, category: $0.category,
                writable: $0.writable,
                detail: $0.tags.prefix(3).joined(separator: " · ")
            )
        }
    }

    var body: some View {
        LibraryBrowser(
            title: "Task Library",
            symbol: "square.grid.2x2",
            items: items,
            newButtonTitle: "Task",
            fileKind: "JSON",
            create: { await appState.core.createTask(name: $0) },
            delete: { await appState.core.deleteTask(path: $0) },
            run: runTask
        )
    }

    private func runTask(_ item: LibraryBrowserItem) {
        guard let project = appState.selectedProject else { return }
        let prompt = "Run the \"\(item.name)\" task on this project."
        Task { await appState.core.startSession(prompt: prompt, cwd: project.path, model: project.model) }
    }
}
