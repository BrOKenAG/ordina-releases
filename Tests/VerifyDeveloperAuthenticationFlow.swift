import Foundation

enum RecoveryTestError: Error { case denied }

@main
struct VerifyDeveloperAuthenticationFlow {
    static func secret(_ uri: String) -> Data {
        let components = URLComponents(string: uri)!
        let encoded = components.queryItems!.first { $0.name == "secret" }!.value!
        let alphabet = Array("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")
        var buffer = 0, bits = 0, output = Data()
        for character in encoded {
            buffer = (buffer << 5) | alphabet.firstIndex(of: character)!
            bits += 5
            if bits >= 8 { bits -= 8; output.append(UInt8((buffer >> bits) & 255)); buffer &= (1 << bits) - 1 }
        }
        return output
    }
    @MainActor static func main() async throws {
        let env = ProcessInfo.processInfo.environment
        precondition(env["ORDINA_DEV_LAB"] == "1" && env["ORDINA_DEV_BACKEND"] == "lab")
        let manifest = try DevToolManager.manifest(forceRefresh: true)
        _ = try DevToolManager.install(manifest.versions.last!)
        var configuration = DeveloperConfiguration()
        configuration.repositoryPath = env["ORDINA_DEV_REPO"]!
        let auth = DeveloperAuthentication()
        auth.load()
        precondition(!auth.bound && auth.error == nil)
        var unauthorized = configuration
        unauthorized.toolPath = "/invalid/untrusted/tool"
        await auth.beginBinding(unauthorized)
        precondition(auth.setupURI == nil && !auth.bound && auth.error != nil)
        await auth.beginBinding(configuration)
        guard let uri = auth.setupURI else { fatalError(auth.error ?? "binding failed") }
        let seed = secret(uri)
        let counter = UInt64(Date().timeIntervalSince1970 / 30)
        let first = DeveloperTOTP.code(secret: seed, counter: counter)
        await auth.verify(first, configuration: configuration)
        guard auth.unlocked && auth.bound else { fatalError(auth.error ?? "verification failed") }
        auth.lock()
        let denied = DeveloperAuthentication(recoveryCheck: { throw RecoveryTestError.denied })
        denied.load()
        await denied.beginBinding(configuration)
        precondition(denied.setupURI == nil && denied.bound && denied.error != nil)
        await denied.revoke(configuration)
        precondition(denied.bound && denied.error != nil)
        let allowed = DeveloperAuthentication(recoveryCheck: {})
        allowed.load()
        await allowed.beginBinding(configuration)
        precondition(allowed.setupURI != nil && secret(allowed.setupURI!) != seed)
        await allowed.verify("invalid", configuration: configuration)
        precondition(allowed.bound && !allowed.unlocked && allowed.error != nil)
        let replacement = secret(allowed.setupURI!)
        let replacementCode = DeveloperTOTP.code(secret: replacement, counter: UInt64(Date().timeIntervalSince1970 / 30))
        await allowed.verify(replacementCode, configuration: unauthorized)
        precondition(allowed.bound && !allowed.unlocked && allowed.error != nil)
        allowed.cancelBinding()
        let unchanged = DeveloperAuthentication()
        unchanged.load()
        precondition(unchanged.bound)
        await auth.verify(first, configuration: configuration)
        precondition(!auth.unlocked && auth.error != nil)
        let next = DeveloperTOTP.code(secret: seed, counter: counter + 1)
        await auth.verify(next, configuration: configuration)
        guard auth.unlocked else { fatalError(auth.error ?? "unlock failed") }
        let restored = DeveloperAuthentication(recoveryCheck: {})
        restored.load()
        precondition(restored.bound && !restored.unlocked)
        await restored.revoke(configuration)
        precondition(!restored.bound && !restored.unlocked)
        await restored.beginBinding(configuration)
        precondition(restored.setupURI != nil && secret(restored.setupURI!) != seed)
        restored.cancelBinding()
        print("Isolated binding, authentication, replay, session, revocation and rebinding passed")
    }
}
