import Foundation

// All mutable roots and trust anchors in one injectable value. Production uses
// the fixed public key and Pages registry; the lab mode is only reachable when
// the process is started with ORDINA_PLUGIN_LAB=1 (the standalone harness), and
// it still enforces every path-shape rule.
struct PluginEnvironment: Sendable {
    let registryURL: URL
    let publicKey: Data
    let policy: PluginPolicy
    let clientVersion: String
    let home: URL
    let supportRoot: URL
    let cacheFile: URL
    let disableProxy: Bool

    static let productionRegistry = URL(string: "https://brokenag.github.io/ordina-releases/registry.json")!
    static let productionPublicKey = Data(base64Encoded: "90vua+GDSP8MSl6owCI/WSIkqBBuXO/AGw0ny4p67Bw=")!

    static var current: PluginEnvironment {
        let environment = ProcessInfo.processInfo.environment
        let home = URL(fileURLWithPath: environment["HOME"]
                       ?? FileManager.default.homeDirectoryForCurrentUser.path)
        let version = environment["ORDINA_CLIENT_VERSION"]
            ?? (Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String)
            ?? "0.0.0"
        if environment["ORDINA_PLUGIN_LAB"] == "1",
           let raw = environment["ORDINA_PLUGIN_REGISTRY"],
           let url = URL(string: raw), let host = url.host {
            let key = environment["ORDINA_PLUGIN_PUBLIC_KEY"].flatMap { Data(base64Encoded: $0) }
                ?? productionPublicKey
            return lab(registryURL: url, host: host, publicKey: key,
                       home: home, clientVersion: version)
        }
        return production(home: home, clientVersion: version)
    }

    static func production(home: URL, clientVersion: String) -> PluginEnvironment {
        build(registryURL: productionRegistry, publicKey: productionPublicKey,
              policy: .production, home: home, clientVersion: clientVersion,
              disableProxy: false)
    }

    static func lab(registryURL: URL, host: String, publicKey: Data,
                    home: URL, clientVersion: String) -> PluginEnvironment {
        build(registryURL: registryURL, publicKey: publicKey, policy: .lab(host: host),
              home: home, clientVersion: clientVersion, disableProxy: true)
    }

    private static func build(registryURL: URL, publicKey: Data, policy: PluginPolicy,
                              home: URL, clientVersion: String,
                              disableProxy: Bool) -> PluginEnvironment {
        let support = home.appendingPathComponent("Library/Application Support/ordina/Plugins",
                                                  isDirectory: true)
        return PluginEnvironment(
            registryURL: registryURL, publicKey: publicKey, policy: policy,
            clientVersion: clientVersion, home: home, supportRoot: support,
            cacheFile: support.appendingPathComponent("registry-cache.json"),
            disableProxy: disableProxy)
    }

    var utilityDirectory: URL {
        home.appendingPathComponent("Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility",
                                    isDirectory: true)
    }

    var chromeDirectory: URL {
        home.appendingPathComponent("Library/Application Support/Google/Chrome/NativeMessagingHosts",
                                    isDirectory: true)
    }

    func stateDirectory(_ id: String) -> URL {
        supportRoot.appendingPathComponent(id, isDirectory: true)
    }

    func stateFile(_ id: String) -> URL {
        stateDirectory(id).appendingPathComponent("state.json")
    }
}
