import AppKit

@main
struct RenderOrdinaIcon {
    static func main() throws {
        let root = URL(fileURLWithPath: CommandLine.arguments[1])
        let assets = root.appendingPathComponent("Ordina/Assets.xcassets/AppIcon.appiconset")
        let manifest = try JSONSerialization.jsonObject(with: Data(contentsOf: assets.appendingPathComponent("Contents.json"))) as! [String: Any]
        let images = manifest["images"] as! [[String: String]]
        for image in images {
            let size = Int(image["size"]!.components(separatedBy: "x")[0])!
            let scale = image["scale"] == "2x" ? 2 : 1
            let pixels = size * scale
            let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels,
                                         bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
                                         isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
            let graphics = NSGraphicsContext(bitmapImageRep: bitmap)!
            NSGraphicsContext.saveGraphicsState()
            NSGraphicsContext.current = graphics
            let context = graphics.cgContext
            context.scaleBy(x: CGFloat(pixels) / 1024, y: CGFloat(pixels) / 1024)
            let tile = CGRect(x: 80, y: 80, width: 864, height: 864)
            context.setShadow(offset: CGSize(width: 0, height: -8), blur: 20, color: NSColor.black.withAlphaComponent(0.16).cgColor)
            context.addPath(CGPath(roundedRect: tile, cornerWidth: 190, cornerHeight: 190, transform: nil))
            context.setFillColor(NSColor(srgbRed: 0.16, green: 0.175, blue: 0.19, alpha: 1).cgColor)
            context.fillPath()
            context.setShadow(offset: .zero, blur: 0, color: nil)
            context.addPath(CGPath(roundedRect: tile.insetBy(dx: 1, dy: 1), cornerWidth: 189, cornerHeight: 189, transform: nil))
            context.setStrokeColor(NSColor.white.withAlphaComponent(0.09).cgColor)
            context.setLineWidth(2)
            context.strokePath()
            context.addPath(OrdinaGeometry.monogram(in: CGRect(x: 64, y: 64, width: 896, height: 896)))
            context.setFillColor(NSColor(srgbRed: 0.93, green: 0.91, blue: 0.86, alpha: 1).cgColor)
            context.fillPath()
            NSGraphicsContext.restoreGraphicsState()
            try bitmap.representation(using: .png, properties: [:])!.write(to: assets.appendingPathComponent(image["filename"]!))
        }
        let preview = root.appendingPathComponent("design/ordina-monogram.png")
        try Data(contentsOf: assets.appendingPathComponent("icon_512x512@2x.png")).write(to: preview, options: .atomic)
        print(preview.path)
    }
}
