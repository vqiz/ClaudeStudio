import SwiftUI
import Charts
import ClaudeStudioKit

/// The Archive — reimagined as a Google-Analytics-style session dashboard:
/// a scorecard row of KPIs, a "sessions over time" trend, a top-projects
/// breakdown, and a recent-sessions table. When the core is connected it reports
/// on the real, persisted archive; otherwise it previews sample data.
struct ArchiveView: View {
    @Environment(AppState.self) private var appState
    @State private var query = ""
    @State private var period: DashboardPeriod = .d28
    @State private var transcript: CoreSession?

    private let samples = ArchivedSession.samples

    // MARK: Unified data

    /// Every archived session normalised to one shape, so live + sample render
    /// identically.
    private var items: [DashItem] {
        if appState.core.isConnected {
            return appState.core.sessions.map { s in
                DashItem(id: s.id,
                         date: s.createdDate,
                         title: s.title,
                         project: URL(fileURLWithPath: s.cwd).lastPathComponent.nilIfEmpty ?? s.cwd,
                         model: s.model,
                         resumable: s.isResumable,
                         cost: nil,
                         live: s)
            }
        }
        return samples.map { s in
            DashItem(id: s.id.uuidString,
                     date: s.finishedAt,
                     title: s.title,
                     project: s.project,
                     model: nil,
                     resumable: false,
                     cost: s.costUSD,
                     live: nil)
        }
    }

    private var cutoff: Date? {
        guard let days = period.days else { return nil }
        return Calendar.current.date(byAdding: .day, value: -days, to: .now)
    }

    private var windowItems: [DashItem] {
        guard let cutoff else { return items }
        return items.filter { $0.date >= cutoff }
    }

    /// Items in the window immediately before the current one (for deltas).
    private var priorItems: [DashItem] {
        guard let days = period.days,
              let cutoff,
              let priorStart = Calendar.current.date(byAdding: .day, value: -days, to: cutoff)
        else { return [] }
        return items.filter { $0.date >= priorStart && $0.date < cutoff }
    }

