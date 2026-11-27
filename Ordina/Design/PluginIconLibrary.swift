import SwiftUI

/// Built-in linear icon set for plugin catalogue entries.
/// IDs are stable lowercase ASCII strings. A missing or unknown ID always
/// resolves to ``PluginIcon/plugin`` so older clients, older ZIPs and edited
/// feeds keep rendering a safe default instead of breaking layout.
enum PluginIcon: String, CaseIterable, Identifiable, Sendable {
    case plugin, cut, timeline, trim, transition, multicam, camera
    case subtitle, caption, text, note, review, comment
    case audio, mic, volume, music, marker, tag
    case automation, script, batch, export, shield

    var id: String { rawValue }

    var label: String {
        switch self {
        case .plugin: return "默认"
        case .cut: return "剪辑"
        case .timeline: return "时间线"
        case .trim: return "裁切"
        case .transition: return "转场"
        case .multicam: return "多机位"
        case .camera: return "摄影机"
        case .subtitle: return "字幕"
        case .caption: return "对白"
        case .text: return "文稿"
        case .note: return "批注"
        case .review: return "审阅"
        case .comment: return "评论"
        case .audio: return "波形"
        case .mic: return "录音"
        case .volume: return "音量"
        case .music: return "音乐"
        case .marker: return "标记"
        case .tag: return "标签"
        case .automation: return "自动化"
        case .script: return "脚本"
        case .batch: return "批量"
        case .export: return "导出"
        case .shield: return "校验"
        }
    }

    static let fallback: PluginIcon = .plugin

    static func resolve(_ id: String?) -> PluginIcon? {
        guard let id, !id.isEmpty else { return nil }
        return PluginIcon(rawValue: id)
    }

    /// Compatibility entry point: unknown IDs fall back to the default glyph.
    static func glyph(_ id: String?) -> PaperGlyph {
        .pluginIcon(resolve(id) ?? .fallback)
    }

