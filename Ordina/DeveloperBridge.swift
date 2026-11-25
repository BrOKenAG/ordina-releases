import Foundation

// The developer publishing bridge. Everything here is transport: it launches
// the locally installed publishing tool with an explicit argument list and
// decodes its versioned JSON contract. No release logic lives in the client.
//
// Trust boundaries:
//  * the tool is never bundled in ordina.app and never runs unless a developer
//    has installed it and enabled the developer centre;
//  * no credential, token or private key is embedded in the app;
//  * the client only *displays* authorisation state; the tool verifies the
//    signed credential itself before any publish;
//  * the lab manifest override is compiled out of release builds.

enum DevProtocol {
    static let version = 1
    static let minimumToolClient = "1.10.0"
    static let tool = "ordina-publisher"
    static let maximumLine = 4_000_000
}

struct DevFailure: Error, LocalizedError, Equatable {
    let code: String
    let message: String
    var stage: String? = nil
    var journal: String? = nil
    var details: [String: String] = [:]

    var errorDescription: String? { message }

    var isRecoverable: Bool {
        ["feeds", "push", "postflight"].contains(stage ?? "")
    }
}

enum DevBridgeError: LocalizedError {
    case toolMissing
    case toolNotExecutable(String)
    case toolIncompatible(required: String, found: String)
    case transport(String)
    case response(String)

    var errorDescription: String? {
        switch self {
        case .toolMissing: return "未找到发布工具。请先安装或选择 ordina-publisher。"
        case .toolNotExecutable(let path): return "发布工具不可执行：\(path)"
        case .toolIncompatible(let required, let found):
            return "发布工具协议 \(found) 与客户端 \(required) 不兼容，请更新发布工具。"
        case .transport(let message): return message
        case .response(let message): return message
        }
    }
}

struct DevToolInfo: Decodable, Equatable, Sendable {
    let tool: String
    let version: String
    let protocolVersion: Int
    let minClientVersion: String
    let features: [String]
    let python: String?
    let backend: String?

    enum CodingKeys: String, CodingKey {
        case tool, version, features, python, backend, minClientVersion
        case protocolVersion = "protocol"
    }
}

struct DevRequirement: Decodable, Equatable, Sendable {
    let id: String
    let ok: Bool
    let message: String
}

struct DevCredentialStatus: Decodable, Equatable, Sendable {
    let valid: Bool
    let code: String?
    let message: String?
    let developer: String?
    let expires: Int?
    let scopes: [String]?
    let repositories: [String]?
    let source: String?
}

struct DevDoctor: Decodable, Equatable, Sendable {
    struct Repository: Decodable, Equatable, Sendable {
        let path: String?
        let valid: Bool
        let branch: String?
        let head: String?
        let clean: Bool
    }
    struct Counts: Decodable, Equatable, Sendable {
        let total: Int
        let pending: Int
    }
    let tool: String
    let version: String
    let protocolVersion: Int
    let minClientVersion: String
    let backend: String?
    let repository: Repository
    let credential: DevCredentialStatus
    let journals: Counts
    let ready: Bool
    let requirements: [DevRequirement]

    enum CodingKeys: String, CodingKey {
        case tool, version, backend, repository, credential, journals, ready
        case requirements, minClientVersion
        case protocolVersion = "protocol"
    }
}

struct DevFileEntry: Decodable, Equatable, Sendable, Identifiable {
    let path: String
    let size: Int
    let sha256: String
    let role: String?
    var id: String { path }
}

struct DevInspection: Decodable, Equatable, Sendable {
    struct Package: Decodable, Equatable, Sendable {
        let id: String
        let name: String
        let summary: String
        let symbol: String
        let version: String
        let kind: String
        let protocolVersion: Int
        let minClientVersion: String
        let capabilities: [String]
        let fileCount: Int
        let assetSize: Int
        let assetSha256: String
        let zipSha256: String
        let files: [DevFileEntry]

