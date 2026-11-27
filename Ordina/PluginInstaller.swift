import Foundation
import Darwin
import Security

// One installer for every plugin, driven only by the signed descriptor and file
// inventory. Capabilities are compiled into the client: a descriptor selects
// behaviour, it never supplies a command or an absolute destination.
struct InstalledFile: Codable, Equatable, Sendable {
    let target: String          // path relative to the user's home
    let sha256: String
    let role: String
    let executable: Bool
}

struct InstalledPlugin: Codable, Equatable, Sendable {
    let id: String
    let version: String
    let descriptor: PluginDescriptor
    let files: [InstalledFile]
    let installedAt: TimeInterval
    let legacyMarker: String?
}

enum PluginInstallStatus: Equatable, Sendable {
    case notInstalled
    case current(version: String)
    case updateAvailable(installed: String, latest: String)
    case unmanaged(version: String?)
    case incompatible(required: String)

    var isInstalled: Bool {
        switch self {
        case .current, .updateAvailable, .unmanaged: return true
        case .notInstalled, .incompatible: return false
        }
    }
}

enum PluginInstaller {
    /// Posted after a successful install/uninstall so every ownership view
    /// refreshes from the install records instead of relying on file events.
    static let didChangeNotification = Notification.Name("ordina.pluginInstallDidChange")

    enum Failure: LocalizedError, Equatable {
        case newerInstalled
        case incompatible(String)
        case missingSupport(String)
        case linkedPath
        case invalidManifest
        case writeFailed(String)

        var errorDescription: String? {
            switch self {
            case .newerInstalled: return "已安装更新的插件版本。"
            case .incompatible(let version): return "需要 ordina \(version) 或更高版本。"
            case .missingSupport(let name): return "缺少安装文件：\(name)"
            case .linkedPath: return "安装路径包含符号链接，请改用实际目录后重试。"
            case .invalidManifest: return "浏览器通信配置无效。"
            case .writeFailed(let name): return "无法写入 \(name)。"
            }
        }
    }

    private static let lock = NSLock()

    private struct PlannedFile {
        let relative: String     // relative to home
        let data: Data
        let executable: Bool
        let role: PluginFileRole
    }

    static func state(_ id: String, environment: PluginEnvironment) -> InstalledPlugin? {
        guard let data = try? Data(contentsOf: environment.stateFile(id)),
              let value = try? JSONDecoder().decode(InstalledPlugin.self, from: data),
              value.id == id else { return nil }
        return value
    }

    static func inspect(_ descriptor: PluginDescriptor,
                        environment: PluginEnvironment = .current) -> PluginInstallStatus {
        guard PluginProtocol.supports(clientVersion: environment.clientVersion,
                                      minimum: descriptor.minClientVersion) else {
            return .incompatible(required: descriptor.minClientVersion)
        }
        let markerVersion = legacyMarker(descriptor, environment: environment)?.version
        let components = necessaryComponentsPresent(descriptor, environment: environment)

        func status(for version: String) -> PluginInstallStatus {
            if version == descriptor.version { return .current(version: version) }
            if PluginProtocol.newer(descriptor.version, than: version) {
                return .updateAvailable(installed: version, latest: descriptor.version)
            }
            return .current(version: version)
        }

        // A state record counts only when every recorded component still
        // exists with the recorded bytes. A stale or partial record must not
        // report an installed plugin.
        if let installed = state(descriptor.id, environment: environment) {
            switch recordIntegrity(installed, environment: environment) {
            case .intact: return status(for: installed.version)
            case .altered:
                if components { return .unmanaged(version: installed.version) }
            case .partial: break
            }
        }
        // Legacy installed.json is only a version hint. It never proves a
        // composite installation on its own: every necessary component must
        // be present as well.
        if let version = markerVersion, components { return status(for: version) }
        if components { return .unmanaged(version: markerVersion) }
        return .notInstalled
    }

    private enum RecordIntegrity { case intact, altered, partial }

    private static func recordIntegrity(_ record: InstalledPlugin,
                                        environment: PluginEnvironment) -> RecordIntegrity {
        guard !record.files.isEmpty else { return .partial }
        let manager = FileManager.default
        var altered = false
        for file in record.files {
            guard let url = try? checkedPath(home: environment.home, relative: file.target),
                  let data = try? Data(contentsOf: url) else { continue }
            if PluginProtocol.digest(data) != file.sha256 { altered = true }
        }
        let present = record.files.allSatisfy { file in
            guard let url = try? checkedPath(home: environment.home, relative: file.target) else { return false }
            return manager.fileExists(atPath: url.path)
        }
        if !present { return .partial }
        return altered ? .altered : .intact
    }

