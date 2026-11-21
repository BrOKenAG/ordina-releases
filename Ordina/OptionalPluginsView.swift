import SwiftUI
import UniformTypeIdentifiers
import Darwin

// Convert the shared installer's status inside the worker. Only this explicitly
// Sendable value crosses the detached-task boundary in Swift 5 mode.
private enum OptionalPluginStatus: Equatable, Sendable {
    case checking, current, missing, outdated, unavailable

    var title: String {
        switch self {
        case .checking: return "检查中…"
        case .current: return "已安装"
        case .missing: return "未安装"
        case .outdated: return "可更新"
        case .unavailable: return "不可用"
        }
    }
}

private struct OptionalPluginRowState: Sendable {
    var status: OptionalPluginStatus = .checking
    var error: String?
    var installing = false
    var remoteEntry: PluginUpdates.Entry?

    var buttonTitle: String {
        if status == .unavailable { return "重试" }
        switch status {
        case .current: return "重新安装"
        case .outdated: return "更新"
        default: return "安装"
        }
    }
}

private enum OptionalPluginWorker {
    static func inspect(_ plugin: OptionalPlugin) -> OptionalPluginRowState {
        do {
            let status: OptionalPluginStatus
            switch try OptionalPluginInstaller.inspect(plugin) {
            case .current: status = .current
            case .missing: status = .missing
            case .outdated: status = .outdated
            }
            var result = OptionalPluginRowState(status: status)
            do {
                let entry = try PluginUpdates.fetch(plugin)
                let target = try OptionalPluginInstaller.scriptDirectory().appendingPathComponent(plugin.filename)
                let installed = (try? Data(contentsOf: target)).flatMap(PluginUpdates.version)
                let bundled = Bundle.main.url(forResource: plugin.resourceName, withExtension: "py")
                    .flatMap { try? Data(contentsOf: $0) }.flatMap(PluginUpdates.version)
                let reference = [installed, bundled].compactMap { $0 }.max { PluginUpdates.newer($1, than: $0) }
                if let reference, PluginUpdates.newer(entry.version, than: reference) {
                    result.remoteEntry = entry
                    if status != .missing { result.status = .outdated }
                }
            } catch PluginUpdates.Failure.notPublished {
                // Plugins without an online release continue using their bundled copy.
            } catch {
                // Offline installs still use the signed app's bundled copy.
                result.error = "无法检查在线更新"
            }
            return result
        } catch {
            return OptionalPluginRowState(status: .unavailable, error: error.localizedDescription)
        }
    }

    static func install(_ plugin: OptionalPlugin, entry: PluginUpdates.Entry?) -> OptionalPluginRowState {
        do {
            if let entry {
                // Re-check the signed manifest before downloading, and never overwrite a newer local version.
                let current = try PluginUpdates.fetch(plugin)
                guard current.version == entry.version, current.sha256 == entry.sha256, current.url == entry.url else {
                    throw PluginUpdates.Failure.invalid
                }
                let data = try PluginUpdates.script(current)
                let target = try OptionalPluginInstaller.scriptDirectory().appendingPathComponent(plugin.filename)
                if let local = (try? Data(contentsOf: target)).flatMap(PluginUpdates.version),
                   PluginUpdates.newer(local, than: current.version) { return inspect(plugin) }
                let temporary = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
                try FileManager.default.createDirectory(at: temporary, withIntermediateDirectories: true)
                defer { try? FileManager.default.removeItem(at: temporary) }
                let source = temporary.appendingPathComponent(plugin.filename)
                try data.write(to: source, options: .atomic)
                try OptionalPluginInstaller.install(plugin, source: source)
            } else {
                try OptionalPluginInstaller.install(plugin)
            }
            return inspect(plugin)
        } catch {
            var result = OptionalPluginRowState(status: .unavailable)
            result.error = "更新失败：\(error.localizedDescription)"
            return result
        }
    }
}

