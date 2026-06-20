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
        HSplitView {
            List(sessions, selection: $selection) { session in
                LiveSessionRow(session: session).tag(session.id)
            }
            .listStyle(.inset)
            .frame(minWidth: 280, idealWidth: 340, maxWidth: 460, maxHeight: .infinity)
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

            if let id = selection, let session = sessions.first(where: { $0.id == id }) {
                LiveSessionDetail(session: session)
                    .id(id)
                    .frame(minWidth: 380, maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ContentUnavailableView("Select a session to view or resume it",
                                       systemImage: "bubble.left.and.text.bubble.right")
                    .frame(minWidth: 380, maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
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

/// The transcript of an archived session, with a Resume action (like
/// `claude --resume`) when the conversation can be continued.
private struct LiveSessionDetail: View {
    @Environment(AppState.self) private var appState
    let session: CoreSession

    @State private var messages: [(role: String, content: String)] = []
    @State private var loaded = false
    @State private var resuming = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    if loaded && messages.isEmpty {
                        ContentUnavailableView("No stored transcript",
                                               systemImage: "text.alignleft",
                                               description: Text("This session has no archived messages."))
                            .padding(.top, 30)
                    }
                    ForEach(messages.indices, id: \.self) { i in
                        messageRow(messages[i])
                    }
                }
                .padding(16)
            }
        }
        .task(id: session.id) {
            loaded = false
            messages = await appState.core.sessionMessages(id: session.id)
            loaded = true
        }
    }

    private var header: some View {
        HStack(alignment: .top, spacing: 10) {
            VStack(alignment: .leading, spacing: 3) {
                Text(session.title).font(.title3.bold()).lineLimit(2)
                Text(session.cwd).font(.caption).foregroundStyle(.secondary)
                    .lineLimit(1).truncationMode(.middle).textSelection(.enabled)
            }
            Spacer()
            if session.isResumable {
                Button {
                    resuming = true
                    Task { await appState.resumeArchived(session); resuming = false }
                } label: {
                    Label("Resume conversation", systemImage: "arrow.uturn.left.circle")
                }
                .buttonStyle(.brand)
                .controlSize(.small)
                .disabled(resuming || !appState.coreConnected)
                .help("Continue this conversation with full context (claude --resume)")
            } else {
                Label("Not resumable", systemImage: "lock")
                    .font(.caption).foregroundStyle(.secondary)
                    .help("This session predates resume support, so it has no Claude session id.")
            }
        }
        .padding(14)
        .background(.bar)
    }

    private func messageRow(_ msg: (role: String, content: String)) -> some View {
        let isUser = msg.role == "user"
        return HStack(alignment: .top, spacing: 10) {
            Image(systemName: isUser ? "person.fill" : "sparkle")
                .font(.caption2.weight(.bold)).foregroundStyle(.white)
                .frame(width: 24, height: 24)
                .background((isUser ? Color.blue : Color.brandViolet).gradient,
                            in: RoundedRectangle(cornerRadius: 7, style: .continuous))
            VStack(alignment: .leading, spacing: 2) {
                Text(isUser ? "You" : "Claude").font(.caption2.weight(.semibold)).foregroundStyle(.secondary)
                Text(msg.content).font(.callout).textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            Spacer(minLength: 0)
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
