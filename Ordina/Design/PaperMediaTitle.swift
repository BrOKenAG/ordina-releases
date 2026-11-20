import AppKit
import CoreText
import SwiftUI

enum PaperMediaTypography {
    /// Explicit fallback runs let layout include Chinese/emoji ascenders and descenders.
    static func title(_ text: String, size: CGFloat) -> NSAttributedString {
        let base = NSFont.systemFont(ofSize: size) as CTFont
        let result = NSMutableAttributedString(string: text)
        var offset = 0
        for character in text {
            let length = String(character).utf16.count
            let range = CFRange(location: offset, length: length)
            let font = CTFontCreateForString(base, text as CFString, range)
            result.addAttribute(.font, value: font as NSFont, range: NSRange(location: offset, length: length))
            offset += length
        }
        return result
    }

    static func swiftUITitle(_ text: String, size: CGFloat) -> AttributedString {
        let native = title(text, size: size)
        var result = AttributedString()
        native.enumerateAttribute(.font, in: NSRange(location: 0, length: native.length)) { value, range, _ in
            var run = AttributedString((text as NSString).substring(with: range))
            if let font = value as? NSFont { run.font = Font(font) }
            result.append(run)
        }
        return result
    }
}

struct PaperMediaTitle: View {
    let text: String
    var size: CGFloat = 12
    var lines = 1
    var truncation: Text.TruncationMode = .tail
    init(_ text: String, size: CGFloat = 12, lines: Int = 1, truncation: Text.TruncationMode = .tail) {
        self.text = text; self.size = size; self.lines = lines; self.truncation = truncation
    }
    var body: some View {
        Text(PaperMediaTypography.swiftUITitle(text, size: size))
            .lineLimit(lines)
            .truncationMode(truncation)
            .fixedSize(horizontal: false, vertical: true)
            // Leave room for fractional fallback-font extents at display rounding.
            .padding(.vertical, 1)
            .accessibilityLabel(text)
            .help(text)
    }
}
