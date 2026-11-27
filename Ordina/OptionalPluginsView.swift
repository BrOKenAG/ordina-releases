import SwiftUI
import AppKit
import Combine
import UniformTypeIdentifiers
import Darwin

// The plugin centre renders whatever the signed registry describes. There is
// no per-plugin branch here: a new plugin appears with its own name, summary,
// icon and capabilities without changing or rebuilding this file.
private enum PluginRowStatus: Equatable, Sendable {
    case loading, current, missing, outdated, unmanaged, incompatible

    var title: String {
        switch self {
        case .loading: return "检查中…"
        case .current: return "已安装"
        case .missing: return "未安装"
        case .outdated: return "可更新"
        case .unmanaged: return "已安装（未托管）"
        case .incompatible: return "需更新 ordina"
        }
    }
}

private struct PluginRowState: Sendable {
    var status: PluginRowStatus = .loading
    var error: String?
    var installing = false

    var buttonTitle: String {
        switch status {
        case .current: return "重新安装"
        case .outdated: return "更新"
        case .unmanaged: return "接管"
        default: return "安装"
        }
    }

    var canAct: Bool {
        status == .missing || status == .outdated || status == .unmanaged || status == .current
    }
}

@MainActor
private final class PluginCenterModel: ObservableObject {
    @Published var items: [PluginCatalog.Item] = []
    @Published var rows: [String: PluginRowState] = [:]
    @Published var loadError: String?
    @Published var loading = false
    private var requests: [String: UUID] = [:]

    func load(silent: Bool = false) async {
        if !silent { loading = true }
        let result = await Task.detached(priority: .utility) {
            PluginOperations.load(forceRefresh: true)
        }.value
        items = result.items
        loadError = result.registryError
        for item in result.items where rows[item.id]?.installing != true {
            rows[item.id] = PluginRowState(status: Self.status(item.status), error: item.error)
        }
        loading = false
    }

    func perform(_ item: PluginCatalog.Item, action: @escaping @Sendable () throws -> Void,
                 onSuccess: (() -> Void)? = nil) {
        guard rows[item.id]?.installing != true else { return }
        var pending = rows[item.id] ?? PluginRowState()
        pending.installing = true
        pending.error = nil
        rows[item.id] = pending
        let request = UUID()
        requests[item.id] = request
        Task { @MainActor in
            let error: String? = await Task.detached(priority: .utility) {
                do { try action(); return nil }
                catch { return error.localizedDescription }
            }.value
            guard requests[item.id] == request else { return }
            requests[item.id] = nil
            await load(silent: true)
            if let error {
                // load() above has already refreshed the row from the real
                // installation state. A failed action must never fabricate
                // "已安装（未托管）" for an uninstalled plugin.
                rows[item.id]?.installing = false
                rows[item.id]?.error = error
            } else {
                rows[item.id]?.installing = false
                onSuccess?()
            }
        }
    }

    func clearError(_ id: String) {
        rows[id]?.error = nil
    }

    static func status(_ value: PluginInstallStatus) -> PluginRowStatus {
        switch value {
        case .current: return .current
        case .updateAvailable: return .outdated
        case .unmanaged: return .unmanaged
        case .incompatible: return .incompatible
        case .notInstalled: return .missing
        }
    }
}

@MainActor
struct OptionalPluginsView: View {
    var inManager = false
    var refreshRevision = 0
    var onPublish: ((PluginPublishing.Source) -> Void)? = nil
    @State private var expanded = false
    @StateObject private var model = PluginCenterModel()

    var body: some View {
        if inManager {
            pluginRows
                .task(id: refreshRevision) { await model.load() }
        } else {
            DisclosureGroup("更多插件", isExpanded: $expanded) {
                pluginRows.padding(.top, 10)
            }
            .task(id: expanded) {
                guard expanded else { return }
                await model.load()
            }
        }
    }

