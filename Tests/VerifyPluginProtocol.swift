import Foundation
import CryptoKit

@main
struct VerifyPluginProtocol {
    static func main() throws {
        let key = Curve25519.Signing.PrivateKey()
        let publicKey = key.publicKey.rawRepresentation
        let otherKey = Curve25519.Signing.PrivateKey().publicKey.rawRepresentation
        let policy = PluginPolicy.production
        let prefix = "/BrOKenAG/ordina-plugin-demo/releases/download/"
        let feed = "https://brokenag.github.io/ordina-plugin-demo/manifest-v2.json"
        let asset = "https://github.com/BrOKenAG/ordina-plugin-demo/releases/download/plugin-demo-v1.0.0/demo.json"

        func envelope(_ object: [String: Any]) throws -> Data {
            let payload = try JSONSerialization.data(withJSONObject: object)
            return try JSONSerialization.data(withJSONObject: [
                "payload": payload.base64EncodedString(),
                "signature": key.signature(for: payload).base64EncodedString(),
            ])
        }
        func expectFailure(_ label: String, _ operation: () throws -> Void) {
            do { try operation() } catch { return }
            fatalError(label + " was accepted")
        }
        func digest(_ data: Data) -> String {
            PluginProtocol.digest(data)
        }
        func descriptor(_ overrides: [String: Any] = [:]) -> [String: Any] {
            var value: [String: Any] = [
                "id": "demo", "name": "Demo", "summary": "demo plugin",
                "symbol": "note.text", "version": "1.0.0",
                "minClientVersion": "1.9.6", "kind": "bundle", "protocol": 2,
                "capabilities": ["resolve.utilityScript"],
                "utilityScript": "demo.py",
            ]
            value.merge(overrides) { _, new in new }
            return value
        }
        func file(_ path: String = "demo.py", _ data: Data = Data("print(1)\n".utf8),
                  role: String = "utilityScript", install: String? = "demo.py",
                  executable: Bool? = nil) -> [String: Any] {
            var value: [String: Any] = [
                "path": path, "sha256": digest(data), "size": data.count, "role": role,
            ]
            if let install { value["installName"] = install }
            if let executable { value["executable"] = executable }
            return value
        }
        func registryEntry(_ overrides: [String: Any] = [:]) -> [String: Any] {
            var value: [String: Any] = [
                "repository": "BrOKenAG/ordina-plugin-demo",
                "feed": feed, "downloadPrefix": prefix, "kind": "bundle",
                "name": "Demo", "summary": "demo plugin", "symbol": "note.text",
                "version": "1.0.0", "minClientVersion": "1.9.6",
                "protocol": 2, "capabilities": ["resolve.utilityScript"],
            ]
            value.merge(overrides) { _, new in new }
            return value
        }
        func registry(_ plugins: [String: Any]) throws -> Data {
            try envelope(["format": 2, "plugins": plugins])
        }
        func releaseEntry(_ files: [[String: Any]], descriptor: [String: Any],
                          version: String = "1.0.0", url: String = asset,
                          sha: String = String(repeating: "a", count: 64),
                          size: Int = 100, kind: String? = "bundle") throws -> Data {
            var entry: [String: Any] = [
                "version": version, "url": url, "sha256": sha, "size": size,
                "descriptor": descriptor, "files": files,
            ]
            if let kind { entry["kind"] = kind }
            return try envelope(["format": 1, "plugins": ["demo": entry]])
        }
        func release(_ data: Data, id: String = "demo") throws -> PluginRelease {
            try PluginProtocol.signedRelease(data, pluginID: id, publicKey: publicKey,
                                             policy: policy)
        }
        func container(_ files: [String: Data], version: String = "1.0.0",
                       id: String = "demo") throws -> Data {
            try JSONSerialization.data(withJSONObject: [
                "format": 1, "id": id, "version": version,
                "files": files.mapValues { $0.base64EncodedString() },
            ])
        }

        // --- registry ---
        let validRegistry = try registry(["demo": registryEntry()])
        let entries = try PluginProtocol.verifiedRegistry(validRegistry, publicKey: publicKey,
                                                          policy: policy)
        precondition(entries["demo"]?.name == "Demo")
        let tombstone = try envelope(["format": 2, "plugins": [:], "withdrawn": ["demo"]])
        let snapshot = try PluginProtocol.verifiedRegistrySnapshot(tombstone, publicKey: publicKey,
                                                                   policy: policy)
        precondition(snapshot.withdrawn == ["demo"] && snapshot.plugins.isEmpty)
        expectFailure("overlapping withdrawn plugin") {
            let conflicting = try envelope(["format": 2, "plugins": ["demo": registryEntry()],
                                            "withdrawn": ["demo"]])
            _ = try PluginProtocol.verifiedRegistrySnapshot(conflicting, publicKey: publicKey,
                                                             policy: policy)
        }
        expectFailure("registry wrong key") {
            _ = try PluginProtocol.verifiedRegistry(validRegistry, publicKey: otherKey, policy: policy)
        }
        expectFailure("registry format 1") {
            _ = try PluginProtocol.verifiedRegistry(try envelope(["format": 1, "plugins": ["demo": registryEntry()]]),
                                                    publicKey: publicKey, policy: policy)
        }
        for (label, value) in [
            ("http feed", registryEntry(["feed": "http://brokenag.github.io/ordina-plugin-demo/manifest-v2.json"])),
            ("foreign feed", registryEntry(["feed": "https://evil.example.com/ordina-plugin-demo/manifest-v2.json"])),
            ("cross-plugin feed", registryEntry(["feed": "https://brokenag.github.io/ordina-plugin-other/manifest-v2.json"])),
            ("query feed", registryEntry(["feed": feed + "?x=1"])),
            ("legacy prefix", registryEntry(["downloadPrefix": "/BrOKenAG/ordina-releases/releases/download/"])),
            ("cross prefix", registryEntry(["downloadPrefix": "/BrOKenAG/ordina-plugin-other/releases/download/"])),
            ("foreign repository", registryEntry(["repository": "someone/ordina-plugin-demo"])),
            ("missing name", registryEntry(["name": NSNull()])),
            ("bad symbol", registryEntry(["symbol": "Bad Symbol!"])),
            ("bad minimum", registryEntry(["minClientVersion": "latest"])),
            ("protocol 1", registryEntry(["protocol": 1])),
            ("unknown capability", registryEntry(["capabilities": ["shell.run"]])),
            ("empty capabilities", registryEntry(["capabilities": []])),
        ] {
            expectFailure("registry " + label) {
                _ = try PluginProtocol.verifiedRegistry(
                    try envelope(["format": 2, "plugins": ["demo": value]]),
                    publicKey: publicKey, policy: policy)
            }
        }
        expectFailure("registry bad id") {
            _ = try PluginProtocol.verifiedRegistry(
                try envelope(["format": 2, "plugins": ["../evil": registryEntry()]]),
                publicKey: publicKey, policy: policy)
        }

        // --- release and file inventory ---
        let script = Data("print(1)\n".utf8)
        let validFiles = [file(), file("plugin.json", Data("{}".utf8), role: "metadata", install: nil)]
        let validReleaseData = try releaseEntry(validFiles, descriptor: descriptor())
        let parsed = try release(validReleaseData)
        precondition(parsed.version == "1.0.0" && parsed.descriptor.isBundle)
        expectFailure("release wrong key") {
            _ = try PluginProtocol.signedRelease(validReleaseData, pluginID: "demo",
                                                 publicKey: otherKey, policy: policy)
        }
        expectFailure("release unknown id") { _ = try release(validReleaseData, id: "other") }
        expectFailure("release version mismatch") {
            _ = try release(try releaseEntry(validFiles, descriptor: descriptor(["version": "1.0.1"])))
        }
        expectFailure("release descriptor id mismatch") {
            _ = try release(try releaseEntry(validFiles, descriptor: descriptor(["id": "other"])))
        }
        expectFailure("release http asset") {
            _ = try release(try releaseEntry(validFiles, descriptor: descriptor(),
                                             url: "http://github.com" + prefix + "demo.json"))
        }
        expectFailure("release foreign asset") {
            _ = try release(try releaseEntry(validFiles, descriptor: descriptor(),
                                             url: "https://evil.example.com" + prefix + "demo.json"))
        }
        expectFailure("release cross-plugin asset") {
            _ = try release(try releaseEntry(
                validFiles, descriptor: descriptor(),
                url: "https://github.com/BrOKenAG/ordina-plugin-other/releases/download/plugin-other-v1/demo.json"))
        }
        expectFailure("release wrong extension") {
            _ = try release(try releaseEntry(validFiles, descriptor: descriptor(),
                                             url: "https://github.com" + prefix + "demo.py"))
        }
        expectFailure("release missing utility script") {
            _ = try release(try releaseEntry(
                [file("plugin.json", Data("{}".utf8), role: "metadata", install: nil)],
                descriptor: descriptor()))
        }
        expectFailure("release two utility scripts") {
            _ = try release(try releaseEntry(
                [file(), file("second.py"), file("plugin.json", Data("{}".utf8), role: "metadata", install: nil)],
                descriptor: descriptor()))
        }
        expectFailure("release traversal path") {
            _ = try release(try releaseEntry(
                [file("../escape.py"), file("plugin.json", Data("{}".utf8), role: "metadata", install: nil)],
                descriptor: descriptor()))
        }
        expectFailure("release absolute install name") {
            _ = try release(try releaseEntry([file(install: "/tmp/demo.py")], descriptor: descriptor()))
        }
        expectFailure("release bad digest") {
            _ = try release(try releaseEntry([file()], descriptor: descriptor(),
                                             sha: "xyz"))
        }
        expectFailure("release unknown capability") {
            _ = try release(try releaseEntry(validFiles,
                                             descriptor: descriptor(["capabilities": ["shell.run"]])))
        }
        expectFailure("release bad support directory") {
            _ = try release(try releaseEntry(validFiles,
                                             descriptor: descriptor(["supportDirectoryName": "../escape"])))
        }
        expectFailure("release unknown protocol") {
            _ = try release(try releaseEntry(validFiles, descriptor: descriptor(["protocol": 3])))
        }
        let chineseLegacy = try release(try releaseEntry(
            validFiles, descriptor: descriptor(["legacyNames": ["导入阅流.py"]])))
        precondition(chineseLegacy.descriptor.legacyNames == ["导入阅流.py"])
        expectFailure("release traversal legacy name") {
            _ = try release(try releaseEntry(
                validFiles, descriptor: descriptor(["legacyNames": ["../escape.py"]])))
        }

        // --- bundle unpacking ---
        let bundleFiles = [
            "plugin.json": Data("{\"id\":\"demo\",\"version\":\"1.0.0\"}".utf8),
            "demo.py": script,
        ]
        let containerData = try container(bundleFiles)
        let inventory = [
            file("plugin.json", bundleFiles["plugin.json"]!, role: "metadata", install: nil),
            file("demo.py", script),
        ]
        let bundleURL = "https://github.com" + prefix + "demo.json"
        let signedBundle = try release(try releaseEntry(
            inventory, descriptor: descriptor(), url: bundleURL,
            sha: digest(containerData), size: containerData.count))
        let unpacked = try PluginProtocol.unpack(containerData, release: signedBundle)
        precondition(unpacked["demo.py"] == script && unpacked.count == 2)
        var tampered = containerData
        tampered[tampered.count - 3] ^= 0xFF
        expectFailure("tampered container") {
            _ = try PluginProtocol.unpack(tampered, release: signedBundle)
        }
        let changedFile = try container(["plugin.json": bundleFiles["plugin.json"]!,
                                         "demo.py": Data("evil\n".utf8)])
        let changedRelease = try release(try releaseEntry(
            inventory, descriptor: descriptor(), url: bundleURL,
            sha: digest(changedFile), size: changedFile.count))
        expectFailure("container file does not match inventory") {
            _ = try PluginProtocol.unpack(changedFile, release: changedRelease)
        }
        let extraFile = try container(bundleFiles.merging(["extra.sh": Data("x".utf8)]) { a, _ in a })
        let extraRelease = try release(try releaseEntry(
            inventory, descriptor: descriptor(), url: bundleURL,
            sha: digest(extraFile), size: extraFile.count))
        expectFailure("undeclared container file") {
            _ = try PluginProtocol.unpack(extraFile, release: extraRelease)
        }
        let scriptDescriptor = descriptor(["kind": "script", "utilityScript": "demo.py"])
        let scriptInventory = [file("demo.py", script)]
        let signedScript = try release(try releaseEntry(
            scriptInventory, descriptor: scriptDescriptor,
            url: "https://github.com" + prefix + "demo.py",
            sha: digest(script), size: script.count, kind: nil))
        let unpackedScript = try PluginProtocol.unpack(script, release: signedScript)
        precondition(unpackedScript["demo.py"] == script)
        expectFailure("script digest mismatch") {
            _ = try PluginProtocol.unpack(Data("other".utf8), release: signedScript)
        }

        // --- primitives ---
        precondition(PluginProtocol.validID("demo-notes"))
        precondition(!PluginProtocol.validID("../evil"))
        precondition(PluginProtocol.newer("1.10.0", than: "1.9.9"))
        precondition(!PluginProtocol.newer("1.9.9", than: "1.9.9"))
        precondition(PluginProtocol.supports(clientVersion: "1.9.6", minimum: "1.9.6"))
        precondition(!PluginProtocol.supports(clientVersion: "1.9.5", minimum: "1.9.6"))
        precondition(PluginProtocol.validRelativePath("extension/icons/logo.png"))
        precondition(!PluginProtocol.validRelativePath("extension/../../etc"))
        precondition(!PluginProtocol.validRelativePath(".hidden"))
        precondition(PluginProtocol.validLegacyName("导入阅流.py"))
        precondition(!PluginProtocol.validLegacyName("../../x.py"))
        precondition(PluginProtocol.validInstallName("Multicam Tools.py"))
        precondition(!PluginProtocol.validInstallName("../x.py"))
        print("Plugin protocol, signatures, URL policy, inventory and unpacking passed")
    }
}
