import SwiftUI

// Single click opens: linked projects open in Resolve, unlinked ones open the
// folder. Secondary actions live in the ellipsis menu and context menu, matching
// the macOS expectation that a click on a row performs its primary action.
struct ProjectRow: View {
    let project: WorkspaceProject
    let compact: Bool
    let open: () -> Void
    let folder: () -> Void
    @State private var hovering = false
    var body: some View {
        HStack(spacing: 12) {
            DoodleIcon(.folder).frame(width: 18, height: 18).foregroundStyle(OrdinaTokens.secondary)
            VStack(alignment: .leading, spacing: 4) {
                Text(project.title).font(OrdinaTokens.type(14).weight(.medium)).lineLimit(1)
                if !compact {
                    Text(project.url.lastPathComponent).font(OrdinaTokens.type(12)).foregroundStyle(OrdinaTokens.secondary)
                }
            }
            Spacer()
            if let date = project.lastUsed {
                Text(date.formatted(.dateTime.month().day().hour().minute()))
                    .font(OrdinaTokens.type(12)).foregroundStyle(OrdinaTokens.secondary)
                    .help("上次打开：" + date.formatted(date: .abbreviated, time: .shortened))
                    .accessibilityLabel("上次打开：" + date.formatted(date: .abbreviated, time: .shortened))
            } else {
                Text("无记录").font(OrdinaTokens.type(12)).foregroundStyle(OrdinaTokens.secondary)
                    .help("尚无打开时间记录")
            }
            Menu { projectActions } label: {
                DoodleIcon(.more).frame(width: 18, height: 18)
                    .frame(width: 44, height: 44).contentShape(Rectangle())
            }
            .menuStyle(.borderlessButton).menuIndicator(.hidden).fixedSize()
            .foregroundStyle(OrdinaTokens.secondary)
            .help("项目操作").accessibilityLabel("项目操作")
        }
        .padding(.leading, 8).frame(minHeight: compact ? 48 : 64).contentShape(Rectangle())
        .onTapGesture(perform: primaryAction)
        .background(hovering ? OrdinaTokens.hover : .clear, in: RoundedRectangle(cornerRadius: 10))
        .onHover { hovering = $0 }
        .contextMenu { projectActions }
        .help(project.linked ? "打开 Resolve 工程" : "打开项目文件夹")
        .accessibilityElement(children: .contain)
        .accessibilityLabel(project.title)
        .accessibilityAction(named: project.linked ? "打开 Resolve 工程" : "打开项目文件夹", primaryAction)
        .accessibilityAction(named: "打开项目文件夹", folder)
    }

    private func primaryAction() {
        if project.linked { open() } else { folder() }
    }

    @ViewBuilder private var projectActions: some View {
            Button("打开 Resolve 工程", action: open).disabled(!project.linked)
            Button("打开项目文件夹", action: folder)
    }
}

private extension WorkspaceProject {
    static let previewLinked = WorkspaceProject(
        url: URL(fileURLWithPath: "/Volumes/NAS/20261103_纪录片粗剪"),
        modified: Date(), linked: true, lastUsed: Date())
    static let previewUnlinked = WorkspaceProject(
        url: URL(fileURLWithPath: "/Volumes/NAS/20261018_婚礼快剪"),
        modified: Date(timeIntervalSinceNow: -86_400), linked: false, lastUsed: nil)
}

#Preview("项目行") {
    VStack(alignment: .leading, spacing: 0) {
        ProjectRow(project: .previewLinked, compact: false, open: {}, folder: {})
        PaperRule()
        ProjectRow(project: .previewUnlinked, compact: true, open: {}, folder: {})
    }
    .padding(24)
    .frame(width: 640)
    .background(Paper.background)
    .preferredColorScheme(.dark)
}
