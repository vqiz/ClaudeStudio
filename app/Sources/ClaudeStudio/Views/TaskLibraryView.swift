import SwiftUI

/// The Task Library — a grid of reusable task cards (saved prompt + skill +
/// trust-mode presets) the user can launch against any project.
struct TaskLibraryView: View {
    private let tasks = TaskCard.samples
    private let columns = [GridItem(.adaptive(minimum: 240), spacing: 16)]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                PageHeader(title: "Task Library", symbol: "square.grid.2x2",
                           subtitle: "Reusable, parameterised agent tasks")

                LazyVGrid(columns: columns, spacing: 16) {
                    ForEach(tasks) { task in
                        TaskCardView(task: task)
                    }
                }
            }
            .padding(20)
        }
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
