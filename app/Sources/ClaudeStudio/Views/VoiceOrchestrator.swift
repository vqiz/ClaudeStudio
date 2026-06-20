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
    @State private var voiceRunActive = false

    var body: some View {
        Color.clear
            .frame(width: 0, height: 0)
            .accessibilityHidden(true)
            .onChange(of: appState.voice.pendingCommand) { _, command in
                guard let command, !command.isEmpty, appState.coreConnected else { return }
                appState.voice.beginThinking(command)
                voiceRunActive = true
                let project = appState.selectedProject
                Task {
                    await appState.core.startSession(
                        prompt: command, cwd: project?.path,
                        model: project?.model, effort: project?.effort, origin: "voice")
                }
            }
            .onChange(of: appState.core.runningSessionId) { old, new in
                guard old != nil, new == nil else { return } // a run just finished
                let reply = lastAssistantText
                if voiceRunActive {
                    voiceRunActive = false
                    appState.voice.deliver(reply ?? "Done.")
                } else if appState.voice.readAloud, let reply {
                    appState.voice.speak(reply)
                }
            }
    }

    /// The final assistant line of the run that just completed.
    private var lastAssistantText: String? {
        appState.core.liveSession.last { $0.kind == "assistant_text" }?.text
    }
}
