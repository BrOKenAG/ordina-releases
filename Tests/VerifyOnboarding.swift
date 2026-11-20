import Foundation

@main
struct VerifyOnboarding {
    @MainActor
    static func main() async throws {
        let files = FileManager.default
        let home = files.temporaryDirectory.appendingPathComponent("Ordina-Onboarding-\(UUID().uuidString)")
        let suite = "Ordina.Onboarding.Tests.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        let uiSuite = "Ordina.Onboarding.UI.Tests.\(UUID().uuidString)"
        let uiDefaults = UserDefaults(suiteName: uiSuite)!
        defer {
            defaults.removePersistentDomain(forName: suite)
            uiDefaults.removePersistentDomain(forName: uiSuite)
            try? files.removeItem(at: home)
        }
        try files.createDirectory(at: home, withIntermediateDirectories: true)
        let source = home.appendingPathComponent("source.py")
        let target = home.appendingPathComponent("Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/Project Setup.py")
        try Data("bundled".utf8).write(to: source)

        for environment in [
            ["ORDO_UI_DEFAULTS_SUITE": uiSuite],
            ["ORDO_UI_TEST": "1", "ORDO_UI_DEFAULTS_SUITE": uiSuite],
            ["ORDO_ONBOARDING_TEST": "1", "ORDO_UI_DEFAULTS_SUITE": uiSuite],
            ["ORDO_UI_TEST": "1", "ORDO_ONBOARDING_TEST": "1"],
            ["ORDO_UI_TEST": "1", "ORDO_ONBOARDING_TEST": "1", "ORDO_UI_DEFAULTS_SUITE": " "]
        ] {
            precondition(OnboardingController.sharedDefaults(environment: environment) === UserDefaults.standard)
        }
        let realProgress = UserDefaults.standard.data(forKey: OnboardingProgress.key)
        let isolatedDefaults = OnboardingController.sharedDefaults(environment: [
            "ORDO_UI_TEST": "1", "ORDO_ONBOARDING_TEST": "1", "ORDO_UI_DEFAULTS_SUITE": uiSuite
        ])
        let isolatedController = OnboardingController(defaults: isolatedDefaults, home: home, source: source, environment: [:], probe: {
            fatalError("Suite isolation should not probe Resolve")
        }, install: { fatalError("Suite isolation should not install") })
        precondition(isolatedController.state == .notStarted && isolatedController.step == .projectRoot)
        isolatedController.deferSetup()
        precondition(OnboardingProgress.load(from: uiDefaults).deferred)
        precondition(UserDefaults.standard.data(forKey: OnboardingProgress.key) == realProgress)

        let missing = try IntegrationInstaller.inspect(home: home, source: source)
        precondition(missing == .missing)
        precondition(!files.fileExists(atPath: target.deletingLastPathComponent().path), "Inspection must not create directories")
        try files.createDirectory(at: target.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data("custom".utf8).write(to: target)
        let outdated = try IntegrationInstaller.inspect(home: home, source: source)
        precondition(outdated == .outdated)
        let unchanged = try Data(contentsOf: target)
        precondition(unchanged == Data("custom".utf8), "Inspection must preserve the installed script")
        let backups = home.appendingPathComponent("Library/Application Support/com.example.MagicTools.Youth/Backups")
        precondition(!files.fileExists(atPath: backups.path))
        do {
            _ = try IntegrationInstaller.inspect(home: home, source: nil)
            fatalError("Missing bundled source must fail")
        } catch is ResolveFailure {}

        var progress = OnboardingProgress.load(from: defaults)
        precondition(progress.state == .notStarted && progress.step == .projectRoot)
        progress.state = .detecting
        progress.step = .resolveLibrary
        progress.save(to: defaults)
        let interrupted = OnboardingProgress.load(from: defaults)
        precondition(interrupted.step == .resolveLibrary && interrupted.state == .needsConfiguration)

        let disk = ResolveDatabase(DbType: "Disk", DbName: "Local", IpAddress: nil)
        let postgres = ResolveDatabase(DbType: "PostgreSQL", DbName: "Studio", IpAddress: "192.0.2.1")
        var active = disk
        var probeFails = false
        var probes = 0
        var installs = 0
        let probe: () async throws -> ResolveProbe = {
            probes += 1
            if probeFails { throw ResolveFailure(message: "测试连接失败") }
            return ResolveProbe(database: active, databases: [disk, postgres])
        }
        let makeController: ([String: String]) -> OnboardingController = { environment in
            OnboardingController(defaults: defaults, home: home, source: source, environment: environment, probe: probe, install: {
                installs += 1
                throw ResolveFailure(message: "测试安装失败")
            }, loadBookmark: { preferences in
                guard let data = preferences.data(forKey: "destinationFolderBookmark") else { return nil }
                var stale = false
                return try? URL(resolvingBookmarkData: data, options: [.withoutUI, .withoutMounting], bookmarkDataIsStale: &stale)
            }, saveBookmark: { url, preferences in
                let data = try url.bookmarkData(options: [], includingResourceValuesForKeys: nil, relativeTo: nil)
                preferences.set(data, forKey: "destinationFolderBookmark")
            })
        }

        let controller = makeController([:])
        controller.resume()
        await controller.refresh()
        precondition(controller.needsPresentation && controller.step == .resolveLibrary)
        precondition(controller.databases.contains(disk) && controller.databases.contains(postgres))
        precondition(installs == 0 && defaults.object(forKey: "resolveDatabaseID") == nil)
        controller.saveDestination(home)
        let bookmark = defaults.data(forKey: "destinationFolderBookmark")!
        controller.saveDestination(home.appendingPathComponent("unavailable-volume"))
        precondition(controller.state == .failed)
        precondition(defaults.data(forKey: "destinationFolderBookmark") == bookmark, "An unavailable replacement must preserve the saved folder")
        let readOnly = home.appendingPathComponent("read-only")
        try files.createDirectory(at: readOnly, withIntermediateDirectories: true)
        try files.setAttributes([.posixPermissions: 0o555], ofItemAtPath: readOnly.path)
        controller.saveDestination(readOnly)
        precondition(controller.state == .failed, "A project root must be writable")
        precondition(defaults.data(forKey: "destinationFolderBookmark") == bookmark, "An unwritable replacement must preserve the saved folder")
        try files.setAttributes([.posixPermissions: 0o755], ofItemAtPath: readOnly.path)
        controller.saveDestination(home)
        precondition(controller.status(for: .projectRoot) == .completed)
        controller.setResolveEnabled(true)
        controller.selectDatabase(postgres.id)
        precondition(!controller.selectedDatabaseIsActive && !controller.canContinue)
        controller.deferSetup()
        precondition(!controller.needsPresentation)
        precondition(defaults.data(forKey: "destinationFolderBookmark") == bookmark)
        precondition(defaults.string(forKey: "resolveDatabaseID") == postgres.id)
        precondition(defaults.bool(forKey: "createResolveProject"))

        let deferred = makeController([:])
        let beforeDeferredResume = probes
        deferred.resume()
        precondition(!deferred.needsPresentation && probes == beforeDeferredResume)
        deferred.reopen()
        await deferred.refresh()
        precondition(deferred.needsPresentation && deferred.step == .resolveLibrary)
        precondition(!deferred.selectedDatabaseIsActive)
        active = postgres
        await deferred.refresh()
        precondition(deferred.selectedDatabaseIsActive && deferred.canContinue)
        precondition(deferred.status(for: .resolveLibrary) == .completed)
        probeFails = true
        await deferred.refresh()
        precondition(!deferred.selectedDatabaseIsActive && !deferred.canContinue)
        precondition(defaults.string(forKey: "resolveDatabaseID") == postgres.id)
        probeFails = false
        await deferred.refresh()
        precondition(deferred.selectedDatabaseIsActive && deferred.canContinue)
        deferred.advance()
        precondition(deferred.step == .projectSetup)
        precondition(OnboardingProgress.load(from: defaults).step == .projectSetup)
        deferred.installProjectSetup()
        precondition(deferred.state == .failed && installs == 1)
        precondition(defaults.data(forKey: "destinationFolderBookmark") == bookmark)
        try Data("bundled".utf8).write(to: target)
        await deferred.refresh()
        precondition(deferred.integrationStatus == .current && deferred.canContinue)
        precondition(deferred.status(for: .projectSetup) == .completed)
        deferred.advance()
        precondition(deferred.state == .completed && !deferred.needsPresentation)

        let completed = makeController([:])
        let beforeCompletedResume = probes
        completed.resume()
        precondition(completed.state == .completed && !completed.needsPresentation)
        precondition(probes == beforeCompletedResume && installs == 1)
        completed.reopen()
        await completed.refresh()
        precondition(completed.needsPresentation && completed.state != .completed)
        completed.deferSetup()

        defaults.removeObject(forKey: OnboardingProgress.key)
        let existing = makeController([:])
        existing.resume()
        await existing.refresh()
        precondition(existing.state == .completed && !existing.needsPresentation, "Existing valid settings should not force the wizard")
        precondition(installs == 1)

        defaults.removeObject(forKey: OnboardingProgress.key)
        let smoke = makeController(["ORDO_UI_TEST": "1"])
        let beforeSmoke = probes
        smoke.resume()
        await Task.yield()
        precondition(!smoke.needsPresentation && probes == beforeSmoke)
        let onboardingSmoke = makeController(["ORDO_UI_TEST": "1", "ORDO_ONBOARDING_TEST": "1"])
        onboardingSmoke.resume()
        precondition(onboardingSmoke.needsPresentation)
        await onboardingSmoke.refresh()

        defaults.removeObject(forKey: OnboardingProgress.key)
        defaults.set(disk.id, forKey: "resolveDatabaseID")
        let mismatch = makeController([:])
        mismatch.resume()
        await mismatch.refresh()
        precondition(mismatch.needsPresentation && mismatch.step == .resolveLibrary)
        precondition(!mismatch.selectedDatabaseIsActive)
        precondition(defaults.string(forKey: "resolveDatabaseID") == disk.id, "Detection must not replace a saved library")
        mismatch.setResolveEnabled(false)
        precondition(mismatch.canContinue)
        mismatch.advance()
        mismatch.advance()
        precondition(mismatch.state == .completed)
        precondition(defaults.string(forKey: "resolveDatabaseID") == disk.id)

        let allowedKeys: Set<String> = [OnboardingProgress.key, "destinationFolderBookmark", "resolveDatabaseID", "createResolveProject"]
        precondition(Set(defaults.persistentDomain(forName: suite)!.keys).isSubset(of: allowedKeys), "Only wizard progress and existing configuration keys may be persisted")

        defaults.removeObject(forKey: OnboardingProgress.key)
        defaults.set(false, forKey: "createResolveProject")
        probeFails = true
        let configuredWithoutResolve = makeController([:])
        let beforeOptionalProbe = probes
        configuredWithoutResolve.resume()
        await configuredWithoutResolve.refresh()
        precondition(!configuredWithoutResolve.resolveEnabled)
        precondition(configuredWithoutResolve.state == .completed && !configuredWithoutResolve.needsPresentation, "Existing users with an explicit Resolve opt-out must not reconfigure an optional library")
        precondition(probes == beforeOptionalProbe, "An existing folder and installation need no Resolve connection when project creation is off")
        probeFails = false

        defaults.removeObject(forKey: OnboardingProgress.key)
        defaults.removeObject(forKey: "createResolveProject")
        defaults.removeObject(forKey: "resolveDatabaseID")
        let unconfiguredLibrary = makeController([:])
        precondition(unconfiguredLibrary.resolveEnabled, "An unset choice should offer Resolve setup without saving a preference")
        precondition(defaults.object(forKey: "createResolveProject") == nil)
        unconfiguredLibrary.resume()
        await unconfiguredLibrary.refresh()
        precondition(unconfiguredLibrary.needsPresentation && unconfiguredLibrary.step == .resolveLibrary)
        precondition(unconfiguredLibrary.resolveEnabled && !unconfiguredLibrary.canContinue)
        precondition(unconfiguredLibrary.status(for: .resolveLibrary) != .completed)
        unconfiguredLibrary.advance()
        precondition(unconfiguredLibrary.step == .resolveLibrary && unconfiguredLibrary.state != .completed)
        precondition(defaults.object(forKey: "createResolveProject") == nil)
        precondition(defaults.object(forKey: "resolveDatabaseID") == nil)
        unconfiguredLibrary.deferSetup()

        defaults.removeObject(forKey: OnboardingProgress.key)
        defaults.removeObject(forKey: "destinationFolderBookmark")
        let optionalResolve = makeController([:])
        precondition(optionalResolve.resolveEnabled)
        optionalResolve.resume()
        await optionalResolve.refresh()
        precondition(optionalResolve.step == .projectRoot)
        optionalResolve.saveDestination(home)
        optionalResolve.advance()
        precondition(optionalResolve.step == .resolveLibrary && !optionalResolve.canContinue)
        precondition(defaults.object(forKey: "createResolveProject") == nil, "Detection must not commit the default choice")
        optionalResolve.advance()
        precondition(optionalResolve.step == .resolveLibrary && defaults.object(forKey: "createResolveProject") == nil)
        optionalResolve.setResolveEnabled(false)
        precondition(optionalResolve.canContinue && optionalResolve.status(for: .resolveLibrary) == .completed)
        precondition(defaults.object(forKey: "createResolveProject") != nil && !defaults.bool(forKey: "createResolveProject"))
        optionalResolve.advance()
        optionalResolve.advance()
        precondition(optionalResolve.state == .completed)

        defaults.removeObject(forKey: OnboardingProgress.key)
        var waitingProgress = OnboardingProgress()
        waitingProgress.state = .needsConfiguration
        waitingProgress.save(to: defaults)
        defaults.set(true, forKey: "createResolveProject")
        defaults.set(disk.id, forKey: "resolveDatabaseID")
        var pendingProbe: CheckedContinuation<ResolveProbe, any Error>?
        var pendingProbeCalls = 0
        let pending = OnboardingController(defaults: defaults, home: home, source: source, environment: [:], probe: {
            pendingProbeCalls += 1
            return try await withCheckedThrowingContinuation { pendingProbe = $0 }
        }, install: { fatalError("Detection must never install") }, loadBookmark: { _ in home })
        pending.resume()
        pending.resume()
        while pendingProbe == nil { await Task.yield() }
        precondition(pendingProbeCalls == 1, "Duplicate launch resume calls must coalesce")
        precondition(pending.canContinue, "A ready folder must remain interactive while Resolve is being probed")
        precondition(pending.status(for: .projectRoot) == .completed)
        precondition(pending.status(for: .resolveLibrary) == .detecting)
        let interruptedLaunch = OnboardingProgress.load(from: defaults)
        precondition(!interruptedLaunch.deferred && interruptedLaunch.state == .needsConfiguration, "An interrupted launch must remain resumable without explicit deferral")
        pending.deferSetup()
        pendingProbe?.resume(returning: ResolveProbe(database: disk, databases: [disk]))
        await Task.yield()
        precondition(!pending.needsPresentation)
        let persistedDeferral = OnboardingProgress.load(from: UserDefaults(suiteName: suite)!)
        precondition(persistedDeferral.deferred && persistedDeferral.state == .needsConfiguration)
        precondition(defaults.data(forKey: "destinationFolderBookmark") == bookmark)

        defaults.set(Data("invalid".utf8), forKey: OnboardingProgress.key)
        let recovered = OnboardingProgress.load(from: defaults)
        precondition(recovered.state == .notStarted)
        precondition(defaults.string(forKey: "resolveDatabaseID") == disk.id)

        print("Onboarding persistence, resume, deferral, existing settings, active-library status, UI-test gating and read-only inspection passed")
    }
}
