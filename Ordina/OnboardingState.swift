import Foundation
import Combine

enum OnboardingStatus: String, Codable {
    case notStarted
    case detecting
    case needsConfiguration
    case completed
    case failed
}

enum OnboardingStep: Int, Codable, CaseIterable {
    case projectRoot
    case resolveLibrary
    case projectSetup
}

struct OnboardingProgress: Codable {
    static let key = "onboardingProgress"
    var state: OnboardingStatus = .notStarted
    var step: OnboardingStep = .projectRoot
    var deferred = false

    static func load(from defaults: UserDefaults) -> OnboardingProgress {
        guard let data = defaults.data(forKey: key),
              var progress = try? JSONDecoder().decode(Self.self, from: data) else { return Self() }
        if progress.state == .detecting { progress.state = .needsConfiguration }
        return progress
    }

    func save(to defaults: UserDefaults) {
        guard let data = try? JSONEncoder().encode(self) else { return }
        defaults.set(data, forKey: Self.key)
    }
}

@MainActor
final class OnboardingController: ObservableObject {
    static let shared = OnboardingController(defaults: sharedDefaults())

    static func sharedDefaults(environment: [String: String] = ProcessInfo.processInfo.environment) -> UserDefaults {
        guard environment["ORDO_UI_TEST"] == "1", environment["ORDO_ONBOARDING_TEST"] == "1",
              let suite = environment["ORDO_UI_DEFAULTS_SUITE"]?.trimmingCharacters(in: .whitespacesAndNewlines),
              !suite.isEmpty, let defaults = UserDefaults(suiteName: suite) else { return .standard }
        return defaults
    }

    @Published var needsPresentation = false
    @Published private(set) var progress: OnboardingProgress
    @Published private(set) var destinationURL: URL?
    @Published private(set) var destinationError = ""
    @Published private(set) var databases: [ResolveDatabase] = []
    @Published private(set) var activeDatabase: ResolveDatabase?
    @Published private(set) var selectedDatabaseID: String
    @Published private(set) var resolveEnabled: Bool
    @Published private(set) var libraryError = ""
    @Published private(set) var integrationStatus: IntegrationInstaller.Status?
    @Published private(set) var integrationError = ""
    @Published private(set) var installing = false
    @Published private(set) var isDetecting = false

    private let defaults: UserDefaults
    private let home: URL
    private let source: URL?
    private let environment: [String: String]
    private let probe: () async throws -> ResolveProbe
    private let install: () throws -> Void
    private let loadBookmark: (UserDefaults) -> URL?
    private let saveBookmark: (URL, UserDefaults) throws -> Void
    private var detectionTask: Task<Void, Never>?
    private var generation = 0
    private var resumed = false
    private var detectExistingConfiguration = false

    init(defaults: UserDefaults = .standard,
         home: URL = FileManager.default.homeDirectoryForCurrentUser,
         source: URL? = Bundle.main.url(forResource: "Project Setup", withExtension: "py"),
         environment: [String: String] = ProcessInfo.processInfo.environment,
         probe: @escaping () async throws -> ResolveProbe = {
             let data = try await ResolveService.call(action: "probe")
             return try JSONDecoder().decode(ResolveProbe.self, from: data)
         },
         install: (() throws -> Void)? = nil,
         loadBookmark: @escaping (UserDefaults) -> URL? = { DestinationBookmark.load(from: $0) },
         saveBookmark: @escaping (URL, UserDefaults) throws -> Void = { try DestinationBookmark.save($0, to: $1) }) {
        self.defaults = defaults
        self.home = home
        self.source = source
        self.environment = environment
        self.probe = probe
        self.install = install ?? { try IntegrationInstaller.install(home: home, source: source) }
        self.loadBookmark = loadBookmark
        self.saveBookmark = saveBookmark
        progress = OnboardingProgress.load(from: defaults)
        selectedDatabaseID = defaults.string(forKey: "resolveDatabaseID") ?? ""
        resolveEnabled = defaults.object(forKey: "createResolveProject") == nil ? true : defaults.bool(forKey: "createResolveProject")
    }

