# ordina 插件协议维护文档

状态：基于本仓库 `44a3cd5` 的协议 2 实现记录；正式发布、生产签名与真实公证验收另行确认。此文描述当前实现，不把目标约束当成已实现 Schema。协议变更须同步 Swift、Python、发布工具和兼容测试。

## 1. 权威来源与数据层次

- `Ordina/PluginProtocol.swift`、`PluginInstaller.swift`、`PluginCatalog.swift`、`PluginEnvironment.swift` 和 `PluginUpdates.swift`：客户端验证、安装及目录行为。
- `scripts/plugin_protocol.py`、`dev_zip.py`、`publish_plugin.py`、`build_feeds.py`、`feed_signing.py`：ZIP、发布描述、生成及签名。`Tests/VerifyPluginProtocol.swift`、`VerifyPluginInstaller.swift`、`VerifyNativePlugin.swift` 与对应 Python 测试提供验证。
- 开发者上传 ZIP：根目录 `plugin.json` 描述源文件。工具生成 JSON 资产容器及清单；开发者不在 ZIP 中填写资产摘要或绝对安装路径。
- 远端 Registry 的签名载荷 `format: 2`；每插件发布清单的签名载荷 `format: 1`；发布资产 JSON 容器 `format: 1`。容器为 `{ "format": 1, "id": "…", "version": "…", "files": { "相对路径": "Base64文件字节" } }`。`protocol: 2` 是描述符协议号，与这些外层 `format` 不同。

## 2. ZIP 内 `plugin.json`

| 字段 | 类型及当前规则 |
|---|---|
| `id` | 必填字符串，`^[a-z][a-z0-9-]{0,31}$`；与发布仓库 ID 一致。 |
| `name` / `summary` | 必填非空字符串，分别最多 64 / 160 字符。 |
| `symbol` | 必填，`^[a-z][a-z0-9.]{0,63}$`。 |
| `version` / `minClientVersion` | 必填字符串，2–4 个由点分隔的数字段；发布新版本需递增。原生能力最低为 `1.10.0`。不是完整 SemVer，不支持 `-rc` 或 `+build`。 |
| `protocol` | 可省略，Python 生成描述符默认 `2`；显式指定时只能为数字 `2`。 |
| `capabilities` | 必填非空、不重复数组，值见下表。 |
| `files` | 可选对象；键是包内相对路径，值为 `{ "role": "…", "installName": "…", "executable": true }`。除根 `plugin.json` 自动作为 `metadata`、`extension/` 自动作为 `extension` 外，所有文件必须声明角色。 |
| `supportDirectoryName` | 可选单级目录名；不填使用插件 ID。规则 `^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$`。 |
| `nativeMessagingHost` | 可选包内相对路径，发布端限制最多两级；不填时客户端取首个 `nativeMessagingHost` 角色文件。 |
| `legacyNames` | 可选数组，最多 8 个旧 `.py` 文件名；不接受目录分隔符、冒号或空字符。 |

ZIP 上传路径在检查时一律生成 `kind: "bundle"`；`kind` 不必写入 `plugin.json`。`utilityScript` 描述字段由文件清单中的脚本 `installName` 或文件名生成。最低 macOS、处理器架构、依赖列表、更新说明等目前没有已实现的插件协议字段；需在交付说明中说明，不能假称客户端会校验。

| 能力 | 当前作用 |
|---|---|
| `resolve.utilityScript` | Resolve Utility 脚本。 |
| `resolve.support` | 支持文件。 |
| `chrome.nativeMessagingHost` | 生成 Chrome Native Messaging 配置并指向安装后的 Host。 |
| `chrome.unpackedExtension` | 浏览器扩展文件；首次需用户手动在 Chrome 加载一次。 |
| `macos.application` | 原生 App；描述符和 `application` 文件角色必须同时出现，最低客户端 `1.10.0`。 |

文件角色为 `metadata`、`utilityScript`、`support`、`nativeMessagingHost`、`nativeMessagingManifest`、`extension`、`application`。bundle 必须有根 `plugin.json`（`metadata`）及恰好一个 `utilityScript`；script 发布资产必须只有一个 `utilityScript`。`application` 文件必须全部处于同一个顶层 `Example.app/Contents/` 下，App 目录中的文件不得声明其他角色。`executable` 为布尔值；需要执行的 App 可执行文件及 Host 应显式设为 `true`。`installName` 仅对 `utilityScript` 和 `nativeMessagingManifest` 在打包时写入清单，必须是安全的单级文件名；根元数据不安装。

## 3. 签名发布清单与字段

清单载荷形如 `{ "format": 1, "plugins": { "插件ID": 发布条目 } }`。发布条目的必填字段是 `version`、`url`、`sha256`、`size`、`descriptor`、`files`；`kind` 为可选字段，bundle 生成器写 `"bundle"`。`descriptor` 包含 `id`、`name`、`summary`、`symbol`、`version`、`minClientVersion`、`kind`、`protocol`、`capabilities`，并可包含上节列出的 `supportDirectoryName`、`nativeMessagingHost`、`legacyNames` 和生成的 `utilityScript`。条目版本必须与描述符版本相等。

