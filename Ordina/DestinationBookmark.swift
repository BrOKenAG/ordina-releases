import Foundation

enum DestinationBookmark {
    static func validate(_ url: URL) throws {
        let accessed = url.startAccessingSecurityScopedResource()
        defer { if accessed { url.stopAccessingSecurityScopedResource() } }
        let values = try url.resourceValues(forKeys: [.isDirectoryKey, .volumeURLKey])
        guard values.isDirectory == true, FileManager.default.isReadableFile(atPath: url.path),
              FileManager.default.isWritableFile(atPath: url.path) else {
            throw ResolveFailure(message: "项目目录不可访问或不可写，请检查连接与权限。")
        }
        let components = url.standardizedFileURL.pathComponents
        if components.count > 2, components[1] == "Volumes" {
            let expected = URL(fileURLWithPath: "/Volumes").appendingPathComponent(components[2])
            guard values.volume?.standardizedFileURL == expected.standardizedFileURL else {
                throw ResolveFailure(message: "项目磁盘未挂载，请连接后重试。")
            }
        }
    }
    static func load(key: String = "destinationFolderBookmark", from defaults: UserDefaults = .standard) -> URL? {
        guard let data = defaults.data(forKey: key) else { return nil }

        do {
            var isStale = false
            let url = try URL(
                resolvingBookmarkData: data,
                options: [.withSecurityScope, .withoutUI, .withoutMounting],
                relativeTo: nil,
                bookmarkDataIsStale: &isStale
            )
            if isStale {
                try? save(url, key: key, to: defaults)
            }
            return url
        } catch {
            // Keep the bookmark so an unavailable volume can be restored later.
            return nil
        }
    }

    static func save(_ url: URL, key: String = "destinationFolderBookmark", to defaults: UserDefaults = .standard) throws {
        let accessing = url.startAccessingSecurityScopedResource()
        defer {
            if accessing { url.stopAccessingSecurityScopedResource() }
        }

        let data = try url.bookmarkData(
            options: .withSecurityScope,
            includingResourceValuesForKeys: nil,
            relativeTo: nil
        )
        defaults.set(data, forKey: key)
    }
}
