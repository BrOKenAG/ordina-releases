import Combine
import Foundation
import Sparkle

@MainActor
final class AppUpdater: ObservableObject {
    static let shared = AppUpdater()

    @Published private(set) var canCheckForUpdates = false
    @Published private(set) var automaticallyChecksForUpdates = false

    private let controller = SPUStandardUpdaterController(
        startingUpdater: false, updaterDelegate: nil, userDriverDelegate: nil
    )
    private var observations: [NSKeyValueObservation] = []
    private var started = false

    func start() {
        guard !started else { return }
        let environment = ProcessInfo.processInfo.environment
        guard environment["ORDO_UI_TEST"] != "1" || environment["ORDINA_TEST_UPDATES"] == "1" else { return }
        started = true
        observations = [
            controller.updater.observe(\.canCheckForUpdates, options: [.initial, .new]) { [weak self] updater, _ in
                let value = updater.canCheckForUpdates
                Task { @MainActor [weak self] in self?.canCheckForUpdates = value }
            },
            controller.updater.observe(\.automaticallyChecksForUpdates, options: [.initial, .new]) { [weak self] updater, _ in
                let value = updater.automaticallyChecksForUpdates
                Task { @MainActor [weak self] in self?.automaticallyChecksForUpdates = value }
            }
        ]
        controller.startUpdater()
    }

    func checkForUpdates() {
        guard canCheckForUpdates else { return }
        controller.checkForUpdates(nil)
    }

    func setAutomaticallyChecksForUpdates(_ enabled: Bool) {
        controller.updater.automaticallyChecksForUpdates = enabled
    }
}
