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

    private var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account,
         kSecAttrSynchronizable as String: false]
    }

    func read() throws -> DeveloperAuthRecord? {
        var request = query
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
        let changes: [String: Any] = [kSecValueData as String: data]
        let status = SecItemUpdate(query as CFDictionary, changes as CFDictionary)
        if status == errSecItemNotFound {
            var item = query
            item[kSecValueData as String] = data
            item[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
            let added = SecItemAdd(item as CFDictionary, nil)
            guard added == errSecSuccess else { throw DeveloperAuthKeychainError.status(added) }
        } else if status != errSecSuccess {
            throw DeveloperAuthKeychainError.status(status)
        }
    }

    func remove() throws {
        let status = SecItemDelete(query as CFDictionary)
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
