import SwiftUI
import AppKit
import Combine

struct WorkspaceProject: Identifiable, Sendable {
    let url: URL
    let modified: Date
    let linked: Bool
    var lastUsed: Date? = nil
    var id: String { url.path }
    var title: String {
        let name = url.lastPathComponent
        return name.range(of: #"^[0-9]{8}_"#, options: .regularExpression) == nil ? name : String(name.dropFirst(9))
    }
}

extension Notification.Name {
    static let ordinaFocusCreation = Notification.Name("OrdinaFocusCreation")
    static let ordinaOpenSettings = Notification.Name("OrdinaOpenSettings")
}

@MainActor
final class WorkspaceState: ObservableObject {
    static let shared = WorkspaceState()
    @Published var projects: [WorkspaceProject] = []
    @Published var resolveError = ""
    @Published var databases: [ResolveDatabase] = []
    @Published var activeDatabaseID = ""
    @Published var projectError = ""
    @Published var checking = false

    @Published var refreshing = false
    private var scanID = UUID()

    func recordUse(_ url: URL) {
        var usage = UserDefaults.standard.dictionary(forKey: "recentProjectUsage") as? [String: Double] ?? [:]
        usage[url.path] = Date().timeIntervalSince1970
        UserDefaults.standard.set(usage, forKey: "recentProjectUsage")
    }

    func refreshProjects() {
        let root = DestinationBookmark.load()
        let usage = UserDefaults.standard.dictionary(forKey: "recentProjectUsage") as? [String: Double] ?? [:]
        let identity = UUID()
        scanID = identity
        refreshing = true
        Task {
            let result = await Task.detached(priority: .utility) { () -> ([WorkspaceProject], String) in
                let accessed = root?.startAccessingSecurityScopedResource() ?? false
                defer { if accessed { root?.stopAccessingSecurityScopedResource() } }
                var candidates = usage.keys.map { URL(fileURLWithPath: $0) }
                var message = ""
                if let root {
                    do {
                        candidates += try FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: [.isDirectoryKey, .isPackageKey, .contentModificationDateKey], options: .skipsHiddenFiles)
                    } catch { message = "项目目录不可访问，请检查连接。" }
                }
                var seen = Set<String>()
                let projects = candidates.compactMap { url -> WorkspaceProject? in
                    guard seen.insert(url.path).inserted else { return nil }
                    guard let values = try? url.resourceValues(forKeys: [.isDirectoryKey, .isPackageKey, .contentModificationDateKey]),
                          values.isDirectory == true, values.isPackage != true else { return nil }
                    return WorkspaceProject(url: url, modified: values.contentModificationDate ?? .distantPast,
                                            linked: FileManager.default.fileExists(atPath: url.appendingPathComponent(".magic-resolve.json").path),
                                            lastUsed: usage[url.path].map { Date(timeIntervalSince1970: $0) })
                }.sorted {
                    if $0.lastUsed != $1.lastUsed { return ($0.lastUsed ?? .distantPast) > ($1.lastUsed ?? .distantPast) }
                    return $0.url.lastPathComponent > $1.url.lastPathComponent
                }
                return (projects, message)
            }.value
            guard scanID == identity else { return }
            projects = result.0
            projectError = result.1
            refreshing = false
        }
    }

    func openProject(_ project: WorkspaceProject) async {
        do {
            guard project.linked else { throw ResolveFailure(message: "此目录尚未关联 Resolve 工程，请先在设置中关联。") }
            _ = try await ResolveService.call(action: "open", projectRoot: project.url.path)
            recordUse(project.url)
            refreshProjects()
            projectError = ""
        } catch { projectError = error.localizedDescription }
    }

    func openFolder(_ project: WorkspaceProject) {
        if NSWorkspace.shared.open(project.url) {
            recordUse(project.url)
            refreshProjects()
        } else { projectError = "项目目录不可访问，请检查连接。" }
    }