        enum CodingKeys: String, CodingKey {
            case id, name, summary, symbol, version, kind, capabilities
            case fileCount, assetSize, assetSha256, zipSha256, files, minClientVersion
            case protocolVersion = "protocol"
        }
    }
    struct Existing: Decodable, Equatable, Sendable {
        let version: String?
        let repository: String?
        let published: Bool?
    }
    struct Compatibility: Decodable, Equatable, Sendable {
        let ok: Bool
        let reasons: [String]
        let clientVersion: String
        let required: String
    }
    struct Repository: Decodable, Equatable, Sendable {
        let slug: String
        let known: Bool
    }
    let package: Package
    let action: String
    let existing: Existing?
    let compatibility: Compatibility
    let repository: Repository

    var isNew: Bool { action == "new" }
    var actionLabel: String {
        switch action {
        case "new": return "新增插件"
        case "update": return "更新插件"
        case "same": return "版本相同"
        case "downgrade": return "低于线上版本"
        default: return action
        }
    }
}

struct DevLocalTest: Decodable, Equatable, Sendable {
    let bundlePath: String
    let version: String
    let plugin: String
    let name: String
    let action: String
    let previousVersion: String?
    let fileCount: Int
    /// The release body uses the same shape as a signed manifest entry, so the
    /// unified installer can verify and install it without a special path.
    let release: PluginRelease
}

struct DevPreview: Decodable, Equatable, Sendable {
    struct Plugin: Decodable, Equatable, Sendable {
        let id: String
        let name: String?
        let version: String
        let previousVersion: String?
        let repository: String
        let action: String
        let tag: String
        let releaseNotes: String
    }
    struct Asset: Decodable, Equatable, Sendable {
        let name: String
        let size: Int
        let sha256: String
    }
    struct Files: Decodable, Equatable, Sendable {
        let added: [String]
        let removed: [String]
        let changed: [String]
        let unchanged: [String]
    }
    struct Feed: Decodable, Equatable, Sendable {
        let label: String
        let repository: String?
        let path: String?
        let action: String
        let bytes: Int
    }
    struct Check: Decodable, Equatable, Sendable, Identifiable {
        let id: String
        let ok: Bool
        let message: String
    }
    struct Blocker: Decodable, Equatable, Sendable, Identifiable {
        let code: String
        let message: String
        var id: String { code }
    }
    struct Step: Decodable, Equatable, Sendable, Identifiable {
        let id: String
        let title: String
        let command: String
    }
    struct RepositoryState: Decodable, Equatable, Sendable {
        let exists: Bool?
        let slug: String
    }
    let plugin: Plugin
    let asset: Asset
    let files: Files
    let fileCount: Int
    let capabilities: [String]
    let feeds: [Feed]
    let checks: [Check]
    let blockers: [Blocker]
    let repository: RepositoryState
    let repositorySteps: [Step]
    let fingerprint: String
}

struct DevPublishResult: Decodable, Equatable, Sendable {
    let journal: String
    let plugin: String
    let version: String
    let repository: String
    let tag: String
    let stages: [String]?
}

struct DevAction: Decodable, Equatable, Sendable {
    let journal: String
    let status: String
    let plugin: String?
    let version: String?
}

struct DevJournal: Decodable, Equatable, Sendable, Identifiable {
    struct Repository: Decodable, Equatable, Sendable {
        let before: String?
        let after: String?
        let rolledBack: String?
    }
    let journal: String
    let status: String
    let plugin: String?
    let name: String?
    let version: String?
    let previousVersion: String?
    let repository: String?
    let tag: String?
    let notes: String?
    let createdAt: Int?
    let toolVersion: String?
    let resumable: Bool
    let rollbackable: Bool
    let repositories: [String: Repository]

    var id: String { journal }
    var isPending: Bool { resumable }
    var statusLabel: String {
        switch status {
        case "preparing": return "准备中"
        case "publishing": return "发布中"
        case "complete": return "已完成"
        case "rolling_back": return "回滚中"
        case "rolled_back": return "已回滚"
        default: return status
        }
    }
}

