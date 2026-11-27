import Foundation

// Build a protocol-valid ZIP from files that are already installed on this
// Mac. No release logic lives here: the generated archive is handed to the
// existing developer centre, which owns authentication, signing and the
// durable release transaction.

enum PluginPublishing {
    enum Source: Equatable, Sendable {
        case localScript(URL)
        case installed(String)
    }

    struct Info: Equatable, Sendable {
        var id = ""
        var name = ""
        var version = ""
        var summary = ""
        var symbol = "doc.text"
        var minClientVersion = "1.10.0"
    }

    struct File: Equatable, Sendable {
        let source: URL
        let target: String
        let path: String
        let role: String
        let installName: String?
        let executable: Bool
    }

    struct Prepared: Equatable, Sendable {
        let source: Source
        let info: Info
        let descriptor: PluginDescriptor
        let files: [File]
        let zip: URL
    }

    struct Launch: Equatable, Sendable {
        let prepared: Prepared
        var notes: String { "首次发布 \(prepared.info.name)" }
    }

    enum Failure: LocalizedError, Sendable {
        case missing([String])
        case invalid(String)
        case unsafe(String)
        case io(String)

        var errorDescription: String? {
            switch self {
            case .missing(let items):
                return "无法完整提取必要组件，未生成安装包。缺失或已变更：\n• " + items.joined(separator: "\n• ")
            case .invalid(let reason): return reason
            case .unsafe(let reason): return "检测到不应打包的内容：" + reason
            case .io(let reason): return "无法读取安装文件：" + reason
            }
        }
    }

    private static let idPattern = try! NSRegularExpression(pattern: "^[a-z][a-z0-9-]{0,31}$")
    private static let versionPattern = try! NSRegularExpression(pattern: "^[0-9]+(?:\\.[0-9]+){1,3}$")
    private static let scriptVersionPattern = try! NSRegularExpression(
        pattern: "(?m)^PLUGIN_VERSION\\s*=\\s*[\"']v?([0-9]+(?:\\.[0-9]+)+)[\"']")
    private static let forbidden = ["cache", "caches", "credential", "credentials", "secret",
                                    "secrets", "token", "tokens", ".env", "private", "personal"]

    static func canPackage(_ source: Source, environment: PluginEnvironment = .current) -> Bool {
        switch source {
        case .localScript(let url):
            return isRegularFile(url) && url.pathExtension.lowercased() == "py"
        case .installed(let id):
            guard let record = PluginInstaller.state(id, environment: environment) else { return false }
            return PluginInstaller.necessaryComponentsPresent(record.descriptor, environment: environment)
                && record.files.allSatisfy { file in
                    guard let url = try? checked(environment.home.appendingPathComponent(file.target)),
                          let data = try? Data(contentsOf: url) else { return false }
                    return PluginProtocol.digest(data) == file.sha256
                }
        }
    }

    @MainActor
    static func canAuthor(configuration: DeveloperConfiguration = DeveloperDefaults.load()) -> Bool {
        guard configuration.enabled else { return false }
        let toolReady = configuration.toolPath.isEmpty
            ? DevToolManager.installation() != nil
            : FileManager.default.isExecutableFile(atPath: configuration.toolPath)
        guard toolReady else { return false }
        let auth = DeveloperAuthentication()
        auth.load()
        return auth.bound || auth.deviceTrusted
    }

    static func suggestedInfo(_ source: Source,
                              environment: PluginEnvironment = .current) throws -> Info {
        switch source {
        case .localScript(let url):
            guard isRegularFile(url), url.pathExtension.lowercased() == "py" else {
                throw Failure.invalid("请选择普通的 Python 脚本文件。")
            }
            let data = try Data(contentsOf: url)
            let text = String(data: data, encoding: .utf8) ?? ""
            return Info(id: identifier(from: url.deletingPathExtension().lastPathComponent),
                        name: url.deletingPathExtension().lastPathComponent,
                        version: scriptVersion(text) ?? "",
                        summary: summary(in: text) ?? "",
                        symbol: "doc.text", minClientVersion: "1.10.0")
        case .installed(let id):
            guard let record = PluginInstaller.state(id, environment: environment) else {
                throw Failure.missing(["插件托管记录 \(id)"])
            }
            return Info(id: record.descriptor.id, name: record.descriptor.name,
                        version: record.version, summary: record.descriptor.summary,
                        symbol: record.descriptor.symbol,
                        minClientVersion: record.descriptor.minClientVersion)
        }
    }

