import Foundation

/// A dynamically-typed MessagePack value.
///
/// This is intentionally small: it covers exactly the value shapes the
/// `IpcEnvelope` protocol needs (nil, bool, int, double, string, binary,
/// array, map). It is `Codable` so `IpcEnvelope` can be encoded/decoded by the
/// hand-rolled `MessagePack` codec below, and it bridges cleanly to JSON-like
/// Swift values for the UI layer.
public indirect enum MsgPackValue: Sendable, Equatable, Codable {
    case `nil`
    case bool(Bool)
    case int(Int64)
    case uint(UInt64)
    case double(Double)
    case string(String)
    case binary(Data)
    case array([MsgPackValue])
    case map([String: MsgPackValue])

    // MARK: Codable bridge (so IpcEnvelope is Codable end-to-end)

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .nil
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Int64.self) {
            self = .int(value)
        } else if let value = try? container.decode(UInt64.self) {
            self = .uint(value)
        } else if let value = try? container.decode(Double.self) {
            self = .double(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode([MsgPackValue].self) {
            self = .array(value)
        } else if let value = try? container.decode([String: MsgPackValue].self) {
            self = .map(value)
        } else {
            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "Unsupported MsgPackValue encoding"
            )
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .nil: try container.encodeNil()
        case .bool(let value): try container.encode(value)
        case .int(let value): try container.encode(value)
        case .uint(let value): try container.encode(value)
        case .double(let value): try container.encode(value)
        case .string(let value): try container.encode(value)
        case .binary(let value): try container.encode(value)
        case .array(let value): try container.encode(value)
        case .map(let value): try container.encode(value)
        }
    }
}

// MARK: - Ergonomic accessors

public extension MsgPackValue {
    var stringValue: String? {
        if case .string(let value) = self { return value }
        return nil
    }

    var intValue: Int64? {
        switch self {
        case .int(let value): return value
        case .uint(let value): return Int64(exactly: value)
        default: return nil
        }
    }

    var doubleValue: Double? {
        switch self {
        case .double(let value): return value
        case .int(let value): return Double(value)
        case .uint(let value): return Double(value)
        default: return nil
        }
    }

    var boolValue: Bool? {
        if case .bool(let value) = self { return value }
        return nil
    }

    var arrayValue: [MsgPackValue]? {
        if case .array(let value) = self { return value }
        return nil
    }

    var mapValue: [String: MsgPackValue]? {
        if case .map(let value) = self { return value }
        return nil
    }

    subscript(key: String) -> MsgPackValue? {
        mapValue?[key]
    }
}

// MARK: - Literal conveniences

extension MsgPackValue: ExpressibleByStringLiteral {
    public init(stringLiteral value: String) { self = .string(value) }
}

extension MsgPackValue: ExpressibleByIntegerLiteral {
    public init(integerLiteral value: Int64) { self = .int(value) }
}

extension MsgPackValue: ExpressibleByBooleanLiteral {
    public init(booleanLiteral value: Bool) { self = .bool(value) }
}

extension MsgPackValue: ExpressibleByDictionaryLiteral {
    public init(dictionaryLiteral elements: (String, MsgPackValue)...) {
        self = .map(Dictionary(uniqueKeysWithValues: elements))
    }
}

extension MsgPackValue: ExpressibleByArrayLiteral {
    public init(arrayLiteral elements: MsgPackValue...) {
        self = .array(elements)
    }
}

/// A minimal, dependency-free MessagePack codec covering the subset of the
/// spec that `IpcEnvelope` uses. It is not a full implementation (extension
/// types, timestamps, and 64-bit-unsigned edge cases beyond Int64 are not
/// round-tripped beyond what we need), but it is correct for everything the
/// transport sends and receives.
public enum MessagePack {
    // MARK: Encoding

    public static func encode(_ value: MsgPackValue) -> Data {
        var out = Data()
        encode(value, into: &out)
        return out
    }