    // All shapes share the 24x24 canvas, 1.7pt stroke, round caps and 2.4pt
    // corners already used by DoodleIcon's custom glyphs.
    static func shape(_ icon: PluginIcon) -> [Path] {
        switch icon {
        case .plugin:
            return [box(2.5, 2.5, 8.5, 8.5), box(13, 2.5, 8.5, 8.5), box(2.5, 13, 8.5, 8.5),
                    line(13.5, 17.25, 21, 17.25), line(17.25, 13.5, 17.25, 21)]
        case .cut:
            return [box(2.5, 6, 8, 12), box(13.5, 6, 8, 12),
                    line(8, 3, 16, 21), line(16, 3, 8, 21)]
        case .timeline:
            return [line(2.5, 7, 21.5, 7), line(2.5, 12, 21.5, 12), line(2.5, 17, 21.5, 17),
                    line(7.5, 4.5, 7.5, 9.5), line(15, 9.5, 15, 14.5), line(11, 14.5, 11, 19.5)]
        case .trim:
            return [line(7, 4, 3, 4), line(3, 4, 3, 20), line(3, 20, 7, 20),
                    line(17, 4, 21, 4), line(21, 4, 21, 20), line(21, 20, 17, 20),
                    line(9, 12, 15, 12)]
        case .transition:
            return [box(2.5, 5, 12, 9), box(9.5, 10, 12, 9), line(6, 17, 18, 17)]
        case .multicam:
            return [box(2.5, 2.5, 8.5, 8.5), box(13, 2.5, 8.5, 8.5),
                    box(2.5, 13, 8.5, 8.5), box(13, 13, 8.5, 8.5),
                    dot(6.75, 6.75, 1.2), dot(17.25, 6.75, 1.2), dot(17.25, 17.25, 1.2)]
        case .camera:
            return [box(2.5, 7, 13, 10, 3), Path { p in
                p.move(to: CGPoint(x: 15.5, y: 10)); p.addLine(to: CGPoint(x: 21, y: 6.5))
                p.addLine(to: CGPoint(x: 21, y: 17.5)); p.addLine(to: CGPoint(x: 15.5, y: 14))
                p.closeSubpath()
            }, dot(9, 12, 2.6)]
        case .subtitle:
            return [box(2.5, 5, 19, 14, 3), line(6, 10, 18, 10), line(6, 14, 14, 14)]
        case .caption:
            return [Path { p in
                p.move(to: CGPoint(x: 4, y: 5)); p.addLine(to: CGPoint(x: 20, y: 5))
                p.addQuadCurve(to: CGPoint(x: 20, y: 9), control: CGPoint(x: 20, y: 5))
                p.addLine(to: CGPoint(x: 20, y: 15)); p.addQuadCurve(to: CGPoint(x: 20, y: 19), control: CGPoint(x: 20, y: 19))
                p.addLine(to: CGPoint(x: 9, y: 19)); p.addLine(to: CGPoint(x: 5, y: 22))
                p.addLine(to: CGPoint(x: 5, y: 19)); p.addLine(to: CGPoint(x: 4, y: 19))
                p.addQuadCurve(to: CGPoint(x: 4, y: 15), control: CGPoint(x: 4, y: 19))
                p.addLine(to: CGPoint(x: 4, y: 9)); p.addQuadCurve(to: CGPoint(x: 4, y: 5), control: CGPoint(x: 4, y: 5))
            }, line(8, 10, 16, 10), line(8, 14, 14, 14)]
        case .text:
            return [Path { p in
                p.move(to: CGPoint(x: 5, y: 2.5)); p.addLine(to: CGPoint(x: 14, y: 2.5))
                p.addLine(to: CGPoint(x: 19, y: 7.5)); p.addLine(to: CGPoint(x: 19, y: 21.5))
                p.addLine(to: CGPoint(x: 5, y: 21.5)); p.closeSubpath()
            }, line(14, 2.5, 14, 7.5), line(14, 7.5, 19, 7.5),
                    line(8, 12, 16, 12), line(8, 16, 16, 16)]
        case .note:
            return [box(4, 2.5, 16, 19, 2.4), line(7.5, 8, 16.5, 8), line(7.5, 12, 13, 12),
                    line(9, 18, 11.5, 15.5), line(11.5, 15.5, 17, 15.5)]
        case .review:
            return [dot(10.5, 10.5, 6.5), line(15.5, 15.5, 21, 21),
                    line(8, 10.5, 10, 12.5), line(10, 12.5, 13.5, 8.5)]
        case .comment:
            return [box(2.5, 4, 19, 14, 3), line(7, 18, 7, 21), line(7, 21, 12, 18),
                    dot(8, 11, 0.9), dot(12, 11, 0.9), dot(16, 11, 0.9)]
        case .audio:
            return [line(3, 12, 21, 12), line(6, 8, 6, 16), line(10, 5, 10, 19),
                    line(14, 7.5, 14, 16.5), line(18, 9.5, 18, 14.5)]
        case .mic:
            return [Path { p in
                p.move(to: CGPoint(x: 12, y: 2.5)); p.addCurve(to: CGPoint(x: 17, y: 7.5),
                    control1: CGPoint(x: 14.8, y: 2.5), control2: CGPoint(x: 17, y: 4.7))
                p.addLine(to: CGPoint(x: 17, y: 12)); p.addCurve(to: CGPoint(x: 12, y: 17),
                    control1: CGPoint(x: 17, y: 14.8), control2: CGPoint(x: 14.8, y: 17))
                p.addCurve(to: CGPoint(x: 7, y: 12), control1: CGPoint(x: 9.2, y: 17), control2: CGPoint(x: 7, y: 14.8))
                p.addLine(to: CGPoint(x: 7, y: 7.5)); p.addCurve(to: CGPoint(x: 12, y: 2.5),
                    control1: CGPoint(x: 7, y: 4.7), control2: CGPoint(x: 9.2, y: 2.5))
            }, line(4, 11, 4, 13), line(4, 11, 8, 11), line(4, 13, 8, 13),
                    line(20, 11, 20, 13), line(16, 11, 20, 11), line(16, 13, 20, 13),
                    line(12, 17, 12, 21), line(8.5, 21, 15.5, 21)]
        case .volume:
            return [Path { p in
                p.move(to: CGPoint(x: 3, y: 9.5)); p.addLine(to: CGPoint(x: 7, y: 9.5))
                p.addLine(to: CGPoint(x: 12, y: 5)); p.addLine(to: CGPoint(x: 12, y: 19))
                p.addLine(to: CGPoint(x: 7, y: 14.5)); p.addLine(to: CGPoint(x: 3, y: 14.5))
                p.closeSubpath()
            }, Path { p in
                p.move(to: CGPoint(x: 15.5, y: 9)); p.addCurve(to: CGPoint(x: 15.5, y: 15),
                    control1: CGPoint(x: 18.5, y: 10), control2: CGPoint(x: 18.5, y: 14))
            }, Path { p in
                p.move(to: CGPoint(x: 18, y: 6.5)); p.addCurve(to: CGPoint(x: 18, y: 17.5),
                    control1: CGPoint(x: 22, y: 8.5), control2: CGPoint(x: 22, y: 15.5))
            }]
        case .music:
            return [line(9, 17, 9, 5), line(9, 5, 18, 3), line(18, 3, 18, 15),
                    dot(6.5, 18, 2.5), dot(15.5, 16, 2.5)]
        case .marker:
            return [Path { p in
                p.move(to: CGPoint(x: 12, y: 21.5)); p.addCurve(to: CGPoint(x: 18.5, y: 12),
                    control1: CGPoint(x: 18, y: 17), control2: CGPoint(x: 18.5, y: 14))
                p.addCurve(to: CGPoint(x: 12, y: 2.5), control1: CGPoint(x: 18.5, y: 7.7), control2: CGPoint(x: 15.5, y: 2.5))
                p.addCurve(to: CGPoint(x: 5.5, y: 12), control1: CGPoint(x: 8.5, y: 2.5), control2: CGPoint(x: 5.5, y: 7.7))
                p.addCurve(to: CGPoint(x: 12, y: 21.5), control1: CGPoint(x: 5.5, y: 14), control2: CGPoint(x: 6, y: 17))
            }, dot(12, 12, 3)]
        case .tag:
            return [Path { p in
                p.move(to: CGPoint(x: 12, y: 3)); p.addLine(to: CGPoint(x: 21, y: 3))
                p.addLine(to: CGPoint(x: 21, y: 12)); p.addLine(to: CGPoint(x: 11, y: 21))
                p.addLine(to: CGPoint(x: 3, y: 13)); p.closeSubpath()
            }, dot(17, 7, 1.3)]
        case .automation:
            return [Path { p in
                p.move(to: CGPoint(x: 5, y: 8)); p.addCurve(to: CGPoint(x: 19, y: 8),
                    control1: CGPoint(x: 8, y: 3), control2: CGPoint(x: 16, y: 3))
            }, line(19, 8, 16, 5.5), line(19, 8, 16, 10.5),
                    Path { p in
                p.move(to: CGPoint(x: 19, y: 16)); p.addCurve(to: CGPoint(x: 5, y: 16),
                    control1: CGPoint(x: 16, y: 21), control2: CGPoint(x: 8, y: 21))
            }, line(5, 16, 8, 13.5), line(5, 16, 8, 18.5), dot(12, 12, 1.4)]
        case .script:
            return [Path { p in
                p.move(to: CGPoint(x: 9, y: 3)); p.addCurve(to: CGPoint(x: 6, y: 6),
                    control1: CGPoint(x: 6, y: 3), control2: CGPoint(x: 6, y: 4.5))
                p.addLine(to: CGPoint(x: 6, y: 9)); p.addCurve(to: CGPoint(x: 9, y: 12),
                    control1: CGPoint(x: 6, y: 10.5), control2: CGPoint(x: 7, y: 12))
                p.addCurve(to: CGPoint(x: 6, y: 15), control1: CGPoint(x: 7, y: 12), control2: CGPoint(x: 6, y: 13.5))
                p.addLine(to: CGPoint(x: 6, y: 18)); p.addCurve(to: CGPoint(x: 9, y: 21),
                    control1: CGPoint(x: 6, y: 19.5), control2: CGPoint(x: 6, y: 21))
            }, Path { p in
                p.move(to: CGPoint(x: 15, y: 3)); p.addCurve(to: CGPoint(x: 18, y: 6),
                    control1: CGPoint(x: 18, y: 3), control2: CGPoint(x: 18, y: 4.5))
                p.addLine(to: CGPoint(x: 18, y: 9)); p.addCurve(to: CGPoint(x: 15, y: 12),
                    control1: CGPoint(x: 18, y: 10.5), control2: CGPoint(x: 17, y: 12))
                p.addCurve(to: CGPoint(x: 18, y: 15), control1: CGPoint(x: 17, y: 12), control2: CGPoint(x: 18, y: 13.5))
                p.addLine(to: CGPoint(x: 18, y: 18)); p.addCurve(to: CGPoint(x: 15, y: 21),
                    control1: CGPoint(x: 18, y: 19.5), control2: CGPoint(x: 18, y: 21))
            }]
        case .batch:
            return [box(4, 3, 16, 6), box(2.5, 9, 16, 6), box(4, 15, 16, 6)]
        case .export:
            return [box(3, 10, 18, 11, 2.4), line(12, 3, 12, 13),
                    line(8.5, 6.5, 12, 3), line(12, 3, 15.5, 6.5)]
        case .shield:
            return [Path { p in
                p.move(to: CGPoint(x: 12, y: 2.5)); p.addLine(to: CGPoint(x: 20, y: 5.5))
                p.addLine(to: CGPoint(x: 20, y: 12)); p.addCurve(to: CGPoint(x: 12, y: 21.5),
                    control1: CGPoint(x: 20, y: 17), control2: CGPoint(x: 16.5, y: 20))
                p.addCurve(to: CGPoint(x: 4, y: 12), control1: CGPoint(x: 7.5, y: 20), control2: CGPoint(x: 4, y: 17))
                p.addLine(to: CGPoint(x: 4, y: 5.5)); p.closeSubpath()
            }, line(8.5, 12, 11, 14.5), line(11, 14.5, 16, 9)]
        }
    }
}

