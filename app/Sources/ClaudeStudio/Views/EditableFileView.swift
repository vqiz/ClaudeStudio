import SwiftUI

/// A reusable inline editor for a text file (CLAUDE.md, AGENTS.md, …). Loads the
/// file through the core (`file.read`), tracks unsaved edits, and saves with
/// `file.write`. Read-only / disabled when the core is offline.
struct EditableFileView: View {
    @Environment(AppState.self) private var appState

    let path: String
    var minHeight: CGFloat = 120

    @State private var content = ""
    @State private var loaded = false
    @State private var dirty = false
    @State private var saving = false
    @State private var exists = true

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                if loaded && !exists {
                    Text("new file").font(.caption2)
                        .padding(.horizontal, 5).padding(.vertical, 1)
                        .background(.quaternary, in: Capsule())
                        .foregroundStyle(.secondary)
                }
                if dirty {
                    Label("unsaved", systemImage: "pencil")
                        .font(.caption2).foregroundStyle(.orange)
                }
                Spacer()
                Button(action: save) {
                    Label("Save", systemImage: "square.and.arrow.down")
                }
                .controlSize(.small)
                .disabled(!dirty || saving || !appState.coreConnected)
            }

            TextEditor(text: $content)
                .font(.system(.callout, design: .monospaced))
                .frame(minHeight: minHeight)
                .scrollContentBackground(.hidden)
                .padding(6)
                .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 8))
                .disabled(!appState.coreConnected || !loaded)
                .onChange(of: content) { _, _ in if loaded { dirty = true } }
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
    }

    private func load() async {
        loaded = false
        dirty = false
        if let result = await appState.core.readFile(path) {
            content = result.content
            exists = result.exists
        } else {
            content = ""
            exists = false
        }
        loaded = true
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
