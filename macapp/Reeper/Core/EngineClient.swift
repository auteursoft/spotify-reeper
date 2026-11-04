import Foundation

/// Progress reported by a long engine job.
public struct EngineProgress: Sendable, Equatable {
    public var message: String
    public var done: Int?
    public var total: Int?

    /// 0...1 when the job knows how far along it is.
    public var fraction: Double? {
        guard let done, let total, total > 0 else { return nil }
        return min(1, max(0, Double(done) / Double(total)))
    }
}

public enum EngineFailure: Error, LocalizedError, Equatable {
    /// The engine reported a problem. `code` is user_error, invalid_pattern, auth,
    /// not_configured, cancelled or internal.
    case engine(code: String, message: String)
    /// The engine process isn't running (it never started, or it quit).
    case stopped(String)
    /// The engine replied with something the app couldn't read (a bug).
    case badReply(String)

    public var code: String {
        switch self {
        case .engine(let code, _): return code
        case .stopped: return "stopped"
        case .badReply: return "internal"
        }
    }

    public var isCancellation: Bool { code == "cancelled" }

    public var errorDescription: String? {
        switch self {
        case .engine(_, let message): return message
        case .stopped(let detail):
            return "Reeper's analysis engine stopped unexpectedly." + (detail.isEmpty ? "" : "\n\n\(detail)")
        case .badReply(let detail): return "Reeper's analysis engine sent an unexpected reply. \(detail)"
        }
    }
}

/// Runs `python -m reeper_engine` and talks to it one JSON object per line.
/// See reeper_engine/server.py for the protocol.
public final class EngineClient: @unchecked Sendable {
    public struct Launch: Sendable {
        public var executable: URL
        public var arguments: [String]
        public var environment: [String: String]
        public var currentDirectory: URL?

        public init(executable: URL, arguments: [String], environment: [String: String],
                    currentDirectory: URL? = nil) {
            self.executable = executable
            self.arguments = arguments
            self.environment = environment
            self.currentDirectory = currentDirectory
        }
    }

    private struct Pending {
        let progress: (@Sendable (EngineProgress) -> Void)?
        let continuation: CheckedContinuation<Data, Error>
    }

    private let launch: Launch
    private let lock = NSLock()
    private let writeLock = NSLock()
    private var process: Process?
    private var input: FileHandle?
    private var pending: [String: Pending] = [:]
    private var counter = 0
    private var readyContinuation: CheckedContinuation<Void, Error>?
    private var isReady = false
    private var stderrTail: [String] = []

    /// Unsolicited events, e.g. ("tokens", <json>) when the engine refreshed Spotify tokens.
    public var onEvent: (@Sendable (String, Data) -> Void)?
    /// Called once if the engine process exits.
    public var onExit: (@Sendable (Int32, String) -> Void)?
    /// Each line the engine writes to stderr (its diagnostic log).
    public var onLog: (@Sendable (String) -> Void)?

    public init(launch: Launch) {
        self.launch = launch
    }

    public var isRunning: Bool {
        lock.lock(); defer { lock.unlock() }
        return process?.isRunning == true && isReady
    }

    // MARK: Lifecycle

    /// Starts the engine and waits until it says it's ready.
    public func start(timeout: TimeInterval = 60) async throws {
        signal(SIGPIPE, SIG_IGN)  // a dead engine must not take the app down with it
        let p = Process()
        p.executableURL = launch.executable
        p.arguments = launch.arguments
        p.environment = launch.environment
        if let dir = launch.currentDirectory { p.currentDirectoryURL = dir }
        let stdinPipe = Pipe(), stdoutPipe = Pipe(), stderrPipe = Pipe()
        p.standardInput = stdinPipe
        p.standardOutput = stdoutPipe
        p.standardError = stderrPipe
        p.terminationHandler = { [weak self] proc in self?.processEnded(status: proc.terminationStatus) }

        try p.run()
        attach(p, input: stdinPipe.fileHandleForWriting)

        let out = stdoutPipe.fileHandleForReading
        let err = stderrPipe.fileHandleForReading
        Thread.detachNewThread { [weak self] in self?.readLoop(out, handleLine: { self?.handleStdout($0) }) }
        Thread.detachNewThread { [weak self] in self?.readLoop(err, handleLine: { self?.handleStderr($0) }) }

        try await withThrowingTaskGroup(of: Void.self) { group in
            group.addTask {
                try await withCheckedThrowingContinuation { (c: CheckedContinuation<Void, Error>) in
                    self.lock.lock()
                    if self.isReady {
                        self.lock.unlock()
                        c.resume()
                    } else {
                        self.readyContinuation = c
                        self.lock.unlock()
                    }
                }
            }
            group.addTask {
                try await Task.sleep(nanoseconds: UInt64(timeout * 1_000_000_000))
                throw EngineFailure.stopped("The engine took too long to start.")
            }
            defer { group.cancelAll() }
            try await group.next()
        }
    }

    private func attach(_ p: Process, input handle: FileHandle) {
        lock.lock(); defer { lock.unlock() }
        process = p
        input = handle
    }

