import Foundation
#if canImport(Glibc)
import Glibc
#else
import Darwin
#endif

/// Actor that owns the Unix-domain-socket connection to the Rust core sidecar.
///
/// Responsibilities:
///  - connect / disconnect to the core's `core.sock`
///  - frame outbound `IpcEnvelope`s as `u32-BE length + MessagePack body`
///  - read inbound frames, correlate `response`/`error` to pending requests,
///    and surface `event` envelopes through an `AsyncStream`.
///
/// The socket I/O uses POSIX `recv`/`send` on a background read loop. All
/// mutable state lives on the actor so it is data-race free under Swift 6
/// strict concurrency.
public actor IpcClient {
    public enum State: Sendable, Equatable {
        case disconnected
        case connecting
        case connected
        case failed(String)
    }

    private(set) public var state: State = .disconnected

    private let socketPath: String
    private var fileDescriptor: Int32 = -1
    private var readThread: Thread?
    private var consumerTask: Task<Void, Never>?

    /// Pending request continuations keyed by envelope id.
    private var pending: [String: CheckedContinuation<IpcEnvelope, Error>] = [:]

    /// An ordered, internal hand-off from the blocking read thread to the actor.
    /// Frames are delivered in strict wire order through a single consumer, so the
    /// `events` stream and the terminal close are never reordered.
    private enum Inbound: Sendable {
        case frame(IpcEnvelope)
        case closed(IpcError)
    }
    private let inbound: AsyncStream<Inbound>
    private let inboundContinuation: AsyncStream<Inbound>.Continuation

    /// Broadcast stream of inbound `event`/`request` envelopes (supervisor ticks,
    /// session deltas, etc.). Consumers `for await` over `events`; it finishes
    /// when the connection is torn down. Bounded so a slow or absent consumer
    /// cannot grow memory without limit.
    public let events: AsyncStream<IpcEnvelope>
    private let eventContinuation: AsyncStream<IpcEnvelope>.Continuation

    public init(socketPath: String = IpcProtocol.defaultSocketPath) {
        self.socketPath = socketPath
        var eventCont: AsyncStream<IpcEnvelope>.Continuation!
        self.events = AsyncStream(bufferingPolicy: .bufferingNewest(256)) { eventCont = $0 }
        self.eventContinuation = eventCont
        var inboundCont: AsyncStream<Inbound>.Continuation!
        self.inbound = AsyncStream(bufferingPolicy: .unbounded) { inboundCont = $0 }
        self.inboundContinuation = inboundCont
    }

    // MARK: Connection lifecycle

    /// Opens the socket and starts the read loop. Idempotent: calling it while
    /// already connected is a no-op.
    public func connect() throws {
        guard state != .connected else { return }
        state = .connecting

        let descriptor = socket(AF_UNIX, SOCK_STREAM, 0)
        guard descriptor >= 0 else {
            let message = String(cString: strerror(errno))
            state = .failed(message)
            throw IpcError.connectionFailed("socket(): \(message)")
        }

        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let pathBytes = socketPath.utf8CString
        guard pathBytes.count <= MemoryLayout.size(ofValue: address.sun_path) else {
            close(descriptor)
            state = .failed("socket path too long")
            throw IpcError.connectionFailed("socket path too long")
        }
        withUnsafeMutablePointer(to: &address.sun_path) { rawPath in
            rawPath.withMemoryRebound(to: CChar.self, capacity: pathBytes.count) { dest in
                _ = pathBytes.withUnsafeBufferPointer { src in
                    memcpy(dest, src.baseAddress, pathBytes.count)
                }
            }
        }

        let connectResult = withUnsafePointer(to: &address) { pointer in
            pointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { sockPointer in
                Darwin.connect(descriptor, sockPointer, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard connectResult == 0 else {
            let message = String(cString: strerror(errno))
            close(descriptor)
            state = .failed(message)
            throw IpcError.connectionFailed("connect(\(socketPath)): \(message)")
        }

        fileDescriptor = descriptor
        state = .connected
        startReadLoop()
        startConsumer()
    }

    public func disconnect() {
        consumerTask?.cancel()
        consumerTask = nil
        readThread = nil
        teardown(error: .socketClosed)
    }

    // MARK: Requests & notifications

    /// How long a request waits for its reply before failing with
    /// ``IpcError/timedOut(method:)``. Bounds the round-trip so a core that is
    /// bound to the socket but wedged (never answers) can't hang the caller —
    /// and, critically, the connect handshake — forever.
    public static let requestTimeout: Duration = .seconds(10)

    /// Sends a request and awaits the correlated response. Throws
    /// `IpcError.remote` if the core replies with an `error` envelope, or
    /// `IpcError.timedOut` if no reply arrives within ``requestTimeout``.
    @discardableResult
    public func send(_ envelope: IpcEnvelope) async throws -> IpcEnvelope {
        guard state == .connected else { throw IpcError.notConnected }
        let id = envelope.id
        let method = envelope.method
        // Armed before the continuation is registered, but `Task.sleep` ensures
        // the body can't run until the deadline — by then `pending[id]` is set
        // (or already resolved). Cancelled the moment the request completes.
        let timeout = Task { [weak self] in
            try? await Task.sleep(for: Self.requestTimeout)
            guard !Task.isCancelled else { return }
            await self?.failTimedOut(id: id, method: method)
        }
        defer { timeout.cancel() }
        return try await withCheckedThrowingContinuation { continuation in
            pending[id] = continuation
            do {
                try writeFrame(envelope)
            } catch {
                pending[id] = nil
                continuation.resume(throwing: error)
            }
        }
    }

    /// Fail a still-pending request whose deadline elapsed. A no-op if the reply
    /// already arrived (the id was removed by ``dispatch(_:)``), so the
    /// continuation is resumed exactly once.
    private func failTimedOut(id: String, method: String) {
        if let continuation = pending.removeValue(forKey: id) {
            continuation.resume(throwing: IpcError.timedOut(method: method))
        }
    }

    /// Sends a notification (no response expected).
    public func notify(method: String, payload: MsgPackValue? = nil) throws {
        guard state == .connected else { throw IpcError.notConnected }
        try writeFrame(IpcEnvelope(kind: .event, method: method, payload: payload))
    }

    // MARK: Framing

    private func encodeEnvelope(_ envelope: IpcEnvelope) -> MsgPackValue {
        var map: [String: MsgPackValue] = [
            "id": .string(envelope.id),
            "kind": .string(envelope.kind.rawValue),
            "method": .string(envelope.method)
        ]
        map["payload"] = envelope.payload ?? .nil
        return .map(map)
    }

    private nonisolated func decodeEnvelope(_ value: MsgPackValue) throws -> IpcEnvelope {
        guard let map = value.mapValue,
              let id = map["id"]?.stringValue,
              let kindRaw = map["kind"]?.stringValue,
              let kind = IpcKind(rawValue: kindRaw),
              let method = map["method"]?.stringValue else {
            throw IpcError.decodeFailed("Malformed envelope")
        }
        let payload = map["payload"]
        let normalizedPayload: MsgPackValue?
        if case .nil = payload ?? .nil { normalizedPayload = nil } else { normalizedPayload = payload }
        return IpcEnvelope(id: id, kind: kind, method: method, payload: normalizedPayload)
    }

    private func writeFrame(_ envelope: IpcEnvelope) throws {
        let body = MessagePack.encode(encodeEnvelope(envelope))
        guard body.count <= IpcProtocol.maxFrameBytes else {
            throw IpcError.frameTooLarge(body.count)
        }
        var frame = Data()
        var length = UInt32(body.count).bigEndian
        withUnsafeBytes(of: &length) { frame.append(contentsOf: $0) }
        frame.append(body)
        try writeAll(frame)
    }

    private func writeAll(_ data: Data) throws {
        try data.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
            guard var base = raw.baseAddress else { return }
            var remaining = raw.count
            while remaining > 0 {
                let written = Darwin.send(fileDescriptor, base, remaining, 0)
                if written <= 0 {
                    throw IpcError.connectionFailed("send(): \(String(cString: strerror(errno)))")
                }
                base = base.advanced(by: written)
                remaining -= written
            }
        }
    }

    // MARK: Read loop

    private func startReadLoop() {
        let descriptor = fileDescriptor
        let thread = Thread { [weak self] in
            self?.readLoopBody(descriptor: descriptor)
        }
        thread.name = "claudestudio.ipc.read"
        readThread = thread
        thread.start()
    }

    /// The blocking read loop. Runs on its **own** `Thread`, never the actor's
    /// executor, so the blocking `recv` here can never stall an outbound `send`.
    /// Decoded frames are pushed — in strict wire order — onto the internal
    /// `inbound` stream, which a single actor-side consumer drains in order.
    private nonisolated func readLoopBody(descriptor: Int32) {
        while true {
            do {
                guard let header = try readExact(count: 4, descriptor: descriptor) else { break }
                let length = header.withUnsafeBytes { $0.load(as: UInt32.self).bigEndian }
                guard length <= IpcProtocol.maxFrameBytes else {
                    finishInbound(with: .frameTooLarge(Int(length)))
                    return
                }
                guard let body = try readExact(count: Int(length), descriptor: descriptor) else { break }
                let envelope = try decodeEnvelope(MessagePack.decode(body))
                inboundContinuation.yield(.frame(envelope))
            } catch {
                finishInbound(with: (error as? IpcError) ?? .socketClosed)
                return
            }
        }
        finishInbound(with: .socketClosed)
    }

    /// Emit a terminal close marker after every preceding frame, then finish the
    /// internal stream so the consumer observes it deterministically.
    private nonisolated func finishInbound(with error: IpcError) {
        inboundContinuation.yield(.closed(error))
        inboundContinuation.finish()
    }

    /// Start the single actor-side consumer that drains `inbound` in order.
    private func startConsumer() {
        consumerTask = Task { [weak self] in
            guard let self else { return }
            await self.runConsumer()
        }
    }

    /// Drain `inbound` in strict order: each frame is fully dispatched before the
    /// next is read, so neither the `events` stream nor the terminal close is
    /// ever raced against an earlier frame.
    private func runConsumer() async {
        for await item in inbound {
            switch item {
            case .frame(let envelope):
                dispatch(envelope)
            case .closed(let error):
                teardown(error: error)
                return
            }
        }
    }

    /// Blocking-but-cooperative read of exactly `count` bytes. Returns nil on a
    /// clean EOF before any bytes of the next frame arrive.
    private nonisolated func readExact(count: Int, descriptor: Int32) throws -> Data? {
        guard count > 0 else { return Data() }
        var buffer = [UInt8](repeating: 0, count: count)
        var received = 0
        while received < count {
            let read = buffer.withUnsafeMutableBytes { raw -> Int in
                guard let base = raw.baseAddress else { return -1 }
                return Darwin.recv(descriptor, base.advanced(by: received), count - received, 0)
            }
            if read == 0 {
                return received == 0 ? nil : nil
            }
            if read < 0 {
                if errno == EINTR { continue }
                throw IpcError.connectionFailed("recv(): \(String(cString: strerror(errno)))")
            }
            received += read
        }
        return Data(buffer)
    }

    private func dispatch(_ envelope: IpcEnvelope) {
        switch envelope.kind {
        case .response:
            if let continuation = pending.removeValue(forKey: envelope.id) {
                continuation.resume(returning: envelope)
            }
        case .error:
            if let continuation = pending.removeValue(forKey: envelope.id) {
                let code = envelope.payload?["code"]?.intValue.map(Int.init) ?? -1
                let message = envelope.payload?["message"]?.stringValue ?? "remote error"
                continuation.resume(throwing: IpcError.remote(code: code, message: message))
            }
        case .event:
            eventContinuation.yield(envelope)
        case .request:
            // The app is a client; inbound requests from the core are surfaced
            // as events so higher layers can choose to answer them.
            eventContinuation.yield(envelope)
        }
    }

    /// Tear the connection down idempotently: reliably unblock the read thread
    /// (`shutdown` before `close`), fail any in-flight requests, finish the event
    /// stream, and mark the client disconnected. Safe to call more than once.
    private func teardown(error: IpcError) {
        if fileDescriptor >= 0 {
            // `shutdown` reliably wakes a `recv` blocked on this fd on Darwin;
            // `close` alone does not, which would leave the read thread stuck and
            // risk it reading from a recycled fd after a later reconnect.
            shutdown(fileDescriptor, SHUT_RDWR)
            close(fileDescriptor)
            fileDescriptor = -1
        }
        for (_, continuation) in pending {
            continuation.resume(throwing: error)
        }
        pending.removeAll()
        eventContinuation.finish()
        if state == .connected || state == .connecting {
            state = .disconnected
        }
    }
}