    private static func encode(_ value: MsgPackValue, into out: inout Data) {
        switch value {
        case .nil:
            out.append(0xC0)
        case .bool(let bool):
            out.append(bool ? 0xC3 : 0xC2)
        case .int(let int):
            encodeInt(int, into: &out)
        case .uint(let uint):
            encodeUInt(uint, into: &out)
        case .double(let double):
            out.append(0xCB)
            appendBigEndian(double.bitPattern, into: &out)
        case .string(let string):
            encodeString(string, into: &out)
        case .binary(let data):
            encodeBinary(data, into: &out)
        case .array(let array):
            encodeArrayHeader(array.count, into: &out)
            for element in array { encode(element, into: &out) }
        case .map(let map):
            encodeMapHeader(map.count, into: &out)
            // Deterministic key ordering keeps frames reproducible for tests.
            for key in map.keys.sorted() {
                encodeString(key, into: &out)
                encode(map[key] ?? .nil, into: &out)
            }
        }
    }

    private static func encodeInt(_ int: Int64, into out: inout Data) {
        if int >= 0 {
            encodeUInt(UInt64(int), into: &out)
            return
        }
        if int >= -32 {
            out.append(UInt8(bitPattern: Int8(int)))
        } else if int >= -128 {
            out.append(0xD0)
            out.append(UInt8(bitPattern: Int8(truncatingIfNeeded: int)))
        } else if int >= -32_768 {
            out.append(0xD1)
            appendBigEndian(UInt16(bitPattern: Int16(truncatingIfNeeded: int)), into: &out)
        } else if int >= -2_147_483_648 {
            out.append(0xD2)
            appendBigEndian(UInt32(bitPattern: Int32(truncatingIfNeeded: int)), into: &out)
        } else {
            out.append(0xD3)
            appendBigEndian(UInt64(bitPattern: int), into: &out)
        }
    }

    private static func encodeUInt(_ uint: UInt64, into out: inout Data) {
        if uint <= 0x7F {
            out.append(UInt8(uint))
        } else if uint <= 0xFF {
            out.append(0xCC)
            out.append(UInt8(uint))
        } else if uint <= 0xFFFF {
            out.append(0xCD)
            appendBigEndian(UInt16(uint), into: &out)
        } else if uint <= 0xFFFF_FFFF {
            out.append(0xCE)
            appendBigEndian(UInt32(uint), into: &out)
        } else {
            out.append(0xCF)
            appendBigEndian(uint, into: &out)
        }
    }

    private static func encodeString(_ string: String, into out: inout Data) {
        let bytes = Data(string.utf8)
        let count = bytes.count
        if count <= 31 {
            out.append(0xA0 | UInt8(count))
        } else if count <= 0xFF {
            out.append(0xD9)
            out.append(UInt8(count))
        } else if count <= 0xFFFF {
            out.append(0xDA)
            appendBigEndian(UInt16(count), into: &out)
        } else {
            out.append(0xDB)
            appendBigEndian(UInt32(count), into: &out)
        }
        out.append(bytes)
    }

    private static func encodeBinary(_ data: Data, into out: inout Data) {
        let count = data.count
        if count <= 0xFF {
            out.append(0xC4)
            out.append(UInt8(count))
        } else if count <= 0xFFFF {
            out.append(0xC5)
            appendBigEndian(UInt16(count), into: &out)
        } else {
            out.append(0xC6)
            appendBigEndian(UInt32(count), into: &out)
        }
        out.append(data)
    }

    private static func encodeArrayHeader(_ count: Int, into out: inout Data) {
        if count <= 15 {
            out.append(0x90 | UInt8(count))
        } else if count <= 0xFFFF {
            out.append(0xDC)
            appendBigEndian(UInt16(count), into: &out)
        } else {
            out.append(0xDD)
            appendBigEndian(UInt32(count), into: &out)
        }
    }

    private static func encodeMapHeader(_ count: Int, into out: inout Data) {
        if count <= 15 {
            out.append(0x80 | UInt8(count))
        } else if count <= 0xFFFF {
            out.append(0xDE)
            appendBigEndian(UInt16(count), into: &out)
        } else {
            out.append(0xDF)
            appendBigEndian(UInt32(count), into: &out)
        }
    }

    private static func appendBigEndian<T: FixedWidthInteger>(_ value: T, into out: inout Data) {
        var bigEndian = value.bigEndian
        withUnsafeBytes(of: &bigEndian) { out.append(contentsOf: $0) }
    }

    // MARK: Decoding

    public static func decode(_ data: Data) throws -> MsgPackValue {
        var reader = Reader(data)
        let value = try reader.readValue()
        return value
    }

    private struct Reader {
        let bytes: [UInt8]
        var offset = 0

