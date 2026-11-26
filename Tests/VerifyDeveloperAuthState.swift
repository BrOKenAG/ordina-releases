import Foundation

@main
struct VerifyDeveloperAuthState {
    static func main() throws {
        var state = DeveloperAuthState()
        precondition(!state.isUnlocked(at: 0))
        state.unlock(at: 100)
        precondition(state.isUnlocked(at: 1899))
        precondition(!state.isUnlocked(at: 1900))
        state.unlock(at: 2000)
        state.lock()
        precondition(!state.isUnlocked(at: 2001))
        for _ in 0..<5 { state.failed(at: 3000) }
        precondition(!state.canAttempt(at: 3001))
        precondition(state.canAttempt(at: 3030))
        state.succeeded()
        precondition(state.canAttempt(at: 3031))
        let first = try DeveloperAuthKeychain.generateSecret()
        let second = try DeveloperAuthKeychain.generateSecret()
        precondition(first.count == 20 && second.count == 20 && first != second)
        print("Developer authentication session, rate limiting and random generation passed")
    }
}
