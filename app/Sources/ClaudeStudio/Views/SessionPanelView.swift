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
        let cost = session.cost
        return VStack(spacing: 6) {
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

/// One transcript entry. Tool calls render as a collapsible disclosure with the
/// invocation input and (when available) the captured output.
private struct TranscriptRow: View {
    let event: SessionEvent
    @State private var expanded = false

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
        }
    }

    private func toolCallView(_ call: ToolCall) -> some View {
        DisclosureGroup(isExpanded: $expanded) {
            VStack(alignment: .leading, spacing: 6) {
                labeled("Input", call.input)
                if let output = call.output { labeled("Output", output) }
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
