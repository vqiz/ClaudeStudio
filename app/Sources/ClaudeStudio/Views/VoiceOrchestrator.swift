import SwiftUI

/// The always-on glue between the voice engine and the core. Mounted (zero-size)
/// in `RootView` so it observes throughout the app's life regardless of which
/// tab is visible:
///
/// - a finalized spoken command (`voice.pendingCommand`) is run through the
///   `claude` CLI in the selected project, exactly like a typed prompt;
/// - when that run finishes, the assistant's reply is read back aloud;
/// - independently, when "read responses aloud" is on, any finished session's
///   reply is spoken.
///
/// Kept out of `CoreConnection` on purpose — it only uses the connection's
/// public, observable surface.
struct VoiceOrchestrator: View {
    @Environment(AppState.self) private var appState

    var body: some View {
        Color.clear
            .frame(width: 0, height: 0)
            .accessibilityHidden(true)
            // A finalized spoken turn → run it through the CLI in the project.
            .onChange(of: appState.voice.pendingCommand) { _, command in
                guard let command, !command.isEmpty, appState.coreConnected else { return }
                appState.voice.beginThinking(command)
                let project = appState.selectedProject
                Task {
                    await appState.core.startSession(
                        prompt: command, cwd: project?.path,
                        model: project?.model, systemPrompt: Self.voiceSystemPrompt,
                        effort: project?.effort, origin: "voice")
                }
            }
            // Barge-in: you started talking while Claude was thinking or speaking,
            // so cancel the running session — the new turn takes over.
            .onChange(of: appState.voice.state) { old, new in
                if new == .listening, old == .thinking || old == .speaking,
                   appState.core.runningSessionId != nil {
                    Task { await appState.core.stopSession() }
                }
            }
            // A run finished. If it was the in-flight voice turn (still thinking),
            // speak the reply and let the conversation loop continue.
            .onChange(of: appState.core.runningSessionId) { old, new in
                guard old != nil, new == nil else { return }
                let reply = lastAssistantText
                if appState.voice.state == .thinking {
                    appState.voice.deliver(reply ?? "Done.")
                } else if appState.voice.readAloud, appState.voice.state == .idle, let reply {
                    appState.voice.speak(reply)
                }
            }
    }

    /// The final assistant line of the run that just completed.
    private var lastAssistantText: String? {
        appState.core.liveSession.last { $0.kind == "assistant_text" }?.text
    }

    /// Appended to voice runs so spoken replies are short and natural — the
    /// answer is read aloud, so plain, concise prose works far better than a
    /// long, marked-up response.
    static let voiceSystemPrompt = """
    This is a spoken voice conversation: your reply is read aloud by text-to-speech. \
    Answer in 1–2 short, natural sentences of plain spoken language. Be direct and get to \
    the point. Do NOT use markdown, code blocks, bullet lists, headers, tables, or emoji. \
    If a complete answer would be long, give the key result concisely and offer to go deeper.
    """
}