    static func prepare(_ info: Info, source: Source,
                        environment: PluginEnvironment = .current) throws -> Prepared {
        let information = try validated(info)
        let collected: [File]
        switch source {
        case .localScript(let url): collected = try scriptFiles(url, info: information, environment: environment)
        case .installed(let id): collected = try installedFiles(id, info: information, environment: environment)
        }
        guard !collected.isEmpty else { throw Failure.missing(["必要的插件文件"]) }
        for file in collected {
            let parts = file.path.lowercased().split(separator: "/").map(String.init)
            if let found = parts.first(where: { forbidden.contains($0) || $0.hasPrefix(".env") }) {
                throw Failure.unsafe("\(found)（\(file.path)）")
            }
        }
        let descriptor = try makeDescriptor(information, files: collected, source: source, environment: environment)
        let zip = try writeArchive(information, files: collected, descriptor: descriptor)
        return Prepared(source: source, info: information, descriptor: descriptor,
                        files: collected, zip: zip)
    }

    /// Create or refresh the managed record only after a successful publish.
    /// The record points at the exact files that were packaged, so the local
    /// script list stops showing the same plugin as an independent script.
    static func recordPublished(_ prepared: Prepared,
                                environment: PluginEnvironment = .current) throws {
        var files: [InstalledFile] = []
        for item in prepared.files {
            let data = try Data(contentsOf: item.source)
            files.append(InstalledFile(target: item.target,
                                       sha256: PluginProtocol.digest(data),
                                       role: item.role,
                                       executable: item.executable))
        }
        let record = InstalledPlugin(id: prepared.descriptor.id,
                                     version: prepared.descriptor.version,
                                     descriptor: prepared.descriptor,
                                     files: files,
                                     installedAt: Date().timeIntervalSince1970,
                                     legacyMarker: nil)
        let directory = environment.stateDirectory(record.id)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try JSONEncoder().encode(record).write(to: environment.stateFile(record.id), options: .atomic)
        NotificationCenter.default.post(name: PluginInstaller.didChangeNotification,
                                        object: record.id)
    }

    static func discard(_ prepared: Prepared) {
        let directory = prepared.zip.deletingLastPathComponent()
        if directory.lastPathComponent.hasPrefix("ordina-local-publish-") {
            try? FileManager.default.removeItem(at: directory)
        }
    }

    // MARK: collection

    private static func scriptFiles(_ url: URL, info: Info,
                                    environment: PluginEnvironment) throws -> [File] {
        guard isRegularFile(url), url.pathExtension.lowercased() == "py" else {
            throw Failure.missing([url.lastPathComponent + "（不是可读取的 Python 脚本）"])
        }
        let name = url.lastPathComponent
        guard validInstallName(name) else {
            throw Failure.invalid("脚本文件名 \(name) 不符合插件协议，请改用英文、数字、空格、点、下划线或连字符后重试。")
        }
        let target = relative(environment.utilityDirectory.appendingPathComponent(name), home: environment.home)
        return [File(source: url, target: target, path: name, role: "utilityScript",
                     installName: name, executable: false)]
    }

    private static func installedFiles(_ id: String, info: Info,
                                       environment: PluginEnvironment) throws -> [File] {
        guard let record = PluginInstaller.state(id, environment: environment) else {
            throw Failure.missing(["插件托管记录 \(id)"])
        }
        let support = PluginInstaller.supportDirectory(record.descriptor, environment: environment)
        var missing: [String] = []
        var result: [File] = []
        var seen = Set<String>()
        for item in record.files {
            guard let source = try? checked(environment.home.appendingPathComponent(item.target)) else {
                missing.append(item.target + "（安装路径包含符号链接）")
                continue
            }
            guard FileManager.default.fileExists(atPath: source.path),
                  let data = try? Data(contentsOf: source),
                  PluginProtocol.digest(data) == item.sha256 else {
                missing.append(item.target + "（文件缺失或内容已变更）")
                continue
            }
            let path = bundlePath(item, record: record, support: support,
                                  home: environment.home)
            guard protocolPath(path), !seen.contains(path) else {
                throw Failure.invalid("无法为 \(item.target) 生成唯一的插件包路径。")
            }
            seen.insert(path)
            result.append(File(source: source, target: item.target, path: path,
                               role: item.role,
                               installName: item.role == "utilityScript" ? (record.descriptor.utilityScript ?? URL(fileURLWithPath: item.target).lastPathComponent) : nil,
                               executable: item.executable))
        }
        if !PluginInstaller.necessaryComponentsPresent(record.descriptor, environment: environment) {
            missing.append("能力声明所需的完整组件")
        }
        if !missing.isEmpty { throw Failure.missing(missing) }
        return result
    }

