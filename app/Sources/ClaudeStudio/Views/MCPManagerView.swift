import SwiftUI
import ClaudeStudioKit

/// An editable list of MCP servers for a context: the project's `.mcp.json`
/// (when `cwd` is set) plus the user's global `~/.claude.json`. Add, edit, and
/// remove servers; changes are written by the core and the list refreshes.
struct MCPManagerView: View {
    @Environment(AppState.self) private var appState
    /// The project directory for project-scoped servers, or nil (user-only).
    let cwd: String?

    @State private var servers: [McpServer] = []
    @State private var loaded = false
    @State private var editor: EditorState?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text(loaded ? "\(servers.count) server\(servers.count == 1 ? "" : "s")" : "Loading…")
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button {
                    editor = EditorState(existing: nil, defaultScope: cwd == nil ? "user" : "project")
                } label: {
                    Label("Add server", systemImage: "plus")
                }
                .controlSize(.small)
                .disabled(!appState.coreConnected)
            }

            if !appState.coreConnected {
                ContentUnavailableView("Core offline", systemImage: "bolt.horizontal.circle",
                                       description: Text("Connect the core to manage MCP servers."))
            } else if servers.isEmpty && loaded {
                ContentUnavailableView("No MCP servers", systemImage: "puzzlepiece.extension",
                                       description: Text("Add a server — it's written to \(cwd == nil ? "~/.claude.json" : ".mcp.json / ~/.claude.json")."))
            } else {
                ForEach(servers) { server in
                    MCPServerRow(server: server,
                                 onEdit: { editor = EditorState(existing: server, defaultScope: server.scope) },
                                 onDelete: { Task { await remove(server) } })
                }
            }
        }
        .task(id: cwd) { await reload() }
        .sheet(item: $editor) { state in
            MCPServerForm(cwd: cwd, state: state) { await reload() }
                .frame(minWidth: 460, minHeight: 420)
        }
    }

    private func reload() async {
        servers = await appState.core.mcpServers(cwd: cwd)
        loaded = true
    }

    private func remove(_ server: McpServer) async {
        _ = await appState.core.removeMcpServer(name: server.name, scope: server.scope,
                                                cwd: server.scope == "project" ? cwd : nil)
        await reload()
    }
}

/// Identifies an open add/edit sheet.
struct EditorState: Identifiable {
    let id = UUID()
    let existing: McpServer?
    let defaultScope: String
}

private struct MCPServerRow: View {
    let server: McpServer
    let onEdit: () -> Void
    let onDelete: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: transportSymbol).font(.title3).foregroundStyle(.tint).frame(width: 28)
            VStack(alignment: .leading, spacing: 3) {
                Text(server.name).font(.headline)
                Text(detailLine).font(.caption.monospaced()).foregroundStyle(.secondary)
                    .lineLimit(1).truncationMode(.middle)
            }
            Spacer()
            tag(server.transport.uppercased(), color: .blue)
            tag(server.scope.capitalized, color: server.scope == "project" ? .purple : .secondary)
            Button(action: onEdit) { Image(systemName: "pencil") }
                .buttonStyle(.borderless).help("Edit")
            Button(role: .destructive, action: onDelete) { Image(systemName: "trash") }
                .buttonStyle(.borderless).help("Remove")
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .dsCard(padding: 14, radius: DS.rMd, elevated: false)
    }

    private var detailLine: String {
        if server.transport == "stdio" {
            return ([server.target] + server.args).joined(separator: " ")
        }
        return server.url.isEmpty ? server.target : server.url
    }

    private var transportSymbol: String {
        switch server.transport {
        case "stdio": return "terminal"
        case "sse": return "dot.radiowaves.up.forward"
        case "http": return "network"
        default: return "puzzlepiece.extension"
        }
    }

    private func tag(_ text: String, color: Color) -> some View {
        Text(text).font(.caption2.weight(.semibold))
            .padding(.horizontal, 7).padding(.vertical, 2)
            .background(color.opacity(0.16), in: Capsule()).foregroundStyle(color)
    }
}

