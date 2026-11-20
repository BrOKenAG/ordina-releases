import Foundation

enum ProjectRawLocations {
    enum Failure: LocalizedError {
        case directory(String)
        case policy

        var errorDescription: String? {
            switch self {
            case .directory(let path): return "请选择可访问的具体原片目录：\(path)"
            case .policy: return "无法读取已有素材位置，请检查项目目录后重试。"
            }
        }
    }

    static func paths(_ folders: [URL], projectRoot: URL) throws -> [String] {
        let root = projectRoot.resolvingSymlinksInPath().standardizedFileURL
        let excluded = Set(["/", "/Users", "/home", "/Volumes", "/tmp", "/private/tmp",
                            FileManager.default.homeDirectoryForCurrentUser.path])
        var paths: [String] = []
        for folder in folders {
            let url = folder.resolvingSymlinksInPath().standardizedFileURL
            guard !excluded.contains(url.path),
                  !root.pathComponents.starts(with: url.pathComponents),
                  let info = try? url.resourceValues(forKeys: [.isDirectoryKey, .volumeURLKey]),
                  info.isDirectory == true,
                  FileManager.default.isReadableFile(atPath: url.path) else {
                throw Failure.directory(url.path)
            }
            if url.pathComponents.count > 2, url.pathComponents[1] == "Volumes" {
                let mount = URL(fileURLWithPath: "/Volumes").appendingPathComponent(url.pathComponents[2])
                guard info.volume?.standardizedFileURL == mount.standardizedFileURL else {
                    throw Failure.directory(url.path)
                }
            }
            if !paths.contains(url.path) { paths.append(url.path) }
        }
        return paths
    }

    static func save(_ paths: [String], in root: URL) throws {
        guard !paths.isEmpty else { return }
        let file = root.appendingPathComponent(".ordo-storage.json")
        guard (try? FileManager.default.destinationOfSymbolicLink(atPath: file.path)) == nil else {
            throw Failure.policy
        }
        var policy: [String: Any] = ["version": 1, "locations": [], "decisions": [:]]
        if FileManager.default.fileExists(atPath: file.path) {
            guard let existing = try JSONSerialization.jsonObject(with: Data(contentsOf: file)) as? [String: Any],
                  existing["version"] as? Int == 1 else { throw Failure.policy }
            policy = existing
        }
        guard var locations = policy["locations"] as? [[String: Any]] else { throw Failure.policy }
        for path in paths {
            if let index = locations.firstIndex(where: { $0["path"] as? String == path }) {
                locations[index]["kind"] = "raw"
            } else {
                let id = UUID().uuidString
                locations.append(["id": id, "kind": "raw", "logicalID": id, "path": path])
            }
        }
        policy["locations"] = locations
        try JSONSerialization.data(withJSONObject: policy, options: [.sortedKeys])
            .write(to: file, options: .atomic)
    }
}
