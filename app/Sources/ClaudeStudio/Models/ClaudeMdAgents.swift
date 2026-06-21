import Foundation

/// Renders the project's assigned agents into a *managed section* of its
/// CLAUDE.md, so that every Claude request in the project follows them. The
/// section lives between two markers; everything outside the markers is the
/// user's own content and is preserved verbatim.
enum ClaudeMdAgents {
    static let startMarker =
        "<!-- claudestudio:agents START — managed by ClaudeStudio; edits between these markers are overwritten -->"
    static let endMarker = "<!-- claudestudio:agents END -->"

    /// The managed block for `agents`, or `""` when none are assigned (which
    /// removes the section entirely).
    static func block(for agents: [AgentDefinition]) -> String {
        guard !agents.isEmpty else { return "" }
        var s = startMarker + "\n"
        s += "## Agents — how every request in this project should run\n\n"
        s += "This project is configured with the agent roles below (from ClaudeStudio's Agent Studio). "
        s += "For every request, adopt the most relevant role and follow its instructions; "
        s += "when a task spans several roles, coordinate them.\n\n"
        for agent in agents {
            let role = agent.role.trimmingCharacters(in: .whitespacesAndNewlines)
            s += "### \(agent.name)" + (role.isEmpty ? "" : " — \(role)") + "\n"
            s += "- Model: `\(agent.model)` · Trust: \(agent.trustMode.label)\n"
            let prompt = agent.systemPrompt.trimmingCharacters(in: .whitespacesAndNewlines)
            if !prompt.isEmpty {
                s += "\n\(prompt)\n"
            }
            s += "\n"
        }
        s += endMarker
        return s
    }

    /// Splice `block` into `content`, replacing any existing managed section and
    /// preserving everything else. Passing an empty `block` removes the section.
    static func splice(into content: String, block: String) -> String {
        var base = content
        if let range = managedRange(in: base) {
            base.removeSubrange(range)
        }
        let trimmedBase = base.trimmingCharacters(in: .whitespacesAndNewlines)
        if block.isEmpty {
            return trimmedBase.isEmpty ? "" : trimmedBase + "\n"
        }
        if trimmedBase.isEmpty {
            return block + "\n"
        }
        return trimmedBase + "\n\n" + block + "\n"
    }

    /// The range covering the managed section (markers inclusive), if present.
    private static func managedRange(in content: String) -> Range<String.Index>? {
        guard let start = content.range(of: startMarker),
              let end = content.range(of: endMarker),
              start.lowerBound < end.upperBound else { return nil }
        return start.lowerBound..<end.upperBound
    }
}
