import AVFoundation
import Observation
import Speech

/// The ClaudeStudio voice assistant engine.
///
/// Speech *in*: Apple's on-device `SFSpeechRecognizer` fed by `AVAudioEngine`
/// mic taps — no cloud STT, no API keys. Speech *out*: `AVSpeechSynthesizer`
/// (system TTS). A recognized command is handed to the app (via
/// ``pendingCommand``), run through the `claude` CLI like any typed prompt, and
/// the reply is read back aloud. The whole loop stays on-device + CLI-only.
///
/// State machine drives the UI colour: idle (grey) → listening (green) →
/// thinking (orange) → speaking (blue).
@Observable
@MainActor
final class VoiceController: NSObject {
    enum VoiceState: Sendable { case idle, listening, thinking, speaking }

    /// One spoken exchange, shown in the assistant log.
    struct Turn: Identifiable, Sendable {
        let id = UUID()
        let user: String
        var assistant: String
        let at = Date()
    }

    private(set) var state: VoiceState = .idle
    /// Live partial transcription while listening.
    private(set) var partialTranscript = ""
    /// The conversation log (oldest first).
    private(set) var conversation: [Turn] = []
    /// A finalized spoken command waiting to be run. The orchestrator consumes
    /// it (``beginThinking(_:)``) and clears it.
    var pendingCommand: String?

    /// Read assistant responses from any live session aloud as they complete.
    var readAloud = false
    /// The most recently spoken lines (newest first).
    private(set) var spokenLog: [String] = []
    /// True once the user denied microphone / speech permission.
    private(set) var authorizationDenied = false

    var isSpeaking: Bool { synthesizer.isSpeaking }
    var isListening: Bool { state == .listening }

    /// Whether speech *input* is possible here: the packaged app / Xcode build
    /// declares the usage strings in Info.plist; a bare `swift run` does not.
    var sttAvailable: Bool {
        Bundle.main.object(forInfoDictionaryKey: "NSSpeechRecognitionUsageDescription") != nil
            && Bundle.main.object(forInfoDictionaryKey: "NSMicrophoneUsageDescription") != nil
    }

    @ObservationIgnored private let synthesizer = AVSpeechSynthesizer()
    @ObservationIgnored private let audioEngine = AVAudioEngine()
    @ObservationIgnored private let recognizer = SFSpeechRecognizer()
    @ObservationIgnored private var request: SFSpeechAudioBufferRecognitionRequest?
    @ObservationIgnored private var task: SFSpeechRecognitionTask?

    override init() {
        super.init()
        synthesizer.delegate = self
    }

    // MARK: Listening (STT)

    /// Begin listening. Barges in over any current TTS. Asks for permission on
    /// first use; on denial sets ``authorizationDenied`` and stays idle.
    func startListening() {
        guard state != .listening, sttAvailable else { return }
        stop() // barge-in: cut off any speaking immediately
        Task {
            guard await ensureAuthorized() else {
                authorizationDenied = true
                return
            }
            authorizationDenied = false
            do {
                try beginCapture()
                partialTranscript = ""
                state = .listening
            } catch VoiceError.microphoneUnavailable {
                // The audio HAL is often not ready in the same instant the user
                // grants permission. Tear down and retry once after a beat
                // instead of failing the first click.
                teardownCapture()
                try? await Task.sleep(nanoseconds: 400_000_000)
                do {
                    try beginCapture()
                    partialTranscript = ""
                    state = .listening
                } catch {
                    teardownCapture()
                    state = .idle
                }
            } catch {
                teardownCapture()
                state = .idle
            }
        }
    }

    /// Stop listening and finalize the command. A non-empty transcript becomes a
    /// ``pendingCommand`` for the orchestrator to run.
    func stopListening() {
        guard state == .listening else { return }
        teardownCapture()
        let text = partialTranscript.trimmingCharacters(in: .whitespacesAndNewlines)
        partialTranscript = ""
        if text.isEmpty {
            state = .idle
        } else {
            pendingCommand = text
        }
    }