@MainActor
struct OptionalPluginsView: View {
    var inManager = false
    var refreshRevision = 0
    @State private var expanded = false
    @State private var rows: [OptionalPlugin: OptionalPluginRowState] = [:]
    @State private var requests: [OptionalPlugin: UUID] = [:]

    var body: some View {
        if inManager {
            pluginRows
                .task(id: refreshRevision) {
                    for plugin in OptionalPlugin.allCases { await refresh(plugin, silent: rows[plugin] != nil) }
                }
        } else {
            DisclosureGroup("更多插件", isExpanded: $expanded) {
                pluginRows.padding(.top, 10)
            }
            .task(id: expanded) {
                guard expanded else { return }
                for plugin in OptionalPlugin.allCases { await refresh(plugin) }
            }
        }
    }

    private var pluginRows: some View {
        VStack(alignment: .leading, spacing: inManager ? 0 : 12) {
            ForEach(OptionalPlugin.allCases) { plugin in
                row(plugin, state: rows[plugin] ?? OptionalPluginRowState())
                if inManager { PaperRule() }
            }
        }
    }

    private func row(_ plugin: OptionalPlugin, state: OptionalPluginRowState) -> some View {
        VStack(alignment: .leading, spacing: OrdinaTokens.space(6)) {
            HStack(spacing: OrdinaTokens.space(12)) {
                if inManager {
                    ResolvePluginLabel(title: plugin.name,
                                       summary: plugin == .yueliu ? "导入阅流审片意见" : "批量命名机位，创建多机位片段",
                                       symbol: plugin == .yueliu ? "text.bubble" : "video")
                        .help(plugin.dependencyHint ?? plugin.name)
                } else {
                    pluginName(plugin)
                }
                Spacer(minLength: OrdinaTokens.space(8))
                if state.installing {
                    ProgressView().controlSize(.small).tint(OrdinaTokens.accent)
                        .accessibilityLabel("正在安装 \(plugin.name)")
                }
                Text(state.installing ? "安装中…" : state.status.title)
                    .font(OrdinaTokens.type(12))
                    .foregroundStyle(state.status == .current && !state.installing ? OrdinaTokens.success : OrdinaTokens.secondary)
                if inManager && state.status == .missing && state.error == nil && !state.installing {
                    Button { performAction(plugin) } label: { Image(systemName: "plus") }
                        .buttonStyle(PaperQuietButton()).help("安装").accessibilityLabel("安装 " + plugin.name)
                } else if inManager && state.status == .current && state.error == nil && !state.installing {
                    Menu {
                        Button("在 Finder 中显示") {
                            if let directory = try? OptionalPluginInstaller.scriptDirectory() {
                                NSWorkspace.shared.activateFileViewerSelecting([directory.appendingPathComponent(plugin.filename)])
                            }
                        }
                        Button("重新安装") { performAction(plugin) }
                    } label: { Image(systemName: "ellipsis").frame(width: 28, height: 28) }
                    .menuStyle(.borderlessButton).menuIndicator(.hidden).fixedSize()
                    .accessibilityLabel(plugin.name + " 操作")
                } else if !inManager || (!state.installing && state.status != .checking) {
                    Button(state.buttonTitle) { performAction(plugin) }
                        .buttonStyle(PaperButtonStyle(compact: true))
                        .frame(minHeight: OrdinaTokens.height(36))
                        .disabled(state.installing || state.status == .checking)
                        .accessibilityLabel("\(state.buttonTitle) \(plugin.name)")
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

    @ViewBuilder
    private func pluginName(_ plugin: OptionalPlugin) -> some View {
        let label = Text(plugin.name).font(OrdinaTokens.type(14))
        if let hint = plugin.dependencyHint {
            label.help(hint)
        } else {
            label
        }
    }

    private func performAction(_ plugin: OptionalPlugin) {
        let state = rows[plugin] ?? OptionalPluginRowState()
        guard !state.installing, state.status != .checking else { return }
        if state.status == .unavailable {
            rows[plugin] = OptionalPluginRowState()
            Task { @MainActor in await refresh(plugin) }
            return
        }
        var pending = state
        pending.installing = true
        pending.error = nil
        rows[plugin] = pending
        let request = UUID()
        requests[plugin] = request
        Task { @MainActor in
            let result = await Task.detached(priority: .utility) {
                OptionalPluginWorker.install(plugin, entry: state.remoteEntry)
            }.value
            guard requests[plugin] == request else { return }
            rows[plugin] = result
        }
    }

    private func refresh(_ plugin: OptionalPlugin, silent: Bool = false) async {
        guard rows[plugin]?.installing != true else { return }
        let request = UUID()
        requests[plugin] = request
        if !silent { rows[plugin] = OptionalPluginRowState() }
        Task { @MainActor in
            try? await Task.sleep(for: .seconds(16))
            guard requests[plugin] == request else { return }
            requests[plugin] = nil
            rows[plugin] = OptionalPluginRowState(status: .unavailable, error: "检查超时，请重试。")
        }
        let result = await Task.detached(priority: .utility) {
            OptionalPluginWorker.inspect(plugin)
        }.value
        // An older inspection must not replace a newer install result, even
        // when that install has already finished by the time we resume.
        guard requests[plugin] == request else { return }
        requests[plugin] = nil
        rows[plugin] = result
    }
}

private struct ResolvePluginLabel: View {
    let title: String
    let summary: String?
    let symbol: String

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: symbol).font(.system(size: 17))
                .frame(width: 36, height: 36)
                .background(Paper.surface, in: RoundedRectangle(cornerRadius: 8))
                .foregroundStyle(Paper.accent)
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

@MainActor
struct ResolvePluginsPage: View {
    @Environment(\.dismiss) private var dismiss
    @State private var selecting = false
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
                    OptionalPluginsView(inManager: true, refreshRevision: revision)
                    if !localScripts.isEmpty {
                        sectionTitle("本机脚本").padding(.top, 18)
                        ForEach(localScripts) { script in
                            HStack {
                                ResolvePluginLabel(title: script.name, summary: script.summary ?? "Resolve 用户脚本", symbol: "doc.text")
                                Spacer()
                                Text("已安装").font(Paper.font(12)).foregroundStyle(Paper.success)
                                Menu {
                                    Button("在 Finder 中显示") { NSWorkspace.shared.activateFileViewerSelecting([script.url]) }
                                    Button("移出 Resolve…") { removing = script }
                                } label: { Image(systemName: "ellipsis").frame(width: 28, height: 28) }
                                .menuStyle(.borderlessButton).menuIndicator(.hidden).fixedSize()
                                .disabled(busy).accessibilityLabel(script.name + " 操作")
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
        .task { await watchScripts() }
    }

    private func sectionTitle(_ title: String) -> some View {
        Text(title).font(Paper.font(12).weight(.medium)).foregroundStyle(Paper.secondary)
            .padding(.bottom, 4)
    }

    private var integrationRow: some View {
        HStack {
            ResolvePluginLabel(title: "Project Setup", summary: "初始化时间线，查看素材归档状态", symbol: "slider.horizontal.3")
            Spacer()
            if integration == .current {
                Text("已安装").font(Paper.font(12)).foregroundStyle(Paper.success)
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
            } else if integration == .missing {
                Button(action: installIntegration) { Image(systemName: "plus") }
                    .buttonStyle(PaperQuietButton()).disabled(busy)
                    .help("安装").accessibilityLabel("安装 Project Setup")
            } else if integration == .outdated {
                Button("更新", action: installIntegration)
                    .buttonStyle(PaperButtonStyle(compact: true)).disabled(busy)
            }
        }
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

private extension Result where Failure == Error {
    var failureDescription: String? {
        if case .failure(let error) = self { return error.localizedDescription }
        return nil
    }
}