    private var windowDelta: Double? {
        guard period.days != nil, !priorItems.isEmpty else { return nil }
        return (Double(windowItems.count) - Double(priorItems.count)) / Double(priorItems.count)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: DS.s4) {
                PageHeader(title: "Archive", symbol: "chart.bar.xaxis", subtitle: subtitle) {
                    PeriodPicker(period: $period)
                }

                scorecards
                charts
                recentSessions
            }
            .padding(DS.s4)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .dashboardCanvas()
        .sheet(item: $transcript) { session in
            NavigationStack {
                LiveSessionDetail(session: session)
                    .frame(minWidth: 540, minHeight: 520)
                    .toolbar {
                        ToolbarItem(placement: .cancellationAction) {
                            Button("Done") { transcript = nil }
                        }
                    }
            }
        }
    }

    private var subtitle: String {
        appState.core.isConnected
            ? "\(items.count) sessions · live from core"
            : "\(items.count) sessions · sample data"
    }

    // MARK: Scorecards

    private var scorecards: some View {
        let resumable = items.filter(\.resumable).count
        let projects = Set(items.map(\.project)).count
        let models = Set(items.compactMap(\.model)).count
        let resumablePct = items.isEmpty ? 0 : Int((Double(resumable) / Double(items.count)) * 100)

        return MetricGrid {
            MetricCard(label: "Total sessions", value: "\(items.count)",
                       symbol: "tray.full", tint: .gBlue,
                       spark: dailySeries(items, lastDays: 14))
            MetricCard(label: "In \(period.short) window", value: "\(windowItems.count)",
                       symbol: "calendar", tint: .gGreen,
                       delta: windowDelta,
                       footnote: windowDelta == nil ? "vs. prior period" : nil)
            MetricCard(label: "Resumable", value: "\(resumable)",
                       symbol: "arrow.uturn.left.circle", tint: .gYellow,
                       footnote: "\(resumablePct)% of sessions")
            MetricCard(label: "Projects", value: "\(projects)",
                       symbol: "folder", tint: .gPurple,
                       footnote: "\(models) model\(models == 1 ? "" : "s") used")
        }
    }

    // MARK: Charts

    private var charts: some View {
        VStack(spacing: DS.s4) {
            ChartCard(title: "Sessions over time", symbol: "chart.bar.fill",
                      subtitle: "Daily sessions in the \(period.rawValue.lowercased()) window") {
                let buckets = dailyBuckets(windowItems)
                if buckets.allSatisfy({ $0.count == 0 }) {
                    emptyChart("No sessions in this period")
                } else {
                    Chart(buckets) { b in
                        BarMark(
                            x: .value("Day", b.date, unit: .day),
                            y: .value("Sessions", b.count)
                        )
                        .foregroundStyle(Color.gBlue.gradient)
                        .cornerRadius(3)
                    }
                    .chartYAxis { AxisMarks(position: .leading) }
                    .frame(height: 220)
                }
            }

            HStack(alignment: .top, spacing: DS.s4) {
                ChartCard(title: "Top projects", symbol: "folder.fill",
                          subtitle: "Sessions by project") {
                    breakdownChart(topCounts(items.map(\.project), limit: 6), tint: .gBlue,
                                   empty: "No projects yet")
                }
                ChartCard(title: "By model", symbol: "cpu",
                          subtitle: "Sessions by model") {
                    breakdownChart(topCounts(items.compactMap(\.model), limit: 6), tint: .gGreen,
                                   empty: "No model data")
                }
            }
        }
    }

    @ViewBuilder
    private func breakdownChart(_ data: [NamedCount], tint: Color, empty: String) -> some View {
        if data.isEmpty {
            emptyChart(empty)
        } else {
            Chart(data) { item in
                BarMark(
                    x: .value("Sessions", item.count),
                    y: .value("Name", item.name)
                )
                .foregroundStyle(tint.gradient)
                .cornerRadius(3)
                .annotation(position: .trailing, alignment: .leading) {
                    Text("\(item.count)").font(.caption2).foregroundStyle(.secondary)
                }
            }
            .chartXAxis(.hidden)
            .frame(height: max(120, CGFloat(data.count) * 34))
        }
    }

    private func emptyChart(_ message: String) -> some View {
        Text(message)
            .font(.callout).foregroundStyle(.secondary)
            .frame(maxWidth: .infinity, minHeight: 160)
    }

    // MARK: Recent sessions

    private var recentSessions: some View {
        let recent = windowItems.sorted { $0.date > $1.date }
        let shown = query.isEmpty ? recent : recent.filter {
            $0.title.localizedCaseInsensitiveContains(query)
                || $0.project.localizedCaseInsensitiveContains(query)
        }
        return ChartCard(title: "Recent sessions", symbol: "list.bullet.rectangle",
                         subtitle: "\(shown.count) shown") {
            TextField("Search sessions or projects", text: $query)
                .textFieldStyle(.roundedBorder)
                .padding(.bottom, DS.s1)

            if shown.isEmpty {
                emptyChart(query.isEmpty ? "No sessions in this period" : "No matches for “\(query)”")
            } else {
                VStack(spacing: 0) {
                    ForEach(shown.prefix(40)) { item in
                        SessionTableRow(item: item) {
                            if let live = item.live { transcript = live }
                        } onResume: {
                            if let live = item.live {
                                Task { await appState.resumeArchived(live) }
                            }
                        }
                        if item.id != shown.prefix(40).last?.id {
                            Divider().overlay(DS.hairline)
                        }
                    }
                }
            }
        }
    }
}

// MARK: - Models

private struct DashItem: Identifiable {
    let id: String
    let date: Date
    let title: String
    let project: String
    let model: String?
    let resumable: Bool
    let cost: Double?
    let live: CoreSession?
}

private struct DayBucket: Identifiable {
    let date: Date
    let count: Int
    var id: Date { date }
}

private struct NamedCount: Identifiable {
    let name: String
    let count: Int
    var id: String { name }
}

// MARK: - Aggregation helpers

