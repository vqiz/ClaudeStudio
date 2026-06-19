import SwiftUI
import ClaudeStudioKit

/// The Archive — a searchable list of sessions. When the core is connected it
/// shows the real, persisted session archive; otherwise it previews sample data.
struct ArchiveView: View {
    @Environment(AppState.self) private var appState
    @State private var query = ""
    @State private var selection: String?

    private let samples = ArchivedSession.samples

    var body: some View {
        VStack(spacing: 0) {
            PageHeader(title: "Archive", symbol: "archivebox", subtitle: subtitle)
                .padding(20)

            if appState.core.isConnected {
                liveList
            } else {
                sampleList
            }
        }
    }

    private var subtitle: String {
        if appState.core.isConnected {
            return "\(appState.core.sessions.count) sessions · live from core"
        }
        return "\(samples.count) completed sessions · sample data"
    }

    // MARK: Live

    private var liveSessions: [CoreSession] {
        let all = appState.core.sessions
        guard !query.isEmpty else { return all }
        return all.filter {
            $0.title.localizedCaseInsensitiveContains(query)
                || $0.cwd.localizedCaseInsensitiveContains(query)
                || ($0.branch ?? "").localizedCaseInsensitiveContains(query)
        }
    }

    @ViewBuilder
    private var liveList: some View {
        let sessions = liveSessions
        List(sessions, selection: $selection) { session in
            LiveSessionRow(session: session).tag(session.id)
        }
        .listStyle(.inset)
        .searchable(text: $query, placement: .toolbar, prompt: "Search sessions, paths, branches")
        .overlay {
            if sessions.isEmpty {
                if query.isEmpty {
                    ContentUnavailableView("No sessions yet",
                                           systemImage: "archivebox",
                                           description: Text("Sessions you run will be archived here, permanently."))
                } else {
                    ContentUnavailableView.search(text: query)
                }
            }
        }
    }

    // MARK: Sample fallback

    private var filteredSamples: [ArchivedSession] {
        guard !query.isEmpty else { return samples }
        return samples.filter {
            $0.title.localizedCaseInsensitiveContains(query)
                || $0.project.localizedCaseInsensitiveContains(query)
                || $0.outcome.localizedCaseInsensitiveContains(query)
        }
    }

    @ViewBuilder
    private var sampleList: some View {
        List(filteredSamples) { session in
            ArchiveRow(session: session)
        }
        .listStyle(.inset)
        .searchable(text: $query, placement: .toolbar, prompt: "Search sessions, projects, outcomes")
        .overlay {
            if filteredSamples.isEmpty {
                ContentUnavailableView.search(text: query)
            }
        }
    }
}

private struct LiveSessionRow: View {
    let session: CoreSession

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text(session.title).font(.headline)
                HStack(spacing: 8) {
                    Label(session.cwd, systemImage: "folder")
                    if let branch = session.branch {
                        Label(branch, systemImage: "arrow.triangle.branch")
                    }
                }
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 3) {
                if let model = session.model {
                    Text(model).font(.caption2.monospaced()).foregroundStyle(.secondary)
                }
                Text(Format.ago(session.createdDate)).font(.caption2).foregroundStyle(.tertiary)
            }
        }
        .padding(.vertical, 5)
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
