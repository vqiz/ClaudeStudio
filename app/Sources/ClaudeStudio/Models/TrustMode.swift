import SwiftUI

/// Permission posture for a session or the global app.
///
/// This is the UI-facing model. It maps one-to-one onto the Rust core's
/// `cs_types::TrustMode` (`strict` / `standard` / `auto` / `yolo`), but the two
/// use different spellings, so cross the IPC boundary via ``coreValue`` and
/// ``init(coreValue:)`` rather than the raw value.
enum TrustMode: String, CaseIterable, Identifiable, Codable, Sendable {
    /// Every tool call requires explicit human approval.
    case readOnly = "read_only"
    /// Edits and safe commands auto-approved; destructive actions still prompt.
    case guarded
    /// Most actions auto-approved within the configured allow-list.
    case autonomous
    /// Full autonomy — Claude acts without prompts (the "yolo" posture).
    case unleashed

    var id: String { rawValue }

    var label: String {
        switch self {
        case .readOnly: return "Read-Only"
        case .guarded: return "Guarded"
        case .autonomous: return "Autonomous"
        case .unleashed: return "Unleashed"
        }
    }

    var symbol: String {
        switch self {
        case .readOnly: return "eye"
        case .guarded: return "shield.lefthalf.filled"
        case .autonomous: return "bolt.badge.automatic"
        case .unleashed: return "flame"
        }
    }

    var tint: Color {
        switch self {
        case .readOnly: return .secondary
        case .guarded: return .blue
        case .autonomous: return .orange
        case .unleashed: return .red
        }
    }

    var blurb: String {
        switch self {
        case .readOnly: return "Claude may read and plan, but every action waits for you."
        case .guarded: return "Routine edits run automatically; risky actions ask first."
        case .autonomous: return "Claude works within your allow-list without interruption."
        case .unleashed: return "No prompts. Reserve for sandboxes and disposable worktrees."
        }
    }

    // MARK: Rust core bridge

    /// The lowercase identifier the Rust core (`cs_types::TrustMode`) uses on the
    /// wire. Send this — not ``rawValue`` — across the IPC boundary.
    var coreValue: String {
        switch self {
        case .readOnly: return "strict"
        case .guarded: return "standard"
        case .autonomous: return "auto"
        case .unleashed: return "yolo"
        }
    }

    /// Build a trust mode from the core's wire identifier. Returns `nil` for an
    /// unrecognized value so callers can fall back to a default.
    init?(coreValue: String) {
        switch coreValue {
        case "strict": self = .readOnly
        case "standard": self = .guarded
        case "auto": self = .autonomous
        case "yolo": self = .unleashed
        default: return nil
        }
    }
}