    /// Necessary components are derived from signed capabilities, never from a
    /// plugin name or a single residual file. Every kind of plugin must at
    /// least have its utility script; each declared capability adds its own
    /// required target. This is shared by installation status and takeover.
    static func necessaryComponentsPresent(_ descriptor: PluginDescriptor,
                                           environment: PluginEnvironment) -> Bool {
        let manager = FileManager.default
        guard manager.fileExists(atPath: utilityTarget(descriptor, environment: environment).path)
        else { return false }
        guard let support = supportDirectory(descriptor, environment: environment) else { return false }
        if descriptor.capabilities.contains(.resolveSupport) {
            guard let contents = try? manager.contentsOfDirectory(atPath: support.path),
                  !contents.isEmpty else { return false }
        }
        if descriptor.capabilities.contains(.chromeNativeMessagingHost),
           let host = descriptor.nativeMessagingHost {
            guard manager.fileExists(atPath: support.appendingPathComponent(host).path) else { return false }
        }
        if descriptor.capabilities.contains(.chromeUnpackedExtension) {
            guard manager.fileExists(atPath: support.appendingPathComponent("extension/manifest.json").path)
            else { return false }
        }
        if descriptor.capabilities.contains(.macOSApplication) {
            let apps = (try? manager.contentsOfDirectory(atPath: support.path)) ?? []
            guard apps.contains(where: { $0.hasSuffix(".app") }) else { return false }
        }
        return true
    }

    static func install(_ release: PluginRelease, files: [String: Data],
                        environment: PluginEnvironment = .current) throws {
        lock.lock()
        defer { lock.unlock() }
        try withFileLock(environment) {
            try performInstall(release, files: files, environment: environment)
        }
    }

