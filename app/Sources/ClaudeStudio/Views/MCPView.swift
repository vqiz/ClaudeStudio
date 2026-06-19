import SwiftUI
import ClaudeStudioKit

/// The MCP Manager — lists the Model Context Protocol servers configured for
/// Claude. Data is read live from the core (`mcp.list`, parsed from the user's
/// Claude config) when connected; otherwise a short explainer is shown.
struct MCPView: View {
    @Environment(AppState.self) private var appState

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                PageHeader(title: "MCP Servers", symbol: "puzzlepiece.extension",
                           subtitle: subtitle)

                if appState.coreConnected {
                    if appState.core.mcpServers.isEmpty {
                        ContentUnavailableView(
                            "No MCP servers configured",
                            systemImage: "puzzlepiece.extension",
                            description: Text("Add servers to your Claude config (`~/.claude.json`) and they'll appear here.")
                        )
                        .padding(.top, 40)
                    } else {
                        ForEach(appState.core.mcpServers) { server in
                            MCPServerRow(server: server)
                        }
                    }
                } else {
                    ContentUnavailableView(
                        "Core offline",
                        systemImage: "bolt.horizontal.circle",
                        description: Text("Connect to the core (Settings → Rust Core) to load your MCP servers.")
                    )
                    .padding(.top, 40)
                }
            }
            .padding(20)
        }
    }

    private var subtitle: String {
        appState.coreConnected
            ? "\(appState.core.mcpServers.count) configured · live from core"
            : "Model Context Protocol servers"
    }
}

private struct MCPServerRow: View {
    let server: McpServer

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: transportSymbol)
                .font(.title3)
                .foregroundStyle(.tint)
                .frame(width: 28)
            VStack(alignment: .leading, spacing: 3) {
                Text(server.name).font(.headline)
                Text(server.target)
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 4) {
                tag(server.transport.uppercased(), color: .blue)
                if !server.scope.isEmpty {
                    tag(server.scope.capitalized, color: .secondary)
                }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 12))
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
        Text(text)
            .font(.caption2.weight(.semibold))
            .padding(.horizontal, 7).padding(.vertical, 2)
            .background(color.opacity(0.16), in: Capsule())
            .foregroundStyle(color)
    }
}
