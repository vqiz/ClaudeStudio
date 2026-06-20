import SwiftUI

/// The Voice view — text-to-speech that reads assistant responses aloud, a
/// quick "speak this" tester, and the spoken log. (Voice *input* needs the
/// packaged app + microphone permission.)
struct VoiceLogView: View {
    @Environment(AppState.self) private var appState
    @State private var toSpeak = ""

    var body: some View {
        @Bindable var voice = appState.voice

        VStack(spacing: 0) {
            HStack {
                PageHeader(title: "Voice", symbol: "waveform",
                           subtitle: "Read responses aloud (text-to-speech)")
                Toggle(isOn: $voice.readAloud) {
                    Label("Read responses aloud", systemImage: "speaker.wave.2.fill")
                }
                .toggleStyle(.switch)
            }
            .padding(20)

            Form {
                Section("Try it") {
                    HStack(spacing: 8) {
                        TextField("Type something to speak…", text: $toSpeak)
                            .textFieldStyle(.roundedBorder)
                            .onSubmit(speak)
                        Button(action: speak) { Label("Speak", systemImage: "play.fill") }
                            .disabled(toSpeak.trimmingCharacters(in: .whitespaces).isEmpty)
                        Button(action: appState.voice.stop) { Label("Stop", systemImage: "stop.fill") }
                            .disabled(!appState.voice.isSpeaking)
                    }
                    if !appState.voice.sttAvailable {
                        Label("Voice input (speech-to-text) is available in the packaged app (microphone + speech permission). Reading aloud works everywhere.",
                              systemImage: "info.circle")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }

                Section("Spoken log") {
                    if appState.voice.spokenLog.isEmpty {
                        Text("Nothing spoken yet. Turn on “Read responses aloud”, then run a session — its replies are read out here.")
                            .font(.caption).foregroundStyle(.secondary)
                    } else {
                        ForEach(Array(appState.voice.spokenLog.enumerated()), id: \.offset) { _, line in
                            Label(line, systemImage: "text.bubble")
                                .font(.callout)
                                .lineLimit(3)
                        }
                    }
                }
            }
            .formStyle(.grouped)
        }
        // Read new assistant text from the live session aloud when enabled.
        .onChange(of: lastAssistantText) { _, newValue in
            if appState.voice.readAloud, let text = newValue { appState.voice.speak(text) }
        }
    }

    /// The most recent assistant line in the live session (drives read-aloud).
    private var lastAssistantText: String? {
        appState.core.liveSession.last { $0.kind == "assistant_text" }?.text
    }

    private func speak() {
        let text = toSpeak
        toSpeak = ""
        appState.voice.speak(text)
    }
}
