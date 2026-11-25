import Foundation

// A standalone snapshot of the client's developer-centre transport. It links
// the same bridge, tool manager and model sources as the app, is compiled once,
// and then drives the real publishing tool inside an isolated lab.
//
// No plugin id, repository or credential is compiled in: everything comes from
// the environment and from the tool's structured output.
@main
struct DeveloperCenterHarness {
    struct Failure: Error, CustomStringConvertible {
        let description: String
    }

    static func main() {
        let arguments = Array(CommandLine.arguments.dropFirst())
        guard let command = arguments.first else {
            emit(["ok": false, "error": "usage: harness <command>"])
            exit(2)
        }
        do {
            switch command {
            case "status":
                emit(try statusPayload())
            case "tool-check":
                emit(try toolCheck())
            case "tool-install":
                let version = try value(after: command, in: arguments)
                emit(try toolInstall(version))
            case "tool-rollback":
                emit(try toolRollback())
            case "inspect":
                let zip = try value(after: command, in: arguments)
                emit(try inspect(zip))
            case "local-test":
                let zip = try value(after: command, in: arguments)
                emit(try localTest(zip))
            case "local-uninstall":
                let id = try value(after: command, in: arguments)
                emit(try localUninstall(id))
            case "preview":
                let zip = try value(after: command, in: arguments)
                emit(try preview(zip, notes: flag("--notes", arguments) ?? ""))
            case "publish":
                let zip = try value(after: command, in: arguments)
                emit(try publish(zip,
                                 notes: flag("--notes", arguments) ?? "",
                                 fingerprint: flag("--fingerprint", arguments),
                                 createRepository: arguments.contains("--create-repository")))
            case "resume":
                emit(try resume(try value(after: command, in: arguments)))
            case "rollback":
                let journal = try value(after: command, in: arguments)
                emit(try rollback(journal, reason: flag("--reason", arguments) ?? ""))
            case "journal-list":
                emit(["ok": true, "journals": journals().map(describe)])
            case "pending":
                emit(["ok": true, "pending": PendingTransactions.count()])
            default:
                throw Failure(description: "unknown command: \(command)")
            }
        } catch let failure as DevFailure {
            emit(["ok": false, "code": failure.code, "error": failure.message,
                  "stage": failure.stage ?? NSNull(),
                  "journal": failure.journal ?? NSNull()])
            exit(1)
        } catch {
            emit(["ok": false, "code": "harness", "error": error.localizedDescription])
            exit(1)
        }
    }

    // MARK: helpers

    static func value(after command: String, in arguments: [String]) throws -> String {
        guard arguments.count >= 2, !arguments[1].hasPrefix("--") else {
            throw Failure(description: "missing argument for \(command)")
        }
        return arguments[1]
    }

    static func flag(_ name: String, _ arguments: [String]) -> String? {
        guard let index = arguments.firstIndex(of: name), index + 1 < arguments.count else {
            return nil
        }
        return arguments[index + 1]
    }

