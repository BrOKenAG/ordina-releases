import SwiftUI

struct WorkflowTemplateEditor: View {
    @ObservedObject var manager: TemplateManager
    var useForProject: ((UUID) -> Void)?
    @State private var drafts: [WorkflowTemplate]
    @State private var selectedID: UUID?
    @Environment(\.dismiss) private var dismiss

    init(manager: TemplateManager, projectTemplateID: UUID? = nil, useForProject: ((UUID) -> Void)? = nil) {
        self.manager = manager
        self.useForProject = useForProject
        _drafts = State(initialValue: manager.templates)
        _selectedID = State(initialValue: projectTemplateID ?? manager.selectedTemplateID ?? manager.templates.first?.id)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 23) {
            HStack {
                Text("模板管理").font(Paper.font(25))
                Spacer()
                Button(action: add) { DoodleIcon(.plus).frame(width: 19, height: 19) }
                    .buttonStyle(PaperQuietButton()).help("新建模板").accessibilityLabel("新建模板")
                Button(action: remove) { DoodleIcon(.trash).frame(width: 19, height: 19) }
                    .buttonStyle(PaperQuietButton()).disabled(drafts.count <= 1)
                    .help("删除模板").accessibilityLabel("删除模板")
            }
            PaperMenu(title: drafts.first(where: { $0.id == selectedID })?.displayName ?? "选择模板") {
                ForEach(drafts) { template in PaperMenuItem(template.displayName) { selectedID = template.id } }
            }
            .accessibilityLabel("工作流模板")
            if let draft = selectedDraft {
                PaperSection("模板名称") { PaperInput(placeholder: "输入模板名称", text: draft.name) }
                PaperSection("文件夹结构") {
                    TextEditor(text: draft.content)
                        .font(Paper.font(12)).scrollContentBackground(.hidden)
                        .padding(9).background(Paper.background)
                        .overlay { RoundedRectangle(cornerRadius: 10).strokeBorder(Paper.rule, lineWidth: 0.8) }
                        .help("每行一个文件夹，使用 / 建立子文件夹。")
                        .accessibilityLabel("文件夹结构")
                }
            }
            HStack {
                Button("恢复默认") { selectedDraft?.wrappedValue.content = manager.defaultContent }
                    .buttonStyle(PaperQuietButton())
                Spacer()
                Button("取消") { dismiss() }.buttonStyle(PaperButtonStyle())
                Button(useForProject == nil ? "保存" : "用于此项目") {
                    guard let selectedID else { return }
                    manager.templates = drafts
                    if let useForProject {
                        if !drafts.contains(where: { $0.id == manager.selectedTemplateID }) {
                            manager.selectedTemplateID = selectedID
                        }
                        useForProject(selectedID)
                    } else {
                        manager.selectedTemplateID = selectedID
                    }
                    dismiss()
                }
                .buttonStyle(PaperButtonStyle(primary: true)).keyboardShortcut(.defaultAction)
            }
        }
        .font(Paper.font()).foregroundStyle(Paper.ink).tint(Paper.accent)
        .padding(30).frame(width: 540, height: 510).background(Paper.background)
    }
    private var selectedDraft: Binding<WorkflowTemplate>? {
        guard let id = selectedID, let index = drafts.firstIndex(where: { $0.id == id }) else { return nil }
        return Binding(get: { drafts[index] }, set: { drafts[index] = $0 })
    }
    private func add() {
        let template = WorkflowTemplate(name: "新模板", content: "01_素材\n02_工程\n03_导出")
        drafts.append(template); selectedID = template.id
    }
    private func remove() {
        guard drafts.count > 1 else { return }
        drafts.removeAll { $0.id == selectedID }; selectedID = drafts.first?.id
    }
}

#Preview("模板管理") {
    WorkflowTemplateEditor(manager: TemplateManager.shared)
        .preferredColorScheme(.dark)
}
