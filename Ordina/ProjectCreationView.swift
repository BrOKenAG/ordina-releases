import SwiftUI
import UniformTypeIdentifiers
import AppKit
import Combine

// 1. 定义模版数据结构
struct WorkflowTemplate: Identifiable, Codable, Hashable {
    var id = UUID()
    var name: String
    var content: String
}

extension WorkflowTemplate {
    var displayName: String { ["full workflow", "默认全套模版"].contains(name) ? "完整工作流" : name }
}

// 2. 模版管理器
class TemplateManager: ObservableObject {
    static let shared = TemplateManager()
    @Published var templates: [WorkflowTemplate] = [] {
        didSet { save() }
    }
    @Published var selectedTemplateID: UUID? {
        didSet {
            if let id = selectedTemplateID {
                UserDefaults.standard.set(id.uuidString, forKey: "selectedTemplateID")
            }
        }
    }
    
    let defaultContent = """
    00_最终交付/1_母带
    00_最终交付/2_成片
    1_工程文件/项目缓存
    1_工程文件/画廊静帧
    2_原始素材/图片
    2_原始素材/音频
    2_原始素材/源视频
    2_原始素材/转码素材
    3_素材/视频
    3_素材/图片
    3_素材/音乐
    3_素材/音效
    4_特效/导出
    4_特效/素材
    5_字幕
    6_导出
    7_封面
    8_调色/调色版本静帧
    8_调色/色彩查找表
    """
    
    init() { load() }
    
    func load() {
        if let data = UserDefaults.standard.data(forKey: "savedTemplatesData"),
           let decoded = try? JSONDecoder().decode([WorkflowTemplate].self, from: data) {
            templates = decoded
        }
        if templates.isEmpty {
            templates.append(WorkflowTemplate(name: "完整工作流", content: defaultContent))
        }
        if let savedIDString = UserDefaults.standard.string(forKey: "selectedTemplateID"),
           let savedID = UUID(uuidString: savedIDString),
           templates.contains(where: { $0.id == savedID }) {
            selectedTemplateID = savedID
        } else {
            selectedTemplateID = templates.first?.id
        }
    }
    
    func save() {
        if let data = try? JSONEncoder().encode(templates) {
            UserDefaults.standard.set(data, forKey: "savedTemplatesData")
        }
    }
    
    func getActiveContent() -> String {
        guard let id = selectedTemplateID,
              let template = templates.first(where: { $0.id == id }) else {
            return defaultContent
        }
        return template.content
    }
}

// 3. 主界面 UI
struct ProjectCreationView: View {
    @ObservedObject private var manager = TemplateManager.shared
    @ObservedObject private var archive = ArchiveController.shared

    private enum Step { case settings, originals }
    @State private var step: Step = .settings
    @State private var showingSetup = false
    @State private var projectName = ""
    @State private var destinationURL: URL?
    @State private var templateID: UUID?
    @State private var showingTemplates = false
    @State private var showingLibraries = false
    @State private var rawFolders: [URL] = []
    @State private var statusMessage = ""
    @State private var setupLaunchError = ""
    @State private var showingSetupLaunchError = false
    @State private var connectionError = ""
    @State private var createResolveProject = false
    @State private var resolveDatabaseID = ""
    @State private var databases: [ResolveDatabase] = []
    @State private var currentDatabaseID = ""
    @State private var isWorking = false
    @State private var isChecking = false
    @State private var creationOutcome = ""
    @FocusState private var nameFocused: Bool
    @AppStorage("createResolveProject") private var defaultCreateResolve = false
    @AppStorage("resolveDatabaseID") private var defaultDatabaseID = ""