签名清单 `files` 是数组；每项含 `path`、64 位小写十六进制 `sha256`、正整数 `size`、`role`，可选 `installName` 和布尔 `executable`。摘要基于文件原始字节；发布条目的摘要与大小针对整个资产容器。客户端逐项验证摘要、大小、路径和清单与容器的一致性。生产 URL 必须是 `https://github.com/BrOKenAG/ordina-plugin-<id>/releases/download/...` 下的资产，bundle 为 `.json`，script 为 `.py`；不接受查询、片段、凭据、路径穿越或其他主机。服务端生成的版本标签为 `plugin-<id>-v<version>`。

签名外层为 `{ "payload": "Base64(UTF-8 JSON)", "signature": "Base64(Ed25519签名)" }`；`feed_signing.canonical` 使用 UTF-8、非 ASCII 原样、键排序及紧凑 JSON，签名覆盖载荷原始字节。客户端固定公钥为 `90vua+GDSP8MSl6owCI/WSIkqBBuXO/AGw0ny4p67Bw=`（Base64，公钥）。正式签名由工具使用 Keychain 的 `ordina-sparkle` 对应能力生成并验证；密钥不进入仓库。隔离测试使用显式 lab 密钥和后端。固定公钥轮换的完整迁移协议目前未在此实现，不能假设可无缝轮换。

## 4. 安装位置与生命周期

以下路径相对于当前用户 HOME；隔离测试由注入的独立 HOME 决定：

| 文件角色 | 安装位置 |
|---|---|
| `utilityScript` | `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/<installName或文件名>` |
| `support`、`nativeMessagingHost`、`extension`、`application` | `~/Library/Application Support/<supportDirectoryName或id>/<包内相对路径>`；App 因此位于该目录下的 `<名称>.app`。 |
| `nativeMessagingManifest` | `~/Library/Application Support/Google/Chrome/NativeMessagingHosts/<installName或文件名>`；客户端根据模板中的 `name`、`description`、`allowed_origins` 生成 `type: stdio` 和指向已安装 Host 的 `path`。 |
| 安装记录、暂存与备份 | `~/Library/Application Support/ordina/Plugins/<id>/state.json`、其下暂存和 `backups/`；锁位于 Plugins 目录。 |

模板 `name` 需为非空字符串，`allowed_origins` 为非空字符串数组，每项以 `chrome-extension://` 开始并以 `/` 结尾；安装器重建配置，不采用 ZIP 自带绝对 Host 路径。路径必须相对、安全、UTF-8 不超过 200 字节；一般最多 4 层，顶层 `.app/Contents/` 最多 12 层；组件仅允许 ASCII 字母、数字、空格、下划线、点和短横线，不能以点开头。ZIP 中符号链接、特殊文件、加密及大小写折叠后的重复路径会被拒绝；不支持包含符号链接的原生包。

安装使用锁、暂存、逐文件备份、替换和 `state.json` 记录；更新清理旧记录中未修改且已不再需要的文件，用户修改的旧文件保留。卸载将受管文件暂移，失败时尝试恢复；已修改的受管文件不删除，回滚失败时保留暂存备份并报告路径。该过程有进程内错误回滚，但**不是断电后可自动恢复的持久化安装事务**，也没有通用跨插件目标冲突保护；不要将开发目标写成现成保证。

原生 App 先在隔离暂存区重建并执行 `codesign --verify --deep --strict`，生产版再验证 Developer ID 证书要求和 `spctl` 的 `source=Notarized Developer ID`。Debug/lab 显式隔离分支允许 ad-hoc 测试签名，Release 无该例外。Python 发布端也重建并检查 App。正式 Developer ID 签名和公证产物的真实端到端测试仍未执行。Python 运行时和依赖须由发布者随包提供并由插件自行定位；客户端不安装 Python，也不执行 ZIP 中的安装命令。

## 5. 容量与 ZIP 安全限制

以下为十进制字节上限，取各层限制的交集：

| 对象 | 当前限制 |
|---|---|
| 上传 ZIP | 32,000,000 字节；条目（含目录）最多 1,024；解压总量最多 64,000,000 字节；单条目最多 16,000,000 字节；单条压缩比不超过 200。 |
| 非原生 ZIP | 解压总量超过 8,000,000 或任一文件超过 1,000,000 字节时拒绝。 |
| 发布资产 JSON 容器 | 原生 bundle 最多 96,000,000 字节；传统 bundle 最多 4,000,000 字节。 |
| 清单文件数及单文件 | 1–512 项；`application` 文件最多 16,000,000，其他角色最多 1,000,000 字节，均须大于零。 |
| Registry / 每插件签名清单 | 各最多 131,072 字节；Base64 解码后的签名载荷同样受该上限约束。 |
| 单脚本下载 | 下载上限 2,000,000 字节；清单中普通文件仍受 1,000,000 字节约束。 |

