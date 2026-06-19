import SwiftUI
import ClaudeStudioKit

/// The Context view (Definitions section) — inspect the effective context for
/// the selected project: AGENTS.md, CLAUDE.md, active skills, hooks, and the
/// memory snippets that will be injected at session start.
struct ContextView: View {
    @Environment(AppState.self) private var appState

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                PageHeader(title: "Context", symbol: "doc.text.magnifyingglass",
                           subtitle: appState.selectedProject?.name ?? "No project selected")

                if let budget = appState.core.budget {
                    GroupBox {
                        contextBudget(budget)
                    } label: {
                        Label("Context Budget — live from core", systemImage: "gauge.with.dots.needle.67percent")
                    }
                }

                GroupBox {
                    contextFile(name: "AGENTS.md", lines: [
                        "# Atlas API — Agent Guide",
                        "- Run `cargo fmt` before committing.",
                        "- Never touch `migrations/` without approval.",
                        "- Prefer `axum` extractors over manual parsing."
                    ])
                } label: { Label("AGENTS.md", systemImage: "doc.text") }

                GroupBox {
                    contextFile(name: "CLAUDE.md", lines: [
                        "Project conventions are inherited from AGENTS.md.",
                        "Use the `tdd` skill for new modules."
                    ])
                } label: { Label("CLAUDE.md", systemImage: "doc.text") }

                GroupBox {
                    ChipFlow(items: appState.selectedProject?.skills ?? [], symbol: "wand.and.stars")
                } label: { Label("Active Skills", systemImage: "wand.and.stars") }

                GroupBox {
                    VStack(alignment: .leading, spacing: 6) {
                        hookRow("PreToolUse", "block writes to migrations/")
                        hookRow("PostToolUse", "run cargo fmt on edited Rust files")
                        hookRow("Stop", "consolidate session into Qdrant memory")
                    }
                    .padding(6)
                } label: { Label("Hooks", systemImage: "link") }

                GroupBox {
                    VStack(alignment: .leading, spacing: 8) {
                        memoryRow("Decision", "Framing settled on length-prefixed MessagePack over a Unix socket.")
                        memoryRow("Preference", "User prefers small, reviewable diffs.")
                        memoryRow("Fact", "Atlas API targets Rust 1.83 and axum 0.7.")
                    }
                    .padding(6)
                } label: { Label("Injected Memory", systemImage: "brain") }
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

    private func contextFile(name: String, lines: [String]) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            ForEach(lines, id: \.self) { line in
                Text(line).font(.system(.caption, design: .monospaced)).textSelection(.enabled)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(8)
    }

    private func hookRow(_ event: String, _ action: String) -> some View {
        HStack(spacing: 8) {
            Text(event).font(.caption.weight(.semibold).monospaced())
                .padding(.horizontal, 6).padding(.vertical, 2)
                .background(.quaternary, in: Capsule())
            Text(action).font(.caption).foregroundStyle(.secondary)
            Spacer()
        }
    }

    private func memoryRow(_ kind: String, _ text: String) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Text(kind).font(.caption2.weight(.bold)).foregroundStyle(.pink)
                .frame(width: 70, alignment: .leading)
            Text(text).font(.caption)
            Spacer()
        }
    }
}
