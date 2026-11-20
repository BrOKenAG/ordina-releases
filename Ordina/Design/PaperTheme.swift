import SwiftUI

// Shared with the ordina Figma foundations. Native materials and system fonts
// stay native; these tokens describe color, rhythm and control geometry.
enum OrdinaTokens {
    private static let colors: [String: UInt32] = [
        "gray/50": 0xF6F7F9, "gray/100": 0xEEF1F5, "gray/200": 0xDCE2E9,
        "gray/400": 0x98A5B3, "gray/500": 0x657382, "gray/550": 0x606F7E, "gray/700": 0x34404D,
        "gray/800": 0x222C38, "gray/900": 0x171E29, "gray/950": 0x10151E,
        "white": 0xFFFFFF, "black": 0x000000, "blue/300": 0xA8C1DC,
        "blue/600": 0x4D7096, "violet/500": 0x8C82D0, "teal/900": 0x193139,
        "green/600": 0x22855E, "green/650": 0x207B56, "green/400": 0x75C9A0,
        "amber/600": 0x9D6826, "amber/650": 0x93601F, "amber/400": 0xD9A05B,
        "red/600": 0xB94A58, "red/650": 0xB14654, "red/400": 0xEF8994, "cream": 0xF0E8D4
    ]
    private static func native(_ name: String) -> NSColor {
        if name == "glass/light" { return .white.withAlphaComponent(173.0 / 255) }
        if name == "glass/dark" { return .white.withAlphaComponent(15.0 / 255) }
        if name == "border/light" { return NSColor(srgbRed: 23.0 / 255, green: 32.0 / 255, blue: 46.0 / 255, alpha: 38.0 / 255) }
        if name == "border/dark" { return .white.withAlphaComponent(36.0 / 255) }
        let hex = colors[name] ?? 0x171E29
        return NSColor(srgbRed: Double((hex >> 16) & 255) / 255,
                       green: Double((hex >> 8) & 255) / 255,
                       blue: Double(hex & 255) / 255, alpha: 1)
    }
    static func palette(_ name: String) -> Color { Color(nsColor: native(name)) }
    private static func adaptive(_ light: String, _ dark: String) -> Color {
        Color(nsColor: NSColor(name: nil) { appearance in
            native(appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua ? dark : light)
        })
    }
    static let bg = adaptive("gray/50", "gray/950")
    static let surface = adaptive("white", "gray/900")
    static let glass = adaptive("glass/light", "glass/dark")
    static let ink = adaptive("gray/900", "gray/50")
    static let secondary = adaptive("gray/550", "gray/400")
    static let faint = secondary
    static let rule = adaptive("border/light", "border/dark")
    static let accent = adaptive("blue/600", "blue/300")
    static let onAccent = adaptive("white", "gray/950")
    static let success = adaptive("green/650", "green/400")
    // Warning amber stays visually distinct from the cream brand accent in dark
    // mode; red is reserved for errors, and the brand mark follows the app icon's
    // warm-silver-on-graphite treatment instead of sharing the error hue.
    static let warning = adaptive("amber/650", "amber/400")
    static let error = adaptive("red/650", "red/400")
    static let hover = adaptive("gray/100", "gray/800")
    static let selected = adaptive("gray/200", "gray/700")
    static let canvasStart = bg
    static let canvasEnd = adaptive("gray/100", "teal/900")
    static let focus = palette("violet/500")
    static let brand = adaptive("gray/900", "cream")
    static let cream = palette("cream")
    static let homeWidth: CGFloat = 640
    static let motion: Double = 0.18
    static let disabledOpacity = 0.38
    static func space(_ value: CGFloat) -> CGFloat { value }
    static func radius(_ value: CGFloat) -> CGFloat { value }
    static func height(_ value: CGFloat) -> CGFloat { value }
    static func type(_ size: CGFloat) -> Font { .system(size: size) }
}

enum Paper {
    static let background = OrdinaTokens.bg
    static let surface = OrdinaTokens.surface
    static let onAccent = OrdinaTokens.onAccent
    static let ink = OrdinaTokens.ink
    static let secondary = OrdinaTokens.secondary
    static let faint = OrdinaTokens.faint
    static let rule = OrdinaTokens.rule
    static let accent = OrdinaTokens.accent
    static let success = OrdinaTokens.success
    static let warning = OrdinaTokens.warning
    static let error = OrdinaTokens.error
    static func font(_ size: CGFloat = 14) -> Font { OrdinaTokens.type(size) }

}

