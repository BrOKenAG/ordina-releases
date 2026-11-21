import Foundation
import CryptoKit

@main struct VerifyPluginUpdates {
    static func main() throws {
        let key = Curve25519.Signing.PrivateKey()
        let script = Data("PLUGIN_VERSION = \"v2.1\"\nprint('ok')".utf8)
        let digest = SHA256.hash(data: script).map { String(format: "%02x", $0) }.joined()
        let payload = Data("""
        {"format":1,"plugins":{"multicam":{"version":"2.1","url":"https://github.com/BrOKenAG/ordina-releases/releases/download/plugins-v2.1/Multicam.py","sha256":"\(digest)"}}}
        """.utf8)
        let envelope = try JSONSerialization.data(withJSONObject: ["payload": payload.base64EncodedString(), "signature": key.signature(for: payload).base64EncodedString()])
        let entry = try PluginUpdates.verifiedEntry(envelope, plugin: .multicam, publicKey: key.publicKey.rawRepresentation)
        precondition(entry.version == "2.1")
        try PluginUpdates.verifyScript(script, entry: entry)
        do { try PluginUpdates.verifyScript(Data("tampered".utf8), entry: entry); fatalError("tampering accepted") } catch {}
        let badKey = Curve25519.Signing.PrivateKey().publicKey.rawRepresentation
        do { _ = try PluginUpdates.verifiedEntry(envelope, plugin: .multicam, publicKey: badKey); fatalError("signature accepted") } catch {}
        precondition(PluginUpdates.newer("2.10", than: "2.9"))
        precondition(!PluginUpdates.newer("2.1", than: "2.1"))
        print("Plugin update signature, digest and versions verified")
    }
}
