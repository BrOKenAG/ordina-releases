# 插件分发架构

> **阶段 B 已完成（本地检查点，未发布）。** 客户端不再枚举插件 id，也不再为某个插件写专用展示或安装分支。插件由**签名注册表 + 版本化插件描述协议**驱动：只要发布一个符合协议的插件并更新 registry，同一份已编译客户端就能发现、展示、安装、更新和卸载它。上线顺序见文末「正式上线计划」。

## 仓库拓扑

| 仓库 | 内容 | Pages |
| --- | --- | --- |
| `BrOKenAG/ordina-releases` | 软件本体、`appcast.xml`、`registry.json`、兼容清单 `plugins/manifest.json`、唯一源 `plugins/registry.source.json` | `main` 根目录 |
| `BrOKenAG/ordina-plugin-minutes` | Minutes Roughcut 的 Release 资产 + `manifest-v2.json` + `manifest.json` | `main` 根目录 |
| `BrOKenAG/ordina-plugin-multicam` | Multicam Tools 的 Release 资产 + 两份清单 | `main` 根目录 |

软件版与插件版、插件与插件之间完全隔离：某个插件发新版不会改动其他插件的清单，也不会产生新的软件发行版。

## 签名信任链

客户端只内置一个引导地址和一把公钥（`SUPublicEDKey`）：

```
内置公钥 (Ed25519)
  └─ registry.json              签名信封：payload(base64) + signature
       ├─ plugins[id].feed              → 该插件仓库的 manifest-v2.json
       ├─ plugins[id].downloadPrefix    → 该插件资产允许的路径前缀
       └─ plugins[id] 展示字段           → name / summary / symbol / version / capabilities
            └─ manifest-v2.json         签名信封（同一把密钥）
                 ├─ descriptor          名称、图标、最低客户端版本、能力集合、安装参数
                 ├─ files[]             逐文件 path / size / SHA-256 / role / installName
                 └─ url                 → Release 资产（整体 SHA-256 + size）
```

- 注册表和每份插件清单都用钥匙串账号 `ordina-sparkle` 的同一把密钥签名，格式一致：`{"payload": "...", "signature": "..."}`。
- 客户端先验签 registry，再验签插件清单；任何一层签名无效都拒绝，**不会降级到缓存或内置地址**。只有网络故障才允许复用已经验签过的缓存（磁盘缓存有效期 7 天）。
- 资产地址必须是 registry 为该插件下发的 `downloadPrefix` 内的直链；生产环境限定 `https://github.com/BrOKenAG/ordina-plugin-<id>/releases/download/`。
- 下载内容只含插件文件。**客户端不再执行下载包里的安装脚本**；安装行为由编译进客户端的四个能力实现。

## 插件描述协议 v2

### registry 条目

`registry.json` 仍是 `format: 2`，每个插件在原有四个字段之外增加展示与兼容信息：

```json
{
  "repository": "BrOKenAG/ordina-plugin-minutes",
  "feed": "https://brokenag.github.io/ordina-plugin-minutes/manifest-v2.json",
  "downloadPrefix": "/BrOKenAG/ordina-plugin-minutes/releases/download/",
  "kind": "bundle",
  "name": "Minutes Roughcut",
  "summary": "将飞书妙记划稿发送至 Resolve",
  "symbol": "text.badge.plus",
  "version": "0.3.0",
  "minClientVersion": "1.9.6",
  "protocol": 2,
  "capabilities": ["resolve.utilityScript", "resolve.support",
                   "chrome.nativeMessagingHost", "chrome.unpackedExtension"]
}
```

旧客户端只解码前四个字段，忽略新增字段，因此**先发布 registry 不会破坏 1.9.6**。

### manifest-v2 条目

每份插件清单 `format` 仍为 `1`，保留旧客户端需要的 `version` / `url` / `sha256` / `kind`，并新增：

```json
{
  "version": "0.3.0",
  "url": "https://github.com/BrOKenAG/ordina-plugin-minutes/releases/download/plugin-minutes-v0.3.0/minutes.json",
  "sha256": "<资产整体 SHA-256>",
  "size": 61956,
  "kind": "bundle",
  "descriptor": {
    "id": "minutes",
    "name": "Minutes Roughcut",
    "summary": "将飞书妙记划稿发送至 Resolve",
    "symbol": "text.badge.plus",
    "version": "0.3.0",
    "minClientVersion": "1.9.6",
    "kind": "bundle",
    "protocol": 2,
    "capabilities": ["resolve.utilityScript", "resolve.support",
                     "chrome.nativeMessagingHost", "chrome.unpackedExtension"],
    "supportDirectoryName": "Minutes Roughcut",
    "nativeMessagingHost": "host.command",
    "utilityScript": "Minutes Roughcut.py"
  },
  "files": [
    {"path": "Minutes Roughcut.py", "sha256": "…", "size": 25926,
     "role": "utilityScript", "installName": "Minutes Roughcut.py"},
    {"path": "host.py", "sha256": "…", "size": 3143, "role": "support"},
    {"path": "host.command", "sha256": "…", "size": 164,
     "role": "nativeMessagingHost", "executable": true},
    {"path": "extension/manifest.json", "sha256": "…", "size": 1326, "role": "extension"},
    {"path": "chrome/com.minutesroughcut.bridge.json", "sha256": "…", "size": 186,
     "role": "nativeMessagingManifest", "installName": "com.minutesroughcut.bridge.json"}
  ]
}
```