        init(_ data: Data) { self.bytes = [UInt8](data) }

        mutating func readValue() throws -> MsgPackValue {
            let byte = try readByte()
            switch byte {
            case 0x00...0x7F:
                return .uint(UInt64(byte))
            case 0xE0...0xFF:
                return .int(Int64(Int8(bitPattern: byte)))
            case 0x80...0x8F:
                return try readMap(count: Int(byte & 0x0F))
            case 0x90...0x9F:
                return try readArray(count: Int(byte & 0x0F))
            case 0xA0...0xBF:
                return try readString(count: Int(byte & 0x1F))
            case 0xC0:
                return .nil
            case 0xC2:
                return .bool(false)
            case 0xC3:
                return .bool(true)
            case 0xC4:
                return .binary(try readData(count: Int(try readByte())))
            case 0xC5:
                return .binary(try readData(count: Int(try readUInt16())))
            case 0xC6:
                return .binary(try readData(count: Int(try readUInt32())))
            case 0xCA:
                return .double(Double(bitPattern: UInt64(try readUInt32())))
            case 0xCB:
                return .double(Double(bitPattern: try readUInt64()))
            case 0xCC:
                return .uint(UInt64(try readByte()))
            case 0xCD:
                return .uint(UInt64(try readUInt16()))
            case 0xCE:
                return .uint(UInt64(try readUInt32()))
            case 0xCF:
                return .uint(try readUInt64())
            case 0xD0:
                return .int(Int64(Int8(bitPattern: try readByte())))
            case 0xD1:
                return .int(Int64(Int16(bitPattern: try readUInt16())))
            case 0xD2:
                return .int(Int64(Int32(bitPattern: try readUInt32())))
            case 0xD3:
                return .int(Int64(bitPattern: try readUInt64()))
            case 0xD9:
                return try readString(count: Int(try readByte()))
            case 0xDA:
                return try readString(count: Int(try readUInt16()))
            case 0xDB:
                return try readString(count: Int(try readUInt32()))
            case 0xDC:
                return try readArray(count: Int(try readUInt16()))
            case 0xDD:
                return try readArray(count: Int(try readUInt32()))
            case 0xDE:
                return try readMap(count: Int(try readUInt16()))
            case 0xDF:
                return try readMap(count: Int(try readUInt32()))
            default:
                throw IpcError.decodeFailed("Unsupported MessagePack tag 0x\(String(byte, radix: 16))")
            }
        }

        mutating func readArray(count: Int) throws -> MsgPackValue {
            var items: [MsgPackValue] = []
            items.reserveCapacity(count)
            for _ in 0..<count { items.append(try readValue()) }
            return .array(items)
        }

        mutating func readMap(count: Int) throws -> MsgPackValue {
            var map: [String: MsgPackValue] = [:]
            for _ in 0..<count {
                let key = try readValue()
                let value = try readValue()
                guard case .string(let keyString) = key else {
                    throw IpcError.decodeFailed("Map keys must be strings")
                }
                map[keyString] = value
            }
            return .map(map)
        }

        mutating func readString(count: Int) throws -> MsgPackValue {
            let data = try readData(count: count)
            guard let string = String(data: data, encoding: .utf8) else {
                throw IpcError.decodeFailed("Invalid UTF-8 string")
            }
            return .string(string)
        }

        mutating func readByte() throws -> UInt8 {
            guard offset < bytes.count else { throw IpcError.decodeFailed("Truncated frame") }
            defer { offset += 1 }
            return bytes[offset]
        }

        mutating func readData(count: Int) throws -> Data {
            guard offset + count <= bytes.count else { throw IpcError.decodeFailed("Truncated payload") }
            defer { offset += count }
            return Data(bytes[offset..<offset + count])
        }

        mutating func readUInt16() throws -> UInt16 {
            let high = UInt16(try readByte())
            let low = UInt16(try readByte())
            return (high << 8) | low
        }

        mutating func readUInt32() throws -> UInt32 {
            var value: UInt32 = 0
            for _ in 0..<4 { value = (value << 8) | UInt32(try readByte()) }
            return value
        }

        mutating func readUInt64() throws -> UInt64 {
            var value: UInt64 = 0
            for _ in 0..<8 { value = (value << 8) | UInt64(try readByte()) }
            return value
        }
    }
}
