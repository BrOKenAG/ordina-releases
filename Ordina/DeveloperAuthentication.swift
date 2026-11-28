import Foundation
import Combine
import LocalAuthentication

@MainActor
final class DeveloperAuthentication: ObservableObject {
    @Published var bound = false
    @Published var unlocked = false
    @Published var deviceTrusted = false
    @Published var busy = false
    @Published var error: String?
    @Published var authorizationExpired = false
    @Published var setupURI: String?
    private var pending: Data?
    private let session = DeveloperAuthSession.shared
    private var attempts = DeveloperAuthState()
    private var sessionCancellable: AnyCancellable?
    private var deviceVerificationDeadline: TimeInterval?
    #if ORDINA_LAB
    private var recoveryCheck: (() async throws -> Void)?
    private var authorityCheck: (() async throws -> Void)?
    init(recoveryCheck: (() async throws -> Void)? = nil,
         authorityCheck: (() async throws -> Void)? = nil) {
        self.recoveryCheck = recoveryCheck
        self.authorityCheck = authorityCheck
        attachSession()
    }
    #else
    init() { attachSession() }
    #endif

    private func attachSession() {
        sessionCancellable = session.$unlocked.sink { [weak self] in self?.unlocked = $0 }
    }

    private func verifyRecovery() async throws {
        #if ORDINA_LAB
        if let recoveryCheck { try await recoveryCheck(); return }
        #endif
        let context = LAContext()
        var evaluationError: NSError?
        guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &evaluationError) else {
            throw DevBridgeError.response("无法验证本机身份，请检查 macOS 登录设置。")
        }
        do {
            guard try await context.evaluatePolicy(.deviceOwnerAuthentication,
                    localizedReason: "验证身份以重新配置开发者验证器") else {
                throw DevBridgeError.response("本机身份验证未完成。")
            }
        } catch {
            throw DevBridgeError.response("本机身份验证未完成。")
        }
    }

    private var store: DeveloperAuthKeychain {
        let service = (Bundle.main.bundleIdentifier ?? "com.ordina.app") + ".developer-totp"
        #if DEBUG || ORDINA_LAB
        if DevBridge.isLab {
            let root = DeveloperPaths.home
            let path = root.appendingPathComponent("ordina-totp-test.keychain-db").path
            let passwordPath = root.appendingPathComponent("ordina-totp-keychain-password")
            return DeveloperAuthKeychain(service: service, isolatedPath: path,
                                         isolatedPassword: try? Data(contentsOf: passwordPath))
        }
        #endif
        return DeveloperAuthKeychain(service: service)
    }

    private var trustStore: DeveloperAuthKeychain {
        let service = (Bundle.main.bundleIdentifier ?? "com.ordina.app") + ".developer-device-trust"
        #if DEBUG || ORDINA_LAB
        if DevBridge.isLab {
            let root = DeveloperPaths.home
            let path = root.appendingPathComponent("ordina-device-trust-test.keychain-db").path
            let passwordPath = root.appendingPathComponent("ordina-totp-keychain-password")
            return DeveloperAuthKeychain(service: service, isolatedPath: path,
                                         isolatedPassword: try? Data(contentsOf: passwordPath))
        }
        #endif
        return DeveloperAuthKeychain(service: service)
    }

    func load() {
        do {
            bound = try store.read() != nil
            deviceTrusted = try trustStore.read() != nil
        } catch { self.error = "无法读取钥匙串。" }
    }

    private func authorize(_ configuration: DeveloperConfiguration) async throws {
        #if ORDINA_LAB
        if let authorityCheck { try await authorityCheck(); return }
        #endif
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

    private func showAuthorizationError(_ error: Error) {
        authorizationExpired = (error as? DevFailure)?.code == "auth.expired"
        self.error = authorizationExpired ? "发布授权已过期，请导入重新签发的授权。" : error.localizedDescription
    }

    func beginBinding(_ configuration: DeveloperConfiguration) async {
        guard !busy else { return }
        busy = true; error = nil
        defer { busy = false }
        do {
            if try store.read() != nil { try await verifyRecovery() }
            try await authorize(configuration)
            let secret = try DeveloperAuthKeychain.generateSecret()
            pending = secret
            authorizationExpired = false
            let encoded = base32(secret)
            setupURI = "otpauth://totp/ordina:Developer?secret=\(encoded)&issuer=ordina&algorithm=SHA1&digits=6&period=30"
        } catch { showAuthorizationError(error) }
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
                    attempts.failed(at: ProcessInfo.processInfo.systemUptime)
                }
                throw DevBridgeError.response("验证码无效或已使用。")
            }
            if pending != nil && !attempts.canAttempt(at: ProcessInfo.processInfo.systemUptime) {
                throw DevBridgeError.response("尝试次数过多，请稍后重试。")
            }
            try await authorize(configuration)
            current = DeveloperAuthRecord(secret: secret, lastCounter: counter)
            try store.save(current!)
            pending = nil; setupURI = nil; bound = true
            authorizationExpired = false
            session.unlock()
        } catch { showAuthorizationError(error) }
    }

    /// Store only a random local token after a full TOTP + official authority
    /// verification plus macOS local authentication. No password is saved.
    func trustThisMac(_ configuration: DeveloperConfiguration) async {
        guard !busy, bound, session.unlocked else { return }
        busy = true; error = nil
        defer { busy = false }
        do {
            try await verifyRecovery()
            try await authorize(configuration)
            try trustStore.save(DeveloperAuthRecord(secret: DeveloperAuthKeychain.generateSecret()))
            deviceTrusted = true
        } catch { self.error = error.localizedDescription }
    }

    func unlockWithDeviceTrust(_ configuration: DeveloperConfiguration) async {
        guard !busy, deviceTrusted else { return }
        busy = true; error = nil
        defer { busy = false }
        do {
            let now = ProcessInfo.processInfo.systemUptime
            if deviceVerificationDeadline.map({ now >= $0 }) ?? true {
                deviceVerificationDeadline = nil
                try await verifyRecovery()
                deviceVerificationDeadline = ProcessInfo.processInfo.systemUptime + 120
            }
            try await authorize(configuration)
            deviceVerificationDeadline = nil
            authorizationExpired = false
            session.unlock()
        } catch {
            showAuthorizationError(error)
            if !authorizationExpired { deviceVerificationDeadline = nil }
        }
    }

    func revokeTrust(_ configuration: DeveloperConfiguration) async {
        guard !busy else { return }
        busy = true; error = nil
        defer { busy = false }
        do {
            try await verifyRecovery()
            try await authorize(configuration)
            try trustStore.remove()
            deviceTrusted = false
        } catch { self.error = error.localizedDescription }
    }

    func lock() { deviceVerificationDeadline = nil; authorizationExpired = false; session.lock(); cancelBinding() }
    func tick() { session.tick() }

    func revoke(_ configuration: DeveloperConfiguration) async {
        guard !busy else { return }
        busy = true; error = nil
        defer { busy = false }
        do {
            try await verifyRecovery()
            try await authorize(configuration)
            try store.remove()
            try? trustStore.remove()
            bound = false; deviceTrusted = false; lock()
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
