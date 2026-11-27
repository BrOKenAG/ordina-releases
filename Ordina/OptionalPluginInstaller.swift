import Foundation

// User-supplied Resolve scripts. Plugin installation lives in PluginInstaller;
// this type only handles the local "import a .py" flow.
enum OptionalPluginInstaller {
    static var defaultHome: URL {
        #if DEBUG || ORDINA_LAB
        if ProcessInfo.processInfo.environment["ORDINA_DEV_LAB"] == "1",
           let home = ProcessInfo.processInfo.environment["HOME"], home.hasPrefix("/") {
            return URL(fileURLWithPath: home, isDirectory: true)
        }
        #endif
        return FileManager.default.homeDirectoryForCurrentUser
    }
    enum Failure: LocalizedError, Sendable {
        case linkedPath

        var errorDescription: String? {
            switch self {
            case .linkedPath:
                return "安装路径包含符号链接，请改用实际目录后重试。"
            }
        }
    }

    // Both embedding views can be open at once. Serialize file operations so
    // simultaneous clicks cannot back up an already replaced version.
    private static let lock = NSLock()
    private static let utilityPath = "Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility"
    private static let backupsPath = "Library/Application Support/com.example.MagicTools.Youth/Backups"

    // All components come from validated file names. Reject links below the
    // supplied home, including dangling links, rather than following them into
    // a different script directory. This function only reads metadata.
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
}

extension OptionalPluginInstaller {
    struct LocalScript: Identifiable, Sendable {
        let url: URL
        let summary: String?
        var id: URL { url }
        var name: String { url.deletingPathExtension().lastPathComponent }
    }

    struct Import: Identifiable, Sendable {
        let source: URL
        let destination: URL
        let existing: Data?
        let sourceDigest: String
        var id: URL { source }
    }

    static func scriptDirectory(home: URL = defaultHome) throws -> URL {
        try checkedPath(home: home, relative: utilityPath)
    }

    static func localScripts(home: URL = defaultHome) throws -> [LocalScript] {
        let directory = try scriptDirectory(home: home)
        let files = FileManager.default
        guard files.fileExists(atPath: directory.path) else { return [] }
        let environment = PluginEnvironment.production(
            home: home, clientVersion: PluginEnvironment.current.clientVersion)
        let managed = PluginInstaller.managedScriptTargets(environment: environment)
        let builtIn = relative(environment.utilityDirectory.appendingPathComponent("Project Setup.py"),
                               home: home)
        return try files.contentsOfDirectory(at: directory, includingPropertiesForKeys: [.isRegularFileKey, .isSymbolicLinkKey],
                                             options: .skipsHiddenFiles)
            .filter { url in
                guard url.pathExtension.lowercased() == "py", isRegularScript(url) else { return false }
                let target = relative(url, home: home)
                return !managed.contains(target) && target != builtIn
            }
            .sorted { $0.lastPathComponent.localizedStandardCompare($1.lastPathComponent) == .orderedAscending }
            .map { LocalScript(url: $0, summary: summary(at: $0)) }
    }

    static func prepareImport(_ source: URL, home: URL = defaultHome) throws -> Import {
        let accessed = source.startAccessingSecurityScopedResource()
        defer { if accessed { source.stopAccessingSecurityScopedResource() } }
        guard isRegularScript(source) else { throw ResolveFailure(message: "请选择一个 Python 脚本（.py）。") }
        let destination = try checkedPath(home: home, relative: utilityPath + "/" + source.lastPathComponent)
        guard source.standardizedFileURL != destination.standardizedFileURL else {
            throw ResolveFailure(message: "此脚本已在安装目录中。")
        }
        let data = try Data(contentsOf: source)
        let existing = FileManager.default.fileExists(atPath: destination.path) ? try Data(contentsOf: destination) : nil
        return Import(source: source, destination: destination, existing: existing,
                      sourceDigest: PluginProtocol.digest(data))
    }

    static func install(_ pending: Import, home: URL = defaultHome) throws {
        lock.lock()
        defer { lock.unlock() }
        let accessed = pending.source.startAccessingSecurityScopedResource()
        defer { if accessed { pending.source.stopAccessingSecurityScopedResource() } }
        let verified = try prepareImport(pending.source, home: home)
        guard verified.destination == pending.destination,
              verified.existing == pending.existing,
              verified.sourceDigest == pending.sourceDigest else {
            throw ResolveFailure(message: "脚本已变化，请重新选择文件。")
        }
        let data = try Data(contentsOf: pending.source)
        if data == verified.existing { return }
        let files = FileManager.default
        if let existing = verified.existing {
            try existing.write(to: try backupURL(for: verified.destination, home: home), options: .atomic)
        }
        try files.createDirectory(at: verified.destination.deletingLastPathComponent(), withIntermediateDirectories: true)
        try data.write(to: verified.destination, options: verified.existing == nil ? .withoutOverwriting : .atomic)
    }

    static func removeLocalScript(_ script: LocalScript, home: URL = defaultHome) throws {
        lock.lock()
        defer { lock.unlock() }
        let target = try checkedPath(home: home, relative: utilityPath + "/" + script.url.lastPathComponent)
        guard target.standardizedFileURL.path == script.url.standardizedFileURL.path, isRegularScript(target) else {
            throw ResolveFailure(message: "脚本已变化，请刷新后重试。")
        }
        try FileManager.default.moveItem(at: target, to: try backupURL(for: target, home: home))
    }

    private static func backupURL(for file: URL, home: URL) throws -> URL {
        let backup = try checkedPath(home: home, relative: backupsPath + "/Resolve Scripts")
            .appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: backup, withIntermediateDirectories: true)
        return backup.appendingPathComponent(file.lastPathComponent)
    }

    private static func relative(_ url: URL, home: URL) -> String {
        let base = home.standardizedFileURL.path
        let path = url.standardizedFileURL.path
        guard path.hasPrefix(base + "/") else { return path }
        return String(path.dropFirst(base.count + 1))
    }

    private static func isRegularScript(_ url: URL) -> Bool {
        guard url.isFileURL, url.pathExtension.lowercased() == "py", !url.lastPathComponent.hasPrefix("."),
              let values = try? url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey]) else { return false }
        return values.isRegularFile == true && values.isSymbolicLink != true
    }

    private static func summary(at url: URL) -> String? {
        guard let handle = try? FileHandle(forReadingFrom: url) else { return nil }
        defer { try? handle.close() }
        guard let data = try? handle.read(upToCount: 4096), let text = String(data: data, encoding: .utf8) else { return nil }
        let lines = text.split(whereSeparator: \.isNewline)
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .drop(while: { $0.hasPrefix("#") })
        guard let first = lines.first else { return nil }
        let quote = String(first.prefix(3))
        guard quote == "\"\"\"" || quote == "'''" else { return nil }
        let body = first.dropFirst(3)
        let title = (body.isEmpty ? String(lines.dropFirst().first ?? "") : String(body))
            .replacingOccurrences(of: quote, with: "").trimmingCharacters(in: .whitespaces)
        return title.isEmpty ? nil : String(title.prefix(120))
    }
}