    func checkResolve() async {
        guard !checking else { return }
        checking = true
        defer { checking = false }
        do {
            let data = try await ResolveService.call(action: "probe")
            let probe = try JSONDecoder().decode(ResolveProbe.self, from: data)
            databases = probe.databases.filter { ["Disk", "PostgreSQL"].contains($0.DbType) }
            activeDatabaseID = probe.database.id
            resolveError = ""
        } catch {
            databases = []
            activeDatabaseID = ""
            resolveError = error.localizedDescription
        }
    }
}

struct WorkspaceView: View {
    @State private var showingProjects = false
    @State private var settings = false
    @State private var showingPlugins = false
    @State private var showProjectsAfterSettings = false
    @State private var checkUpdatesAfterSettings = false
    @State private var search = ""
    @State private var year = "全部"
    @FocusState private var searchFocused: Bool
    @ObservedObject private var workspace = WorkspaceState.shared
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    private var years: [String] {
        Array(Set(workspace.projects.compactMap { project in
            project.url.lastPathComponent.range(of: #"^[0-9]{8}_"#, options: .regularExpression) == nil
                ? nil : String(project.url.lastPathComponent.prefix(4))
        })).sorted(by: >)
    }
    private var filtered: [WorkspaceProject] {
        workspace.projects.filter {
            (search.isEmpty || $0.title.localizedCaseInsensitiveContains(search)) &&
            (year == "全部" || $0.url.lastPathComponent.hasPrefix(year))
        }
    }
    var body: some View {
        Group {
            if showingProjects { projectsPage } else { homePage }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background { OrdinaHomeBackground() }
        .foregroundStyle(OrdinaTokens.ink)
        .overlay(alignment: .topTrailing) {
            HStack(spacing: 4) {
                Button { showingPlugins = true } label: {
                    Image(systemName: "puzzlepiece.extension").font(.system(size: 23))
                        .frame(width: 24, height: 24)
                }
                .buttonStyle(PaperQuietButton()).help("插件管理").accessibilityLabel("插件管理")
                Button { settings = true } label: {
                    Image(systemName: "gearshape").font(.system(size: 23))
                }
                .buttonStyle(PaperQuietButton()).help("设置").accessibilityLabel("设置")
            }
            .padding(.trailing, 16).padding(.top, 8)
        }
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.18), value: showingProjects)
        .sheet(isPresented: $settings, onDismiss: {
            if showProjectsAfterSettings {
                showProjectsAfterSettings = false
                showingProjects = true
                workspace.refreshProjects()
            }
            guard checkUpdatesAfterSettings else { return }
            checkUpdatesAfterSettings = false
            AppUpdater.shared.checkForUpdates()
        }) {
            WorkspaceSettingsView(requestUpdateCheck: { checkUpdatesAfterSettings = true },
                                  requestProjects: { showProjectsAfterSettings = true })
        }
        .sheet(isPresented: $showingPlugins) { ResolvePluginsPage() }
        .onReceive(NotificationCenter.default.publisher(for: .ordinaOpenSettings)) { _ in settings = true }
        .onReceive(NotificationCenter.default.publisher(for: .ordinaFocusCreation)) { _ in showingProjects = false }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            if showingProjects { workspace.refreshProjects() }
        }
    }

    private var homePage: some View {
        ProjectCreationView()
            .padding(.horizontal, 32)
            .padding(.bottom, 84)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var projectsPage: some View {
        VStack(alignment: .leading, spacing: 24) {
            HStack(spacing: 12) {
                Button { showingProjects = false } label: {
                    DoodleIcon(.chevronLeft).frame(width: 15, height: 15)
                        .frame(width: 36, height: 36).contentShape(Rectangle())
                }.buttonStyle(.plain).help("返回").accessibilityLabel("返回")
                Text("项目").font(.system(size: 24, weight: .semibold))
            }
            HStack {
                PaperInput(placeholder: "搜索项目", text: $search, focus: $searchFocused)
                    .frame(maxWidth: 260)
                Spacer()
                PaperMenu(title: year) {
                    PaperMenuItem("全部") { year = "全部" }
                    ForEach(years, id: \.self) { value in PaperMenuItem(value) { year = value } }
                }
                .frame(width: 120)
                .accessibilityLabel("年份筛选")
                Button { workspace.refreshProjects() } label: { DoodleIcon(.refresh).frame(width: 16, height: 16) }
                    .buttonStyle(PaperQuietButton()).help("刷新").accessibilityLabel("刷新")
            }
            if !workspace.projectError.isEmpty {
                HStack(alignment: .top, spacing: 6) {
                    DoodleIcon(.alert).frame(width: 13, height: 13).foregroundStyle(OrdinaTokens.error)
                    Text(workspace.projectError).font(OrdinaTokens.type(12)).foregroundStyle(OrdinaTokens.error)
                        .textSelection(.enabled).help(workspace.projectError)
                }
            }
            if filtered.isEmpty {
                VStack(spacing: 6) {
                    Text(search.isEmpty ? "暂无项目" : "无匹配项目")
                        .font(OrdinaTokens.type(14)).foregroundStyle(OrdinaTokens.secondary)
                    Text(search.isEmpty ? "回到首页，输入项目名称创建一个。" : "试试其他关键词，或切换年份筛选。")
                        .font(OrdinaTokens.type(12)).foregroundStyle(OrdinaTokens.faint)
                }.frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(filtered) { project in
                            projectRow(project)
                            PaperRule()
                        }
                    }
                }
            }
        }.padding(32)
        .onAppear { searchFocused = true }
    }
    private func projectRow(_ project: WorkspaceProject) -> some View {
        ProjectRow(project: project, compact: false,
                         open: { Task { await workspace.openProject(project) } },
                         folder: { workspace.openFolder(project) })
    }
}

