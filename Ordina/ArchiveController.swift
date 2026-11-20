import AppKit
import Combine
import Darwin
import Foundation

struct ArchiveEvent: Decodable {
    var status: String
    var project: String?
    var progress: Double?
    var archived: Int?
    var skipped: Int?
    var pending: Int?
    var issues: [String]?
}

@MainActor
final class ArchiveController: ObservableObject {
    static let shared = ArchiveController()
    @Published var automatic = true {
        didSet {
            guard !syncing, oldValue != automatic else { return }
            Task { await configure(automatic: automatic) }
        }
    }
    @Published var scanIntervalSeconds = 5 {
        didSet {
            guard !syncing, oldValue != scanIntervalSeconds else { return }
            Task {
                do {
                    try await control("set_interval", scanIntervalSeconds: scanIntervalSeconds)
                    poll()
                } catch { status = error.localizedDescription }
            }
        }
    }
    @Published var automaticArchive = true {
        didSet {
            guard !syncing, oldValue != automaticArchive else { return }
            let desired = automaticArchive
            Task {
                do {
                    try await control("set_automatic_archive", automaticArchive: desired)
                    poll()
                } catch { status = error.localizedDescription }
            }
        }
    }
    @Published var isRunning = false
    @Published var isBinding = false
    @Published var status = "正在连接…"
    @Published var projectName = ""
    @Published var progress: Double?
    @Published var archived = 0
    @Published var skipped = 0
    @Published var pending = 0
    @Published var issues: [String] = []
    private var timer: Timer?
    private var syncing = false
    private var starting = false
    private var suspensions = 0
    private var retryAfter = Date.distantPast
    private let directory = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("com.example.MagicTools.Youth/Archive", isDirectory: true)
    private var configURL: URL { directory.appendingPathComponent("config.json") }

