import SwiftUI
import ClaudeStudioKit

extension KnowledgeGraph {
    /// Build a real knowledge graph from live workspace data: a central node,
    /// your projects, the sessions that ran in them, the definition library, and
    /// the configured MCP servers. Positions are a simple deterministic radial
    /// layout the Brain View maps to pixels.
    static func build(
        projects: [Project],
        sessions: [CoreSession],
        definitions: [LibraryDefinition],
        mcpServers: [McpServer]
    ) -> KnowledgeGraph {
        var nodes: [GraphNode] = []
        var edges: [GraphEdge] = []

        func point(_ angle: Double, _ radius: Double) -> CGPoint {
            CGPoint(x: min(0.94, max(0.06, 0.5 + radius * cos(angle))),
                    y: min(0.94, max(0.06, 0.5 + radius * sin(angle))))
        }

        let center = GraphNode(label: "Workspace", kind: .concept, position: CGPoint(x: 0.5, y: 0.5))
        nodes.append(center)

        // Projects around the centre.
        var projectNodeID: [String: GraphNode.ID] = [:]
        let projectCount = max(projects.count, 1)
        for (index, project) in projects.enumerated() {
            let angle = Double(index) / Double(projectCount) * 2 * .pi - .pi / 2
            let node = GraphNode(label: project.name, kind: .concept, position: point(angle, 0.20))
            nodes.append(node)
            projectNodeID[project.path] = node.id
            edges.append(GraphEdge(from: center.id, to: node.id, weight: 0.9))
        }

        // Sessions, linked to the project they ran in (by working directory).
        for (index, session) in sessions.prefix(24).enumerated() {
            let angle = Double(index) * 2.399963  // golden angle for even spread
            let node = GraphNode(label: session.title, kind: .session, position: point(angle, 0.36))
            nodes.append(node)
            if let path = projects.first(where: { session.cwd.hasPrefix($0.path) })?.path,
               let projectID = projectNodeID[path] {
                edges.append(GraphEdge(from: projectID, to: node.id, weight: 0.6))
            } else {
                edges.append(GraphEdge(from: center.id, to: node.id, weight: 0.3))
            }
        }

        // Definition library, hung off a hub on the left.
        if !definitions.isEmpty {
            let hub = GraphNode(label: "Definitions", kind: .skill, position: point(.pi, 0.28))
            nodes.append(hub)
            edges.append(GraphEdge(from: center.id, to: hub.id, weight: 0.5))
            let shown = Array(definitions.prefix(12))
            for (index, definition) in shown.enumerated() {
                let angle = .pi + (Double(index) - Double(shown.count - 1) / 2) * 0.14
                let node = GraphNode(label: definition.name, kind: .skill, position: point(angle, 0.45))
                nodes.append(node)
                edges.append(GraphEdge(from: hub.id, to: node.id, weight: 0.4))
            }
        }

        // MCP servers, hung off a hub on the right.
        if !mcpServers.isEmpty {
            let hub = GraphNode(label: "MCP", kind: .memory, position: point(0, 0.28))
            nodes.append(hub)
            edges.append(GraphEdge(from: center.id, to: hub.id, weight: 0.5))
            let shown = Array(mcpServers.prefix(10))
            for (index, server) in shown.enumerated() {
                let angle = (Double(index) - Double(shown.count - 1) / 2) * 0.16
                let node = GraphNode(label: server.name, kind: .memory, position: point(angle, 0.45))
                nodes.append(node)
                edges.append(GraphEdge(from: hub.id, to: node.id, weight: 0.4))
            }
        }

        return KnowledgeGraph(nodes: nodes, edges: edges)
    }

    /// Whether the graph has anything beyond the lone centre node.
    var hasContent: Bool { nodes.count > 1 }
}
