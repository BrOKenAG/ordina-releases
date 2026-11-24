import Foundation

enum OptionalPlugin: String, CaseIterable, Identifiable, Sendable {
    case yueliu
    case multicam
    case minutes

    var id: String { rawValue }

    var name: String { resourceName }

    var resourceName: String {
        switch self {
        case .yueliu: return "Import YueLiu"
        case .multicam: return "Multicam Tools"
        case .minutes: return "Minutes Roughcut"
        }
    }

    var filename: String { resourceName + ".py" }

    var legacyFilenames: [String] {
        self == .yueliu ? ["导入阅流.py"] : []
    }

    var dependencyHint: String? {
        switch self {
        case .yueliu: return "需在 Resolve 使用的 Python 环境中安装 openpyxl。"
        case .multicam: return nil
        case .minutes: return "需要 Chrome 和 DaVinci Resolve Studio。"
        }
    }
}

enum OptionalPluginInstaller {
    enum Failure: LocalizedError, Sendable {
        case sourceUnavailable(String)
        case linkedPath
        case newerInstalled

        var errorDescription: String? {
            switch self {
            case .sourceUnavailable(let name):
                return "无法读取 \(name) 资源，请重新安装 ordina 后重试。"
            case .newerInstalled:
                return "已安装更新的插件版本。"
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
    private static let minutesSupportPath = "Library/Application Support/Minutes Roughcut"
    private static let minutesBundleName = "Minutes Roughcut"
    private static let minutesChromeManifest = "Library/Application Support/Google/Chrome/NativeMessagingHosts/com.minutesroughcut.bridge.json"
    private static let minutesRequired = [
        "plugin.json", "Minutes Roughcut.py", "host.py", "host.command",
        "extension/manifest.json",
    ]

    static func inspect(
        _ plugin: OptionalPlugin,
        home: URL = FileManager.default.homeDirectoryForCurrentUser,
        source: URL? = nil
    ) throws -> IntegrationInstaller.Status {
        lock.lock()
        defer { lock.unlock() }
        if plugin == .minutes {
            let pairs = [
                ("Minutes Roughcut.py", utilityPath + "/Minutes Roughcut.py"),
                ("host.py", minutesSupportPath + "/host.py"),
                ("host.command", minutesSupportPath + "/host.command"),
                ("extension/manifest.json", minutesSupportPath + "/extension/manifest.json"),
            ]
            for (_, destination) in pairs {
                let target = try checkedPath(home: home, relative: destination)
                guard FileManager.default.fileExists(atPath: target.path) else { return .missing }
            }
            let manifest = try checkedPath(home: home, relative: minutesChromeManifest)
            guard FileManager.default.fileExists(atPath: manifest.path) else { return .missing }
            let versions = try minutesVersions(home: home)
            guard let installed = versions.installed,
                  let bundled = versions.bundled else { return .outdated }
            return versionNewer(bundled, than: installed) ? .outdated : .current
        }
        let data = try sourceData(for: plugin, source: source)
        let target = try checkedPath(home: home, relative: utilityPath + "/" + plugin.filename)
        for filename in plugin.legacyFilenames {
            let legacy = try checkedPath(home: home, relative: utilityPath + "/" + filename)
            if FileManager.default.fileExists(atPath: legacy.path) { return .outdated }
        }
        guard FileManager.default.fileExists(atPath: target.path) else { return .missing }
        let installed = try Data(contentsOf: target)
        return installed == data || isNewer(installed, than: data) ? .current : .outdated
    }

    static func install(
        _ plugin: OptionalPlugin,
        home: URL = FileManager.default.homeDirectoryForCurrentUser,
        source: URL? = nil
    ) throws {
        lock.lock()
        defer { lock.unlock() }
        if plugin == .minutes {
            // The installer script always comes from the signed app. `source`
            // may be an extracted, signature-verified plugin bundle.
            guard let installer = Bundle.main.resourceURL?
                .appendingPathComponent(minutesBundleName)
                .appendingPathComponent("install.py"),
                  FileManager.default.fileExists(atPath: installer.path) else {
                throw Failure.sourceUnavailable(plugin.name)
            }
            let base = source ?? Bundle.main.resourceURL?.appendingPathComponent(minutesBundleName)
            guard let base else { throw Failure.sourceUnavailable(plugin.name) }
            for item in minutesRequired where !FileManager.default.fileExists(atPath: base.appendingPathComponent(item).path) {
                throw Failure.sourceUnavailable(plugin.name)
            }
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
            process.arguments = [installer.path]
            var environment = ProcessInfo.processInfo.environment
            environment["MINUTES_INSTALL_SOURCE"] = base.path
            environment["HOME"] = home.path
            process.environment = environment
            let pipe = Pipe()
            process.standardOutput = pipe
            process.standardError = pipe
            try process.run()
            let output = pipe.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            guard process.terminationStatus == 0 else {
                throw ResolveFailure(message: String(data: output, encoding: .utf8) ?? "安装失败")
            }
            return
        }
        // Load the full resource before creating folders or touching an install.
        let data = try sourceData(for: plugin, source: source)
        let files = FileManager.default
        let target = try checkedPath(home: home, relative: utilityPath + "/" + plugin.filename)
        let legacy = try plugin.legacyFilenames.map {
            try checkedPath(home: home, relative: utilityPath + "/" + $0)
        }.filter { files.fileExists(atPath: $0.path) }
        let previous = files.fileExists(atPath: target.path) ? try Data(contentsOf: target) : nil
        let legacyData = try legacy.map { try Data(contentsOf: $0) }
        if let previous, (previous == data || isNewer(previous, than: data)) && legacy.isEmpty { return }
        if let previous, isNewer(previous, than: data) { throw Failure.newerInstalled }
        let saved = (previous.map { $0 == data ? [] : [$0] } ?? []) + legacyData
        if !saved.isEmpty {
            let backups = try checkedPath(home: home, relative: backupsPath + "/" + plugin.id)
            try files.createDirectory(at: backups, withIntermediateDirectories: true)
            for item in saved {
                let backup = backups.appendingPathComponent(UUID().uuidString + ".py")
                try item.write(to: backup, options: .atomic)
            }
        }
        try files.createDirectory(at: target.deletingLastPathComponent(), withIntermediateDirectories: true)
        if previous != data { try data.write(to: target, options: .atomic) }
        // Remove legacy menu entries only after preserving every old script
        // and successfully installing the replacement.
        for old in legacy { try files.removeItem(at: old) }
    }

    // Installed and bundled bundle versions. Comparing versions instead of
    // bytes means an independently published plugin isn't reported outdated.
    static func minutesVersions(
        home: URL = FileManager.default.homeDirectoryForCurrentUser
    ) throws -> (installed: String?, bundled: String?) {
        let marker = try checkedPath(home: home, relative: minutesSupportPath + "/installed.json")
        let installed = (try? Data(contentsOf: marker)).flatMap { bundleVersion($0, id: "minutes") }
        var bundled: String?
        if let url = Bundle.main.resourceURL?
            .appendingPathComponent(minutesBundleName)
            .appendingPathComponent("plugin.json"),
           let data = try? Data(contentsOf: url) {
            bundled = bundleVersion(data, id: "minutes")
        }
        return (installed, bundled)
    }

    static func minutesExtensionDirectory(
        home: URL = FileManager.default.homeDirectoryForCurrentUser
    ) throws -> URL {
        try checkedPath(home: home, relative: minutesSupportPath + "/extension")
    }

    private static func bundleVersion(_ data: Data, id: String) -> String? {
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              object["id"] as? String == id,
              let value = object["version"] as? String,
              value.range(of: #"^[0-9]+(\.[0-9]+){1,3}$"#, options: .regularExpression) != nil else { return nil }
        return value
    }

    private static func versionNewer(_ candidate: String, than installed: String) -> Bool {
        candidate.compare(installed, options: .numeric) == .orderedDescending
    }

    private static func isNewer(_ installed: Data, than source: Data) -> Bool {
        func version(_ data: Data) -> String? {
            guard let text = String(data: data, encoding: .utf8),
                  let regex = try? NSRegularExpression(pattern: #"(?m)^PLUGIN_VERSION\s*=\s*["']v?([0-9]+(?:\.[0-9]+)+)["']"#),
                  let match = regex.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)),
                  let range = Range(match.range(at: 1), in: text) else { return nil }
            return String(text[range])
        }
        guard let a = version(installed), let b = version(source) else { return false }
        return a.compare(b, options: .numeric) == .orderedDescending
    }

    private static func sourceData(for plugin: OptionalPlugin, source: URL?) throws -> Data {
        guard let url = source ?? Bundle.main.url(forResource: plugin.resourceName, withExtension: "py") else {
            throw Failure.sourceUnavailable(plugin.name)
        }
        do {
            return try Data(contentsOf: url)
        } catch {
            throw Failure.sourceUnavailable(plugin.name)
        }
    }

    // All components come from the fixed enum allowlist. Reject links below
    // the supplied home, including dangling links, rather than following them
    // into a different script directory. This function only reads metadata.
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
        var id: URL { source }
    }

    static func scriptDirectory(home: URL = FileManager.default.homeDirectoryForCurrentUser) throws -> URL {
        try checkedPath(home: home, relative: utilityPath)
    }

    static func localScripts(home: URL = FileManager.default.homeDirectoryForCurrentUser) throws -> [LocalScript] {
        let directory = try scriptDirectory(home: home)
        let files = FileManager.default
        guard files.fileExists(atPath: directory.path) else { return [] }
        let bundled = Set(OptionalPlugin.allCases.flatMap { [$0.filename] + $0.legacyFilenames } + ["Project Setup.py"])
        return try files.contentsOfDirectory(at: directory, includingPropertiesForKeys: [.isRegularFileKey, .isSymbolicLinkKey],
                                             options: .skipsHiddenFiles)
            .filter { $0.pathExtension.lowercased() == "py" && !bundled.contains($0.lastPathComponent) && isRegularScript($0) }
            .sorted { $0.lastPathComponent.localizedStandardCompare($1.lastPathComponent) == .orderedAscending }
            .map { LocalScript(url: $0, summary: summary(at: $0)) }
    }

    static func prepareImport(_ source: URL, home: URL = FileManager.default.homeDirectoryForCurrentUser) throws -> Import {
        let accessed = source.startAccessingSecurityScopedResource()
        defer { if accessed { source.stopAccessingSecurityScopedResource() } }
        guard isRegularScript(source) else { throw ResolveFailure(message: "请选择一个 Python 脚本（.py）。") }
        let destination = try checkedPath(home: home, relative: utilityPath + "/" + source.lastPathComponent)
        guard source.standardizedFileURL != destination.standardizedFileURL else {
            throw ResolveFailure(message: "此脚本已在安装目录中。")
        }
        let existing = FileManager.default.fileExists(atPath: destination.path) ? try Data(contentsOf: destination) : nil
        return Import(source: source, destination: destination, existing: existing)
    }

    static func install(_ pending: Import, home: URL = FileManager.default.homeDirectoryForCurrentUser) throws {
        lock.lock()
        defer { lock.unlock() }
        let accessed = pending.source.startAccessingSecurityScopedResource()
        defer { if accessed { pending.source.stopAccessingSecurityScopedResource() } }
        let verified = try prepareImport(pending.source, home: home)
        guard verified.destination == pending.destination, verified.existing == pending.existing else {
            throw ResolveFailure(message: "同名脚本已变化，请重新选择文件。")
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

    static func removeLocalScript(_ script: LocalScript, home: URL = FileManager.default.homeDirectoryForCurrentUser) throws {
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
