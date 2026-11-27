import Foundation
import CryptoKit

// The signed plugin protocol. Everything the client may display or install is
// described here; nothing in a descriptor can name a shell command, an absolute
// path or an arbitrary install target. Installation behaviour is limited to the
// capabilities compiled into this client.
enum PluginCapability: String, CaseIterable, Codable, Sendable {
    case macOSApplication = "macos.application"
    case resolveUtilityScript = "resolve.utilityScript"
    case resolveSupport = "resolve.support"
    case chromeNativeMessagingHost = "chrome.nativeMessagingHost"
    case chromeUnpackedExtension = "chrome.unpackedExtension"
}

enum PluginFileRole: String, Codable, Sendable {
    case application
    case metadata
    case utilityScript
    case support
    case nativeMessagingHost
    case nativeMessagingManifest
    case `extension`
}

struct PluginFile: Decodable, Equatable, Sendable {
    let path: String
    let sha256: String
    let size: Int
    let role: PluginFileRole
    let installName: String?
    let executable: Bool?
}

struct PluginDescriptor: Codable, Equatable, Sendable, Identifiable {
    let id: String
    let name: String
    let summary: String
    let symbol: String
    /// Optional compatibility field: a built-in icon id. Older clients and
    /// older ZIPs simply omit it; the view falls back to `symbol`.
    /// Defaulted so every existing constructor stays source-compatible.
    var iconID: String? = nil
    let version: String
    let minClientVersion: String
    let kind: String
    let protocolVersion: Int
    let capabilities: [PluginCapability]
    let supportDirectoryName: String?
    let nativeMessagingHost: String?
    let legacyNames: [String]?
    let utilityScript: String?

    enum CodingKeys: String, CodingKey {
        case id, name, summary, symbol, iconID, version, minClientVersion, kind
        case protocolVersion = "protocol"
        case capabilities, supportDirectoryName, nativeMessagingHost, legacyNames
        case utilityScript
    }

    var isBundle: Bool { kind == "bundle" }
    var needsBrowserExtension: Bool { capabilities.contains(.chromeUnpackedExtension) }
    var needsNativeMessaging: Bool { capabilities.contains(.chromeNativeMessagingHost) }
}

struct PluginRelease: Decodable, Equatable, Sendable {
    let version: String
    let url: String
    let sha256: String
    let size: Int
    let kind: String?
    let descriptor: PluginDescriptor
    let files: [PluginFile]
}

struct PluginRegistryEntry: Decodable, Equatable, Sendable {
    let repository: String
    let feed: String
    let downloadPrefix: String
    let kind: String?
    let name: String?
    let summary: String?
    let symbol: String?
    var iconID: String? = nil
    let version: String?
    let minClientVersion: String?
    let protocolVersion: Int?
    let capabilities: [String]?

    enum CodingKeys: String, CodingKey {
        case repository, feed, downloadPrefix, kind, name, summary, symbol, iconID
        case version, minClientVersion, capabilities
        case protocolVersion = "protocol"
    }
}

// Trust policy for registry/feed/asset addresses. Production is the default;
// the lab policy only relaxes scheme/host so an isolated local registry can be
// exercised, and it keeps every path shape check intact.
struct PluginPolicy: Sendable {
    let feedScheme: String
    let feedHost: String
    let downloadScheme: String
    let downloadHost: String
    let repositoryPrefix: String
    let downloadPathPrefix: String
    let feedPathPrefix: String
    let allowPort: Bool

    static let production = PluginPolicy(
        feedScheme: "https", feedHost: "brokenag.github.io",
        downloadScheme: "https", downloadHost: "github.com",
        repositoryPrefix: "BrOKenAG/ordina-plugin-",
        downloadPathPrefix: "/BrOKenAG/ordina-plugin-",
        feedPathPrefix: "/ordina-plugin-", allowPort: false)

    static func lab(host: String) -> PluginPolicy {
        PluginPolicy(feedScheme: "http", feedHost: host,
                     downloadScheme: "http", downloadHost: host,
                     repositoryPrefix: "BrOKenAG/ordina-plugin-",
                     downloadPathPrefix: "/BrOKenAG/ordina-plugin-",
                     feedPathPrefix: "/ordina-plugin-", allowPort: true)
    }
}

enum PluginProtocol {
    enum Failure: LocalizedError, Equatable {
        case invalid
        case unavailable
        // Carries the concrete transport/verification cause so callers can
        // report what actually failed instead of collapsing every failure
        // into the same generic "无法获取插件目录" line.
        case detail(String)
        case notPublished
        case unsupportedClient
        case incompatibleClient(String)
        case corrupted

