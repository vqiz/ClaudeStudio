import SwiftUI

/// The OS View — a "mission control" for every running agent. A grid of session
/// cards on top, and a live Supervisor / Event-Bus stream below.
struct OSView: View {
    @Environment(AppState.self) private var appState

    private let columns = [GridItem(.adaptive(minimum: 260), spacing: 16)]

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    PageHeader(title: "OS View", symbol: "rectangle.3.group",
                               subtitle: "Mission control for every running agent")

                    LazyVGrid(columns: columns, spacing: 16) {
                        ForEach(appState.sessions) { session in
                            SessionCard(session: session, isActive: session.id == appState.activeSession?.id)
                                .onTapGesture { appState.activeSession = session }
                        }
                    }
                }
                .padding(20)
            }

            Divider()
            EventStream(events: appState.busEvents)
                .frame(height: 240)
        }
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
