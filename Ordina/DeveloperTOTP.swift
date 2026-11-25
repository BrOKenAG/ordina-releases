import Foundation
import CryptoKit

// RFC 6238 / RFC 4226. Session management and Keychain storage are separate.
enum DeveloperTOTP {
    static func code(secret: Data, counter: UInt64) -> String {
        var moving = counter.bigEndian
        let message = withUnsafeBytes(of: &moving) { Data($0) }
        let digest = Array(HMAC<Insecure.SHA1>.authenticationCode(
            for: message, using: SymmetricKey(data: secret)))
        let offset = Int(digest[19] & 0x0f)
        let number = (UInt32(digest[offset] & 0x7f) << 24)
            | (UInt32(digest[offset + 1]) << 16)
            | (UInt32(digest[offset + 2]) << 8)
            | UInt32(digest[offset + 3])
        return String(format: "%06u", number % 1_000_000)
    }

    static func match(_ candidate: String, secret: Data, time: TimeInterval,
                      lastCounter: UInt64?) -> UInt64? {
        let bytes = Array(candidate.utf8)
        guard bytes.count == 6, bytes.allSatisfy({ (48...57).contains($0) }),
              time >= 0, time.isFinite, time < Double(UInt64.max) else { return nil }
        let current = UInt64(time / 30)
        for delta in [-1, 0, 1] {
            if delta == -1 && current == 0 { continue }
            let counter = delta == -1 ? current - 1 : current + UInt64(delta)
            if let lastCounter, counter <= lastCounter { continue }
            let expected = Array(code(secret: secret, counter: counter).utf8)
            let difference = zip(bytes, expected).reduce(UInt8(0)) { $0 | ($1.0 ^ $1.1) }
            if difference == 0 { return counter }
        }
        return nil
    }
}
