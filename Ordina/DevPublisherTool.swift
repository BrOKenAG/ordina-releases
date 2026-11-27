import Foundation

// The publishing contract. Two implementations exist by design:
//   * LocalPublisherTool  — the installed Python engine (this stage);
//   * RemotePublisherService — reserved seam for a future NAS/service, which
//     is intentionally unimplemented and always fails closed.
// The model only talks to this protocol, so adding a remote backend later does
// not change the UI or the tool semantics.

protocol DeveloperPublishing: Sendable {
    var location: String { get }
    func hello() async throws -> DevToolInfo
    func doctor() async throws -> DevDoctor
    func inspect(zip: URL) async throws -> DevInspection
    func localTest(zip: URL) async throws -> DevLocalTest
    func preview(zip: URL, notes: String) async throws -> DevPreview
    func publish(zip: URL, notes: String, fingerprint: String,
                 createRepository: Bool,
                 onEvent: @escaping @Sendable (DevEvent) -> Void) async throws -> DevPublishResult
    func journals() async throws -> [DevJournal]
    func resume(journal: String,
                onEvent: @escaping @Sendable (DevEvent) -> Void) async throws -> DevAction
    func rollback(journal: String, reason: String) async throws -> DevAction
    func history() async throws -> DevHistory
    func lifecyclePlugins() async throws -> [DevLifecyclePlugin]
    func lifecyclePreview(id: String, published: Bool) async throws -> DevLifecyclePreview
    func lifecycleSet(_ preview: DevLifecyclePreview,
                      onEvent: @escaping @Sendable (DevEvent) -> Void) async throws -> DevAction
    func credentialStatus() async throws -> DevCredentialStatus
    func importCredential(file: URL) async throws -> DevCredentialStatus
}

struct RemotePublisherService: DeveloperPublishing {
    var location: String { "remote" }

    private func unavailable() -> Error {
        DevBridgeError.transport("远程发布服务尚未启用；当前版本只支持本地发布工具。")
    }

    func hello() async throws -> DevToolInfo { throw unavailable() }
    func doctor() async throws -> DevDoctor { throw unavailable() }
    func inspect(zip: URL) async throws -> DevInspection { throw unavailable() }
    func localTest(zip: URL) async throws -> DevLocalTest { throw unavailable() }
    func preview(zip: URL, notes: String) async throws -> DevPreview { throw unavailable() }
    func publish(zip: URL, notes: String, fingerprint: String, createRepository: Bool,
                 onEvent: @escaping @Sendable (DevEvent) -> Void) async throws -> DevPublishResult {
        throw unavailable()
    }
    func journals() async throws -> [DevJournal] { throw unavailable() }
    func resume(journal: String,
                onEvent: @escaping @Sendable (DevEvent) -> Void) async throws -> DevAction {
        throw unavailable()
    }
    func rollback(journal: String, reason: String) async throws -> DevAction { throw unavailable() }
    func history() async throws -> DevHistory { throw unavailable() }
    func lifecyclePlugins() async throws -> [DevLifecyclePlugin] { throw unavailable() }
    func lifecyclePreview(id: String, published: Bool) async throws -> DevLifecyclePreview { throw unavailable() }
    func lifecycleSet(_ preview: DevLifecyclePreview,
                      onEvent: @escaping @Sendable (DevEvent) -> Void) async throws -> DevAction { throw unavailable() }
    func credentialStatus() async throws -> DevCredentialStatus { throw unavailable() }
    func importCredential(file: URL) async throws -> DevCredentialStatus { throw unavailable() }
}

// MARK: - process transport

enum DevProcess {
    struct Outcome {
        let result: [String: Any]
        let stderr: String
    }

