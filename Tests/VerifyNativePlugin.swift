import Foundation

@main
struct VerifyNativePlugin {
    static func main() throws {
        let manager = FileManager.default
        let app = URL(fileURLWithPath: ProcessInfo.processInfo.environment["ORDINA_TEST_NATIVE_APP"]!)
        let home = manager.temporaryDirectory.appendingPathComponent("ordina-native-" + UUID().uuidString)
        try manager.createDirectory(at: home, withIntermediateDirectories: true)
        defer { try? manager.removeItem(at: home) }
        let env = PluginEnvironment.lab(registryURL: URL(string:"http://127.0.0.1:1/registry.json")!, host: "127.0.0.1", publicKey: Data(), home: home, clientVersion: "1.10.0")
        func make(_ version: String, corrupt: Bool = false) throws -> (PluginRelease, [String:Data]) {
            let descriptor = PluginDescriptor(id:"native-demo", name:"Native Demo", summary:"Isolated test", symbol:"puzzlepiece", version:version, minClientVersion:"1.10.0", kind:"bundle", protocolVersion:2, capabilities:[.macOSApplication, .resolveUtilityScript, .resolveSupport, .chromeNativeMessagingHost, .chromeUnpackedExtension], supportDirectoryName:"Native Demo", nativeMessagingHost:"host.command", legacyNames:nil, utilityScript:"native-demo.py")
            var data: [String:Data] = ["plugin.json":Data("{}".utf8), "native-demo.py":Data("# resolve".utf8), "engine.py":Data("# engine".utf8), "host.command":Data("#!/bin/sh\nexit 0\n".utf8), "extension/manifest.json":Data("{}".utf8), "chrome.json":Data("{\"name\":\"com.example.test\",\"allowed_origins\":[\"chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/\"]}".utf8)]
            let enumerator = manager.enumerator(at: app, includingPropertiesForKeys: [.isRegularFileKey])!
            for case let url as URL in enumerator where (try url.resourceValues(forKeys: [.isRegularFileKey])).isRegularFile == true {
                let relative = String(url.path.dropFirst(app.path.count + 1))
                data["Example.app/" + relative] = try Data(contentsOf:url)
            }
            if version == "1.1.0" { data.removeValue(forKey: "engine.py") }
            if corrupt { data["Example.app/Contents/MacOS/Example"]!.append(0) }
            var files:[PluginFile] = []
            for (path, content) in data.sorted(by: {$0.key < $1.key}) {
                let role:PluginFileRole = path.hasPrefix("Example.app/") ? .application : path == "plugin.json" ? .metadata : path == "native-demo.py" ? .utilityScript : path == "host.command" ? .nativeMessagingHost : path == "chrome.json" ? .nativeMessagingManifest : path.hasPrefix("extension/") ? .extension : .support
                files.append(PluginFile(path:path, sha256:PluginProtocol.digest(content), size:content.count, role:role, installName:path == "chrome.json" ? "com.example.test.json" : nil, executable:path.hasSuffix("/MacOS/Example") || path == "host.command"))
            }
            let release = PluginRelease(version:version,url:"https://example.invalid",sha256:String(repeating:"a", count:64),size:100,kind:"bundle",descriptor:descriptor,files:files)
            return (release,data)
        }
        let (first, contents) = try make("1.0.0")
        if ProcessInfo.processInfo.environment["ORDINA_TEST_EXPECT_REJECT"] == "1" {
            do { try PluginInstaller.install(first, files: contents, environment: env); fatalError("untrusted app accepted") } catch {}
            precondition(!manager.fileExists(atPath: env.utilityDirectory.appendingPathComponent("native-demo.py").path))
            print("Production rejects untrusted native signature")
            return
        }
        try PluginInstaller.install(first, files:contents, environment:env)
        let installed = env.home.appendingPathComponent("Library/Application Support/Native Demo/Example.app/Contents/MacOS/Example")
        precondition(manager.fileExists(atPath:installed.path))
        let (bad, badData) = try make("1.1.0", corrupt:true)
        do { try PluginInstaller.install(bad, files:badData, environment:env); fatalError("tampered signature accepted") } catch {}
        precondition(PluginInstaller.state("native-demo", environment:env)?.version == "1.0.0")
        let (second, update) = try make("1.1.0")
        try PluginInstaller.install(second, files:update, environment:env)
        precondition(PluginInstaller.state("native-demo", environment:env)?.version == "1.1.0")
        precondition(!manager.fileExists(atPath:env.home.appendingPathComponent("Library/Application Support/Native Demo/engine.py").path))
        try PluginInstaller.uninstall(second.descriptor, environment:env)
        precondition(!manager.fileExists(atPath:installed.path))
        print("Native composite install, signature rejection, update and uninstall passed")
    }
}
