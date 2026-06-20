import SwiftUI

/// A reusable inline editor for a text file (CLAUDE.md, AGENTS.md, …). Loads the
/// file through the core (`file.read`), tracks unsaved edits, and saves with
/// `file.write`. Read-only / disabled when the core is offline.
struct EditableFileView: View {
    @Environment(AppState.self) private var appState

    let path: String
    var minHeight: CGFloat = 120
    /// When false, the file is shown read-only (no Save, no edits persisted) —
    /// used for shipped library items the user can't modify in place.
    var editable: Bool = true
    /// Optional starter content offered (via a "Start from template" button)
    /// when the file doesn't exist yet.
    var template: String? = nil

    @State private var content = ""
    @State private var loaded = false
    @State private var dirty = false
    @State private var saving = false
    @State private var exists = true
    /// The path the current `content` buffer belongs to. Lets us flush unsaved
    /// edits to the *previous* file before `.task(id: path)` reloads a new one,
    /// so switching the selected project (same view instance, new path) can't
    /// silently discard typed changes.
    @State private var bufferPath: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                if loaded && !exists {
                    Text("new file").font(.caption2)
                        .padding(.horizontal, 5).padding(.vertical, 1)
                        .background(.quaternary, in: Capsule())
                        .foregroundStyle(.secondary)
                }
                if !editable {
                    Label("read-only", systemImage: "lock")
                        .font(.caption2).foregroundStyle(.secondary)
                }
                if dirty {
                    Label("unsaved", systemImage: "pencil")
                        .font(.caption2).foregroundStyle(.orange)
                }
                Spacer()
                if editable, loaded, !exists, let template, content.isEmpty {
                    Button {
                        content = template
                        dirty = true
                    } label: {
                        Label("Start from template", systemImage: "doc.badge.plus")
                    }
                    .controlSize(.small)
                }
                if editable {
                    Button(action: save) {
                        Label("Save", systemImage: "square.and.arrow.down")
                    }
                    .controlSize(.small)
                    .disabled(!dirty || saving || !appState.coreConnected)
                }
            }

            TextEditor(text: $content)
                .font(.system(.callout, design: .monospaced))
                .frame(minHeight: minHeight)
                .scrollContentBackground(.hidden)
                .padding(6)
                .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 8))
                .disabled(!appState.coreConnected || !loaded || !editable)
                .onChange(of: content) { _, _ in if loaded && editable { dirty = true } }
                .overlay {
                    if !loaded {
                        ProgressView().controlSize(.small)
                    }
                }

            if !appState.coreConnected {
                Text("Connect the core to view and edit this file.")
                    .font(.caption2).foregroundStyle(.secondary)
            }
        }
        .task(id: path) { await load() }
        .onDisappear(perform: flushIfDirty)
    }

    private func load() async {
        // The path changed underneath us (e.g. the user switched the selected
        // project): flush unsaved edits to the *previous* file before replacing
        // the buffer, so typed changes are never silently lost.
        if editable, dirty, let previous = bufferPath, previous != path, appState.coreConnected {
            _ = await appState.core.writeFile(previous, content: content)
        }
        loaded = false
        dirty = false
        bufferPath = path
        if let result = await appState.core.readFile(path) {
            content = result.content
            exists = result.exists
        } else {
            content = ""
            exists = false
        }
        loaded = true
    }

    /// Persist unsaved edits when the editor is dismissed (e.g. navigating to a
    /// different section). Best-effort; the captured snapshot outlives the view.
    private func flushIfDirty() {
        guard editable, dirty, appState.coreConnected else { return }
        let text = content
        let target = bufferPath ?? path
        Task { await appState.core.writeFile(target, content: text) }
    }

    private func save() {
        saving = true
        let text = content
        Task {
            let ok = await appState.core.writeFile(path, content: text)
            saving = false
            if ok {
                dirty = false
                exists = true
            }
        }
    }
}
