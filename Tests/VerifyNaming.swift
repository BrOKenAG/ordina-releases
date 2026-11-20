import Foundation

@main
struct VerifyNaming {
    static func main() throws {
        let dated = try ProjectNaming(name: "20261102_示例项目")
        precondition(dated.nasFolder == "20261102_示例项目")
        precondition(dated.resolveFolders == ["2026", "Q4", "1102_示例项目"])
        precondition(dated.projectName == "示例项目")
        for (month, quarter) in [("01", "Q1"), ("02", "Q1"), ("03", "Q1"), ("04", "Q2"), ("05", "Q2"), ("06", "Q2"), ("07", "Q3"), ("08", "Q3"), ("09", "Q3"), ("10", "Q4"), ("11", "Q4"), ("12", "Q4")] {
            let naming = try ProjectNaming(name: "2026\(month)01_项目2026_版本2")
            precondition(naming.resolveFolders == ["2026", quarter, "\(month)01_项目2026_版本2"])
            precondition(naming.projectName == "项目2026_版本2")
        }
        let plain = try ProjectNaming(name: "测试", date: ISO8601DateFormatter().date(from: "2026-11-02T12:00:00Z")!)
        precondition(plain.nasFolder == "20261102_测试")
        precondition(plain.resolveFolders == ["2026", "Q4", "1102_测试"])
        let nextYear = try ProjectNaming(name: "20270102_测试")
        precondition(nextYear.resolveFolders == ["2027", "Q1", "0102_测试"])
        let futureDate = ISO8601DateFormatter().date(from: "2027-01-02T12:00:00Z")!
        let futurePlain = try ProjectNaming(name: "测试", date: futureDate)
        precondition(futurePlain.nasFolder == "20270102_测试")
        precondition(futurePlain.resolveFolders == nextYear.resolveFolders)
        for invalid in ["", "../越界", "20260230_测试", "20261102_", "a:b"] {
            do {
                _ = try ProjectNaming(name: invalid)
                fatalError("应拒绝：\(invalid)")
            } catch is ResolveFailure {}
        }
        print("命名验证通过")
    }
}
