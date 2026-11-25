import Foundation
import Combine

// The developer centre model. It owns no release logic: every action is a call
// into the installed tool, and every state shown in the UI comes from the
// tool's structured output. Long operations run off the main actor so the
// window stays responsive while a transaction is in flight.

@MainActor
final class DeveloperCenterModel: ObservableObject {
    enum Phase: Equatable {
        case idle
        case working(String)
        case failed(String)

        var isWorking: Bool { if case .working = self { return true }; return false }
    }

    struct Stage: Identifiable, Equatable {
        let name: String
        var state: String
        var id: String { name }
        var title: String {
            switch name {
            case "preflight": return "检查发布环境"
            case "asset": return "准备发布资产"
            case "gate": return "校验已上传资产"
            case "feeds": return "更新签名清单"
            case "push": return "确认事务提交"
            case "postflight": return "线上复核"
            default: return name
            }
        }
        var symbol: String {
            switch state {
            case "ok": return "checkmark.circle.fill"
            case "running": return "circle.dotted"
            case "failed": return "xmark.circle.fill"
            default: return "circle"
            }
        }
    }

    enum ToolLevel: Equatable {
        case missing, ready, incompatible, needsRepair, notConfigured
    }

    struct ToolState: Equatable {
        var level: ToolLevel = .notConfigured
        var version: String?
        var protocolVersion: Int?
        var detail: String?
        var credential: DevCredentialStatus?

        var title: String {
            switch level {
            case .ready: return "发布工具 " + (version ?? "")
            case .incompatible: return "发布工具版本不兼容"
            case .needsRepair: return "发布工具需要修复"
            case .missing: return "未安装发布工具"
            case .notConfigured: return "未配置发布环境"
            }
        }
    }

    @Published var configuration: DeveloperConfiguration {
        didSet { DeveloperDefaults.save(configuration) }
    }
    @Published var phase: Phase = .idle
    @Published var toolState = ToolState()
    @Published var doctor: DevDoctor?
    @Published var packageURL: URL?
    @Published var inspection: DevInspection?
    @Published var notes = ""
    @Published var preview: DevPreview?
    @Published var stages: [Stage] = []
    @Published var logLines: [String] = []
    @Published var journals: [DevJournal] = []
    @Published var history: DevHistory?
    @Published var installedTool: DevToolInstallation?
    @Published var availableTool: DevToolEntry?
    @Published var toolMessage: String?
    @Published var localTestResult: DevLocalTest?
    @Published var lastJournal: String?
    @Published var error: String?
    @Published var notice: String?
    @Published var busy = false
    @Published var tab = 0
    @Published var checkingTool = false

    private var service: DeveloperPublishing?

    init(configuration: DeveloperConfiguration = DeveloperDefaults.load()) {
        self.configuration = configuration
    }

    // MARK: derived

    var canPublish: Bool {
        guard inspection?.compatibility.ok == true, preview != nil,
              !busy, !notes.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        else { return false }
        guard let preview else { return false }
        return preview.blockers.isEmpty && inspection?.action != "same"
            && inspection?.action != "downgrade"
    }

    var pendingJournals: [DevJournal] { journals.filter(\.isPending) }

    /// Journals are authoritative and readable without the tool, so a broken
    /// or missing tool can never be swapped while a transaction is open.
    var hasPendingTransaction: Bool {
        !pendingJournals.isEmpty || PendingTransactions.count() > 0
    }

    // MARK: lifecycle

    func load() async {
        installedTool = DevToolManager.installation()
        guard configuration.enabled else { return }
        guard DeveloperToolLocator.resolve(configuration) != nil else {
            toolState = ToolState(level: .missing,
                                  detail: "发布工具未安装，普通用户不受影响。")
            await refreshRecordsOnly()
            return
        }
        await refreshAll()
    }

    func refreshAll() async {
        await refreshJournals()
        await refreshHistory()
        await refreshDoctor()
    }

    func refreshRecordsOnly() async {
        await refreshJournals()
        await refreshHistory()
    }

    func refreshJournals() async {
        if let service {
            if let value = try? await service.journals() {
                journals = value
                return
            }
        }
        journals = DeveloperCenterModel.localJournals()
    }

