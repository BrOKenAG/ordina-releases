import SwiftUI

struct GlassFieldBorder: View {
    let focused: Bool
    let working: Bool
    let outcome: String
    var cornerRadius: CGFloat = 20
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.controlActiveState) private var activeState
    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 30, paused: !focused || working || reduceMotion || activeState != .key || !outcome.isEmpty)) { context in
            let angle = reduceMotion ? 0 : context.date.timeIntervalSinceReferenceDate.truncatingRemainder(dividingBy: 7) / 7 * 360
            RoundedRectangle(cornerRadius: cornerRadius)
                .strokeBorder(
                    AngularGradient(colors: colors, center: .center, startAngle: .degrees(angle), endAngle: .degrees(angle + 360)),
                    lineWidth: outcome.isEmpty && !focused ? 1 : 1.3)
                .shadow(color: outcome == "failure" ? OrdinaTokens.error.opacity(0.12) : OrdinaTokens.focus.opacity(focused ? 0.12 : 0), radius: 8)
        }.allowsHitTesting(false)
    }
    private var colors: [Color] {
        if outcome == "success" { return [OrdinaTokens.success.opacity(0.7), OrdinaTokens.success.opacity(0.35)] }
        if outcome == "failure" { return [OrdinaTokens.error.opacity(0.8), OrdinaTokens.error.opacity(0.35)] }
        if focused || working { return [OrdinaTokens.rule, OrdinaTokens.accent.opacity(0.65), OrdinaTokens.focus.opacity(0.55), OrdinaTokens.rule, OrdinaTokens.accent.opacity(0.3)] }
        return [OrdinaTokens.rule]
    }
}

struct OrdinaCreateButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var enabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 14, weight: .medium))
            .foregroundStyle(OrdinaTokens.onAccent)
            .padding(.horizontal, 14).frame(minWidth: 76, minHeight: 44)
            .background(OrdinaTokens.accent.opacity(configuration.isPressed ? 0.76 : 1),
                        in: Capsule())
            .opacity(enabled ? 1 : OrdinaTokens.disabledOpacity)
            .contentShape(Capsule())
    }
}

struct OrdinaHomeBackground: View {
    var body: some View {
        LinearGradient(colors: [OrdinaTokens.canvasStart, OrdinaTokens.canvasEnd],
                       startPoint: .topLeading, endPoint: .bottomTrailing)
            .allowsHitTesting(false).accessibilityHidden(true)
    }
}
