import SwiftUI

/// The Voice Log — transcribed voice commands, their parsed intent, and whether
/// the assistant handled them.
struct VoiceLogView: View {
    @Environment(AppState.self) private var appState
    private let entries = VoiceLogEntry.samples

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                PageHeader(title: "Voice Log", symbol: "waveform",
                           subtitle: "Spoken commands and parsed intents")
                Toggle(isOn: Binding(get: { appState.isListening },
                                     set: { appState.isListening = $0 })) {
                    Label("Listening", systemImage: "mic.fill")
                }
                .toggleStyle(.switch)
            }
            .padding(20)

            List(entries) { entry in
                VoiceRow(entry: entry)
            }
            .listStyle(.inset)
        }
    }
}

private struct VoiceRow: View {
    let entry: VoiceLogEntry

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: entry.handled ? "checkmark.circle.fill" : "questionmark.circle")
                .foregroundStyle(entry.handled ? Color.green : Color.orange)
                .font(.title3)
            VStack(alignment: .leading, spacing: 4) {
                Text("\u{201C}\(entry.transcript)\u{201D}").font(.callout).italic()
                Text(entry.intent).font(.system(.caption, design: .monospaced)).foregroundStyle(.tint)
            }
            Spacer()
            Text(Format.ago(entry.timestamp)).font(.caption2).foregroundStyle(.tertiary)
        }
        .padding(.vertical, 5)
    }
}
