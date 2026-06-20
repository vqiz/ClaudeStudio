import SwiftUI
import ClaudeStudioKit

/// Skills & Plugins manager — install, uninstall, enable/disable, edit, and run
/// skills and Claude Code plugins. Project-aware: project-scoped skills install
/// into the selected project's `.claude/skills`.
struct ExtensionsView: View {
    @Environment(AppState.self) private var appState

    enum Tab: String, CaseIterable, Identifiable {
        case skills, plugins
        var id: String { rawValue }
        var title: String { self == .skills ? "Skills" : "Plugins" }
        var symbol: String { self == .skills ? "wand.and.stars" : "puzzlepiece.extension" }
    }
    @State private var tab: Tab = .skills

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                PageHeader(title: "Skills & Plugins", symbol: "square.stack.3d.up",
                           subtitle: "Install, run, and manage skills and plugins")

                Picker("", selection: $tab) {
                    ForEach(Tab.allCases) { Label($0.title, systemImage: $0.symbol).tag($0) }
                }
                .pickerStyle(.segmented).labelsHidden().frame(maxWidth: 320)

                if appState.coreConnected {
                    switch tab {
                    case .skills: SkillsManager(cwd: appState.selectedProject?.path)
                    case .plugins: PluginsManager()
                    }
                } else {
                    ContentUnavailableView("Core offline", systemImage: "bolt.horizontal.circle",
                                           description: Text("Connect the core to manage skills and plugins."))
                        .padding(.top, 30)
                }
            }
            .padding(20)
        }
    }
}

// MARK: - Skills

private struct SkillsManager: View {
    @Environment(AppState.self) private var appState
    let cwd: String?

    @State private var skills: [LibrarySkill] = []
    @State private var loaded = false
    @State private var editingPath: String?
    @State private var sheet: SkillSheet?

    enum SkillSheet: Identifiable {
        case new, install
        var id: String { self == .new ? "new" : "install" }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                if let cwd {
                    Label(URL(fileURLWithPath: cwd).lastPathComponent, systemImage: "folder")
                        .font(.caption).foregroundStyle(.secondary)
                } else {
                    Label("No project — user skills only", systemImage: "person")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Menu {
                    Button("New skill…", systemImage: "plus") { sheet = .new }
                    Button("Install from URL / path…", systemImage: "arrow.down.circle") { sheet = .install }
                } label: {
                    Label("Add", systemImage: "plus")
                }
                .menuStyle(.borderlessButton).fixedSize()
            }

            if skills.isEmpty && loaded {
                ContentUnavailableView("No skills installed", systemImage: "wand.and.stars",
                                       description: Text("Create one, or install from a git repo or folder."))
            } else {
                ForEach(skills) { skill in
                    VStack(spacing: 0) {
                        SkillRow(skill: skill,
                                 canRun: appState.selectedProject != nil && appState.core.runningSessionId == nil,
                                 onRun: { runSkill(skill) },
                                 onEdit: { editingPath = (editingPath == skill.path) ? nil : skill.path },
                                 onDelete: { Task { await delete(skill) } })
                        if editingPath == skill.path {
                            EditableFileView(path: skill.path, minHeight: 200)
                                .padding(.horizontal, 14).padding(.bottom, 12)
                        }
                    }
                    .background(.background.secondary, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                }
            }
        }
        .task(id: cwd) { await reload() }
        .sheet(item: $sheet) { which in
            switch which {
            case .new:
                ExtensionInputSheet(title: "New Skill", field: "Skill name", showScope: cwd != nil) { name, scope in
                    if let path = await appState.core.createSkill(name: name, scope: scope, cwd: cwd) {
                        await reload(); editingPath = path
                    }
                }
            case .install:
                ExtensionInputSheet(title: "Install Skills", field: "Git URL or local folder",
                                    showScope: cwd != nil) { source, scope in
                    _ = await appState.core.installSkills(source: source, scope: scope, cwd: cwd)
                    await reload()
                }
            }
        }
    }