    static func emit(_ payload: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: payload),
              let text = String(data: data, encoding: .utf8) else { return }
        print(text)
    }

    static func configuration() -> DeveloperConfiguration {
        var value = DeveloperConfiguration()
        value.enabled = true
        value.repositoryPath = ProcessInfo.processInfo.environment["ORDINA_DEV_REPO"] ?? ""
        value.toolPath = ProcessInfo.processInfo.environment["ORDINA_DEV_TOOL"] ?? ""
        return value
    }

    static func tool() throws -> LocalPublisherTool {
        guard let path = DeveloperToolLocator.resolve(configuration()) else {
            throw Failure(description: "发布工具未安装")
        }
        return LocalPublisherTool(executable: path, configuration: configuration())
    }

    static func journals() -> [DevJournal] {
        DeveloperCenterModel.localJournals()
    }

    static func describe(_ journal: DevJournal) -> [String: Any] {
        ["journal": journal.journal, "status": journal.status,
         "plugin": journal.plugin ?? NSNull(), "version": journal.version ?? NSNull(),
         "repository": journal.repository ?? NSNull(),
         "resumable": journal.resumable, "rollbackable": journal.rollbackable,
         "repositories": Array(journal.repositories.keys).sorted()]
    }

    // MARK: commands

    static func statusPayload() throws -> [String: Any] {
        let record = DevToolManager.installation()
        var payload: [String: Any] = [
            "ok": true,
            "installed": record?.version ?? NSNull(),
            "integrity": record.map { DevToolManager.digest(of: URL(fileURLWithPath: $0.path)) == $0.sha256 }
                ?? NSNull(),
            "previous": record?.previous ?? [],
            "pending": PendingTransactions.count(),
            "journals": journals().map(describe),
        ]
        if let tool = try? tool() {
            payload["hello"] = try? tool.helloSync().version
            payload["protocol"] = (try? tool.helloSync().protocolVersion) ?? NSNull()
        }
        payload["records"] = DeveloperCenterModel.localRecords().count
        return payload
    }

    static func toolCheck() throws -> [String: Any] {
        let manifest = try DevToolManager.manifest()
        return ["ok": true,
                "versions": manifest.versions.map {
                    ["version": $0.version, "protocol": $0.protocolVersion,
                     "compatible": DevToolManager.compatible($0)]
                }]
    }

    static func toolInstall(_ version: String) throws -> [String: Any] {
        let manifest = try DevToolManager.manifest()
        guard let entry = manifest.versions.first(where: { $0.version == version }) else {
            throw Failure(description: "清单中没有 \(version)")
        }
        let record = try DevToolManager.install(entry)
        try DevToolManager.healthCheck(path: URL(fileURLWithPath: record.path), expected: entry)
        return ["ok": true, "installed": record.version, "previous": record.previous,
                "sha256": record.sha256]
    }

    static func toolRollback() throws -> [String: Any] {
        let record = try DevToolManager.rollback()
        return ["ok": true, "installed": record.version]
    }

    static func inspect(_ zip: String) throws -> [String: Any] {
        let value = try tool().inspectBlocking(zip: URL(fileURLWithPath: zip))
        return ["ok": true,
                "id": value.package.id, "name": value.package.name,
                "version": value.package.version, "action": value.action,
                "previous": value.existing?.version ?? NSNull(),
                "compatible": value.compatibility.ok,
                "protocol": value.package.protocolVersion,
                "capabilities": value.package.capabilities,
                "fileCount": value.package.fileCount,
                "minClientVersion": value.package.minClientVersion,
                "repository": value.repository.slug]
    }

    static func localTest(_ zip: String) throws -> [String: Any] {
        let result = try tool().localTestBlocking(zip: URL(fileURLWithPath: zip))
        let data = try Data(contentsOf: URL(fileURLWithPath: result.bundlePath))
        let files = try PluginProtocol.unpack(data, release: result.release)
        try PluginInstaller.install(result.release, files: files, environment: .current)
        let installed = PluginInstaller.state(result.release.descriptor.id, environment: .current)
        return ["ok": true, "installed": installed?.version ?? NSNull(),
                "files": installed?.files.count ?? 0,
                "version": result.version, "action": result.action]
    }

    static func localUninstall(_ id: String) throws -> [String: Any] {
        guard let record = PluginInstaller.state(id, environment: .current) else {
            throw Failure(description: "没有安装记录")
        }
        try PluginInstaller.uninstall(record.descriptor, environment: .current)
        let remaining = PluginInstaller.state(id, environment: .current)
        return ["ok": true, "uninstalled": remaining == nil,
                "userDataKept": FileManager.default.fileExists(
                    atPath: PluginInstaller.supportDirectory(
                        record.descriptor, environment: .current)?.path ?? "/nonexistent")]
    }

    static func preview(_ zip: String, notes: String) throws -> [String: Any] {
        let value = try tool().previewBlocking(zip: URL(fileURLWithPath: zip), notes: notes)
        return ["ok": true,
                "version": value.plugin.version, "action": value.plugin.action,
                "repository": value.plugin.repository, "tag": value.plugin.tag,
                "fingerprint": value.fingerprint,
                "blockers": value.blockers.map(\.message),
                "checks": value.checks.map { ["id": $0.id, "ok": $0.ok, "message": $0.message] },
                "files": ["added": value.files.added.count,
                          "changed": value.files.changed.count,
                          "removed": value.files.removed.count],
                "feeds": value.feeds.count,
                "assetSize": value.asset.size,
                "repositoryExists": value.repository.exists ?? NSNull(),
                "steps": value.repositorySteps.map(\.id)]
    }

    static func publish(_ zip: String, notes: String, fingerprint: String?,
                        createRepository: Bool) throws -> [String: Any] {
        let value = try tool().previewBlocking(zip: URL(fileURLWithPath: zip), notes: notes)
        let result = try tool().publishBlocking(
            zip: URL(fileURLWithPath: zip), notes: notes,
            fingerprint: fingerprint ?? value.fingerprint,
            createRepository: createRepository)
        return ["ok": true, "journal": result.journal, "plugin": result.plugin,
                "version": result.version, "repository": result.repository,
                "tag": result.tag, "stages": result.stages ?? []]
    }

    static func resume(_ journal: String) throws -> [String: Any] {
        let action = try tool().resumeBlocking(journal: journal)
        return ["ok": true, "status": action.status, "journal": action.journal,
                "plugin": action.plugin ?? NSNull(), "version": action.version ?? NSNull()]
    }

    static func rollback(_ journal: String, reason: String) throws -> [String: Any] {
        let action = try tool().rollbackBlocking(journal: journal, reason: reason)
        return ["ok": true, "status": action.status, "journal": action.journal]
    }
}

// Synchronous wrappers around the async contract, so the harness stays a plain
// command-line program while exercising the exact same client code paths.
extension LocalPublisherTool {
    func inspectBlocking(zip: URL) throws -> DevInspection { try awaitSync { try await inspect(zip: zip) } }
    func localTestBlocking(zip: URL) throws -> DevLocalTest { try awaitSync { try await localTest(zip: zip) } }
    func previewBlocking(zip: URL, notes: String) throws -> DevPreview {
        try awaitSync { try await preview(zip: zip, notes: notes) }
    }
    func publishBlocking(zip: URL, notes: String, fingerprint: String,
                         createRepository: Bool) throws -> DevPublishResult {
        try awaitSync {
            try await publish(zip: zip, notes: notes, fingerprint: fingerprint,
                              createRepository: createRepository) { _ in }
        }
    }
    func resumeBlocking(journal: String) throws -> DevAction {
        try awaitSync { try await resume(journal: journal) { _ in } }
    }
    func rollbackBlocking(journal: String, reason: String) throws -> DevAction {
        try awaitSync { try await rollback(journal: journal, reason: reason) }
    }

    private func awaitSync<T>(_ body: @escaping () async throws -> T) throws -> T {
        let semaphore = DispatchSemaphore(value: 0)
        var outcome: Result<T, Error>?
        Task.detached {
            do { outcome = .success(try await body()) }
            catch { outcome = .failure(error) }
            semaphore.signal()
        }
        semaphore.wait()
        switch outcome {
        case .success(let value): return value
        case .failure(let error): throw error
        case nil: throw Failure(description: "没有返回结果")
        }
    }

    private struct Failure: Error, CustomStringConvertible {
        let description: String
    }
}