`files` 是客户端唯一认可的文件清单。客户端逐项核对 `size` 与 `SHA-256`，不再包含任何按插件硬编码的必需文件列表。

### 能力白名单

| 能力 | 客户端行为 |
| --- | --- |
| `resolve.utilityScript` | 把 `role: utilityScript` 的文件写入 Resolve `Fusion/Scripts/Utility/<installName>` |
| `resolve.support` | 把 `role: support` 的文件写入 `~/Library/Application Support/<supportDirectoryName>/<path>` |
| `chrome.nativeMessagingHost` | 把 `role: nativeMessagingHost` 的文件写入同一支持目录并设置 `0700`；`role: nativeMessagingManifest` 的模板只取 `name` / `allowed_origins`，`path` 由客户端强制改写为宿主绝对路径，写入 Chrome `NativeMessagingHosts/` |
| `chrome.unpackedExtension` | 把 `role: extension` 的文件写入支持目录的 `extension/`，由用户在 Chrome 中手动加载（Chrome 禁止静默安装） |

协议**不能**携带命令、shell 片段、绝对安装路径或任意目标目录。`supportDirectoryName` 只能是单段安全名称；`installName`、`legacyNames` 只能是单段文件名；文件路径最多四段且禁止 `..`、绝对路径、隐藏文件与反斜杠。新增能力必须修改并重新发布客户端，这是有意的安全边界。

## 唯一数据源

`plugins/registry.source.json` 仍是**唯一由人维护**的文件。每个插件条目在原有 `repository` / `feed` / `downloadPrefix` / `kind` / `asset` / `source` / `version` / `sha256` / `size` 之外，增加：

| 字段 | 用途 |
| --- | --- |
| `name` / `summary` / `symbol` | 插件中心的展示信息（`symbol` 为 SF Symbol 名） |
| `minClientVersion` | 最低客户端版本；低于该版本的客户端只展示、不允许安装 |
| `protocol` | 必须为 `2` |
| `capabilities` | 能力白名单子集 |
| `supportDirectoryName` | 支持目录名；省略时使用插件 id（单段安全名称） |
| `nativeMessagingHost` | 本地通信宿主在支持目录内的相对路径 |
| `legacyNames` | 旧菜单文件名（可为中文），仅用于迁移清理 |
| `installName` | 单脚本插件的安装文件名（可与资产文件名不同） |
| `files` | 发布事务在 GATE 通过后写入的逐文件清单 |

所有签名产物由 `scripts/build_feeds.py` 从该源派生：

| 产物 | 消费者 | 内容 |
| --- | --- | --- |
| `registry.json` | 1.9.6 及以后 | 每个插件的地址、展示字段、版本、最低客户端版本、能力 |
| `plugins/manifest.json` | 1.9.4 及更早 | 所有已发布插件的 `version` / `url` / `sha256` / `kind` |
| 各插件仓库 `manifest.json` | 1.9.5 | 该插件的兼容条目 |
| 各插件仓库 `manifest-v2.json` | 1.9.6 及以后 | 兼容条目 + `descriptor` + `files` |

**禁止手工编辑任何一份产物**。发布脚本在内存里更新源、派生全部产物，再作为事务的 `feeds` 阶段一次性写入；GATE 未通过时磁盘上什么都不会变。`build_feeds.py --check` 可确认现有产物仍与源逐字节一致。

## 客户端解析与状态

1. 从内置引导地址 `https://brokenag.github.io/ordina-releases/registry.json` 下载并验签（内存缓存 5 分钟，磁盘缓存 7 天；签名错误绝不降级缓存）。
2. 为每个 registry 条目下载并验签 `manifest-v2.json`，校验 `descriptor.id`、`version`、`protocol == 2`、能力集合、URL 前缀与扩展名。
3. 发现结果按「已安装优先、名称排序」展示；registry 不可用时只展示本地已安装插件，并显示离线提示。
4. `minClientVersion` 高于当前客户端时显示「需更新 ordina」并禁用安装。
5. 安装：下载资产 → 整体 SHA-256/size → 逐文件 SHA-256/size → staging 全量写入 → 备份被替换文件 → 原子替换 → 写 `state.json`。任一步失败按逆序回滚，旧版本保持可用。
6. 更新与安装共用同一状态机；已安装版本高于在线版本时拒绝降级。
7. 卸载：按 `state.json` 删除插件文件，用户修改过的文件先备份；`extension/` 等空目录自动清理，支持目录中的用户数据保留。

状态与备份位于：

```
~/Library/Application Support/ordina/Plugins/<id>/state.json
~/Library/Application Support/ordina/Plugins/<id>/backups/<时间戳>/
~/Library/Application Support/ordina/Plugins/.lock        （跨进程文件锁）
```