    private var trimmedName: String { projectName.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var canContinue: Bool {
        destinationURL != nil && templateID != nil &&
        (!createResolveProject || (!isChecking && !resolveDatabaseID.isEmpty && currentDatabaseID == resolveDatabaseID
                                   && databases.contains(where: { $0.id == resolveDatabaseID })))
    }

    var body: some View {
        VStack(spacing: 22) {
            Image("WordMark").resizable().scaledToFit().frame(height: 80)
                .offset(y: 5.5).frame(height: 36).clipped()
                .accessibilityLabel("ordina")
            HStack(spacing: 12) {
                TextField("输入项目名称", text: $projectName,
                          prompt: Text("输入项目名称").foregroundStyle(OrdinaTokens.secondary))
                    .textFieldStyle(.plain).font(OrdinaTokens.type(18))
                    .frame(minHeight: 30)
                    .foregroundStyle(OrdinaTokens.ink)
                    .focused($nameFocused).focusEffectDisabled().accessibilityLabel("项目名称")
                    .onSubmit(openSetup)
                Button("+新建", action: openSetup)
                    .buttonStyle(OrdinaCreateButtonStyle())
                    .disabled(trimmedName.isEmpty || isWorking)
                    .accessibilityLabel("新建项目")
            }
            .padding(.leading, 22).padding(.trailing, 10).frame(height: 64)
            .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 32))
            .background(OrdinaTokens.glass, in: RoundedRectangle(cornerRadius: 32))
            .overlay { GlassFieldBorder(focused: nameFocused, working: isWorking, outcome: creationOutcome, cornerRadius: 32) }
        }
        .frame(maxWidth: 560)
        .frame(maxWidth: .infinity)
        .onAppear { nameFocused = true }
        .onReceive(NotificationCenter.default.publisher(for: .ordinaFocusCreation)) { _ in
            if !showingSetup { nameFocused = true }
        }
        .sheet(isPresented: $showingSetup, onDismiss: { nameFocused = true }) {
            setupSheet
        }
        .alert("项目已创建", isPresented: $showingSetupLaunchError) {
            Button("好", role: .cancel) {}
        } message: {
            Text(setupLaunchError)
        }
    }

    private func openSetup() {
        guard !trimmedName.isEmpty, !showingSetup, !isWorking else { return }
        step = .settings
        destinationURL = DestinationBookmark.load()
        templateID = manager.selectedTemplateID ?? manager.templates.first?.id
        createResolveProject = defaultCreateResolve
        resolveDatabaseID = defaultDatabaseID
        rawFolders = []
        statusMessage = ""
        connectionError = ""
        creationOutcome = ""
        showingSetup = true
    }

    private var setupSheet: some View {
        VStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 12) {
                HStack {
                    if step == .originals {
                        Button {
                            statusMessage = ""
                            step = .settings
                        } label: {
                            DoodleIcon(.chevronLeft).frame(width: 14, height: 14)
                                .frame(width: 36, height: 36).contentShape(Rectangle())
                        }
                        .buttonStyle(.plain).accessibilityLabel("返回").disabled(isWorking)
                    }
                    Spacer()
                    HStack(spacing: 8) {
                        Capsule().fill(step == .settings ? OrdinaTokens.cream : OrdinaTokens.success)
                            .frame(width: step == .settings ? 16 : 6, height: 6)
                        Capsule().fill(step == .originals ? OrdinaTokens.cream : OrdinaTokens.faint.opacity(0.45))
                            .frame(width: step == .originals ? 16 : 6, height: 6)
                    }
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel(step == .settings ? "第 1 步，共 2 步" : "第 2 步，共 2 步")
                    Spacer()
                    Button {
                        showingSetup = false
                    } label: {
                        DoodleIcon(.close).frame(width: 14, height: 14)
                            .frame(width: 36, height: 36).contentShape(Rectangle())
                    }
                    .buttonStyle(.plain).accessibilityLabel("取消新建").keyboardShortcut(.cancelAction)
                    .disabled(isWorking)
                }
                .foregroundStyle(OrdinaTokens.cream)
                Text(step == .settings ? "基础设定" : "素材目录")
                    .font(OrdinaTokens.type(28).weight(.semibold))
            }
            .padding(.horizontal, 28).padding(.top, 16).padding(.bottom, 20)
            GeometryReader { viewport in
                ScrollView {
                    VStack(alignment: .leading, spacing: 12) {
                        if step == .settings { settingsPanel } else { originalsPanel }
                        if !statusMessage.isEmpty {
                            Text(statusMessage).font(OrdinaTokens.type(13))
                                .foregroundStyle(OrdinaTokens.error).fixedSize(horizontal: false, vertical: true)
                                .textSelection(.enabled)
                        }
                    }
                    .padding(.horizontal, 28)
                    .padding(.bottom, step == .settings ? 16 : 0)
                    .frame(minHeight: step == .originals ? viewport.size.height : 0,
                           alignment: step == .originals ? .center : .top)
                }
                .scrollBounceBehavior(.basedOnSize)
            }
            .disabled(isWorking)
            PaperRule()
            HStack(spacing: 16) {
                if step == .originals {
                    Button("稍后设置") { submit(rawFolders: []) }
                        .buttonStyle(.plain).font(OrdinaTokens.type(14))
                        .foregroundStyle(OrdinaTokens.secondary)
                        .padding(.horizontal, 8).frame(height: 44)
                        .disabled(isWorking)
                }
                Spacer()
                let enabled = step == .settings ? canContinue : !rawFolders.isEmpty && !isWorking
                Button {
                    if step == .settings { advance() } else { submit(rawFolders: rawFolders) }
                } label: {
                    HStack(spacing: 8) {
                        if isWorking { ProgressView().controlSize(.small) }
                        Text(step == .settings ? "继续" : isWorking ? "新建中…" : "创建项目")
                        if !isWorking { DoodleIcon(.arrowRight).frame(width: 13, height: 13) }
                    }
                    .font(OrdinaTokens.type(14).weight(.semibold))
                    .foregroundStyle(enabled ? OrdinaTokens.bg : OrdinaTokens.faint)
                    .frame(width: 136, height: 44)
                    .background(enabled ? OrdinaTokens.cream : OrdinaTokens.selected, in: Capsule())
                    .contentShape(Capsule())
                }
                .buttonStyle(.plain).keyboardShortcut(.defaultAction).disabled(!enabled)
            }
            .padding(.horizontal, 28).padding(.vertical, 16)
        }
        .frame(width: 520, height: 460)
        .foregroundStyle(OrdinaTokens.ink).background(OrdinaTokens.bg)
        .preferredColorScheme(.dark).tint(OrdinaTokens.cream)
        .interactiveDismissDisabled(isWorking)
        .task { await checkResolve() }
    }

    private var settingsPanel: some View {
        setupPanel {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("保存位置").font(OrdinaTokens.type(13).weight(.medium))
                    Text(destinationURL?.path ?? "请选择文件夹")
                        .font(OrdinaTokens.type(12)).foregroundStyle(OrdinaTokens.secondary)
                        .lineLimit(1).truncationMode(.middle)
                        .help(destinationURL?.path ?? "选择项目目录")
                }
                Spacer(minLength: 12)
                Button(destinationURL == nil ? "选择…" : "更改…") { selectFolders(originals: false) }
                    .buttonStyle(PaperButtonStyle(compact: true))
            }
            .frame(minHeight: 44)
            PaperRule()
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("文件夹模板").font(OrdinaTokens.type(13).weight(.medium))
                    Text(manager.templates.first(where: { $0.id == templateID })?.displayName ?? "选择模板")
                        .font(OrdinaTokens.type(12)).foregroundStyle(OrdinaTokens.secondary)
                        .lineLimit(1).truncationMode(.middle)
                }
                Spacer(minLength: 12)
                Button("更改…") { showingTemplates.toggle() }
                    .buttonStyle(PaperButtonStyle(compact: true))
                    .accessibilityLabel("更改文件夹模板")
                    .sheet(isPresented: $showingTemplates) {
                        WorkflowTemplateEditor(manager: manager, projectTemplateID: templateID) { templateID = $0 }
                    }
            }
            .frame(minHeight: 44)
            PaperRule()
            HStack {
                Text("同步创建 Resolve 工程").font(OrdinaTokens.type(13).weight(.medium))
                    .accessibilityHidden(true)
                Spacer()
                Toggle("同步创建 Resolve 工程", isOn: $createResolveProject)
                    .labelsHidden()
                    .toggleStyle(.switch)
                    .tint(OrdinaTokens.cream)
            }
            .frame(minHeight: 44)
            if createResolveProject {
                PaperRule()
                ResolveLibraryRow(databases: databases, selection: resolveDatabaseID, connectedID: currentDatabaseID,
                                  checking: isChecking, status: libraryStatus) { showingLibraries = true }
                    .sheet(isPresented: $showingLibraries) {
                        ResolveLibraryPicker(databases: databases, selection: resolveDatabaseID, connectedID: currentDatabaseID,
                                             checking: isChecking, error: connectionError,
                                             refresh: { Task { await checkResolve() } }, choose: { resolveDatabaseID = $0 })
                    }
            }
        }
    }

    private var libraryStatus: String {
        if isChecking { return "检测中…" }
        if !connectionError.isEmpty { return connectionError }
        if !resolveDatabaseID.isEmpty && currentDatabaseID != resolveDatabaseID {
            return "请在 Resolve 切换到所选项目库。"
        }
        return ""
    }

    private var originalsPanel: some View {
        setupPanel {
            HStack {
                Image(systemName: "folder").font(OrdinaTokens.type(20)).foregroundStyle(OrdinaTokens.cream)
                Text("选择原始素材文件夹").font(OrdinaTokens.type(14).weight(.medium))
                Spacer()
                Button("添加目录…") { selectFolders(originals: true) }
                    .buttonStyle(PaperButtonStyle(compact: true))
            }
            .frame(minHeight: 44)
            ForEach(rawFolders, id: \.self) { folder in
                PaperRule()
                HStack(spacing: 12) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(folder.lastPathComponent).font(OrdinaTokens.type(13).weight(.medium)).lineLimit(1)
                        Text(folder.path).font(OrdinaTokens.type(12)).foregroundStyle(OrdinaTokens.secondary)
                            .lineLimit(1).truncationMode(.middle)
                    }
                    .help(folder.path)
                    Spacer()
                    Button {
                        rawFolders.removeAll { $0 == folder }
                    } label: {
                        DoodleIcon(.close).frame(width: 12, height: 12)
                            .frame(width: 36, height: 36).contentShape(Rectangle())
                    }
                    .buttonStyle(.plain).accessibilityLabel("移除" + folder.lastPathComponent)
                }
            }
        }
    }

    private func setupPanel<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8, content: content)
            .padding(16).frame(maxWidth: .infinity, alignment: .leading)
            .background(OrdinaTokens.surface, in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(OrdinaTokens.rule, lineWidth: 0.8))
    }

    private func selectFolders(originals: Bool) {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = originals
        panel.prompt = "选择"
        panel.title = originals ? "选择原始素材文件夹" : "选择保存位置"
        panel.directoryURL = originals ? rawFolders.first : destinationURL
        guard panel.runModal() == .OK else { return }
        if originals {
            for url in panel.urls {
                let folder = url.resolvingSymlinksInPath().standardizedFileURL
                if !rawFolders.contains(folder) { rawFolders.append(folder) }
            }
        } else {
            destinationURL = panel.url
        }
        statusMessage = ""
    }

    private func advance() {
        guard canContinue, let destinationURL else { return }
        do {
            _ = try ProjectNaming(name: projectName)
            try DestinationBookmark.validate(destinationURL)
            statusMessage = ""
            step = .originals
        } catch {
            statusMessage = error.localizedDescription
        }
    }

    private func submit(rawFolders: [URL]) {
        guard !isWorking else { return }
        Task { await createFolders(rawFolders: rawFolders) }
    }

    @MainActor
    private func checkResolve() async {
        guard !isChecking else { return }
        isChecking = true
        defer { isChecking = false }
        do {
            let data = try await ResolveService.call(action: "probe")
            guard !Task.isCancelled else { return }
            let probe = try JSONDecoder().decode(ResolveProbe.self, from: data)
            databases = probe.databases.filter { ["PostgreSQL", "Disk"].contains($0.DbType) }
            currentDatabaseID = probe.database.id
            if resolveDatabaseID.isEmpty {
                resolveDatabaseID = databases.first(where: { $0.id == probe.database.id })?.id ?? ""
            }
            connectionError = ""
        } catch {
            guard !Task.isCancelled else { return }
            databases = []
            currentDatabaseID = ""
            connectionError = error.localizedDescription
        }
    }

    @MainActor
    private func createFolders(rawFolders: [URL]) async {
        guard !isWorking, let baseURL = destinationURL else { return }
        do { try DestinationBookmark.validate(baseURL) }
        catch {
            statusMessage = "项目目录不可用，请检查磁盘连接与权限。"
            creationOutcome = "failure"
            return
        }
        isWorking = true
        creationOutcome = ""
        statusMessage = ""
        defer {
            if creationOutcome.isEmpty { creationOutcome = "failure" }
            isWorking = false
            archive.resumeAfterOperation()
        }
        do {
            try await archive.pauseForOperation()
        } catch {
            statusMessage = error.localizedDescription
            return
        }
        let accessing = baseURL.startAccessingSecurityScopedResource()
        defer {
            if accessing { baseURL.stopAccessingSecurityScopedResource() }
        }
        
        let naming: ProjectNaming
        do {
            naming = try ProjectNaming(name: projectName)
        } catch {
            statusMessage = error.localizedDescription
            return
        }
        let mainFolderName = naming.nasFolder
        let mainFolderURL = baseURL.appendingPathComponent(mainFolderName)
        
        let fileManager = FileManager.default
        
        let activeContent = manager.templates.first(where: { $0.id == templateID })?.content ?? manager.defaultContent
        let folderTemplateList = activeContent.components(separatedBy: .newlines)
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
        
        var nasCreated = false
        do {
            let rawPaths = try ProjectRawLocations.paths(rawFolders, projectRoot: mainFolderURL)
            if destinationURL != DestinationBookmark.load() {
                try DestinationBookmark.save(baseURL)
            }
            let database = databases.first(where: { $0.id == resolveDatabaseID })
            if createResolveProject {
                guard !fileManager.fileExists(atPath: mainFolderURL.appendingPathComponent(".magic-resolve.json").path) else {
                    throw ResolveFailure(message: "此项目目录已关联工程，请使用原工程或更换项目名。")
                }
                guard let database else {
                    throw ResolveFailure(message: "请检查连接并选择项目库。")
                }
                _ = try await ResolveService.call(action: "preflight", database: database, naming: naming)
            }
            for subFolder in folderTemplateList {
                let parts = subFolder.components(separatedBy: "/")
                guard !subFolder.hasPrefix("/"), !parts.contains(".."), !subFolder.contains("\0") else {
                    throw ResolveFailure(message: "模板包含无效路径，请编辑模板。")
                }
            }
            try fileManager.createDirectory(at: mainFolderURL, withIntermediateDirectories: true, attributes: nil)
            for subFolder in folderTemplateList {
                let subFolderURL = mainFolderURL.appendingPathComponent(subFolder)
                try fileManager.createDirectory(at: subFolderURL, withIntermediateDirectories: true, attributes: nil)
            }
            nasCreated = true
            try ProjectRawLocations.save(rawPaths, in: mainFolderURL)
            if createResolveProject, let database {
                let association = try await ResolveService.call(action: "create", database: database, naming: naming)
                try association.write(to: mainFolderURL.appendingPathComponent(".magic-resolve.json"), options: .atomic)
                archive.register(mainFolderURL)
            }
            defaultCreateResolve = createResolveProject
            defaultDatabaseID = resolveDatabaseID
            statusMessage = ""
            showingSetup = false
            creationOutcome = "success"
            Task { @MainActor in
                try? await Task.sleep(for: .seconds(2))
                if creationOutcome == "success" { creationOutcome = "" }
            }
            WorkspaceState.shared.recordUse(mainFolderURL)
            WorkspaceState.shared.refreshProjects()
            projectName = ""
            if createResolveProject {
                do {
                    _ = try await ResolveService.call(action: "open_new_project", projectRoot: mainFolderURL.path)
                    NSRunningApplication.runningApplications(withBundleIdentifier: "com.blackmagic-design.DaVinciResolve")
                        .first?.activate()
                } catch {
                    setupLaunchError = error.localizedDescription + "\n可在 Resolve 的脚本菜单打开 Project Setup。"
                    showingSetupLaunchError = true
                }
            } else {
                NSWorkspace.shared.open(mainFolderURL)
            }
        } catch {
            statusMessage = (nasCreated ? "项目目录已保留。" : "") + error.localizedDescription
        }
    }
}

#Preview("创建首页") {
    ProjectCreationView()
        .padding(32)
        .frame(width: 720, height: 520)
        .background(OrdinaHomeBackground())
        .preferredColorScheme(.dark)
}
