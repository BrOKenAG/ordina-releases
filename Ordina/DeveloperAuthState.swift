import Foundation

// Memory-only state; closing the process always discards the session.
struct DeveloperAuthState {
    private var expires: TimeInterval?
    private var failures = 0
    private var retryAt: TimeInterval = 0

    func isUnlocked(at time: TimeInterval) -> Bool {
        guard time.isFinite, let expires else { return false }
        return time < expires
    }

    mutating func unlock(at time: TimeInterval) {
        guard time.isFinite else { return }
        expires = time + 30 * 60
        succeeded()
    }

    mutating func lock() { expires = nil }

    func canAttempt(at time: TimeInterval) -> Bool {
        time.isFinite && time >= retryAt
    }

    mutating func failed(at time: TimeInterval) {
        guard canAttempt(at: time) else { return }
        failures += 1
        if failures >= 5 {
            retryAt = time + 30
            failures = 0
        }
    }

    mutating func succeeded() {
        failures = 0
        retryAt = 0
    }
}