ZIP 忽略 `.DS_Store`、`__MACOSX` 和 `__pycache__`；移除 App 签名所需文件会导致签名验证失败。禁止穿越、绝对路径、反斜线、控制字符、重复路径、链接、加密条目及异常压缩比。

## 6. 已验证的示例

以下最小 `plugin.json` 已使用实际 `build_bundle` 校验；ZIP 同时需包含 `demo.py`。这里的文件内容为 `PLUGIN_VERSION = "1.0.0"` 加换行。

```json
{
  "id": "demo",
  "name": "Demo",
  "summary": "示例",
  "symbol": "note.text",
  "version": "1.0.0",
  "minClientVersion": "1.9.6",
  "protocol": 2,
  "capabilities": [
    "resolve.utilityScript"
  ],
  "files": {
    "demo.py": {
      "role": "utilityScript",
      "installName": "demo.py"
    }
  }
}
```

以下是同一最小包经工具生成并经 `validate_manifest_entry` 校验的 **发布清单载荷示例**。它不是签名外层、没有发布资产，不能直接作为生产清单；示例摘要对应上面的实际样例文件与 JSON 容器。

```json
{
  "format": 1,
  "plugins": {
    "demo": {
      "version": "1.0.0",
      "url": "https://github.com/BrOKenAG/ordina-plugin-demo/releases/download/plugin-demo-v1.0.0/demo.json",
      "sha256": "41dfbd12853f03b390fe41b8f0ac82168e2ac4cd7f6f15a83be3c751892e9cf5",
      "size": 534,
      "kind": "bundle",
      "descriptor": {
        "id": "demo",
        "name": "Demo",
        "summary": "示例",
        "symbol": "note.text",
        "version": "1.0.0",
        "minClientVersion": "1.9.6",
        "kind": "bundle",
        "protocol": 2,
        "capabilities": [
          "resolve.utilityScript"
        ],
        "utilityScript": "demo.py"
      },
      "files": [
        {
          "path": "demo.py",
          "sha256": "77e03b5f78f315128efa675c54aa85f01fe58e7e78f3bf5e381d4272580a35cf",
          "size": 25,
          "role": "utilityScript",
          "installName": "demo.py"
        },
        {
          "path": "plugin.json",
          "sha256": "c38346403e903c062ec475c08588d7a714e96c1ea8decdbb04cb42415978edaf",
          "size": 310,
          "role": "metadata"
        }
      ]
    }
  }
}
```

以下是隔离验收包实际使用并经 `build_bundle` 及 ad-hoc 测试签名验证的复合 `plugin.json`。ZIP 必须包含其列出的全部真实文件；App 和引擎仅为测试占位，不代表正式签名、公证或可用的 Python 运行时。

```json
{
  "id": "native-composite-lab",
  "name": "Native Composite Lab",
  "version": "1.0.0",
  "summary": "隔离复合插件样例",
  "symbol": "note.text",
  "minClientVersion": "1.10.0",
  "protocol": 2,
  "capabilities": [
    "resolve.utilityScript",
    "resolve.support",
    "chrome.unpackedExtension",
    "macos.application",
    "chrome.nativeMessagingHost"
  ],
  "supportDirectoryName": "Native Composite Lab",
  "files": {
    "native-composite-lab.py": {
      "role": "utilityScript",
      "installName": "native-composite-lab.py"
    },
    "support/notes.txt": {
      "role": "support"
    },
    "extension/manifest.json": {
      "role": "extension"
    },
    "engine.py": {
      "role": "support"
    },
    "host.command": {
      "role": "nativeMessagingHost",
      "executable": true
    },
    "chrome.json": {
      "role": "nativeMessagingManifest",
      "installName": "test.ordina.composite.json"
    },
    "Example.app/Contents/Info.plist": {
      "role": "application",
      "executable": false
    },
    "Example.app/Contents/_CodeSignature/CodeResources": {
      "role": "application",
      "executable": false
    },
    "Example.app/Contents/MacOS/Example": {
      "role": "application",
      "executable": true
    }
  },
  "nativeMessagingHost": "host.command"
}
```

## 7. Registry、上下架与兼容

Registry 载荷 `format: 2`，`plugins` 仅含上架插件，`withdrawn` 可记录已下架 ID。下架资产及历史不被本协议撤回；客户端保留已安装插件的使用和卸载。旧客户端内置副本、离线缓存和历史链接不能保证即时撤销。原生能力不会出现在兼容 Registry 的 `capabilities` 中，以免旧客户端因未知枚举拒绝整个目录；完整能力保留在签名的单插件清单。旧客户端可能在该插件条目显示清单无法读取，不能安装该原生插件。Chrome 扩展首次需手动加载一次，后续更新沿用目录。

## 8. 发布与信任边界

软件、插件、开发者发布工具独立更新。生产写入由 Python 发布引擎和后端独立检查官方仓库授权及可信签名；本地 Authenticator、隐藏入口和客户端标记不能替代这些检查。已发布同版本资产不可覆盖。当前源码的发布工具 `1.4.0` 是未正式签名的候选版本，不能声称已经发布或完成生产验证。
