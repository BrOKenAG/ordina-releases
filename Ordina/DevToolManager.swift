import Foundation
import CryptoKit

// The publishing tool is an independently versioned component with its own
// signed manifest, its own directory and its own rollback. It is never placed
// inside ordina.app: replacing it must not invalidate the app signature.
//
// Layout (all outside the app bundle):
//   Developer/Publisher/versions/<version>/ordina-publisher.pyz
//   Developer/Publisher/installed.json
//   Developer/journals/            transaction journals (survive updates)
//   Developer/records.jsonl        release history (survives updates)
//
// The Keychain credential is never copied here; the tool reads it itself.

struct DevToolEntry: Decodable, Equatable, Sendable {
    let version: String
    let protocolVersion: Int
    let minClientVersion: String
    let asset: String
    let sha256: String
    let size: Int
    let tag: String
    let url: String
    let notes: String

    enum CodingKeys: String, CodingKey {
        case version, asset, sha256, size, tag, url, notes, minClientVersion
        case protocolVersion = "protocol"
    }
}

struct DevToolManifest: Decodable, Equatable, Sendable {
    let format: Int
    let tool: String
    let channel: String
    let versions: [DevToolEntry]
}

struct DevToolInstallation: Codable, Equatable, Sendable {
    var version: String
    var path: String
    var sha256: String
    var protocolVersion: Int
    var installedAt: TimeInterval
    var previous: [String]
    var digests: [String: String]
}

enum DevToolError: LocalizedError {
    case manifest(String)
    case download(String)
    case integrity(String)
    case healthCheck(String)
    case pending(Int)
    case noRollback

    var errorDescription: String? {
        switch self {
        case .manifest(let message): return message
        case .download(let message): return message
        case .integrity(let message): return "发布工具完整性校验失败：\(message)"
        case .healthCheck(let message): return "新版本发布工具自检失败，已保留旧版本：\(message)"
        case .pending(let count):
            return "存在 \(count) 个未完成的发布事务，请先完成或回滚后再更新发布工具。"
        case .noRollback: return "没有可回滚的历史版本。"
        }
    }
}

enum DevToolManager {
    private static let maximumToolSize = 8_000_000
    private static let maximumVersions = 3

    // MARK: paths and state

    static func installation() -> DevToolInstallation? {
        guard let data = try? Data(contentsOf: DeveloperPaths.installationFile),
              let value = try? JSONDecoder().decode(DevToolInstallation.self, from: data),
              FileManager.default.fileExists(atPath: value.path)
        else { return nil }
        return value
    }

    static func installedVersions() -> [String] {
        (try? FileManager.default.contentsOfDirectory(atPath: DeveloperPaths.versions.path))?
            .filter { !$0.hasPrefix(".") }
            .sorted(by: { $0.compare($1, options: .numeric) == .orderedDescending }) ?? []
    }

    @discardableResult
    static func forgetInstallation() -> Bool {
        try? FileManager.default.removeItem(at: DeveloperPaths.installationFile)
        return true
    }