private func box(_ x: CGFloat, _ y: CGFloat, _ w: CGFloat, _ h: CGFloat,
                 _ radius: CGFloat = 2.4) -> Path {
    RoundedRectangle(cornerRadius: radius, style: .continuous)
        .path(in: CGRect(x: x, y: y, width: w, height: h))
}

private func dot(_ x: CGFloat, _ y: CGFloat, _ r: CGFloat) -> Path {
    Circle().path(in: CGRect(x: x - r, y: y - r, width: r * 2, height: r * 2))
}

private func line(_ x1: CGFloat, _ y1: CGFloat, _ x2: CGFloat, _ y2: CGFloat) -> Path {
    Path { p in
        p.move(to: CGPoint(x: x1, y: y1))
        p.addLine(to: CGPoint(x: x2, y: y2))
    }
}

/// Renders one built-in glyph with the shared DoodleIcon stroke.
struct PluginIconGlyph: View {
    let icon: PluginIcon
    var tint: Color = Paper.secondary
    var body: some View {
        let paths = PluginIcon.shape(icon)
        return ZStack {
            ForEach(paths.indices, id: \.self) { index in strokeGlyph(paths[index], tint: tint) }
        }
        .frame(width: 24, height: 24)
        .accessibilityHidden(true)
    }
}

