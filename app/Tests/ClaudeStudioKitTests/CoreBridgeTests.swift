#if canImport(XCTest)
import XCTest
import Foundation
@testable import ClaudeStudioKit

/// Tests for the typed bridge to the Rust core.
///
/// The decode tests run everywhere. `testEndToEndAgainstRustCore` spawns the real
/// `claudestudio-core` binary and talks to it over a Unix socket; it is skipped
/// unless `CLAUDESTUDIO_CORE_BIN` points at a built binary, so it never breaks a
/// Swift-only CI run.
final class CoreBridgeTests: XCTestCase {

    // MARK: Payload decoding (always runs)

    func testCoreConfigDecodesFromCorePayload() throws {
        // Mirrors the `config.get` payload shape from `cs-cli`'s router.
        let payload: MsgPackValue = .map([
            "trust_mode": .string("standard"),
            "default_model": .string("sonnet"),
            "daily_budget_usd": .double(10.0),
            "context_token_budget": .uint(180_000),
            "voice": .map(["enabled": .bool(false)]),
            "vector": .map(["collection": .string("claudestudio")])
        ])
        let config = try XCTUnwrap(CoreConfig(payload: payload))
        XCTAssertEqual(config.trustMode, "standard")
        XCTAssertEqual(config.defaultModel, "sonnet")
        XCTAssertEqual(config.dailyBudgetUSD, 10.0)
        XCTAssertEqual(config.contextTokenBudget, 180_000)
        XCTAssertFalse(config.voiceEnabled)
        XCTAssertEqual(config.vectorCollection, "claudestudio")
    }

    func testCoreConfigRejectsMissingRequiredFields() {
        let payload: MsgPackValue = .map(["daily_budget_usd": .double(1.0)])
        XCTAssertNil(CoreConfig(payload: payload))
    }

    func testContextBudgetDecodesLayers() throws {
        let payload: MsgPackValue = .map([
            "total_budget": .uint(180_000),
            "granted_total": .uint(17_400),
            "remaining": .uint(162_600),
            "layers": .array([
                .map([
                    "layer": .string("Global CLAUDE.md"),
                    "requested_tokens": .uint(1_200),
                    "granted_tokens": .uint(1_200),
                    "truncated": .bool(false)
                ]),
                .map([
                    "layer": .string("Vector Retrieval"),
                    "requested_tokens": .uint(6_000),
                    "granted_tokens": .uint(4_000),
                    "truncated": .bool(true)
                ])
            ])
        ])
        let budget = try XCTUnwrap(ContextBudget(payload: payload))
        XCTAssertEqual(budget.totalBudget, 180_000)
        XCTAssertEqual(budget.grantedTotal, 17_400)
        XCTAssertEqual(budget.remaining, 162_600)
        XCTAssertEqual(budget.layers.count, 2)
        XCTAssertEqual(budget.layers[0].label, "Global CLAUDE.md")
        XCTAssertFalse(budget.layers[0].truncated)
        XCTAssertTrue(budget.layers[1].truncated)
        XCTAssertEqual(budget.layers[1].grantedTokens, 4_000)
    }

    // MARK: End-to-end against the real sidecar (opt-in)

    func testEndToEndAgainstRustCore() async throws {
        let env = ProcessInfo.processInfo.environment
        guard let binPath = env["CLAUDESTUDIO_CORE_BIN"],
              FileManager.default.isExecutableFile(atPath: binPath) else {
            throw XCTSkip("Set CLAUDESTUDIO_CORE_BIN to the built claudestudio-core binary to run the bridge integration test.")
        }

        let socket = NSTemporaryDirectory()
            + "cs-bridge-\(ProcessInfo.processInfo.processIdentifier)-\(UInt32.random(in: 0...UInt32.max)).sock"
        try? FileManager.default.removeItem(atPath: socket)

        let process = Process()
        process.executableURL = URL(fileURLWithPath: binPath)
        process.arguments = [socket]
        process.environment = ["RUST_LOG": "warn"]
        try process.run()
        defer {
            if process.isRunning { process.terminate() }
            try? FileManager.default.removeItem(atPath: socket)
        }

        try await waitForFile(socket, timeout: 5.0)

        let core = CoreClient(socketPath: socket)
        try await core.connect()

        // 1. ping → pong
        let pong = try await core.ping()
        XCTAssertTrue(pong, "core should answer ping with pong")

        // 2. config.get → typed config
        let config = try await core.fetchConfig()
        XCTAssertFalse(config.trustMode.isEmpty)
        XCTAssertFalse(config.defaultModel.isEmpty)
        XCTAssertGreaterThan(config.contextTokenBudget, 0)

        // 3. context.budget → six layers
        let budget = try await core.fetchContextBudget()
        XCTAssertEqual(budget.layers.count, 6, "the assembler reports six context layers")
        XCTAssertGreaterThan(budget.totalBudget, 0)

        // 4. error path: an unknown method round-trips as a thrown remote error
        do {
            _ = try await core.call("does.not.exist")
            XCTFail("unknown method should have thrown")
        } catch let IpcError.remote(_, message) {
            XCTAssertTrue(message.contains("unknown method"), "got: \(message)")
        }

        await core.disconnect()
    }

    /// Poll until `path` exists or the timeout elapses.
    private func waitForFile(_ path: String, timeout: TimeInterval) async throws {
        let start = Date()
        while Date().timeIntervalSince(start) < timeout {
            if FileManager.default.fileExists(atPath: path) { return }
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        XCTFail("core socket \(path) did not appear within \(timeout)s")
    }
}
#endif
