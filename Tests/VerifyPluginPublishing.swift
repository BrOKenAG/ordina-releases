import Foundation

@main
struct VerifyPluginPublishing {
    private enum CheckFailure: Error { case failed(String) }

    static func require(_ condition: Bool, _ message: String) throws {
        if !condition { throw CheckFailure.failed(message) }
    }

    static func output(_ executable: String, _ arguments: [String]) throws -> String {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        let pipe = Pipe()
        process.standardOutput = pipe
        try process.run()
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        guard process.terminationStatus == 0 else { throw CheckFailure.failed("\(executable) failed") }
        return String(data: data, encoding: .utf8) ?? ""
    }

    static func main() throws {
        let manager = FileManager.default
        let root = manager.temporaryDirectory.appendingPathComponent("Ordina-Publishing-" + UUID().uuidString)
        try manager.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? manager.removeItem(at: root) }
        setenv("HOME", root.path, 1)
        let home = root.appendingPathComponent("home")
        let utility = home.appendingPathComponent(
            "Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility")
        try manager.createDirectory(at: utility, withIntermediateDirectories: true)
        let environment = PluginEnvironment.lab(
            registryURL: URL(string: "http://127.0.0.1:1/registry.json")!,
            host: "127.0.0.1", publicKey: Data(), home: home, clientVersion: "1.10.1")

        let script = "\"\"\"Import YueLiu review comments.\"\"\"\nPLUGIN_VERSION = \"v2.3\"\nprint(\"ready\")\n"
        let installedScript = utility.appendingPathComponent("Import YueLiu.py")
        try Data(script.utf8).write(to: installedScript)
        let source: PluginPublishing.Source = .localScript(installedScript)
        try require(PluginPublishing.canPackage(source, environment: environment),
                    "valid local script must be packageable")
        let info = try PluginPublishing.suggestedInfo(source, environment: environment)
        try require(info.id == "import-yueliu" && info.version == "2.3",
                    "metadata must be derived from the installed script")

        let prepared = try PluginPublishing.prepare(info, source: source, environment: environment)
        try require(prepared.files.count == 1, "local script package must contain exactly one file")
        try require(prepared.descriptor.utilityScript == "Import YueLiu.py",
                    "install name must match the actual script")
        let listing = try output("/usr/bin/unzip", ["-Z1", prepared.zip.path])
        let names = listing.split(separator: "\n").map(String.init).filter { !$0.isEmpty }
        try require(Set(names) == Set(["plugin.json", "Import YueLiu.py"]),
                    "unexpected ZIP contents: \(names)")
        let manifestData = Data(try output("/usr/bin/unzip",
                                           ["-p", prepared.zip.path, "plugin.json"]).utf8)
        let manifest = try JSONSerialization.jsonObject(with: manifestData) as? [String: Any]
        try require(manifest?["id"] as? String == info.id
                    && (manifest?["protocol"] as? Int) == 2
                    && (manifest?["files"] as? [String: Any])?["Import YueLiu.py"] != nil,
                    "ZIP manifest is not protocol-valid")

        try PluginPublishing.recordPublished(prepared, environment: environment)
        try require(PluginInstaller.state(info.id, environment: environment)?.version == info.version,
                    "managed record was not created")
        let listed = try OptionalPluginInstaller.localScripts(home: home)
        try require(!listed.contains(where: { $0.url.lastPathComponent == "Import YueLiu.py" }),
                    "managed script still appears in the local list")

        // A composite plugin with a missing recorded component must fail loudly.
        let descriptor = PluginDescriptor(
            id: "composite", name: "Composite", summary: "composite", symbol: "doc",
            version: "1.0.0", minClientVersion: "1.10.0", kind: "bundle",
            protocolVersion: 2, capabilities: [.resolveUtilityScript, .resolveSupport],
            supportDirectoryName: "Composite", nativeMessagingHost: nil,
            legacyNames: nil, utilityScript: "Composite.py")
        let record = InstalledPlugin(id: "composite", version: "1.0.0", descriptor: descriptor,
                                     files: [], installedAt: 0, legacyMarker: nil)
        try manager.createDirectory(at: environment.stateDirectory("composite"),
                                    withIntermediateDirectories: true)
        try JSONEncoder().encode(record).write(to: environment.stateFile("composite"))
        do {
            _ = try PluginPublishing.prepare(PluginPublishing.Info(
                id: "composite", name: "Composite", version: "1.1.0",
                summary: "composite", symbol: "doc", minClientVersion: "1.10.0"),
                source: .installed("composite"), environment: environment)
            throw CheckFailure.failed("incomplete plugin must not produce a ZIP")
        } catch let error as PluginPublishing.Failure {
            if case .missing(let items) = error {
                try require(items.contains { $0.contains("必要的 Resolve 脚本") }
                            || items.contains { $0.contains("完整组件") },
                            "missing component must be reported explicitly")
            } else {
                throw CheckFailure.failed("wrong packaging failure: \(error.localizedDescription)")
            }
        }
    }
}
