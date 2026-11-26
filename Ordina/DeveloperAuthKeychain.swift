import Foundation
import Security

/// Secret and last-used counter are stored together so a successful code can
/// be persisted before a session is granted. Never serialize this into defaults.
struct DeveloperAuthRecord: Codable {
    let secret: Data
    var lastCounter: UInt64?
    var failures: Int? = nil
    var retryAfter: TimeInterval? = nil
}

enum DeveloperAuthKeychainError: Error {
    case status(OSStatus)
    case corrupt
}

struct DeveloperAuthKeychain {
    let service: String
    private let account = "totp"

    var isolatedPath: String? = nil
    var isolatedPassword: Data? = nil

    private func isolatedKeychain() throws -> SecKeychain? {
        guard let isolatedPath else { return nil }
        guard let password = isolatedPassword, password.count >= 24 else {
            throw DeveloperAuthKeychainError.corrupt
        }
        var keychain: SecKeychain?
        let status: OSStatus
        if FileManager.default.fileExists(atPath: isolatedPath) {
            status = SecKeychainOpen(isolatedPath, &keychain)
            guard status == errSecSuccess, let keychain else { throw DeveloperAuthKeychainError.status(status) }
            let unlocked = password.withUnsafeBytes { SecKeychainUnlock(keychain, UInt32($0.count), $0.baseAddress, true) }
            guard unlocked == errSecSuccess else { throw DeveloperAuthKeychainError.status(unlocked) }
        } else {
            status = password.withUnsafeBytes {
                SecKeychainCreate(isolatedPath, UInt32($0.count), $0.baseAddress, false, nil, &keychain)
            }
            guard status == errSecSuccess else { throw DeveloperAuthKeychainError.status(status) }
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: isolatedPath)
        }
        guard let keychain else { throw DeveloperAuthKeychainError.corrupt }
        return keychain
    }

    private func query(_ keychain: SecKeychain?) -> [String: Any] {
        var result: [String: Any] = [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account,
         kSecAttrSynchronizable as String: false]
        if let keychain { result[kSecMatchSearchList as String] = [keychain] }
        return result
    }

    func read() throws -> DeveloperAuthRecord? {
        var request = query(try isolatedKeychain())
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess else { throw DeveloperAuthKeychainError.status(status) }
        guard let data = result as? Data,
              let record = try? JSONDecoder().decode(DeveloperAuthRecord.self, from: data),
              record.secret.count >= 20 else { throw DeveloperAuthKeychainError.corrupt }
        return record
    }

    func save(_ record: DeveloperAuthRecord) throws {
        let data = try JSONEncoder().encode(record)
        let keychain = try isolatedKeychain()
        let request = query(keychain)
        let changes: [String: Any] = [kSecValueData as String: data]
        let status = SecItemUpdate(request as CFDictionary, changes as CFDictionary)
        if status == errSecItemNotFound {
            var item = request
            item.removeValue(forKey: kSecMatchSearchList as String)
            item[kSecValueData as String] = data
            if let keychain { item[kSecUseKeychain as String] = keychain }
            else { item[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly }
            let added = SecItemAdd(item as CFDictionary, nil)
            guard added == errSecSuccess else { throw DeveloperAuthKeychainError.status(added) }
        } else if status != errSecSuccess {
            throw DeveloperAuthKeychainError.status(status)
        }
    }

    func remove() throws {
        let status = SecItemDelete(query(try isolatedKeychain()) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw DeveloperAuthKeychainError.status(status)
        }
    }

    static func generateSecret() throws -> Data {
        var bytes = [UInt8](repeating: 0, count: 20)
        let status = SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes)
        guard status == errSecSuccess else { throw DeveloperAuthKeychainError.status(status) }
        return Data(bytes)
    }
}
