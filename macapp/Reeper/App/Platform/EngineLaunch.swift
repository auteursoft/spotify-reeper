import Foundation

/// Where the bundled Python engine lives, and where Reeper keeps its data.
enum EngineLaunch {
    /// Reeper's data folder: moods, analysis cache, downloaded models. In the App Store
    /// (sandboxed) build this is inside ~/Library/Containers/com.auteursoft.reeper/.
    static var dataDirectory: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        let dir = base.appendingPathComponent("Reeper", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    /// Audio models shipped inside the app, if the build included them.
    static var bundledModels: URL? {
        Bundle.main.resourceURL?.appendingPathComponent("models", isDirectory: true)
    }

    /// How to start the engine: the Python runtime bundled in Contents/Resources/python,
    /// or (Debug builds only) a Python and source checkout named by the REEPER_PYTHON and
    /// REEPER_SOURCE environment variables, which you can set in the Xcode scheme.
    static func configuration() -> EngineClient.Launch? {
        var env: [String: String] = [
            "PYTHONDONTWRITEBYTECODE": "1",  // the app bundle is read-only and signed
            "PYTHONNOUSERSITE": "1",
            "PYTHONUNBUFFERED": "1",
            "TF_CPP_MIN_LOG_LEVEL": "3",
            "HOME": NSHomeDirectory(),
            "TMPDIR": NSTemporaryDirectory(),
            "LANG": "en_US.UTF-8",
        ]
        if let resources = Bundle.main.resourceURL {
            let python = resources.appendingPathComponent("python/bin/python3")
            if FileManager.default.isExecutableFile(atPath: python.path) {
                env["PYTHONHOME"] = resources.appendingPathComponent("python").path
                env["PYTHONPATH"] = resources.appendingPathComponent("engine").path
                return .init(executable: python, arguments: ["-s", "-m", "reeper_engine"], environment: env,
                             currentDirectory: dataDirectory)
            }
        }
        #if DEBUG
        let info = ProcessInfo.processInfo.environment
        if let python = info["REEPER_PYTHON"], let source = info["REEPER_SOURCE"] {
            env["PYTHONPATH"] = source
            return .init(executable: URL(fileURLWithPath: python), arguments: ["-m", "reeper_engine"],
                         environment: env, currentDirectory: dataDirectory)
        }
        #endif
        return nil
    }
}
