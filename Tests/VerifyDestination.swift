import Foundation

@main
struct VerifyDestination {
    static func main() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        try DestinationBookmark.validate(root)
        let missing = root.appendingPathComponent("offline-root")
        do {
            try DestinationBookmark.validate(missing)
            fatalError("Missing project roots must not be accepted")
        } catch {}
        precondition(!FileManager.default.fileExists(atPath: missing.path))
        if CommandLine.arguments.count > 1 {
            try DestinationBookmark.validate(URL(fileURLWithPath: CommandLine.arguments[1]))
        }
        let plain = root.appendingPathComponent("plain.txt")
        try Data("data".utf8).write(to: plain)
        do {
            try DestinationBookmark.validate(plain)
            fatalError("Files must not be accepted as project roots")
        } catch {}
        print("Directory access and missing-root protection passed")
    }
}
