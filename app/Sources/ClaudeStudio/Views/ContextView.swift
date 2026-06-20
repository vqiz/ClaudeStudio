import SwiftUI
import ClaudeStudioKit

/// The Context view — what the core assembles before each prompt: the live
/// budget, the editable global and project CLAUDE.md, and the Definition Library.
struct ContextView: View {
    @Environment(AppState.self) private var appState

    private var globalClaudeMd: String {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return (home as NSString).appendingPathComponent(".claude/CLAUDE.md")
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                PageHeader(title: "Context", symbol: "doc.text.magnifyingglass",
                           subtitle: "Everything the core injects before a prompt")

                if let budget = appState.core.budget {
                    GroupBox {
                        contextBudget(budget)
                    } label: {
                        Label("Context Budget — live from core", systemImage: "gauge.with.dots.needle.67percent")
                    }
                }

                GroupBox {
                    EditableFileView(path: globalClaudeMd)
                } label: {
                    Label("Global CLAUDE.md  ·  ~/.claude/CLAUDE.md", systemImage: "doc.text")
                }

                if let project = appState.selectedProject {
                    GroupBox {
                        EditableFileView(path: project.claudeMdPath)
                    } label: {
                        Label("\(project.name) · CLAUDE.md", systemImage: "doc.text")
                    }
                } else {
                    Text("Select a project to edit its CLAUDE.md, or add one under Projects.")
                        .font(.caption).foregroundStyle(.secondary)
                }

                if !appState.core.definitions.isEmpty {
                    GroupBox {
                        definitionLibrary(appState.core.definitions)
                    } label: {
                        Label("Definition Library — \(appState.core.definitions.count) live from core",
                              systemImage: "books.vertical")
                    }
                }
            }
            .padding(20)
        }
    }

    private func contextBudget(_ budget: ContextBudget) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(budget.layers) { layer in
                HStack(spacing: 8) {
                    Text(layer.label)
                        .font(.caption)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    if layer.truncated {
                        Image(systemName: "scissors")
                            .font(.caption2).foregroundStyle(.orange)
                            .help("Truncated to fit the budget")
                    }
                    Text("\(layer.grantedTokens.formatted()) / \(layer.requestedTokens.formatted())")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
            }
            Divider()
            HStack {
                Text("Granted \(budget.grantedTotal.formatted()) of \(budget.totalBudget.formatted())")
                    .font(.caption.weight(.semibold))
                Spacer()
                Text("\(budget.remaining.formatted()) remaining")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding(6)
    }

    private func definitionLibrary(_ defs: [LibraryDefinition]) -> some View {
        let grouped = Dictionary(grouping: defs) { $0.category.isEmpty ? "Other" : $0.category }
        return VStack(alignment: .leading, spacing: 8) {
            ForEach(grouped.sorted { $0.key < $1.key }, id: \.key) { category, items in
                Text(category)
                    .font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                ForEach(items) { def in
                    HStack(spacing: 8) {
                        Image(systemName: "doc.plaintext").font(.caption2).foregroundStyle(.tint)
                        Text(def.name).font(.callout)
                        if !def.scope.isEmpty {
                            Text(def.scope)
                                .font(.caption2)
                                .padding(.horizontal, 5).padding(.vertical, 1)
                                .background(.quaternary, in: Capsule())
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                    }
                }
            }
        }
        .padding(6)
    }

}
