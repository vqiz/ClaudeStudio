import AVFoundation
import NaturalLanguage
import Observation
import os
import Speech

private let voiceLog = Logger(subsystem: "dev.claudestudio.voice", category: "capture")

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
    /// Live microphone input level (0…1), measured straight off the audio tap —
    /// independent of speech recognition. Drives the level meter so you can see
    /// whether any audio is actually arriving.
    private(set) var inputLevel: Float = 0
    /// Number of audio buffers the tap has received since the engine started.
    /// If this stays 0 while listening, the engine isn't capturing at all; if it
    /// climbs but the level is ~0, audio is arriving but silent (wrong input /
    /// muted / silent grant). A visible diagnostic.
    private(set) var bufferCount: Int = 0
    /// Highest input level seen this session (for the diagnostic readout).
    private(set) var peakLevel: Float = 0

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
        guard sttAvailable, !conversationActive else {
            voiceLog.info("startConversation skipped (stt=\(self.sttAvailable, privacy: .public) active=\(self.conversationActive, privacy: .public))")
            return
        }
        voiceLog.info("startConversation: requesting authorization")
        Task {
            let authorized = await ensureAuthorized()
            voiceLog.info("authorization granted=\(authorized, privacy: .public)")
            guard authorized else {
                authorizationDenied = true
                return
            }
            authorizationDenied = false
            do {
                try startEngine()
                conversationActive = true
                openWindow(listening: true)
                voiceLog.info("conversation active, listening")
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

        // Prefer the node's OUTPUT format for the tap (the Apple-standard for an
        // input-node tap); fall back to the hardware input format. A 0
        // sample-rate / 0-channel format means the mic isn't ready, and calling
        // installTap with it throws an uncatchable Obj-C exception, so validate.
        var format = input.outputFormat(forBus: 0)
        if format.sampleRate <= 0 || format.channelCount == 0 {
            format = input.inputFormat(forBus: 0)
        }
        voiceLog.info("startEngine format=\(format.sampleRate, privacy: .public)Hz ch=\(format.channelCount, privacy: .public)")
        guard format.sampleRate > 0, format.channelCount > 0 else {
            voiceLog.error("startEngine: invalid input format — mic unavailable")
            throw VoiceError.microphoneUnavailable
        }
        bufferCount = 0
        peakLevel = 0
        inputLevel = 0
        // Feed the recognizer AND report the live input level for the meter.
        installMicTap(on: input, format: format, forwardingTo: requestBox) { [weak self] level in
            Task { @MainActor in self?.updateLevel(level) }
        }
        audioEngine.prepare()
        do {
            try audioEngine.start()
        } catch {
            voiceLog.error("audioEngine.start failed: \(error.localizedDescription, privacy: .public)")
            throw error
        }
        engineRunning = true
        voiceLog.info("audioEngine started OK")
    }

    private func teardownEngine() {
        if audioEngine.isRunning {
            audioEngine.stop()
            audioEngine.inputNode.removeTap(onBus: 0)
        }
        engineRunning = false
        inputLevel = 0
    }

    /// Smooth the raw RMS level for a readable meter: snap up on attack, ease
    /// down on release. Also tracks the diagnostic buffer count + peak.
    private func updateLevel(_ raw: Float) {
        bufferCount += 1
        if raw > peakLevel { peakLevel = raw }
        inputLevel = raw > inputLevel ? raw : (inputLevel * 0.82 + raw * 0.18)
        if bufferCount == 1 || bufferCount % 50 == 0 {
            voiceLog.info("tap buffers=\(self.bufferCount, privacy: .public) level=\(Int(raw * 100), privacy: .public)% peak=\(Int(self.peakLevel * 100), privacy: .public)%")
        }
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
        // Read clean, spoken text (no markdown) in the most natural voice we can.
        let spoken = Self.cleanForSpeech(text)
        guard !spoken.isEmpty else { return }
        currentSpoken = spoken
        let utterance = AVSpeechUtterance(string: spoken)
        utterance.voice = bestVoice(for: spoken)
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate
        utterance.prefersAssistiveTechnologySettings = false
        synthesizer.speak(utterance)
        spokenLog.insert(spoken, at: 0)
        if spokenLog.count > 50 { spokenLog.removeLast() }
    }

    @ObservationIgnored private var voiceByLanguage: [String: AVSpeechSynthesisVoice] = [:]

    /// Pick the most natural available voice for the text's language: prefer
    /// premium, then enhanced, over the default (compact, robotic) voice. Cached.
    private func bestVoice(for text: String) -> AVSpeechSynthesisVoice? {
        let lang = Self.languageCode(of: text)
        if let cached = voiceByLanguage[lang] { return cached }
        let rank: (AVSpeechSynthesisVoiceQuality) -> Int = { q in
            switch q {
            case .premium: return 3
            case .enhanced: return 2
            default: return 1
            }
        }
        let candidates = AVSpeechSynthesisVoice.speechVoices().filter { $0.language.hasPrefix(lang) }
        let chosen = candidates.max { rank($0.quality) < rank($1.quality) }
            ?? AVSpeechSynthesisVoice(language: lang)
        if let chosen { voiceByLanguage[lang] = chosen }
        return chosen
    }

    /// Two-letter language code for `text` (e.g. "en", "de"), via on-device
    /// language detection, falling back to the system's preferred language.
    private static func languageCode(of text: String) -> String {
        let recognizer = NLLanguageRecognizer()
        recognizer.processString(text)
        if let lang = recognizer.dominantLanguage?.rawValue, !lang.isEmpty {
            return String(lang.prefix(2))
        }
        return String((Locale.preferredLanguages.first ?? "en").prefix(2))
    }

    /// Strip markdown so the synthesizer reads natural prose instead of
    /// "asterisk asterisk", backticks, hashes, bullets, and link URLs.
    private static func cleanForSpeech(_ text: String) -> String {
        var s = text
        s = s.replacingOccurrences(of: "```", with: " ")
        for token in ["**", "__", "`", "#", "~~"] {
            s = s.replacingOccurrences(of: token, with: "")
        }
        // Markdown links [label](url) → label
        s = s.replacingOccurrences(of: #"\[([^\]]+)\]\([^)]+\)"#, with: "$1", options: .regularExpression)
        // Line-leading bullets / blockquotes / list numbers
        s = s.replacingOccurrences(of: #"(?m)^\s*([-*>•]|\d+\.)\s+"#, with: "", options: .regularExpression)
        // Stray emphasis asterisks/underscores around words
        s = s.replacingOccurrences(of: #"[*_]([^*_\n]+)[*_]"#, with: "$1", options: .regularExpression)
        // Collapse runs of whitespace
        s = s.replacingOccurrences(of: #"[ \t]{2,}"#, with: " ", options: .regularExpression)
        return s.trimmingCharacters(in: .whitespacesAndNewlines)
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
                           forwardingTo box: RecognitionRequestBox,
                           onLevel: @escaping @Sendable (Float) -> Void) {
    input.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
        box.append(buffer)
        onLevel(micLevel(of: buffer))
    }
}

/// RMS amplitude of a buffer, scaled to roughly 0…1 for display (speech RMS is
/// small). Cheap enough to run on the realtime audio thread.
private func micLevel(of buffer: AVAudioPCMBuffer) -> Float {
    guard let channel = buffer.floatChannelData else { return 0 }
    let n = Int(buffer.frameLength)
    guard n > 0 else { return 0 }
    let samples = channel[0]
    var sum: Float = 0
    var i = 0
    while i < n {
        let s = samples[i]
        sum += s * s
        i += 1
    }
    let rms = (sum / Float(n)).squareRoot()
    return min(1, rms * 12)
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
