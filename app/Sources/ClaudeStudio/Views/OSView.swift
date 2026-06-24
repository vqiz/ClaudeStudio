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
        .background(Color.gGreen.opacity(0.12), in: RoundedRectangle(cornerRadius: DS.rSm))
        .overlay(RoundedRectangle(cornerRadius: DS.rSm).strokeBorder(Color.gGreen.opacity(0.3), lineWidth: 1))
    }

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    PageHeader(title: "OS View", symbol: "rectangle.3.group",
                               subtitle: appState.coreConnected
                               ? "\(appState.core.sessions.count) sessions · live from core"
                               : "Mission control for every running agent")

                    scorecards

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
        .dashboardCanvas()
    }

    /// A Google-style KPI scorecard row summarising mission-control state.
    @ViewBuilder
    private var scorecards: some View {
        if appState.coreConnected {
            let sessions = appState.core.sessions
            let running = appState.core.runningSessionId != nil ? 1 : 0
            let projects = Set(sessions.map(\.cwd)).count
            MetricGrid {
                MetricCard(label: "Sessions", value: "\(sessions.count)",
                           symbol: "bolt.fill", tint: .gBlue)
                MetricCard(label: "Running now", value: "\(running)",
                           symbol: "play.circle.fill", tint: running > 0 ? .gGreen : .secondary,
                           footnote: running > 0 ? "live" : "idle")
                MetricCard(label: "Projects", value: "\(projects)",
                           symbol: "folder.fill", tint: .gYellow)
                MetricCard(label: "Events", value: "\(appState.core.recentEvents.count)",
                           symbol: "dot.radiowaves.left.and.right", tint: .gPurple,
                           footnote: "on the bus")
            }
        } else {
            let active = appState.sessions.filter { $0.id == appState.activeSession?.id }.count
            MetricGrid {
                MetricCard(label: "Sessions", value: "\(appState.sessions.count)",
                           symbol: "bolt.fill", tint: .gBlue)
                MetricCard(label: "Active", value: "\(active)",
                           symbol: "play.circle.fill", tint: active > 0 ? .gGreen : .secondary)
                MetricCard(label: "Events", value: "\(appState.busEvents.count)",
                           symbol: "dot.radiowaves.left.and.right", tint: .gPurple,
                           footnote: "on the bus")
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
        .frame(maxWidth: .infinity, alignment: .leading)
        .dsCard()
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
        .frame(maxWidth: .infinity, alignment: .leading)
        .dsCard()
        .overlay(
            RoundedRectangle(cornerRadius: DS.rMd, style: .continuous)
                .strokeBorder(isActive ? Color.gBlue : Color.clear, lineWidth: 2)
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