    var state: OnboardingStatus { progress.state }
    var step: OnboardingStep { progress.step }
    var selectedDatabaseIsActive: Bool {
        !isDetecting && !selectedDatabaseID.isEmpty && activeDatabase?.id == selectedDatabaseID
            && databases.contains(where: { $0.id == selectedDatabaseID })
    }
    var canContinue: Bool {
        !installing && status(for: step) == .completed
    }
    var libraryStatus: String {
        if isDetecting { return "正在检测…" }
        if !libraryError.isEmpty { return libraryError }
        if selectedDatabaseIsActive { return "已连接" }
        if !selectedDatabaseID.isEmpty {
            return databases.contains(where: { $0.id == selectedDatabaseID })
                ? "请在 Resolve 中切换到所选项目库，再刷新。"
                : "所选项目库不可用，请在 Resolve 中添加或连接后刷新。"
        }
        return databases.isEmpty ? "请启动 Resolve，添加或连接项目库后刷新。" : "选择已有项目库"
    }

    func status(for step: OnboardingStep) -> OnboardingStatus {
        if isReady(step) { return .completed }
        switch step {
        case .projectRoot:
            if !destinationError.isEmpty { return .failed }
        case .resolveLibrary:
            if isDetecting { return .detecting }
            if !libraryError.isEmpty { return .failed }
        case .projectSetup:
            if installing { return .detecting }
            if !integrationError.isEmpty { return .failed }
        }
        return state == .notStarted ? .notStarted : .needsConfiguration
    }

    func resume() {
        guard !resumed else { return }
        resumed = true
        guard environment["ORDO_UI_TEST"] != "1" || environment["ORDO_ONBOARDING_TEST"] == "1" else { return }
        guard state != .completed, !progress.deferred else { return }
        detectExistingConfiguration = state == .notStarted
        needsPresentation = true
        Task { await refresh() }
    }

    func reopen() {
        invalidateDetection()
        detectExistingConfiguration = false
        progress.deferred = false
        progress.state = .needsConfiguration
        persist()
        needsPresentation = true
        Task { await refresh() }
    }

    func deferSetup() {
        invalidateDetection()
        detectExistingConfiguration = false
        if state != .completed {
            progress.deferred = true
            if state == .detecting { progress.state = .needsConfiguration }
            persist()
        }
        needsPresentation = false
    }

    func refresh() async {
        if let detectionTask {
            await detectionTask.value
            return
        }
        guard needsPresentation else { return }
        let token = generation
        isDetecting = true
        let task = Task { await self.detect(generation: token) }
        detectionTask = task
        await task.value
        if generation == token {
            detectionTask = nil
            isDetecting = false
        }
    }

    func saveDestination(_ url: URL) {
        guard folderIsAvailable(url) else {
            destinationError = "此文件夹不可用或不可写，请检查权限和连接。"
            progress.state = .failed
            persist()
            return
        }
        do {
            try saveBookmark(url, defaults)
            destinationURL = url
            destinationError = ""
            progress.state = .needsConfiguration
            persist()
        } catch {
            destinationError = "无法保存文件夹权限，请重新选择。"
            progress.state = .failed
            persist()
        }
    }

    func setResolveEnabled(_ enabled: Bool) {
        resolveEnabled = enabled
        defaults.set(enabled, forKey: "createResolveProject")
    }

    func selectDatabase(_ id: String) {
        guard databases.contains(where: { $0.id == id }) else { return }
        selectedDatabaseID = id
        defaults.set(id, forKey: "resolveDatabaseID")
        setResolveEnabled(true)
    }

    func back() {
        guard !installing, let previous = OnboardingStep(rawValue: step.rawValue - 1) else { return }
        detectExistingConfiguration = false
        progress.step = previous
        persist()
    }

