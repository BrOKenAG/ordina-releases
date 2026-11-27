import SwiftUI

enum PaperGlyph { case folder, audio, download, verify, pet, check, close, plus, more, chevron, reveal, settings, trash, pause, play, link,
                         chevronLeft, chevronRight, refresh, alert, minus, turnDown, arrowRight, pluginGrid, sliders }

struct DoodleIcon: View {
    let glyph: PaperGlyph
    init(_ glyph: PaperGlyph) { self.glyph = glyph }
    private var symbol: String {
        switch glyph {
        case .folder: "folder"
        case .audio: "waveform"
        case .download: "arrow.down.to.line"
        case .verify: "checkmark.shield"
        case .pet: "pawprint"
        case .check: "checkmark"
        case .close: "xmark"
        case .plus: "plus"
        case .more: "ellipsis"
        case .chevron: "chevron.down"
        case .reveal: "arrow.up.right"
        case .settings: "gearshape"
        case .trash: "trash"
        case .pause: "pause"
        case .play: "play"
        case .link: "link"
        case .chevronLeft: "chevron.left"
        case .chevronRight: "chevron.right"
        case .refresh: "arrow.clockwise"
        case .alert: "exclamationmark.triangle"
        case .minus: "minus.circle"
        case .turnDown: "arrow.turn.down.right"
        case .arrowRight: "arrow.right"
        case .pluginGrid, .sliders: ""
        }
    }
    @ViewBuilder
    var body: some View {
        switch glyph {
        case .pluginGrid:
            PluginGridGlyph().frame(width: 24, height: 24).accessibilityHidden(true)
        case .sliders:
            SlidersGlyph().frame(width: 24, height: 24).accessibilityHidden(true)
        default:
            Image(systemName: symbol).resizable().scaledToFit().padding(2).accessibilityHidden(true)
        }
    }
}

private func strokeGlyph(_ path: Path) -> some View {
    path.stroke(Paper.secondary, style: StrokeStyle(lineWidth: 1.7, lineCap: .round, lineJoin: .round))
}

private struct PluginGridGlyph: View {
    var body: some View {
        ZStack {
            strokeGlyph(RoundedRectangle(cornerRadius: 2.4, style: .continuous)
                .path(in: CGRect(x: 2.5, y: 2.5, width: 8.5, height: 8.5)))
            strokeGlyph(RoundedRectangle(cornerRadius: 2.4, style: .continuous)
                .path(in: CGRect(x: 13, y: 2.5, width: 8.5, height: 8.5)))
            strokeGlyph(RoundedRectangle(cornerRadius: 2.4, style: .continuous)
                .path(in: CGRect(x: 2.5, y: 13, width: 8.5, height: 8.5)))
            strokeGlyph(Path { path in
                path.move(to: CGPoint(x: 13.5, y: 17.25))
                path.addLine(to: CGPoint(x: 21, y: 17.25))
                path.move(to: CGPoint(x: 17.25, y: 13.5))
                path.addLine(to: CGPoint(x: 17.25, y: 21))
            })
        }
    }
}

private struct SlidersGlyph: View {
    private let stops: [(CGFloat, CGFloat)] = [(6, 8), (12, 16), (18, 10)]
    var body: some View {
        ZStack {
            ForEach(stops, id: \.0) { y, _ in
                strokeGlyph(Path { path in
                    path.move(to: CGPoint(x: 2.5, y: y))
                    path.addLine(to: CGPoint(x: 21.5, y: y))
                })
            }
            ForEach(stops, id: \.0) { y, x in
                let frame = CGRect(x: x - 3.2, y: y - 3.2, width: 6.4, height: 6.4)
                Circle().path(in: frame).fill(Paper.background)
                strokeGlyph(Circle().path(in: frame))
            }
        }
    }
}
