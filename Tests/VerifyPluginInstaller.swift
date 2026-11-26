import Foundation

@main
struct VerifyPluginInstaller {
    private enum CheckFailure: Error { case failed(String) }

    static func require(_ condition: Bool, _ message: String) throws {
        if !condition { throw CheckFailure.failed(message) }
    }

    static func expectFailure(_ message: String, _ operation: () throws -> Void) throws {
        do { try operation() } catch { return }
        throw CheckFailure.failed(message)
    }

    static func makeDescriptor(id: String = "demo", version: String = "1.0.0",
                           minimum: String = "1.9.6",
                           capabilities: [PluginCapability] = [.resolveUtilityScript,
                                                               .resolveSupport,
                                                               .chromeNativeMessagingHost,
                                                               .chromeUnpackedExtension],
                           support: String? = "Demo Notes",
                           host: String? = "host.command",
                           legacy: [String]? = nil,
                           utility: String = "demo.py") -> PluginDescriptor {
        PluginDescriptor(id: id, name: "Demo Notes", summary: "demo plugin",
                         symbol: "note.text", version: version,
                         minClientVersion: minimum, kind: "bundle",
                         protocolVersion: 2, capabilities: capabilities,
                         supportDirectoryName: support, nativeMessagingHost: host,
                         legacyNames: legacy, utilityScript: utility)
    }

    static func file(_ path: String, _ data: Data, _ role: PluginFileRole,
                     install: String? = nil, executable: Bool? = nil) -> PluginFile {
        PluginFile(path: path, sha256: PluginProtocol.digest(data), size: data.count,
                   role: role, installName: install, executable: executable)
    }

    static func release(_ descriptor: PluginDescriptor,
                        _ files: [PluginFile]) -> PluginRelease {
        PluginRelease(version: descriptor.version,
                      url: "https://github.com/BrOKenAG/ordina-plugin-\(descriptor.id)/releases/download/"
                        + "plugin-\(descriptor.id)-v\(descriptor.version)/\(descriptor.id).json",
                      sha256: String(repeating: "a", count: 64), size: 100,
                      kind: descriptor.isBundle ? "bundle" : nil,
                      descriptor: descriptor, files: files)
    }

    static func payload(_ version: String) -> (PluginDescriptor, [PluginFile], [String: Data]) {
        let script = Data("PLUGIN_VERSION = \"v\(version)\"\n".utf8)
        let host = Data("# host \(version)\n".utf8)
        let command = Data("#!/bin/sh\necho \(version)\n".utf8)
        let extensionManifest = Data("{\"manifest_version\":3,\"version\":\"\(version)\"}".utf8)
        let chrome = Data(("{\"name\":\"com.demo.bridge\",\"description\":\"Demo\","
                           + "\"type\":\"stdio\",\"allowed_origins\":[\"chrome-extension://"
                           + String(repeating: "a", count: 32) + "/\"]}").utf8)
        let metadata = Data("{\"id\":\"demo\",\"version\":\"\(version)\"}".utf8)
        let descriptor = makeDescriptor(version: version)
        let files = [
            file("plugin.json", metadata, .metadata),
            file("demo.py", script, .utilityScript, install: "demo.py"),
            file("host.py", host, .support),
            file("host.command", command, .nativeMessagingHost, executable: true),
            file("extension/manifest.json", extensionManifest, .extension),
            file("chrome/com.demo.bridge.json", chrome, .nativeMessagingManifest,
                 install: "com.demo.bridge.json"),
        ]
        let data = ["plugin.json": metadata, "demo.py": script, "host.py": host,
                    "host.command": command, "extension/manifest.json": extensionManifest,
                    "chrome/com.demo.bridge.json": chrome]
        return (descriptor, files, data)
    }

    static func main() throws {
        let manager = FileManager.default
        let root = manager.temporaryDirectory.appendingPathComponent("Ordina-PluginInstaller-" + UUID().uuidString)
        try manager.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? manager.removeItem(at: root) }
        let home = root.appendingPathComponent("home")
        try manager.createDirectory(at: home, withIntermediateDirectories: true)
        let environment = PluginEnvironment.lab(
            registryURL: URL(string: "http://127.0.0.1:1/registry.json")!,
            host: "127.0.0.1", publicKey: Data(), home: home, clientVersion: "1.9.6")
        let utility = environment.utilityDirectory
        let support = home.appendingPathComponent("Library/Application Support/Demo Notes")
        let chrome = environment.chromeDirectory

        let (descriptor, files, data) = payload("1.0.0")
        let first = release(descriptor, files)

        try require(PluginInstaller.inspect(descriptor, environment: environment) == .notInstalled,
                    "fresh home should be not installed")
        try require(!manager.fileExists(atPath: utility.path), "inspect must not create folders")

