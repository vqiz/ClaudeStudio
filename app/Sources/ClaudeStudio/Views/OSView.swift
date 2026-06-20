import SwiftUI
import ClaudeStudioKit

/// The OS View — a "mission control" for every running agent. A grid of session
/// cards on top, and a live Supervisor / Event-Bus stream below.
struct OSView: View {
    @Environment(AppState.self) private var appState

    private let columns = [GridItem(.adaptive(minimum: 260), spacing: 16)]

    private var runningBanner: some View {
        HStack(spacing: 8) {
            ProgressView().controlSize(.small)
            Text("A session is running now…").font(.callout.weight(.medium))
            Spacer()
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.brandIndigo.opacity(0.12), in: RoundedRectangle(cornerRadius: 10))
    }

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    PageHeader(title: "OS View", symbol: "rectangle.3.group",
                               subtitle: appState.coreConnected
                               ? "\(appState.core.sessions.count) sessions · live from core"
                               : "Mission control for every running agent")

                    if appState.coreConnected {
                        if appState.core.runningSessionId != nil {
                            runningBanner
                        }
                        if appState.core.sessions.isEmpty {
                            ContentUnavailableView("No sessions yet",
                                                   systemImage: "bolt.slash",
                                                   description: Text("Run a session from a project or the Session panel; it appears here and in the Archive."))
                                .padding(.top, 30)
                        } else {
                            LazyVGrid(columns: columns, spacing: 16) {
                                ForEach(appState.core.sessions.prefix(18)) { session in
                                    LiveSessionCard(session: session)
                                }
                            }
                        }
                    } else {
                        LazyVGrid(columns: columns, spacing: 16) {
                            ForEach(appState.sessions) { session in
                                SessionCard(session: session, isActive: session.id == appState.activeSession?.id)
                                    .onTapGesture { appState.activeSession = session }
                            }
                        }
                    }
                }
                .padding(20)
            }

            Divider()
            if appState.coreConnected {
                LiveEventStream(events: appState.core.recentEvents)
                    .frame(height: 240)
            } else {
                EventStream(events: appState.busEvents)
                    .frame(height: 240)
            }
        }
    }
}

/// The real Supervisor / Event-Bus feed, streamed from the core over IPC.
private struct LiveEventStream: View {
    let events: [CoreEvent]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 8) {
                Label("Supervisor · Event Bus", systemImage: "dot.radiowaves.left.and.right")
                    .font(.headline)
                Text("LIVE")
                    .font(.caption2.weight(.bold))
                    .padding(.horizontal, 6).padding(.vertical, 2)
                    .background(Color.green.opacity(0.2), in: Capsule())
                    .foregroundStyle(.green)
                Spacer()
                Text("\(events.count) events").font(.caption).foregroundStyle(.secondary)
            }
            .padding(.horizontal, 16).padding(.vertical, 10)
            .background(.bar)

            if events.isEmpty {
                ContentUnavailableView("Listening for events",
                                       systemImage: "dot.radiowaves.left.and.right",
                                       description: Text("System events from the core appear here in real time."))
            } else {
                List(events) { event in
                    HStack(alignment: .top, spacing: 10) {
                        Image(systemName: "bolt.horizontal.circle")
                            .foregroundStyle(.tint).frame(width: 18)
                        VStack(alignment: .leading, spacing: 1) {
                            Text(event.label).font(.callout)
                            Text(Format.clock(event.at)).font(.caption2).foregroundStyle(.tertiary)
                        }
                        Spacer()
                    }
                    .listRowSeparator(.hidden)
                }
                .listStyle(.plain)
            }
        }
    }
}

/// A card for a real archived/running session (from the core).
private struct LiveSessionCard: View {
    let session: CoreSession

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Image(systemName: "bolt.fill").foregroundStyle(.tint)
                Text(session.model ?? "session")
                    .font(.caption.weight(.medium)).foregroundStyle(.secondary)
                Spacer()
                Text(Format.ago(session.createdDate)).font(.caption2).foregroundStyle(.tertiary)
            }
            Text(session.title).font(.headline).lineLimit(2)
            Label(session.cwd, systemImage: "folder")
                .font(.caption).foregroundStyle(.secondary)
                .lineLimit(1).truncationMode(.middle)
            if let branch = session.branch {
                Label(branch, systemImage: "arrow.triangle.branch")
                    .font(.caption2).foregroundStyle(.secondary)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 12))
    }
}

private struct SessionCard: View {
    let session: AgentSession
    let isActive: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Circle().fill(session.status.color).frame(width: 9, height: 9)
                Text(session.status.label).font(.caption.weight(.medium)).foregroundStyle(session.status.color)
                Spacer()
                TrustModeBadge(mode: session.trustMode)
            }
            Text(session.title).font(.headline).lineLimit(2)
            Label(session.projectName, systemImage: "folder").font(.caption).foregroundStyle(.secondary)
            Divider()
            HStack {
                Label(session.cost.formattedCost, systemImage: "dollarsign.circle").font(.caption)
                Spacer()
                Label(session.cost.formattedTokens, systemImage: "number").font(.caption)
            }
            .foregroundStyle(.secondary)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 12))
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .strokeBorder(isActive ? Color.accentColor : Color.clear, lineWidth: 2)
        )
    }
}

/// The live event-bus list. New entries are inserted at the top by `AppState`.
private struct EventStream: View {
    let events: [BusEvent]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Label("Supervisor · Event Bus", systemImage: "dot.radiowaves.left.and.right")
                    .font(.headline)
                Spacer()
                Text("\(events.count) events").font(.caption).foregroundStyle(.secondary)
            }
            .padding(.horizontal, 16).padding(.vertical, 10)
            .background(.bar)

            List(events) { event in
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: event.severity.symbol)
                        .foregroundStyle(event.severity.color)
                        .frame(width: 18)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(event.message).font(.callout)
                        HStack(spacing: 6) {
                            Text(event.source).font(.caption2.weight(.semibold)).foregroundStyle(.tint)
                            Text(Format.clock(event.timestamp)).font(.caption2).foregroundStyle(.tertiary)
                        }
                    }
                    Spacer()
                }
                .listRowSeparator(.hidden)
            }
            .listStyle(.plain)
        }
    }
}