    private static func bundlePath(_ item: InstalledFile, record: InstalledPlugin,
                                   support: URL?, home: URL) -> String {
        let name = URL(fileURLWithPath: item.target).lastPathComponent
        switch item.role {
        case "utilityScript":
            return record.descriptor.utilityScript ?? name
        case "extension":
            if let range = item.target.range(of: "/extension/") {
                return "extension/" + String(item.target[range.upperBound...])
            }
            return "extension/" + name
        case "nativeMessagingManifest":
            return "native-messaging/" + name
        default:
            if let support,
               let range = item.target.range(of: relative(support, home: home) + "/") {
                return String(item.target[range.upperBound...])
            }
            return name
        }
    }

    // MARK: descriptor and archive

    private static func makeDescriptor(_ info: Info, files: [File], source: Source,
                                       environment: PluginEnvironment) throws -> PluginDescriptor {
        guard let utility = files.first(where: { $0.role == "utilityScript" }) else {
            throw Failure.missing(["必要的 Resolve 脚本"])
        }
        guard let installName = utility.installName, validInstallName(installName) else {
            throw Failure.invalid("主脚本文件名不符合插件协议。")
        }
        var capabilities: [PluginCapability] = [.resolveUtilityScript]
        var supportName: String?
        var host: String?
        if case .installed(let id) = source,
           let record = PluginInstaller.state(id, environment: environment) {
            capabilities = record.descriptor.capabilities
            supportName = record.descriptor.supportDirectoryName
            host = record.descriptor.nativeMessagingHost
        }
        let roles = Set(files.map(\.role))
        if roles.contains("support") || roles.contains("nativeMessagingHost")
            || roles.contains("nativeMessagingManifest") || roles.contains("extension") {
            if !capabilities.contains(.resolveSupport) { capabilities.append(.resolveSupport) }
        }
        if roles.contains("nativeMessagingHost") && !capabilities.contains(.chromeNativeMessagingHost) {
            capabilities.append(.chromeNativeMessagingHost)
        }
        if roles.contains("extension") && !capabilities.contains(.chromeUnpackedExtension) {
            capabilities.append(.chromeUnpackedExtension)
        }
        return PluginDescriptor(id: info.id, name: info.name, summary: info.summary,
                                symbol: info.symbol, version: info.version,
                                minClientVersion: info.minClientVersion, kind: "bundle",
                                protocolVersion: 2, capabilities: capabilities,
                                supportDirectoryName: supportName ?? info.id,
                                nativeMessagingHost: host, legacyNames: nil,
                                utilityScript: installName)
    }

