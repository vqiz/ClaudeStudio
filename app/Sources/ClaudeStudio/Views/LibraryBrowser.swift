import SwiftUI

/// One row in a library browser, mapped from a `LibraryTask` / `LibraryDefinition`.
struct LibraryBrowserItem: Identifiable, Equatable {
    var id: String { path }
    let path: String
    let name: String
    let category: String
    /// True for the user's own (editable) items; false for shipped ones.
    let writable: Bool
    /// Secondary line — joined tags (tasks) or scope (definitions).
    let detail: String
}

/// A reusable, editable master–detail browser for a filesystem-backed library
/// (tasks or definitions). The left column groups items by category and offers a
/// "New" action; the right column edits the selected file in place (read-only for
/// shipped items, which can instead be duplicated into the user's library).
struct LibraryBrowser: View {
    @Environment(AppState.self) private var appState

    let title: String
    let symbol: String
    let items: [LibraryBrowserItem]
    let newButtonTitle: String
    let fileKind: String          // "JSON" / "Markdown" — a small badge
    /// Create a new item with the given name; returns the new file path.
    let create: (String) async -> String?
    /// Delete the item at `path`.
    let delete: (String) async -> Bool
    /// Optional Run action (tasks have one; definitions don't).
    var run: ((LibraryBrowserItem) -> Void)?

    @State private var selectedPath: String?
    @State private var promptingName = false
    @State private var newName = ""
    @State private var busy = false

    var body: some View {
        Group {
            if appState.coreConnected {
                HSplitView {
                    sidebar.frame(minWidth: 240, idealWidth: 280, maxWidth: 380, maxHeight: .infinity)
                    detail.frame(minWidth: 360, maxWidth: .infinity, maxHeight: .infinity)
                }
            } else {
                ContentUnavailableView(
                    "Core offline",
                    systemImage: "bolt.horizontal.circle",
                    description: Text("Connect the core to browse and edit the \(title.lowercased()).")
                )
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .navigationTitle(title)
        .alert("New \(newButtonTitle)", isPresented: $promptingName) {
            TextField("Name", text: $newName)
            Button("Create", action: confirmCreate)
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("It's added to your editable library.")
        }
    }

    // MARK: Sidebar

    private var grouped: [(String, [LibraryBrowserItem])] {
        Dictionary(grouping: items) { $0.category.isEmpty ? "Other" : $0.category }
            .sorted { $0.key < $1.key }
    }

    private var sidebar: some View {
        VStack(spacing: 0) {
            HStack {
                Label(title, systemImage: symbol).font(.headline)
                Spacer()
                Text("\(items.count)").font(.caption).foregroundStyle(.secondary)
                Button {
                    newName = ""
                    promptingName = true
                } label: {
                    Image(systemName: "plus")
                }
                .buttonStyle(.borderless)
                .help("New \(newButtonTitle.lowercased())")
            }
            .padding(12)
            .background(.bar)

            List(selection: $selectedPath) {
                ForEach(grouped, id: \.0) { category, rows in
                    Section(category) {
                        ForEach(rows) { item in
                            row(item).tag(item.path)
                        }
                    }
                }
            }
        }
    }

    private func row(_ item: LibraryBrowserItem) -> some View {
        HStack(spacing: 8) {
            Image(systemName: item.writable ? "pencil.circle.fill" : "shippingbox")
                .foregroundStyle(item.writable ? Color.accentColor : .secondary)
            VStack(alignment: .leading, spacing: 1) {
                Text(item.name).font(.callout.weight(.medium)).lineLimit(1)
                if !item.detail.isEmpty {
                    Text(item.detail).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
            }
        }
        .padding(.vertical, 2)
    }

    // MARK: Detail

    @ViewBuilder
    private var detail: some View {
        if let path = selectedPath, let item = items.first(where: { $0.path == path }) {
            LibraryItemDetail(item: item, fileKind: fileKind, run: run,
                              onDelete: { await remove(item) },
                              onDuplicate: { await duplicate(item) })
                .id(item.path)
        } else if items.isEmpty {
            ContentUnavailableView {
                Label("Your \(title.lowercased()) is empty", systemImage: symbol)
            } description: {
                Text("Create one with the + button, or load the shipped starter set from Settings → Templates → Load default templates.")
            }
        } else {
            ContentUnavailableView("Select an item", systemImage: symbol)
        }
    }

    // MARK: Actions

    private func confirmCreate() {
        let name = newName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty else { return }
        Task {
            if let path = await create(name) { selectedPath = path }
        }
    }

    private func remove(_ item: LibraryBrowserItem) async {
        _ = await delete(item.path)
        if selectedPath == item.path { selectedPath = nil }
    }

    /// Copy a shipped item into the user's editable library, then select it.
    private func duplicate(_ item: LibraryBrowserItem) async {
        guard let newPath = await create("\(item.name) copy") else { return }
        if let src = await appState.core.readFile(item.path) {
            _ = await appState.core.writeFile(newPath, content: src.content)
        }
        selectedPath = newPath
    }
}

/// The right-hand editor for one library item.
private struct LibraryItemDetail: View {
    @Environment(AppState.self) private var appState
    let item: LibraryBrowserItem
    let fileKind: String
    var run: ((LibraryBrowserItem) -> Void)?
    let onDelete: () async -> Void
    let onDuplicate: () async -> Void

    @State private var working = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    EditableFileView(path: item.path, minHeight: 320, editable: item.writable)
                    if !item.writable {
                        Label("This is a shipped item. Duplicate it to your library to edit it.",
                              systemImage: "info.circle")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Text(item.path)
                        .font(.caption2.monospaced()).foregroundStyle(.tertiary)
                        .textSelection(.enabled)
                }
                .padding(16)
            }
        }
    }

    private var header: some View {
        HStack(alignment: .top, spacing: 10) {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 8) {
                    Text(item.name).font(.title3.bold())
                    badge(item.writable ? "Yours" : "Shipped",
                          color: item.writable ? .accentColor : .secondary)
                    badge(fileKind, color: .secondary)
                }
                if !item.category.isEmpty {
                    Text(item.category).font(.caption).foregroundStyle(.secondary)
                }
            }
            Spacer()
            if let run {
                Button {
                    run(item)
                } label: {
                    Label("Run", systemImage: "play.fill")
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                .disabled(appState.selectedProject == nil || !appState.coreConnected
                          || appState.core.runningSessionId != nil)
                .help(appState.selectedProject == nil ? "Select a project first" : "Run on the selected project")
            }
            if item.writable {
                Button(role: .destructive) {
                    working = true
                    Task { await onDelete(); working = false }
                } label: {
                    Label("Delete", systemImage: "trash")
                }
                .controlSize(.small)
                .disabled(working)
            } else {
                Button {
                    working = true
                    Task { await onDuplicate(); working = false }
                } label: {
                    Label("Duplicate", systemImage: "doc.on.doc")
                }
                .controlSize(.small)
                .disabled(working)
            }
        }
        .padding(14)
        .background(.bar)
    }

    private func badge(_ text: String, color: Color) -> some View {
        Text(text)
            .font(.caption2.weight(.semibold))
            .padding(.horizontal, 7).padding(.vertical, 2)
            .background(color.opacity(0.16), in: Capsule())
            .foregroundStyle(color)
    }
}
