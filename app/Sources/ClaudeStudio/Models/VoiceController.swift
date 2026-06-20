import AVFoundation
import Observation

/// Text-to-speech for ClaudeStudio: reads assistant responses aloud and keeps a
/// short spoken log. Uses the system speech synthesizer (`AVSpeechSynthesizer`),
/// which needs no permission and works in both `swift run` and the packaged app.
///
/// Speech *input* (STT / wake-word) needs microphone + speech-recognition
/// permission and is only meaningful in the packaged app; see `sttAvailable`.
@Observable
@MainActor
final class VoiceController {
    /// Read assistant responses from live sessions aloud as they arrive.
    var readAloud = false

    /// The most recently spoken lines (newest first).
    private(set) var spokenLog: [String] = []

    /// Whether the synthesizer is currently speaking.
    var isSpeaking: Bool { synthesizer.isSpeaking }

    private let synthesizer = AVSpeechSynthesizer()

    /// Whether speech *input* is possible here (packaged app declares the usage
    /// strings in Info.plist; `swift run` does not).
    var sttAvailable: Bool {
        Bundle.main.object(forInfoDictionaryKey: "NSSpeechRecognitionUsageDescription") != nil
    }

    func speak(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        let utterance = AVSpeechUtterance(string: trimmed)
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate
        utterance.prefersAssistiveTechnologySettings = false
        synthesizer.speak(utterance)
        spokenLog.insert(trimmed, at: 0)
        if spokenLog.count > 50 { spokenLog.removeLast() }
    }

    func stop() {
        synthesizer.stopSpeaking(at: .immediate)
    }
}