    init() {
        if ProcessInfo.processInfo.environment["ORDO_UI_TEST"] == "1" {
            automatic = false
            return
        }
        let config = read(configURL)
        automatic = config["automatic"] as? Bool ?? UserDefaults.standard.object(forKey: "automaticMediaArchive") as? Bool ?? true
        scanIntervalSeconds = Self.intervalSeconds(config)
        automaticArchive = config["automaticArchive"] as? Bool ?? true
        timer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in self?.poll() }
        }
    }

    private func read(_ url: URL) -> [String: Any] {
        guard let data = try? Data(contentsOf: url),
              let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [:] }
        return value
    }

    private func alive(_ config: [String: Any]) -> Bool {
        let state = read(directory.appendingPathComponent("status.json"))
        if state["instance"] as? String == config["instance"] as? String, state["running"] as? Bool == false { return false }
        guard let pid = (config["pid"] as? NSNumber)?.int32Value, pid > 1 else { return false }
        return kill(pid, 0) == 0 || errno == EPERM
    }

    private static func intervalSeconds(_ config: [String: Any]) -> Int {
        guard let seconds = config["scanIntervalSeconds"] as? Int,
              (1...86400).contains(seconds) else { return 5 }
        return seconds
    }

    private func control(_ action: String, automatic desired: Bool? = nil, scanIntervalSeconds: Int? = nil,
                         automaticArchive: Bool? = nil) async throws {
        guard let script = Bundle.main.url(forResource: "archive_service", withExtension: "py"),
              let resources = Bundle.main.resourceURL else {
            throw ResolveFailure(message: "缺少后台归档组件，请重新构建应用。")
        }
        var request: [String: Any] = ["action": action, "configPath": configURL.path, "resources": resources.path,
                                      "projectRoots": UserDefaults.standard.stringArray(forKey: "archiveProjectRoots") ?? []]
        if let desired { request["automatic"] = desired }
        if let scanIntervalSeconds { request["scanIntervalSeconds"] = scanIntervalSeconds }
        if let automaticArchive { request["automaticArchive"] = automaticArchive }
        if let root = DestinationBookmark.load() { request["searchRoot"] = root.path }
        let inputData = try JSONSerialization.data(withJSONObject: request)
        try await Task.detached {
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
            process.arguments = ["-B", script.path, "--control", "run"]
            var environment = ProcessInfo.processInfo.environment
            environment["PYTHONUTF8"] = "1"
            environment["PYTHONIOENCODING"] = "utf-8"
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            process.environment = environment
            let input = Pipe()
            let output = Pipe()
            process.standardInput = input
            process.standardOutput = output
            process.standardError = FileHandle.nullDevice
            try process.run()
            try input.fileHandleForWriting.write(contentsOf: inputData)
            try input.fileHandleForWriting.close()
            let data = output.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            guard let response = try JSONSerialization.jsonObject(with: data) as? [String: Any], response["ok"] as? Bool == true else {
                let response = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
                throw ResolveFailure(message: response?["error"] as? String ?? "后台归档设置失败，请重试。")
            }
        }.value
    }

    func register(_ root: URL) {
        var paths = UserDefaults.standard.stringArray(forKey: "archiveProjectRoots") ?? []
        if !paths.contains(root.path) {
            paths.append(root.path)
            UserDefaults.standard.set(paths, forKey: "archiveProjectRoots")
        }
    }

    func bind() async {
        guard !isBinding else { return }
        isBinding = true
        defer { isBinding = false; resumeAfterOperation() }
        do {
            try await pauseForOperation()
            let panel = NSOpenPanel()
            panel.canChooseFiles = false
            panel.canChooseDirectories = true
            panel.allowsMultipleSelection = false
            panel.title = "选择当前工程的项目目录"
            panel.prompt = "关联"
            panel.directoryURL = DestinationBookmark.load()
            guard panel.runModal() == .OK, let root = panel.url else { return }
            let data = try await ResolveService.call(action: "bind", projectRoot: root.path)
            let metadata = try JSONSerialization.jsonObject(with: data) as? [String: Any]
            register(root)
            projectName = metadata?["projectName"] as? String ?? root.lastPathComponent
            status = "已关联：\(root.lastPathComponent)"
            issues = []
        } catch { status = error.localizedDescription }
    }

    func resumeIfEnabled() {
        guard automatic, suspensions == 0, !isBinding, !starting, Date() >= retryAfter else { return }
        starting = true
        Task {
            await configure(automatic: nil)
            starting = false
        }
    }

    private func configure(automatic desired: Bool?) async {
        do {
            try await control("configure", automatic: desired)
            poll()
        } catch {
            status = error.localizedDescription
            retryAfter = Date().addingTimeInterval(5)
        }
    }

    func pauseForOperation() async throws {
        suspensions += 1
        try await control("pause")
        poll()
    }

    func resumeAfterOperation() {
        suspensions = max(0, suspensions - 1)
        resumeIfEnabled()
    }

    private func poll() {
        let config = read(configURL)
        let autoArchive = config["automaticArchive"] as? Bool ?? true
        if automaticArchive != autoArchive {
            syncing = true
            automaticArchive = autoArchive
            syncing = false
        }
        let interval = Self.intervalSeconds(config)
        if scanIntervalSeconds != interval {
            syncing = true
            scanIntervalSeconds = interval
            syncing = false
        }
        if let desired = config["automatic"] as? Bool, desired != automatic, !starting {
            syncing = true
            automatic = desired
            syncing = false
        }
        isRunning = alive(config)
        let state = read(directory.appendingPathComponent("status.json"))
        if state["instance"] as? String == config["instance"] as? String,
           let data = try? JSONSerialization.data(withJSONObject: state),
           let event = try? JSONDecoder().decode(ArchiveEvent.self, from: data) {
            status = event.status
            if let value = event.project { projectName = value }
            progress = event.progress
            if let value = event.archived { archived = value }
            if let value = event.skipped { skipped = value }
            if let value = event.pending { pending = value }
            if let value = event.issues { issues = value.filter { $0 != event.status } }
        }
        if !isRunning {
            progress = nil
            if !automatic { status = "后台扫描已关闭" }
            if automatic, suspensions == 0, !isBinding { resumeIfEnabled() }
        }
    }
}
