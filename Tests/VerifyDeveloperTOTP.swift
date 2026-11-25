import Foundation

@main
struct VerifyDeveloperTOTP {
    static func main() throws {
        let secret = Data("12345678901234567890".utf8)
        precondition(DeveloperTOTP.code(secret: secret, counter: 1) == "287082")
        precondition(DeveloperTOTP.code(secret: secret, counter: 37037036) == "081804")
        precondition(DeveloperTOTP.match("287082", secret: secret, time: 59, lastCounter: nil) == 1)
        precondition(DeveloperTOTP.match("287082", secret: secret, time: 59, lastCounter: 1) == nil)
        precondition(DeveloperTOTP.match("287082", secret: secret, time: 120, lastCounter: nil) == nil)
        precondition(DeveloperTOTP.match("abcdef", secret: secret, time: 59, lastCounter: nil) == nil)
        print("TOTP RFC 6238 vectors and replay checks passed")
    }
}
