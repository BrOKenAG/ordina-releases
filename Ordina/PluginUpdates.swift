import Foundation
import CryptoKit

// Only authenticated manifests can authorize executable plugin downloads.
enum PluginUpdates {
    struct Entry: Decodable {
        let version: String
        let url: String
        let sha256: String
    }
    private struct Payload: Decodable { let format: Int; let plugins: [String: Entry] }
    private struct Envelope: Decodable { let payload: String; let signature: String }
    enum Failure: LocalizedError {
        case invalid, unavailable
        var errorDescription: String? {
            switch self {
            case .invalid: return "插件更新校验失败。"
            case .unavailable: return "无法检查插件更新。"
            }
        }
    }
    static let feedURL = URL(string: "https://brokenag.github.io/ordina-releases/plugins/manifest.json")!
    static let publicKey = Data(base64Encoded: "90vua+GDSP8MSl6owCI/WSIkqBBuXO/AGw0ny4p67Bw=")!

    static func verifiedEntry(_ data: Data, plugin: OptionalPlugin, publicKey: Data = publicKey) throws -> Entry {
        let envelope = try JSONDecoder().decode(Envelope.self, from: data)
        guard let payload = Data(base64Encoded: envelope.payload), payload.count <= 65536,
              let signature = Data(base64Encoded: envelope.signature),
              let key = try? Curve25519.Signing.PublicKey(rawRepresentation: publicKey),
              key.isValidSignature(signature, for: payload) else { throw Failure.invalid }
        let document = try JSONDecoder().decode(Payload.self, from: payload)
        guard document.format == 1, let entry = document.plugins[plugin.id],
              validVersion(entry.version), entry.sha256.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil,
              let url = URL(string: entry.url), url.scheme == "https", url.host == "github.com",
              url.user == nil, url.password == nil, url.port == nil, url.query == nil, url.fragment == nil,
              url.path.hasPrefix("/BrOKenAG/ordina-releases/releases/download/") else { throw Failure.invalid }
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
    static func verifyScript(_ data: Data, entry: Entry) throws {
        guard !data.isEmpty, data.count <= 2_000_000,
              SHA256.hash(data: data).map({ String(format: "%02x", $0) }).joined() == entry.sha256,
              version(data) == entry.version else { throw Failure.invalid }
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
        try verifiedEntry(download(feedURL, maxBytes: 100_000), plugin: plugin)
    }
    static func script(_ entry: Entry) throws -> Data {
        guard let url = URL(string: entry.url) else { throw Failure.invalid }
        let data = try download(url, maxBytes: 2_000_000)
        try verifyScript(data, entry: entry)
        return data
    }
}
