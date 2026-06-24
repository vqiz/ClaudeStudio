import SwiftUI

/// The live session panel: a streaming transcript with collapsible tool calls,
/// a header showing model + trust mode + status, and a footer cost counter.
struct SessionPanelView: View {
    @Environment(AppState.self) private var appState
    @State private var prompt = ""

    var body: some View {
        VStack(spacing: 0) {
            if appState.coreConnected {
                liveSessionPanel
            } else if let session = appState.activeSession {
                header(session)
                Divider()
                transcript(session)
                Divider()
                costFooter(session)
            } else {
                ContentUnavailableView("No active session", systemImage: "bolt.slash")
            }
        }
        .background(.background)
    }

    // MARK: Live session (real Claude, streamed from the core)

    private var liveSessionPanel: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                Label("Live Session", systemImage: "sparkles").font(.headline)
                if appState.core.runningSessionId != nil {
                    ProgressView().controlSize(.small)
                    Text("running…").font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                TrustModeBadge(mode: appState.globalTrustMode)
            }
            .padding(12)
            .background(.bar)
            Divider()

            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 10) {
                        if appState.core.liveSession.isEmpty {
                            ContentUnavailableView(
                                "Run a prompt",
                                systemImage: "text.cursor",
                                description: Text("The core spawns the Claude CLI and streams the result here.")
                            )
                            .padding(.top, 48)
                        }
                        ForEach(appState.core.liveSession) { item in
                            LiveSessionRow(item: item).id(item.id)
                        }
                    }
                    .padding(12)
                }
                .onChange(of: appState.core.liveSession.count) { _, _ in
                    if let last = appState.core.liveSession.last {
                        withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
                    }
                }
            }

            Divider()
            HStack(spacing: 8) {
                TextField("Ask Claude to do something…", text: $prompt, axis: .vertical)
                    .textFieldStyle(.roundedBorder)
                    .lineLimit(1...4)
                    .onSubmit(runLiveSession)
                Button(action: runLiveSession) {
                    Image(systemName: "arrow.up.circle.fill").font(.title2)
                }
                .buttonStyle(.plain)
                .disabled(prompt.trimmingCharacters(in: .whitespaces).isEmpty
                          || appState.core.runningSessionId != nil)
            }
            .padding(12)
            .background(.bar)
        }
    }

    private func runLiveSession() {
        let text = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, appState.core.runningSessionId == nil else { return }
        prompt = ""
        let project = appState.selectedProject
        Task { await appState.core.startSession(prompt: text, cwd: project?.path, model: project?.model) }
    }

    private func header(_ session: AgentSession) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(session.title).font(.headline).lineLimit(1)
                Spacer()
                statusPill(session.status)
            }
            HStack(spacing: 8) {
                Label(session.projectName, systemImage: "folder")
                if let branch = session.worktreeBranch {
                    Label(branch, systemImage: "arrow.triangle.branch")
                }
                Spacer()
                Text(session.model).monospaced()
            }
            .font(.caption)
            .foregroundStyle(.secondary)
            TrustModeBadge(mode: session.trustMode)
        }
        .padding(12)
        .background(.bar)
    }

    private func transcript(_ session: AgentSession) -> some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    ForEach(session.events) { event in
                        TranscriptRow(event: event).id(event.id)
                    }
                }
                .padding(12)
            }
            .onChange(of: session.events.count) { _, _ in
                if let last = session.events.last {
                    withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
                }
            }
        }
    }

    private func costFooter(_ session: AgentSession) -> some View {
        SessionCostFooter(cost: session.cost)
    }

    private func statusPill(_ status: AgentSession.Status) -> some View {
        Label(status.label, systemImage: "circle.fill")
            .font(.caption2.weight(.semibold))
            .imageScale(.small)
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(status.color.opacity(0.16), in: Capsule())
            .foregroundStyle(status.color)
    }
}

