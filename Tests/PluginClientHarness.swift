import Foundation

// A standalone client snapshot used by scripts/verify_plugin_protocol.py.
// It links the same protocol, catalog and installer sources as the app, is
// compiled once, and then talks to whatever registry the environment names.
// No plugin id is compiled in: `list` reports exactly what the registry says.
@main
struct PluginClientHarness {
    struct Failure: Error, CustomStringConvertible {
        let description: String
    }

    static func main() {
        let arguments = Array(CommandLine.arguments.dropFirst())
        guard let command = arguments.first else {
            emit(["ok": false, "error": "usage: harness list|status|install|uninstall [id]"])
            exit(2)
        }
        let environment = PluginEnvironment.current
        do {
            switch command {
            case "list":
                let result = PluginCatalog.load(environment: environment)
                emit([
                    "ok": true,
                    "registryError": result.registryError as Any? ?? NSNull(),
                    "items": result.items.map(describe),
                ])
            case "status":
                let result = PluginCatalog.load(environment: environment)
                guard let id = arguments.dropFirst().first,
                      let item = result.items.first(where: { $0.id == id }) else {
                    throw Failure(description: "unknown plugin: \(arguments.dropFirst().first ?? "")")
                }
                emit(["ok": true, "item": describe(item)])
            case "install", "update":
                let id = try pluginID(arguments)
                let item = try loadItem(id, environment: environment)
                try PluginOperations.install(item, environment: environment)
                emit(["ok": true, "installed": id, "version": item.version])
            case "uninstall":
                let id = try pluginID(arguments)
                let result = PluginCatalog.load(environment: environment)
                guard let item = result.items.first(where: { $0.id == id }) else {
                    throw Failure(description: "unknown plugin: \(id)")
                }
                try PluginOperations.uninstall(item, environment: environment)
                emit(["ok": true, "uninstalled": id])
            default:
                throw Failure(description: "unknown command: \(command)")
            }
        } catch {
            emit(["ok": false, "error": error.localizedDescription])
            exit(1)
        }
    }

    private static func pluginID(_ arguments: [String]) throws -> String {
        guard arguments.count >= 2, !arguments[1].isEmpty else {
            throw Failure(description: "missing plugin id")
        }
        return arguments[1]
    }

    private static func loadItem(_ id: String,
                                 environment: PluginEnvironment) throws -> PluginCatalog.Item {
        let result = PluginCatalog.load(environment: environment)
        guard let item = result.items.first(where: { $0.id == id }) else {
            throw Failure(description: "plugin not in registry: \(id)")
        }
        if let error = item.error { throw Failure(description: error) }
        guard item.release != nil else { throw Failure(description: "plugin has no release") }
        return item
    }

    private static func describe(_ item: PluginCatalog.Item) -> [String: Any] {
        var status = "unknown"
        switch item.status {
        case .notInstalled: status = "notInstalled"
        case .current(let version): status = "current:\(version)"
        case .updateAvailable(let installed, let latest): status = "update:\(installed)->\(latest)"
        case .unmanaged(let version): status = "unmanaged:\(version ?? "?")"
        case .incompatible(let required): status = "incompatible:\(required)"
        }
        return [
            "id": item.id,
            "name": item.name,
            "summary": item.summary,
            "symbol": item.symbol,
            "version": item.version,
            "minClientVersion": item.minClientVersion,
            "capabilities": item.capabilities.map(\.rawValue),
            "installed": item.isInstalled,
            "canInstall": item.canInstall,
            "withdrawn": item.isWithdrawn,
            "status": status,
            "error": item.error as Any? ?? NSNull(),
        ]
    }

    private static func emit(_ object: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: object,
                                                     options: [.sortedKeys]),
              let text = String(data: data, encoding: .utf8) else {
            print("{\"ok\":false,\"error\":\"encoding\"}")
            return
        }
        print(text)
    }
}
