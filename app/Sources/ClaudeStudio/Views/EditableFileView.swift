import SwiftUI

/// A reusable inline editor for a text file (CLAUDE.md, AGENTS.md, …). Loads the
/// file through the core (`file.read`) — instantly from the prefetch cache when
/// available — tracks unsaved edits against a baseline, and saves with
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
    /// The last-loaded / last-saved on-disk content. Edits are "dirty" exactly
    /// when `content != baseline`, so a programmatic (re)load never counts as an
    /// edit — the cause of false "unsaved" / clobbered loads.
    @State private var baseline = ""
    @State private var loaded = false
    @State private var saving = false
    @State private var exists = true
    /// The path the current `content` buffer belongs to, so we can flush unsaved
    /// edits to the *previous* file before reloading a new one.
    @State private var bufferPath: String?

    private var dirty: Bool { editable && content != baseline }

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
                    Button { content = template } label: {
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
                .overlay {
                    if !loaded { ProgressView().controlSize(.small) }
                }

            if !appState.coreConnected {
                Text("Connect the core to view and edit this file.")
                    .font(.caption2).foregroundStyle(.secondary)
            }
        }
        .task(id: path) { await load() }
        // Files couldn't load while the core was offline — load them the moment
        // it connects, so CLAUDE.md / AGENTS.md never stay blank.
        .onChange(of: appState.coreConnected) { _, connected in
            if connected, !loaded || (!exists && content.isEmpty) {
                Task { await load() }
            }
        }
        .onDisappear(perform: flushIfDirty)
    }

    private func load() async {
        // Flush unsaved edits to the *previous* file before switching buffers.
        if editable, content != baseline, let previous = bufferPath, previous != path,
           appState.coreConnected {
            _ = await appState.core.writeFile(previous, content: content)
        }
        bufferPath = path

        // Instant: show cached content first so a tab/project switch never
        // flickers through an empty editor.
        if let cached = appState.core.cachedFile(path) {
            content = cached.content
            baseline = cached.content
            exists = cached.exists
            loaded = true
        } else {
            loaded = false
        }

        // Authoritative read. Only adopt it when the user hasn't started typing
        // (content still equals the baseline), and never blank a buffer we
        // already populated from cache on a transient read failure.
        if let result = await appState.core.readFile(path) {
            if content == baseline {
                content = result.content
                baseline = result.content
                exists = result.exists
            }
        } else if !loaded {
            content = ""
            baseline = ""
            exists = false
        }
        loaded = true
    }

    /// Persist unsaved edits when the editor is dismissed (e.g. navigating to a
    /// different section). Best-effort; the captured snapshot outlives the view.
    private func flushIfDirty() {
        guard editable, content != baseline, appState.coreConnected else { return }
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
                baseline = text
                exists = true
            }
        }
    }
}