struct WorkspaceSettingsView: View {
    var requestUpdateCheck: () -> Void = {}
    var requestProjects: () -> Void = {}
    @ObservedObject private var updater = AppUpdater.shared
    @ObservedObject private var workspace = WorkspaceState.shared
    @ObservedObject private var templates = TemplateManager.shared
    @ObservedObject private var archive = ArchiveController.shared
    @AppStorage("resolveDatabaseID") private var databaseID = ""
    @AppStorage("createResolveProject") private var createResolve = false
    @State private var destination = DestinationBookmark.load()
    @State private var destinationError = ""
    @State private var editing = false
    @State private var showingLibraries = false
    @State private var showingPlugins = false
    @State private var versionClicks = 0
    @State private var showingDeveloper = false
    @State private var interval = ""
    @State private var intervalError = ""
    @Environment(\.dismiss) private var dismiss

    private var versionLabel: String {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? ""
        let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? ""
        return build.isEmpty ? "版本 \(version)" : "版本 \(version)（\(build)）"
    }

    var body: some View {
        VStack(spacing: 16) {
            HStack {
                Text("设置").font(OrdinaTokens.type(24).weight(.semibold))
                Spacer()
                Button { dismiss() } label: { DoodleIcon(.close).frame(width: 16, height: 16) }
                    .buttonStyle(PaperQuietButton()).help("关闭").accessibilityLabel("关闭")
                    .keyboardShortcut(.cancelAction)
            }.padding(.horizontal, 28).padding(.top, 20)
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    settingsGroup("项目默认值") {
                        settingRow("全部项目", action: "打开…") {
                            requestProjects()
                            dismiss()
                        }
                        PaperRule()
                        HStack(spacing: 12) {
                            Text("默认保存位置").fixedSize()
                            Spacer(minLength: 12)
                            settingsValue(destination?.path ?? "未选择")
                                .textSelection(.enabled)
                                .help(destination?.path ?? "选择文件夹")
                            Button("更改…", action: chooseDirectory)
                                .buttonStyle(PaperButtonStyle(compact: true))
                                .accessibilityLabel("更改默认保存位置")
                        }.frame(minHeight: 44)
                        if !destinationError.isEmpty { Text(destinationError).foregroundStyle(Paper.error) }
                        PaperRule()
                        HStack(spacing: 12) {
                            Text("默认文件夹模板").fixedSize()
                            Spacer(minLength: 12)
                            Button { editing = true } label: {
                                settingsValue(templateName)
                                    .padding(.vertical, 8)
                                    .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            .help("打开模板管理")
                            .accessibilityLabel("默认文件夹模板，" + templateName)
                        }.frame(minHeight: 44)
                    }
                    settingsGroup("Resolve 连接") {
                        toggleRow("同步创建 Resolve 工程", isOn: $createResolve)
                        PaperRule()
                        HStack(spacing: 12) {
                            Text("项目库").fixedSize()
                            Spacer(minLength: 12)
                            Button { showingLibraries = true } label: {
                                HStack(spacing: 8) {
                                    Circle().fill(connectionColor).frame(width: 7, height: 7)
                                        .accessibilityHidden(true)
                                    settingsValue(libraryName)
                                }
                                .padding(.vertical, 8)
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            .help(connectionStatus)
                            .accessibilityLabel("选择项目库，" + libraryName)
                            .accessibilityValue(connectionStatus)
                        }.frame(minHeight: 44)
                    }
                    settingsGroup("Resolve 插件") {
                        settingRow("插件管理", action: "打开…") { showingPlugins = true }
                    }
                    settingsGroup("归档偏好") {
                        toggleRow("后台扫描", isOn: $archive.automatic)
                        PaperRule()
                        toggleRow("自动归档", isOn: $archive.automaticArchive).disabled(!archive.automatic)
                        PaperRule()
                        HStack {
                            Text("扫描间隔")
                            Spacer()
                            TextField("扫描间隔", text: $interval).labelsHidden()
                                .textFieldStyle(.roundedBorder).frame(width: 65)
                                .onSubmit(saveInterval)
                            Text("秒").foregroundStyle(Paper.secondary)
                            Button("保存", action: saveInterval)
                                .buttonStyle(PaperButtonStyle(compact: true))
                                .disabled(interval == String(archive.scanIntervalSeconds))
                        }.frame(minHeight: 44)
                        if !intervalError.isEmpty { Text(intervalError).foregroundStyle(Paper.error) }
                    }
                }.padding(.horizontal, 28).padding(.bottom, 8)
            }.scrollBounceBehavior(.basedOnSize)
            VStack(alignment: .leading, spacing: 6) {
                PaperRule()
                if versionClicks >= 10 {
                    HStack {
                        Text("测试选项").foregroundStyle(Paper.secondary)
                        Spacer()
                        Button("重新打开引导") { OnboardingController.shared.reopen(); dismiss() }
                            .buttonStyle(PaperButtonStyle(compact: true))
                        Button("开发者中心…") { showingDeveloper = true }
                            .buttonStyle(PaperButtonStyle(compact: true))
                    }
                }
                HStack {
                    Button(versionLabel) { versionClicks = min(10, versionClicks + 1) }
                        .buttonStyle(.plain).font(Paper.font(12)).foregroundStyle(Paper.secondary)
                    Spacer()
                    Button("检查更新…") {
                        requestUpdateCheck()
                        dismiss()
                    }
                        .buttonStyle(PaperQuietButton())
                        .disabled(!updater.canCheckForUpdates)
                }.padding(.vertical, 6)
                Toggle("自动检查更新", isOn: Binding(
                    get: { updater.automaticallyChecksForUpdates },
                    set: { updater.setAutomaticallyChecksForUpdates($0) }
                ))
                .toggleStyle(.switch).controlSize(.small)
                .font(Paper.font(12)).foregroundStyle(Paper.secondary)
            }.padding(.horizontal, 28).padding(.bottom, 16)
        }
        .frame(width: 620, height: 640).background(Paper.background).foregroundStyle(Paper.ink).tint(Paper.accent)
        .sheet(isPresented: $editing) { WorkflowTemplateEditor(manager: templates) }
        .sheet(isPresented: $showingPlugins) { ResolvePluginsPage() }
        .sheet(isPresented: $showingDeveloper) { DeveloperCenterView() }
        .sheet(isPresented: $showingLibraries) {
            ResolveLibraryPicker(databases: workspace.databases, selection: databaseID, connectedID: workspace.activeDatabaseID,
                                 checking: workspace.checking, error: workspace.resolveError,
                                 refresh: { Task { await workspace.checkResolve() } }, choose: { databaseID = $0 })
        }
        .task {
            interval = String(archive.scanIntervalSeconds)
            await workspace.checkResolve()
        }
        .onChange(of: archive.scanIntervalSeconds) { oldValue, value in
            if interval == String(oldValue) { interval = String(value) }
        }
    }

