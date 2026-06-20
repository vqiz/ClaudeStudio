import SwiftUI
import ClaudeStudioKit

/// The Definitions Library — browse, create, edit, and delete reusable
/// definition documents (markdown + frontmatter) the core can inject as context.
/// Shipped definitions are read-only (duplicate to customise); your own live in
/// the writable user library.
struct DefinitionsLibraryView: View {
    @Environment(AppState.self) private var appState

    private var items: [LibraryBrowserItem] {
        appState.core.definitions.map {
            LibraryBrowserItem(
                path: $0.path, name: $0.name, category: $0.category,
                writable: $0.writable,
                detail: $0.scope.isEmpty ? "" : $0.scope
            )
        }
    }

    var body: some View {
        LibraryBrowser(
            title: "Definitions Library",
            symbol: "books.vertical",
            items: items,
            newButtonTitle: "Definition",
            fileKind: "Markdown",
            create: { await appState.core.createDefinition(name: $0) },
            delete: { await appState.core.deleteDefinition(path: $0) }
        )
    }
}
