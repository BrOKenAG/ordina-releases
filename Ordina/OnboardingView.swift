import SwiftUI
import AppKit

@MainActor
struct OnboardingView: View {
    @ObservedObject private var controller: OnboardingController
    @State private var showingManualSetup = false
    @State private var showingLibraries = false

    init(controller: OnboardingController? = nil) {
        self.controller = controller ?? .shared
    }

    var body: some View {
        VStack(spacing: 0) {
            navigation
            GeometryReader { geometry in
                ScrollView {
                    VStack(alignment: .leading, spacing: OrdinaTokens.space(24)) {
                        Text(title).font(OrdinaTokens.type(32).weight(.semibold))
                        switch controller.step {
                        case .projectRoot: projectRoot
                        case .resolveLibrary: resolveLibrary
                        case .projectSetup: projectSetup
                        }
                    }
                    .frame(maxWidth: 560, alignment: .leading)
                    .padding(OrdinaTokens.space(24))
                    .frame(maxWidth: .infinity, minHeight: geometry.size.height, alignment: .center)
                }
            }
            footer
        }
        .foregroundStyle(OrdinaTokens.ink)
        .background(OrdinaTokens.bg)
        .preferredColorScheme(.dark)
        .tint(OrdinaTokens.cream)
        .frame(minWidth: OrdinaTokens.homeWidth, minHeight: 560)
        .onChange(of: controller.step) { _, _ in showingManualSetup = false }
    }