    private var pluginRows: some View {
        VStack(alignment: .leading, spacing: inManager ? 0 : 12) {
            if let error = model.loadError {
                Text(error)
                    .font(OrdinaTokens.type(12))
                    .foregroundStyle(OrdinaTokens.error)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if model.items.isEmpty && model.loading {
                HStack(spacing: 10) {
                    ProgressView().controlSize(.small)
                    Text("正在读取插件目录…")
                        .font(OrdinaTokens.type(12)).foregroundStyle(OrdinaTokens.secondary)
                }
                .frame(minHeight: 44)
            } else if model.items.isEmpty && model.loadError == nil {
                Text("暂无可用插件。")
                    .font(OrdinaTokens.type(12)).foregroundStyle(OrdinaTokens.secondary)
                    .frame(minHeight: 44)
            }
            ForEach(model.items) { item in
                row(item, state: model.rows[item.id] ?? PluginRowState())
                if inManager { PaperRule() }
            }
        }
    }

    private func row(_ item: PluginCatalog.Item, state: PluginRowState) -> some View {
        VStack(alignment: .leading, spacing: OrdinaTokens.space(6)) {
            HStack(spacing: OrdinaTokens.space(12)) {
                if inManager {
                    ResolvePluginLabel(title: item.name, summary: item.summary,
                                       symbol: item.symbol, iconID: item.iconID)
                } else {
                    Text(item.name).font(OrdinaTokens.type(14))
                        .help(item.summary)
                }
                Spacer(minLength: OrdinaTokens.space(8))
                PluginRowTrailing(
                    status: state.installing ? "安装中…"
                        : (item.isWithdrawn ? "已下架"
                           : (item.isLocalOnly ? "本机记录" : state.status.title)),
                    highlighted: state.status == .current && !state.installing
                        && !item.isWithdrawn && !item.isLocalOnly
                ) {
                    if state.installing {
                        ProgressView().controlSize(.small).tint(OrdinaTokens.accent)
                            .accessibilityLabel("正在安装 \(item.name)")
                    } else if state.status == .incompatible {
                        Text("≥ \(item.minClientVersion)")
                            .font(OrdinaTokens.type(11))
                            .foregroundStyle(OrdinaTokens.secondary)
                    } else if inManager && state.status == .missing {
                        Button { install(item) } label: { Image(systemName: "plus") }
                            .buttonStyle(PaperQuietButton()).help("安装")
                            .accessibilityLabel("安装 " + item.name)
                            .disabled(!item.canInstall || state.error != nil)
                    } else if !inManager || state.status == .outdated || state.status == .unmanaged {
                        Button(state.buttonTitle) { install(item) }
                            .buttonStyle(PaperButtonStyle(compact: true))
                            .frame(minHeight: OrdinaTokens.height(36))
                            .disabled(state.status == .loading
                                      || state.status == .incompatible || state.error != nil)
                            .accessibilityLabel("\(state.buttonTitle) \(item.name)")
                    } else {
                        EmptyView()
                    }
                } menu: {
                    if item.isWithdrawn && item.isInstalled && !state.installing {
                        Menu {
                            Button("在 Finder 中显示") { reveal(item) }
                            Button("卸载…") { confirmUninstall(item) }
                        } label: { Image(systemName: "ellipsis").frame(width: 28, height: 28) }
                        .menuStyle(.borderlessButton).menuIndicator(.hidden).fixedSize()
                        .accessibilityLabel(item.name + " 操作")
                    } else if inManager && state.status == .current && !state.installing {
                        Menu {
                            Button("在 Finder 中显示") { reveal(item) }
                            if item.needsBrowserExtension {
                                Button("准备浏览器扩展") { prepareBrowserExtension(item) }
                            }
                            if let onPublish, PluginPublishing.canAuthor(),
                               PluginPublishing.canPackage(.installed(item.id)) {
                                Button("上架插件") { onPublish(.installed(item.id)) }
                                Divider()
                            }
                            Button("重新安装") { install(item) }
                            Divider()
                            Button("卸载…") { confirmUninstall(item) }
                        } label: { Image(systemName: "ellipsis").frame(width: 28, height: 28) }
                        .menuStyle(.borderlessButton).menuIndicator(.hidden).fixedSize()
                        .accessibilityLabel(item.name + " 操作")
                    } else {
                        EmptyView()
                    }
                }
            }
            if let error = state.error {
                Text(error)
                    .font(OrdinaTokens.type(12))
                    .foregroundStyle(OrdinaTokens.error)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func install(_ item: PluginCatalog.Item) {
        guard item.canInstall else { return }
        model.clearError(item.id)
        let wasMissing = model.rows[item.id]?.status != .current
        model.perform(item, action: {
            try PluginOperations.install(item)
        }, onSuccess: {
            if wasMissing && item.needsBrowserExtension {
                prepareBrowserExtension(item)
            }
        })
    }

    private func confirmUninstall(_ item: PluginCatalog.Item) {
        let alert = NSAlert()
        alert.messageText = "卸载 \(item.name)？"
        alert.informativeText = "将移除插件写入的文件；用户数据和设置会保留。"
        alert.addButton(withTitle: "卸载")
        alert.addButton(withTitle: "取消")
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        model.perform(item, action: {
            try PluginOperations.uninstall(item)
        })
    }

    private func reveal(_ item: PluginCatalog.Item) {
        let environment = PluginEnvironment.current
        if let descriptor = item.descriptor,
           let extensionDirectory = PluginInstaller.extensionDirectory(descriptor,
                                                                       environment: environment),
           FileManager.default.fileExists(atPath: extensionDirectory.path) {
            NSWorkspace.shared.activateFileViewerSelecting([extensionDirectory])
            return
        }
        if let descriptor = item.descriptor {
            let target = PluginInstaller.utilityTarget(descriptor, environment: environment)
            if FileManager.default.fileExists(atPath: target.path) {
                NSWorkspace.shared.activateFileViewerSelecting([target])
                return
            }
            if let support = PluginInstaller.supportDirectory(descriptor, environment: environment),
               FileManager.default.fileExists(atPath: support.path) {
                NSWorkspace.shared.activateFileViewerSelecting([support])
                return
            }
        }
        if let directory = try? OptionalPluginInstaller.scriptDirectory() {
            NSWorkspace.shared.activateFileViewerSelecting([directory])
        }
    }

    // Chrome blocks every silent install path for unpacked extensions. One tap
    // still does everything ordina can: copy the path, open the extensions page
    // in Chrome itself, and reveal the folder for drag-and-drop.
    private func prepareBrowserExtension(_ item: PluginCatalog.Item) {
        guard let descriptor = item.descriptor,
              let directory = PluginInstaller.extensionDirectory(descriptor),
              FileManager.default.fileExists(atPath: directory.appendingPathComponent("manifest.json").path) else {
            let alert = NSAlert()
            alert.messageText = "扩展文件尚未安装"
            alert.informativeText = "请先在插件中心完成 \(item.name) 的安装。"
            alert.addButton(withTitle: "好")
            alert.runModal()
            return
        }
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        pasteboard.setString(directory.path, forType: .string)
        NSWorkspace.shared.activateFileViewerSelecting([directory])
        if let chrome = NSWorkspace.shared.urlForApplication(withBundleIdentifier: "com.google.Chrome"),
           let url = URL(string: "chrome://extensions") {
            let configuration = NSWorkspace.OpenConfiguration()
            configuration.activates = true
            NSWorkspace.shared.open([url], withApplicationAt: chrome, configuration: configuration, completionHandler: nil)
        } else if let url = URL(string: "chrome://extensions") {
            NSWorkspace.shared.open(url)
        }
        let alert = NSAlert()
        alert.messageText = "浏览器扩展只剩两步"
        alert.informativeText = "扩展文件夹已在 Finder 中选中，路径也已复制。\n\n1. 在刚打开的 Chrome 扩展程序页面右上角开启「开发者模式」。\n2. 点「加载已解压的扩展程序」，按 ⌘⇧G 粘贴已复制的路径并确认。\n\n也可以直接把 Finder 中选中的 extension 文件夹拖进页面。设置一次即可，之后更新插件无需重复。"
        alert.addButton(withTitle: "好")
        alert.runModal()
    }
}

private struct ResolvePluginLabel: View {
    let title: String
    let summary: String?
    let symbol: String
    var iconID: String? = nil

    var body: some View {
        HStack(spacing: 12) {
            Group {
                if let icon = PluginIcon.resolve(iconID) {
                    DoodleIcon(.pluginIcon(icon))
                } else {
                    Image(systemName: symbol).font(.system(size: 17))
                        .foregroundStyle(Paper.accent)
                }
            }
            .frame(width: 36, height: 36)
            .background(Paper.surface, in: RoundedRectangle(cornerRadius: 8))
            .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(Paper.font(14).weight(.medium)).lineLimit(1)
                if let summary {
                    Text(summary).font(Paper.font(12)).foregroundStyle(Paper.secondary).lineLimit(2)
                }
            }
        }.frame(minHeight: 62)
    }
}

/// One trailing layout for every plugin row: fixed-width status text, a fixed
/// action slot and a fixed overflow-menu slot. Rows never compensate with
/// per-row offsets or extra padding.
private struct PluginRowTrailing<Action: View, Menu: View>: View {
    let status: String
    let highlighted: Bool
    private let action: () -> Action
    private let menu: () -> Menu

    init(status: String, highlighted: Bool,
         @ViewBuilder action: @escaping () -> Action,
         @ViewBuilder menu: @escaping () -> Menu) {
        self.status = status
        self.highlighted = highlighted
        self.action = action
        self.menu = menu
    }

    var body: some View {
        HStack(spacing: 8) {
            Text(status)
                .font(Paper.font(12))
                .foregroundStyle(highlighted ? OrdinaTokens.success : OrdinaTokens.secondary)
                .lineLimit(1)
                .frame(width: 96, alignment: .trailing)
            action()
                .frame(minWidth: 72, alignment: .trailing)
            menu()
                .frame(width: 28, height: 28)
        }
    }
}

private struct PublishDraft: Identifiable {
    let id = UUID()
    let source: PluginPublishing.Source
}

@MainActor
struct ResolvePluginsPage: View {
    var onPublish: (PluginPublishing.Launch) -> Void = { _ in }
    @Environment(\.dismiss) private var dismiss
    @State private var selecting = false
    @State private var publishDraft: PublishDraft?
    @State private var publishInfo = PluginPublishing.Info()
    @State private var publishError = ""
    @State private var packaging = false
    @State private var targeted = false
    @State private var importRequest: OptionalPluginInstaller.Import?
    @State private var removing: OptionalPluginInstaller.LocalScript?
    @State private var localScripts: [OptionalPluginInstaller.LocalScript] = []
    @State private var integration: IntegrationInstaller.Status?
    @State private var revision = 0
    @State private var busy = false
    @State private var error = ""
    @State private var listingError = ""
    private let pythonType = UTType(filenameExtension: "py") ?? .data

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            HStack {
                Text("Resolve 插件").font(Paper.font(24).weight(.semibold))
                Spacer()
                Button("选择文件…") { selecting = true }
                    .buttonStyle(PaperButtonStyle(compact: true)).disabled(busy)
                Button { dismiss() } label: { Image(systemName: "xmark") }
                    .buttonStyle(PaperQuietButton()).help("关闭").accessibilityLabel("关闭")
                    .keyboardShortcut(.cancelAction)
            }
            if !error.isEmpty { Text(error).foregroundStyle(Paper.error) }
            if !listingError.isEmpty { Text(listingError).foregroundStyle(Paper.error) }
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    sectionTitle("ordina 内置")
                    integrationRow
                    PaperRule()
                    OptionalPluginsView(inManager: true, refreshRevision: revision) { beginPublish($0) }
                    if !localScripts.isEmpty {
                        sectionTitle("本机脚本").padding(.top, 18)
                        ForEach(localScripts) { script in
                            HStack {
                                ResolvePluginLabel(title: script.name, summary: script.summary ?? "Resolve 用户脚本", symbol: "doc.text")
                                Spacer()
                                // A local script is neither formally managed nor
                                // published; the label says so explicitly.
                                PluginRowTrailing(status: "本机脚本", highlighted: false) {
                                    EmptyView()
                                } menu: {
                                    Menu {
                                        Button("在 Finder 中显示") { NSWorkspace.shared.activateFileViewerSelecting([script.url]) }
                                        if PluginPublishing.canAuthor() {
                                            Button("上架插件") { beginPublish(.localScript(script.url)) }
                                        }
                                        Button("移出 Resolve…") { removing = script }
                                    } label: { Image(systemName: "ellipsis").frame(width: 28, height: 28) }
                                    .menuStyle(.borderlessButton).menuIndicator(.hidden).fixedSize()
                                    .disabled(busy).accessibilityLabel(script.name + " 操作")
                                }
                            }
                            PaperRule()
                        }
                    }
                }
            }.scrollBounceBehavior(.basedOnSize)
        }
        .padding(28).frame(width: 570, height: 515)
        .foregroundStyle(Paper.ink).background(Paper.background)
        .contentShape(Rectangle())
        .overlay {
            if targeted {
                RoundedRectangle(cornerRadius: 16).strokeBorder(Paper.accent, lineWidth: 2)
                    .overlay(alignment: .bottom) {
                        Text("松开以导入").padding(9).background(Paper.surface, in: Capsule()).padding(12)
                    }.allowsHitTesting(false)
            }
        }
        .dropDestination(for: URL.self) { urls, _ in
            guard !busy else { return false }
            guard urls.count == 1, let url = urls.first else {
                error = "请一次拖入一个 Python 脚本。"
                return false
            }
            prepare(url)
            return true
        } isTargeted: { targeted = $0 && !busy }
        .fileImporter(isPresented: $selecting, allowedContentTypes: [pythonType]) { result in
            switch result {
            case .success(let url): prepare(url)
            case .failure(let error): self.error = error.localizedDescription
            }
        }
        .sheet(item: $importRequest) { pending in
            confirmation(pending.existing == nil ? "安装脚本" : "替换同名脚本",
                         name: pending.source.lastPathComponent, path: pending.destination.path,
                         action: pending.existing == nil ? "安装" : "备份并替换",
                         cancel: { importRequest = nil }) {
                importRequest = nil
                perform { try OptionalPluginInstaller.install(pending) }
            }
        }
        .sheet(item: $removing) { script in
            confirmation("移出 Resolve", name: script.url.lastPathComponent, path: "脚本将移至 ordina 备份。",
                         action: "移出", cancel: { removing = nil }) {
                removing = nil
                perform { try OptionalPluginInstaller.removeLocalScript(script) }
            }
        }
        .sheet(item: $publishDraft) { draft in
            publishForm(draft)
        }
        .task { await watchScripts() }
        .onReceive(NotificationCenter.default.publisher(for: PluginInstaller.didChangeNotification)) { _ in
            Task { await refresh() }
        }
    }

    private func sectionTitle(_ title: String) -> some View {
        Text(title).font(Paper.font(12).weight(.medium)).foregroundStyle(Paper.secondary)
            .padding(.bottom, 4)
    }

    private var integrationRow: some View {
        HStack {
            ResolvePluginLabel(title: "Project Setup", summary: "初始化时间线，查看素材归档状态", symbol: "slider.horizontal.3")
            Spacer()
            PluginRowTrailing(status: integrationStatus, highlighted: integration == .current) {
                if integration == .missing {
                    Button(action: installIntegration) { Image(systemName: "plus") }
                        .buttonStyle(PaperQuietButton()).disabled(busy)
                        .help("安装").accessibilityLabel("安装 Project Setup")
                } else if integration == .outdated {
                    Button("更新", action: installIntegration)
                        .buttonStyle(PaperButtonStyle(compact: true)).disabled(busy)
                } else {
                    EmptyView()
                }
            } menu: {
                if integration == .current {
                    Menu {
                        Button("在 Finder 中显示") {
                            if let directory = try? OptionalPluginInstaller.scriptDirectory() {
                                NSWorkspace.shared.activateFileViewerSelecting([directory.appendingPathComponent("Project Setup.py")])
                            }
                        }
                        Button("重新安装", action: installIntegration)
                    } label: { Image(systemName: "ellipsis").frame(width: 28, height: 28) }
                    .menuStyle(.borderlessButton).menuIndicator(.hidden).fixedSize()
                    .disabled(busy).accessibilityLabel("Project Setup 操作")
                } else {
                    EmptyView()
                }
            }
        }
    }

    private var integrationStatus: String {
        if integration == .current { return "已安装" }
        if integration == .missing { return "未安装" }
        if integration == .outdated { return "可更新" }
        return "检查中…"
    }

    private func confirmation(_ title: String, name: String, path: String, action: String,
                              cancel: @escaping () -> Void, confirm: @escaping () -> Void) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(title).font(Paper.font(21).weight(.semibold))
            Text(name).textSelection(.enabled)
            Text(path).font(Paper.font(12)).foregroundStyle(Paper.secondary).textSelection(.enabled)
            HStack {
                Spacer()
                Button("取消", action: cancel).buttonStyle(PaperButtonStyle()).keyboardShortcut(.cancelAction)
                Button(action, action: confirm).buttonStyle(PaperButtonStyle(primary: true))
            }
        }.padding(24).frame(width: 460).background(Paper.background)
    }

    private func prepare(_ url: URL) {
        guard !busy, importRequest == nil else { return }
        do { importRequest = try OptionalPluginInstaller.prepareImport(url); error = "" }
        catch { self.error = error.localizedDescription }
    }

    private func installIntegration() { perform { try IntegrationInstaller.install() } }

    private func perform(_ operation: @escaping @Sendable () throws -> Void) {
        guard !busy else { return }
        busy = true
        error = ""
        Task {
            do { try await Task.detached(priority: .utility, operation: operation).value }
            catch { self.error = error.localizedDescription }
            busy = false
            await refresh()
        }
    }

    private func refresh() async {
        let result = await Task.detached(priority: .utility) {
            (Result { try IntegrationInstaller.inspect() }, Result { try OptionalPluginInstaller.localScripts() })
        }.value
        integration = try? result.0.get()
        localScripts = (try? result.1.get()) ?? []
        let errors = [result.0.failureDescription, result.1.failureDescription].compactMap { $0 }
        listingError = Array(Set(errors)).sorted().joined(separator: "\n")
        revision += 1
    }

    private func watchScripts() async {
        await refresh()
        while !Task.isCancelled {
            guard let directory = try? OptionalPluginInstaller.scriptDirectory() else { return }
            var watched = directory
            while !FileManager.default.fileExists(atPath: watched.path), watched.path != "/" {
                watched.deleteLastPathComponent()
            }
            let fd = open(watched.path, O_EVTONLY)
            guard fd >= 0 else { return }
            let (events, continuation) = AsyncStream<Void>.makeStream(bufferingPolicy: .bufferingNewest(1))
            let source = DispatchSource.makeFileSystemObjectSource(
                fileDescriptor: fd, eventMask: [.write, .rename, .delete, .attrib], queue: .global(qos: .utility))
            source.setEventHandler { continuation.yield(()) }
            source.setCancelHandler { close(fd) }
            continuation.onTermination = { _ in source.cancel() }
            source.resume()
            for await _ in events {
                break
            }
            source.cancel()
            continuation.finish()
            guard !Task.isCancelled else { return }
            try? await Task.sleep(for: .milliseconds(120))
            guard !Task.isCancelled else { return }
            await refresh()
        }
    }
}

