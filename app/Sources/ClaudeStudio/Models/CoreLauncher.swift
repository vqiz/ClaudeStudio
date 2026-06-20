import Foundation

/// Locates and, when needed, spawns the `claudestudio-core` sidecar so the app
/// works with **no terminal** — you just launch the app and it starts (and later
/// stops) the core itself.
///
/// The binary is found, in order, via:
/// 1. `CLAUDESTUDIO_CORE_BIN` (explicit override),
/// 2. bundled next to the app executable (`.app/Contents/MacOS/` — shipped app or
///    the Xcode "build & bundle core" phase),
/// 3. a dev fallback that walks up from the executable to a
///    `core/target/{release,debug}/claudestudio-core` checkout (covers `swift run`).
@MainActor
final class CoreLauncher {
    static let shared = CoreLauncher()

    /// The core process we spawned (nil if a core was already running or we
    /// couldn't start one). Only a process we own is ever terminated.
    private var process: Process?

    /// True once we've spawned a core that is still running.
    var didSpawn: Bool { process?.isRunning ?? false }

    /// Ensure a core is listening at `socketPath`, spawning one if necessary.
    /// Call this only after a direct connect attempt has failed.
    func ensureRunning(socketPath: String) async -> Bool {
        guard let binary = locateBinary() else { return false }
        guard spawn(binary, socketPath: socketPath) else { return false }
        return await waitForSocket(socketPath, timeout: 8)
    }

    /// Terminate the core we spawned (no-op for a core started elsewhere).
    func terminate() {
        process?.terminate()
        process = nil
    }

    // MARK: - Locating the binary

    func locateBinary() -> String? {
        let fm = FileManager.default

        if let override = ProcessInfo.processInfo.environment["CLAUDESTUDIO_CORE_BIN"],
           fm.isExecutableFile(atPath: override) {
            return override
        }

        // Bundled next to the app executable.
        if let aux = Bundle.main.url(forAuxiliaryExecutable: "claudestudio-core")?.path,
           fm.isExecutableFile(atPath: aux) {
            return aux
        }
        if let exeDir = Bundle.main.executableURL?.deletingLastPathComponent() {
            let candidate = exeDir.appendingPathComponent("claudestudio-core").path
            if fm.isExecutableFile(atPath: candidate) { return candidate }
        }

        // Dev fallback: walk up to a Cargo target dir (handles `swift run`).
        if let start = Bundle.main.executableURL {
            var dir = start.deletingLastPathComponent()
            for _ in 0..<8 {
                for config in ["release", "debug"] {
                    let candidate = dir.appendingPathComponent("core/target/\(config)/claudestudio-core").path
                    if fm.isExecutableFile(atPath: candidate) { return candidate }
                }
                dir = dir.deletingLastPathComponent()
            }
        }
        return nil
    }

    // MARK: - Spawning

    private func spawn(_ binaryPath: String, socketPath: String) -> Bool {
        if let existing = process { existing.terminate(); process = nil }

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: binaryPath)
        proc.arguments = [socketPath]

        // Inherit the environment, but make sure the core can find `claude`
        // (Finder/Xcode launch the app with a minimal PATH) and quiet its logs.
        var env = ProcessInfo.processInfo.environment
        if env["RUST_LOG"] == nil { env["RUST_LOG"] = "warn" }
        // Point the core at the shipped task/definition libraries (bundled in the
        // .app, or a dev checkout next to the binary) unless already set.
        if env["CLAUDESTUDIO_LIBRARY_DIR"] == nil, let lib = locateLibraryDir(binaryPath: binaryPath) {
            env["CLAUDESTUDIO_LIBRARY_DIR"] = lib
        }
        let extraPaths = [
            "\(NSHomeDirectory())/.local/bin",
            "/opt/homebrew/bin",
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
        ]
        let current = env["PATH"].map { [$0] } ?? []
        env["PATH"] = (extraPaths + current).joined(separator: ":")
        // Parent-death watchdog: hand the core a stdin pipe we hold open for the
        // app's lifetime and tell it to watch stdin for EOF. If the app dies for
        // *any* reason — Quit, Force-Quit (SIGKILL), crash, or debugger stop —
        // the OS closes our write end, the core's stdin reaches EOF, and it
        // exits instead of lingering as an orphan that keeps the socket bound.
        // (`applicationWillTerminate` alone misses all the abnormal exits.)
        env["CLAUDESTUDIO_WATCH_STDIN"] = "1"
        proc.environment = env
        proc.standardInput = Pipe()

        do {
            try proc.run()
            process = proc
            return true
        } catch {
            return false
        }
    }

    /// Find a directory that contains the shipped `tasks/` and `definitions/`
    /// libraries: the app bundle's Resources (shipped) or a checkout above the
    /// binary (dev). Returns nil if neither is found.
    private func locateLibraryDir(binaryPath: String) -> String? {
        let fm = FileManager.default
        func hasLibraries(_ dir: URL) -> Bool {
            var isDir: ObjCBool = false
            let t = dir.appendingPathComponent("tasks").path
            let d = dir.appendingPathComponent("definitions").path
            return fm.fileExists(atPath: t, isDirectory: &isDir) && isDir.boolValue
                && fm.fileExists(atPath: d, isDirectory: &isDir) && isDir.boolValue
        }
        if let resources = Bundle.main.resourceURL, hasLibraries(resources) {
            return resources.path
        }
        var dir = URL(fileURLWithPath: binaryPath).deletingLastPathComponent()
        for _ in 0..<8 {
            if hasLibraries(dir) { return dir.path }
            dir = dir.deletingLastPathComponent()
        }
        return nil
    }

    private func waitForSocket(_ path: String, timeout: TimeInterval) async -> Bool {
        let start = Date()
        while Date().timeIntervalSince(start) < timeout {
            if FileManager.default.fileExists(atPath: path) { return true }
            if let proc = process, !proc.isRunning { return false }
            try? await Task.sleep(nanoseconds: 50_000_000)
        }
        return FileManager.default.fileExists(atPath: path)
    }
}
