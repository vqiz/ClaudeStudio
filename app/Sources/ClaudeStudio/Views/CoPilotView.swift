import SwiftUI

/// Co-Pilot (F027): der inline KI-Assistent. Der Nutzer beschreibt eine Aufgabe; der Co-Pilot
/// schlägt konkrete Aktionen vor und führt sie über den Core-`copilot.run_action`-Flow aus
/// (z. B. fehlschlagende Tests reparieren, einen Security-Scan fahren). Einer der acht
/// Haupteinträge der Sidebar.
struct CoPilotView: View {
    @Environment(AppState.self) private var appState
    @State private var request = ""

    private let quickActions: [(title: String, symbol: String, action: String)] = [
        ("Tests reparieren", "checkmark.seal", "fix_tests"),
        ("Security-Scan", "lock.shield", "fix_findings"),
        ("Refactoring vorschlagen", "wand.and.rays", "refactor"),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            PageHeader(title: "Co-Pilot", symbol: "wand.and.stars",
                       subtitle: "Inline-KI-Assistent — beschreibe eine Aufgabe, der Co-Pilot handelt")

            GroupBox {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Was soll der Co-Pilot tun?")
                        .font(.headline)
                    TextField("z. B. „Behebe die fehlschlagenden Tests im todo-api“", text: $request)
                        .textFieldStyle(.roundedBorder)
                    HStack(spacing: 10) {
                        ForEach(quickActions, id: \.action) { qa in
                            Button {
                                request = qa.title
                            } label: {
                                Label(qa.title, systemImage: qa.symbol)
                            }
                            .buttonStyle(.bordered)
                        }
                    }
                }
                .padding(6)
            }

            GroupBox("Letzte Co-Pilot-Aktionen") {
                VStack(alignment: .leading, spacing: 8) {
                    Label("fix_tests · todo-api — 4 Tests grün", systemImage: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                    Label("fix_findings · data-pipeline — 1 Finding behoben", systemImage: "lock.shield.fill")
                        .foregroundStyle(.blue)
                }
                .font(.callout)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(6)
            }

            Spacer()
        }
        .padding(20)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }
}
