import Foundation

// Discovery is entirely registry-driven. Nothing here enumerates plugin ids;
// a plugin the compiled client has never heard of appears as soon as the
// signed registry lists it.
enum PluginCatalog {
    struct Item: Identifiable, Sendable {
        let id: String
        let name: String
        let summary: String
        let symbol: String
        let version: String
        let minClientVersion: String
        let capabilities: [PluginCapability]
        let descriptor: PluginDescriptor?
        let release: PluginRelease?
        let status: PluginInstallStatus
        let error: String?
        var isWithdrawn = false

        var isInstalled: Bool { status.isInstalled }
        var canInstall: Bool { !isWithdrawn && release != nil && descriptor != nil }
        var needsBrowserExtension: Bool {
            capabilities.contains(.chromeUnpackedExtension)
        }
    }

    struct LoadResult: Sendable {
        let items: [Item]
        let registryError: String?
    }

    static func load(forceRefresh: Bool = false,
                     environment: PluginEnvironment = .current) -> LoadResult {
        var entries: [String: PluginRegistryEntry] = [:]
        var withdrawn: Set<String> = []
        var registryError: String?
        do {
            let snapshot = try PluginUpdates.registry(forceRefresh: forceRefresh, environment: environment)
            entries = snapshot.plugins
            withdrawn = snapshot.withdrawn
        } catch {
            registryError = "无法获取插件目录：\(error.localizedDescription)"
        }
        var items: [String: Item] = [:]
        // Installed records remain visible even when the registry is offline.
        items = installedItems(environment: environment)
        for id in withdrawn where items[id] != nil {
            items[id]?.isWithdrawn = true
        }
        for (id, entry) in entries {
            let installed = items[id]
            items[id] = resolve(id: id, entry: entry, installed: installed,
                                environment: environment)
        }
        return LoadResult(items: items.values.sorted {
            if $0.isInstalled != $1.isInstalled { return $0.isInstalled }
            return $0.name.localizedStandardCompare($1.name) == .orderedAscending
        }, registryError: registryError)
    }

    static func resolve(id: String, entry: PluginRegistryEntry, installed: Item?,
                        environment: PluginEnvironment = .current) -> Item {
        do {
            let release = try PluginUpdates.release(id: id, entry: entry,
                                                    environment: environment)
            let status = PluginInstaller.inspect(release.descriptor, environment: environment)
            return Item(id: id, name: release.descriptor.name, summary: release.descriptor.summary,
                        symbol: release.descriptor.symbol, version: release.version,
                        minClientVersion: release.descriptor.minClientVersion,
                        capabilities: release.descriptor.capabilities,
                        descriptor: release.descriptor, release: release,
                        status: status, error: nil)
        } catch {
            let descriptor = installed?.descriptor
            return Item(id: id,
                        name: entry.name ?? descriptor?.name ?? id,
                        summary: entry.summary ?? descriptor?.summary ?? "",
                        symbol: entry.symbol ?? descriptor?.symbol ?? "puzzlepiece.extension",
                        version: entry.version ?? descriptor?.version ?? "",
                        minClientVersion: entry.minClientVersion ?? descriptor?.minClientVersion ?? "",
                        capabilities: descriptor?.capabilities
                            ?? (entry.capabilities ?? []).compactMap(PluginCapability.init(rawValue:)),
                        descriptor: descriptor,
                        release: nil,
                        status: installed?.status ?? .notInstalled,
                        error: "无法读取插件清单：\(error.localizedDescription)")
        }
    }

    private static func installedItems(environment: PluginEnvironment) -> [String: Item] {
        var result: [String: Item] = [:]
        guard let directories = try? FileManager.default.contentsOfDirectory(
            at: environment.supportRoot, includingPropertiesForKeys: nil) else { return result }
        for directory in directories {
            let id = directory.lastPathComponent
            guard let record = PluginInstaller.state(id, environment: environment) else { continue }
            let status = PluginInstaller.inspect(record.descriptor, environment: environment)
            result[id] = Item(id: id, name: record.descriptor.name,
                              summary: record.descriptor.summary,
                              symbol: record.descriptor.symbol,
                              version: record.version,
                              minClientVersion: record.descriptor.minClientVersion,
                              capabilities: record.descriptor.capabilities,
                              descriptor: record.descriptor, release: nil,
                              status: status, error: nil)
        }
        return result
    }
}

// One place that performs discovery -> verified download -> install -> state
// refresh. Both the SwiftUI view and the standalone harness call this.
enum PluginOperations {
    static func load(forceRefresh: Bool = false,
                     environment: PluginEnvironment = .current) -> PluginCatalog.LoadResult {
        PluginCatalog.load(forceRefresh: forceRefresh, environment: environment)
    }

    static func install(_ item: PluginCatalog.Item,
                        environment: PluginEnvironment = .current) throws {
        guard let release = item.release else {
            throw PluginProtocol.Failure.notPublished
        }
        let current = try PluginUpdates.registry(forceRefresh: true, allowOfflineCache: false,
                                                 environment: environment)
        guard let entry = current.plugins[item.id], !current.withdrawn.contains(item.id) else {
            throw PluginProtocol.Failure.notPublished
        }
        let verified = try PluginUpdates.release(id: item.id, entry: entry, environment: environment)
        guard verified == release else { throw PluginProtocol.Failure.unavailable }
        let files = try PluginUpdates.files(for: release, environment: environment)
        try PluginInstaller.install(release, files: files, environment: environment)
    }

    static func uninstall(_ item: PluginCatalog.Item,
                          environment: PluginEnvironment = .current) throws {
        guard let descriptor = item.descriptor else {
            throw PluginProtocol.Failure.notPublished
        }
        try PluginInstaller.uninstall(descriptor, environment: environment)
    }
}