    /// Journals are readable without launching the tool, which is what makes
    /// recovery possible after a crash or a broken update.
    nonisolated static func localJournals() -> [DevJournal] {
        let manager = FileManager.default
        guard let files = try? manager.contentsOfDirectory(at: DeveloperPaths.journals,
                                                           includingPropertiesForKeys: nil)
        else { return [] }
        var result: [DevJournal] = []
        for file in files where file.pathExtension == "json"
            && !file.lastPathComponent.hasSuffix(".meta.json") {
            guard let data = try? Data(contentsOf: file),
                  let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let status = object["status"] as? String else { continue }
            var meta: [String: Any] = [:]
            let sidecar = URL(fileURLWithPath: file.path + ".meta.json")
            if let raw = try? Data(contentsOf: sidecar),
               let parsed = try? JSONSerialization.jsonObject(with: raw) as? [String: Any] {
                meta = parsed
            }
            var repositories: [String: DevJournal.Repository] = [:]
            for (repo, value) in (object["repositories"] as? [String: Any]) ?? [:] {
                guard let record = value as? [String: Any] else { continue }
                repositories[repo] = DevJournal.Repository(
                    before: record["before_head"] as? String,
                    after: record["after_head"] as? String,
                    rolledBack: record["rollback_head"] as? String)
            }
            result.append(DevJournal(
                journal: file.path, status: status,
                plugin: meta["plugin"] as? String, name: meta["name"] as? String,
                version: meta["version"] as? String,
                previousVersion: meta["previousVersion"] as? String,
                repository: meta["repository"] as? String,
                tag: meta["tag"] as? String, notes: meta["notes"] as? String,
                createdAt: meta["createdAt"] as? Int,
                toolVersion: meta["toolVersion"] as? String,
                resumable: status == "preparing" || status == "publishing",
                rollbackable: status == "complete" || status == "publishing",
                repositories: repositories))
        }
        return result.sorted { ($0.createdAt ?? 0) > ($1.createdAt ?? 0) }
    }

    func refreshHistory() async {
        if let service, let value = try? await service.history() {
            history = value
            if journals.isEmpty { journals = value.journals }
            return
        }
        history = DevHistory(records: DeveloperCenterModel.localRecords(), journals: journals)
    }

    nonisolated static func localRecords() -> [DevRecord] {
        guard let data = try? Data(contentsOf: DeveloperPaths.records),
              let text = String(data: data, encoding: .utf8) else { return [] }
        let decoder = JSONDecoder()
        var result: [DevRecord] = []
        for line in text.split(separator: "\n").suffix(60) {
            guard let raw = line.data(using: .utf8),
                  let record = try? decoder.decode(DevRecord.self, from: raw) else { continue }
            result.append(record)
        }
        return result.reversed()
    }

    func refreshDoctor() async {
        guard let service = makeService() else {
            toolState = ToolState(level: .missing,
                                  detail: configuration.toolPath.isEmpty
                                    ? "未安装发布工具。安装后可在此完成首次配置。"
                                    : "所选的发布工具不可用。")
            return
        }
        checkingTool = true
        defer { checkingTool = false }
        do {
            let info = try await service.hello()
            guard info.protocolVersion == DevProtocol.version else {
                toolState = ToolState(level: .incompatible, version: info.version,
                                      protocolVersion: info.protocolVersion,
                                      detail: "工具协议 \(info.protocolVersion) 与客户端 \(DevProtocol.version) 不兼容。")
                return
            }
            let doctor = try await service.doctor()
            self.doctor = doctor
            toolState = ToolState(level: .ready, version: doctor.version,
                                  protocolVersion: doctor.protocolVersion,
                                  detail: doctor.ready ? "发布环境已就绪"
                                    : (doctor.requirements.first { !$0.ok }?.message
                                       ?? "发布环境未就绪"),
                                  credential: doctor.credential)
        } catch let failure as DevFailure {
            toolState = ToolState(level: .needsRepair, detail: failure.message)
        } catch {
            toolState = ToolState(level: .needsRepair, detail: error.localizedDescription)
        }
    }

    func makeService() -> DeveloperPublishing? {
        if let service { return service }
        guard let executable = DeveloperToolLocator.resolve(configuration) else { return nil }
        let tool = LocalPublisherTool(executable: executable,
                                      configuration: configuration)
        service = tool
        return tool
    }

    func invalidateService() {
        service = nil
    }

