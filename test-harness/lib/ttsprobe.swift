// Offline-TTS-Beweis (F232): synthetisiert Text über AVSpeechSynthesizer.write — DIESELBE
// Engine, die VoiceController.speak() nutzt — komplett offline (kein Netzwerk, kein Mikrofon,
// keine Audioausgabe). Rendert die PCM-Buffer in eine Datei und meldet Frames + Spitzenpegel.
// Args: <text> <out.caf>. Gibt eine JSON-Zeile auf stdout aus.
import AVFoundation
import Foundation

let text = CommandLine.arguments.count > 1
    ? CommandLine.arguments[1] : "Hallo, hier spricht ClaudeStudio offline."
let outPath = CommandLine.arguments.count > 2
    ? CommandLine.arguments[2] : "tts_out.caf"

let synth = AVSpeechSynthesizer()
let utt = AVSpeechUtterance(string: text)
utt.rate = AVSpeechUtteranceDefaultSpeechRate
// Beste verfügbare Stimme für die Textsprache (wie bestVoice() in VoiceController).
let lang = AVSpeechSynthesisVoice.currentLanguageCode()
if let v = AVSpeechSynthesisVoice.speechVoices().first(where: { $0.language.hasPrefix("de") })
            ?? AVSpeechSynthesisVoice(language: lang) {
    utt.voice = v
}

var totalFrames: Int64 = 0
var peak: Float = 0
var audioFile: AVAudioFile?
var done = false

synth.write(utt) { (buffer: AVAudioBuffer) in
    guard let pcm = buffer as? AVAudioPCMBuffer else { return }
    if pcm.frameLength == 0 {
        done = true
        CFRunLoopStop(CFRunLoopGetMain())
        return
    }
    if audioFile == nil {
        audioFile = try? AVAudioFile(forWriting: URL(fileURLWithPath: outPath),
                                     settings: pcm.format.settings)
    }
    try? audioFile?.write(from: pcm)
    totalFrames += Int64(pcm.frameLength)
    if let ch = pcm.floatChannelData {
        let n = Int(pcm.frameLength)
        for i in 0..<n { peak = max(peak, abs(ch[0][i])) }
    } else if let ch16 = pcm.int16ChannelData {
        let n = Int(pcm.frameLength)
        for i in 0..<n { peak = max(peak, abs(Float(ch16[0][i]) / 32768.0)) }
    }
}

// Auf die Synthese-Callbacks warten (max. 10 s).
let deadline = Date().addingTimeInterval(10)
while !done && Date() < deadline {
    RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.2))
}

let attrs = try? FileManager.default.attributesOfItem(atPath: outPath)
let bytes = (attrs?[.size] as? Int) ?? 0
print("{\"frames\": \(totalFrames), \"peak\": \(peak), \"bytes\": \(bytes), \"done\": \(done)}")
