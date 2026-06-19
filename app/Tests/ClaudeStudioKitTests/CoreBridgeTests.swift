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

        // The repo root holds the shipped tasks/ and definitions/ libraries, and
        // is a git repo we can query. Derive it from this file's path.
        let repoRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()  // ClaudeStudioKitTests
            .deletingLastPathComponent()  // Tests
            .deletingLastPathComponent()  // app
            .deletingLastPathComponent()  // repo root
            .path
        // Isolate the core's state directory so the test never touches real data.
        let tmpHome = NSTemporaryDirectory() + "cs-bridge-home-\(ProcessInfo.processInfo.processIdentifier)"
        try? FileManager.default.createDirectory(atPath: tmpHome, withIntermediateDirectories: true)
        // An MCP config in the isolated HOME (mcp.list defaults to ~/.claude.json).
        try? #"{"mcpServers":{"fs":{"command":"npx"},"web":{"type":"http","url":"https://x"}}}"#
            .write(toFile: tmpHome + "/.claude.json", atomically: true, encoding: .utf8)

        let process = Process()
        process.executableURL = URL(fileURLWithPath: binPath)
        process.arguments = [socket]
        process.environment = [
            "RUST_LOG": "warn",
            "HOME": tmpHome,
            "CLAUDESTUDIO_LIBRARY_DIR": repoRoot,
        ]
        try process.run()
        defer {
            if process.isRunning { process.terminate() }
            try? FileManager.default.removeItem(atPath: socket)
            try? FileManager.default.removeItem(atPath: tmpHome)
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

        // 4. config.set round-trips and persists
        let updated = try await core.setConfig(trustMode: "auto", dailyBudgetUSD: 25.0)
        XCTAssertEqual(updated.trustMode, "auto")
        let reread = try await core.fetchConfig()
        XCTAssertEqual(reread.trustMode, "auto", "config.set must persist")

        // 5. session lifecycle (SQLite archive)
        let created = try await core.call("session.create", .map([
            "title": .string("Bridge test"), "cwd": .string(repoRoot), "branch": .string("main"),
        ]))
        let sid = try XCTUnwrap(created.payload?["id"]?.stringValue, "session.create returns an id")
        let sessions = try await core.listSessions()
        XCTAssertTrue(sessions.contains { $0.id == sid }, "created session appears in the list")

        // 6. git over the repo
        let branch = try await core.call("git.branch", .map(["cwd": .string(repoRoot)]))
        XCTAssertFalse((branch.payload?["branch"]?.stringValue ?? "").isEmpty)
        let gitLog = try await core.call("git.log", .map(["cwd": .string(repoRoot), "limit": .int(3)]))
        XCTAssertGreaterThanOrEqual((gitLog.payload?["commits"]?.arrayValue ?? []).count, 1)

        // 7. shipped libraries
        let tasks = try await core.fetchTasks()
        XCTAssertGreaterThan(tasks.count, 0, "task library should be discovered")
        let defs = try await core.fetchDefinitions()
        XCTAssertGreaterThan(defs.count, 0, "definition library should be discovered")
        let servers = try await core.fetchMcpServers()
        XCTAssertEqual(servers.count, 2, "two MCP servers from the isolated config")
        XCTAssertEqual(Set(servers.map(\.transport)), ["stdio", "http"])

        // 8. error path: an unknown method round-trips as a thrown remote error
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
