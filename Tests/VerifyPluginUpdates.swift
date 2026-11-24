import Foundation
import CryptoKit

@main struct VerifyPluginUpdates {
    static func main() throws {
        let key = Curve25519.Signing.PrivateKey()
        let publicKey = key.publicKey.rawRepresentation

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
        func signedEntry(for data: Data, plugin: OptionalPlugin, version: String = "0.2.0") throws -> PluginUpdates.Entry {
            let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
            let payload = try JSONSerialization.data(withJSONObject: [
                "format": 1,
                "plugins": [plugin.id: [
                    "version": version,
                    "url": "https://github.com/BrOKenAG/ordina-releases/releases/download/x/\(plugin.id).json",
                    "sha256": digest,
                    "kind": "bundle",
                ]],
            ])
            return try PluginUpdates.verifiedEntry(try envelope(payload), plugin: plugin, publicKey: publicKey)
        }

        // --- single-script plugin (unchanged behaviour) ---
        let script = Data("PLUGIN_VERSION = \"v2.1\"\nprint('ok')".utf8)
        let digest = SHA256.hash(data: script).map { String(format: "%02x", $0) }.joined()
        let scriptPayload = Data("""
        {"format":1,"plugins":{"multicam":{"version":"2.1","url":"https://github.com/BrOKenAG/ordina-releases/releases/download/plugins-v2.1/Multicam.py","sha256":"\(digest)"}}}
        """.utf8)
        let scriptEnvelope = try envelope(scriptPayload)
        let scriptEntry = try PluginUpdates.verifiedEntry(scriptEnvelope, plugin: .multicam, publicKey: publicKey)
        precondition(scriptEntry.version == "2.1" && scriptEntry.isScript && !scriptEntry.isBundle)
        try PluginUpdates.verifyScript(script, entry: scriptEntry)
        expectFailure("tampered script") { try PluginUpdates.verifyScript(Data("tampered".utf8), entry: scriptEntry) }
        expectFailure("missing plugin") { _ = try PluginUpdates.verifiedEntry(scriptEnvelope, plugin: .yueliu, publicKey: publicKey) }
        expectFailure("wrong key") { _ = try PluginUpdates.verifiedEntry(scriptEnvelope, plugin: .multicam,
                                                                          publicKey: Curve25519.Signing.PrivateKey().publicKey.rawRepresentation) }
        precondition(PluginUpdates.newer("2.10", than: "2.9"))
        precondition(!PluginUpdates.newer("2.1", than: "2.1"))

        // --- bundle plugin ---
        let meta = Data("{\"id\":\"minutes\",\"version\":\"0.2.0\"}".utf8)
        let required = [
            "plugin.json": meta,
            "Minutes Roughcut.py": Data("# resolve script".utf8),
            "host.py": Data("# host".utf8),
            "host.command": Data("#!/bin/sh".utf8),
            "extension/manifest.json": Data("{\"manifest_version\":3}".utf8),
        ]
        let bundleData = try container(required)
        let bundleEntry = try signedEntry(for: bundleData, plugin: .minutes)
        precondition(bundleEntry.isBundle && !bundleEntry.isScript && bundleEntry.version == "0.2.0")
        let files = try PluginUpdates.verifyBundle(bundleData, entry: bundleEntry, plugin: .minutes)
        precondition(files.count == required.count)
        expectFailure("tampered bundle") { _ = try PluginUpdates.verifyBundle(Data("tampered".utf8), entry: bundleEntry, plugin: .minutes) }
        expectFailure("bundle verified as script") { try PluginUpdates.verifyScript(bundleData, entry: bundleEntry) }
        expectFailure("script entry verified as bundle") { _ = try PluginUpdates.verifyBundle(bundleData, entry: scriptEntry, plugin: .multicam) }

        var missing = required
        missing.removeValue(forKey: "host.command")
        let missingData = try container(missing)
        let missingEntry = try signedEntry(for: missingData, plugin: .minutes)
        expectFailure("missing required component") { _ = try PluginUpdates.verifyBundle(missingData, entry: missingEntry, plugin: .minutes) }

        let traversalData = try container(required.merging(["../../escape.py": Data("eA==".utf8)]) { a, _ in a })
        let traversalEntry = try signedEntry(for: traversalData, plugin: .minutes)
        expectFailure("path traversal") { _ = try PluginUpdates.verifyBundle(traversalData, entry: traversalEntry, plugin: .minutes) }

        let wrongVersionData = try container(required, version: "0.9.0")
        let wrongVersionEntry = try signedEntry(for: wrongVersionData, plugin: .minutes, version: "0.9.0")
        expectFailure("version mismatch") { _ = try PluginUpdates.verifyBundle(wrongVersionData, entry: wrongVersionEntry, plugin: .minutes) }

        let wrongIdData = try container(required, id: "other")
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
        {"format":1,"plugins":{"minutes":{"version":"0.2.0","url":"https://github.com/BrOKenAG/ordina-releases/releases/download/x/minutes.json","sha256":"\(SHA256.hash(data: bundleData).map { String(format: "%02x", $0) }.joined())","kind":"evil"}}}
        """.utf8)
        expectFailure("unknown kind") { _ = try PluginUpdates.verifiedEntry(try envelope(badKind), plugin: .minutes, publicKey: publicKey) }
        let wrongExtension = Data("""
        {"format":1,"plugins":{"minutes":{"version":"0.2.0","url":"https://github.com/BrOKenAG/ordina-releases/releases/download/x/minutes.py","sha256":"\(SHA256.hash(data: bundleData).map { String(format: "%02x", $0) }.joined())","kind":"bundle"}}}
        """.utf8)
        expectFailure("bundle with .py url") { _ = try PluginUpdates.verifiedEntry(try envelope(wrongExtension), plugin: .minutes, publicKey: publicKey) }

        print("Plugin update signature, digest, versions and bundle verification passed")
    }
}
