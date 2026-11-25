import SwiftUI
import AppKit
import UniformTypeIdentifiers

// The developer centre: three areas (release, management, tool). The entry is
// hidden in settings, but hiding is only discovery — every action below is
// authorised by the publishing tool's own signed credential check.
@MainActor
struct DeveloperCenterView: View {
    @StateObject private var model = DeveloperCenterModel()
    @Environment(\.dismiss) private var dismiss
    @State private var selectingPackage = false
    @State private var selectingCredential = false
    @State private var targeted = false
    @State private var confirmingPublish = false
    @State private var confirmingRollback: DevJournal?
    @State private var confirmingToolInstall = false
    @State private var rollbackReason = ""
    @State private var showingLog = false
    @State private var showingAdvanced = false

    var body: some View {
        VStack(spacing: 0) {
            header
            if !model.configuration.enabled {
                enablePanel
            } else {
                Picker("", selection: $model.tab) {
                    Text("发布").tag(0)
                    Text("管理").tag(1)
                    Text("工具").tag(2)
                }
                .pickerStyle(.segmented).labelsHidden()
                .padding(.horizontal, 24).padding(.bottom, 10)
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        switch model.tab {
                        case 1: management
                        case 2: toolPanel
                        default: release
                        }
                    }
                    .padding(.horizontal, 24).padding(.bottom, 20)
                }
                .scrollBounceBehavior(.basedOnSize)
            }
            footer
        }
        .frame(width: 760, height: 720)
        .background(Paper.background).foregroundStyle(Paper.ink)
        .task { await model.load() }
        .fileImporter(isPresented: $selectingPackage,
                      allowedContentTypes: [.zip], allowsMultipleSelection: false) { result in
            if case .success(let urls) = result, let url = urls.first {
                Task { await model.choosePackage(url) }
            }
        }
        .fileImporter(isPresented: $selectingCredential,
                      allowedContentTypes: [.json, .data], allowsMultipleSelection: false) { result in
            if case .success(let urls) = result, let url = urls.first {
                Task { await model.importCredential(url) }
            }
        }
        .confirmationDialog("确认发布", isPresented: $confirmingPublish, titleVisibility: .visible) {
            Button("发布", role: .destructive) {
                let create = model.preview?.repository.exists == false
                Task { await model.publish(createRepository: create) }
            }
            Button("取消", role: .cancel) {}
        } message: {
            Text(publishConfirmation)
        }
        .sheet(item: $confirmingRollback) { journal in
            rollbackSheet(journal)
        }
    }

    // MARK: header and footer

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text("开发者中心").font(Paper.font(24).weight(.semibold))
                Text(model.toolState.title).font(Paper.font(12))
                    .foregroundStyle(model.toolState.level == .ready ? Paper.success : Paper.secondary)
            }
            Spacer()
            if model.busy { ProgressView().controlSize(.small).tint(Paper.accent) }
            Button { dismiss() } label: { Image(systemName: "xmark") }
                .buttonStyle(PaperQuietButton()).help("关闭").accessibilityLabel("关闭")
                .keyboardShortcut(.cancelAction)
        }
        .padding(.horizontal, 24).padding(.top, 20).padding(.bottom, 12)
    }

    private var footer: some View {
        VStack(alignment: .leading, spacing: 0) {
            if let error = model.error {
                PaperRule()
                HStack(alignment: .top, spacing: 6) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.system(size: 11)).foregroundStyle(Paper.error)
                    Text(error).font(Paper.font(12)).foregroundStyle(Paper.error)
                        .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                    Spacer()
                    if let journal = model.lastJournal {
                        Button("查看事务") {
                            model.tab = 1
                        }.buttonStyle(PaperQuietButton())
                        .help(journal)
                    }
                }.padding(.horizontal, 24).padding(.vertical, 8)
            } else if let notice = model.notice {
                PaperRule()
                Text(notice).font(Paper.font(12)).foregroundStyle(Paper.secondary)
                    .padding(.horizontal, 24).padding(.vertical, 8)
            }
        }
    }

    // MARK: enable

    private var enablePanel: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 12) {
                Image(systemName: "hammer.fill").font(.system(size: 18))
                    .frame(width: 40, height: 40)
                    .background(Paper.surface, in: RoundedRectangle(cornerRadius: 10))
                    .foregroundStyle(Paper.accent)
                VStack(alignment: .leading, spacing: 3) {
                    Text("面向插件开发者").font(Paper.font(14).weight(.medium))
                    Text("安装独立发布工具后，可直接用 ZIP 完成本地测试与正式发布。普通用户不需要开启。")
                        .font(Paper.font(12)).foregroundStyle(Paper.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Text("发布权限由发布工具与签名授权独立验证：开启本页不会获得任何发布权限，也无法内置或代替凭据。")
                .font(Paper.font(12)).foregroundStyle(Paper.secondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack {
                Spacer()
                Button("启用开发者中心") { model.enable() }
                    .buttonStyle(PaperButtonStyle(primary: true))
            }
        }
        .padding(20)
        .background(Paper.surface, in: RoundedRectangle(cornerRadius: 14))
        .overlay { RoundedRectangle(cornerRadius: 14).strokeBorder(Paper.rule, lineWidth: 0.8) }
        .padding(.horizontal, 24).padding(.top, 8)
        .frame(maxHeight: .infinity, alignment: .top)
    }

    // MARK: release

    private var release: some View {
        VStack(alignment: .leading, spacing: 16) {
            if model.toolState.level != .ready { toolHint }
            packageSection
            if let inspection = model.inspection {
                packageCard(inspection)
                notesSection
                actionRow(inspection)
                previewSection
            }
            if !model.stages.isEmpty || !model.logLines.isEmpty { progressSection }
        }
    }

    private var toolHint: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "info.circle").font(.system(size: 12)).foregroundStyle(Paper.secondary)
            VStack(alignment: .leading, spacing: 2) {
                Text(model.toolState.detail ?? "发布工具尚未就绪。")
                    .font(Paper.font(12)).fixedSize(horizontal: false, vertical: true)
                Button("打开工具设置") { model.tab = 2 }
                    .buttonStyle(.plain).font(Paper.font(12)).foregroundStyle(Paper.accent)
            }
            Spacer()
        }
        .padding(12)
        .background(Paper.surface, in: RoundedRectangle(cornerRadius: 10))
        .overlay { RoundedRectangle(cornerRadius: 10).strokeBorder(Paper.rule, lineWidth: 0.8) }
    }

    private var packageSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            if model.packageURL == nil {
                VStack(spacing: 10) {
                    Image(systemName: "shippingbox").font(.system(size: 26))
                        .foregroundStyle(Paper.secondary)
                    Text("拖入插件 ZIP").font(Paper.font(14).weight(.medium))
                    Text("包含 plugin.json 的标准插件包；也可以从文件中选取。")
                        .font(Paper.font(12)).foregroundStyle(Paper.secondary)
                    Button("选择 ZIP…") { selectingPackage = true }
                        .buttonStyle(PaperButtonStyle(compact: true))
                }
                .frame(maxWidth: .infinity, minHeight: 168)
                .background(Paper.surface, in: RoundedRectangle(cornerRadius: 14))
                .overlay {
                    RoundedRectangle(cornerRadius: 14)
                        .strokeBorder(targeted ? Paper.accent : Paper.rule,
                                      style: StrokeStyle(lineWidth: targeted ? 1.6 : 0.8, dash: [6, 4]))
                }
                .dropDestination(for: URL.self) { urls, _ in
                    guard !model.busy, urls.count == 1, let url = urls.first,
                          url.pathExtension.lowercased() == "zip" else { return false }
                    Task { await model.choosePackage(url) }
                    return true
                } isTargeted: { targeted = $0 && !model.busy }
            } else {
                HStack(spacing: 8) {
                    Text(model.packageURL?.lastPathComponent ?? "")
                        .font(Paper.font(12).monospaced()).foregroundStyle(Paper.secondary)
                        .lineLimit(1)
                    Spacer()
                    Button("更换…") { selectingPackage = true }
                        .buttonStyle(PaperButtonStyle(compact: true)).disabled(model.busy)
                    Button("移出") {
                        model.packageURL = nil; model.inspection = nil
                        model.preview = nil; model.stages = []
                    }
                    .buttonStyle(PaperQuietButton()).disabled(model.busy)
                }
                .dropDestination(for: URL.self) { urls, _ in
                    guard !model.busy, urls.count == 1, let url = urls.first,
                          url.pathExtension.lowercased() == "zip" else { return false }
                    Task { await model.choosePackage(url) }
                    return true
                } isTargeted: { targeted = $0 && !model.busy }
                .padding(10)
                .background(Paper.surface, in: RoundedRectangle(cornerRadius: 10))
                .overlay {
                    RoundedRectangle(cornerRadius: 10)
                        .strokeBorder(targeted ? Paper.accent : Paper.rule, lineWidth: 0.8)
                }
            }
        }
    }

    private func packageCard(_ inspection: DevInspection) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 12) {
                Image(systemName: inspection.package.symbol)
                    .font(.system(size: 17)).frame(width: 40, height: 40)
                    .background(Paper.surface, in: RoundedRectangle(cornerRadius: 10))
                    .foregroundStyle(Paper.accent)
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 8) {
                        Text(inspection.package.name).font(Paper.font(14).weight(.medium))
                        badge(inspection.actionLabel,
                              color: inspection.isNew ? Paper.accent : Paper.success)
                    }
                    Text(inspection.package.summary).font(Paper.font(12))
                        .foregroundStyle(Paper.secondary).lineLimit(2)
                    Text(versionLine(inspection))
                        .font(Paper.font(12).monospaced()).foregroundStyle(Paper.secondary)
                }
                Spacer()
            }
            PaperRule()
            HStack(spacing: 16) {
                fact("插件 ID", inspection.package.id)
                fact("协议", "v\(inspection.package.protocolVersion)")
                fact("最低客户端", inspection.package.minClientVersion)
                fact("文件", "\(inspection.package.fileCount) 个，\(bytes(inspection.package.assetSize))")
            }
            if !inspection.package.capabilities.isEmpty {
                HStack(spacing: 6) {
                    ForEach(inspection.package.capabilities, id: \.self) { capability in
                        Text(capability).font(Paper.font(11).monospaced())
                            .padding(.horizontal, 7).padding(.vertical, 3)
                            .background(Paper.surface, in: Capsule())
                            .overlay { Capsule().strokeBorder(Paper.rule, lineWidth: 0.8) }
                            .foregroundStyle(Paper.secondary)
                    }
                }
            }
            if !inspection.compatibility.ok {
                Text(inspection.compatibility.reasons.joined(separator: "；"))
                    .font(Paper.font(12)).foregroundStyle(Paper.error)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let result = model.localTestResult {
                HStack(spacing: 8) {
                    badge("已安装测试版本 \(result.version)", color: Paper.accent)
                    Text("仅写入本机，不影响线上").font(Paper.font(11)).foregroundStyle(Paper.secondary)
                    Spacer()
                    Button("卸载测试版本") { Task { await model.uninstallLocalTest() } }
                        .buttonStyle(PaperQuietButton()).disabled(model.busy)
                }
            }
        }
        .padding(14)
        .background(Paper.surface, in: RoundedRectangle(cornerRadius: 14))
        .overlay { RoundedRectangle(cornerRadius: 14).strokeBorder(Paper.rule, lineWidth: 0.8) }
    }

    private func versionLine(_ inspection: DevInspection) -> String {
        let previous = inspection.existing?.version
        switch inspection.action {
        case "new": return "\(inspection.package.version) · 首次发布"
        case "same": return "\(inspection.package.version) · 与线上版本相同"
        case "downgrade": return "\(inspection.package.version) · 低于线上 \(previous ?? "")"
        default: return "\(inspection.package.version) ← \(previous ?? "")"
        }
    }

    private var notesSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("更新说明").font(Paper.font(12).weight(.medium)).foregroundStyle(Paper.secondary)
                Spacer()
                Text("\(model.notes.count)/2000").font(Paper.font(11)).foregroundStyle(Paper.faint)
            }
            TextEditor(text: $model.notes)
                .font(Paper.font(12))
                .scrollContentBackground(.hidden)
                .padding(8)
                .frame(minHeight: 64, maxHeight: 96)
                .background(Paper.surface, in: RoundedRectangle(cornerRadius: 10))
                .overlay { RoundedRectangle(cornerRadius: 10).strokeBorder(Paper.rule, lineWidth: 0.8) }
                .disabled(model.busy)
        }
    }

    private func actionRow(_ inspection: DevInspection) -> some View {
        HStack(spacing: 10) {
            Button("本地测试") { Task { await model.localTest() } }
                .buttonStyle(PaperButtonStyle(compact: true))
                .disabled(model.busy || !inspection.compatibility.ok)
                .help("安装到本机 Resolve，不创建 Release、不修改 Registry")
            Button("预览发布") { Task { await model.runPreview() } }
                .buttonStyle(PaperButtonStyle(compact: true))
                .disabled(model.busy || !inspection.compatibility.ok)
            Spacer()
            Button("发布…") { confirmingPublish = true }
                .buttonStyle(PaperButtonStyle(primary: true))
                .disabled(!model.canPublish)
        }
        .frame(minHeight: 40)
    }

    private var publishConfirmation: String {
        guard let preview = model.preview, let inspection = model.inspection else { return "" }
        var lines = [
            "\(inspection.package.name) \(preview.plugin.version)",
            "仓库：\(preview.plugin.repository)",
            "标签：\(preview.plugin.tag)",
            "资产：\(preview.asset.name)（\(bytes(preview.asset.size))）",
        ]
        if preview.repository.exists == false {
            lines.append("将创建仓库并启用 GitHub Pages，需再次确认。")
        }
        lines.append("发布会写入签名清单与 Registry，失败可恢复或回滚。")
        return lines.joined(separator: "\n")
    }

    @ViewBuilder
    private var previewSection: some View {
        if let preview = model.preview {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Text("发布预览").font(Paper.font(12).weight(.medium))
                        .foregroundStyle(Paper.secondary)
                    Spacer()
                    Text(preview.fingerprint.prefix(12)).font(Paper.font(11).monospaced())
                        .foregroundStyle(Paper.faint)
                }
                checkList(preview)
                if !preview.blockers.isEmpty {
                    VStack(alignment: .leading, spacing: 4) {
                        ForEach(preview.blockers) { blocker in
                            Text("· \(blocker.message)").font(Paper.font(12))
                                .foregroundStyle(Paper.error)
                        }
                    }
                }
                if preview.repository.exists == false {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("发布前将执行：").font(Paper.font(12).weight(.medium))
                        ForEach(preview.repositorySteps) { step in
                            Text("· \(step.title)：\(step.command)")
                                .font(Paper.font(11).monospaced())
                                .foregroundStyle(Paper.secondary)
                                .textSelection(.enabled)
                        }
                    }
                }
                HStack(spacing: 16) {
                    fact("文件", "新增 \(preview.files.added.count) · 修改 \(preview.files.changed.count) · 删除 \(preview.files.removed.count)")
                    fact("清单", "\(preview.feeds.count) 项")
                }
            }
            .padding(14)
            .background(Paper.surface, in: RoundedRectangle(cornerRadius: 12))
            .overlay { RoundedRectangle(cornerRadius: 12).strokeBorder(Paper.rule, lineWidth: 0.8) }
        }
    }

    private func checkList(_ preview: DevPreview) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(preview.checks) { check in
                HStack(spacing: 6) {
                    Image(systemName: check.ok ? "checkmark.circle.fill" : "exclamationmark.circle.fill")
                        .font(.system(size: 10))
                        .foregroundStyle(check.ok ? Paper.success : Paper.error)
                    Text(check.message).font(Paper.font(12))
                        .foregroundStyle(check.ok ? Paper.ink : Paper.error)
                }
            }
        }
    }

    private var progressSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            if !model.stages.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(model.stages) { stage in
                        HStack(spacing: 8) {
                            Image(systemName: stage.symbol).font(.system(size: 11))
                                .foregroundStyle(stage.state == "failed" ? Paper.error
                                                 : (stage.state == "ok" ? Paper.success : Paper.secondary))
                            Text(stage.title).font(Paper.font(12))
                            Spacer()
                            Text(stageLabel(stage.state)).font(Paper.font(11))
                                .foregroundStyle(Paper.secondary)
                        }
                    }
                }
            }
            if model.phase.isWorking {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text(phaseLabel).font(Paper.font(12)).foregroundStyle(Paper.secondary)
                }
            }
            if !model.logLines.isEmpty {
                DisclosureGroup("发布日志", isExpanded: $showingLog) {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 2) {
                            ForEach(Array(model.logLines.enumerated()), id: \.offset) { _, line in
                                Text(line).font(Paper.font(11).monospaced())
                                    .foregroundStyle(Paper.secondary)
                                    .textSelection(.enabled)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                        }.padding(8)
                    }
                    .frame(maxHeight: 140)
                    .background(Paper.surface, in: RoundedRectangle(cornerRadius: 8))
                }
                .font(Paper.font(12))
            }
        }
    }

    private var phaseLabel: String {
        if case .working(let text) = model.phase { return text }
        return ""
    }

    private func stageLabel(_ state: String) -> String {
        switch state {
        case "ok": return "完成"
        case "running": return "进行中"
        case "failed": return "失败"
        default: return "等待"
        }
    }

    // MARK: management

    private var management: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text("发布管理").font(Paper.font(12).weight(.medium)).foregroundStyle(Paper.secondary)
                Spacer()
                Button("刷新") { Task { await model.refreshAll() } }
                    .buttonStyle(PaperQuietButton()).disabled(model.busy)
            }
            if model.journals.isEmpty {
                emptyCard("暂无发布记录", detail: "发布或本地测试后，事务与历史会显示在这里。")
            } else {
                VStack(spacing: 0) {
                    ForEach(model.journals.prefix(20)) { journal in
                        journalRow(journal)
                        PaperRule()
                    }
                }
                .padding(.horizontal, 14)
                .background(Paper.surface, in: RoundedRectangle(cornerRadius: 12))
                .overlay { RoundedRectangle(cornerRadius: 12).strokeBorder(Paper.rule, lineWidth: 0.8) }
            }
            if let records = model.history?.records, !records.isEmpty {
                Text("历史发布").font(Paper.font(12).weight(.medium))
                    .foregroundStyle(Paper.secondary)
                VStack(spacing: 0) {
                    ForEach(records.prefix(12)) { record in
                        recordRow(record)
                        PaperRule()
                    }
                }
                .padding(.horizontal, 14)
                .background(Paper.surface, in: RoundedRectangle(cornerRadius: 12))
                .overlay { RoundedRectangle(cornerRadius: 12).strokeBorder(Paper.rule, lineWidth: 0.8) }
            }
        }
    }

    private func journalRow(_ journal: DevJournal) -> some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 8) {
                    Text(journal.name ?? journal.plugin ?? "未知插件")
                        .font(Paper.font(13).weight(.medium))
                    Text("v\(journal.version ?? "?")").font(Paper.font(12).monospaced())
                        .foregroundStyle(Paper.secondary)
                    badge(journal.statusLabel,
                          color: journal.status == "complete" ? Paper.success
                                : (journal.status == "rolled_back" ? Paper.secondary : Paper.warning))
                }
                Text([journal.repository, journal.notes]
                        .compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · "))
                    .font(Paper.font(11)).foregroundStyle(Paper.secondary).lineLimit(1)
            }
            Spacer()
            if journal.isPending {
                Button("继续") { Task { await model.resume(journal) } }
                    .buttonStyle(PaperButtonStyle(compact: true)).disabled(model.busy)
            }
            if journal.rollbackable {
                Button("回滚…") {
                    rollbackReason = "回滚 \(journal.name ?? journal.plugin ?? "") v\(journal.version ?? "")"
                    confirmingRollback = journal
                }
                .buttonStyle(PaperQuietButton()).disabled(model.busy)
            }
        }
        .frame(minHeight: 52)
    }

    private func recordRow(_ record: DevRecord) -> some View {
        HStack(spacing: 10) {
            Text(record.kind == "rollback" ? "回滚" : (record.kind == "resume" ? "恢复" : "发布"))
                .font(Paper.font(11)).foregroundStyle(Paper.secondary)
                .frame(width: 32, alignment: .leading)
            Text(record.plugin ?? "").font(Paper.font(12))
            Text(record.version.map { "v\($0)" } ?? "")
                .font(Paper.font(12).monospaced()).foregroundStyle(Paper.secondary)
            if let notes = record.notes, !notes.isEmpty {
                Text(notes).font(Paper.font(11)).foregroundStyle(Paper.secondary).lineLimit(1)
            }
            Spacer()
            if let at = record.at {
                Text(DeveloperCenterModel.date(at)).font(Paper.font(11)).foregroundStyle(Paper.faint)
            }
        }
        .frame(minHeight: 34)
    }

    private func rollbackSheet(_ journal: DevJournal) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("回滚发布").font(Paper.font(21).weight(.semibold))
            Text("\(journal.name ?? journal.plugin ?? "") v\(journal.version ?? "")")
                .font(Paper.font(13))
            Text("将把 Registry 与插件清单恢复到该事务发布前的字节。若远端已有其他提交，回滚会被拒绝。")
                .font(Paper.font(12)).foregroundStyle(Paper.secondary)
                .fixedSize(horizontal: false, vertical: true)
            TextField("回滚原因", text: $rollbackReason)
                .textFieldStyle(.roundedBorder).font(Paper.font(12))
            HStack {
                Spacer()
                Button("取消") { confirmingRollback = nil }
                    .buttonStyle(PaperButtonStyle()).keyboardShortcut(.cancelAction)
                Button("回滚") {
                    let target = journal
                    confirmingRollback = nil
                    Task { await model.rollback(target, reason: rollbackReason) }
                }
                .buttonStyle(PaperButtonStyle(primary: true))
            }
        }
        .padding(24).frame(width: 460).background(Paper.background)
    }

    // MARK: tool

    private var toolPanel: some View {
        VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 10) {
                    Image(systemName: model.toolState.level == .ready
                          ? "checkmark.seal.fill" : "exclamationmark.triangle.fill")
                        .foregroundStyle(model.toolState.level == .ready ? Paper.success : Paper.warning)
                    Text(model.toolState.title).font(Paper.font(13).weight(.medium))
                    Spacer()
                    if let protocolVersion = model.toolState.protocolVersion {
                        Text("协议 \(protocolVersion)").font(Paper.font(11).monospaced())
                            .foregroundStyle(Paper.secondary)
                    }
                }
                if let detail = model.toolState.detail {
                    Text(detail).font(Paper.font(12)).foregroundStyle(Paper.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let credential = model.toolState.credential {
                    HStack(spacing: 6) {
                        Image(systemName: credential.valid ? "key.fill" : "key.slash")
                            .font(.system(size: 10))
                            .foregroundStyle(credential.valid ? Paper.success : Paper.error)
                        Text(credential.valid
                             ? "授权有效 · \(credential.developer ?? "") · \(credential.expires.map(DeveloperCenterModel.date) ?? "") 到期"
                             : (credential.message ?? "未授权"))
                            .font(Paper.font(12)).foregroundStyle(Paper.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                HStack(spacing: 8) {
                    Button("检查更新") { Task { await model.checkToolUpdate() } }
                        .buttonStyle(PaperButtonStyle(compact: true))
                        .disabled(model.checkingTool || model.busy)
                    if let update = model.availableTool {
                        Button("安装 \(update.version)") { confirmingToolInstall = true }
                            .buttonStyle(PaperButtonStyle(compact: true))
                            .disabled(model.busy || model.hasPendingTransaction)
                    }
                    Button("回滚工具") { Task { await model.rollbackTool() } }
                        .buttonStyle(PaperQuietButton())
                        .disabled(model.busy || model.hasPendingTransaction
                                  || (model.installedTool?.previous.isEmpty ?? true))
                    Button("导入授权…") { selectingCredential = true }
                        .buttonStyle(PaperQuietButton()).disabled(model.busy)
                    Spacer()
                }
                if let message = model.toolMessage {
                    Text(message).font(Paper.font(11)).foregroundStyle(Paper.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if model.hasPendingTransaction {
                    Text("存在未完成的发布事务，更新或回滚发布工具已暂停。")
                        .font(Paper.font(12)).foregroundStyle(Paper.warning)
                }
            }
            .padding(14)
            .background(Paper.surface, in: RoundedRectangle(cornerRadius: 12))
            .overlay { RoundedRectangle(cornerRadius: 12).strokeBorder(Paper.rule, lineWidth: 0.8) }
            .confirmationDialog("安装发布工具", isPresented: $confirmingToolInstall,
                                titleVisibility: .visible) {
                Button("安装") {
                    if let update = model.availableTool {
                        Task { await model.installTool(update) }
                    }
                }
                Button("取消", role: .cancel) {}
            } message: {
                Text("新版本经过签名清单、SHA-256 与运行自检后才会启用；授权、发布记录与未完成事务都会保留。")
            }

            VStack(spacing: 0) {
                pathRow("发布仓库", value: model.configuration.repositoryPath) {
                    chooseRepository()
                }
                PaperRule()
                pathRow("发布工具", value: model.configuration.toolPath.isEmpty
                        ? (model.installedTool.map { "托管安装 \($0.version)" } ?? "未安装")
                        : model.configuration.toolPath) {
                    chooseTool()
                }
                if !model.configuration.toolPath.isEmpty {
                    PaperRule()
                    HStack {
                        Text("使用自定义工具路径").font(Paper.font(12))
                        Spacer()
                        Button("改回托管安装") { model.setToolPath(nil) }
                            .buttonStyle(PaperQuietButton())
                    }.frame(minHeight: 40)
                }
            }
            .padding(.horizontal, 14)
            .background(Paper.surface, in: RoundedRectangle(cornerRadius: 12))
            .overlay { RoundedRectangle(cornerRadius: 12).strokeBorder(Paper.rule, lineWidth: 0.8) }

            DisclosureGroup("高级信息", isExpanded: $showingAdvanced) {
                VStack(alignment: .leading, spacing: 6) {
                    fact("事务目录", DeveloperPaths.journals.path)
                    fact("发布记录", DeveloperPaths.records.path)
                    fact("工具目录", DeveloperPaths.versions.path)
                    if let doctor = model.doctor {
                        ForEach(doctor.requirements, id: \.id) { requirement in
                            HStack(spacing: 6) {
                                Image(systemName: requirement.ok ? "checkmark.circle" : "xmark.circle")
                                    .font(.system(size: 10))
                                    .foregroundStyle(requirement.ok ? Paper.success : Paper.error)
                                Text(requirement.message).font(Paper.font(11))
                                    .foregroundStyle(Paper.secondary)
                            }
                        }
                    }
                }.padding(.top, 8)
            }
            .font(Paper.font(12))
        }
    }

    private func pathRow(_ title: String, value: String, choose: @escaping () -> Void) -> some View {
        HStack(spacing: 12) {
            Text(title).fixedSize()
            Spacer(minLength: 12)
            Text(value.isEmpty ? "未配置" : value)
                .font(Paper.font(11).monospaced())
                .foregroundStyle(Paper.secondary)
                .lineLimit(1).truncationMode(.middle)
                .help(value)
            Button("选择…", action: choose)
                .buttonStyle(PaperButtonStyle(compact: true))
                .accessibilityLabel("选择\(title)")
        }.frame(minHeight: 44)
    }

    // MARK: helpers

    private func badge(_ text: String, color: Color) -> some View {
        Text(text).font(Paper.font(10).weight(.medium))
            .padding(.horizontal, 6).padding(.vertical, 2)
            .background(color.opacity(0.12), in: Capsule())
            .foregroundStyle(color)
    }

    private func fact(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(title).font(Paper.font(10)).foregroundStyle(Paper.faint)
            Text(value).font(Paper.font(11)).foregroundStyle(Paper.secondary)
                .lineLimit(1).truncationMode(.middle)
        }
    }

    private func emptyCard(_ title: String, detail: String) -> some View {
        VStack(spacing: 4) {
            Text(title).font(Paper.font(13)).foregroundStyle(Paper.secondary)
            Text(detail).font(Paper.font(11)).foregroundStyle(Paper.faint)
        }
        .frame(maxWidth: .infinity, minHeight: 120)
        .background(Paper.surface, in: RoundedRectangle(cornerRadius: 12))
        .overlay { RoundedRectangle(cornerRadius: 12).strokeBorder(Paper.rule, lineWidth: 0.8) }
    }

    private func bytes(_ value: Int) -> String {
        ByteCountFormatter.string(fromByteCount: Int64(value), countStyle: .file)
    }

    private func chooseRepository() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.prompt = "选择"
        panel.message = "选择 ordina 发布仓库（包含 plugins/registry.source.json）"
        if panel.runModal() == .OK, let url = panel.url {
            model.setRepository(url)
        }
    }

    private func chooseTool() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.prompt = "选择"
        panel.message = "选择已独立安装的 ordina-publisher（.pyz 或可执行文件）"
        if panel.runModal() == .OK, let url = panel.url {
            model.setToolPath(url)
        }
    }
}

#Preview("开发者中心") {
    DeveloperCenterView().preferredColorScheme(.dark)
}