struct DevRecord: Decodable, Equatable, Sendable, Identifiable {
    let kind: String
    let plugin: String?
    let version: String?
    let previousVersion: String?
    let notes: String?
    let status: String?
    let at: Int?
    let journal: String?
    let toolVersion: String?
    var id: String { (journal ?? "") + "-" + kind + "-" + String(at ?? 0) }
}

struct DevHistory: Decodable, Equatable, Sendable {
    let records: [DevRecord]
    let journals: [DevJournal]
}

struct DevEvent: Equatable, Sendable {
    enum Kind: Equatable, Sendable {
        case stage(name: String, state: String, index: Int, total: Int)
        case log(String)
        case other
    }
    let kind: Kind
}

// MARK: - configuration

struct DeveloperConfiguration: Codable, Equatable, Sendable {
    var enabled = false
    var toolPath = ""          // empty means the managed installation
    var repositoryPath = ""
    var showsAdvanced = false
}

enum DeveloperDefaults {
    static let key = "developerCenterConfiguration"

    static func load(_ defaults: UserDefaults = .standard) -> DeveloperConfiguration {
        guard let data = defaults.data(forKey: key),
              let value = try? JSONDecoder().decode(DeveloperConfiguration.self, from: data)
        else { return DeveloperConfiguration() }
        return value
    }

    static func save(_ value: DeveloperConfiguration, defaults: UserDefaults = .standard) {
        guard let data = try? JSONEncoder().encode(value) else { return }
        defaults.set(data, forKey: key)
    }
}

enum DeveloperPaths {
    static var home: URL {
        URL(fileURLWithPath: ProcessInfo.processInfo.environment["HOME"]
            ?? FileManager.default.homeDirectoryForCurrentUser.path)
    }

    /// Developer data lives outside the app bundle and outside the tool
    /// versions, so tool updates never touch credentials, records or journals.
    static var root: URL {
        home.appendingPathComponent("Library/Application Support/ordina/Developer",
                                    isDirectory: true)
    }
    static var publisher: URL { root.appendingPathComponent("Publisher", isDirectory: true) }
    static var versions: URL { publisher.appendingPathComponent("versions", isDirectory: true) }
    static var installationFile: URL { publisher.appendingPathComponent("installed.json") }
    static var journals: URL { root.appendingPathComponent("journals", isDirectory: true) }
    static var records: URL { root.appendingPathComponent("records.jsonl") }
    static var downloads: URL { root.appendingPathComponent("downloads", isDirectory: true) }
}

// MARK: - process transport

enum DevBridge {
    static var clientVersion: String {
        ProcessInfo.processInfo.environment["ORDINA_CLIENT_VERSION"]
            ?? (Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String)
            ?? DevProtocol.minimumToolClient
    }

    static var isLab: Bool {
        #if DEBUG || ORDINA_LAB
        return ProcessInfo.processInfo.environment["ORDINA_DEV_LAB"] == "1"
        #else
        return false
        #endif
    }

    /// A release build always uses the signed Pages manifest. Only a lab or
    /// debug build may point at a loopback manifest, and even then the
    /// signature is verified with the pinned key.
    static var manifestURL: URL {
        if isLab, let raw = ProcessInfo.processInfo.environment["ORDINA_DEV_MANIFEST"],
           let url = URL(string: raw) {
            return url
        }
        return URL(string: "https://brokenag.github.io/ordina-releases/dev-tool/manifest.json")!
    }

    static var python: URL {
        if let raw = ProcessInfo.processInfo.environment["ORDINA_DEV_PYTHON"],
           FileManager.default.isExecutableFile(atPath: raw) {
            return URL(fileURLWithPath: raw)
        }
        return URL(fileURLWithPath: "/usr/bin/python3")
    }

    static func decode<T: Decodable>(_ type: T.Type, from raw: Any?) throws -> T {
        guard let raw, JSONSerialization.isValidJSONObject(raw) else {
            throw DevBridgeError.response("发布工具返回了无法识别的数据。")
        }
        let data = try JSONSerialization.data(withJSONObject: raw)
        return try JSONDecoder().decode(T.self, from: data)
    }
}