    /// Run the tool with an explicit argument list. Never a shell string: ZIP
    /// names, notes and paths are passed as separate argv entries.
    static func run(executable: URL,
                    arguments: [String],
                    environment: [String: String],
                    timeout: TimeInterval,
                    onEvent: (@Sendable (DevEvent) -> Void)? = nil) throws -> Outcome {
        let process = Process()
        process.executableURL = executable
        process.arguments = arguments
        var merged = ProcessInfo.processInfo.environment
        for key in merged.keys where key.hasPrefix("ORDINA_DEV_") {
            merged.removeValue(forKey: key)
        }
        merged.merge(environment) { _, new in new }
        merged["PYTHONUNBUFFERED"] = "1"
        process.environment = merged

        let output = Pipe()
        let errors = Pipe()
        process.standardOutput = output
        process.standardError = errors

        let queue = DispatchQueue(label: "ordina.developer.tool")
        var buffer = Data()
        var result: [String: Any]?
        var failure: String?

        func handle(_ line: Data) {
            guard !line.isEmpty,
                  let object = try? JSONSerialization.jsonObject(with: line) as? [String: Any]
            else { return }
            switch object["type"] as? String {
            case "event":
                guard let name = object["event"] as? String else { return }
                if name == "stage", let stage = object["stage"] as? String,
                   let state = object["state"] as? String {
                    onEvent?(DevEvent(kind: .stage(
                        name: stage, state: state,
                        index: object["index"] as? Int ?? 0,
                        total: object["total"] as? Int ?? 0)))
                } else if name == "log", let message = object["message"] as? String {
                    onEvent?(DevEvent(kind: .log(message)))
                } else {
                    onEvent?(DevEvent(kind: .other))
                }
            case "result":
                result = object
            default:
                break
            }
        }

        output.fileHandleForReading.readabilityHandler = { handle_ in
            let chunk = handle_.availableData
            guard !chunk.isEmpty else { return }
            queue.sync {
                buffer.append(chunk)
                while let index = buffer.firstIndex(of: 0x0A) {
                    let line = Data(buffer[buffer.startIndex..<index])
                    buffer.removeSubrange(buffer.startIndex...index)
                    handle(line)
                }
                if buffer.count > DevProtocol.maximumLine { buffer.removeAll() }
            }
        }
        errors.fileHandleForReading.readabilityHandler = { handle_ in
            let chunk = handle_.availableData
            guard !chunk.isEmpty, let text = String(data: chunk, encoding: .utf8) else { return }
            queue.sync {
                failure = String(((failure ?? "") + text).suffix(4000))
            }
        }

        let finished = DispatchSemaphore(value: 0)
        process.terminationHandler = { _ in finished.signal() }
        do {
            try process.run()
        } catch {
            output.fileHandleForReading.readabilityHandler = nil
            errors.fileHandleForReading.readabilityHandler = nil
            throw DevBridgeError.transport("无法启动发布工具：\(error.localizedDescription)")
        }
        if finished.wait(timeout: .now() + timeout) == .timedOut {
            process.terminate()
            _ = finished.wait(timeout: .now() + 5)
            output.fileHandleForReading.readabilityHandler = nil
            errors.fileHandleForReading.readabilityHandler = nil
            throw DevBridgeError.transport("发布工具超时（\(Int(timeout)) 秒），事务记录已保留，可稍后恢复。")
        }
        output.fileHandleForReading.readabilityHandler = nil
        errors.fileHandleForReading.readabilityHandler = nil
        queue.sync {
            if !buffer.isEmpty { handle(Data(buffer)) ; buffer.removeAll() }
        }
        guard let result else {
            throw DevBridgeError.transport(
                "发布工具没有返回结构化结果。\((failure ?? "").suffix(400))")
        }
        return Outcome(result: result, stderr: failure ?? "")
    }
}

struct LocalPublisherTool: DeveloperPublishing {
    let executable: URL
    let configuration: DeveloperConfiguration
    var journalPath: URL? = DeveloperPaths.journals
    var recordsPath: URL? = DeveloperPaths.records

    var location: String { executable.path }

    /// python3 <pyz> …  or  <executable> …
    private var invocation: (URL, [String]) {
        if executable.pathExtension == "pyz" {
            return (DevBridge.python, [executable.path])
        }
        return (executable, [])
    }

