import Foundation
import CryptoKit

// Only authenticated manifests can authorize executable plugin downloads.
enum PluginUpdates {
    struct Entry: Decodable, Equatable, Sendable {
        let version: String
        let url: String
        let sha256: String
        let kind: String?

        var isBundle: Bool { kind == "bundle" }
        var isScript: Bool { kind == nil || kind == "script" }
    }
    // One signed registry tells the client where each plugin's feed lives and
    // which download paths that feed is allowed to use. Plugin discovery and
    // installation remain limited to types supported by this app version.
    struct RegistryEntry: Decodable, Sendable {
        let repository: String
        let feed: String
        let downloadPrefix: String
        let kind: String?
    }
    private struct RegistryPayload: Decodable {
        let format: Int
        let plugins: [String: RegistryEntry]
    }
    private struct Payload: Decodable { let format: Int; let plugins: [String: Entry] }
    private struct Envelope: Decodable { let payload: String; let signature: String }
    private struct Container: Decodable {
        let format: Int
        let id: String
        let version: String
        let files: [String: String]
    }
    enum Failure: LocalizedError {
        case invalid, unavailable, notPublished
        var errorDescription: String? {
            switch self {
            case .invalid: return "插件更新校验失败。"
            case .unavailable: return "无法检查插件更新。"
            case .notPublished: return "暂无在线版本。"
            }
        }
    }

    // The single bootstrap address. Everything below it is discovered from the
    // signed registry, so no per-plugin feed URL is hardcoded in the app.
    static let registryURL = URL(string: "https://brokenag.github.io/ordina-releases/registry.json")!
    static let publicKey = Data(base64Encoded: "90vua+GDSP8MSl6owCI/WSIkqBBuXO/AGw0ny4p67Bw=")!
    static let bundleLimit = 4_000_000
    static let bundleRequired = [
        "plugin.json", "Minutes Roughcut.py", "host.py", "host.command",
        "extension/manifest.json",
    ]

    private static let registryLock = NSLock()
    private static var registryCache: (fetched: Date, entries: [String: RegistryEntry])?

