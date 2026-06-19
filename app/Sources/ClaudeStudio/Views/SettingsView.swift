import SwiftUI

/// Settings — global trust posture, core connection, memory, and voice options.
/// Rendered both in the sidebar detail and the macOS Settings scene.
struct SettingsView: View {
    @Environment(AppState.self) private var appState

    @State private var socketPath = IpcSocketDefaults.path
    @State private var qdrantURL = "http://127.0.0.1:6334"
    @State private var enableVoice = true
    @State private var autoConsolidateMemory = true

    var body: some View {
        @Bindable var appState = appState

        Form {
            Section("Trust") {
                Picker("Default trust mode", selection: $appState.globalTrustMode) {
                    ForEach(TrustMode.allCases) { mode in
                        Label(mode.label, systemImage: mode.symbol).tag(mode)
                    }
                }
                Text(appState.globalTrustMode.blurb)
                    .font(.caption).foregroundStyle(.secondary)
            }

            Section("Rust Core (IPC)") {
                LabeledContent("Socket") {
                    TextField("Socket path", text: $socketPath)
                        .textFieldStyle(.roundedBorder)
                        .frame(maxWidth: 320)
                }
                LabeledContent("Status") {
                    HStack(spacing: 6) {
                        Circle().fill(coreStatusColor).frame(width: 8, height: 8)
                        Text(coreStatusText).foregroundStyle(.secondary)
                    }
                }
                Button {
                    appState.core.socketPath = socketPath
                    Task { await appState.connectCore() }
                } label: {
                    Label(appState.core.isConnected ? "Reconnect" : "Connect",
                          systemImage: "bolt.horizontal.circle")
                }
                if let config = appState.core.config {
                    LabeledContent("Default model", value: config.defaultModel.capitalized)
                    LabeledContent("Daily budget",
                                   value: config.dailyBudgetUSD > 0 ? Format.usd(config.dailyBudgetUSD) : "No limit")
                    LabeledContent("Context budget", value: "\(config.contextTokenBudget.formatted()) tokens")
                }
                Text("The app speaks length-prefixed MessagePack over this Unix socket.")
                    .font(.caption).foregroundStyle(.secondary)
            }

            Section("Memory") {
                LabeledContent("Qdrant") {
                    TextField("Qdrant URL", text: $qdrantURL)
                        .textFieldStyle(.roundedBorder)
                        .frame(maxWidth: 320)
                }
                Toggle("Auto-consolidate memory after each session", isOn: $autoConsolidateMemory)
            }

            Section("Voice") {
                Toggle("Enable voice assistant", isOn: $enableVoice)
                Toggle("Listening now", isOn: $appState.isListening)
                    .disabled(!enableVoice)
            }
        }
        .formStyle(.grouped)
        .navigationTitle("Settings")
    }

    private var coreStatusText: String {
        switch appState.core.status {
        case .offline: return "Offline"
        case .connecting: return "Connecting…"
        case .online: return "Connected"
        case .failed(let reason): return reason
        }
    }

    private var coreStatusColor: Color {
        switch appState.core.status {
        case .online: return .green
        case .connecting: return .yellow
        case .offline: return .secondary
        case .failed: return .red
        }
    }
}

/// Indirection so the app target need not import ClaudeStudioKit just for the
/// default path string in this view.
enum IpcSocketDefaults {
    static let path: String = {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        return "\(home)/.claudestudio/core.sock"
    }()
}
