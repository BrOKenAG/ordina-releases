import Foundation

// Standalone temporary-home verification. Never executes the Python resources.
@main
struct VerifyOptionalPlugins {
    private enum CheckFailure: Error {
        case failed(String)
    }

    private static func require(_ condition: Bool, _ message: String) throws {
        if !condition { throw CheckFailure.failed(message) }
    }

    private static func expectFailure(_ message: String, _ operation: () throws -> Void) throws {
        do {
            try operation()
        } catch {
            return
        }
        throw CheckFailure.failed(message)
    }

    // Includes empty directories, file contents and modification dates, so a
    // read-only inspection cannot silently create folders or rewrite files.
    private struct Entry: Equatable {
        let type: String
        let modified: Date?
        let data: Data?
    }

    private static func snapshot(_ root: URL) throws -> [String: Entry] {
        let files = FileManager.default
        var result: [String: Entry] = [:]
        func visit(_ url: URL, relative: String) throws {
            let attributes = try files.attributesOfItem(atPath: url.path)
            let type = attributes[.type] as? FileAttributeType
            result[relative] = Entry(
                type: type?.rawValue ?? "unknown",
                modified: attributes[.modificationDate] as? Date,
                data: type == .typeRegular ? try Data(contentsOf: url) : nil
            )
            if type == .typeDirectory {
                for child in try files.contentsOfDirectory(at: url, includingPropertiesForKeys: nil) {
                    try visit(child, relative: relative + "/" + child.lastPathComponent)
                }
            }
        }
        try visit(root, relative: "")
        return result
    }

    static func main() throws {
        let files = FileManager.default
        let root = files.temporaryDirectory.appendingPathComponent("Ordina-OptionalPlugins-" + UUID().uuidString)
        try files.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? files.removeItem(at: root) }

        try require(OptionalPlugin.allCases.map(\.id) == ["yueliu", "multicam"], "插件白名单不符")
        try require(OptionalPlugin.yueliu.name == "Import YueLiu", "阅流显示名称不符")
        try require(OptionalPlugin.yueliu.filename == "Import YueLiu.py", "阅流文件名不符")
        try require(OptionalPlugin.multicam.filename == "Multicam Tools.py", "多机位文件名不符")
        try require(OptionalPlugin.yueliu.dependencyHint?.contains("openpyxl") == true, "缺少阅流依赖提示")
        try require(OptionalPlugin.multicam.dependencyHint == nil, "多机位不应有额外依赖提示")