    private static func performInstall(_ release: PluginRelease, files: [String: Data],
                                       environment: PluginEnvironment) throws {
        let descriptor = release.descriptor
        try PluginProtocol.validateFiles(release.files, id: descriptor.id, kind: descriptor.kind)
        guard descriptor.capabilities.contains(.macOSApplication) == release.files.contains(where: { $0.role == .application }) else {
            throw PluginProtocol.Failure.invalid
        }
        guard PluginProtocol.supports(clientVersion: environment.clientVersion,
                                      minimum: descriptor.minClientVersion) else {
            throw Failure.incompatible(descriptor.minClientVersion)
        }
        if let installed = state(descriptor.id, environment: environment),
           PluginProtocol.newer(installed.version, than: descriptor.version) {
            throw Failure.newerInstalled
        }
        let plan = try plannedFiles(release, files: files, environment: environment)
        let manager = FileManager.default
        let stateDirectory = environment.stateDirectory(descriptor.id)
        let staging = stateDirectory.appendingPathComponent("staging-" + UUID().uuidString)
        try manager.createDirectory(at: staging, withIntermediateDirectories: true)
        defer { try? manager.removeItem(at: staging) }

        // Verify the complete reconstructed native bundle before any target is modified.
        try verifyNativeApplication(release, files: files, staging: staging, environment: environment)

        // Stage every payload first. A failure here leaves the existing
        // installation untouched.
        var staged: [PlannedFile] = []
        for (index, item) in plan.enumerated() {
            let url = staging.appendingPathComponent(String(index))
            do {
                try item.data.write(to: url, options: .atomic)
            } catch {
                throw Failure.writeFailed(item.relative)
            }
            staged.append(item)
        }

        let backup = stateDirectory
            .appendingPathComponent("backups", isDirectory: true)
            .appendingPathComponent(timestamp(), isDirectory: true)
        var applied: [(target: URL, backup: URL?, existed: Bool, permissions: NSNumber?)] = []
        do {
            for (index, item) in staged.enumerated() {
                let target = try checkedPath(home: environment.home, relative: item.relative)
                try manager.createDirectory(at: target.deletingLastPathComponent(),
                                            withIntermediateDirectories: true)
                let exists = manager.fileExists(atPath: target.path)
                var saved: URL?
                let permissions = exists ? (try manager.attributesOfItem(atPath: target.path)[.posixPermissions] as? NSNumber) : nil
                if exists {
                    let current = try Data(contentsOf: target)
                    if current == item.data {
                        applied.append((target, nil, true, permissions))
                        let desired = item.executable ? 0o700 : 0o600
                        if permissions?.intValue != desired {
                            try manager.setAttributes([.posixPermissions: desired], ofItemAtPath: target.path)
                        }
                        continue
                    }
                    let destination = backup.appendingPathComponent("\(index)-" + target.lastPathComponent)
                    try manager.createDirectory(at: backup, withIntermediateDirectories: true)
                    try current.write(to: destination, options: .atomic)
                    saved = destination
                }
                let temporary = target.deletingLastPathComponent()
                    .appendingPathComponent(".ordina-" + UUID().uuidString)
                try item.data.write(to: temporary, options: .atomic)
                if exists {
                    _ = try manager.replaceItemAt(target, withItemAt: temporary)
                } else {
                    try manager.moveItem(at: temporary, to: target)
                }
                applied.append((target, saved, exists, permissions))
                try manager.setAttributes([.posixPermissions: item.executable ? 0o700 : 0o600], ofItemAtPath: target.path)
            }

            // Remove obsolete, unmodified files from earlier versions inside the
            // same transaction; preserve any user-modified files.
            if let previous = state(descriptor.id, environment: environment) {
                let desired = Set(staged.map(\.relative))
                for (index, item) in previous.files.enumerated() where !desired.contains(item.target) {
                    let target = try checkedPath(home: environment.home, relative: item.target)
                    guard manager.fileExists(atPath: target.path),
                          let current = try? Data(contentsOf: target),
                          PluginProtocol.digest(current) == item.sha256 else { continue }
                    let saved = backup.appendingPathComponent("obsolete-\(index)-" + target.lastPathComponent)
                    try manager.createDirectory(at: backup, withIntermediateDirectories: true)
                    try current.write(to: saved, options: .atomic)
                    let permissions = try manager.attributesOfItem(atPath: target.path)[.posixPermissions] as? NSNumber
                    try manager.removeItem(at: target)
                    applied.append((target, saved, true, permissions))
                }
            }

            let recorded = staged.map {
                InstalledFile(target: $0.relative, sha256: PluginProtocol.digest($0.data),
                              role: $0.role.rawValue, executable: $0.executable)
            }
            let marker = legacyMarker(descriptor, environment: environment)?.path
            let record = InstalledPlugin(id: descriptor.id, version: descriptor.version,
                                         descriptor: descriptor, files: recorded,
                                         installedAt: Date().timeIntervalSince1970,
                                         legacyMarker: marker)
            try manager.createDirectory(at: stateDirectory, withIntermediateDirectories: true)
            let encoded = try JSONEncoder().encode(record)
            try encoded.write(to: environment.stateFile(descriptor.id), options: .atomic)
        } catch {
            // Roll back in reverse order: restore backups, remove new files.
            for record in applied.reversed() {
                if let saved = record.backup, let data = try? Data(contentsOf: saved) {
                    try? data.write(to: record.target, options: .atomic)
                    if let permissions = record.permissions {
                        try? manager.setAttributes([.posixPermissions: permissions], ofItemAtPath: record.target.path)
                    }
                } else if !record.existed {
                    try? manager.removeItem(at: record.target)
                } else if let permissions = record.permissions {
                    try? manager.setAttributes([.posixPermissions: permissions], ofItemAtPath: record.target.path)
                }
            }
            throw Failure.writeFailed(error.localizedDescription)
        }

        // Remove pre-protocol names only after the new install is recorded.
        for name in descriptor.legacyNames ?? [] {
            let legacy = try checkedPath(home: environment.home,
                                         relative: relative(environment.utilityDirectory
                                            .appendingPathComponent(name), home: environment.home))
            guard manager.fileExists(atPath: legacy.path) else { continue }
            if legacy.path == utilityTarget(descriptor, environment: environment).path { continue }
            let destination = backup.appendingPathComponent(UUID().uuidString + ".py")
            try manager.createDirectory(at: backup, withIntermediateDirectories: true)
            try manager.moveItem(at: legacy, to: destination)
        }
    }

