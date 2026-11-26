import Foundation

@main
struct VerifyDeveloperAuthKeychain {
    static func main() throws {
        let testPath = ProcessInfo.processInfo.environment["ORDINA_TEST_KEYCHAIN"]
        let testPassword = testPath == nil ? nil : Data(repeating: 0x42, count: 32)
        let store = DeveloperAuthKeychain(service: "com.ordina.test.totp." + UUID().uuidString,
                                         isolatedPath: testPath, isolatedPassword: testPassword)
        defer { try? store.remove() }
        let absent = try store.read()
        precondition(absent == nil)
        let secret = try DeveloperAuthKeychain.generateSecret()
        try store.save(DeveloperAuthRecord(secret: secret, lastCounter: 10))
        let saved = try store.read()
        precondition(saved?.secret == secret && saved?.lastCounter == 10)
        try store.save(DeveloperAuthRecord(secret: secret, lastCounter: 11))
        let updated = try store.read()
        precondition(updated?.lastCounter == 11)
        try store.remove()
        let removed = try store.read()
        precondition(removed == nil)
        print("Isolated Keychain save, replay counter and removal passed")
    }
}