    // MARK: configuration

    func enable() {
        configuration.enabled = true
        Task { await load() }
    }

    func setRepository(_ url: URL) {
        configuration.repositoryPath = url.path
        DeveloperDefaults.save(configuration)
        invalidateService()
        Task { await refreshDoctor() }
    }

    func setToolPath(_ url: URL?) {
        configuration.toolPath = url?.path ?? ""
        DeveloperDefaults.save(configuration)
        invalidateService()
        Task { await load() }
    }

    func importCredential(_ url: URL) async {
        guard let service = makeService() else { return }
        await perform("正在验证授权…") {
            let status = try await service.importCredential(file: url)
            self.toolState.credential = status
            self.notice = status.valid
                ? "授权已写入钥匙串（\(status.developer ?? "")，\(status.expires.map(Self.date) ?? "") 到期）"
                : "授权无效：\(status.message ?? "")"
            await self.refreshDoctor()
        }
    }

    // MARK: package flow

    func choosePackage(_ url: URL) async {
        packageURL = url
        inspection = nil
        preview = nil
        localTestResult = nil
        stages = []
        logLines = []
        error = nil
        guard let service = makeService() else {
            error = DevBridgeError.toolMissing.localizedDescription
            return
        }
        await perform("正在解析安装包…") {
            let value = try await service.inspect(zip: url)
            self.inspection = value
            if self.notes.isEmpty {
                self.notes = value.isNew ? "首次发布 \(value.package.name)" : ""
            }
        }
    }

    func localTest() async {
        guard let url = packageURL, let service = makeService() else { return }
        await perform("正在安装测试版本…") {
            let result = try await service.localTest(zip: url)
            let data = try Data(contentsOf: URL(fileURLWithPath: result.bundlePath))
            let files = try PluginProtocol.unpack(data, release: result.release)
            try PluginInstaller.install(result.release, files: files, environment: .current)
            self.localTestResult = result
            self.notice = "已在本机安装测试版本 \(result.version)；不会修改线上 Registry。"
        }
    }

    func uninstallLocalTest() async {
        guard let result = localTestResult else { return }
        await perform("正在卸载测试版本…") {
            try PluginInstaller.uninstall(result.release.descriptor, environment: .current)
            self.localTestResult = nil
            self.notice = "测试版本已卸载，用户数据保留。"
        }
    }

    func runPreview() async {
        guard let url = packageURL, let service = makeService() else { return }
        await perform("正在生成发布预览…") {
            self.preview = try await service.preview(zip: url,
                                                     notes: self.notes.trimmingCharacters(in: .whitespacesAndNewlines))
            self.stages = []
        }
    }

    func publish(createRepository: Bool) async {
        guard let url = packageURL, let preview, let service = makeService() else { return }
        stages = []
        logLines = []
        lastJournal = nil
        let notes = self.notes.trimmingCharacters(in: .whitespacesAndNewlines)
        busy = true
        phase = .working("正在发布…")
        error = nil
        do {
            let result = try await service.publish(
                zip: url, notes: notes, fingerprint: preview.fingerprint,
                createRepository: createRepository) { [weak self] event in
                    Task { @MainActor in self?.apply(event) }
                }
            lastJournal = result.journal
            notice = "已发布 \(result.plugin) \(result.version)"
            phase = .idle
            self.preview = nil
            await refreshAll()
        } catch let failure as DevFailure {
            apply(failure: failure)
        } catch {
            apply(error: error)
        }
        busy = false
    }

    // MARK: transactions

    func resume(_ journal: DevJournal) async {
        guard let service = makeService() else { return }
        stages = []
        logLines = []
        busy = true
        phase = .working("正在恢复事务…")
        error = nil
        do {
            let action = try await service.resume(journal: journal.journal) { [weak self] event in
                Task { @MainActor in self?.apply(event) }
            }
            notice = "事务已完成：\(action.plugin ?? "") \(action.version ?? "")"
            lastJournal = action.journal
            phase = .idle
            await refreshAll()
        } catch let failure as DevFailure {
            apply(failure: failure)
        } catch {
            apply(error: error)
        }
        busy = false
    }

