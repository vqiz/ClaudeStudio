import SwiftUI
import ClaudeStudioKit

/// The MCP Manager — add, edit, and remove Model Context Protocol servers. Shows
/// the selected project's `.mcp.json` servers plus the user's global ones; edits
/// are written by the core.
struct MCPView: View {
    @Environment(AppState.self) private var appState

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                PageHeader(title: "MCP Servers", symbol: "puzzlepiece.extension",
                           subtitle: subtitle)

                if let project = appState.selectedProject {
                    Label("Project: \(project.name)", systemImage: "folder")
                        .font(.caption).foregroundStyle(.secondary)
                } else {
                    Label("No project selected — editing user (global) servers only.",
                          systemImage: "person")
                        .font(.caption).foregroundStyle(.secondary)
                }

                MCPManagerView(cwd: appState.selectedProject?.path)
            }
            .padding(20)
        }
    }

    private var subtitle: String {
        appState.coreConnected
            ? "Add, edit, and remove MCP servers · project + user scope"
            : "Model Context Protocol servers"
    }
}