        var errorDescription: String? {
            switch self {
            case .invalid: return "插件清单校验失败。"
            case .unavailable: return "无法获取插件目录。"
            case .detail(let cause): return cause
            case .notPublished: return "暂无在线版本。"
            case .unsupportedClient: return "插件协议版本高于当前客户端。"
            case .incompatibleClient(let version): return "需要 ordina \(version) 或更高版本。"
            case .corrupted: return "插件文件校验失败。"
            }
        }
    }

    struct Envelope: Decodable {
        let payload: String
        let signature: String
    }
    private struct RegistryPayload: Decodable {
        let format: Int
        let plugins: [String: PluginRegistryEntry]
        let withdrawn: [String]?
    }
    struct RegistrySnapshot: Sendable {
        let plugins: [String: PluginRegistryEntry]
        let withdrawn: Set<String>
    }
    private struct ReleasePayload: Decodable {
        let format: Int
        let plugins: [String: PluginRelease]
    }
    private struct Container: Decodable {
        let format: Int
        let id: String
        let version: String
        let files: [String: String]
    }

    static let maximumRegistry = 131_072
    static let maximumManifest = 131_072
    static let maximumBundle = 4_000_000
    static let maximumNativeBundle = 96_000_000
    static let maximumScript = 2_000_000
    static let maximumFile = 1_000_000
    static let maximumNativeFile = 16_000_000

    static func verifiedPayload(_ data: Data, publicKey: Data) throws -> Data {
        guard data.count <= maximumManifest else {
            throw Failure.detail("目录内容超过 \(maximumManifest) 字节上限。")
        }
        let envelope: Envelope
        do {
            envelope = try JSONDecoder().decode(Envelope.self, from: data)
        } catch {
            throw Failure.detail("目录信封 JSON 解析失败：\(error.localizedDescription)")
        }
        guard let payload = Data(base64Encoded: envelope.payload),
              payload.count <= maximumManifest else {
            throw Failure.detail("目录 payload Base64 解码失败或超过大小上限。")
        }
        guard let signature = Data(base64Encoded: envelope.signature) else {
            throw Failure.detail("目录签名 Base64 解码失败。")
        }
        guard let key = try? Curve25519.Signing.PublicKey(rawRepresentation: publicKey) else {
            throw Failure.detail("内置公钥格式无效。")
        }
        guard key.isValidSignature(signature, for: payload) else {
            throw Failure.detail("目录 Ed25519 签名校验失败：公钥或内容不匹配。")
        }
        return payload
    }