extension ArchiveView {
    /// One bucket per calendar day across the current window, zero-filled so the
    /// trend reads correctly (the GA "continuous axis" look).
    fileprivate func dailyBuckets(_ items: [DashItem]) -> [DayBucket] {
        let cal = Calendar.current
        let days = period.days ?? max(1, daysSpan(items))
        let today = cal.startOfDay(for: .now)
        var counts: [Date: Int] = [:]
        for it in items {
            counts[cal.startOfDay(for: it.date), default: 0] += 1
        }
        return (0..<days).reversed().compactMap { offset -> DayBucket? in
            guard let day = cal.date(byAdding: .day, value: -offset, to: today) else { return nil }
            return DayBucket(date: day, count: counts[day] ?? 0)
        }
    }

    /// A compact sparkline series (counts per day) for a scorecard.
    fileprivate func dailySeries(_ items: [DashItem], lastDays: Int) -> [Double] {
        let cal = Calendar.current
        let today = cal.startOfDay(for: .now)
        var counts: [Date: Int] = [:]
        for it in items { counts[cal.startOfDay(for: it.date), default: 0] += 1 }
        return (0..<lastDays).reversed().map { offset -> Double in
            guard let day = cal.date(byAdding: .day, value: -offset, to: today) else { return 0 }
            return Double(counts[day] ?? 0)
        }
    }

    private func daysSpan(_ items: [DashItem]) -> Int {
        guard let earliest = items.map(\.date).min() else { return 1 }
        let days = Calendar.current.dateComponents([.day], from: earliest, to: .now).day ?? 1
        return min(max(days + 1, 1), 120)
    }

    /// The top-N most frequent values with their counts, descending.
    fileprivate func topCounts(_ values: [String], limit: Int) -> [NamedCount] {
        var counts: [String: Int] = [:]
        for v in values where !v.isEmpty { counts[v, default: 0] += 1 }
        return counts.map { NamedCount(name: $0.key, count: $0.value) }
            .sorted { $0.count > $1.count }
            .prefix(limit)
            .map { $0 }
    }
}

private extension String {
    var nilIfEmpty: String? { isEmpty ? nil : self }
}

// MARK: - Recent-session row

private struct SessionTableRow: View {
    let item: DashItem
    var onOpen: () -> Void
    var onResume: () -> Void
    @State private var hovering = false

    var body: some View {
        HStack(spacing: DS.s3) {
            Image(systemName: "bubble.left.and.text.bubble.right")
                .font(.callout)
                .foregroundStyle(Color.gBlue)
                .frame(width: 30, height: 30)
                .background(Color.gBlue.opacity(0.1), in: RoundedRectangle(cornerRadius: 8, style: .continuous))

            VStack(alignment: .leading, spacing: 2) {
                Text(item.title).font(.callout.weight(.medium)).lineLimit(1)
                HStack(spacing: 8) {
                    Label(item.project, systemImage: "folder").lineLimit(1)
                    if let model = item.model {
                        Label(model, systemImage: "cpu").lineLimit(1)
                    }
                }
                .font(.caption).foregroundStyle(.secondary)
            }

            Spacer(minLength: DS.s2)

            if let cost = item.cost {
                Text(Format.usd(cost)).font(.caption.monospaced()).foregroundStyle(.secondary)
            }
            Text(Format.ago(item.date)).font(.caption2).foregroundStyle(.tertiary)

            if item.resumable {
                Button(action: onResume) {
                    Image(systemName: "arrow.uturn.left.circle.fill")
                }
                .buttonStyle(.plain)
                .foregroundStyle(Color.gGreen)
                .help("Resume this conversation (claude --resume)")
            }
        }
        .padding(.vertical, DS.s2)
        .padding(.horizontal, DS.s1)
        .background(hovering ? Color.gBlue.opacity(0.05) : .clear,
                    in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .contentShape(Rectangle())
        .onTapGesture { onOpen() }
        .onHover { hovering = $0 }
    }
}

/// The transcript of an archived session, with a Resume action when the
/// conversation can be continued. Shown in a sheet from the dashboard table.
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
                .background((isUser ? Color.gBlue : Color.brandViolet).gradient,
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