    func rollback(_ journal: DevJournal, reason: String) async {
        guard let service = makeService() else { return }
        busy = true
        phase = .working("正在回滚…")
        error = nil
        do {
            let action = try await service.rollback(journal: journal.journal, reason: reason)
            notice = "已回滚 \(action.plugin ?? "") \(action.version ?? "") 到发布前状态"
            await refreshAll()
            phase = .idle
        } catch let failure as DevFailure {
            apply(failure: failure)
        } catch {
            apply(error: error)
        }
        busy = false
    }

    // MARK: tool channel

    func checkToolUpdate() async {
        checkingTool = true
        defer { checkingTool = false }
        do {
            let manifest = try await Task.detached(priority: .utility) {
                try DevToolManager.manifest()
            }.value
            let current = DevToolManager.installation()?.version
            let candidate = manifest.versions
                .filter { DevToolManager.compatible($0) }
                .sorted { $0.version.compare($1.version, options: .numeric) == .orderedDescending }
                .first
            installedTool = DevToolManager.installation()
            if let candidate, candidate.version != current {
                availableTool = candidate
                toolMessage = "可更新到 \(candidate.version)"
            } else {
                availableTool = nil
                toolMessage = current == nil ? "可从签名清单安装发布工具" : "已是最新版本"
            }
        } catch {
            availableTool = nil
            toolMessage = error.localizedDescription
        }
    }

    func installTool(_ entry: DevToolEntry) async {
        guard !hasPendingTransaction else {
            toolMessage = "存在未完成的发布事务，请先完成或回滚。"
            return
        }
        busy = true
        phase = .working("正在安装发布工具 \(entry.version)…")
        error = nil
        do {
            let record = try await Task.detached(priority: .userInitiated) {
                let record = try DevToolManager.install(entry)
                try DevToolManager.healthCheck(
                    path: URL(fileURLWithPath: record.path), expected: entry)
                return record
            }.value
            installedTool = record
            invalidateService()
            availableTool = nil
            notice = "发布工具已更新到 \(entry.version)，授权、记录与事务均已保留。"
            phase = .idle
            await refreshDoctor()
        } catch {
            apply(error: error)
        }
        busy = false
    }

    func rollbackTool() async {
        guard !hasPendingTransaction else {
            toolMessage = "存在未完成的发布事务，请先完成或回滚。"
            return
        }
        busy = true
        do {
            let record = try await Task.detached(priority: .userInitiated) {
                try DevToolManager.rollback()
            }.value
            installedTool = record
            invalidateService()
            notice = "已回滚到发布工具 \(record.version)"
            await refreshDoctor()
        } catch {
            apply(error: error)
        }
        busy = false
    }

    // MARK: events and errors

    private func apply(_ event: DevEvent) {
        switch event.kind {
        case .stage(let name, let state, _, _):
            if let index = stages.firstIndex(where: { $0.name == name }) {
                stages[index].state = state
            } else {
                stages.append(Stage(name: name, state: state))
            }
            if state == "failed" { phase = .working("发布失败，可恢复") }
        case .log(let message):
            logLines.append(message)
            if logLines.count > 200 { logLines.removeFirst(logLines.count - 200) }
        case .other:
            break
        }
    }

    private func apply(failure: DevFailure) {
        error = failure.message
        if let journal = failure.journal { lastJournal = journal }
        phase = .failed(failure.message)
        if failure.stage == "feeds" || failure.stage == "push" || failure.stage == "postflight" {
            notice = "事务已记录，可在「发布管理」中继续或回滚。"
        }
        Task { await refreshAll() }
    }

    private func apply(error: Error) {
        self.error = error.localizedDescription
        phase = .failed(error.localizedDescription)
    }

    private func perform(_ label: String, _ operation: @escaping @MainActor () async throws -> Void) async {
        guard !busy else { return }
        busy = true
        phase = .working(label)
        error = nil
        do {
            try await operation()
            if case .working = phase { phase = .idle }
        } catch let failure as DevFailure {
            apply(failure: failure)
        } catch {
            apply(error: error)
        }
        busy = false
    }

    static func date(_ seconds: Int) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "zh_Hans_CN")
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter.string(from: Date(timeIntervalSince1970: TimeInterval(seconds)))
    }
}

enum DeveloperToolLocator {
    /// Explicit path first (developer-managed), then the managed installation.
    static func resolve(_ configuration: DeveloperConfiguration) -> URL? {
        DevToolManager.verifiedPath(configuration)
    }
}