    static func uninstall(_ descriptor: PluginDescriptor,
                          environment: PluginEnvironment = .current) throws {
        lock.lock()
        defer { lock.unlock() }
        try withFileLock(environment) {
            let manager = FileManager.default
            let record = state(descriptor.id, environment: environment)
            let stateDirectory = environment.stateDirectory(descriptor.id)
            let staging = stateDirectory.appendingPathComponent("uninstall-" + UUID().uuidString, isDirectory: true)
            var targets: [URL] = []
            if let record {
                for file in record.files {
                    targets.append(try checkedPath(home: environment.home, relative: file.target))
                }
            } else {
                targets.append(try checkedPath(home: environment.home,
                    relative: relative(utilityTarget(descriptor, environment: environment), home: environment.home)))
                for name in descriptor.legacyNames ?? [] {
                    targets.append(try checkedPath(home: environment.home,
                        relative: relative(environment.utilityDirectory.appendingPathComponent(name), home: environment.home)))
                }
            }
            try manager.createDirectory(at: staging, withIntermediateDirectories: true)
            var moved: [(original: URL, backup: URL)] = []
            do {
                for (index, target) in targets.enumerated() {
                    guard manager.fileExists(atPath: target.path) else { continue }
                    if let record, let saved = record.files.first(where: {
                        environment.home.appendingPathComponent($0.target).standardizedFileURL == target.standardizedFileURL
                    }) {
                        let data = try Data(contentsOf: target)
                        // Changed files may contain user data and are not removed.
                        if PluginProtocol.digest(data) != saved.sha256 { continue }
                    }
                    let backup = staging.appendingPathComponent(String(index))
                    try manager.moveItem(at: target, to: backup)
                    moved.append((target, backup))
                }
                let stateFile = environment.stateFile(descriptor.id)
                if manager.fileExists(atPath: stateFile.path) {
                    let backup = staging.appendingPathComponent("state.json")
                    try manager.moveItem(at: stateFile, to: backup)
                    moved.append((stateFile, backup))
                }
            } catch {
                var rollbackFailed = false
                for item in moved.reversed() {
                    do { try manager.moveItem(at: item.backup, to: item.original) }
                    catch { rollbackFailed = true }
                }
                if rollbackFailed {
                    throw Failure.writeFailed("卸载回滚未完成，备份保留在 " + staging.path)
                }
                try? manager.removeItem(at: staging)
                throw Failure.writeFailed(error.localizedDescription)
            }
            try? manager.removeItem(at: staging)
            if let remaining = try? manager.contentsOfDirectory(atPath: stateDirectory.path), remaining.isEmpty {
                try? manager.removeItem(at: stateDirectory)
            }
            // Remove now-empty plugin-owned subdirectories (for example
            // extension/) without touching anything the user added.
            if let support = supportDirectory(descriptor, environment: environment) {
                var directories = Set<URL>()
                for target in targets where target.path.hasPrefix(support.path + "/") {
                    var directory = target.deletingLastPathComponent()
                    while directory.path.hasPrefix(support.path + "/") {
                        directories.insert(directory)
                        directory.deleteLastPathComponent()
                    }
                }
                for directory in directories.sorted(by: { $0.path.count > $1.path.count }) {
                    if let contents = try? manager.contentsOfDirectory(atPath: directory.path),
                       contents.isEmpty {
                        try? manager.removeItem(at: directory)
                    }
                }
            }
            // Keep the support directory when the user has data in it.
            if let support = supportDirectory(descriptor, environment: environment),
               let contents = try? manager.contentsOfDirectory(atPath: support.path),
               contents.isEmpty {
                try? manager.removeItem(at: support)
            }
        }
    }

    // Files this client manages, identified by their recorded home-relative
    // target path — never by display name. Two unrelated scripts sharing a
    // name must stay distinguishable, and a takeover must transfer ownership
    // into the installation record instead of hiding an arbitrary row.
    static func managedScriptTargets(environment: PluginEnvironment = .current) -> Set<String> {
        var targets: Set<String> = []
        guard let entries = try? FileManager.default.contentsOfDirectory(
            at: environment.supportRoot, includingPropertiesForKeys: nil) else { return targets }
        for directory in entries {
            let id = directory.lastPathComponent
            guard let record = state(id, environment: environment) else { continue }
            for file in record.files where file.role == PluginFileRole.utilityScript.rawValue {
                targets.insert(file.target)
            }
            let utilityBase = relative(environment.utilityDirectory, home: environment.home)
            for name in record.descriptor.legacyNames ?? [] {
                targets.insert(utilityBase + "/" + name)
            }
        }
        return targets
    }

