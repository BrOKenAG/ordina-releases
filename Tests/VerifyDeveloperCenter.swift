import Foundation

// The developer centre must be safe by construction, not by convention:
// recovery data has to be readable without launching the tool, the tool
// channel has to be pinned to the signed manifest, and a release build must
// have no way to point the client at a lab.
@main
struct VerifyDeveloperCenter {
    private enum CheckFailure: Error, CustomStringConvertible {
        case failed(String)
        var description: String {
            switch self { case .failed(let message): return message }
        }
    }

    static func require(_ condition: Bool, _ message: String) throws {
        if !condition { throw CheckFailure.failed(message) }
    }

    static func main() throws {
        let manager = FileManager.default
        let root = manager.temporaryDirectory
            .appendingPathComponent("Ordina-DeveloperCenter-" + UUID().uuidString)
        try manager.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? manager.removeItem(at: root) }

        let home = root.appendingPathComponent("home")
        try manager.createDirectory(at: home, withIntermediateDirectories: true)
        setenv("HOME", home.path, 1)
        setenv("ORDINA_CLIENT_VERSION", "1.10.0", 1)

        // Paths live outside the app bundle and outside every tool version, so
        // a tool update can never take credentials, journals or records with it.
        let base = home.appendingPathComponent("Library/Application Support/ordina/Developer")
        try require(DeveloperPaths.root.path == base.path, "developer root moved")
        try require(DeveloperPaths.journals.path == base.appendingPathComponent("journals").path,
                    "journal directory moved")
        try require(DeveloperPaths.records.path == base.appendingPathComponent("records.jsonl").path,
                    "records file moved")
        try require(DeveloperPaths.versions.path
                        == base.appendingPathComponent("Publisher/versions").path,
                    "tool versions directory moved")
        try require(DeveloperPaths.records.path.hasPrefix(DeveloperPaths.root.path),
                    "records left the developer root")

        // A release build must not be movable to another registry or key.
        #if DEBUG || ORDINA_LAB
        #else
        try require(DevBridge.isLab == false, "release build reports lab mode")
        #endif
        unsetenv("ORDINA_DEV_MANIFEST")
        try require(DevBridge.manifestURL.absoluteString
                        == "https://brokenag.github.io/ordina-releases/dev-tool/manifest.json",
                    "tool manifest is not the pinned Pages manifest")
        try require(DevProtocol.version == 1, "developer protocol version changed")
        try require(DevProtocol.minimumToolClient == "1.10.0",
                    "minimum tool client version changed")

        // Journals are readable without the tool: that is what makes recovery
        // possible after a crash or a broken update.
        try manager.createDirectory(at: DeveloperPaths.journals, withIntermediateDirectories: true)
        let journal = DeveloperPaths.journals.appendingPathComponent("tx.json")
        let document: [String: Any] = [
            "status": "publishing",
            "repositories": [
                "BrOKenAG/ordina-releases": ["before_head": "aaa", "after_head": "bbb"],
                "BrOKenAG/ordina-plugin-demo": ["before_head": "ccc", "rollback_head": "ddd"],
            ],
        ]
        try JSONSerialization.data(withJSONObject: document).write(to: journal)
        let meta: [String: Any] = [
            "plugin": "demo", "name": "Demo", "version": "1.2.0",
            "previousVersion": "1.1.0", "repository": "BrOKenAG/ordina-plugin-demo",
            "tag": "plugin-demo-v1.2.0", "notes": "third", "createdAt": 1_700_000_000,
            "toolVersion": "1.0.0",
        ]
        try JSONSerialization.data(withJSONObject: meta)
            .write(to: URL(fileURLWithPath: journal.path + ".meta.json"))

        let loaded = DeveloperCenterModel.localJournals()
        try require(loaded.count == 1, "journal sidecar was counted as a transaction")
        guard let entry = loaded.first else { throw CheckFailure.failed("journal missing") }
        try require(entry.status == "publishing", "journal status lost")
        try require(entry.plugin == "demo" && entry.version == "1.2.0",
                    "journal metadata lost")
        try require(entry.previousVersion == "1.1.0", "previous version lost")
        try require(entry.resumable, "an interrupted transaction must be resumable")
        try require(entry.rollbackable, "an interrupted transaction must be rollbackable")
        try require(entry.repositories.keys.sorted()
                        == ["BrOKenAG/ordina-plugin-demo", "BrOKenAG/ordina-releases"],
                    "journal repositories lost")
        try require(PendingTransactions.count() == 1,
                    "pending transactions must block a tool update")

        // A completed transaction is no longer pending but stays rollbackable.
        let completed = DeveloperPaths.journals.appendingPathComponent("done.json")
        try JSONSerialization.data(withJSONObject: ["status": "complete",
                                                   "repositories": [:]]).write(to: completed)
        try require(PendingTransactions.count() == 1,
                    "a completed transaction must not block a tool update")
        let after = DeveloperCenterModel.localJournals()
        try require(after.count == 2, "completed journal disappeared")

        // A rolled back transaction is still listed, and never resumable.
        let rolled = DeveloperPaths.journals.appendingPathComponent("rolled.json")
        try JSONSerialization.data(withJSONObject: ["status": "rolled_back",
                                                   "repositories": [:]]).write(to: rolled)
        let all = DeveloperCenterModel.localJournals()
        let rolledEntry = all.first { $0.journal.hasSuffix("rolled.json") }
        try require(rolledEntry != nil, "rolled back journal disappeared")
        try require(rolledEntry?.resumable == false, "a rolled back transaction is resumable")
        try require(rolledEntry?.rollbackable == false,
                    "a rolled back transaction is still rollbackable")

        // Records survive independently of the tool binary.
        try manager.createDirectory(at: DeveloperPaths.root, withIntermediateDirectories: true)
        let records = (0..<3).map { index -> String in
            """
            {"kind":"publish","plugin":"demo","version":"1.0.\(index)",\
            "journal":"\(journal.path)","status":"complete"}
            """
        }.joined(separator: "\n") + "\n"
        try records.write(to: DeveloperPaths.records, atomically: true, encoding: .utf8)
        try require(DeveloperCenterModel.localRecords().count == 3, "records lost")
        try require(DeveloperCenterModel.localRecords().first?.version == "1.0.2",
                    "records are not newest first")

        // No installation file means no installation: the manager never guesses.
        try require(DevToolManager.installation() == nil, "phantom installation reported")
        try require(DevToolManager.installedVersions().isEmpty, "phantom versions reported")

        // Failures carry the machine-readable fields the UI renders.
        let failure = DevFailure(code: "publish.failed", message: "boom",
                                 stage: "feeds", journal: "/tmp/tx.json")
        try require(failure.code == "publish.failed" && failure.stage == "feeds"
                        && failure.journal == "/tmp/tx.json", "failure metadata lost")

        print("VerifyDeveloperCenter: ok")
    }
}