/// One streamed item from a live Claude session.
private struct LiveSessionRow: View {
    let item: LiveSessionEvent

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: item.symbol)
                .font(.caption)
                .foregroundStyle(.white)
                .frame(width: 22, height: 22)
                .background(color.gradient, in: Circle())
            VStack(alignment: .leading, spacing: 2) {
                if item.kind == "tool_use" {
                    Text("Tool: \(item.text)").font(.callout.weight(.medium))
                } else {
                    Text(item.text).font(.callout).textSelection(.enabled)
                }
            }
            Spacer(minLength: 0)
        }
    }

    private var color: Color {
        switch item.kind {
        case "assistant_text": return .purple
        case "tool_use", "tool_result": return .gray
        case "result": return .green
        case "error": return .red
        default: return .secondary
        }
    }
}

/// Ein geladener Kontext-Block (F145): eine Datei, ein Tool oder ein Memory-Eintrag, der aktuell
/// im Kontext der Session liegt, mit seinem Token-Anteil.
struct ContextBlock: Identifiable, Hashable, Sendable {
    enum Kind: String, Sendable, Hashable { case file, tool, memory }
    let id: UUID
    var kind: Kind
    var name: String
    var tokens: Int

    init(id: UUID = UUID(), kind: Kind, name: String, tokens: Int) {
        self.id = id; self.kind = kind; self.name = name; self.tokens = tokens
    }
}

/// Active-Context-Bar (F145): zeigt die aktuell geladenen Kontext-Blöcke (Dateien/Tools/Memory) mit
/// ihrem jeweiligen Token-Anteil — als proportionaler Balken plus Liste mit Token-Zahlen.
struct ContextBar: View {
    let blocks: [ContextBlock]

    private var total: Int { max(blocks.reduce(0) { $0 + $1.tokens }, 1) }
    private func color(_ k: ContextBlock.Kind) -> Color {
        switch k { case .file: .blue; case .tool: .green; case .memory: .purple }
    }
    private func icon(_ k: ContextBlock.Kind) -> String {
        switch k { case .file: "doc.text"; case .tool: "wrench.and.screwdriver"; case .memory: "brain" }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Label("Active Context", systemImage: "square.stack.3d.up.fill").font(.headline)
                Spacer()
                Text("\(total) tokens").font(.caption).foregroundStyle(.secondary).monospacedDigit()
            }
            GeometryReader { geo in
                HStack(spacing: 2) {
                    ForEach(blocks) { b in
                        Rectangle().fill(color(b.kind))
                            .frame(width: geo.size.width * CGFloat(b.tokens) / CGFloat(total))
                    }
                }
            }
            .frame(height: 14)
            .clipShape(Capsule())
            VStack(alignment: .leading, spacing: 5) {
                ForEach(blocks) { b in
                    HStack(spacing: 6) {
                        Image(systemName: icon(b.kind)).foregroundStyle(color(b.kind))
                        Text(b.name)
                        Spacer()
                        Text("\(b.tokens) tok").foregroundStyle(.secondary).monospacedDigit()
                    }
                    .font(.caption)
                }
            }
        }
        .padding(14)
    }
}

/// Split-View (F146): links die Session-Transkript-Spalte, rechts die gerade vom Agent bearbeitete
/// Datei in einer READ-ONLY-Ansicht. Die „bearbeitete Datei" wird aus dem letzten Edit/Write-Tool-Call
/// der Session abgeleitet. Ein echter `HSplitView` mit verschiebbarem Trenner.
struct SessionSplitView: View {
    let events: [SessionEvent]
    let fileContent: String

    /// Die zuletzt bearbeitete Datei aus den Events (letzter Edit/Write-Tool-Call).
    var editedFile: String {
        for event in events.reversed() {
            if case .toolCall(let call) = event.kind,
               call.name == "Edit" || call.name == "Write" {
                // Der Dateiname steht am Anfang des Tool-Inputs (vor " — ").
                return call.input.components(separatedBy: " — ").first ?? call.input
            }
        }
        return "—"
    }