    func advance() {
        guard canContinue else { return }
        detectExistingConfiguration = false
        if step == .resolveLibrary && !resolveEnabled { setResolveEnabled(false) }
        if let next = OnboardingStep(rawValue: step.rawValue + 1) {
            progress.step = next
            progress.state = .needsConfiguration
            persist()
        } else if OnboardingStep.allCases.allSatisfy(isReady) {
            complete()
        } else {
            progress.step = OnboardingStep.allCases.first(where: { !isReady($0) }) ?? .projectRoot
            persist()
        }
    }

    func installProjectSetup() {
        guard !installing else { return }
        installing = true
        defer { installing = false }
        do {
            try install()
            integrationStatus = try IntegrationInstaller.inspect(home: home, source: source)
            integrationError = ""
            progress.state = .needsConfiguration
        } catch {
            integrationStatus = nil
            integrationError = "安装失败：\(error.localizedDescription)"
            progress.state = .failed
        }
        persist()
    }

    private func detect(generation token: Int) async {
        progress.state = .detecting
        persist()
        selectedDatabaseID = defaults.string(forKey: "resolveDatabaseID") ?? ""
        resolveEnabled = defaults.object(forKey: "createResolveProject") == nil ? true : defaults.bool(forKey: "createResolveProject")
        destinationURL = loadBookmark(defaults)
        if let url = destinationURL, !folderIsAvailable(url) { destinationURL = nil }
        destinationError = destinationURL == nil && defaults.data(forKey: "destinationFolderBookmark") != nil
            ? "保存位置不可用或不可写，请检查权限和连接后刷新。" : ""
        do {
            integrationStatus = try IntegrationInstaller.inspect(home: home, source: source)
            integrationError = ""
        } catch {
            integrationStatus = nil
            integrationError = error.localizedDescription
        }
        if detectExistingConfiguration && OnboardingStep.allCases.allSatisfy(isReady) {
            detectExistingConfiguration = false
            complete()
            return
        }
        do {
            let result = try await probe()
            guard generation == token, !Task.isCancelled else { return }
            var seen = Set<String>()
            databases = result.databases.filter {
                ($0.DbType == "Disk" || $0.DbType == "PostgreSQL") && seen.insert($0.id).inserted
            }
            activeDatabase = result.database
            libraryError = ""
        } catch {
            guard generation == token, !Task.isCancelled else { return }
            databases = []
            activeDatabase = nil
            libraryError = "无法连接 Resolve，请启动后刷新。"
        }
        guard generation == token, !Task.isCancelled else { return }
        isDetecting = false
        progress.state = integrationError.isEmpty ? .needsConfiguration : .failed
        if detectExistingConfiguration {
            detectExistingConfiguration = false
            if let first = OnboardingStep.allCases.first(where: { !isReady($0) }) {
                progress.step = first
            } else {
                complete()
                return
            }
        }
        persist()
    }

    private func isReady(_ step: OnboardingStep) -> Bool {
        switch step {
        case .projectRoot: return destinationURL != nil
        case .resolveLibrary:
            return resolveEnabled ? selectedDatabaseIsActive : defaults.object(forKey: "createResolveProject") != nil
        case .projectSetup: return integrationStatus == .current
        }
    }

    private func folderIsAvailable(_ url: URL) -> Bool {
        let accessing = url.startAccessingSecurityScopedResource()
        defer { if accessing { url.stopAccessingSecurityScopedResource() } }
        return (try? url.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true
            && FileManager.default.isReadableFile(atPath: url.path)
            && FileManager.default.isWritableFile(atPath: url.path)
    }

    private func invalidateDetection() {
        generation += 1
        detectionTask?.cancel()
        detectionTask = nil
        isDetecting = false
    }

    private func complete() {
        invalidateDetection()
        progress.state = .completed
        progress.deferred = false
        persist()
        needsPresentation = false
    }

    private func persist() {
        progress.save(to: defaults)
    }
}
