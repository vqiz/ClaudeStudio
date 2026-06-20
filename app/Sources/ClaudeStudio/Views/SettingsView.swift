import SwiftUI

/// Settings — appearance, default agent behaviour, and the core connection.
/// Only real, functional settings are shown; model and context budget are
/// persisted to the core via `config.set`.
struct SettingsView: View {
    @Environment(AppState.self) private var appState

    @State private var socketPath = IpcSocketDefaults.path
    @State private var contextBudgetText = ""
    @State private var loadingDefaults = false
    @State private var loadResult: String?

    private static let models: [(id: String, label: String)] = [
        ("haiku", "Haiku — fastest, lightest"),
        ("sonnet", "Sonnet — balanced (default)"),
        ("opus", "Opus — most capable"),
    ]

    var body: some View {
        @Bindable var appState = appState

        Form {
            Section("Appearance") {
                Picker("Theme", selection: $appState.theme) {
                    ForEach(AppTheme.allCases) { theme in
                        Label(theme.label, systemImage: theme.symbol).tag(theme)
                    }
                }
                .pickerStyle(.segmented)
                Text(appState.theme.blurb)
                    .font(.caption).foregroundStyle(.secondary)
            }

            Section("Default agent behaviour") {
                Picker("Trust mode", selection: $appState.globalTrustMode) {
                    ForEach(TrustMode.allCases) { mode in
                        Label(mode.label, systemImage: mode.symbol).tag(mode)
                    }
                }
                Text(appState.globalTrustMode.blurb)
                    .font(.caption).foregroundStyle(.secondary)

                if appState.core.config != nil {
                    Picker("Default model", selection: modelBinding) {
                        ForEach(Self.models, id: \.id) { Text($0.label).tag($0.id) }
                    }
                    Text("The reasoning tier for new sessions. You can override it per project.")
                        .font(.caption).foregroundStyle(.secondary)
                } else {
                    Text("Connect the core to edit the default model.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }

            if let config = appState.core.config {
                Section("Context window") {
                    LabeledContent("Token budget") {
                        TextField("tokens", text: $contextBudgetText)
                            .textFieldStyle(.roundedBorder)
                            .frame(maxWidth: 140)
                            .onSubmit(saveContextBudget)
                            .multilineTextAlignment(.trailing)
                    }
                    Text("How many tokens of CLAUDE.md, memory and retrieval the core assembles per prompt. Currently \(config.contextTokenBudget.formatted()).")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }

            Section("Templates") {
                Button {
                    Task { await loadDefaults() }
                } label: {
                    Label(loadingDefaults ? "Loading…" : "Load default templates",
                          systemImage: "square.and.arrow.down.on.square")
                }
                .disabled(loadingDefaults || !appState.coreConnected)
                if let loadResult {
                    Text(loadResult).font(.caption).foregroundStyle(.secondary)
                }
                Text("The Task, Definition and Agent libraries start empty. Load the shipped starter set (tasks, definitions, and agent templates) as editable copies into your library. Re-running only adds what's missing.")
                    .font(.caption).foregroundStyle(.secondary)
                if !appState.coreConnected {
                    Text("Connect the core to load task & definition templates.")
                        .font(.caption).foregroundStyle(.orange)
                }
            }

            Section("Rust core") {
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
                LabeledContent("Socket") {
                    TextField("Socket path", text: $socketPath)
                        .textFieldStyle(.roundedBorder)
                        .frame(maxWidth: 320)
                }
                Text("The app starts and stops the core automatically — no terminal needed. Sessions run through your `claude` CLI login (subscription), never the API.")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .navigationTitle("Settings")
        .onAppear(perform: syncFromConfig)
        .onChange(of: appState.core.config?.contextTokenBudget) { _, _ in syncFromConfig() }
    }

    // MARK: Bindings & actions

    private var modelBinding: Binding<String> {
        Binding(
            get: { appState.core.config?.defaultModel ?? "sonnet" },
            set: { newValue in Task { await appState.core.updateConfig(defaultModel: newValue) } }
        )
    }

    /// Load the shipped defaults: tasks + definitions (via the core) and the
    /// agent templates (locally), reporting what was added.
    private func loadDefaults() async {
        loadingDefaults = true
        defer { loadingDefaults = false }
        let lib = await appState.core.loadDefaultTemplates()
        let agentsAdded = appState.agentStore.loadDefaults()
        if let lib {
            loadResult = "Loaded \(lib.tasks) task(s), \(lib.definitions) definition(s), \(agentsAdded) agent(s)."
        } else {
            loadResult = "Loaded \(agentsAdded) agent(s). Connect the core to also load tasks & definitions."
        }
    }

    private func saveContextBudget() {
        guard let value = Int(contextBudgetText.filter(\.isNumber)), value > 0 else { return }
        Task { await appState.core.updateConfig(contextTokenBudget: value) }
    }

    private func syncFromConfig() {
        if let budget = appState.core.config?.contextTokenBudget {
            contextBudgetText = String(budget)
        }
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