    static func validID(_ value: String) -> Bool {
        value.range(of: #"^[a-z][a-z0-9-]{0,31}$"#, options: .regularExpression) != nil
    }

    static func validVersion(_ value: String) -> Bool {
        value.range(of: #"^[0-9]+(\.[0-9]+){1,3}$"#, options: .regularExpression) != nil
    }

    static func validSymbol(_ value: String) -> Bool {
        value.range(of: #"^[a-z][a-z0-9.]{0,63}$"#, options: .regularExpression) != nil
    }

    /// Icon ids are optional and never whitelisted here: an unknown id is
    /// rendered as the default glyph instead of invalidating a signed feed.
    static func validIconID(_ value: String?) -> Bool {
        guard let value else { return true }
        return value.range(of: #"^[a-z][a-z0-9-]{0,31}$"#, options: .regularExpression) != nil
    }

    static func validSupportName(_ value: String) -> Bool {
        value.range(of: #"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$"#, options: .regularExpression) != nil
    }

    static func validInstallName(_ value: String) -> Bool {
        value.range(of: #"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,127}$"#, options: .regularExpression) != nil
            && !value.contains("/") && !value.contains("\\") && !value.hasPrefix(".")
    }

    // Old menu entries may contain non-ASCII names (e.g. 导入阅流.py); they are
    // only ever used to locate and remove a file inside the Utility folder.
    static func validLegacyName(_ value: String) -> Bool {
        !value.isEmpty && value.count <= 128 && value.hasSuffix(".py")
            && !value.hasPrefix(".") && !value.contains("/")
            && !value.contains("\\") && !value.contains(":") && !value.contains("\0")
    }

    // Mirrors the packaging rule: relative, at most four components, no
    // traversal, no dotfiles, printable ASCII only.
    static func validRelativePath(_ name: String) -> Bool {
        guard !name.isEmpty, name.utf8.count <= 200, !name.hasPrefix("/"),
              !name.contains("\\"), !name.contains(":"), !name.contains("\0") else { return false }
        let parts = name.split(separator: "/", omittingEmptySubsequences: false)
        let maximumDepth = parts.count >= 2 && parts[0].hasSuffix(".app") && parts[1] == "Contents" ? 12 : 4
        guard parts.count <= maximumDepth, parts.allSatisfy({
            !$0.isEmpty && !$0.hasPrefix(".") && $0 != ".."
                && $0.range(of: #"^[A-Za-z0-9 _.\-]+$"#, options: .regularExpression) != nil
        }) else { return false }
        return true
    }

    private static func validFeed(_ value: String, id: String, policy: PluginPolicy) -> Bool {
        guard let url = URL(string: value), url.scheme == policy.feedScheme,
              url.host == policy.feedHost, policy.allowPort || url.port == nil,
              url.user == nil, url.password == nil, url.query == nil, url.fragment == nil else { return false }
        return url.path == "\(policy.feedPathPrefix)\(id)/manifest-v2.json"
            || url.path == "\(policy.feedPathPrefix)\(id)/manifest.json"
    }

    private static func validDownloadPrefix(_ value: String, id: String, policy: PluginPolicy) -> Bool {
        value == "\(policy.downloadPathPrefix)\(id)/releases/download/"
            && !value.contains("..") && !value.contains("//")
    }

    static func verifiedRegistry(_ data: Data, publicKey: Data,
                                 policy: PluginPolicy) throws -> [String: PluginRegistryEntry] {
        try verifiedRegistrySnapshot(data, publicKey: publicKey, policy: policy).plugins
    }

    static func verifiedRegistrySnapshot(_ data: Data, publicKey: Data,
                                         policy: PluginPolicy) throws -> RegistrySnapshot {
        let payload = try verifiedPayload(data, publicKey: publicKey)
        let document: RegistryPayload
        do {
            document = try JSONDecoder().decode(RegistryPayload.self, from: payload)
        } catch {
            throw Failure.detail("目录协议 JSON 解析失败：\(error.localizedDescription)")
        }
        guard document.format == 2 else {
            throw Failure.detail("目录协议格式不受支持：format=\(document.format)，需要 2。")
        }
        let withdrawn = document.withdrawn ?? []
        guard withdrawn.count <= 1000, Set(withdrawn).count == withdrawn.count,
              withdrawn.allSatisfy({ validID($0) && document.plugins[$0] == nil })
        else { throw Failure.invalid }
        var result: [String: PluginRegistryEntry] = [:]
        for (id, entry) in document.plugins {
            guard validID(id), entry.protocolVersion == 2,
                  validFeed(entry.feed, id: id, policy: policy),
                  validDownloadPrefix(entry.downloadPrefix, id: id, policy: policy),
                  entry.repository.hasPrefix(policy.repositoryPrefix),
                  entry.kind == nil || entry.kind == "bundle" || entry.kind == "script",
                  let name = entry.name, !name.isEmpty, name.count <= 64,
                  let summary = entry.summary, !summary.isEmpty, summary.count <= 160,
                  let symbol = entry.symbol, validSymbol(symbol), validIconID(entry.iconID),
                  let version = entry.version, validVersion(version),
                  let minimum = entry.minClientVersion, validVersion(minimum),
                  let capabilities = entry.capabilities, !capabilities.isEmpty,
                  capabilities.allSatisfy({ PluginCapability(rawValue: $0) != nil })
            else { throw Failure.invalid }
            result[id] = entry
        }
        return RegistrySnapshot(plugins: result, withdrawn: Set(withdrawn))
    }

    static func signedRelease(_ data: Data, pluginID: String, publicKey: Data,
                              policy: PluginPolicy) throws -> PluginRelease {
        let payload = try verifiedPayload(data, publicKey: publicKey)
        let document = try JSONDecoder().decode(ReleasePayload.self, from: payload)
        guard document.format == 1, let release = document.plugins[pluginID] else {
            throw Failure.notPublished
        }
        let descriptor = release.descriptor
        let prefix = "\(policy.downloadPathPrefix)\(pluginID)/releases/download/"
        guard descriptor.id == pluginID, validID(pluginID),
              descriptor.protocolVersion == 2,
              validVersion(descriptor.version), validVersion(release.version),
              descriptor.version == release.version,
              descriptor.kind == "bundle" || descriptor.kind == "script",
              (release.kind == nil || release.kind == descriptor.kind),
              !descriptor.name.isEmpty, descriptor.name.count <= 64,
              !descriptor.summary.isEmpty, descriptor.summary.count <= 160,
              validSymbol(descriptor.symbol), validIconID(descriptor.iconID),
              validVersion(descriptor.minClientVersion),
              !descriptor.capabilities.isEmpty,
              (!descriptor.capabilities.contains(.macOSApplication) || supports(clientVersion: descriptor.minClientVersion, minimum: "1.10.0")),
              Set(descriptor.capabilities).count == descriptor.capabilities.count,
              descriptor.supportDirectoryName.map(validSupportName) ?? true,
              descriptor.nativeMessagingHost.map({ validRelativePath($0) }) ?? true,
              descriptor.utilityScript.map({ validInstallName($0) && $0.hasSuffix(".py") }) ?? true,
              (descriptor.legacyNames ?? []).count <= 8,
              (descriptor.legacyNames ?? []).allSatisfy(validLegacyName),
              release.sha256.range(of: #"^[0-9a-f]{64}$"#, options: .regularExpression) != nil,
              release.size > 0, release.size <= (descriptor.capabilities.contains(.macOSApplication) ? maximumNativeBundle : maximumBundle),
              let url = URL(string: release.url), url.scheme == policy.downloadScheme,
              url.host == policy.downloadHost, policy.allowPort || url.port == nil,
              url.user == nil, url.password == nil, url.query == nil, url.fragment == nil,
              url.path.hasPrefix(prefix), !url.path.contains(".."), !url.path.contains("//"),
              url.pathExtension == (descriptor.isBundle ? "json" : "py")
        else { throw Failure.invalid }
        try validateFiles(release.files, id: pluginID, kind: descriptor.kind)
        guard descriptor.capabilities.contains(.macOSApplication) == release.files.contains(where: { $0.role == .application }) else { throw Failure.invalid }
        return release
    }

    static func validateFiles(_ files: [PluginFile], id: String, kind: String) throws {
        guard !files.isEmpty, files.count <= 512 else { throw Failure.invalid }
        var seen = Set<String>()
        for file in files {
            guard validRelativePath(file.path), !seen.contains(file.path) else { throw Failure.invalid }
            seen.insert(file.path)
            guard file.sha256.range(of: #"^[0-9a-f]{64}$"#, options: .regularExpression) != nil,
                  file.size > 0, file.size <= (file.role == .application ? maximumNativeFile : maximumFile) else { throw Failure.invalid }
            if let name = file.installName, !validInstallName(name) { throw Failure.invalid }
        }
        let appFiles = files.filter { $0.role == .application }
        if !appFiles.isEmpty {
            let roots = Set(appFiles.compactMap { $0.path.split(separator: "/").first.map(String.init) })
            guard roots.count == 1, let root = roots.first, root.hasSuffix(".app"),
                  appFiles.allSatisfy({ $0.path.hasPrefix(root + "/Contents/") }),
                  files.allSatisfy({ !$0.path.hasPrefix(root + "/") || $0.role == .application }) else { throw Failure.invalid }
        }
        let roles = files.map(\.role)
        if kind == "script" {
            guard files.count == 1, roles == [.utilityScript] else { throw Failure.invalid }
        } else {
            guard files.contains(where: { $0.role == .metadata && $0.path == "plugin.json" }),
                  roles.filter({ $0 == .utilityScript }).count == 1 else { throw Failure.invalid }
        }
    }

    // A bundle is a signed JSON container; every file is verified against the
    // signed inventory before anything is written.
    static func unpack(_ data: Data, release: PluginRelease) throws -> [String: Data] {
        guard digest(data) == release.sha256, data.count == release.size else {
            throw Failure.corrupted
        }
        guard release.descriptor.isBundle else {
            guard case let file = release.files[0], file.size == data.count,
                  file.sha256 == release.sha256 else { throw Failure.corrupted }
            return [file.path: data]
        }
        guard let container = try? JSONDecoder().decode(Container.self, from: data),
              container.format == 1, container.id == release.descriptor.id,
              container.version == release.version, container.files.count <= 512 else {
            throw Failure.invalid
        }
        var result: [String: Data] = [:]
        var total = 0
        for item in release.files {
            guard let encoded = container.files[item.path],
                  let file = Data(base64Encoded: encoded),
                  file.count == item.size, file.count <= (item.role == .application ? maximumNativeFile : maximumFile),
                  digest(file) == item.sha256 else { throw Failure.corrupted }
            total += file.count
            guard total <= (release.descriptor.capabilities.contains(.macOSApplication) ? maximumNativeBundle : maximumBundle) else { throw Failure.corrupted }
            result[item.path] = file
        }
        guard result.count == container.files.count else { throw Failure.corrupted }
        return result
    }

    static func digest(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    static func newer(_ candidate: String, than installed: String) -> Bool {
        guard validVersion(candidate), validVersion(installed) else { return false }
        return candidate.compare(installed, options: .numeric) == .orderedDescending
    }

    static func supports(clientVersion: String, minimum: String) -> Bool {
        guard validVersion(clientVersion), validVersion(minimum) else { return false }
        return clientVersion.compare(minimum, options: .numeric) != .orderedAscending
    }
}