    /// Asks the engine to quit (quick requests finish; long jobs are cancelled and resume later).
    public func stop(grace: TimeInterval = 3) {
        try? send(["method": "shutdown"])
        lock.lock()
        let p = process
        lock.unlock()
        guard let p else { return }
        let deadline = Date().addingTimeInterval(grace)
        while p.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.05) }
        if p.isRunning { p.terminate() }
    }

    // MARK: Requests

    /// Calls an engine method and decodes its result.
    public func call<R: Decodable>(_ method: String, _ params: [String: JSONValue] = [:],
                                   as type: R.Type = R.self,
                                   progress: (@Sendable (EngineProgress) -> Void)? = nil) async throws -> R {
        let data = try await callRaw(method, params, progress: progress)
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        do {
            return try decoder.decode(R.self, from: data)
        } catch {
            throw EngineFailure.badReply("(\(method): \(error))")
        }
    }

    /// Calls an engine method and returns its raw JSON result. Cancelling the Swift task
    /// cancels the engine job.
    public func callRaw(_ method: String, _ params: [String: JSONValue] = [:],
                        progress: (@Sendable (EngineProgress) -> Void)? = nil) async throws -> Data {
        let id = nextID()
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (c: CheckedContinuation<Data, Error>) in
                lock.lock()
                guard process != nil else {
                    lock.unlock()
                    c.resume(throwing: EngineFailure.stopped("The engine isn't running."))
                    return
                }
                pending[id] = Pending(progress: progress, continuation: c)
                lock.unlock()
                do {
                    try send(["id": .string(id), "method": .string(method), "params": .object(params)])
                } catch {
                    if let p = takePending(id) {
                        p.continuation.resume(throwing: EngineFailure.stopped("\(error)"))
                    }
                    return
                }
                if Task.isCancelled { cancel(id) }
            }
        } onCancel: {
            self.cancel(id)
        }
    }

    private func cancel(_ id: String) {
        try? send(["method": "cancel", "params": ["id": .string(id)]])
    }

    private func nextID() -> String {
        lock.lock(); defer { lock.unlock() }
        counter += 1
        return String(counter)
    }

    private func send(_ message: [String: JSONValue]) throws {
        var data = try JSONEncoder().encode(JSONValue.object(message))
        data.append(0x0A)
        lock.lock()
        let handle = input
        lock.unlock()
        guard let handle else { throw EngineFailure.stopped("The engine isn't running.") }
        writeLock.lock(); defer { writeLock.unlock() }
        try handle.write(contentsOf: data)
    }

    private func takePending(_ id: String) -> Pending? {
        lock.lock(); defer { lock.unlock() }
        return pending.removeValue(forKey: id)
    }

    // MARK: Reading

    private func readLoop(_ handle: FileHandle, handleLine: (String) -> Void) {
        var buffer = Data()
        while true {
            let chunk = handle.availableData
            if chunk.isEmpty { break }
            buffer.append(chunk)
            while let nl = buffer.firstIndex(of: 0x0A) {
                let lineData = buffer[buffer.startIndex..<nl]
                buffer.removeSubrange(buffer.startIndex...nl)
                if let line = String(data: lineData, encoding: .utf8), !line.isEmpty { handleLine(line) }
            }
        }
    }

    private func handleStderr(_ line: String) {
        lock.lock()
        stderrTail.append(line)
        if stderrTail.count > 40 { stderrTail.removeFirst(stderrTail.count - 40) }
        lock.unlock()
        onLog?(line)
    }

    private func handleStdout(_ line: String) {
        guard let obj = (try? JSONSerialization.jsonObject(with: Data(line.utf8))) as? [String: Any],
              let type = obj["type"] as? String else {
            onLog?("unreadable engine output: \(line)")
            return
        }
        let id = obj["id"] as? String
        switch type {
        case "ready":
            lock.lock()
            isReady = true
            let c = readyContinuation
            readyContinuation = nil
            lock.unlock()
            c?.resume()
        case "progress":
            guard let id else { return }
            lock.lock()
            let p = pending[id]
            lock.unlock()
            p?.progress?(EngineProgress(message: obj["message"] as? String ?? "",
                                        done: (obj["done"] as? NSNumber)?.intValue,
                                        total: (obj["total"] as? NSNumber)?.intValue))
        case "result":
            guard let id, let p = takePending(id) else { return }
            let result = obj["result"] ?? NSNull()
            if let data = try? JSONSerialization.data(withJSONObject: result, options: [.fragmentsAllowed]) {
                p.continuation.resume(returning: data)
            } else {
                p.continuation.resume(throwing: EngineFailure.badReply("unreadable result"))
            }
        case "error":
            let failure = EngineFailure.engine(code: obj["code"] as? String ?? "internal",
                                               message: obj["message"] as? String ?? "Unknown error")
            if let id, let p = takePending(id) {
                p.continuation.resume(throwing: failure)
            } else {
                onLog?("engine error: \(line)")
            }
        case "event":
            let data = (try? JSONSerialization.data(withJSONObject: obj["data"] ?? NSNull(),
                                                    options: [.fragmentsAllowed])) ?? Data()
            onEvent?(obj["event"] as? String ?? "", data)
        default:
            onLog?("unknown engine message: \(line)")
        }
    }

    private func processEnded(status: Int32) {
        lock.lock()
        let waiting = pending
        pending.removeAll()
        let ready = readyContinuation
        readyContinuation = nil
        let tail = stderrTail.suffix(8).joined(separator: "\n")
        process = nil
        input = nil
        isReady = false
        lock.unlock()
        let failure = EngineFailure.stopped(tail)
        for p in waiting.values { p.continuation.resume(throwing: failure) }
        ready?.resume(throwing: failure)
        onExit?(status, tail)
    }
}