    private static func writeArchive(_ info: Info, files: [File],
                                     descriptor: PluginDescriptor) throws -> URL {
        let root = DeveloperPaths.root.appendingPathComponent(
            "LocalPublish/ordina-local-publish-" + UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        var manifest: [String: Any] = [
            "id": info.id, "name": info.name, "version": info.version,
            "summary": info.summary, "symbol": info.symbol,
            "minClientVersion": info.minClientVersion, "protocol": 2,
            "capabilities": descriptor.capabilities.map(\.rawValue),
        ]
        if let support = descriptor.supportDirectoryName { manifest["supportDirectoryName"] = support }
        if let host = descriptor.nativeMessagingHost { manifest["nativeMessagingHost"] = host }
        var declared: [String: Any] = [:]
        for file in files {
            var item: [String: Any] = ["role": file.role]
            if let installName = file.installName { item["installName"] = installName }
            if file.executable { item["executable"] = true }
            declared[file.path] = item
        }
        manifest["files"] = declared
        let manifestData = try JSONSerialization.data(withJSONObject: manifest,
                                                      options: [.sortedKeys, .withoutEscapingSlashes])
        let zip = root.appendingPathComponent("\(info.id)-\(info.version).zip")
        var entries: [(String, Data)] = [("plugin.json", manifestData)]
        for file in files {
            guard let data = try? Data(contentsOf: file.source) else {
                throw Failure.missing([file.target])
            }
            entries.append((file.path, data))
        }
        // Stage ordinary files only, then let the system ZIP tool write a
        // deterministic archive with explicit relative paths. No directory
        // entries, extended attributes or symlinks are carried over.
        for (path, data) in entries {
            let target = root.appendingPathComponent(path)
            try FileManager.default.createDirectory(at: target.deletingLastPathComponent(),
                                                    withIntermediateDirectories: true)
            try data.write(to: target, options: .withoutOverwriting)
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/zip")
        process.currentDirectoryURL = root
        process.arguments = ["-q", "-X", "-D", zip.lastPathComponent]
            + entries.map(\.0)
        let errors = Pipe()
        process.standardError = errors
        do {
            try process.run()
            process.waitUntilExit()
        } catch {
            throw Failure.io(error.localizedDescription)
        }
        guard process.terminationStatus == 0 else {
            let text = String(data: errors.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
            throw Failure.io("ZIP 写入失败：" + text.suffix(300))
        }
        return zip
    }

    // MARK: validation helpers

    private static func validated(_ info: Info) throws -> Info {
        var value = info
        value.id = value.id.trimmingCharacters(in: .whitespacesAndNewlines)
        value.name = value.name.trimmingCharacters(in: .whitespacesAndNewlines)
        value.version = value.version.trimmingCharacters(in: .whitespacesAndNewlines)
            .replacingOccurrences(of: "v", with: "")
        value.summary = value.summary.trimmingCharacters(in: .whitespacesAndNewlines)
        value.symbol = value.symbol.trimmingCharacters(in: .whitespacesAndNewlines)
        guard matches(idPattern, value.id) else { throw Failure.invalid("插件标识需为小写字母、数字或连字符，最多 32 字符。") }
        guard 1...64 ~= value.name.count else { throw Failure.invalid("插件名称需为 1–64 个字符。") }
        guard matches(versionPattern, value.version) else { throw Failure.invalid("版本号需为点分隔的数字，例如 2.4 或 2.4.0。") }
        guard matches(versionPattern, value.minClientVersion) else { throw Failure.invalid("最低客户端版本格式不正确。") }
        guard 1...160 ~= value.summary.count else { throw Failure.invalid("插件简介需为 1–160 个字符。") }
        guard value.symbol.range(of: "^[a-z][a-z0-9.]{0,63}$", options: .regularExpression) != nil else {
            throw Failure.invalid("图标标识格式不正确。")
        }
        return value
    }

    private static func identifier(from name: String) -> String {
        let parts = name.split(whereSeparator: { !$0.isASCII || (!$0.isLetter && !$0.isNumber) })
            .map(String.init).joined(separator: "-").lowercased()
        let cleaned = parts.trimmingCharacters(in: CharacterSet(charactersIn: "-"))
        let value = cleaned.isEmpty ? "plugin" : cleaned
        return String(value.prefix(32)).trimmingCharacters(in: CharacterSet(charactersIn: "-"))
    }

    private static func scriptVersion(_ text: String) -> String? {
        let range = NSRange(text.startIndex..., in: text)
        guard let match = scriptVersionPattern.firstMatch(in: text, range: range),
              let found = Range(match.range(at: 1), in: text) else { return nil }
        return String(text[found])
    }

    private static func summary(in text: String) -> String? {
        guard let quote = text.range(of: "\"\"\""),
              let end = text.range(of: "\"\"\"", range: quote.upperBound..<text.endIndex) else { return nil }
        let body = text[quote.upperBound..<end.lowerBound]
            .split(whereSeparator: \.isNewline).first.map(String.init)?
            .trimmingCharacters(in: .whitespaces)
        guard let body, !body.isEmpty else { return nil }
        return String(body.prefix(160))
    }

    private static func matches(_ pattern: NSRegularExpression, _ value: String) -> Bool {
        pattern.firstMatch(in: value, range: NSRange(value.startIndex..., in: value)) != nil
    }

    private static func validInstallName(_ value: String) -> Bool {
        value.range(of: "^[A-Za-z0-9][A-Za-z0-9 ._-]{0,127}$", options: .regularExpression) != nil
            && value.hasSuffix(".py")
    }

    private static func protocolPath(_ value: String) -> Bool {
        value.split(separator: "/").allSatisfy { part in
            !part.isEmpty && !part.hasPrefix(".")
            && part.range(of: "^[A-Za-z0-9 _.-]+$", options: .regularExpression) != nil
        }
    }

    private static func checked(_ url: URL) throws -> URL {
        var current = url
        while current.path != "/" {
            if (try? FileManager.default.destinationOfSymbolicLink(atPath: current.path)) != nil {
                throw Failure.unsafe(current.path)
            }
            current = current.deletingLastPathComponent()
        }
        return url
    }

    private static func isRegularFile(_ url: URL) -> Bool {
        guard url.isFileURL, let values = try? url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey])
        else { return false }
        return values.isRegularFile == true && values.isSymbolicLink != true
    }

    private static func relative(_ url: URL, home: URL) -> String {
        let path = url.standardizedFileURL.path
        let base = home.standardizedFileURL.path
        return path.hasPrefix(base + "/") ? String(path.dropFirst(base.count + 1)) : path
    }
}