    private var navigation: some View {
        HStack {
            Button { controller.back() } label: {
                DoodleIcon(.chevronLeft).frame(width: 15, height: 15)
                    .foregroundStyle(OrdinaTokens.cream)
                    .frame(width: OrdinaTokens.height(44), height: OrdinaTokens.height(44))
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(controller.step == .projectRoot || controller.installing)
            .accessibilityLabel("返回")
            Spacer()
            HStack(spacing: OrdinaTokens.space(8)) {
                ForEach(OnboardingStep.allCases, id: \.rawValue) { step in
                    Capsule().fill(step == controller.step ? OrdinaTokens.cream : (controller.status(for: step) == .completed ? OrdinaTokens.success : OrdinaTokens.faint.opacity(0.35)))
                        .frame(width: step == controller.step ? 16 : 6, height: 6)
                }
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("第 \(controller.step.rawValue + 1) 步，共 3 步")
            Spacer()
            Button("稍后设置") { controller.deferSetup() }
                .buttonStyle(.plain).font(OrdinaTokens.type(14))
                .foregroundStyle(OrdinaTokens.secondary)
                .frame(height: OrdinaTokens.height(44))
                .disabled(controller.installing)
        }
        .padding(.horizontal, OrdinaTokens.space(24)).padding(.top, OrdinaTokens.space(16))
    }

    private var projectRoot: some View {
        VStack(alignment: .leading, spacing: OrdinaTokens.space(16)) {
            panel {
                HStack(spacing: OrdinaTokens.space(16)) {
                    Image(systemName: "folder").font(OrdinaTokens.type(28).weight(.light))
                        .foregroundStyle(OrdinaTokens.cream)
                    VStack(alignment: .leading, spacing: OrdinaTokens.space(6)) {
                        Text(controller.destinationURL?.lastPathComponent ?? "项目文件夹")
                            .font(OrdinaTokens.type(16).weight(.medium)).lineLimit(1)
                        if let url = controller.destinationURL {
                            Text(url.path).font(OrdinaTokens.type(12)).foregroundStyle(OrdinaTokens.secondary)
                                .lineLimit(2).textSelection(.enabled)
                        } else {
                            Text("本机、外接硬盘或 NAS").font(OrdinaTokens.type(12)).foregroundStyle(OrdinaTokens.secondary)
                        }
                    }
                    Spacer(minLength: OrdinaTokens.space(8))
                    Button(controller.destinationURL == nil ? "选择文件夹" : "更改") { chooseFolder() }
                        .buttonStyle(PaperButtonStyle())
                }
            }
            if !controller.destinationError.isEmpty {
                Text(controller.destinationError).font(OrdinaTokens.type(12)).foregroundStyle(OrdinaTokens.error)
                refreshButton
            }
        }
    }

    private var resolveLibrary: some View {
        VStack(alignment: .leading, spacing: OrdinaTokens.space(16)) {
            panel {
                VStack(alignment: .leading, spacing: OrdinaTokens.space(20)) {
                    HStack {
                        Text("同时创建 Resolve 工程")
                            .accessibilityHidden(true)
                        Spacer()
                        Toggle("同时创建 Resolve 工程", isOn: Binding(
                            get: { controller.resolveEnabled },
                            set: { controller.setResolveEnabled($0) }
                        ))
                        .labelsHidden().toggleStyle(.switch).tint(OrdinaTokens.cream)
                    }
                    if controller.resolveEnabled {
                        PaperRule()
                        ResolveLibraryRow(databases: controller.databases, selection: controller.selectedDatabaseID,
                                          connectedID: controller.activeDatabase?.id ?? "", checking: controller.isDetecting,
                                          status: controller.libraryStatus) { showingLibraries = true }
                            .sheet(isPresented: $showingLibraries) {
                                ResolveLibraryPicker(databases: controller.databases, selection: controller.selectedDatabaseID,
                                                     connectedID: controller.activeDatabase?.id ?? "", checking: controller.isDetecting,
                                                     error: controller.libraryError, refresh: { Task { await controller.refresh() } },
                                                     choose: controller.selectDatabase)
                            }
                    }
                }
            }
            DisclosureGroup("手动配置", isExpanded: $showingManualSetup) {
                Text("在 Resolve 的项目管理器中添加或连接已有的本地或 PostgreSQL 项目库，选中后返回刷新。")
                    .font(OrdinaTokens.type(12)).foregroundStyle(OrdinaTokens.secondary).padding(.top, OrdinaTokens.space(10))
            }
            .font(OrdinaTokens.type(12))
            .foregroundStyle(OrdinaTokens.secondary)
        }
    }

    private var projectSetup: some View {
        VStack(alignment: .leading, spacing: OrdinaTokens.space(16)) {
            panel {
                HStack(alignment: .center, spacing: OrdinaTokens.space(16)) {
                    Image(systemName: "puzzlepiece.extension").font(OrdinaTokens.type(28).weight(.light))
                        .foregroundStyle(OrdinaTokens.cream)
                    VStack(alignment: .leading, spacing: OrdinaTokens.space(6)) {
                        Text("Project Setup").font(OrdinaTokens.type(16).weight(.medium))
                        Text(integrationLabel).font(OrdinaTokens.type(12))
                            .foregroundStyle(controller.integrationStatus == .current ? OrdinaTokens.success : OrdinaTokens.secondary)
                    }
                    Spacer()
                    if controller.integrationStatus == .current {
                        Image(systemName: "checkmark.circle").font(OrdinaTokens.type(20))
                            .foregroundStyle(OrdinaTokens.success)
                    } else {
                        Button(installationTitle) { controller.installProjectSetup() }
                            .buttonStyle(PaperButtonStyle())
                            .disabled(controller.installing)
                    }
                }
            }
            if !controller.integrationError.isEmpty {
                Text(controller.integrationError).font(OrdinaTokens.type(12)).foregroundStyle(OrdinaTokens.error)
            } else if controller.integrationStatus == .outdated {
                Text("更新前会备份现有脚本。")
                    .font(OrdinaTokens.type(12)).foregroundStyle(OrdinaTokens.secondary)
            }
            HStack {
                if controller.status(for: .projectSetup) == .detecting { detecting }
                Spacer()
                refreshButton
            }
            OptionalPluginsView()
        }
    }

    private var footer: some View {
        VStack(spacing: 0) {
            PaperRule()
            HStack {
                Spacer()
                Button { controller.advance() } label: {
                    HStack(spacing: OrdinaTokens.space(8)) {
                        Text(controller.step == .projectSetup ? "完成" : "继续")
                        DoodleIcon(.arrowRight).frame(width: 13, height: 13)
                    }
                    .font(OrdinaTokens.type(14).weight(.semibold))
                    .foregroundStyle(controller.canContinue ? OrdinaTokens.bg : OrdinaTokens.faint)
                    .frame(width: OrdinaTokens.space(136), height: OrdinaTokens.height(44))
                    .background(controller.canContinue ? OrdinaTokens.cream : OrdinaTokens.selected, in: Capsule())
                }
                .buttonStyle(.plain)
                .disabled(!controller.canContinue)
                .keyboardShortcut(.defaultAction)
            }
            .padding(OrdinaTokens.space(24))
        }
    }

    private var refreshButton: some View {
        Button { Task { await controller.refresh() } } label: {
            Text("刷新").padding(.horizontal, 8).frame(minHeight: 44).contentShape(Rectangle())
        }
            .buttonStyle(.plain).font(OrdinaTokens.type(12))
            .foregroundStyle(OrdinaTokens.cream)
            .disabled(controller.isDetecting || controller.installing)
    }

    private var detecting: some View {
        HStack(spacing: OrdinaTokens.space(8)) {
            ProgressView().controlSize(.small)
            Text("正在检测…").font(OrdinaTokens.type(12)).foregroundStyle(OrdinaTokens.secondary)
        }
    }

    private func panel<Content: View>(@ViewBuilder content: () -> Content) -> some View {
        content().padding(OrdinaTokens.space(24)).frame(maxWidth: .infinity, alignment: .leading)
            .background(OrdinaTokens.surface, in: RoundedRectangle(cornerRadius: OrdinaTokens.radius(12)))
            .overlay(RoundedRectangle(cornerRadius: OrdinaTokens.radius(12)).strokeBorder(OrdinaTokens.rule, lineWidth: 0.8))
    }

    private var title: String {
        switch controller.step {
        case .projectRoot: return "项目保存在哪里"
        case .resolveLibrary: return "连接 Resolve 项目库"
        case .projectSetup: return "安装 Project Setup"
        }
    }

    private var integrationLabel: String {
        if controller.installing { return "正在安装…" }
        switch controller.integrationStatus {
        case .current: return "已安装 · 当前版本"
        case .missing: return "未安装"
        case .outdated: return "可更新"
        case nil: return "检测未完成"
        }
    }

    private var installationTitle: String {
        if !controller.integrationError.isEmpty { return "重试安装" }
        return controller.integrationStatus == .outdated ? "更新" : "安装"
    }

    private func chooseFolder() {
        let picker = NSOpenPanel()
        picker.title = "选择项目根目录"
        picker.prompt = "选择文件夹"
        picker.canChooseDirectories = true
        picker.canChooseFiles = false
        picker.allowsMultipleSelection = false
        picker.canCreateDirectories = true
        picker.directoryURL = controller.destinationURL
        if picker.runModal() == .OK, let url = picker.url {
            controller.saveDestination(url)
        }
    }
}

/// Builds an isolated onboarding controller for previews: an ephemeral defaults
/// suite seeded with the requested step, a stubbed Resolve probe, and a no-op
/// installer, so the canvas renders static states without touching real data.
private enum OnboardingPreviews {
    @MainActor
    static func controller(step: OnboardingStep) -> OnboardingController {
        let suite = "Ordina.Preview.Onboarding.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        var progress = OnboardingProgress()
        progress.state = .needsConfiguration
        progress.step = step
        progress.save(to: defaults)
        defaults.set(true, forKey: "createResolveProject")
        let local = ResolveDatabase(DbType: "Disk", DbName: "本地项目库", IpAddress: nil)
        // TEST-NET-1 documentation address; this preview uses a mock probe.
        let remote = ResolveDatabase(DbType: "PostgreSQL", DbName: "示例项目库", IpAddress: "192.0.2.20")
        return OnboardingController(
            defaults: defaults,
            environment: ["ORDO_UI_TEST": "1"],
            probe: { ResolveProbe(database: local, databases: [local, remote]) },
            install: {})
    }
}

#Preview("引导 · 项目根目录") {
    OnboardingView(controller: OnboardingPreviews.controller(step: .projectRoot))
        .frame(width: 880, height: 600)
}

#Preview("引导 · Resolve 项目库") {
    OnboardingView(controller: OnboardingPreviews.controller(step: .resolveLibrary))
        .frame(width: 880, height: 600)
}

#Preview("引导 · Project Setup") {
    OnboardingView(controller: OnboardingPreviews.controller(step: .projectSetup))
        .frame(width: 880, height: 600)
}