    /// Toggle listening (used by the mic button / push-to-talk).
    func toggleListening() {
        if state == .listening { stopListening() } else { startListening() }
    }

    /// Failure modes that beginCapture can surface without crashing.
    enum VoiceError: Error { case microphoneUnavailable }

    private func beginCapture() throws {
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        // Prefer fully on-device recognition when the system supports it.
        if recognizer?.supportsOnDeviceRecognition == true {
            request.requiresOnDeviceRecognition = true
        }
        self.request = request

        // Start from a clean graph so a stale tap / format can't linger.
        audioEngine.stop()
        let input = audioEngine.inputNode
        input.removeTap(onBus: 0)

        // Use the input node's *native* format. When the mic isn't actually
        // usable yet — no input device, or permission hasn't propagated to the
        // audio HAL right after the user granted it — that format has a 0
        // sample-rate / 0 channels. Calling `installTap` with such a format
        // throws an Objective-C exception that `try` CANNOT catch and HARD-
        // CRASHES the app (this was the voice crash). Validate first and fail
        // gracefully instead.
        let format = input.inputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else {
            throw VoiceError.microphoneUnavailable
        }
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { [weak request] buffer, _ in
            request?.append(buffer)
        }
        audioEngine.prepare()
        try audioEngine.start()

        task = recognizer?.recognitionTask(with: request) { [weak self] result, error in
            guard let self else { return }
            if let result {
                let text = result.bestTranscription.formattedString
                Task { @MainActor in self.partialTranscript = text }
            }
            if error != nil || (result?.isFinal ?? false) {
                Task { @MainActor in
                    if self.state == .listening { self.stopListening() }
                }
            }
        }
    }

    private func teardownCapture() {
        if audioEngine.isRunning {
            audioEngine.stop()
            audioEngine.inputNode.removeTap(onBus: 0)
        }
        request?.endAudio()
        task?.cancel()
        request = nil
        task = nil
    }

    private func ensureAuthorized() async -> Bool {
        let speechOK: Bool = await withCheckedContinuation { cont in
            switch SFSpeechRecognizer.authorizationStatus() {
            case .authorized: cont.resume(returning: true)
            case .notDetermined:
                SFSpeechRecognizer.requestAuthorization { cont.resume(returning: $0 == .authorized) }
            default: cont.resume(returning: false)
            }
        }
        guard speechOK else { return false }
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: return true
        case .notDetermined: return await AVCaptureDevice.requestAccess(for: .audio)
        default: return false
        }
    }

    // MARK: Orchestration hooks (called by the app)

    /// Mark a recognized command as running; records the user turn.
    func beginThinking(_ userText: String) {
        pendingCommand = nil
        conversation.append(Turn(user: userText, assistant: ""))
        state = .thinking
    }

    /// Deliver the assistant's reply for the in-flight voice command: fill in the
    /// last turn and read it aloud.
    func deliver(_ assistant: String) {
        let text = assistant.trimmingCharacters(in: .whitespacesAndNewlines)
        if let i = conversation.indices.last {
            conversation[i].assistant = text
        }
        if text.isEmpty {
            state = .idle
        } else {
            speak(text)
        }
    }

    // MARK: Speaking (TTS)

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

// MARK: - TTS delegate (state transitions)

extension VoiceController: AVSpeechSynthesizerDelegate {
    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer,
                                       didStart utterance: AVSpeechUtterance) {
        MainActor.assumeIsolated { if state != .listening { state = .speaking } }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer,
                                       didFinish utterance: AVSpeechUtterance) {
        MainActor.assumeIsolated { if state == .speaking { state = .idle } }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer,
                                       didCancel utterance: AVSpeechUtterance) {
        MainActor.assumeIsolated { if state == .speaking { state = .idle } }
    }
}
