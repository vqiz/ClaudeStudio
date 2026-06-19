#if canImport(XCTest)
import XCTest
@testable import ClaudeStudioKit

final class MessagePackTests: XCTestCase {
    private func roundTrip(_ value: MsgPackValue) throws -> MsgPackValue {
        try MessagePack.decode(MessagePack.encode(value))
    }

    func testScalarRoundTrip() throws {
        XCTAssertEqual(try roundTrip(.nil), .nil)
        XCTAssertEqual(try roundTrip(.bool(true)), .bool(true))
        XCTAssertEqual(try roundTrip(.string("claude")).stringValue, "claude")
        XCTAssertEqual(try roundTrip(.double(3.14)).doubleValue, 3.14)
    }

    func testIntegerRanges() throws {
        for value: Int64 in [-1, -33, -200, -40_000, -3_000_000_000, 0, 127, 300, 70_000, 5_000_000_000] {
            let decoded = try roundTrip(.int(value))
            XCTAssertEqual(decoded.intValue, value, "failed for \(value)")
        }
    }

    func testNestedContainers() throws {
        let value: MsgPackValue = .map([
            "method": .string("session.start"),
            "args": .array([.int(1), .string("two"), .bool(false)]),
            "nested": .map(["depth": .int(2)])
        ])
        let decoded = try roundTrip(value)
        XCTAssertEqual(decoded["method"]?.stringValue, "session.start")
        XCTAssertEqual(decoded["args"]?.arrayValue?.count, 3)
        XCTAssertEqual(decoded["nested"]?["depth"]?.intValue, 2)
    }

    func testEnvelopeCodableThroughMsgPack() throws {
        let envelope = IpcEnvelope.request(
            method: "supervisor.tick",
            payload: .map(["budget": .double(2.5)])
        )
        // Encode the envelope as a MsgPackValue map and back.
        let encoded: MsgPackValue = .map([
            "id": .string(envelope.id),
            "kind": .string(envelope.kind.rawValue),
            "method": .string(envelope.method),
            "payload": envelope.payload ?? .nil
        ])
        let decoded = try roundTrip(encoded)
        XCTAssertEqual(decoded["kind"]?.stringValue, IpcKind.request.rawValue)
        XCTAssertEqual(decoded["method"]?.stringValue, "supervisor.tick")
        XCTAssertEqual(decoded["payload"]?["budget"]?.doubleValue, 2.5)
    }
}
#endif
