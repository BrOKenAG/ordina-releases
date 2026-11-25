import Foundation

// The catalogue must still show installed plugins when the registry is
// unreachable, and must not invent plugins it has no signed record for.
@main
struct VerifyPluginCatalog {
    private enum CheckFailure: Error { case failed(String) }

    static func require(_ condition: Bool, _ message: String) throws {
        if !condition { throw CheckFailure.failed(message) }
    }

    static func main() throws {
        let manager = FileManager.default
        let root = manager.temporaryDirectory.appendingPathComponent("Ordina-Catalog-" + UUID().uuidString)
        try manager.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? manager.removeItem(at: root) }
        let home = root.appendingPathComponent("home")
        try manager.createDirectory(at: home, withIntermediateDirectories: true)
        let environment = PluginEnvironment.lab(
            registryURL: URL(string: "http://127.0.0.1:1/registry.json")!,
            host: "127.0.0.1", publicKey: Data(), home: home, clientVersion: "1.9.6")

        let script = Data("PLUGIN_VERSION = \"v1.0.0\"\n".utf8)
        let descriptor = PluginDescriptor(
            id: "demo", name: "Demo Notes", summary: "installed while offline",
            symbol: "note.text", version: "1.0.0", minClientVersion: "1.9.6",
            kind: "script", protocolVersion: 2, capabilities: [.resolveUtilityScript],
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

        let result = PluginCatalog.load(environment: environment)
        try require(result.registryError != nil, "unreachable registry should be reported")
        let demo = result.items.first { $0.id == "demo" }
        try require(demo != nil, "installed plugin missing from offline catalogue")
        try require(demo?.name == "Demo Notes", "descriptor name lost")
        try require(demo?.summary == "installed while offline", "descriptor summary lost")
        try require(demo?.status == .current(version: "1.0.0"), "wrong offline status")
        try require(demo?.isInstalled == true, "installed flag wrong")
        try require(result.items.count == 1, "catalogue invented unregistered plugins")

        // A failed manifest refresh keeps the installed item usable but
        // exposes the error, and never downgrades its state.
        let entry = PluginRegistryEntry(
            repository: "BrOKenAG/ordina-plugin-demo",
            feed: "http://127.0.0.1:1/ordina-plugin-demo/manifest-v2.json",
            downloadPrefix: "/BrOKenAG/ordina-plugin-demo/releases/download/",
            kind: "script", name: "Demo Notes", summary: "registry copy",
            symbol: "note.text", version: "1.2.0", minClientVersion: "1.9.6",
            protocolVersion: 2, capabilities: ["resolve.utilityScript"])
        let resolved = PluginCatalog.resolve(id: "demo", entry: entry, installed: demo,
                                             environment: environment)
        try require(resolved.error != nil, "failed refresh should be reported")
        try require(resolved.status == .current(version: "1.0.0"),
                    "failed refresh changed the installed state")
        try require(resolved.descriptor?.name == "Demo Notes", "installed descriptor lost")

        print("Plugin catalogue: offline merge, no invented plugins and failed refresh passed")
    }
}