    private func reload() async { skills = await appState.core.skills(cwd: cwd); loaded = true }

    private func runSkill(_ skill: LibrarySkill) {
        guard let project = appState.selectedProject else { return }
        Task { await appState.core.startSession(prompt: "/\(skill.command)", cwd: project.path, model: project.model) }
    }

    private func delete(_ skill: LibrarySkill) async {
        _ = await appState.core.uninstallSkill(path: skill.path)
        await reload()
    }
}

private struct SkillRow: View {
    let skill: LibrarySkill
    let canRun: Bool
    let onRun: () -> Void
    let onEdit: () -> Void
    let onDelete: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: "wand.and.stars").font(.title3).foregroundStyle(.tint).frame(width: 26)
            VStack(alignment: .leading, spacing: 2) {
                Text("/\(skill.command)").font(.headline)
                if !skill.description.isEmpty {
                    Text(skill.description).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                }
            }
            Spacer()
            tag(skill.scope.capitalized, color: skill.scope == "project" ? .purple : .secondary)
            Button(action: onRun) { Image(systemName: "play.circle.fill") }
                .buttonStyle(.borderless).disabled(!canRun).help("Run /\(skill.command) on the selected project")
            Button(action: onEdit) { Image(systemName: "pencil") }
                .buttonStyle(.borderless).help("Edit SKILL.md")
            Button(role: .destructive, action: onDelete) { Image(systemName: "trash") }
                .buttonStyle(.borderless).help("Uninstall")
        }
        .padding(14)
    }

    private func tag(_ text: String, color: Color) -> some View {
        Text(text).font(.caption2.weight(.semibold))
            .padding(.horizontal, 7).padding(.vertical, 2)
            .background(color.opacity(0.16), in: Capsule()).foregroundStyle(color)
    }
}

// MARK: - Plugins

private struct PluginsManager: View {
    @Environment(AppState.self) private var appState
    @State private var plugins: [Plugin] = []
    @State private var marketplaces: [PluginMarketplace] = []
    @State private var loaded = false
    @State private var busy = false
    @State private var sheet: PluginSheet?
    @State private var error: String?

    enum PluginSheet: Identifiable {
        case install, marketplace
        var id: String { self == .install ? "install" : "marketplace" }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(loaded ? "\(plugins.count) installed" : "Loading…")
                    .font(.caption).foregroundStyle(.secondary)
                if busy { ProgressView().controlSize(.small) }
                Spacer()
                Button { sheet = .marketplace } label: { Label("Add marketplace", systemImage: "building.2") }
                    .controlSize(.small)
                Button { sheet = .install } label: { Label("Install plugin", systemImage: "plus") }
                    .controlSize(.small)
            }

            if let error {
                Text(error).font(.caption).foregroundStyle(.red)
            }

            if plugins.isEmpty && loaded {
                ContentUnavailableView("No plugins installed", systemImage: "puzzlepiece.extension",
                                       description: Text("Install one as plugin@marketplace, e.g. github@claude-plugins-official."))
            } else {
                ForEach(plugins) { plugin in
                    PluginRow(plugin: plugin,
                              onToggle: { enabled in Task { await setEnabled(plugin, enabled) } },
                              onUninstall: { Task { await uninstall(plugin) } })
                }
            }

