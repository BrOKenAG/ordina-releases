import Foundation

@main
struct VerifyIntegration {
    static func main() throws {
        let files = FileManager.default
        let home = files.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try files.createDirectory(at: home, withIntermediateDirectories: true)
        defer { try? files.removeItem(at: home) }
        let source = home.appendingPathComponent("source.py")
        let target = home.appendingPathComponent("Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/Project Setup.py")
        let backup = home.appendingPathComponent("Library/Application Support/com.example.MagicTools.Youth/Backups/Project Setup")
        try Data("new".utf8).write(to: source)
        try IntegrationInstaller.install(home: home, source: source)
        let installed = try Data(contentsOf: target)
        precondition(installed == Data("new".utf8))
        try Data("custom".utf8).write(to: target)
        try IntegrationInstaller.install(home: home, source: source)
        let backups = try files.contentsOfDirectory(at: backup, includingPropertiesForKeys: nil)
        precondition(backups.count == 1)
        let saved = try Data(contentsOf: backups[0])
        precondition(saved == Data("custom".utf8))
        try IntegrationInstaller.install(home: home, source: source)
        let repeated = try files.contentsOfDirectory(atPath: backup.path)
        precondition(repeated.count == 1)
        try Data("PLUGIN_VERSION = \"v2.3\"\n".utf8).write(to: source)
        let newer = Data("PLUGIN_VERSION = \"v2.10\"\n".utf8)
        try newer.write(to: target)
        let newerStatus = try IntegrationInstaller.inspect(home: home, source: source)
        precondition(newerStatus == .current)
        try IntegrationInstaller.install(home: home, source: source)
        let preserved = try Data(contentsOf: target)
        precondition(preserved == newer, "必须保留比 App 内置版本更新的插件")
        do {
            try IntegrationInstaller.install(home: home, source: nil)
            fatalError("应拒绝缺失资源")
        } catch is ResolveFailure {}
        print("集成安装、备份与重复安装验证通过")
    }
}