    private static func validPluginID(_ value: String) -> Bool {
        value.range(of: #"^[a-z][a-z0-9-]{0,31}$"#, options: .regularExpression) != nil
    }

    // The registry is signed, but still constrain the shape of what it may
    // point at: our Pages host, our GitHub releases, HTTPS only.
    private static func validFeed(_ value: String) -> Bool {
        guard let url = URL(string: value), url.scheme == "https",
              url.host == "brokenag.github.io", url.user == nil, url.password == nil,
              url.port == nil, url.query == nil, url.fragment == nil,
              url.path.hasPrefix("/ordina-plugin-"),
              (url.path.hasSuffix("/manifest.json") || url.path.hasSuffix("/manifest-v2.json")) else { return false }
        return true
    }

    private static func validDownloadPrefix(_ value: String) -> Bool {
        value.hasPrefix("/BrOKenAG/") && value.hasSuffix("/releases/download/")
            && !value.contains("..") && !value.contains("//")
    }

    static func verifiedRegistry(_ data: Data, publicKey: Data = publicKey) throws -> [String: RegistryEntry] {
        let envelope = try JSONDecoder().decode(Envelope.self, from: data)
        guard let payload = Data(base64Encoded: envelope.payload), payload.count <= 65536,
              let signature = Data(base64Encoded: envelope.signature),
              let key = try? Curve25519.Signing.PublicKey(rawRepresentation: publicKey),
              key.isValidSignature(signature, for: payload) else { throw Failure.invalid }
        let document = try JSONDecoder().decode(RegistryPayload.self, from: payload)
        guard document.format == 2 else { throw Failure.invalid }
        var result: [String: RegistryEntry] = [:]
        for (name, descriptor) in document.plugins {
            guard validPluginID(name), validFeed(descriptor.feed),
                  validDownloadPrefix(descriptor.downloadPrefix),
                  descriptor.repository.hasPrefix("BrOKenAG/ordina-plugin-"),
                  descriptor.kind == nil || descriptor.kind == "bundle" || descriptor.kind == "script"
            else { throw Failure.invalid }
            result[name] = descriptor
        }
        return result
    }

    // A failed signature is never downgraded to a cached or built-in value.
    // Only a network failure may reuse a recently verified registry.
    static func registryEntries(forceRefresh: Bool = false) throws -> [String: RegistryEntry] {
        registryLock.lock()
        defer { registryLock.unlock() }
        if !forceRefresh, let cache = registryCache,
           Date().timeIntervalSince(cache.fetched) < 300 { return cache.entries }
        let (entries, refreshed) = try resolveRegistry(cached: registryCache?.entries,
            downloadData: { try download(registryURL, maxBytes: 100_000) },
            verifyData: { try verifiedRegistry($0) })
        if refreshed { registryCache = (Date(), entries) }
        return entries
    }

    // Signature errors must never select a stale cache. Network failures may
    // use an already verified cache; callers can still install bundled files.
    static func resolveRegistry(cached: [String: RegistryEntry]?,
                                downloadData: () throws -> Data,
                                verifyData: (Data) throws -> [String: RegistryEntry]) throws -> ([String: RegistryEntry], Bool) {
        let data: Data
        do { data = try downloadData() }
        catch {
            if let cached { return (cached, false) }
            throw Failure.unavailable
        }
        return (try verifyData(data), true)
    }

    static func verifiedEntry(_ data: Data, plugin: OptionalPlugin,
                              publicKey: Data = publicKey,
                              allowedPrefix: String) throws -> Entry {
        let envelope = try JSONDecoder().decode(Envelope.self, from: data)
        guard let payload = Data(base64Encoded: envelope.payload), payload.count <= 65536,
              let signature = Data(base64Encoded: envelope.signature),
              let key = try? Curve25519.Signing.PublicKey(rawRepresentation: publicKey),
              key.isValidSignature(signature, for: payload) else { throw Failure.invalid }
        let document = try JSONDecoder().decode(Payload.self, from: payload)
        guard document.format == 1 else { throw Failure.invalid }
        guard let entry = document.plugins[plugin.id] else { throw Failure.notPublished }
        if let kind = entry.kind, kind != "bundle", kind != "script" { throw Failure.invalid }
        guard validVersion(entry.version), entry.sha256.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil,
              let url = URL(string: entry.url), url.scheme == "https", url.host == "github.com",
              url.user == nil, url.password == nil, url.port == nil, url.query == nil, url.fragment == nil,
              validDownloadPrefix(allowedPrefix), url.path.hasPrefix(allowedPrefix) else { throw Failure.invalid }
        if entry.isBundle {
            guard url.pathExtension == "json" else { throw Failure.invalid }
        } else {
            guard url.pathExtension == "py" else { throw Failure.invalid }
        }
        return entry
    }
    static func validVersion(_ value: String) -> Bool {
        value.range(of: "^[0-9]+(\\.[0-9]+){1,3}$", options: .regularExpression) != nil
    }
    static func newer(_ candidate: String, than installed: String) -> Bool {
        guard validVersion(candidate), validVersion(installed) else { return false }
        return candidate.compare(installed, options: .numeric) == .orderedDescending
    }
    static func version(_ data: Data) -> String? {
        guard let text = String(data: data, encoding: .utf8),
              let regex = try? NSRegularExpression(pattern: #"(?m)^PLUGIN_VERSION\s*=\s*["']v?([0-9]+(?:\.[0-9]+)+)["']"#),
              let match = regex.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)),
              let range = Range(match.range(at: 1), in: text) else { return nil }
        return String(text[range])
    }
    private static func digest(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
    static func verifyScript(_ data: Data, entry: Entry) throws {
        guard entry.isScript, !data.isEmpty, data.count <= 2_000_000,
              digest(data) == entry.sha256,
              version(data) == entry.version else { throw Failure.invalid }
    }

    // A bundle is a signed JSON container. Every path is checked before any
    // file is written, and the installer itself always comes from the app.
    static func verifyBundle(_ data: Data, entry: Entry, plugin: OptionalPlugin) throws -> [String: Data] {
        guard entry.isBundle, !data.isEmpty, data.count <= bundleLimit,
              digest(data) == entry.sha256 else { throw Failure.invalid }
        guard let container = try? JSONDecoder().decode(Container.self, from: data),
              container.format == 1, container.id == plugin.id,
              container.version == entry.version, validVersion(entry.version),
              container.files.count <= 32 else { throw Failure.invalid }
        var result: [String: Data] = [:]
        var total = 0
        for (name, encoded) in container.files {
            guard validBundlePath(name), let file = Data(base64Encoded: encoded),
                  !file.isEmpty, file.count <= 1_000_000 else { throw Failure.invalid }
            total += file.count
            guard total <= bundleLimit else { throw Failure.invalid }
            result[name] = file
        }
        for required in bundleRequired where result[required] == nil { throw Failure.invalid }
        guard let meta = result["plugin.json"],
              let object = try? JSONSerialization.jsonObject(with: meta) as? [String: Any],
              object["version"] as? String == entry.version,
              object["id"] as? String == plugin.id else { throw Failure.invalid }
        return result
    }

    static func validBundlePath(_ name: String) -> Bool {
        guard !name.isEmpty, name.utf8.count <= 200, !name.hasPrefix("/"),
              !name.contains("\\"), !name.contains(":"), !name.contains("\0") else { return false }
        let parts = name.split(separator: "/", omittingEmptySubsequences: false)
        guard parts.count <= 4, parts.allSatisfy({ !$0.isEmpty && !$0.hasPrefix(".") && $0 != ".." }) else { return false }
        guard parts.allSatisfy({ $0.range(of: #"^[A-Za-z0-9 _.\-]+$"#, options: .regularExpression) != nil }) else { return false }
        if parts.count == 1 {
            return ["plugin.json", "Minutes Roughcut.py", "host.py", "host.command", "install.py"].contains(String(parts[0]))
        }
        return parts[0] == "extension"
    }

    static func bundle(_ entry: Entry, plugin: OptionalPlugin) throws -> URL {
        guard let url = URL(string: entry.url) else { throw Failure.invalid }
        let files = try verifyBundle(download(url, maxBytes: bundleLimit), entry: entry, plugin: plugin)
        let manager = FileManager.default
        let root = manager.temporaryDirectory.appendingPathComponent("ordina-bundle-" + UUID().uuidString)
        do {
            try manager.createDirectory(at: root, withIntermediateDirectories: true)
            for (name, file) in files {
                var target = root
                for part in name.split(separator: "/") {
                    target.appendPathComponent(String(part))
                    if (try? manager.destinationOfSymbolicLink(atPath: target.path)) != nil { throw Failure.invalid }
                }
                try manager.createDirectory(at: target.deletingLastPathComponent(), withIntermediateDirectories: true)
                try file.write(to: target, options: .atomic)
            }
        } catch {
            try? manager.removeItem(at: root)
            throw error
        }
        return root
    }

    private final class BoundedDownload: NSObject, URLSessionDataDelegate, @unchecked Sendable {
        let limit: Int
        let done = DispatchSemaphore(value: 0)
        var bytes = Data()
        var accepted = false
        var failure: Error?
        init(limit: Int) { self.limit = limit }
        func urlSession(_ session: URLSession, dataTask: URLSessionDataTask,
                        didReceive response: URLResponse,
                        completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
            guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode),
                  response.expectedContentLength <= Int64(limit) else {
                failure = Failure.unavailable; completionHandler(.cancel); return
            }
            accepted = true
            completionHandler(.allow)
        }
        func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
            guard data.count <= limit - bytes.count else {
                failure = Failure.unavailable; dataTask.cancel(); return
            }
            bytes.append(data)
        }
        func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
            if let error { failure = error }
            done.signal()
        }
    }
    static func download(_ url: URL, maxBytes: Int) throws -> Data {
        let delegate = BoundedDownload(limit: maxBytes)
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 8
        configuration.timeoutIntervalForResource = 12
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        let queue = OperationQueue()
        queue.maxConcurrentOperationCount = 1
        let session = URLSession(configuration: configuration, delegate: delegate, delegateQueue: queue)
        let task = session.dataTask(with: url)
        task.resume()
        guard delegate.done.wait(timeout: .now() + 13) == .success else {
            task.cancel(); session.invalidateAndCancel(); throw Failure.unavailable
        }
        session.finishTasksAndInvalidate()
        guard delegate.accepted, delegate.failure == nil else { throw Failure.unavailable }
        return delegate.bytes
    }
    static func fetch(_ plugin: OptionalPlugin) throws -> Entry {
        let descriptors = try registryEntries()
        guard let descriptor = descriptors[plugin.id],
              let url = URL(string: descriptor.feed) else { throw Failure.notPublished }
        return try verifiedEntry(download(url, maxBytes: 100_000), plugin: plugin,
                                 allowedPrefix: descriptor.downloadPrefix)
    }
    static func script(_ entry: Entry) throws -> Data {
        guard let url = URL(string: entry.url) else { throw Failure.invalid }
        let data = try download(url, maxBytes: 2_000_000)
        try verifyScript(data, entry: entry)
        return data
    }
}
