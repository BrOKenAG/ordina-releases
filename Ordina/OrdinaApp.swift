import SwiftUI

final class OrdinaApplicationDelegate: NSObject, NSApplicationDelegate {
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        sender.windows.first(where: { $0.title == "ordina" })?.makeKeyAndOrderFront(nil)
        return true
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.appearance = NSAppearance(named: .darkAqua)
        QuickAccessController.shared.start()
        AppUpdater.shared.start()
        NSApp.windows.first(where: { $0.title == "ordina" })?.isReleasedWhenClosed = false
        if ProcessInfo.processInfo.environment["ORDO_UI_TEST"] != "1" {
            OnboardingController.shared.resume()
            ArchiveController.shared.resumeIfEnabled()
        }
    }
    func application(_ sender: NSApplication, openFiles filenames: [String]) {
        ArchiveController.shared.resumeIfEnabled()
        sender.reply(toOpenOrPrint: .success)
    }
}

@main
struct OrdinaApp: App {
    @NSApplicationDelegateAdaptor(OrdinaApplicationDelegate.self) private var delegate
    init() {
        let defaults = UserDefaults.standard
        if !defaults.bool(forKey: "migratedYouthSandboxSettings") {
            let oldPreferences = FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent("Library/Containers/com.example.MagicTools.Youth/Data/Library/Preferences/com.example.MagicTools.Youth.plist")
            if let data = try? Data(contentsOf: oldPreferences),
               let values = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any] {
                for key in ["savedTemplatesData", "selectedTemplateID", "destinationFolderBookmark"] {
                    if defaults.object(forKey: key) == nil, let value = values[key] {
                        defaults.set(value, forKey: key)
                    }
                }
            }
            defaults.set(true, forKey: "migratedYouthSandboxSettings")
        }
    }
    var body: some Scene {
        Window("ordina", id: "main") {
            OnboardingRootView()
                .font(Paper.font()).foregroundStyle(Paper.ink).tint(Paper.accent)
                .background(Paper.background)
                .preferredColorScheme(.dark)
                .environment(\.locale, Locale(identifier: "zh-Hans"))
                .frame(minWidth: 880, minHeight: 600)
        }
        .defaultSize(width: 960, height: 640)
        .commands {
            CommandGroup(after: .appInfo) {
                CheckForUpdatesButton()
            }
            CommandGroup(replacing: .newItem) {
                Button("新建项目") { QuickAccessController.shared.show() }.keyboardShortcut("n")
            }
            CommandGroup(after: .appSettings) {
                Button("设置…") { NotificationCenter.default.post(name: .ordinaOpenSettings, object: nil) }
                    .keyboardShortcut(",")
            }
        }
    }
}

private struct CheckForUpdatesButton: View {
    @ObservedObject private var updater = AppUpdater.shared

    var body: some View {
        Button("检查更新…", action: updater.checkForUpdates)
            .disabled(!updater.canCheckForUpdates)
    }
}