/// Grid selector with a live preview. Binding stores the compatibility
/// ``iconID`` string; nil selects the default icon.
struct PluginIconPicker: View {
    @Binding var iconID: String?
    private let columns = Array(repeating: GridItem(.fixed(34), spacing: 8), count: 6)

    private var selection: PluginIcon { PluginIcon.resolve(iconID) ?? .fallback }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                Text("图标").font(Paper.font(11)).foregroundStyle(Paper.secondary)
                Spacer()
                HStack(spacing: 6) {
                    PluginIconGlyph(icon: selection)
                    Text(selection.label).font(Paper.font(11)).foregroundStyle(Paper.secondary)
                }
            }
            LazyVGrid(columns: columns, spacing: 8) {
                ForEach(PluginIcon.allCases) { icon in
                    Button {
                        iconID = icon == .fallback ? nil : icon.rawValue
                    } label: {
                        PluginIconGlyph(icon: icon)
                            .frame(width: 32, height: 32)
                            .background(Paper.surface, in: RoundedRectangle(cornerRadius: 6))
                            .overlay {
                                RoundedRectangle(cornerRadius: 6)
                                    .strokeBorder(iconID == (icon == .fallback ? nil : icon.rawValue)
                                                  ? Paper.accent : Paper.rule, lineWidth: 1)
                            }
                    }
                    .buttonStyle(.plain)
                    .help(icon.label)
                    .accessibilityLabel(icon.label)
                }
            }
        }
    }
}
