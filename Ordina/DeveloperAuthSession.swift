import Foundation
import Combine

/// One 30-minute unlocked session shared by every developer entry point.
/// Locking or expiry is global; credentials and signing are never stored here.
@MainActor
final class DeveloperAuthSession: ObservableObject {
    static let shared = DeveloperAuthSession()

    private static let lifetime: TimeInterval = 30 * 60
    private var expiresAt: Date?

    @Published private(set) var unlocked = false

    func unlock() {
        expiresAt = Date().addingTimeInterval(Self.lifetime)
        unlocked = true
    }

    func lock() {
        expiresAt = nil
        unlocked = false
    }

    func tick() {
        guard let expiresAt, Date() >= expiresAt else { return }
        lock()
    }
}
