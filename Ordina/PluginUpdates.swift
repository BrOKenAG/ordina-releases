import Foundation

// Signed registry, signed per-plugin manifest and asset download. No plugin id
// or file list is known here: the bootstrap registry is the only fixed address.
enum PluginUpdates {
    private static let lock = NSLock()
    private static var memoryCache: [String: (fetched: Date, entries: [String: PluginRegistryEntry])] = [:]
    private static let cacheLifetime: TimeInterval = 7 * 24 * 60 * 60
    private static let memoryLifetime: TimeInterval = 300

    private struct DiskCache: Codable {
        let fetchedAt: TimeInterval
        let registry: String
        let envelope: String
    }

    static func registry(forceRefresh: Bool = false,
                         environment: PluginEnvironment = .current) throws -> [String: PluginRegistryEntry] {
        lock.lock()
        defer { lock.unlock() }
        let key = environment.registryURL.absoluteString
        if !forceRefresh, let cached = memoryCache[key],
           Date().timeIntervalSince(cached.fetched) < memoryLifetime {
            return cached.entries
        }
        let data: Data
        do {
            data = try download(environment.registryURL, maxBytes: PluginProtocol.maximumRegistry,
                                disableProxy: environment.disableProxy)
        } catch {
            // A network failure may reuse a previously *verified* cache. A
            // signature failure below never falls back to stale content.
            if let cached = diskCache(environment), cached.count > 0 {
                memoryCache[key] = (Date(), cached)
                return cached
            }
            throw PluginProtocol.Failure.unavailable
        }
        let entries = try PluginProtocol.verifiedRegistry(
            data, publicKey: environment.publicKey, policy: environment.policy)
        memoryCache[key] = (Date(), entries)
        storeDiskCache(data, environment: environment)
        return entries
    }

    static func release(id: String, entry: PluginRegistryEntry,
                        environment: PluginEnvironment = .current) throws -> PluginRelease {
        guard let url = URL(string: entry.feed) else { throw PluginProtocol.Failure.invalid }
        let data = try download(url, maxBytes: PluginProtocol.maximumManifest,
                                disableProxy: environment.disableProxy)
        return try PluginProtocol.signedRelease(
            data, pluginID: id,
            publicKey: environment.publicKey, policy: environment.policy)
    }

    // Download and fully verify a release asset. Returns the exact files the
    // signed inventory authorizes; nothing else is ever written.
    static func files(for release: PluginRelease,
                      environment: PluginEnvironment = .current) throws -> [String: Data] {
        guard let url = URL(string: release.url) else { throw PluginProtocol.Failure.invalid }
        let limit = release.descriptor.isBundle ? PluginProtocol.maximumBundle
                                                : PluginProtocol.maximumScript
        let data = try download(url, maxBytes: limit, disableProxy: environment.disableProxy)
        return try PluginProtocol.unpack(data, release: release)
    }

    private static func diskCache(_ environment: PluginEnvironment) -> [String: PluginRegistryEntry]? {
        guard let raw = try? Data(contentsOf: environment.cacheFile),
              let record = try? JSONDecoder().decode(DiskCache.self, from: raw),
              record.registry == environment.registryURL.absoluteString,
              Date().timeIntervalSince1970 - record.fetchedAt < cacheLifetime,
              let envelope = Data(base64Encoded: record.envelope),
              let entries = try? PluginProtocol.verifiedRegistry(
                envelope, publicKey: environment.publicKey, policy: environment.policy)
        else { return nil }
        return entries
    }

    private static func storeDiskCache(_ data: Data, environment: PluginEnvironment) {
        let record = DiskCache(fetchedAt: Date().timeIntervalSince1970,
                               registry: environment.registryURL.absoluteString,
                               envelope: data.base64EncodedString())
        guard let encoded = try? JSONEncoder().encode(record) else { return }
        try? FileManager.default.createDirectory(at: environment.supportRoot,
                                                 withIntermediateDirectories: true)
        try? encoded.write(to: environment.cacheFile, options: .atomic)
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
                failure = PluginProtocol.Failure.unavailable
                completionHandler(.cancel)
                return
            }
            accepted = true
            completionHandler(.allow)
        }
        func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
            guard data.count <= limit - bytes.count else {
                failure = PluginProtocol.Failure.unavailable
                dataTask.cancel()
                return
            }
            bytes.append(data)
        }
        func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
            if let error { failure = error }
            done.signal()
        }
    }

    static func download(_ url: URL, maxBytes: Int, disableProxy: Bool = false) throws -> Data {
        let delegate = BoundedDownload(limit: maxBytes)
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 8
        configuration.timeoutIntervalForResource = 12
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        if disableProxy { configuration.connectionProxyDictionary = [:] }
        let queue = OperationQueue()
        queue.maxConcurrentOperationCount = 1
        let session = URLSession(configuration: configuration, delegate: delegate, delegateQueue: queue)
        let task = session.dataTask(with: url)
        task.resume()
        guard delegate.done.wait(timeout: .now() + 13) == .success else {
            task.cancel()
            session.invalidateAndCancel()
            throw PluginProtocol.Failure.unavailable
        }
        session.finishTasksAndInvalidate()
        guard delegate.accepted, delegate.failure == nil else {
            throw PluginProtocol.Failure.unavailable
        }
        return delegate.bytes
    }
}