    static func supportDirectory(_ descriptor: PluginDescriptor,
                                 environment: PluginEnvironment) -> URL? {
        // A missing name falls back to the plugin id, which is already
        // restricted to a single safe path component.
        let name = descriptor.supportDirectoryName ?? descriptor.id
        guard PluginProtocol.validSupportName(name) else { return nil }
        return environment.home.appendingPathComponent("Library/Application Support", isDirectory: true)
            .appendingPathComponent(name, isDirectory: true)
    }

    static func extensionDirectory(_ descriptor: PluginDescriptor,
                                   environment: PluginEnvironment = .current) -> URL? {
        supportDirectory(descriptor, environment: environment)?
            .appendingPathComponent("extension", isDirectory: true)
    }

    static func utilityTarget(_ descriptor: PluginDescriptor,
                              environment: PluginEnvironment) -> URL {
        let name = descriptor.utilityScript ?? "\(descriptor.name).py"
        return environment.utilityDirectory.appendingPathComponent(name)
    }

    private static func plannedFiles(_ release: PluginRelease, files: [String: Data],
                                     environment: PluginEnvironment) throws -> [PlannedFile] {
        let descriptor = release.descriptor
        // Re-check the signed inventory even though the download layer already
        // did: the installer must never write bytes it cannot attribute.
        for item in release.files {
            guard let data = files[item.path], data.count == item.size,
                  PluginProtocol.digest(data) == item.sha256 else {
                throw PluginProtocol.Failure.corrupted
            }
        }
        var result: [PlannedFile] = []
        let nativeHost = descriptor.nativeMessagingHost
            ?? release.files.first(where: { $0.role == .nativeMessagingHost })?.path
        for item in release.files {
            guard let data = files[item.path] else { throw Failure.missingSupport(item.path) }
            switch item.role {
            case .metadata:
                continue
            case .utilityScript:
                let name = item.installName ?? (item.path as NSString).lastPathComponent
                guard PluginProtocol.validInstallName(name) else { throw Failure.writeFailed(item.path) }
                result.append(PlannedFile(
                    relative: relative(environment.utilityDirectory.appendingPathComponent(name),
                                       home: environment.home),
                    data: data, executable: item.executable ?? false, role: item.role))
            case .support, .nativeMessagingHost, .extension, .application:
                guard let support = supportDirectory(descriptor, environment: environment) else {
                    throw Failure.writeFailed(item.path)
                }
                result.append(PlannedFile(
                    relative: relative(support.appendingPathComponent(item.path),
                                       home: environment.home),
                    data: data, executable: item.executable ?? false, role: item.role))
            case .nativeMessagingManifest:
                guard descriptor.capabilities.contains(.chromeNativeMessagingHost) else {
                    throw Failure.writeFailed(item.path)
                }
                guard let host = nativeHost,
                      let support = supportDirectory(descriptor, environment: environment)
                else { throw Failure.invalidManifest }
                let hostURL = support.appendingPathComponent(host)
                let generated = try generatedManifest(template: data, descriptor: descriptor,
                                                      host: hostURL)
                let name = item.installName ?? (item.path as NSString).lastPathComponent
                guard PluginProtocol.validInstallName(name) else { throw Failure.invalidManifest }
                result.append(PlannedFile(
                    relative: relative(environment.chromeDirectory.appendingPathComponent(name),
                                       home: environment.home),
                    data: generated, executable: false, role: item.role))
            }
        }
        return result
    }

