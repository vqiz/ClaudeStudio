import SwiftUI

/// The Voice Assistant — talk to Claude hands-free. Press the mic (or push-to-
/// talk), speak a command; it's transcribed on-device, run through the `claude`
/// CLI in the selected project, and the reply is read back aloud. Speech input
/// needs the packaged app + microphone permission; reading aloud works anywhere.
struct VoiceLogView: View {
    @Environment(AppState.self) private var appState

    var body: some View {
        @Bindable var voice = appState.voice

        VStack(spacing: 0) {
            HStack {
                PageHeader(title: "Voice Assistant", symbol: "waveform",
                           subtitle: "Talk to Claude — on-device speech, spoken replies")
                Toggle(isOn: $voice.readAloud) {
                    Label("Read responses aloud", systemImage: "speaker.wave.2.fill")
                }
                .toggleStyle(.switch)
            }
            .padding(20)

            micPanel
                .padding(.horizontal, 20)

            Divider().padding(.top, 16)

            conversation
        }
    }

    // MARK: Mic + state

    private var voice: VoiceController { appState.voice }

    private var micPanel: some View {
        VStack(spacing: 12) {
            Button(action: voice.toggleListening) {
                ZStack {
                    Circle()
                        .fill(stateColor.gradient)
                        .frame(width: 96, height: 96)
                        .shadow(color: stateColor.opacity(0.45), radius: voice.state == .listening ? 22 : 10)
                    Image(systemName: voice.state == .listening ? "mic.fill" : "mic")
                        .font(.system(size: 38, weight: .semibold))
                        .foregroundStyle(.white)
                }
            }
            .buttonStyle(.plain)
            .disabled(!voice.sttAvailable)
            .help(voice.sttAvailable ? "Click to talk · click again to send" : "Voice input needs the packaged app")

            Text(stateLabel)
                .font(.headline)
                .foregroundStyle(stateColor)

            if voice.state == .listening {
                Text(voice.partialTranscript.isEmpty ? "Listening…" : voice.partialTranscript)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 460)
                    .lineLimit(3)
                    .transition(.opacity)
            }

            if voice.state == .speaking {
                Button { voice.stop() } label: { Label("Stop speaking", systemImage: "stop.fill") }
                    .controlSize(.small)
            }

            if appState.selectedProject == nil {
                Label("Select a project (under Projects) — voice commands run there.",
                      systemImage: "folder.badge.questionmark")
                    .font(.caption).foregroundStyle(.secondary)
            } else if let project = appState.selectedProject {
                Label("Commands run in \(project.name)", systemImage: "folder")
                    .font(.caption).foregroundStyle(.secondary)
            }

            if voice.authorizationDenied {
                Label("Microphone / speech permission was denied. Enable it in System Settings → Privacy.",
                      systemImage: "exclamationmark.triangle")
                    .font(.caption).foregroundStyle(.orange)
            } else if !voice.sttAvailable {
                Label("Speech-to-text is available in the packaged app (mic + speech permission). Reading aloud works everywhere.",
                      systemImage: "info.circle")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity)
        .animation(.easeInOut(duration: 0.2), value: voice.state)
    }

    private var stateColor: Color {
        switch voice.state {
        case .idle: return .secondary
        case .listening: return .green
        case .thinking: return .orange
        case .speaking: return .blue
        }
    }
    private var stateLabel: String {
        switch voice.state {
        case .idle: return "Ready"
        case .listening: return "Listening"
        case .thinking: return "Thinking"
        case .speaking: return "Speaking"
        }
    }

    // MARK: Conversation log

    private var conversation: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 14) {
                    if voice.conversation.isEmpty {
                        ContentUnavailableView(
                            "No conversation yet",
                            systemImage: "text.bubble",
                            description: Text("Press the mic and say something like “what changed in this project today?”")
                        )
                        .padding(.top, 30)
                    }
                    ForEach(voice.conversation) { turn in
                        turnView(turn).id(turn.id)
                    }
                }
                .padding(20)
            }
            .onChange(of: voice.conversation.count) { _, _ in
                if let last = voice.conversation.last {
                    withAnimation { proxy.scrollTo(last.id, anchor: .bottom) }
                }
            }
        }
    }

    private func turnView(_ turn: VoiceController.Turn) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            bubble(role: "You", icon: "person.fill", tint: .blue, text: turn.user)
            if turn.assistant.isEmpty {
                Label("…", systemImage: "ellipsis").font(.callout).foregroundStyle(.secondary)
            } else {
                bubble(role: "Claude", icon: "sparkle", tint: .brandViolet, text: turn.assistant)
            }
        }
    }

    private func bubble(role: String, icon: String, tint: Color, text: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: icon)
                .font(.caption2.weight(.bold)).foregroundStyle(.white)
                .frame(width: 24, height: 24)
                .background(tint.gradient, in: RoundedRectangle(cornerRadius: 7, style: .continuous))
            VStack(alignment: .leading, spacing: 2) {
                Text(role).font(.caption2.weight(.semibold)).foregroundStyle(.secondary)
                Text(text).font(.callout).textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }
}