### 平滑迁移

- **Minutes Roughcut 0.2.0 → 0.3.0**：旧版写入的 `~/Library/Application Support/Minutes Roughcut/installed.json` 被当作 legacy 版本标记读取；旧菜单脚本、`host.py`、`host.command`、`extension/` 与 Chrome 通信配置路径均保持不变，用户数据不迁移、不丢失。首次用新版安装时写入统一 `state.json`，旧标记保留。
- **Import YueLiu 旧中文名**：`legacyNames` 声明 `导入阅流.py`；检测到旧文件时显示「已安装（未托管）」，安装后把旧文件备份到 `backups/` 再移除，只保留一个菜单入口。
- **Multicam Tools**：单脚本插件，`installName` 保持 `Multicam Tools.py`，升级只替换该文件。

## 新增插件流程（不需要重新编译客户端）

1. 在插件源目录放 `plugin.json`，声明 `id` / `name` / `summary` / `symbol` / `version` / `minClientVersion` / `protocol: 2` / `capabilities` / `supportDirectoryName`（如需要）/ `nativeMessagingHost`（如需要）/ `files`（每个文件 role，`extension/` 下的文件可省略并自动归为 extension）。
2. 预演：`python3 scripts/publish_plugin.py <id> --source <插件源目录> --dry-run`。
3. 真实发布：`python3 scripts/publish_plugin.py <id> --source <插件源目录> --execute --lab-report docs/validation/lab-release.json`。发布事务会创建 Release、GATE 校验资产、把 `files` 写回唯一源、派生并推送 registry 与两份插件清单。
4. 客户端下一次读取 registry 即可发现该插件；**不需要修改客户端源码、不需要重新编译、不需要发布新的 ordina 版本**。

多文件插件打包为确定性 JSON 容器，客户端校验整体与逐文件哈希；单脚本插件直接下载 `.py`。`plugin.json` 与 `extension/manifest.json` 的版本号应一起递增。

## 兼容性矩阵

| 客户端 | 读取的清单 | 新增插件是否需要改客户端 |
| --- | --- | --- |
| 1.9.4 及更早 | `ordina-releases/plugins/manifest.json` | 是 |
| 1.9.5 | 各插件仓库 `manifest.json` | 是 |
| 1.9.6 | `registry.json` → `manifest-v2.json`（只读兼容字段） | 是 |
| 阶段 B 及以后 | `registry.json` → `manifest-v2.json`（`descriptor` + `files`） | **否** |

旧客户端忽略 `descriptor` / `files` 等新增字段，因此服务端可以先升级清单与 registry，再发布新客户端。

## 注意事项

- **不要删除清单仍引用的 Release**：会让需要下载该版本的客户端拿到死链。
- 同版本无法靠重跑 `publish_plugin.py` 恢复（版本必须递增），只能重建同名 Release 并上传字节完全一致的资产。
- 判断资产是否可用用 GET，不要用 `curl -I`（GitHub 资产恒返回 404）。
- 旧版 `install.py` 仅保留给 1.9.6 及更早客户端；新客户端不会执行它。确认无旧客户端后可从插件源目录移除。
- Chrome 扩展仍需用户手动「加载已解压的扩展程序」，这是 Chrome 的平台限制，插件中心只负责准备目录、复制路径并打开扩展页。

## 正式上线计划

1. **先升清单，不升客户端**：用发布事务把 `minutes` 0.3.0、`multicam` 2.1（清单升级）与 `yueliu`（如要发布）的 `manifest-v2.json` 写入各自仓库，同时更新 `registry.json` 与兼容清单。旧客户端继续读取兼容字段，不受影响。
2. **验证线上体检**：`python3 scripts/check_feeds.py --remote` 应覆盖 registry、兼容清单、两份插件清单与资产哈希；`build_feeds.py --check` 应与线上逐字节一致。
3. **构建新客户端**：在本地非沙盒 Terminal 用标准 `xcodebuild` 构建（`scripts/build_app.py`），不要使用临时 `swiftc` 产物或旧 App 替换。新客户端读取 v2 清单。
4. **真实客户端验证**：安装/更新/卸载 `minutes`、`multicam`、`yueliu`；确认旧 `installed.json` 迁移、用户数据保留、Chrome 通信配置路径正确；再在隔离 Registry 发布一个客户端从未见过的测试插件，确认仅改 Registry 即可发现与安装。
5. **发布软件**：`appcast.xml` 与 `registry.json` 必须在同一发布事务中推送；先 registry、真实客户端验证后再 appcast（沿用阶段 A 的发布顺序）。
6. **回滚**：`registry.json`、兼容清单与各插件 `manifest-v2.json` 恢复到上一已知良好提交；**绝不删除 Release**。回滚后重跑 `check_feeds.py --remote`。

验收记录见 `docs/validation/plugin-protocol.json`（13 项：发现、安装、更新、卸载、离线缓存、Registry 不可用、签名错误、资产损坏、版本不兼容、安装中断、三个现有插件回归）。