    static func digest(of path: URL) -> String? {
        guard let data = try? Data(contentsOf: path) else { return nil }
        return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    /// The active tool is re-hashed before every session; a tampered file is
    /// treated as not installed rather than silently executed.
    static func verifiedPath(_ configuration: DeveloperConfiguration) -> URL? {
        if !configuration.toolPath.isEmpty {
            let url = URL(fileURLWithPath: (configuration.toolPath as NSString).expandingTildeInPath)
            guard FileManager.default.isExecutableFile(atPath: url.path) else { return nil }
            return url
        }
        guard let record = installation() else { return nil }
        let url = URL(fileURLWithPath: record.path)
        guard digest(of: url) == record.sha256 else { return nil }
        return url
    }

    static func integrityError(_ configuration: DeveloperConfiguration) -> String? {
        if !configuration.toolPath.isEmpty {
            let url = URL(fileURLWithPath: (configuration.toolPath as NSString).expandingTildeInPath)
            guard FileManager.default.fileExists(atPath: url.path) else {
                return "所选发布工具不存在。"
            }
            guard FileManager.default.isExecutableFile(atPath: url.path) else {
                return "所选发布工具不可执行。"
            }
            return nil
        }
        guard let record = installation() else { return nil }
        guard digest(of: URL(fileURLWithPath: record.path)) == record.sha256 else {
            return "发布工具文件已被修改，请重新安装 \(record.version)。"
        }
        return nil
    }

    // MARK: manifest

    static func validVersion(_ value: String) -> Bool {
        value.range(of: #"^[0-9]+(\.[0-9]+){1,3}$"#, options: .regularExpression) != nil
    }

    private static func validManifestURL(_ url: URL) -> Bool {
        if DevBridge.isLab, url.host == "127.0.0.1" || url.host == "localhost" {
            return url.scheme == "http"
        }
        return url.scheme == "https" && url.host == "brokenag.github.io"
            && url.path == "/ordina-releases/dev-tool/manifest.json"
            && url.user == nil && url.password == nil && url.query == nil
    }

    private static func validAsset(_ entry: DevToolEntry) -> Bool {
        guard let url = URL(string: entry.url) else { return false }
        let prefix = "/BrOKenAG/ordina-releases/releases/download/dev-tool-v"
        let path = url.path
        guard path.hasPrefix(prefix), !path.contains(".."), !path.contains("//"),
              path.hasSuffix("/" + entry.asset) else { return false }
        if DevBridge.isLab, url.host == "127.0.0.1" || url.host == "localhost" {
            return url.scheme == "http"
        }
        return url.scheme == "https" && url.host == "github.com"
            && url.user == nil && url.password == nil && url.query == nil
            && url.pathExtension == "pyz"
    }

    /// Download and verify the signed manifest. The key is pinned in the app;
    /// the tool never supplies its own trust anchor.
    static func manifest(forceRefresh: Bool = false) throws -> DevToolManifest {
        let url = DevBridge.manifestURL
        guard validManifestURL(url) else {
            throw DevToolError.manifest("发布工具清单地址不受信任。")
        }
        let data = try PluginUpdates.download(url, maxBytes: PluginProtocol.maximumManifest,
                                              disableProxy: DevBridge.isLab)
        let payload: Data
        do {
            payload = try PluginProtocol.verifiedPayload(
                data, publicKey: DevBridge.toolManifestPublicKey)
        } catch {
            throw DevToolError.manifest("发布工具清单签名无效。")
        }
        let value: DevToolManifest
        do {
            value = try JSONDecoder().decode(DevToolManifest.self, from: payload)
        } catch {
            throw DevToolError.manifest("发布工具清单格式无效。")
        }
        guard value.format == 1, value.tool == DevProtocol.tool, !value.versions.isEmpty else {
            throw DevToolError.manifest("发布工具清单格式无效。")
        }
        for entry in value.versions {
            guard validVersion(entry.version), entry.protocolVersion == DevProtocol.version,
                  validVersion(entry.minClientVersion),
                  entry.sha256.range(of: #"^[0-9a-f]{64}$"#, options: .regularExpression) != nil,
                  entry.size > 0, entry.size <= maximumToolSize,
                  entry.asset.hasSuffix(".pyz"), validAsset(entry)
            else { throw DevToolError.manifest("发布工具清单包含无效条目。") }
        }
        return value
    }

    static func compatible(_ entry: DevToolEntry) -> Bool {
        PluginProtocol.supports(clientVersion: DevBridge.clientVersion,
                                minimum: entry.minClientVersion)
    }

    // MARK: install / update / rollback

    static func install(_ entry: DevToolEntry,
                        manifestVersion: Int = DevProtocol.version) throws -> DevToolInstallation {
        let pending = PendingTransactions.count()
        if pending > 0 { throw DevToolError.pending(pending) }
        let manager = FileManager.default
        try manager.createDirectory(at: DeveloperPaths.versions, withIntermediateDirectories: true)
        try manager.createDirectory(at: DeveloperPaths.downloads, withIntermediateDirectories: true)

        let download = DeveloperPaths.downloads
            .appendingPathComponent("\(entry.version)-\(UUID().uuidString).pyz")
        defer { try? manager.removeItem(at: download) }
        let data = try PluginUpdates.download(URL(string: entry.url)!, maxBytes: maximumToolSize,
                                              disableProxy: DevBridge.isLab)
        guard data.count == entry.size, PluginProtocol.digest(data) == entry.sha256 else {
            throw DevToolError.integrity("下载内容与清单不一致。")
        }
        guard data.prefix(4) == Data([0x50, 0x4B, 0x03, 0x04]) else {
            throw DevToolError.integrity("发布工具不是有效的归档文件。")
        }
        try data.write(to: download, options: .atomic)

        let directory = DeveloperPaths.versions.appendingPathComponent(entry.version,
                                                                       isDirectory: true)
        try manager.createDirectory(at: directory, withIntermediateDirectories: true)
        let target = directory.appendingPathComponent("ordina-publisher.pyz")
        if manager.fileExists(atPath: target.path) { try manager.removeItem(at: target) }
        try manager.moveItem(at: download, to: target)

        let previous = installation()
        var digests = previous?.digests ?? [:]
        digests[entry.version] = entry.sha256
        let history = ((previous?.previous ?? []) + [previous?.version].compactMap { $0 })
            .filter { $0 != entry.version }
        let record = DevToolInstallation(
            version: entry.version, path: target.path, sha256: entry.sha256,
            protocolVersion: entry.protocolVersion,
            installedAt: Date().timeIntervalSince1970,
            previous: Array(history.suffix(maximumVersions)), digests: digests)
        try JSONEncoder().encode(record).write(to: DeveloperPaths.installationFile, options: .atomic)
        prune(excluding: record)
        return record
    }

    static func rollback(to version: String? = nil) throws -> DevToolInstallation {
        let pending = PendingTransactions.count()
        if pending > 0 { throw DevToolError.pending(pending) }
        guard let current = installation() else { throw DevToolError.noRollback }
        let target = version ?? current.previous.last
        guard let target else { throw DevToolError.noRollback }
        let path = DeveloperPaths.versions.appendingPathComponent(target, isDirectory: true)
            .appendingPathComponent("ordina-publisher.pyz")
        guard let expected = current.digests[target], digest(of: path) == expected else {
            throw DevToolError.integrity("历史版本 \(target) 缺失或已损坏。")
        }
        var record = current
        record.version = target
        record.path = path.path
        record.sha256 = expected
        record.installedAt = Date().timeIntervalSince1970
        record.previous = current.previous.filter { $0 != target }
        if current.version != target { record.previous.append(current.version) }
        try JSONEncoder().encode(record).write(to: DeveloperPaths.installationFile, options: .atomic)
        return record
    }

    private static func prune(excluding record: DevToolInstallation) {
        let keep = Set([record.version] + record.previous + [record.version])
        for name in installedVersions() where !keep.contains(name) {
            try? FileManager.default.removeItem(
                at: DeveloperPaths.versions.appendingPathComponent(name, isDirectory: true))
        }
    }

    /// Health check after install: the new tool must answer with the same
    /// version and a compatible protocol before it becomes the active one.
    static func healthCheck(path: URL, expected: DevToolEntry) throws {
        let tool = LocalPublisherTool(executable: path, configuration: DeveloperConfiguration(),
                                      journalPath: nil, recordsPath: nil)
        let info: DevToolInfo
        do {
            info = try tool.helloSync()
        } catch {
            throw DevToolError.healthCheck(error.localizedDescription)
        }
        guard info.version == expected.version else {
            throw DevToolError.healthCheck("版本号不符：\(info.version)")
        }
        guard info.protocolVersion == DevProtocol.version else {
            throw DevToolError.healthCheck("协议版本不符：\(info.protocolVersion)")
        }
    }
}

enum PendingTransactions {
    /// Count unfinished journals without launching the tool: this must work
    /// even when the tool is broken, and it is what blocks a tool update.
    static func count() -> Int {
        let manager = FileManager.default
        guard let files = try? manager.contentsOfDirectory(at: DeveloperPaths.journals,
                                                           includingPropertiesForKeys: nil)
        else { return 0 }
        var total = 0
        for file in files where file.pathExtension == "json"
            && !file.lastPathComponent.hasSuffix(".meta.json") {
            guard let data = try? Data(contentsOf: file),
                  let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let status = object["status"] as? String else { continue }
            if status == "preparing" || status == "publishing" { total += 1 }
        }
        return total
    }
}