struct PaperSection<Content: View>: View {
    let title: String
    @ViewBuilder let content: Content
    init(_ title: String, @ViewBuilder content: () -> Content) {
        self.title = title; self.content = content()
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            PaperMediaTitle(title, size: 11).foregroundStyle(Paper.secondary)
            content
        }
    }
}

struct PaperButtonStyle: ButtonStyle {
    var primary = false
    var compact = false
    @Environment(\.isEnabled) private var enabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(Paper.font(compact ? 12 : 14).weight(.medium))
            .foregroundStyle(primary ? Paper.onAccent : Paper.ink)
            .padding(.horizontal, compact ? 10 : 16)
            .frame(minHeight: compact ? 36 : 44)
            .background(primary ? Paper.accent.opacity(configuration.isPressed ? 0.76 : 0.94) : Paper.ink.opacity(configuration.isPressed ? 0.06 : 0),
                        in: RoundedRectangle(cornerRadius: 10))
            .overlay {
                if !primary { RoundedRectangle(cornerRadius: 10).strokeBorder(Paper.rule, lineWidth: 0.8) }
            }
            .opacity(enabled ? 1 : OrdinaTokens.disabledOpacity)
            .contentShape(Rectangle())
    }
}

struct PaperQuietButton: ButtonStyle {
    @Environment(\.isEnabled) private var enabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(Paper.font(12))
            .foregroundStyle(Paper.secondary)
            .padding(8)
            .frame(minWidth: 36, minHeight: 36)
            .opacity(enabled ? (configuration.isPressed ? 0.5 : 1) : 0.3)
            .contentShape(Rectangle())
    }
}

struct PaperInput: View {
    var placeholder: String
    @Binding var text: String
    var label: String? = nil
    /// External focus binding so callers can autofocus or keyboard-drive the field.
    var focus: FocusState<Bool>.Binding? = nil
    @FocusState private var focused: Bool
    private var isFocused: Bool { focus?.wrappedValue ?? focused }
    private var core: some View {
        TextField(placeholder, text: $text)
            .textFieldStyle(.plain)
            .font(Paper.font())
            .padding(.horizontal, 12).padding(.vertical, 11)
            .background(Paper.surface, in: RoundedRectangle(cornerRadius: 10))
            .overlay { RoundedRectangle(cornerRadius: 10).strokeBorder(isFocused ? Paper.ink.opacity(0.55) : Paper.rule, lineWidth: 0.8) }
    }
    var body: some View {
        Group {
            if let focus {
                core.focused(focus)
            } else {
                core.focused($focused)
            }
        }
        .focusEffectDisabled()
        .accessibilityLabel(label ?? placeholder)
    }
}

struct PaperRule: View {
    var body: some View { Rectangle().fill(Paper.rule).frame(height: 0.6) }
}

struct PaperToggleStyle: ToggleStyle {
    var fontSize: CGFloat = 12
    func makeBody(configuration: Configuration) -> some View {
        Button { configuration.isOn.toggle() } label: {
            HStack(spacing: 8) {
                ZStack {
                    RoundedRectangle(cornerRadius: 1).strokeBorder(Paper.ink.opacity(0.4), lineWidth: 0.8)
                    if configuration.isOn { DoodleIcon(.check).padding(1) }
                }
                .frame(width: 13, height: 13)
                configuration.label.font(Paper.font(fontSize))
            }
            .foregroundStyle(Paper.ink)
        }
        .buttonStyle(.plain)
        .accessibilityValue(configuration.isOn ? "开启" : "关闭")
        .accessibilityAddTraits(configuration.isOn ? [.isSelected] : [])
    }
}

struct PaperProgress: View {
    let value: Double
    var body: some View {
        GeometryReader { proxy in
            ZStack(alignment: .leading) {
                Rectangle().fill(Paper.rule)
                Rectangle().fill(Paper.ink.opacity(0.7)).frame(width: proxy.size.width * max(0, min(1, value)))
            }
        }
        .frame(height: 2)
        .accessibilityValue("\(Int(max(0, min(1, value)) * 100)) 百分比")
    }
}

// The label, whitespace and chevron all belong to the same button.
// A SwiftUI popover keeps the paper typography instead of NSMenu's system font.
struct PaperMenu<Options: View>: View {
    let title: String
    @ViewBuilder var options: () -> Options
    @State private var isPresented = false
    @State private var width: CGFloat = 200

