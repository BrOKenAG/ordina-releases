import AppKit
import SwiftUI

enum OrdinaGeometry {
    static func monogram(in rect: CGRect, flipped: Bool = false) -> CGPath {
        let path = CGMutablePath()
        let bowl = CGPoint(x: 0.49, y: 0.43)
        let radius = 0.29
        let stemLeft = 0.625
        let shoulder = acos((stemLeft - bowl.x) / radius)

        path.move(to: CGPoint(x: bowl.x + radius, y: 0.84))
        path.addLine(to: CGPoint(x: stemLeft, y: 0.775))
        path.addLine(to: CGPoint(x: stemLeft, y: bowl.y + sin(shoulder) * radius))
        path.addArc(center: bowl, radius: radius, startAngle: shoulder, endAngle: 2 * .pi, clockwise: false)
        path.closeSubpath()

        let counterRadius = 0.151
        path.move(to: CGPoint(x: bowl.x + counterRadius, y: bowl.y))
        path.addRelativeArc(center: bowl, radius: counterRadius, startAngle: 0, delta: -2 * .pi)
        path.closeSubpath()

        let size = min(rect.width, rect.height)
        var transform = CGAffineTransform(a: size, b: 0, c: 0, d: flipped ? -size : size,
                                          tx: rect.midX - size / 2, ty: rect.midY + (flipped ? size / 2 : -size / 2))
        return path.copy(using: &transform)!
    }

    static var menuImage: NSImage {
        let image = NSImage(size: NSSize(width: 19, height: 19), flipped: false) { rect in
            guard let context = NSGraphicsContext.current?.cgContext else { return false }
            context.addPath(monogram(in: rect.insetBy(dx: -1, dy: -1)))
            context.setFillColor(NSColor.black.cgColor)
            context.fillPath()
            return true
        }
        image.isTemplate = true
        image.accessibilityDescription = "ordina"
        return image
    }
}