    private static func verifyNativeApplication(_ release: PluginRelease, files: [String: Data],
                                                staging: URL, environment: PluginEnvironment) throws {
        let native = release.files.filter { $0.role == .application }
        guard !native.isEmpty else { return }
        guard release.descriptor.capabilities.contains(.macOSApplication),
              let root = native.first?.path.split(separator: "/").first.map(String.init),
              root.hasSuffix(".app"), native.allSatisfy({ $0.path.hasPrefix(root + "/Contents/") }) else {
            throw Failure.writeFailed("原生 App 结构无效")
        }
        let manager = FileManager.default
        for item in native {
            guard let data = files[item.path], data.count == item.size,
                  PluginProtocol.digest(data) == item.sha256 else { throw PluginProtocol.Failure.corrupted }
            let destination = staging.appendingPathComponent(item.path)
            try manager.createDirectory(at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
            try data.write(to: destination, options: .atomic)
            if item.executable == true { try manager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: destination.path) }
        }
        let application = staging.appendingPathComponent(root)
        func run(_ executable: String, _ arguments: [String]) throws -> String {
            let process = Process(); let output = Pipe()
            process.executableURL = URL(fileURLWithPath: executable)
            process.arguments = arguments; process.standardOutput = output; process.standardError = output
            try process.run()
            let data = output.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            guard process.terminationStatus == 0 else { throw Failure.writeFailed("原生 App 签名验证失败") }
            return String(decoding: data, as: UTF8.self)
        }
        _ = try run("/usr/bin/codesign", ["--verify", "--deep", "--strict", application.path])
        var staticCode: SecStaticCode?
        guard SecStaticCodeCreateWithPath(application as CFURL, [], &staticCode) == errSecSuccess,
              let staticCode else { throw Failure.writeFailed("原生 App 签名无效") }
        guard SecStaticCodeCheckValidity(staticCode, SecCSFlags(rawValue: kSecCSCheckAllArchitectures | kSecCSStrictValidate), nil) == errSecSuccess else {
            throw Failure.writeFailed("原生 App 签名无效")
        }
    }

    private static func generatedManifest(template: Data, descriptor: PluginDescriptor,
                                          host: URL) throws -> Data {
        guard let object = try? JSONSerialization.jsonObject(with: template) as? [String: Any],
              let name = object["name"] as? String, !name.isEmpty,
              let origins = object["allowed_origins"] as? [String], !origins.isEmpty,
              origins.allSatisfy({ $0.hasPrefix("chrome-extension://") && $0.hasSuffix("/") })
        else { throw Failure.invalidManifest }
        let payload: [String: Any] = [
            "name": name,
            "description": object["description"] as? String ?? descriptor.name,
            "type": "stdio",
            "path": host.path,
            "allowed_origins": origins,
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: payload,
                                                     options: [.sortedKeys, .prettyPrinted]) else {
            throw Failure.invalidManifest
        }
        return data
    }

    private static func legacyMarker(_ descriptor: PluginDescriptor,
                                     environment: PluginEnvironment) -> (path: String, version: String?)? {
        guard let support = supportDirectory(descriptor, environment: environment) else { return nil }
        let marker = support.appendingPathComponent("installed.json")
        guard let data = try? Data(contentsOf: marker),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              object["id"] as? String == descriptor.id else { return nil }
        return (relative(marker, home: environment.home), object["version"] as? String)
    }

    private static func timestamp() -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        return formatter.string(from: Date()) + "-" + String(UUID().uuidString.prefix(8))
    }

    private static func relative(_ url: URL, home: URL) -> String {
        let base = home.standardizedFileURL.path
        let path = url.standardizedFileURL.path
        guard path.hasPrefix(base + "/") else { return path }
        return String(path.dropFirst(base.count + 1))
    }

    // Reject links anywhere below home, including dangling ones; only metadata
    // is read here.
    private static func checkedPath(home: URL, relative: String) throws -> URL {
        var url = home
        for component in relative.split(separator: "/") {
            url.appendPathComponent(String(component))
            if (try? FileManager.default.destinationOfSymbolicLink(atPath: url.path)) != nil {
                throw Failure.linkedPath
            }
        }
        return url
    }

    private static func withFileLock<T>(_ environment: PluginEnvironment,
                                        _ body: () throws -> T) throws -> T {
        let manager = FileManager.default
        try manager.createDirectory(at: environment.supportRoot, withIntermediateDirectories: true)
        let path = environment.supportRoot.appendingPathComponent(".lock").path
        let descriptor = open(path, O_CREAT | O_RDWR, 0o600)
        guard descriptor >= 0 else { throw Failure.writeFailed(".lock") }
        defer { close(descriptor) }
        guard flock(descriptor, LOCK_EX) == 0 else { throw Failure.writeFailed(".lock") }
        defer { flock(descriptor, LOCK_UN) }
        return try body()
    }
}
