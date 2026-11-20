import Foundation

struct ResolveDatabase: Codable, Hashable, Identifiable {
    let DbType: String
    let DbName: String
    let IpAddress: String?
    var id: String { "\(DbType)|\(IpAddress ?? "")|\(DbName)" }
    var label: String { "\(DbName) · \(IpAddress ?? DbType)" }
}

struct ResolveProbe: Decodable {
    let database: ResolveDatabase
    let databases: [ResolveDatabase]
}

struct ProjectNaming {
    let nasFolder: String
    let projectName: String
    let resolveFolders: [String]

    init(name: String, date: Date = Date()) throws {
        let clean = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty, !clean.contains("/"), !clean.contains(":"),
              !clean.contains("\0"), clean != ".", clean != ".." else {
            throw ResolveFailure(message: "项目名称不能包含 /、: 或空字符。")
        }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.dateFormat = "yyyyMMdd"
        let datedName = clean.range(of: #"^[0-9]{8}_"#, options: .regularExpression) != nil
            ? clean : "\(formatter.string(from: date))_\(clean)"
        let prefix = String(datedName.prefix(8))
        formatter.isLenient = false
        guard let projectDate = formatter.date(from: prefix), formatter.string(from: projectDate) == prefix else {
            throw ResolveFailure(message: "项目日期无效，请使用 yyyyMMdd_项目名。")
        }
        projectName = String(datedName.dropFirst(9))
        guard !projectName.trimmingCharacters(in: .whitespaces).isEmpty else {
            throw ResolveFailure(message: "请输入项目名称。")
        }
        nasFolder = datedName
        let month = Int(prefix.dropFirst(4).prefix(2))!
        resolveFolders = [String(prefix.prefix(4)), "Q\((month - 1) / 3 + 1)", String(datedName.dropFirst(4))]
    }
}

struct ResolveFailure: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

enum ResolveService {
    static func call(action: String, database: ResolveDatabase? = nil, naming: ProjectNaming? = nil, projectRoot: String? = nil, payload: [String: Any] = [:]) async throws -> Data {
        try await Task.detached {
            guard let script = Bundle.main.url(forResource: "resolve_bridge", withExtension: "py") else {
                throw ResolveFailure(message: "缺少 Resolve 连接组件，请重新构建应用。")
            }
            var request: [String: Any] = payload
            request["action"] = action
            if let projectRoot { request["projectRoot"] = projectRoot }
            if let database {
                request["database"] = try JSONSerialization.jsonObject(with: JSONEncoder().encode(database))
            }
            if let naming {
                request["folders"] = naming.resolveFolders
                request["projectName"] = naming.projectName
            }
            let process = Process()
            process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
            var environment = ProcessInfo.processInfo.environment
            environment["PYTHONUTF8"] = "1"
            environment["PYTHONIOENCODING"] = "utf-8"
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            process.environment = environment
            process.arguments = [script.path]
            let input = Pipe()
            let output = Pipe()
            let errors = Pipe()
            process.standardInput = input
            process.standardOutput = output
            process.standardError = errors
            try process.run()
            let deadline = DispatchWorkItem { if process.isRunning { process.terminate() } }
            if action != "archive_execute" {
                DispatchQueue.global().asyncAfter(deadline: .now() + (action == "archive_scan" ? 600 : 30), execute: deadline)
            }
            defer { deadline.cancel() }
            try input.fileHandleForWriting.write(contentsOf: JSONSerialization.data(withJSONObject: request))
            try input.fileHandleForWriting.close()
            let data = output.fileHandleForReading.readDataToEndOfFile()
            process.waitUntilExit()
            guard let line = String(data: data, encoding: .utf8)?.components(separatedBy: .newlines)
                .last(where: { $0.hasPrefix("MAGIC_RESOLVE:") }),
                  let body = String(line.dropFirst("MAGIC_RESOLVE:".count)).data(using: .utf8),
                  let response = try JSONSerialization.jsonObject(with: body) as? [String: Any] else {
                throw ResolveFailure(message: action == "archive_scan" ? "素材扫描未完成，请检查存储连接后重试。" : "Resolve 连接未完成，请检查 Python 3 与 Resolve 设置；若刚执行创建，请先检查项目库。")
            }
            guard response["ok"] as? Bool == true else {
                throw ResolveFailure(message: response["error"] as? String ?? "Resolve 操作失败。")
            }
            return try JSONSerialization.data(withJSONObject: response["data"] ?? [:])
        }.value
    }
}
