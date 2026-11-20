import SwiftUI

struct ResolveLibraryRow: View {
    let databases: [ResolveDatabase]
    let selection: String
    var connectedID: String
    var checking: Bool
    var status: String = ""
    let change: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text("项目库").font(.system(size: 13, weight: .medium))
                Text(databases.first { $0.id == selection }?.label ?? (selection.isEmpty ? "未选择" : "所选项目库不可用"))
                    .lineLimit(1).truncationMode(.middle)
                    .foregroundStyle(Paper.secondary)
                if !status.isEmpty {
                    Text(status).foregroundStyle(!checking && selection == connectedID && !selection.isEmpty ? Paper.success : Paper.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .font(Paper.font(12))
            Spacer(minLength: 12)
            Button(selection.isEmpty ? "选择…" : "更改…", action: change)
                .buttonStyle(PaperButtonStyle(compact: true))
                .accessibilityLabel("选择项目库")
        }.frame(minHeight: 44)
    }
}

struct ResolveLibraryPicker: View {
    let databases: [ResolveDatabase]
    let selection: String
    let connectedID: String
    let checking: Bool
    let error: String
    let refresh: () -> Void
    let choose: (String) -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text("选择项目库").font(Paper.font(23).weight(.semibold))
                Spacer()
                Button(checking ? "检测中…" : "刷新", action: refresh)
                    .buttonStyle(PaperButtonStyle(compact: true)).disabled(checking)
            }
            if !error.isEmpty {
                Text(error).font(Paper.font(12)).foregroundStyle(Paper.error)
            }
            if databases.isEmpty {
                Text(checking ? "正在检测项目库…" : "在 Resolve 中添加或连接项目库后刷新。")
                    .foregroundStyle(Paper.secondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    VStack(spacing: 0) {
                        ForEach(databases) { database in
                            Button {
                                choose(database.id)
                                dismiss()
                            } label: {
                                HStack(spacing: 12) {
                                    VStack(alignment: .leading, spacing: 4) {
                                        Text(database.DbName).font(Paper.font(14).weight(.medium))
                                        Text(database.IpAddress ?? (database.DbType == "Disk" ? "本地" : database.DbType))
                                            .font(Paper.font(12)).foregroundStyle(Paper.secondary)
                                    }
                                    Spacer()
                                    if database.id == connectedID && !checking {
                                        Text("已连接").font(Paper.font(12)).foregroundStyle(Paper.success)
                                    }
                                    Image(systemName: "checkmark")
                                        .opacity(database.id == selection ? 1 : 0)
                                        .accessibilityHidden(true)
                                }
                                .padding(.horizontal, 12).frame(minHeight: 54)
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            .accessibilityAddTraits(database.id == selection ? .isSelected : [])
                            PaperRule()
                        }
                    }
                }.disabled(checking)
            }
            HStack {
                Spacer()
                Button("关闭") { dismiss() }.buttonStyle(PaperButtonStyle()).keyboardShortcut(.cancelAction)
            }
        }
        .padding(24).frame(width: 450, height: 350)
        .foregroundStyle(Paper.ink).background(Paper.background)
    }
}