    var body: some View {
        Button { isPresented.toggle() } label: {
            HStack(spacing: 12) {
                PaperMediaTitle(title, size: 13)
                Spacer(minLength: 0)
                DoodleIcon(.chevron).frame(width: 12, height: 12)
                    .foregroundStyle(Paper.secondary)
            }
            .padding(.horizontal, 12)
            .frame(maxWidth: .infinity, minHeight: 40, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .foregroundStyle(Paper.ink)
        .overlay { RoundedRectangle(cornerRadius: 10).strokeBorder(Paper.rule, lineWidth: 0.8).allowsHitTesting(false) }
        .background {
            GeometryReader { geometry in
                Color.clear.onAppear { width = geometry.size.width }
                    .onChange(of: geometry.size.width) { _, value in width = value }
            }
        }
        .accessibilityValue(title)
        .popover(isPresented: $isPresented, arrowEdge: .bottom) {
            ScrollView {
                VStack(alignment: .leading, spacing: 2) { options() }
                    .environment(\.paperMenuDismiss, { isPresented = false })
                    .padding(6)
            }
            .scrollBounceBehavior(.basedOnSize)
            .fixedSize(horizontal: false, vertical: true)
            .frame(width: min(420, max(180, width)))
            .frame(maxHeight: 360)
            .background(Paper.background)
            .overlay { RoundedRectangle(cornerRadius: 10).strokeBorder(Paper.rule, lineWidth: 0.8) }
            .padding(8)
            .presentationBackground(Paper.background)
            
        }
    }
}

private struct PaperMenuDismissKey: EnvironmentKey {
    static let defaultValue: () -> Void = {}
}

private extension EnvironmentValues {
    var paperMenuDismiss: () -> Void {
        get { self[PaperMenuDismissKey.self] }
        set { self[PaperMenuDismissKey.self] = newValue }
    }
}

struct PaperMenuItem: View {
    let title: String
    let action: () -> Void
    @Environment(\.paperMenuDismiss) private var closeMenu
    @Environment(\.dismiss) private var dismissPopover
    @State private var hovering = false

    init(_ title: String, action: @escaping () -> Void) {
        self.title = title
        self.action = action
    }

    var body: some View {
        Button {
            action()
            closeMenu()
            dismissPopover()
        } label: {
            PaperMediaTitle(title, size: 13)
                .foregroundStyle(Paper.ink)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 12).padding(.vertical, 9)
                .background(hovering ? Paper.rule.opacity(0.45) : Color.clear)
                .clipShape(RoundedRectangle(cornerRadius: 6))
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
    }
}

/// Component gallery for canvas iteration: every paper control at a glance.
#Preview("Paper 组件库") {
    ScrollView {
        VStack(alignment: .leading, spacing: 28) {
            PaperSection("按钮") {
                HStack(spacing: 12) {
                    Button("新建项目") {}.buttonStyle(PaperButtonStyle(primary: true))
                    Button("取消") {}.buttonStyle(PaperButtonStyle())
                    Button("刷新") {}.buttonStyle(PaperButtonStyle(compact: true))
                    Button("稍后设置") {}.buttonStyle(PaperQuietButton())
                }
            }
            PaperSection("输入") {
                PaperInput(placeholder: "你的下一个项目叫什么？", text: .constant(""), label: "项目名称")
            }
            PaperSection("选择器") {
                PaperMenu(title: "完整工作流") {
                    PaperMenuItem("完整工作流") {}
                    PaperMenuItem("纪录片短模板") {}
                }
            }
            PaperSection("开关") {
                Toggle("同时创建 Resolve 工程", isOn: .constant(true)).toggleStyle(PaperToggleStyle())
                Toggle("后台扫描", isOn: .constant(true)).toggleStyle(PaperToggleStyle())
            }
            PaperSection("进度") {
                PaperProgress(value: 0.62).frame(width: 260)
            }
            PaperSection("状态标签") {
                HStack(spacing: 8) {
                    Text("已归档").font(Paper.font(12)).foregroundStyle(Paper.success)
                    Text("待归档").font(Paper.font(12)).foregroundStyle(Paper.accent)
                    Text("离线").font(Paper.font(12)).foregroundStyle(Paper.error)
                    Text("警告").font(Paper.font(12)).foregroundStyle(Paper.warning)
                }
            }
            PaperSection("分隔线") { PaperRule() }
        }
        .padding(36)
        .frame(width: 520)
    }
    .background(Paper.background)
    .preferredColorScheme(.dark)
}
