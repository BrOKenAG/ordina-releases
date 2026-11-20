import AppKit

@MainActor
final class QuickAccessController: NSObject {
    static let shared = QuickAccessController()
    private var item: NSStatusItem?

    func start() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        item.button?.image = OrdinaGeometry.menuImage
        item.button?.target = self
        item.button?.action = #selector(activateStatusItem)
        item.button?.sendAction(on: [.leftMouseUp, .rightMouseUp])
        item.button?.toolTip = "打开 ordina"
        self.item = item
    }

    @objc private func activateStatusItem() {
        if NSApp.currentEvent?.type == .rightMouseUp, let button = item?.button {
            let menu = NSMenu()
            menu.addItem(withTitle: "退出 ordina", action: #selector(quit), keyEquivalent: "").target = self
            menu.popUp(positioning: nil, at: NSPoint(x: 0, y: button.bounds.minY), in: button)
        } else {
            show()
        }
    }

    @objc private func quit() { NSApp.terminate(nil) }

    func show() {
        NSApp.activate(ignoringOtherApps: true)
        NSApp.windows.first { $0.title == "ordina" }?.makeKeyAndOrderFront(nil)
        NotificationCenter.default.post(name: .ordinaFocusCreation, object: nil)
    }
}
