import SwiftUI

/// The Archive — a searchable list of completed sessions with outcome and cost.
struct ArchiveView: View {
    @State private var query = ""
    @State private var selection: ArchivedSession.ID?

    private let sessions = ArchivedSession.samples

    private var filtered: [ArchivedSession] {
        guard !query.isEmpty else { return sessions }
        return sessions.filter {
            $0.title.localizedCaseInsensitiveContains(query)
                || $0.project.localizedCaseInsensitiveContains(query)
                || $0.outcome.localizedCaseInsensitiveContains(query)
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Archive", symbol: "archivebox",
                       subtitle: "\(sessions.count) completed sessions")
                .padding(20)

            List(filtered, selection: $selection) { session in
                ArchiveRow(session: session).tag(session.id)
            }
            .listStyle(.inset)
            .searchable(text: $query, placement: .toolbar, prompt: "Search sessions, projects, outcomes")
            .overlay {
                if filtered.isEmpty {
                    ContentUnavailableView.search(text: query)
                }
            }
        }
    }
}

private struct ArchiveRow: View {
    let session: ArchivedSession

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text(session.title).font(.headline)
                HStack(spacing: 8) {
                    Label(session.project, systemImage: "folder")
                    Label(session.outcome, systemImage: "checkmark.seal")
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 3) {
                TrustModeBadge(mode: session.trustMode)
                Text(Format.usd(session.costUSD)).font(.caption.monospaced())
                Text(Format.ago(session.finishedAt)).font(.caption2).foregroundStyle(.tertiary)
            }
        }
        .padding(.vertical, 5)
    }
}