/// Add/edit form for a single MCP server, presented as a sheet.
private struct MCPServerForm: View {
    @Environment(AppState.self) private var appState
    @Environment(\.dismiss) private var dismiss
    let cwd: String?
    let state: EditorState
    let onSaved: () async -> Void

    @State private var name = ""
    @State private var scope = "user"
    @State private var transport = "stdio"
    @State private var command = ""
    @State private var argsText = ""
    @State private var envText = ""
    @State private var url = ""
    @State private var saving = false
    @State private var error: String?

    private var isEdit: Bool { state.existing != nil }

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text(isEdit ? "Edit MCP Server" : "Add MCP Server").font(.headline)
                Spacer()
            }
            .padding()
            .background(.bar)
            Divider()

            Form {
                Section("Identity") {
                    TextField("Name", text: $name).disabled(isEdit)
                    Picker("Scope", selection: $scope) {
                        if cwd != nil { Text("Project (.mcp.json)").tag("project") }
                        Text("User (~/.claude.json)").tag("user")
                    }
                    .disabled(isEdit)
                }
                Section("Transport") {
                    Picker("Type", selection: $transport) {
                        Text("stdio (local process)").tag("stdio")
                        Text("SSE (remote)").tag("sse")
                        Text("HTTP (remote)").tag("http")
                    }
                    .pickerStyle(.segmented)

                    if transport == "stdio" {
                        TextField("Command (e.g. npx)", text: $command)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Arguments (one per line)").font(.caption).foregroundStyle(.secondary)
                            TextEditor(text: $argsText)
                                .font(.system(.callout, design: .monospaced)).frame(minHeight: 70)
                        }
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Environment (KEY=value, one per line)").font(.caption).foregroundStyle(.secondary)
                            TextEditor(text: $envText)
                                .font(.system(.callout, design: .monospaced)).frame(minHeight: 50)
                        }
                    } else {
                        TextField("URL", text: $url)
                    }
                }
                if let error {
                    Text(error).font(.caption).foregroundStyle(.red)
                }
            }
            .formStyle(.grouped)

            Divider()
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                Button(isEdit ? "Save" : "Add") { Task { await save() } }
                    .buttonStyle(.borderedProminent)
                    .disabled(saving || !isValid)
            }
            .padding()
        }
        .onAppear(perform: populate)
    }

    private var isValid: Bool {
        guard !name.trimmingCharacters(in: .whitespaces).isEmpty else { return false }
        return transport == "stdio"
            ? !command.trimmingCharacters(in: .whitespaces).isEmpty
            : !url.trimmingCharacters(in: .whitespaces).isEmpty
    }

    private func populate() {
        guard let s = state.existing else {
            scope = state.defaultScope
            return
        }
        name = s.name
        scope = s.scope.isEmpty ? state.defaultScope : s.scope
        transport = s.transport.isEmpty ? "stdio" : s.transport
        command = s.target
        argsText = s.args.joined(separator: "\n")
        envText = s.env.map { "\($0.key)=\($0.value)" }.sorted().joined(separator: "\n")
        url = s.url
    }

    private func save() async {
        saving = true
        defer { saving = false }
        let args = argsText.split(separator: "\n").map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
        var env: [String: String] = [:]
        for line in envText.split(separator: "\n") {
            let parts = line.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
            if parts.count == 2 {
                let key = parts[0].trimmingCharacters(in: .whitespaces)
                if !key.isEmpty { env[key] = parts[1].trimmingCharacters(in: .whitespaces) }
            }
        }
        let ok = await appState.core.upsertMcpServer(
            name: name.trimmingCharacters(in: .whitespaces),
            transport: transport,
            scope: scope,
            cwd: scope == "project" ? cwd : nil,
            command: transport == "stdio" ? command.trimmingCharacters(in: .whitespaces) : nil,
            args: args, env: env,
            url: transport == "stdio" ? nil : url.trimmingCharacters(in: .whitespaces))
        if ok {
            await onSaved()
            dismiss()
        } else {
            error = "Couldn't save. Check the fields and that the core is connected."
        }
    }
}