    var body: some View {
        HSplitView {
            VStack(alignment: .leading, spacing: 8) {
                Label("Session", systemImage: "sparkles").font(.headline)
                ScrollView {
                    VStack(alignment: .leading, spacing: 10) {
                        ForEach(events) { TranscriptRow(event: $0) }
                    }
                }
            }
            .padding(12).frame(minWidth: 260)

            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Label(editedFile, systemImage: "doc.text").font(.headline)
                    Spacer()
                    Text("read-only")
                        .font(.caption2.weight(.semibold))
                        .padding(.horizontal, 6).padding(.vertical, 2)
                        .background(.quaternary, in: Capsule())
                        .foregroundStyle(.secondary)
                }
                ScrollView {
                    Text(fileContent)
                        .font(.system(.caption, design: .monospaced))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .padding(12).frame(minWidth: 260)
        }
    }
}

/// Der Live-Kosten-Footer (F144): zeigt den laufenden USD-Counter (akkumulierte Kosten /
/// Budget), die Token-Zahl und einen Live-Indikator. Der `CostTracker` summiert die Kosten
/// JEDER Modell-Antwort (Event), daher steigt der Counter mit jeder Antwort. Eine einzige
/// Quelle der Wahrheit, die SessionPanelView und der UITest nutzen.
struct SessionCostFooter: View {
    let cost: CostTracker

    var body: some View {
        VStack(spacing: 6) {
            ProgressView(value: cost.budgetFraction) {
                HStack {
                    Text("Budget")
                    Spacer()
                    Text("\(cost.formattedCost) / \(Format.usd(cost.budgetUSD))")
                        .foregroundStyle(cost.isOverBudget ? .red : .secondary)
                }
                .font(.caption)
            }
            .tint(cost.isOverBudget ? .red : .accentColor)

            HStack {
                Label(cost.formattedTokens, systemImage: "number")
                Spacer()
                Label("Live", systemImage: "dot.radiowaves.left.and.right")
                    .foregroundStyle(.green)
            }
            .font(.caption)
            .foregroundStyle(.secondary)
        }
        .padding(12)
        .background(.bar)
    }
}

/// One transcript entry. Tool calls render as a collapsible disclosure with the
/// invocation input and (when available) the captured output.
struct TranscriptRow: View {
    let event: SessionEvent
    /// Anfangszustand der Tool-Call-Karte (F137: auf-/zuklappbar). Default zugeklappt.
    var initiallyExpanded = false
    @State private var expanded = false

