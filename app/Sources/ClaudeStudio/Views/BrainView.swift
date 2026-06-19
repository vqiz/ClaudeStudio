import SwiftUI

/// The Brain View — a `Canvas`-rendered force-directed knowledge-graph
/// placeholder. Nodes are seeded with normalised positions and given a light
/// breathing animation; a real force simulation can replace `jitter` later.
struct BrainView: View {
    @State private var graph = KnowledgeGraph.sample()
    @State private var phase: Double = 0
    @State private var selected: GraphNode.ID?

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                PageHeader(title: "Brain View", symbol: "brain",
                           subtitle: "Semantic memory · concepts, files, sessions")
                legend
            }
            .padding(20)

            GeometryReader { geometry in
                Canvas { context, size in
                    draw(in: &context, size: size)
                } symbols: {
                    ForEach(graph.nodes) { node in
                        Text(node.label)
                            .font(.caption2.weight(.medium))
                            .tag(node.id)
                    }
                }
                .background(graphBackground)
                .onTapGesture { location in
                    selected = nearestNode(to: location, in: geometry.size)
                }
            }
            .overlay(alignment: .bottomLeading) { selectionInfo }
        }
        .onAppear {
            withAnimation(.easeInOut(duration: 4).repeatForever(autoreverses: true)) {
                phase = 1
            }
        }
    }

    private func point(for node: GraphNode, size: CGSize) -> CGPoint {
        let jitterX = sin(phase * .pi * 2 + Double(node.label.count)) * 6
        let jitterY = cos(phase * .pi * 2 + Double(node.label.count)) * 6
        return CGPoint(x: node.position.x * size.width + jitterX,
                       y: node.position.y * size.height + jitterY)
    }

    private func draw(in context: inout GraphicsContext, size: CGSize) {
        // Edges first, so nodes paint on top.
        for edge in graph.edges {
            guard let from = graph.nodes.first(where: { $0.id == edge.from }),
                  let to = graph.nodes.first(where: { $0.id == edge.to }) else { continue }
            var path = Path()
            path.move(to: point(for: from, size: size))
            path.addLine(to: point(for: to, size: size))
            context.stroke(path, with: .color(.secondary.opacity(0.25 + edge.weight * 0.3)),
                           lineWidth: 1 + edge.weight)
        }

        for node in graph.nodes {
            let center = point(for: node, size: size)
            let radius: CGFloat = node.id == selected ? 26 : 20
            let rect = CGRect(x: center.x - radius, y: center.y - radius, width: radius * 2, height: radius * 2)
            context.fill(Circle().path(in: rect), with: .color(node.kind.color.opacity(0.85)))
            context.stroke(Circle().path(in: rect),
                           with: .color(node.id == selected ? .primary : node.kind.color),
                           lineWidth: node.id == selected ? 3 : 1.5)
            if let resolved = context.resolveSymbol(id: node.id) {
                context.draw(resolved, at: CGPoint(x: center.x, y: center.y + radius + 10))
            }
        }
    }

    private func nearestNode(to location: CGPoint, in size: CGSize) -> GraphNode.ID? {
        graph.nodes.min { lhs, rhs in
            hypot(point(for: lhs, size: size).x - location.x, point(for: lhs, size: size).y - location.y)
                < hypot(point(for: rhs, size: size).x - location.x, point(for: rhs, size: size).y - location.y)
        }?.id
    }

    private var graphBackground: some View {
        LinearGradient(colors: [Color.black.opacity(0.04), Color.purple.opacity(0.06)],
                       startPoint: .top, endPoint: .bottom)
    }

    private var legend: some View {
        HStack(spacing: 12) {
            ForEach([GraphNode.Kind.concept, .file, .session, .skill, .memory], id: \.self) { kind in
                HStack(spacing: 4) {
                    Circle().fill(kind.color).frame(width: 8, height: 8)
                    Text(String(describing: kind).capitalized).font(.caption2).foregroundStyle(.secondary)
                }
            }
        }
    }

    @ViewBuilder
    private var selectionInfo: some View {
        if let id = selected, let node = graph.nodes.first(where: { $0.id == id }) {
            VStack(alignment: .leading, spacing: 2) {
                Text(node.label).font(.headline)
                Text(String(describing: node.kind).capitalized).font(.caption).foregroundStyle(node.kind.color)
            }
            .padding(12)
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 10))
            .padding(20)
        }
    }
}