            if !marketplaces.isEmpty {
                Text("Marketplaces").font(.subheadline.weight(.semibold)).foregroundStyle(.secondary).padding(.top, 6)
                ForEach(marketplaces) { m in
                    HStack(spacing: 8) {
                        Image(systemName: "building.2").foregroundStyle(.secondary)
                        Text(m.name).font(.callout.weight(.medium))
                        Text(m.repo).font(.caption.monospaced()).foregroundStyle(.secondary)
                        Spacer()
                    }
                    .padding(.horizontal, 12).padding(.vertical, 6)
                    .background(.background.secondary, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                }
            }
        }
        .task { await reload() }
        .sheet(item: $sheet) { which in
            switch which {
            case .install:
                ExtensionInputSheet(title: "Install Plugin", field: "plugin@marketplace", showScope: false) { source, _ in
                    busy = true; error = nil
                    let ok = await appState.core.installPlugin(source: source)
                    busy = false
                    if !ok { error = "Install failed for \(source). Check the name and marketplace." }
                    await reload()
                }
            case .marketplace:
                ExtensionInputSheet(title: "Add Marketplace", field: "GitHub repo, URL, or path", showScope: false) { source, _ in
                    busy = true; error = nil
                    let ok = await appState.core.addMarketplace(source: source)
                    busy = false
                    if !ok { error = "Couldn't add marketplace \(source)." }
                    await reload()
                }
            }
        }
    }

    private func reload() async {
        plugins = await appState.core.plugins()
        marketplaces = await appState.core.marketplaces()
        loaded = true
    }

    private func setEnabled(_ plugin: Plugin, _ enabled: Bool) async {
        busy = true; error = nil
        let ok = await appState.core.setPluginEnabled(name: plugin.fullId, enabled: enabled)
        busy = false
        if !ok { error = "Couldn't \(enabled ? "enable" : "disable") \(plugin.name)." }
        await reload()
    }

    private func uninstall(_ plugin: Plugin) async {
        busy = true; error = nil
        _ = await appState.core.uninstallPlugin(name: plugin.fullId, scope: plugin.scope)
        busy = false
        await reload()
    }
}

private struct PluginRow: View {
    let plugin: Plugin
    let onToggle: (Bool) -> Void
    let onUninstall: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: plugin.hasMcp ? "puzzlepiece.extension.fill" : "puzzlepiece.extension")
                .font(.title3).foregroundStyle(.tint).frame(width: 26)
            VStack(alignment: .leading, spacing: 2) {
                Text(plugin.name).font(.headline)
                HStack(spacing: 6) {
                    if !plugin.marketplace.isEmpty {
                        Text(plugin.marketplace).font(.caption.monospaced()).foregroundStyle(.secondary)
                    }
                    Text("v\(plugin.version)").font(.caption2).foregroundStyle(.tertiary)
                    if plugin.hasMcp {
                        Text("MCP").font(.caption2.weight(.semibold))
                            .padding(.horizontal, 5).padding(.vertical, 1)
                            .background(Color.blue.opacity(0.16), in: Capsule()).foregroundStyle(.blue)
                    }
                }
            }
            Spacer()
            Toggle("", isOn: Binding(get: { plugin.enabled }, set: { onToggle($0) }))
                .labelsHidden().toggleStyle(.switch).controlSize(.mini)
                .help(plugin.enabled ? "Enabled" : "Disabled")
            Button(role: .destructive, action: onUninstall) { Image(systemName: "trash") }
                .buttonStyle(.borderless).help("Uninstall")
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }
}

// MARK: - Shared input sheet

/// A small modal: one text field + optional project/user scope picker, with a
/// confirm action that runs an async closure.
private struct ExtensionInputSheet: View {
    @Environment(\.dismiss) private var dismiss
    let title: String
    let field: String
    let showScope: Bool
    let onConfirm: (String, String) async -> Void

    @State private var text = ""
    @State private var scope = "user"
    @State private var working = false

    var body: some View {
        VStack(spacing: 0) {
            HStack { Text(title).font(.headline); Spacer() }.padding().background(.bar)
            Divider()
            Form {
                TextField(field, text: $text)
                if showScope {
                    Picker("Scope", selection: $scope) {
                        Text("Project").tag("project")
                        Text("User (global)").tag("user")
                    }
                    .pickerStyle(.segmented)
                }
            }
            .formStyle(.grouped)
            Divider()
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                Button("Confirm") {
                    working = true
                    Task {
                        await onConfirm(text.trimmingCharacters(in: .whitespacesAndNewlines), scope)
                        working = false
                        dismiss()
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(working || text.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            .padding()
        }
        .frame(minWidth: 440, minHeight: 200)
    }
}
