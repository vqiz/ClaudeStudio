import SwiftUI

/// Shared formatting helpers and small reusable view atoms.
enum Format {
    static func ago(_ date: Date) -> String {
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .abbreviated
        return formatter.localizedString(for: date, relativeTo: .now)
    }

    static func clock(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "HH:mm:ss"
        return formatter.string(from: date)
    }

    static func usd(_ value: Double) -> String {
        String(format: "$%.2f", value)
    }
}

extension BusEvent.Severity {
    var color: Color {
        switch self {
        case .info: return .secondary
        case .action: return .blue
        case .approval: return .orange
        case .warning: return .yellow
        case .success: return .green
        }
    }

    var symbol: String {
        switch self {
        case .info: return "info.circle"
        case .action: return "play.circle"
        case .approval: return "hand.raised"
        case .warning: return "exclamationmark.triangle"
        case .success: return "checkmark.circle"
        }
    }
}

extension AgentSession.Status {
    var color: Color {
        switch self {
        case .running: return .green
        case .awaitingApproval: return .orange
        case .paused: return .yellow
        case .completed: return .blue
        case .failed: return .red
        }
    }
}

extension GraphNode.Kind {
    var color: Color {
        switch self {
        case .concept: return .purple
        case .file: return .blue
        case .session: return .green
        case .skill: return .orange
        case .memory: return .pink
        }
    }
}

/// A reusable section header with an SF Symbol used across the detail views.
struct PageHeader: View {
    let title: String
    let symbol: String
    var subtitle: String?

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Image(systemName: symbol)
                .font(.title2)
                .foregroundStyle(.tint)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.title2.bold())
                if let subtitle {
                    Text(subtitle).font(.subheadline).foregroundStyle(.secondary)
                }
            }
            Spacer()
        }
    }
}