    private var templateName: String {
        templates.templates.first { $0.id == templates.selectedTemplateID }?.displayName ?? "未选择"
    }

    private var libraryName: String {
        workspace.databases.first { $0.id == databaseID }?.label
            ?? databaseID.split(separator: "|", maxSplits: 2).last.map(String.init)
            ?? "未选择"
    }

    private var connectionStatus: String {
        if workspace.checking { return "检测中…" }
        if !workspace.resolveError.isEmpty { return workspace.resolveError }
        if databaseID.isEmpty { return "未选择项目库" }
        if databaseID == workspace.activeDatabaseID { return "已连接" }
        if workspace.activeDatabaseID.isEmpty { return "未检查连接" }
        if !workspace.databases.contains(where: { $0.id == databaseID }) { return "所选项目库不可用" }
        return "请在 Resolve 切换到所选项目库。"
    }

    private var connectionColor: Color {
        if workspace.checking { return Paper.secondary }
        if !workspace.resolveError.isEmpty { return Paper.error }
        guard !databaseID.isEmpty, !workspace.activeDatabaseID.isEmpty else { return Paper.secondary }
        return databaseID == workspace.activeDatabaseID ? Paper.success : Paper.error
    }

    private func settingsValue(_ value: String) -> some View {
        Text(value).font(Paper.font(12).monospaced())
            .foregroundStyle(Paper.secondary)
            .multilineTextAlignment(.trailing)
            .fixedSize(horizontal: false, vertical: true)
    }

