import SwiftUI
import ClaudeStudioKit

/// The Task Library. When the core is connected it lists the real shipped task
/// definitions (grouped by category); otherwise it previews sample cards.
struct TaskLibraryView: View {
    @Environment(AppState.self) private var appState
    private let samples = TaskCard.samples
    private let columns = [GridItem(.adaptive(minimum: 260), spacing: 16)]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                PageHeader(title: "Task Library", symbol: "square.grid.2x2", subtitle: subtitle)

                if appState.core.isConnected {
                    liveGrid
                } else {
                    sampleGrid
                }
            }
            .padding(20)
        }
    }

    private var subtitle: String {
        appState.core.isConnected
            ? "\(appState.core.tasks.count) one-click workflows · live from core"
            : "Reusable, parameterised agent tasks · sample data"
    }

    // MARK: Live

    private var groupedTasks: [(String, [LibraryTask])] {
        let groups = Dictionary(grouping: appState.core.tasks) { $0.category.isEmpty ? "Other" : $0.category }
        return groups.sorted { $0.key < $1.key }
    }

    @ViewBuilder
    private var liveGrid: some View {
        ForEach(groupedTasks, id: \.0) { category, tasks in
            Text(category)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(.secondary)
                .padding(.top, 4)
            LazyVGrid(columns: columns, spacing: 16) {
                ForEach(tasks) { task in
                    LiveTaskCardView(task: task)
                }
            }
        }
    }

    // MARK: Sample fallback

    @ViewBuilder
    private var sampleGrid: some View {
        LazyVGrid(columns: columns, spacing: 16) {
            ForEach(samples) { task in
                TaskCardView(task: task)
            }
        }
    }
}

private struct LiveTaskCardView: View {
    let task: LibraryTask
    @State private var hovering = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Image(systemName: "wand.and.stars").foregroundStyle(.tint)
                Text(task.name).font(.headline).lineLimit(2)
                Spacer()
            }
            if !task.summary.isEmpty {
                Text(task.summary)
                    .font(.callout).foregroundStyle(.secondary)
                    .lineLimit(3)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if !task.tags.isEmpty {
                ChipFlow(items: Array(task.tags.prefix(4)), symbol: "tag")
            }
            Spacer(minLength: 0)
            Button {
            } label: {
                Label("Run", systemImage: "play.fill").frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.small)
        }
        .padding(16)
        .frame(maxWidth: .infinity, minHeight: 150, alignment: .leading)
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 12))
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .strokeBorder(hovering ? Color.accentColor.opacity(0.5) : .clear, lineWidth: 1.5)
        )
        .onHover { hovering = $0 }
    }
}

private struct TaskCardView: View {
    let task: TaskCard
    @State private var hovering = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Image(systemName: "wand.and.stars").foregroundStyle(.tint)
                Text(task.title).font(.headline)
                Spacer()
            }
            Text(task.summary).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Divider()
            HStack {
                Label(task.skill, systemImage: "puzzlepiece.extension").font(.caption)
                Spacer()
                TrustModeBadge(mode: task.defaultTrustMode)
            }
            HStack {
                Label("~\(Format.usd(task.estimatedCostUSD))", systemImage: "dollarsign.circle").font(.caption)
                Spacer()
                Text("\(task.runCount) runs").font(.caption).foregroundStyle(.secondary)
            }
            Button {
            } label: {
                Label("Launch", systemImage: "play.fill").frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.small)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 12))
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .strokeBorder(hovering ? Color.accentColor.opacity(0.5) : .clear, lineWidth: 1.5)
        )
        .onHover { hovering = $0 }
    }
}