    private func environment() -> [String: String] {
        var values: [String: String] = [
            "ORDINA_DEV_CLIENT_VERSION": DevBridge.clientVersion,
        ]
        // GUI-launched apps inherit launchd's PATH, which omits Homebrew.
        // The publisher shells out to `gh` for read-only authority checks;
        // give every invocation the standard CLI locations without altering
        // credentials, backend selection or any authorization decision.
        let inherited = ProcessInfo.processInfo.environment["PATH"] ?? ""
        var search = inherited.split(separator: ":").map(String.init)
        for directory in ["/opt/homebrew/bin", "/usr/local/bin"] where !search.contains(directory) {
            search.append(directory)
        }
        values["PATH"] = search.joined(separator: ":")
        if !configuration.repositoryPath.isEmpty {
            values["ORDINA_DEV_REPO"] = configuration.repositoryPath
        }
        if let journalPath { values["ORDINA_DEV_JOURNAL_DIR"] = journalPath.path }
        if let recordsPath { values["ORDINA_DEV_RECORDS"] = recordsPath.path }
        if DevBridge.isLab {
            // Only a lab/debug build forwards lab switches; a release build has
            // no way to point the engine at an isolated backend.
            let process = ProcessInfo.processInfo.environment
            for key in ["ORDINA_DEV_LAB", "ORDINA_DEV_BACKEND", "ORDINA_DEV_LAB_ROOT",
                        "ORDINA_DEV_CREDENTIALS", "ORDINA_DEV_MANIFEST",
                        "ORDINA_DEV_SIGN_KEY", "ORDINA_DEV_LAB_WRITE_SOURCE",
                        "ORDINA_DEV_LAB_FAIL_AFTER", "ORDINA_DEV_LAB_BAD_ASSET",
                        "ORDINA_DEV_LAB_ENOSPC"] {
                if let value = process[key] { values[key] = value }
            }
            values["ORDINA_DEV_LAB"] = "1"
        }
        return values
    }

    private func call(_ arguments: [String], timeout: TimeInterval = 120,
                      onEvent: (@Sendable (DevEvent) -> Void)? = nil) throws -> [String: Any] {
        let (program, prefix) = invocation
        guard FileManager.default.isExecutableFile(atPath: program.path) else {
            throw DevBridgeError.toolMissing
        }
        let outcome = try DevProcess.run(executable: program,
                                         arguments: prefix + arguments,
                                         environment: environment(),
                                         timeout: timeout,
                                         onEvent: onEvent)
        if let error = outcome.result["error"] as? [String: Any] {
            var details: [String: String] = [:]
            for (key, value) in (error["details"] as? [String: Any]) ?? [:] {
                details[key] = String(describing: value)
            }
            throw DevFailure(code: error["code"] as? String ?? "tool.error",
                             message: error["message"] as? String ?? "发布工具执行失败。",
                             stage: error["stage"] as? String,
                             journal: error["journal"] as? String,
                             details: details)
        }
        guard outcome.result["ok"] as? Bool == true, let data = outcome.result["data"] else {
            throw DevBridgeError.response("发布工具返回了无法识别的结果。")
        }
        return data as? [String: Any] ?? ["value": data]
    }

    private func value<T: Decodable>(_ type: T.Type, from data: [String: Any]) throws -> T {
        try DevBridge.decode(type, from: data)
    }

    func authorizeIdentity() async throws {
        try await Task.detached(priority: .userInitiated) {
            let result = try call(["auth-authorize"], timeout: 90)
            guard result["authorized"] as? Bool == true else {
                throw DevBridgeError.response("无法确认开发者发布权限。")
            }
        }.value
    }

    func helloSync() throws -> DevToolInfo {
        try value(DevToolInfo.self, from: call(["hello"], timeout: 20))
    }

    func hello() async throws -> DevToolInfo {
        try await Task.detached(priority: .userInitiated) { try helloSync() }.value
    }

    func doctor() async throws -> DevDoctor {
        try await Task.detached(priority: .userInitiated) {
            try value(DevDoctor.self, from: call(["doctor"], timeout: 60))
        }.value
    }

