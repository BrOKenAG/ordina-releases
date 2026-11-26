import Foundation
import Combine

@MainActor
final class DeveloperAuthentication: ObservableObject {
    @Published var bound = false
    @Published var unlocked = false
    @Published var busy = false
    @Published var error: String?
    @Published var setupURI: String?
    private var pending: Data?
    private var session = DeveloperAuthState()
    private var store: DeveloperAuthKeychain {
        DeveloperAuthKeychain(service: (Bundle.main.bundleIdentifier ?? "com.ordina.app") + ".developer-totp")
    }

    func load() {
        do { bound = try store.read() != nil }
        catch { self.error = "无法读取钥匙串。" }
    }

    private func authorize(_ configuration: DeveloperConfiguration) async throws {
        guard configuration.toolPath.isEmpty, let installation = DevToolManager.installation(),
              let path = DevToolManager.verifiedPath(configuration) else {
            throw DevBridgeError.response("请先安装可信发布工具。")
        }
        let manifest = try await Task.detached(priority: .userInitiated) {
            try DevToolManager.manifest(forceRefresh: true)
        }.value
        guard let entry = manifest.versions.first(where: { $0.version == installation.version }),
              entry.sha256 == installation.sha256,
              DevToolManager.digest(of: path) == entry.sha256 else {
            throw DevBridgeError.response("发布工具未通过签名和完整性验证。")
        }
        try await LocalPublisherTool(executable: path, configuration: configuration).authorizeIdentity()
    }

    func beginBinding(_ configuration: DeveloperConfiguration) async {
        guard !busy else { return }
        busy = true; error = nil
        defer { busy = false }
        do {
            try await authorize(configuration)
            let secret = try DeveloperAuthKeychain.generateSecret()
            pending = secret
            let encoded = base32(secret)
            setupURI = "otpauth://totp/ordina:Developer?secret=\(encoded)&issuer=ordina&algorithm=SHA1&digits=6&period=30"
        } catch { self.error = error.localizedDescription }
    }

    func cancelBinding() { pending = nil; setupURI = nil }

    func verify(_ code: String, configuration: DeveloperConfiguration) async {
        guard !busy else { return }
        busy = true; error = nil
        defer { busy = false }
        do {
            let now = Date().timeIntervalSince1970
            var current = try store.read()
            if let retry = current?.retryAfter, now < retry {
                throw DevBridgeError.response("尝试次数过多，请稍后重试。")
            }
            guard let secret = pending ?? current?.secret else {
                throw DevBridgeError.response("请先绑定验证器。")
            }
            let last = pending == nil ? current?.lastCounter : nil
            guard let counter = DeveloperTOTP.match(code, secret: secret, time: now, lastCounter: last) else {
                if var record = current {
                    let failures = (record.failures ?? 0) + 1
                    record.failures = failures >= 5 ? 0 : failures
                    if failures >= 5 { record.retryAfter = now + 30 }
                    try store.save(record)
                } else {
                    session.failed(at: ProcessInfo.processInfo.systemUptime)
                }
                throw DevBridgeError.response("验证码无效或已使用。")
            }
            if pending != nil && !session.canAttempt(at: ProcessInfo.processInfo.systemUptime) {
                throw DevBridgeError.response("尝试次数过多，请稍后重试。")
            }
            try await authorize(configuration)
            current = DeveloperAuthRecord(secret: secret, lastCounter: counter)
            try store.save(current!)
            pending = nil; setupURI = nil; bound = true
            session.unlock(at: ProcessInfo.processInfo.systemUptime)
            unlocked = true
        } catch { self.error = error.localizedDescription }
    }

    func lock() { session.lock(); unlocked = false; cancelBinding() }
    func tick() {
        if unlocked && !session.isUnlocked(at: ProcessInfo.processInfo.systemUptime) { lock() }
    }

    func revoke(_ configuration: DeveloperConfiguration) async {
        guard !busy else { return }
        busy = true; error = nil
        defer { busy = false }
        do {
            try await authorize(configuration)
            try store.remove()
            bound = false; lock()
        } catch { self.error = error.localizedDescription }
    }

    private func base32(_ data: Data) -> String {
        let alphabet = Array("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")
        var buffer = 0, bits = 0
        var result = ""
        for byte in data {
            buffer = (buffer << 8) | Int(byte); bits += 8
            while bits >= 5 {
                bits -= 5
                result.append(alphabet[(buffer >> bits) & 31])
                buffer &= (1 << bits) - 1
            }
        }
        if bits > 0 { result.append(alphabet[(buffer << (5 - bits)) & 31]) }
        return result
    }
}
