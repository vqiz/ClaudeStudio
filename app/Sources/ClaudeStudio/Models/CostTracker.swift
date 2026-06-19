import Foundation
import Observation

/// Running cost / token accounting for a session, updated as transcript events
/// arrive. Exposed as `@Observable` so SwiftUI cost counters refresh live.
@Observable
final class CostTracker {
    private(set) var totalCostUSD: Double
    private(set) var totalTokens: Int
    /// Soft budget ceiling in USD. Crossing it flips `isOverBudget`.
    var budgetUSD: Double

    init(totalCostUSD: Double = 0, totalTokens: Int = 0, budgetUSD: Double = 5.0) {
        self.totalCostUSD = totalCostUSD
        self.totalTokens = totalTokens
        self.budgetUSD = budgetUSD
    }

    var isOverBudget: Bool { totalCostUSD >= budgetUSD }

    var budgetFraction: Double {
        guard budgetUSD > 0 else { return 0 }
        return min(totalCostUSD / budgetUSD, 1.0)
    }

    func record(_ event: SessionEvent) {
        totalCostUSD += event.costDelta
        totalTokens += event.tokenDelta
    }

    func reset() {
        totalCostUSD = 0
        totalTokens = 0
    }

    var formattedCost: String {
        String(format: "$%.3f", totalCostUSD)
    }

    var formattedTokens: String {
        if totalTokens >= 1_000 {
            return String(format: "%.1fk tok", Double(totalTokens) / 1_000)
        }
        return "\(totalTokens) tok"
    }
}
