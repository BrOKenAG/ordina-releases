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
        #if DEBUG || ORDINA_LAB
        let env = ProcessInfo.processInfo.environment
        if env["ORDINA_DEV_LAB"] == "1" {
            guard env["ORDO_UI_TEST"] == "1", env["ORDINA_DEV_BACKEND"] == "lab",
                  let home = env["HOME"], home.hasPrefix("/"),
                  let identity = Bundle.main.bundleIdentifier,
                  identity.hasPrefix("com.ordina.acceptance."),
                  let registry = env["ORDINA_PLUGIN_REGISTRY"], registry.hasPrefix("http://127.0.0.1:"),
                  let manifest = env["ORDINA_DEV_MANIFEST"], manifest.hasPrefix("http://127.0.0.1:"),
                  env["ORDINA_PLUGIN_PUBLIC_KEY"] != nil, env["ORDINA_DEV_MANIFEST_PUBLIC_KEY"] != nil
            else { fatalError("隔离验收配置不完整，已停止。") }
            let root = URL(fileURLWithPath: home).deletingLastPathComponent().resolvingSymlinksInPath().path + "/"
            let paths: [String?] = [Bundle.main.bundleURL.path, home, env["ORDINA_DEV_REPO"],
                         env["ORDINA_DEV_LAB_ROOT"], env["ORDINA_DEV_SIGN_KEY"], env["ORDINA_DEV_CREDENTIALS"]]
            guard paths.allSatisfy({ value in
                guard let value else { return false }
                return URL(fileURLWithPath: value).resolvingSymlinksInPath().path.hasPrefix(root)
            }) else { fatalError("隔离路径无效，已停止。") }
            return
        }
        #endif
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
