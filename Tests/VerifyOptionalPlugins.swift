import Foundation

// Local script import only. Plugin installation is covered by
// VerifyPluginInstaller; this file checks the user-facing .py flow.
@main
struct VerifyOptionalPlugins {
    private enum CheckFailure: Error { case failed(String) }

    static func require(_ condition: Bool, _ message: String) throws {
        if !condition { throw CheckFailure.failed(message) }
    }

    static func expectFailure(_ message: String, _ operation: () throws -> Void) throws {
        do { try operation() } catch { return }
        throw CheckFailure.failed(message)
    }

    static func main() throws {
        let manager = FileManager.default
        let root = manager.temporaryDirectory.appendingPathComponent("Ordina-LocalScripts-" + UUID().uuidString)
        try manager.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? manager.removeItem(at: root) }
        let home = root.appendingPathComponent("home")
        let utility = home.appendingPathComponent(
            "Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility")
        try manager.createDirectory(at: utility, withIntermediateDirectories: true)
        let environment = PluginEnvironment.lab(
            registryURL: URL(string: "http://127.0.0.1:1/registry.json")!,
            host: "127.0.0.1", publicKey: Data(), home: home, clientVersion: "1.9.6")

        // A managed plugin script must not be offered as a user script.
        let script = Data("PLUGIN_VERSION = \"v1.0.0\"\n".utf8)
        let descriptor = PluginDescriptor(
            id: "demo", name: "Demo", summary: "demo", symbol: "doc",
            version: "1.0.0", minClientVersion: "1.9.6", kind: "script",
            protocolVersion: 2, capabilities: [.resolveUtilityScript],
            supportDirectoryName: nil, nativeMessagingHost: nil,
            legacyNames: nil, utilityScript: "demo.py")
        let file = PluginFile(path: "demo.py", sha256: PluginProtocol.digest(script),
                              size: script.count, role: .utilityScript,
                              installName: "demo.py", executable: nil)
        let release = PluginRelease(
            version: "1.0.0",
            url: "https://github.com/BrOKenAG/ordina-plugin-demo/releases/download/"
                + "plugin-demo-v1.0.0/demo.py",
            sha256: PluginProtocol.digest(script), size: script.count, kind: nil,
            descriptor: descriptor, files: [file])
        try PluginInstaller.install(release, files: ["demo.py": script], environment: environment)

        try Data("# project setup\n".utf8).write(to: utility.appendingPathComponent("Project Setup.py"))
        try Data("# alpha\n".utf8).write(to: utility.appendingPathComponent("alpha.py"))
        try Data("# beta\n".utf8).write(to: utility.appendingPathComponent("beta.py"))
        let listed = try OptionalPluginInstaller.localScripts(home: home)
        try require(Set(listed.map(\.url.lastPathComponent)) == ["alpha.py", "beta.py"],
                    "managed or built-in scripts leaked into the local list")

        // Import a new user script.
        let source = root.appendingPathComponent("gamma.py")
        try Data("\"\"\"Gamma helper.\"\"\"\nprint(1)\n".utf8).write(to: source)
        let pending = try OptionalPluginInstaller.prepareImport(source, home: home)
        try require(pending.existing == nil, "new import should not report an existing file")
        try OptionalPluginInstaller.install(pending, home: home)
        try require(try Data(contentsOf: utility.appendingPathComponent("gamma.py"))
                    == Data("\"\"\"Gamma helper.\"\"\"\nprint(1)\n".utf8), "import content mismatch")

        // Replacing must back up the previous content.
        try Data("public var old = 1\n".utf8).write(to: utility.appendingPathComponent("gamma.py"))
        let replace = try OptionalPluginInstaller.prepareImport(source, home: home)
        try require(replace.existing != nil, "replacement should see existing content")
        try OptionalPluginInstaller.install(replace, home: home)
        let backups = home.appendingPathComponent(
            "Library/Application Support/com.example.MagicTools.Youth/Backups/Resolve Scripts")
        let enumerator = manager.enumerator(at: backups, includingPropertiesForKeys: nil)
        var found = false
        while let candidate = enumerator?.nextObject() as? URL {
            if (try? Data(contentsOf: candidate)) == Data("public var old = 1\n".utf8) { found = true }
        }
        try require(found, "replaced script was not backed up")

        // A changed source between prepare and install is rejected.
        let pendingStale = try OptionalPluginInstaller.prepareImport(source, home: home)
        try Data("\"\"\"Changed.\"\"\"\n".utf8).write(to: source)
        try expectFailure("stale import was accepted") {
            try OptionalPluginInstaller.install(pendingStale, home: home)
        }

        // Non-Python input and a linked Utility directory are rejected.
        let text = root.appendingPathComponent("notes.txt")
        try Data("x".utf8).write(to: text)
        try expectFailure("non-python import was accepted") {
            _ = try OptionalPluginInstaller.prepareImport(text, home: home)
        }
        let linkedHome = root.appendingPathComponent("linked")
        let linkedUtility = linkedHome.appendingPathComponent(
            "Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility")
        let elsewhere = root.appendingPathComponent("elsewhere")
        try manager.createDirectory(at: elsewhere, withIntermediateDirectories: true)
        try manager.createDirectory(at: linkedUtility.deletingLastPathComponent(),
                                    withIntermediateDirectories: true)
        try manager.createSymbolicLink(at: linkedUtility, withDestinationURL: elsewhere)
        try expectFailure("linked utility accepted") {
            let pending = try OptionalPluginInstaller.prepareImport(source, home: linkedHome)
            try OptionalPluginInstaller.install(pending, home: linkedHome)
        }
        try require(try manager.contentsOfDirectory(atPath: elsewhere.path).isEmpty,
                    "linked import wrote outside home")

        // Removing a local script moves it to the backup area.
        let gamma = listed.first { $0.url.lastPathComponent == "alpha.py" }!
        try OptionalPluginInstaller.removeLocalScript(gamma, home: home)
        try require(!manager.fileExists(atPath: utility.appendingPathComponent("alpha.py").path),
                    "removed script still present")
        print("Local script import, replacement, backup, rejection and removal passed")
    }
}