    private func settingsGroup<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(Paper.font(12).weight(.medium)).foregroundStyle(Paper.secondary)
            VStack(alignment: .leading, spacing: 0, content: content)
                .padding(.horizontal, 14).padding(.vertical, 4)
                .background(Paper.surface, in: RoundedRectangle(cornerRadius: 12))
                .overlay { RoundedRectangle(cornerRadius: 12).strokeBorder(Paper.rule, lineWidth: 0.8) }
        }
    }

    private func settingRow(_ title: String, action: String, perform: @escaping () -> Void) -> some View {
        HStack(spacing: 12) {
            Text(title)
            Spacer()
            Button(action, action: perform).buttonStyle(PaperButtonStyle(compact: true))
                .accessibilityLabel(title + "，" + action)
        }.frame(minHeight: 44)
    }

    private func toggleRow(_ title: String, isOn: Binding<Bool>) -> some View {
        HStack {
            Text(title).accessibilityHidden(true)
            Spacer()
            Toggle(title, isOn: isOn).labelsHidden().toggleStyle(.switch).tint(OrdinaTokens.cream)
        }.frame(minHeight: 44)
    }

    private func chooseDirectory() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.directoryURL = destination
        panel.prompt = "选择"
        if panel.runModal() == .OK, let url = panel.url {
            do {
                try DestinationBookmark.save(url)
                destination = url
                destinationError = ""
                NotificationCenter.default.post(name: Notification.Name("OrdinaConfigurationChanged"), object: nil)
                workspace.refreshProjects()
            } catch { destinationError = error.localizedDescription }
        }
    }

    private func saveInterval() {
        guard let seconds = Int(interval), (1...86400).contains(seconds) else {
            intervalError = "请输入 1–86400 秒的整数。"
            return
        }
        intervalError = ""
        archive.scanIntervalSeconds = seconds
        interval = String(seconds)
    }
}

#Preview("首页 · 暗色") {
    WorkspaceView()
        .frame(width: 960, height: 640)
        .preferredColorScheme(.dark)
}


#Preview("设置") {
    WorkspaceSettingsView()
        .preferredColorScheme(.dark)
}
