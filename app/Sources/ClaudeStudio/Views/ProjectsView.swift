import SwiftUI

/// The Projects detail: a master list of projects on the left, a project
/// inspector in the middle, and the live `SessionPanelView` docked on the right.
struct ProjectsView: View {
    @Environment(AppState.self) private var appState

    var body: some View {
        @Bindable var appState = appState

        HSplitView {
            projectList
                .frame(minWidth: 240, idealWidth: 300, maxWidth: 380)

            if let project = appState.selectedProject {
                ProjectInspector(project: project)
                    .frame(minWidth: 320)
            } else {
                ContentUnavailableView("Select a project", systemImage: "folder")
            }

            SessionPanelView()
                .frame(minWidth: 320, idealWidth: 380)
        }
    }

    private var projectList: some View {
        @Bindable var appState = appState
        return List(selection: $appState.selectedProjectID) {
            ForEach(appState.projects) { project in
                ProjectRow(project: project).tag(project.id)
            }
        }
        .safeAreaInset(edge: .top) {
            HStack {
                PageHeader(title: "Projects", symbol: "folder.badge.gearshape")
                Button {} label: { Image(systemName: "plus") }
                    .buttonStyle(.borderless)
                    .help("Add project")
            }
            .padding(12)
            .background(.bar)
        }
    }
}

private struct ProjectRow: View {
    let project: Project

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(project.name).font(.headline)
                Spacer()
                TrustModeBadge(mode: project.trustMode)
            }
            HStack(spacing: 8) {
                Label(project.branch, systemImage: "arrow.triangle.branch")
                if project.activeSessionCount > 0 {
                    Label("\(project.activeSessionCount) active", systemImage: "circle.fill")
                        .foregroundStyle(.green)
                }
                Spacer()
                Text(Format.ago(project.lastActivity))
            }
            .font(.caption)
            .foregroundStyle(.secondary)
        }
        .padding(.vertical, 4)
    }
}

/// Inspector showing a project's worktrees, attached skills, and MCP servers.
private struct ProjectInspector: View {
    let project: Project

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                PageHeader(title: project.name, symbol: "folder.badge.gearshape", subtitle: project.path)

                GroupBox("Trust & Activity") {
                    HStack(spacing: 24) {
                        stat("Trust", project.trustMode.label, project.trustMode.symbol, project.trustMode.tint)
                        stat("Branch", project.branch, "arrow.triangle.branch", .secondary)
                        stat("Sessions", "\(project.activeSessionCount)", "bolt.fill", .green)
                        Spacer()
                    }
                    .padding(6)
                }

                GroupBox("Worktrees") {
                    if project.worktrees.isEmpty {
                        Text("No worktrees. Create one to run an isolated session.")
                            .font(.callout).foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading).padding(6)
                    } else {
                        VStack(spacing: 0) {
                            ForEach(project.worktrees) { worktree in
                                WorktreeRow(worktree: worktree)
                                if worktree.id != project.worktrees.last?.id { Divider() }
                            }
                        }
                        .padding(6)
                    }
                }

                GroupBox("Skills") { ChipFlow(items: project.skills, symbol: "wand.and.stars") }
                GroupBox("MCP Servers") { ChipFlow(items: project.mcpServers, symbol: "server.rack") }
            }
            .padding(20)
        }
    }

    private func stat(_ title: String, _ value: String, _ symbol: String, _ tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Label(title, systemImage: symbol).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.headline).foregroundStyle(tint)
        }
    }
}

private struct WorktreeRow: View {
    let worktree: Worktree

    var body: some View {
        HStack {
            Image(systemName: "arrow.triangle.branch").foregroundStyle(.tint)
            VStack(alignment: .leading, spacing: 1) {
                Text(worktree.branch).font(.callout.weight(.medium))
                Text(worktree.path).font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            if worktree.aheadBy > 0 {
                Label("\(worktree.aheadBy)", systemImage: "arrow.up").font(.caption).foregroundStyle(.blue)
            }
            if worktree.isDirty {
                Text("dirty").font(.caption2.weight(.semibold))
                    .padding(.horizontal, 6).padding(.vertical, 2)
                    .background(Color.orange.opacity(0.18), in: Capsule())
                    .foregroundStyle(.orange)
            }
        }
        .padding(.vertical, 5)
    }
}

/// A simple wrapping flow of labeled chips.
struct ChipFlow: View {
    let items: [String]
    var symbol: String

    var body: some View {
        if items.isEmpty {
            Text("None configured").font(.callout).foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .leading).padding(6)
        } else {
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 110), spacing: 8, alignment: .leading)], alignment: .leading, spacing: 8) {
                ForEach(items, id: \.self) { item in
                    Label(item, systemImage: symbol)
                        .font(.caption.weight(.medium))
                        .lineLimit(1)
                        .padding(.horizontal, 8).padding(.vertical, 4)
                        .background(.quaternary, in: Capsule())
                }
            }
            .padding(6)
        }
    }
}