private extension ResolvePluginsPage {
    func beginPublish(_ source: PluginPublishing.Source) {
        guard PluginPublishing.canAuthor() else { return }
        do {
            publishInfo = try PluginPublishing.suggestedInfo(source)
            publishError = ""
            publishDraft = PublishDraft(source: source)
        } catch {
            self.error = error.localizedDescription
        }
    }

    func publishForm(_ draft: PublishDraft) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("上架插件").font(Paper.font(21).weight(.semibold))
            Text("将从实际安装路径收集必要文件并生成标准 ZIP；发布信息确认后进入开发者中心完成认证、签名和发布事务。")
                .font(Paper.font(12)).foregroundStyle(Paper.secondary)
                .fixedSize(horizontal: false, vertical: true)
            field("插件标识", text: $publishInfo.id)
            field("名称", text: $publishInfo.name)
            field("版本", text: $publishInfo.version)
            PluginIconPicker(iconID: $publishInfo.iconID)
            field("最低客户端", text: $publishInfo.minClientVersion)
            VStack(alignment: .leading, spacing: 4) {
                Text("简介").font(Paper.font(11)).foregroundStyle(Paper.secondary)
                TextEditor(text: $publishInfo.summary)
                    .font(Paper.font(12)).frame(height: 52)
                    .overlay { RoundedRectangle(cornerRadius: 6).strokeBorder(Paper.rule, lineWidth: 0.8) }
            }
            if !publishError.isEmpty {
                Text(publishError).font(Paper.font(12)).foregroundStyle(Paper.error)
                    .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
            }
            HStack {
                Spacer()
                Button("取消") { publishDraft = nil }.buttonStyle(PaperButtonStyle())
                Button("生成并继续") { package(draft) }
                    .buttonStyle(PaperButtonStyle(primary: true)).disabled(packaging)
            }
        }.padding(24).frame(width: 480).background(Paper.background)
    }

    func field(_ title: String, text: Binding<String>) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(Paper.font(11)).foregroundStyle(Paper.secondary)
            TextField("", text: text).font(Paper.font(12)).textFieldStyle(.roundedBorder)
        }
    }

    func package(_ draft: PublishDraft) {
        guard !packaging else { return }
        packaging = true
        publishError = ""
        let info = publishInfo
        Task {
            do {
                let prepared = try await Task.detached(priority: .utility) {
                    try PluginPublishing.prepare(info, source: draft.source)
                }.value
                publishDraft = nil
                packaging = false
                onPublish(PluginPublishing.Launch(prepared: prepared))
            } catch {
                publishError = error.localizedDescription
                packaging = false
            }
        }
    }
}

private extension Result where Failure == Error {
    var failureDescription: String? {
        if case .failure(let error) = self { return error.localizedDescription }
        return nil
    }
}
