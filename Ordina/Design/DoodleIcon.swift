import SwiftUI

enum PaperGlyph { case folder, audio, download, verify, pet, check, close, plus, more, chevron, reveal, settings, trash, pause, play, link,
                         chevronLeft, chevronRight, refresh, alert, minus, turnDown, arrowRight }

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
        }
    }
    var body: some View {
        Image(systemName: symbol).resizable().scaledToFit().padding(2).accessibilityHidden(true)
    }
}