        // Install and verify every capability target.
        try PluginInstaller.install(first, files: data, environment: environment)
        try require(try Data(contentsOf: utility.appendingPathComponent("demo.py"))
                    == data["demo.py"], "utility script mismatch")
        let host = support.appendingPathComponent("host.command")
        try require(manager.fileExists(atPath: host.path), "native host missing")
        try require((try manager.attributesOfItem(atPath: host.path)[.posixPermissions] as? NSNumber)?
            .uint16Value == 0o700, "native host must be executable")
        try require(manager.fileExists(atPath: support.appendingPathComponent("extension/manifest.json").path),
                    "extension missing")
        let manifestPath = chrome.appendingPathComponent("com.demo.bridge.json")
        let manifest = try JSONSerialization.jsonObject(with: Data(contentsOf: manifestPath)) as? [String: Any]
        try require(manifest?["path"] as? String == host.path, "chrome host path wrong")
        try require(PluginInstaller.inspect(descriptor, environment: environment) == .current(version: "1.0.0"),
                    "should be current")
        let recorded = PluginInstaller.state("demo", environment: environment)
        try require(recorded?.files.count == 5, "state should record five installed files")

        // Idempotent reinstall.
        let backups = home.appendingPathComponent("Library/Application Support/ordina/Plugins/demo/backups")
        try PluginInstaller.install(first, files: data, environment: environment)
        try require(!manager.fileExists(atPath: backups.path), "identical reinstall must not back up")

        // Update, keep user data, back up replaced bytes.
        let userFile = support.appendingPathComponent("user.txt")
        try Data("keep".utf8).write(to: userFile)
        let (descriptor11, files11, data11) = payload("1.1.0")
        try require(PluginInstaller.inspect(descriptor11, environment: environment)
                    == .updateAvailable(installed: "1.0.0", latest: "1.1.0"), "should offer update")
        try PluginInstaller.install(release(descriptor11, files11), files: data11,
                                    environment: environment)
        try require(try Data(contentsOf: utility.appendingPathComponent("demo.py"))
                    == data11["demo.py"], "update did not replace script")
        try require(try Data(contentsOf: userFile) == Data("keep".utf8), "user data lost")
        try require(PluginInstaller.state("demo", environment: environment)?.version == "1.1.0",
                    "state not updated")
        let saved = try manager.contentsOfDirectory(at: backups, includingPropertiesForKeys: nil,
                                                    options: .skipsHiddenFiles)
        try require(!saved.isEmpty, "update must back up replaced files")

        // Never downgrade.
        try expectFailure("downgrade was accepted") {
            try PluginInstaller.install(first, files: data, environment: environment)
        }
        try require(PluginInstaller.state("demo", environment: environment)?.version == "1.1.0",
                    "downgrade changed the installation")

        // Incompatible minimum client version.
        let future = release(makeDescriptor(minimum: "2.0.0"), files)
        try require(PluginInstaller.inspect(makeDescriptor(minimum: "2.0.0"), environment: environment)
                    == .incompatible(required: "2.0.0"), "incompatible not reported")
        try expectFailure("incompatible install was accepted") {
            try PluginInstaller.install(future, files: data, environment: environment)
        }

        // Corrupt bytes are rejected before any write.
        var corrupt = data
        corrupt["demo.py"] = Data("evil".utf8)
        try expectFailure("corrupt payload was accepted") {
            try PluginInstaller.install(release(descriptor11, files11), files: corrupt,
                                        environment: environment)
        }
        try require(try Data(contentsOf: utility.appendingPathComponent("demo.py"))
                    == data11["demo.py"], "corrupt install changed the script")

        // Uninstall removes plugin files, keeps user data, prunes empty folders.
        try PluginInstaller.uninstall(descriptor11, environment: environment)
        try require(!manager.fileExists(atPath: utility.appendingPathComponent("demo.py").path),
                    "utility script left behind")
        try require(!manager.fileExists(atPath: support.appendingPathComponent("host.py").path),
                    "support file left behind")
        try require(!manager.fileExists(atPath: support.appendingPathComponent("extension").path),
                    "empty extension folder left behind")
        try require(!manager.fileExists(atPath: manifestPath.path), "chrome manifest left behind")
        try require(try Data(contentsOf: userFile) == Data("keep".utf8), "uninstall removed user data")
        try require(PluginInstaller.state("demo", environment: environment) == nil, "state left behind")

