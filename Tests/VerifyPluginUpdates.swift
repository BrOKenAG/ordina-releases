import Foundation
import CryptoKit

@main struct VerifyPluginUpdates {
    static func main() throws {
        let key = Curve25519.Signing.PrivateKey()
        let publicKey = key.publicKey.rawRepresentation
        let minutesPrefix = "/BrOKenAG/ordina-plugin-minutes/releases/download/"
        let multicamPrefix = "/BrOKenAG/ordina-plugin-multicam/releases/download/"
        let minutesURL = "https://github.com/BrOKenAG/ordina-plugin-minutes/releases/download/plugin-minutes-v0.2.0/minutes.json"
        let multicamURL = "https://github.com/BrOKenAG/ordina-plugin-multicam/releases/download/plugin-multicam-v2.1/Multicam.Tools.py"

        func envelope(_ payload: Data) throws -> Data {
            try JSONSerialization.data(withJSONObject: [
                "payload": payload.base64EncodedString(),
                "signature": key.signature(for: payload).base64EncodedString(),
            ])
        }
        func expectFailure(_ label: String, _ operation: () throws -> Void) {
            do { try operation() } catch { return }
            fatalError(label + " was accepted")
        }
        func container(_ files: [String: Data], version: String = "0.2.0", id: String = "minutes") throws -> Data {
            try JSONSerialization.data(withJSONObject: [
                "format": 1, "id": id, "version": version,
                "files": files.mapValues { $0.base64EncodedString() },
            ])
        }
        func signedEntry(for data: Data, plugin: OptionalPlugin, version: String = "0.2.0",
                         url: String? = nil, prefix: String? = nil,
                         kind: String? = "bundle") throws -> PluginUpdates.Entry {
            let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
            let assetURL = url ?? "https://github.com/BrOKenAG/ordina-plugin-\(plugin.id)/releases/download/plugin-\(plugin.id)-v\(version)/\(plugin.id).json"
            var fields: [String: Any] = ["version": version, "url": assetURL, "sha256": digest]
            if let kind { fields["kind"] = kind }
            let payload = try JSONSerialization.data(withJSONObject: ["format": 1, "plugins": [plugin.id: fields]])
            return try PluginUpdates.verifiedEntry(try envelope(payload), plugin: plugin,
                                                   publicKey: publicKey,
                                                   allowedPrefix: prefix ?? "/BrOKenAG/ordina-plugin-\(plugin.id)/releases/download/")
        }
        func registry(_ plugins: [String: [String: String]], format: Int = 2) throws -> Data {
            let payload = try JSONSerialization.data(withJSONObject: ["format": format, "plugins": plugins])
            return try envelope(payload)
        }
        func descriptor(_ name: String, kind: String = "script") -> [String: String] {
            [
                "repository": "BrOKenAG/ordina-plugin-\(name)",
                "feed": "https://brokenag.github.io/ordina-plugin-\(name)/manifest.json",
                "downloadPrefix": "/BrOKenAG/ordina-plugin-\(name)/releases/download/",
                "kind": kind,
            ]
        }

        // --- single-script plugin (unchanged behaviour) ---
        let script = Data("PLUGIN_VERSION = \"v2.1\"\nprint('ok')".utf8)
        let digest = SHA256.hash(data: script).map { String(format: "%02x", $0) }.joined()
        let scriptPayload = Data("""
        {"format":1,"plugins":{"multicam":{"version":"2.1","url":"\(multicamURL)","sha256":"\(digest)"}}}
        """.utf8)
        let scriptEnvelope = try envelope(scriptPayload)
        let scriptEntry = try PluginUpdates.verifiedEntry(scriptEnvelope, plugin: .multicam,
                                                          publicKey: publicKey, allowedPrefix: multicamPrefix)
        precondition(scriptEntry.version == "2.1" && scriptEntry.isScript && !scriptEntry.isBundle)
        try PluginUpdates.verifyScript(script, entry: scriptEntry)
        expectFailure("tampered script") { try PluginUpdates.verifyScript(Data("tampered".utf8), entry: scriptEntry) }
        expectFailure("missing plugin") { _ = try PluginUpdates.verifiedEntry(scriptEnvelope, plugin: .yueliu,
                                                                              publicKey: publicKey, allowedPrefix: multicamPrefix) }
        expectFailure("wrong key") { _ = try PluginUpdates.verifiedEntry(scriptEnvelope, plugin: .multicam,
                                                                         publicKey: Curve25519.Signing.PrivateKey().publicKey.rawRepresentation,
                                                                         allowedPrefix: multicamPrefix) }
        precondition(PluginUpdates.newer("2.10", than: "2.9"))
        precondition(!PluginUpdates.newer("2.1", than: "2.1"))

        // --- regression: independent plugin repositories must be accepted ---
        // ordina 1.9.5 hardcoded the main-repository prefix and rejected these.
        let independentBundle = try container([
            "plugin.json": Data("{\"id\":\"minutes\",\"version\":\"0.2.0\"}".utf8),
            "Minutes Roughcut.py": Data("# resolve script".utf8),
            "host.py": Data("# host".utf8),
            "host.command": Data("#!/bin/sh".utf8),
            "extension/manifest.json": Data("{\"manifest_version\":3}".utf8),
        ])
        let independentEntry = try signedEntry(for: independentBundle, plugin: .minutes, url: minutesURL)
        precondition(independentEntry.url == minutesURL)
        expectFailure("legacy main-repository prefix") {
            _ = try signedEntry(for: independentBundle, plugin: .minutes, url: minutesURL,
                                prefix: "/BrOKenAG/ordina-releases/releases/download/")
        }
        expectFailure("cross-plugin prefix") {
            _ = try signedEntry(for: independentBundle, plugin: .minutes, url: minutesURL,
                                prefix: multicamPrefix)
        }
        expectFailure("invalid download prefix") {
            _ = try signedEntry(for: independentBundle, plugin: .minutes, url: minutesURL,
                                prefix: "/BrOKenAG/ordina-plugin-minutes/../../releases/download/")
        }

        // --- bundle plugin ---
        let bundleEntry = independentEntry
        precondition(bundleEntry.isBundle && !bundleEntry.isScript && bundleEntry.version == "0.2.0")
        let files = try PluginUpdates.verifyBundle(independentBundle, entry: bundleEntry, plugin: .minutes)
        precondition(files.count == 5)
        expectFailure("tampered bundle") { _ = try PluginUpdates.verifyBundle(Data("tampered".utf8), entry: bundleEntry, plugin: .minutes) }
        expectFailure("bundle verified as script") { try PluginUpdates.verifyScript(independentBundle, entry: bundleEntry) }
        expectFailure("script entry verified as bundle") { _ = try PluginUpdates.verifyBundle(independentBundle, entry: scriptEntry, plugin: .multicam) }

        var missing = [
            "plugin.json": Data("{\"id\":\"minutes\",\"version\":\"0.2.0\"}".utf8),
            "Minutes Roughcut.py": Data("# resolve script".utf8),
            "host.py": Data("# host".utf8),
            "extension/manifest.json": Data("{\"manifest_version\":3}".utf8),
        ]
        let missingData = try container(missing)
        let missingEntry = try signedEntry(for: missingData, plugin: .minutes)
        expectFailure("missing required component") { _ = try PluginUpdates.verifyBundle(missingData, entry: missingEntry, plugin: .minutes) }
        missing["host.command"] = Data("#!/bin/sh".utf8)

        let traversalData = try container(missing.merging(["../../escape.py": Data("eA==".utf8)]) { a, _ in a })
        let traversalEntry = try signedEntry(for: traversalData, plugin: .minutes)
        expectFailure("path traversal") { _ = try PluginUpdates.verifyBundle(traversalData, entry: traversalEntry, plugin: .minutes) }

        let wrongVersionData = try container(missing, version: "0.9.0")
        let wrongVersionEntry = try signedEntry(for: wrongVersionData, plugin: .minutes, version: "0.9.0")
        expectFailure("version mismatch") { _ = try PluginUpdates.verifyBundle(wrongVersionData, entry: wrongVersionEntry, plugin: .minutes) }

        let wrongIdData = try container(missing, id: "other")
        let wrongIdEntry = try signedEntry(for: wrongIdData, plugin: .minutes)
        expectFailure("id mismatch") { _ = try PluginUpdates.verifyBundle(wrongIdData, entry: wrongIdEntry, plugin: .minutes) }

        precondition(PluginUpdates.validBundlePath("extension/icons/logo.png"))
        precondition(PluginUpdates.validBundlePath("Minutes Roughcut.py"))
        precondition(!PluginUpdates.validBundlePath("/absolute.py"))
        precondition(!PluginUpdates.validBundlePath("..\\escape.py"))
        precondition(!PluginUpdates.validBundlePath("extension/../../etc"))
        precondition(!PluginUpdates.validBundlePath(".hidden"))
        precondition(!PluginUpdates.validBundlePath("a/b/c/d/e.js"))

        let badKind = Data("""
        {"format":1,"plugins":{"minutes":{"version":"0.2.0","url":"\(minutesURL)","sha256":"\(SHA256.hash(data: independentBundle).map { String(format: "%02x", $0) }.joined())","kind":"evil"}}}
        """.utf8)
        expectFailure("unknown kind") { _ = try PluginUpdates.verifiedEntry(try envelope(badKind), plugin: .minutes,
                                                                            publicKey: publicKey, allowedPrefix: minutesPrefix) }
        let wrongExtension = Data("""
        {"format":1,"plugins":{"minutes":{"version":"0.2.0","url":"https://github.com/BrOKenAG/ordina-plugin-minutes/releases/download/plugin-minutes-v0.2.0/minutes.py","sha256":"\(SHA256.hash(data: independentBundle).map { String(format: "%02x", $0) }.joined())","kind":"bundle"}}}
        """.utf8)
        expectFailure("bundle with .py url") { _ = try PluginUpdates.verifiedEntry(try envelope(wrongExtension), plugin: .minutes,
                                                                                   publicKey: publicKey, allowedPrefix: minutesPrefix) }

        // --- signed remote registry ---
        let validRegistry = try registry(["minutes": descriptor("minutes", kind: "bundle"),
                                          "multicam": descriptor("multicam")])
        let entries = try PluginUpdates.verifiedRegistry(validRegistry, publicKey: publicKey)
        precondition(entries.count == 2)
        precondition(entries["minutes"]?.downloadPrefix == minutesPrefix)
        precondition(entries["multicam"]?.feed == "https://brokenag.github.io/ordina-plugin-multicam/manifest.json")
        expectFailure("registry wrong key") {
            _ = try PluginUpdates.verifiedRegistry(validRegistry,
                                                   publicKey: Curve25519.Signing.PrivateKey().publicKey.rawRepresentation)
        }
        let tampered = try JSONSerialization.data(withJSONObject: [
            "payload": validRegistry.count > 0 ? (try JSONSerialization.jsonObject(with: validRegistry) as! [String: Any])["payload"]! : "",
            "signature": Data("invalid".utf8).base64EncodedString(),
        ])
        expectFailure("registry bad signature") { _ = try PluginUpdates.verifiedRegistry(tampered, publicKey: publicKey) }
        expectFailure("registry wrong format") { _ = try PluginUpdates.verifiedRegistry(try registry(["minutes": descriptor("minutes")], format: 1), publicKey: publicKey) }
        expectFailure("registry http feed") {
            var bad = descriptor("minutes"); bad["feed"] = "http://brokenag.github.io/ordina-plugin-minutes/manifest.json"
            _ = try PluginUpdates.verifiedRegistry(try registry(["minutes": bad]), publicKey: publicKey)
        }
        expectFailure("registry external feed") {
            var bad = descriptor("minutes"); bad["feed"] = "https://evil.example.com/ordina-plugin-minutes/manifest.json"
            _ = try PluginUpdates.verifiedRegistry(try registry(["minutes": bad]), publicKey: publicKey)
        }
        expectFailure("registry bad download prefix") {
            var bad = descriptor("minutes"); bad["downloadPrefix"] = "/BrOKenAG/ordina-plugin-minutes/releases/"
            _ = try PluginUpdates.verifiedRegistry(try registry(["minutes": bad]), publicKey: publicKey)
        }
        expectFailure("registry bad repository") {
            var bad = descriptor("minutes"); bad["repository"] = "someone-else/ordina-plugin-minutes"
            _ = try PluginUpdates.verifiedRegistry(try registry(["minutes": bad]), publicKey: publicKey)
        }
        expectFailure("registry bad plugin id") {
            _ = try PluginUpdates.verifiedRegistry(try registry(["../evil": descriptor("minutes")]), publicKey: publicKey)
        }

        print("Plugin registry, signature, digest, versions and bundle verification passed")
    }
}