        for plugin in OptionalPlugin.allCases {
            let home = root.appendingPathComponent(plugin.id)
            let source = root.appendingPathComponent(plugin.filename)
            let missingSource = root.appendingPathComponent("missing-" + plugin.filename)
            let target = home.appendingPathComponent("Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/" + plugin.filename)
            let backups = home.appendingPathComponent("Library/Application Support/com.example.MagicTools.Youth/Backups/" + plugin.id)
            let original = Data("# bundled \(plugin.id)\n".utf8)
            let custom = Data("# user's custom \(plugin.id)\n".utf8)
            try original.write(to: source)

            // Even a completely absent home must remain absent after inspect.
            let beforeMissing = try snapshot(root)
            let missing = try OptionalPluginInstaller.inspect(plugin, home: home, source: source)
            try require(missing == .missing, "应报告未安装")
            try require(try snapshot(root) == beforeMissing, "检测未安装状态不应写文件")

            try expectFailure("缺失源文件应拒绝安装") {
                try OptionalPluginInstaller.install(plugin, home: home, source: missingSource)
            }
            try require(try snapshot(root) == beforeMissing, "源文件缺失时不应创建目录")

            let unrelated = home.appendingPathComponent("Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/Project Setup.py")
            let unrelatedData = Data("# unrelated integration\n".utf8)
            try files.createDirectory(at: unrelated.deletingLastPathComponent(), withIntermediateDirectories: true)
            try unrelatedData.write(to: unrelated)
            try OptionalPluginInstaller.install(plugin, home: home, source: source)
            try require(try Data(contentsOf: target) == original, "首次安装内容不符")
            try require(!files.fileExists(atPath: backups.path), "首次安装不应备份")
            let installedSnapshot = try snapshot(root)
            let current = try OptionalPluginInstaller.inspect(plugin, home: home, source: source)
            try require(current == .current, "应报告已安装")
            try require(try snapshot(root) == installedSnapshot, "检测已安装状态不应写文件")
            try OptionalPluginInstaller.install(plugin, home: home, source: source)
            try require(try snapshot(root) == installedSnapshot, "相同内容重装应完全幂等")

            try custom.write(to: target, options: .atomic)
            let customizedSnapshot = try snapshot(root)
            let outdated = try OptionalPluginInstaller.inspect(plugin, home: home, source: source)
            try require(outdated == .outdated, "应报告可更新")
            try require(try snapshot(root) == customizedSnapshot, "检测更新不应写文件")
            try expectFailure("检测应拒绝缺失资源") {
                _ = try OptionalPluginInstaller.inspect(plugin, home: home, source: missingSource)
            }
            try expectFailure("安装应拒绝缺失资源") {
                try OptionalPluginInstaller.install(plugin, home: home, source: missingSource)
            }
            try require(try snapshot(root) == customizedSnapshot, "资源失败应保留现有安装")

            try OptionalPluginInstaller.install(plugin, home: home, source: source)
            try require(try Data(contentsOf: target) == original, "更新内容不符")
            let saved = try files.contentsOfDirectory(at: backups, includingPropertiesForKeys: nil)
            try require(saved.count == 1, "更新应保留一份备份")
            try require(saved[0].pathExtension == "py", "备份扩展名不符")
            try require(UUID(uuidString: saved[0].deletingPathExtension().lastPathComponent) != nil, "备份应采用 UUID")
            try require(try Data(contentsOf: saved[0]) == custom, "备份必须保留修改前内容")
            let updatedSnapshot = try snapshot(root)
            try OptionalPluginInstaller.install(plugin, home: home, source: source)
            try require(try snapshot(root) == updatedSnapshot, "重复更新不应重写文件或新增备份")

            let olderBundle = Data("PLUGIN_VERSION = \"v2.1\"\n".utf8)
            let newerInstall = Data("PLUGIN_VERSION = \"v2.2\"\n".utf8)
            try olderBundle.write(to: source)
            try newerInstall.write(to: target)
            try require(try OptionalPluginInstaller.inspect(plugin, home: home, source: source) == .current, "较新插件不应显示为过期")
            try OptionalPluginInstaller.install(plugin, home: home, source: source)
            try require(try Data(contentsOf: target) == newerInstall, "不能将插件降级")
            try original.write(to: source)

            // A failed backup must leave the customized installation intact.
            try custom.write(to: target, options: .atomic)
            try files.removeItem(at: backups)
            try Data("blocked".utf8).write(to: backups)
            let blockedSnapshot = try snapshot(root)
            try expectFailure("备份路径不可用时应停止更新") {
                try OptionalPluginInstaller.install(plugin, home: home, source: source)
            }
            try require(try snapshot(root) == blockedSnapshot, "备份失败不应修改现有安装")
            try require(try Data(contentsOf: source) == original, "不得修改源脚本")
            try require(try Data(contentsOf: unrelated) == unrelatedData, "不得修改其他集成")

            // A linked Utility directory must never redirect writes elsewhere.
            let linkedHome = root.appendingPathComponent("linked-" + plugin.id)
            let linkedUtility = linkedHome.appendingPathComponent("Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility")
            let elsewhere = root.appendingPathComponent("originals-" + plugin.id)
            try files.createDirectory(at: elsewhere, withIntermediateDirectories: true)
            try custom.write(to: elsewhere.appendingPathComponent(plugin.filename))
            try files.createDirectory(at: linkedUtility.deletingLastPathComponent(), withIntermediateDirectories: true)
            try files.createSymbolicLink(at: linkedUtility, withDestinationURL: elsewhere)
            let elsewhereSnapshot = try snapshot(elsewhere)
            try expectFailure("应拒绝符号链接安装目录") {
                try OptionalPluginInstaller.install(plugin, home: linkedHome, source: source)
            }
            try require(try snapshot(elsewhere) == elsewhereSnapshot, "不得覆盖其他位置的原脚本")
        }
        let migrationHome = root.appendingPathComponent("legacy-migration")
        let utility = migrationHome.appendingPathComponent("Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility")
        try files.createDirectory(at: utility, withIntermediateDirectories: true)
        let old = utility.appendingPathComponent("导入阅流.py")
        let renamed = utility.appendingPathComponent("Import YueLiu.py")
        let resource = root.appendingPathComponent("renamed-resource.py")
        let oldData = Data("# existing user script\n".utf8)
        let newData = Data("# renamed bundle\n".utf8)
        try oldData.write(to: old)
        try newData.write(to: resource)
        let initial = try snapshot(migrationHome)
        try require(try OptionalPluginInstaller.inspect(.yueliu, home: migrationHome, source: resource) == .outdated,
                    "旧中文名应报告可更新")
        try require(try snapshot(migrationHome) == initial, "检测旧名称不应写文件")
        try OptionalPluginInstaller.install(.yueliu, home: migrationHome, source: resource)
        try require(try Data(contentsOf: renamed) == newData, "英文名安装内容不符")
        try require(!files.fileExists(atPath: old.path), "不应保留两个菜单入口")
        let legacyBackups = migrationHome.appendingPathComponent("Library/Application Support/com.example.MagicTools.Youth/Backups/yueliu")
        let savedLegacy = try files.contentsOfDirectory(at: legacyBackups, includingPropertiesForKeys: nil)
        try require(savedLegacy.count == 1 && (try Data(contentsOf: savedLegacy[0])) == oldData, "应完整备份旧中文脚本")
        let afterMigration = try snapshot(migrationHome)
        try OptionalPluginInstaller.install(.yueliu, home: migrationHome, source: resource)
        try require(try snapshot(migrationHome) == afterMigration, "迁移后的重复安装应幂等")

        try oldData.write(to: old)
        try Data("custom English".utf8).write(to: renamed)
        try OptionalPluginInstaller.install(.yueliu, home: migrationHome, source: resource)
        try require(!files.fileExists(atPath: old.path), "同时存在新旧名称时应收敛为一个入口")
        let savedBoth = try files.contentsOfDirectory(at: legacyBackups, includingPropertiesForKeys: nil)
        try require(savedBoth.count == 3, "应备份新旧名称的两份修改内容")

        let linkedHome = root.appendingPathComponent("linked-legacy")
        let linkedUtility = linkedHome.appendingPathComponent("Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility")
        try files.createDirectory(at: linkedUtility, withIntermediateDirectories: true)
        try files.createSymbolicLink(at: linkedUtility.appendingPathComponent("导入阅流.py"), withDestinationURL: resource)
        let beforeLinked = try snapshot(linkedHome)
        try expectFailure("旧名称符号链接应拒绝迁移") {
            try OptionalPluginInstaller.install(.yueliu, home: linkedHome, source: resource)
        }
        try require(try snapshot(linkedHome) == beforeLinked, "不得跟随旧名称符号链接写文件")
        print("可选插件：只读检测、安装、改名迁移、备份、幂等与失败保护验证通过")
    }
}
