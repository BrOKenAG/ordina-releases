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

        // The tool manifest trust anchor is pinned in a release build. A lab
        // build may substitute an isolated test key, but only while lab mode is
        // active, so an acceptance run never needs the production release key.
        let isolatedKey = Data(repeating: 0xAB, count: 32)
        setenv("ORDINA_DEV_MANIFEST_PUBLIC_KEY", isolatedKey.base64EncodedString(), 1)
        #if DEBUG || ORDINA_LAB
        setenv("ORDINA_DEV_LAB", "1", 1)
        try require(DevBridge.isLab, "lab mode not active")
        try require(DevBridge.toolManifestPublicKey == isolatedKey,
                    "lab manifest key was not used")
        unsetenv("ORDINA_DEV_LAB")
        #else
        try require(DevBridge.isLab == false, "release build reports lab mode")
        #endif
        try require(DevBridge.toolManifestPublicKey
                        == PluginEnvironment.productionPublicKey,
                    "production key not restored outside lab mode")
        unsetenv("ORDINA_DEV_MANIFEST_PUBLIC_KEY")

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

        // A successful publish returns the release tab to its initial drop
        // zone. No stale ZIP, notes, preview or progress may survive it.
        try MainActor.assumeIsolated {
            let model = DeveloperCenterModel()
            model.packageURL = URL(fileURLWithPath: "/tmp/demo.zip")
            model.notes = "首次发布 Demo"
            model.stages = [DeveloperCenterModel.Stage(name: "preflight", state: "ok")]
            model.logLines = ["done"]
            model.error = "旧错误"
            model.phase = .working("正在发布…")
            model.clearPackage()
            try require(model.packageURL == nil, "completed publish kept the selected ZIP")
            try require(model.inspection == nil, "completed publish kept the inspection")
            try require(model.preview == nil, "completed publish kept the preview")
            try require(model.notes.isEmpty, "completed publish kept the release notes")
            try require(model.stages.isEmpty, "completed publish kept the progress list")
            try require(model.logLines.isEmpty, "completed publish kept the log")
            try require(model.error == nil, "completed publish kept a stale error")
            try require(model.phase == .idle, "completed publish left a running phase")
            try require(!model.canPublish, "completed publish can still be published again")
        }

        // Deleting a finished journal is local-only and path-checked. Pending
        // transactions and paths outside the records directory are protected.
        try MainActor.assumeIsolated {
            let directory = DeveloperPaths.journals
            try manager.createDirectory(at: directory, withIntermediateDirectories: true)
            let record = directory.appendingPathComponent("tx-delete.json")
            let sidecar = URL(fileURLWithPath: record.path + ".meta.json")
            try "{\"status\":\"complete\"}".write(to: record, atomically: true,
                                                  encoding: .utf8)
            try "{}".write(to: sidecar, atomically: true, encoding: .utf8)

            func entry(_ path: String, _ status: String,
                       _ resumable: Bool) -> DevJournal {
                DevJournal(journal: path, status: status, plugin: "demo",
                           name: "Demo", version: "1.0.0", previousVersion: nil,
                           repository: nil, tag: nil, notes: nil, createdAt: nil,
                           toolVersion: nil, resumable: resumable,
                           rollbackable: status == "complete", repositories: [:])
            }
            let model = DeveloperCenterModel()
            model.deleteJournal(entry(record.path, "complete", false))
            try require(!manager.fileExists(atPath: record.path),
                        "finished journal was not removed")
            try require(!manager.fileExists(atPath: sidecar.path),
                        "journal sidecar was not removed")
            try require(model.error == nil, "delete reported an unexpected error")
            try require(model.notice?.contains("线上 Registry") == true,
                        "delete notice omitted the online-safety boundary")

            let outside = root.appendingPathComponent("outside.json")
            try "{}".write(to: outside, atomically: true, encoding: .utf8)
            model.deleteJournal(entry(outside.path, "complete", false))
            try require(manager.fileExists(atPath: outside.path),
                        "journal outside the records directory was deleted")
            try require(model.error != nil, "forged path was not rejected")

            let pending = directory.appendingPathComponent("tx-pending.json")
            try "{\"status\":\"publishing\"}".write(to: pending, atomically: true,
                                                   encoding: .utf8)
            model.deleteJournal(entry(pending.path, "publishing", true))
            try require(manager.fileExists(atPath: pending.path),
                        "pending transaction was deleted")
            try require(model.error != nil, "pending transaction was not protected")
        }

        print("VerifyDeveloperCenter: ok")
    }
}
