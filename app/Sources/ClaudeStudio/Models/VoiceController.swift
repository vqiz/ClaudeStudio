import AVFoundation
import Observation
import Speech

/// Thread-safe holder for the active recognition request, so the (non-isolated)
/// realtime audio tap can forward buffers to whichever request is current
/// without ever touching `@MainActor` state.
final class RecognitionRequestBox: @unchecked Sendable {
    private let lock = NSLock()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    func set(_ r: SFSpeechAudioBufferRecognitionRequest?) {
        lock.lock(); request = r; lock.unlock()
    }
    func append(_ buffer: AVAudioPCMBuffer) {
        lock.lock(); let r = request; lock.unlock()
        r?.append(buffer)
    }
}

/// The ClaudeStudio voice assistant — a **hands-free conversation**, not a
/// push-to-talk transcriber.
///
/// You start a conversation once (the mic button). Then it runs on its own:
///
/// - **Speak naturally.** It detects when you stop talking (a short silence) and
///   submits your turn automatically — no second click.
/// - **Claude works, then talks back** (system TTS), and it **listens again**
///   for your next turn. Back and forth, hands-free.
/// - **Interrupt any time.** If you start speaking while Claude is thinking or
///   talking, it cuts Claude off (stops the speech, cancels the run) and starts
///   your new turn — a barge-in.
///
/// All on-device + CLI-only: Apple's `SFSpeechRecognizer` (on-device) for input,
/// `AVSpeechSynthesizer` for output, and the recognized text is run through the
/// `claude` CLI like any typed prompt.
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
    /// True while a hands-free conversation is active (the mic button toggles it).
    private(set) var conversationActive = false

    /// Read assistant responses from any live (typed) session aloud as they
    /// complete — independent of a voice conversation.
    var readAloud = false
    /// The most recently spoken lines (newest first).
    private(set) var spokenLog: [String] = []
    /// True once the user denied microphone / speech permission.
    private(set) var authorizationDenied = false

    var isSpeaking: Bool { synthesizer.isSpeaking }
    var isListening: Bool { state == .listening }

    /// Whether speech *input* is possible here: the packaged app declares the
    /// usage strings in Info.plist; a bare `swift run` does not.
    var sttAvailable: Bool {
        Bundle.main.object(forInfoDictionaryKey: "NSSpeechRecognitionUsageDescription") != nil
            && Bundle.main.object(forInfoDictionaryKey: "NSMicrophoneUsageDescription") != nil
    }

    /// How long a pause (no new words) ends your turn and submits it.
    private let endpointSilence: UInt64 = 1_300_000_000  // 1.3s

    @ObservationIgnored private let synthesizer = AVSpeechSynthesizer()
    @ObservationIgnored private let audioEngine = AVAudioEngine()
    @ObservationIgnored private let recognizer = SFSpeechRecognizer()
    @ObservationIgnored private let requestBox = RecognitionRequestBox()
    @ObservationIgnored private var currentRequest: SFSpeechAudioBufferRecognitionRequest?
    @ObservationIgnored private var currentTask: SFSpeechRecognitionTask?
    @ObservationIgnored private var engineRunning = false
    /// Monotonic id so late callbacks from a replaced recognition task are ignored.
    @ObservationIgnored private var windowID = 0
    @ObservationIgnored private var silenceTask: Task<Void, Never>?
    /// The text currently being spoken (used to ignore TTS echo during barge-in).
    @ObservationIgnored private var currentSpoken = ""

    enum VoiceError: Error { case microphoneUnavailable }

    override init() {
        super.init()
        synthesizer.delegate = self
    }

    // MARK: - Conversation lifecycle (the mic button)

    /// Toggle the hands-free conversation on/off (the title-bar mic button).
    func toggleListening() {
        if conversationActive { endConversation() } else { startConversation() }
    }

    /// Begin a hands-free conversation: ask permission once, bring up the audio
    /// engine, and start listening for your first turn.
    func startConversation() {
        guard sttAvailable, !conversationActive else { return }
        Task {
            guard await ensureAuthorized() else {
                authorizationDenied = true
                return
            }
            authorizationDenied = false
            do {
                try startEngine()
                conversationActive = true
                openWindow(listening: true)
            } catch VoiceError.microphoneUnavailable {
                // HAL not ready right after a fresh grant — retry once.
                teardownEngine()
                try? await Task.sleep(nanoseconds: 400_000_000)
                do {
                    try startEngine()
                    conversationActive = true
                    openWindow(listening: true)
                } catch {
                    teardownEngine()
                    state = .idle
                }
            } catch {
                teardownEngine()
                state = .idle
            }
        }
    }

    /// End the conversation: stop listening and any speech, and reset to idle.
    func endConversation() {
        conversationActive = false
        silenceTask?.cancel()
        silenceTask = nil
        synthesizer.stopSpeaking(at: .immediate)
        currentSpoken = ""
        teardownWindow()
        teardownEngine()
        partialTranscript = ""
        state = .idle
    }

    // MARK: - Audio engine (runs continuously for the whole conversation)

    private func startEngine() throws {
        if engineRunning { return }
        audioEngine.stop()
        let input = audioEngine.inputNode
        input.removeTap(onBus: 0)
        // Voice processing gives acoustic echo cancellation + noise suppression
        // so the mic is less likely to hear Claude's own speech. Best-effort.
        try? input.setVoiceProcessingEnabled(true)

        // A 0 sample-rate / 0-channel format means the mic isn't ready; calling
        // installTap with it throws an uncatchable Obj-C exception, so validate.
        let format = input.inputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else {
            throw VoiceError.microphoneUnavailable
        }
        installMicTap(on: input, format: format, forwardingTo: requestBox)
        audioEngine.prepare()
        try audioEngine.start()
        engineRunning = true
    }

    private func teardownEngine() {
        if audioEngine.isRunning {
            audioEngine.stop()
            audioEngine.inputNode.removeTap(onBus: 0)
        }
        try? audioEngine.inputNode.setVoiceProcessingEnabled(false)
        engineRunning = false
    }

    // MARK: - Recognition windows (one per turn; the engine stays up)

    private func openWindow(listening: Bool) {
        teardownWindow()
        windowID += 1
        let id = windowID
        let req = SFSpeechAudioBufferRecognitionRequest()
        req.shouldReportPartialResults = true
        if recognizer?.supportsOnDeviceRecognition == true {
            req.requiresOnDeviceRecognition = true
        }
        currentRequest = req
        requestBox.set(req)
        if listening {
            partialTranscript = ""
            state = .listening
        }
        currentTask = startRecognition(
            recognizer, request: req,
            onPartial: { [weak self] text in
                Task { @MainActor in self?.handlePartial(text, window: id) }
            },
            onFinish: { [weak self] in
                Task { @MainActor in self?.handleFinish(window: id) }
            })
    }

    private func teardownWindow() {
        currentRequest?.endAudio()
        currentTask?.cancel()
        currentRequest = nil
        currentTask = nil
        requestBox.set(nil)
    }

    private func handlePartial(_ text: String, window: Int) {
        guard window == windowID, conversationActive else { return }
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        switch state {
        case .listening:
            partialTranscript = text
            if !trimmed.isEmpty { scheduleEndpoint() }
        case .thinking:
            // No speech is playing, so any words from you = an interruption.
            if !trimmed.isEmpty { bargeIn(with: text) }
        case .speaking:
            // Claude is talking; ignore what is just an echo of its own words.
            if !trimmed.isEmpty && !isLikelyEcho(trimmed) { bargeIn(with: text) }
        case .idle:
            break
        }
    }

    private func handleFinish(window: Int) {
        guard window == windowID, conversationActive else { return }
        // The recognition task ended (its time limit, or a final result). Keep
        // the conversation alive: submit a pending utterance, else re-open.
        if state == .listening,
           !partialTranscript.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            finalizeTurn()
        } else {
            openWindow(listening: state == .listening)
        }
    }

    // MARK: - Endpointing (auto-submit on a pause)

    private func scheduleEndpoint() {
        silenceTask?.cancel()
        let pause = endpointSilence
        silenceTask = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: pause)
            guard let self, !Task.isCancelled else { return }
            if self.state == .listening { self.finalizeTurn() }
        }
    }

    private func finalizeTurn() {
        silenceTask?.cancel()
        silenceTask = nil
        let text = partialTranscript.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        partialTranscript = ""
        state = .thinking
        // Fresh window so we can hear a barge-in while Claude works.
        openWindow(listening: false)
        pendingCommand = text  // the orchestrator runs it through the CLI
    }

    private func bargeIn(with text: String) {
        // You started talking over Claude — cut it off and make this a new turn.
        synthesizer.stopSpeaking(at: .immediate)
        currentSpoken = ""
        state = .listening
        partialTranscript = text
        scheduleEndpoint()
        // The orchestrator observes state→listening and cancels the running run.
    }

    private func isLikelyEcho(_ heard: String) -> Bool {
        guard !currentSpoken.isEmpty else { return false }
        let spoken = currentSpoken.lowercased()
        let h = heard.lowercased()
        // If we only heard the start of what Claude is currently saying, it's echo.
        return spoken.hasPrefix(h) || h.hasPrefix(String(spoken.prefix(max(8, h.count))))
    }

    // MARK: - Orchestration hooks (called by the app)

    /// Mark a recognized command as running; records the user turn.
    func beginThinking(_ userText: String) {
        pendingCommand = nil
        conversation.append(Turn(user: userText, assistant: ""))
        // Don't clobber a barge-in that already moved us back to listening.
        if state != .listening { state = .thinking }
    }

    /// Deliver the assistant's reply for the in-flight voice turn: fill in the
    /// last turn and read it aloud (then the loop listens again).
    func deliver(_ assistant: String) {
        let text = assistant.trimmingCharacters(in: .whitespacesAndNewlines)
        if let i = conversation.indices.last {
            conversation[i].assistant = text
        }
        if text.isEmpty {
            advanceToNextTurnOrIdle()
        } else {
            speak(text)
        }
    }

    private func advanceToNextTurnOrIdle() {
        if conversationActive {
            openWindow(listening: true)
        } else {
            state = .idle
        }
    }

    // MARK: - Speaking (TTS)

    func speak(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        currentSpoken = trimmed
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

    private func handleSpeechEnded(natural: Bool) {
        currentSpoken = ""
        // Only advance on a *natural* finish; a cancel is a barge-in/stop that
        // already set the next state.
        if natural, state == .speaking {
            advanceToNextTurnOrIdle()
        } else if !conversationActive, state == .speaking {
            state = .idle
        }
    }

    private func ensureAuthorized() async -> Bool {
        // `nonisolated` so the TCC callbacks (delivered on a background queue)
        // are not main-actor-isolated, which would trap on macOS 26.
        await Self.requestAuthorization()
    }

    /// `nonisolated` on purpose: the speech / mic authorization callbacks fire on
    /// a background TCC queue; an actor-isolated callback would trap there.
    private nonisolated static func requestAuthorization() async -> Bool {
        let speechOK: Bool = await withCheckedContinuation { (cont: CheckedContinuation<Bool, Never>) in
            switch SFSpeechRecognizer.authorizationStatus() {
            case .authorized: cont.resume(returning: true)
            case .notDetermined:
                SFSpeechRecognizer.requestAuthorization { status in
                    cont.resume(returning: status == .authorized)
                }
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
}

// MARK: - TTS delegate (state transitions)

extension VoiceController: AVSpeechSynthesizerDelegate {
    // Hop to the main actor via a Task rather than `MainActor.assumeIsolated`,
    // which would trap if the delegate is delivered off the main thread.
    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer,
                                       didStart utterance: AVSpeechUtterance) {
        Task { @MainActor in if self.state != .listening { self.state = .speaking } }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer,
                                       didFinish utterance: AVSpeechUtterance) {
        Task { @MainActor in self.handleSpeechEnded(natural: true) }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer,
                                       didCancel utterance: AVSpeechUtterance) {
        Task { @MainActor in self.handleSpeechEnded(natural: false) }
    }
}

// MARK: - Non-isolated capture helpers
//
// File-scope free functions (no actor isolation), so the closures they install
// are NOT @MainActor-isolated — essential, because the audio tap fires on a
// realtime audio thread and the recognition callback on an arbitrary queue, and
// an isolated closure would trap (dispatch_assert_queue) off the main actor.

private func installMicTap(on input: AVAudioInputNode, format: AVAudioFormat,
                           forwardingTo box: RecognitionRequestBox) {
    input.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
        box.append(buffer)
    }
}

private func startRecognition(_ recognizer: SFSpeechRecognizer?,
                              request: SFSpeechAudioBufferRecognitionRequest,
                              onPartial: @escaping @Sendable (String) -> Void,
                              onFinish: @escaping @Sendable () -> Void) -> SFSpeechRecognitionTask? {
    recognizer?.recognitionTask(with: request) { result, error in
        if let result { onPartial(result.bestTranscription.formattedString) }
        if error != nil || (result?.isFinal ?? false) { onFinish() }
    }
}
