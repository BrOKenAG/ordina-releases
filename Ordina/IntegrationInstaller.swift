import Foundation

enum IntegrationInstaller {
    enum Status: Equatable {
        case current
        case missing
        case outdated
    }

    static func inspect(home: URL = FileManager.default.homeDirectoryForCurrentUser, source: URL? = Bundle.main.url(forResource: "Project Setup", withExtension: "py")) throws -> Status {
        guard let source else { throw ResolveFailure(message: "缺少 Project Setup，请重新安装 ordina。") }
        let data = try Data(contentsOf: source)
        let target = home.appendingPathComponent("Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/Project Setup.py")
        guard FileManager.default.fileExists(atPath: target.path) else { return .missing }
        let installed = try Data(contentsOf: target)
        return installed == data || isNewer(installed, than: data) ? .current : .outdated
    }

    static func install(home: URL = FileManager.default.homeDirectoryForCurrentUser, source: URL? = Bundle.main.url(forResource: "Project Setup", withExtension: "py")) throws {
        guard let source else { throw ResolveFailure(message: "缺少 Project Setup，请重新安装 ordina。") }
        let directory = home.appendingPathComponent("Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility")
        let target = directory.appendingPathComponent("Project Setup.py")
        let data = try Data(contentsOf: source)
        let files = FileManager.default
        try files.createDirectory(at: directory, withIntermediateDirectories: true)
        if files.fileExists(atPath: target.path) {
            let previous = try Data(contentsOf: target)
            if previous == data || isNewer(previous, than: data) { return }
            let backups = home.appendingPathComponent("Library/Application Support/com.example.MagicTools.Youth/Backups/Project Setup")
            try files.createDirectory(at: backups, withIntermediateDirectories: true)
            try previous.write(to: backups.appendingPathComponent(UUID().uuidString + ".py"), options: .atomic)
        }
        try data.write(to: target, options: .atomic)
    }

    private static func isNewer(_ installed: Data, than bundled: Data) -> Bool {
        func version(_ data: Data) -> String? {
            guard let text = String(data: data, encoding: .utf8),
                  let range = text.range(of: #"(?m)^PLUGIN_VERSION\s*=\s*["']v?([0-9]+(?:\.[0-9]+)*)["']"#,
                                         options: .regularExpression) else { return nil }
            return String(text[range]).components(separatedBy: CharacterSet(charactersIn: "0123456789.").inverted)
                .first { !$0.isEmpty }
        }
        guard let installedVersion = version(installed), let bundledVersion = version(bundled) else { return false }
        return installedVersion.compare(bundledVersion, options: .numeric) == .orderedDescending
    }
}
