import Foundation

// Signed registry, signed per-plugin manifest and asset download. No plugin id
// or file list is known here: the bootstrap registry is the only fixed address.
enum PluginUpdates {
    private static let lock = NSLock()
    private static var memoryCache: [String: (fetched: Date, snapshot: PluginProtocol.RegistrySnapshot)] = [:]
    private static let cacheLifetime: TimeInterval = 7 * 24 * 60 * 60
    private static let memoryLifetime: TimeInterval = 300

    private struct DiskCache: Codable {
        let fetchedAt: TimeInterval
        let registry: String
        let envelope: String
    }

    static func registry(forceRefresh: Bool = false,
                         allowOfflineCache: Bool = true,
                         environment: PluginEnvironment = .current) throws -> PluginProtocol.RegistrySnapshot {
        lock.lock()
        defer { lock.unlock() }
        let key = environment.registryURL.absoluteString
        if !forceRefresh, let cached = memoryCache[key],
           Date().timeIntervalSince(cached.fetched) < memoryLifetime {
            return cached.snapshot
        }
        let data: Data
        do {
            data = try download(environment.registryURL, maxBytes: PluginProtocol.maximumRegistry,
                                disableProxy: environment.disableProxy)
        } catch {
            // A network failure may reuse a previously *verified* cache. A
            // signature failure below never falls back to stale content.
            if allowOfflineCache, let cached = diskCache(environment) {
                memoryCache[key] = (Date(), cached)
                return cached
            }
            // Preserve the concrete transport cause (HTTP status, timeout,
            // DNS/proxy error) instead of collapsing it into a generic line.
            throw error
        }
        let entries = try PluginProtocol.verifiedRegistrySnapshot(
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
        let limit = release.descriptor.isBundle ? (release.descriptor.capabilities.contains(.macOSApplication) ? PluginProtocol.maximumNativeBundle : PluginProtocol.maximumBundle)
                                                : PluginProtocol.maximumScript
        let data = try download(url, maxBytes: limit, disableProxy: environment.disableProxy)
        return try PluginProtocol.unpack(data, release: release)
    }

    private static func diskCache(_ environment: PluginEnvironment) -> PluginProtocol.RegistrySnapshot? {
        guard let raw = try? Data(contentsOf: environment.cacheFile),
              let record = try? JSONDecoder().decode(DiskCache.self, from: raw),
              record.registry == environment.registryURL.absoluteString,
              Date().timeIntervalSince1970 - record.fetchedAt < cacheLifetime,
              let envelope = Data(base64Encoded: record.envelope),
              let entries = try? PluginProtocol.verifiedRegistrySnapshot(
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
        let url: URL
        let done = DispatchSemaphore(value: 0)
        var bytes = Data()
        var accepted = false
        var failure: Error?
        var lastProgress = Date()
        init(limit: Int, url: URL) { self.limit = limit; self.url = url }
        private func fail(_ reason: String) -> PluginProtocol.Failure {
            .detail("无法获取 \(url.host ?? url.absoluteString) 的资源：\(reason)")
        }
        func urlSession(_ session: URLSession, dataTask: URLSessionDataTask,
                        didReceive response: URLResponse,
                        completionHandler: @escaping (URLSession.ResponseDisposition) -> Void) {
            guard let http = response as? HTTPURLResponse else {
                failure = fail("响应不是 HTTP。")
                completionHandler(.cancel)
                return
            }
            guard (200...299).contains(http.statusCode) else {
                failure = fail("HTTP \(http.statusCode)。")
                completionHandler(.cancel)
                return
            }
            guard response.expectedContentLength <= Int64(limit) else {
                failure = fail("内容长度 \(response.expectedContentLength) 超过 \(limit) 字节上限。")
                completionHandler(.cancel)
                return
            }
            accepted = true
            completionHandler(.allow)
        }
        func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
            guard data.count <= limit - bytes.count else {
                failure = fail("下载内容超过 \(limit) 字节上限。")
                dataTask.cancel()
                return
            }
            bytes.append(data)
            lastProgress = Date()
        }
        func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
            if let error {
                // A deliberate cancel after an HTTP/size rejection must not
                // replace the specific cause with NSURLErrorCancelled.
                if failure == nil || (error as NSError).code != NSURLErrorCancelled {
                    let ns = error as NSError
                    failure = fail("\(ns.domain) \(ns.code)：\(error.localizedDescription)")
                }
            }
            done.signal()
        }
    }

    static func download(_ url: URL, maxBytes: Int, disableProxy: Bool = false) throws -> Data {
        let delegate = BoundedDownload(limit: maxBytes, url: url)
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 8
        // A signed release bundle can be tens of megabytes behind a proxy.
        // A fixed whole-resource deadline makes steady transfers fail with
        // NSURLError -1001; progress is monitored below instead.
        configuration.timeoutIntervalForResource = 0
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        if disableProxy { configuration.connectionProxyDictionary = [:] }
        let queue = OperationQueue()
        queue.maxConcurrentOperationCount = 1
        let session = URLSession(configuration: configuration, delegate: delegate, delegateQueue: queue)
        let task = session.dataTask(with: url)
        task.resume()
        let started = Date()
        var stalled = false
        while delegate.done.wait(timeout: .now() + 1) != .success {
            let idle = Date().timeIntervalSince(delegate.lastProgress)
            if idle > 8 || Date().timeIntervalSince(started) > 180 {
                stalled = true
                break
            }
        }
        if stalled {
            task.cancel()
            session.invalidateAndCancel()
            throw PluginProtocol.Failure.detail(
                "请求 \(url.host ?? url.absoluteString) 连续 8 秒无响应，已中止下载。")
        }
        session.finishTasksAndInvalidate()
        if let failure = delegate.failure { throw failure }
        guard delegate.accepted else {
            throw PluginProtocol.Failure.detail("请求 \(url.absoluteString) 未获得有效响应。")
        }
        return delegate.bytes
    }
}