    func inspect(zip: URL) async throws -> DevInspection {
        try await Task.detached(priority: .userInitiated) {
            try value(DevInspection.self, from: call(["inspect", "--zip", zip.path]))
        }.value
    }

    func localTest(zip: URL) async throws -> DevLocalTest {
        let directory = DeveloperPaths.root.appendingPathComponent("LocalTest", isDirectory: true)
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        return try await Task.detached(priority: .userInitiated) {
            try value(DevLocalTest.self, from: call(
                ["local-test", "--zip", zip.path, "--work-dir", directory.path]))
        }.value
    }

    func preview(zip: URL, notes: String) async throws -> DevPreview {
        try await Task.detached(priority: .userInitiated) {
            try value(DevPreview.self, from: call(
                ["preview", "--zip", zip.path, "--notes", notes]))
        }.value
    }

    func publish(zip: URL, notes: String, fingerprint: String, createRepository: Bool,
                 onEvent: @escaping @Sendable (DevEvent) -> Void) async throws -> DevPublishResult {
        try await Task.detached(priority: .userInitiated) {
            var arguments = ["publish", "--zip", zip.path, "--notes", notes,
                             "--execute", "--expect-fingerprint", fingerprint]
            if createRepository { arguments.append("--create-repository") }
            return try value(DevPublishResult.self,
                             from: call(arguments, timeout: 900, onEvent: onEvent))
        }.value
    }

    func journals() async throws -> [DevJournal] {
        struct Payload: Decodable { let journals: [DevJournal] }
        return try await Task.detached(priority: .utility) {
            let data = try call(["status", "--all"])
            return try value(Payload.self, from: data).journals
        }.value
    }

    func resume(journal: String,
                onEvent: @escaping @Sendable (DevEvent) -> Void) async throws -> DevAction {
        try await Task.detached(priority: .userInitiated) {
            try value(DevAction.self,
                      from: call(["resume", "--journal", journal], timeout: 900,
                                 onEvent: onEvent))
        }.value
    }

    func rollback(journal: String, reason: String) async throws -> DevAction {
        try await Task.detached(priority: .userInitiated) {
            try value(DevAction.self,
                      from: call(["rollback", "--journal", journal, "--reason", reason],
                                 timeout: 300))
        }.value
    }

    func history() async throws -> DevHistory {
        try await Task.detached(priority: .utility) {
            try value(DevHistory.self, from: call(["history"]))
        }.value
    }

    func lifecyclePlugins() async throws -> [DevLifecyclePlugin] {
        struct Payload: Decodable { let plugins: [DevLifecyclePlugin] }
        return try await Task.detached(priority: .utility) {
            try value(Payload.self, from: call(["lifecycle-list"], timeout: 60)).plugins
        }.value
    }

    func lifecyclePreview(id: String, published: Bool) async throws -> DevLifecyclePreview {
        try await Task.detached(priority: .userInitiated) {
            try value(DevLifecyclePreview.self, from: call(["lifecycle-preview", "--plugin-id", id,
                                                             "--published", published ? "yes" : "no"], timeout: 120))
        }.value
    }

    func lifecycleSet(_ preview: DevLifecyclePreview,
                      onEvent: @escaping @Sendable (DevEvent) -> Void) async throws -> DevAction {
        try await Task.detached(priority: .userInitiated) {
            try value(DevAction.self, from: call(["lifecycle-set", "--plugin-id", preview.id,
                                                 "--published", preview.published ? "yes" : "no",
                                                 "--expect-fingerprint", preview.fingerprint, "--execute"],
                                                timeout: 900, onEvent: onEvent))
        }.value
    }

    func credentialStatus() async throws -> DevCredentialStatus {
        try await Task.detached(priority: .utility) {
            try value(DevCredentialStatus.self, from: call(["credential-status"], timeout: 30))
        }.value
    }

    func importCredential(file: URL) async throws -> DevCredentialStatus {
        try await Task.detached(priority: .userInitiated) {
            _ = try call(["credential-import", "--file", file.path], timeout: 60)
            return try value(DevCredentialStatus.self,
                             from: call(["credential-status"], timeout: 30))
        }.value
    }
}