    init(event: SessionEvent, initiallyExpanded: Bool = false) {
        self.event = event
        self.initiallyExpanded = initiallyExpanded
        _expanded = State(initialValue: initiallyExpanded)
    }

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            roleGlyph
            VStack(alignment: .leading, spacing: 4) {
                content
                Text(Format.clock(event.timestamp)).font(.caption2).foregroundStyle(.tertiary)
            }
            Spacer(minLength: 0)
        }
    }

    @ViewBuilder
    private var content: some View {
        switch event.kind {
        case .message(let text):
            Text(text).font(.callout).textSelection(.enabled)
        case .planStep(let text):
            Label(text, systemImage: "list.bullet.indent").font(.callout).foregroundStyle(.secondary)
        case .status(let text):
            Label(text, systemImage: "sparkles").font(.caption).foregroundStyle(.purple)
        case .permissionRequest(let text):
            Label(text, systemImage: "hand.raised").font(.callout).foregroundStyle(.orange)
        case .toolCall(let call):
            toolCallView(call)
        case .finding(let finding):
            findingView(finding)
        case .thinking(let text):
            thinkingView(text)
        }
    }

    /// F147: Extended-Thinking als kollabierbare Sektion — ein Disclosure-Button blendet den
    /// Denkprozess ein/aus. Standardmäßig zugeklappt.
    private func thinkingView(_ text: String) -> some View {
        DisclosureGroup(isExpanded: $expanded) {
            Text(text)
                .font(.system(.caption, design: .monospaced))
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
                .padding(.top, 4)
        } label: {
            HStack(spacing: 6) {
                Image(systemName: "brain")
                Text("Extended Thinking").font(.callout.weight(.semibold))
            }
            .foregroundStyle(.purple)
        }
        .padding(8)
        .background(.purple.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
    }

    /// F148: ein Finding inline als hervorgehobener Block — Schweregrad-Farbe, Nachricht und
    /// Datei:Zeilennummer.
    private func findingView(_ f: CodeFinding) -> some View {
        let color: Color = f.severity == .high ? .red : (f.severity == .medium ? .orange : .yellow)
        return VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(color)
                Text(f.severity.rawValue.uppercased())
                    .font(.caption2.weight(.bold)).foregroundStyle(color)
                Text(f.message).font(.callout.weight(.medium))
            }
            Text("\(f.file):\(f.line)")
                .font(.system(.caption, design: .monospaced))
                .foregroundStyle(.secondary)
        }
        .padding(8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(color.opacity(0.10), in: RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(color.opacity(0.4), lineWidth: 1))
    }

    private func toolCallView(_ call: ToolCall) -> some View {
        DisclosureGroup(isExpanded: $expanded) {
            VStack(alignment: .leading, spacing: 6) {
                labeled("Input", call.input)
                // F149: Tool-Output strukturiert — stdout (JSON ggf. eingerückt) und Exit-Code getrennt.
                if let output = call.formattedOutput {
                    labeled(call.exitCode != nil ? "stdout" : "Output", output)
                }
                if let code = call.exitCode {
                    HStack(spacing: 6) {
                        Text("exit code").font(.caption2.weight(.semibold)).foregroundStyle(.secondary)
                        Text("\(code)")
                            .font(.system(.caption, design: .monospaced).weight(.bold))
                            .padding(.horizontal, 6).padding(.vertical, 2)
                            .background((code == 0 ? Color.green : Color.red).opacity(0.16), in: Capsule())
                            .foregroundStyle(code == 0 ? .green : .red)
                    }
                }
            }
            .padding(.top, 4)
        } label: {
            HStack(spacing: 6) {
                Image(systemName: "wrench.and.screwdriver")
                Text(call.name).font(.callout.weight(.semibold))
                toolStatus(call.status)
            }
        }
        .padding(8)
        .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 8))
    }

    private func labeled(_ title: String, _ body: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.caption2.weight(.semibold)).foregroundStyle(.secondary)
            Text(body).font(.system(.caption, design: .monospaced)).textSelection(.enabled)
        }
    }

    private func toolStatus(_ status: ToolCall.Status) -> some View {
        let (text, color): (String, Color)
        switch status {
        case .running: (text, color) = ("running", .blue)
        case .succeeded: (text, color) = ("ok", .green)
        case .failed: (text, color) = ("failed", .red)
        case .awaitingApproval: (text, color) = ("approve?", .orange)
        }
        return Text(text)
            .font(.caption2.weight(.semibold))
            .padding(.horizontal, 6).padding(.vertical, 2)
            .background(color.opacity(0.16), in: Capsule())
            .foregroundStyle(color)
    }

    private var roleGlyph: some View {
        let (symbol, color): (String, Color)
        switch event.role {
        case .user: (symbol, color) = ("person.fill", .blue)
        case .assistant: (symbol, color) = ("sparkle", .purple)
        case .tool: (symbol, color) = ("wrench.fill", .gray)
        case .system: (symbol, color) = ("gearshape.fill", .secondary)
        case .supervisor: (symbol, color) = ("eye.fill", .orange)
        }
        return Image(systemName: symbol)
            .font(.caption)
            .foregroundStyle(.white)
            .frame(width: 22, height: 22)
            .background(color.gradient, in: Circle())
    }
}