        // Interrupted install rolls back already-written files.
        try manager.createDirectory(at: support.appendingPathComponent("host.py"),
                                    withIntermediateDirectories: true)
        try expectFailure("blocked install was accepted") {
            try PluginInstaller.install(release(descriptor11, files11), files: data11,
                                        environment: environment)
        }
        try require(!manager.fileExists(atPath: utility.appendingPathComponent("demo.py").path),
                    "partial install left utility script")
        try require(PluginInstaller.state("demo", environment: environment) == nil,
                    "failed install recorded state")
        try manager.removeItem(at: support.appendingPathComponent("host.py"))
        try PluginInstaller.install(release(descriptor11, files11), files: data11,
                                    environment: environment)
        try require(PluginInstaller.state("demo", environment: environment)?.version == "1.1.0",
                    "retry did not install")
        try PluginInstaller.uninstall(descriptor11, environment: environment)

        // Legacy marker migration: installed.json at 0.9.0 offers an update.
        let legacySupport = home.appendingPathComponent("Library/Application Support/Demo Notes")
        try manager.createDirectory(at: legacySupport, withIntermediateDirectories: true)
        try Data("{\"id\":\"demo\",\"version\":\"0.9.0\"}".utf8)
            .write(to: legacySupport.appendingPathComponent("installed.json"))
        try require(PluginInstaller.inspect(descriptor, environment: environment)
                    == .updateAvailable(installed: "0.9.0", latest: "1.0.0"),
                    "legacy marker migration not detected")

        // Legacy file name migration (non-ASCII) and backup.
        try manager.removeItem(at: legacySupport.appendingPathComponent("installed.json"))
        try manager.createDirectory(at: utility, withIntermediateDirectories: true)
        let legacy = utility.appendingPathComponent("旧名.py")
        try Data("# legacy\n".utf8).write(to: legacy)
        let legacyDescriptor = makeDescriptor(legacy: ["旧名.py"])
        try require(PluginInstaller.inspect(legacyDescriptor, environment: environment)
                    == .unmanaged(version: nil), "legacy file should be unmanaged")
        try PluginInstaller.install(release(legacyDescriptor, files), files: data,
                                    environment: environment)
        try require(!manager.fileExists(atPath: legacy.path), "legacy name was not removed")
        try require(manager.fileExists(atPath: utility.appendingPathComponent("demo.py").path),
                    "renamed script missing")
        let legacyBackups = home.appendingPathComponent(
            "Library/Application Support/ordina/Plugins/demo/backups")
        let enumerator = manager.enumerator(at: legacyBackups, includingPropertiesForKeys: nil)
        var foundLegacyBackup = false
        while let candidate = enumerator?.nextObject() as? URL {
            if (try? Data(contentsOf: candidate)) == Data("# legacy\n".utf8) {
                foundLegacyBackup = true
            }
        }
        try require(foundLegacyBackup, "legacy script was not backed up")
        try PluginInstaller.uninstall(legacyDescriptor, environment: environment)

        // A linked Utility directory must never redirect writes.
        let linkedHome = root.appendingPathComponent("linked-home")
        let linkedUtility = linkedHome.appendingPathComponent(
            "Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility")
        let elsewhere = root.appendingPathComponent("elsewhere")
        try manager.createDirectory(at: elsewhere, withIntermediateDirectories: true)
        try manager.createDirectory(at: linkedUtility.deletingLastPathComponent(),
                                    withIntermediateDirectories: true)
        try manager.createSymbolicLink(at: linkedUtility, withDestinationURL: elsewhere)
        let linked = PluginEnvironment.lab(
            registryURL: URL(string: "http://127.0.0.1:1/registry.json")!,
            host: "127.0.0.1", publicKey: Data(), home: linkedHome, clientVersion: "1.9.6")
        try expectFailure("linked install directory was accepted") {
            try PluginInstaller.install(first, files: data, environment: linked)
        }
        try require(try manager.contentsOfDirectory(atPath: elsewhere.path).isEmpty,
                    "linked install wrote outside home")

        // A failure committing the installation record must undo every file.
        let failingHome = root.appendingPathComponent("record-failure-home")
        let failing = PluginEnvironment.lab(registryURL: URL(string: "http://127.0.0.1:1/registry.json")!,
                                            host: "127.0.0.1", publicKey: Data(), home: failingHome,
                                            clientVersion: "1.9.6")
        try manager.createDirectory(at: failing.stateFile("demo"), withIntermediateDirectories: true)
        try expectFailure("state commit failure was accepted") {
            try PluginInstaller.install(first, files: data, environment: failing)
        }
        try require(!manager.fileExists(atPath: failing.utilityDirectory.appendingPathComponent("demo.py").path),
                    "record failure left plugin files installed")
        print("Plugin installer: capabilities, update, rollback, uninstall and migration passed")
    }
}
