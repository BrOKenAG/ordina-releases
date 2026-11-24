# 插件分发架构

1.9.5 的固定清单路径为各插件仓库的 `manifest.json`，其中资产必须保留主仓库地址；1.9.6 的 registry 指向 `manifest-v2.json`，可以使用各插件仓库的资产。两者必须分别签名、验证，并在 registry 门禁之前准备。主仓库旧版资产已按历史哈希恢复。

跨仓库发布并非原子操作；中途失败时，先记录每个仓库的实际清单和本地提交状态，保留所有 Release，确认签名、版本和哈希后才能继续。自动跨仓库插件发布和回滚推送目前仍保持关闭，避免未经验证的恢复覆盖线上内容。

> **1.9.6 发布准备中：跨仓库插件推送和回滚推送已暂时停用。以下旧命令不应执行；先完成故障恢复验证。软件 appcast 须在真实客户端验证后单独发布。**

每个插件拥有独立的发布仓库、独立的版本号和独立的签名清单。客户端通过一个**统一签名的注册表** `registry.json` 发现这些清单，不需要为每个插件硬编码地址。

## 仓库拓扑

| 仓库 | 内容 | Pages |
| --- | --- | --- |
| `BrOKenAG/ordina-releases` | 软件本体、`appcast.xml`、`registry.json`、兼容清单 `plugins/manifest.json`、唯一源 `plugins/registry.source.json` | `main` 根目录 |
| `BrOKenAG/ordina-plugin-minutes` | Minutes Roughcut 的 Release 资产 + `manifest.json` | `main` 根目录 |
| `BrOKenAG/ordina-plugin-multicam` | Multicam Tools 的 Release 资产 + `manifest.json` | `main` 根目录 |

软件版与插件版、插件与插件之间完全隔离：某个插件发新版不会改动其他插件的清单，也不会产生新的软件发行版。

## 签名信任链

客户端只内置一个引导地址和一把公钥（`config/Info.plist` 的 `SUPublicEDKey`）：

```
内置公钥 (Ed25519)
  └─ registry.json            签名信封：payload(base64) + signature
       ├─ plugins[name].feed          → 各插件仓库的 manifest.json
       └─ plugins[name].downloadPrefix → 该插件资产允许的路径前缀
            └─ manifest.json          签名信封（同一把密钥）
                 └─ entry.url          → Release 资产
                      └─ SHA-256（脚本）/ Ed25519（软件 DMG）校验
```

- 注册表和每份插件清单都用钥匙串账号 `ordina-sparkle` 的同一把密钥签名，格式一致：`{"payload": "...", "signature": "..."}`。
- 客户端先验签 registry，再验签插件清单；任何一层签名无效都拒绝，**不会降级到内置地址**。
- 资产地址必须落在 registry 为该插件下发的 `downloadPrefix` 内，且是 `github.com` 的 HTTPS 直链；`check_feeds.py` 复用同一套规则做体检。
- 下载内容只含插件文件，安装逻辑（`install.py`）始终来自已签名的 App 本身。

## 唯一数据源

`plugins/registry.source.json` 是**唯一由人维护**的文件。所有签名产物都由 `scripts/build_feeds.py` 从它派生：

| 产物 | 消费者 | 内容 |
| --- | --- | --- |
| `registry.json`（Pages 根） | 1.9.6 及以后 | 每个插件的 `repository` / `feed` / `downloadPrefix` / `kind` |
| `plugins/manifest.json` | 1.9.4 及更早 | 所有已发布插件的 `version` / `url` / `sha256` / `kind` |
| 各插件仓库 `manifest.json` | 1.9.5 及以后 | 该插件自己的条目 |

**禁止手工编辑任何一份产物**。发布脚本在内存里更新源、派生全部产物，再作为事务的 `feeds` 阶段一次性写入；GATE 未通过时磁盘上什么都不会变。用 `build_feeds.py --check` 可随时确认现有产物仍与源逐字节一致。

源中的字段：

| 字段 | 用途 |
| --- | --- |
| `repository` | 该插件的 GitHub 发布仓库 |
| `feed` | 该插件签名清单的 Pages 地址 |
| `downloadPrefix` | 该插件资产允许的路径前缀，供客户端和体检脚本校验 |
| `kind` | `bundle`（多文件插件包）或 `script`（单脚本） |
| `asset` / `source` | 资产文件名 / 仓库内源码路径 |
| `published` | 是否已有线上版本；`false` 的插件不进入任何产物 |
| `version` / `sha256` / `size` | 当前已发布资产，仅用于派生兼容清单与各插件清单 |

`registry.json` **不广播版本号或哈希**：版本权威始终在各插件自己的清单里，注册表只回答「去哪找清单、允许从哪里下载资产」。已支持的插件可以独立发版；新增插件仍需客户端支持其发现、展示、安装和运行入口。

## 客户端解析

1. 从内置引导地址 `https://brokenag.github.io/ordina-releases/registry.json` 下载并验签。
2. 按插件 id 查 `feed`，校验其为 `brokenag.github.io` 下 `/ordina-plugin-*/manifest.json` 的 HTTPS 地址。
3. 下载并验签该插件清单，用 registry 下发的 `downloadPrefix` 校验 `entry.url`。
4. registry 中未登记的插件按「暂无在线版本」处理，回退到 App 内置副本（例如尚未发布的 `yueliu`）。
5. 网络故障可复用本进程中已验签的 registry 缓存；**签名无效不会回退缓存**，避免降级攻击。

进程内缓存让插件管理页刷新时只下载一次注册表。

## 兼容性矩阵

| 客户端 | 读取的清单 | 由谁生成 |
| --- | --- | --- |
| 1.9.4 及更早 | `ordina-releases/plugins/manifest.json`（合并清单） | `build_feeds.py` 自动派生 |
| 1.9.5 | 各插件仓库自己的 `manifest.json` | `build_feeds.py` 自动派生 |
| 1.9.6 及以后 | `ordina-releases/registry.json` → 各插件仓库 `manifest.json` | `build_feeds.py` 自动派生 |

三份清单内容都来自同一份源，因此双写兼容不需要人工维护多份数据。1.9.5 的 Release、清单和二进制保持原样，不因本架构改动。

> 已知历史缺陷：1.9.5 把资产地址前缀硬编码为主仓库，会拒绝独立仓库的清单条目。当前版本已改为从签名 registry 下发前缀，修复该缺陷；1.9.5 已发布产物不作修改。

## 新增插件流程

1. 创建公开仓库 `ordina-plugin-<name>`，开启 Pages（`main` 根目录），提交 `.nojekyll`。
2. 在 `plugins/registry.source.json` 增加一条，`published: false`，填好 `repository` / `feed` / `downloadPrefix` / `kind` / `asset` / `source`。
3. 在 `Integrations/OptionalPlugins/` 放入源码并递增版本。
4. 运行 `python3 scripts/publish_plugin.py <name> --repo-path <name>=<本地克隆路径>`。

事务会自动完成：创建 Release → GATE 校验资产 → 更新源 → 派生四份签名清单 → 推送。客户端只支持预先定义的插件；新增插件仍需实现客户端入口，不能仅靠发布 registry 自动发现。

多文件插件（`kind: bundle`）打包为确定性 JSON 容器，客户端校验整体 SHA-256、逐文件路径白名单与体积，再用 App 自带的 `install.py` 安装；下载内容不含可执行安装逻辑。递增版本时同时更新 `plugin.json` 与 `extension/manifest.json` 中的版本号。

## 注意事项

- **不要删除清单仍引用的 Release**：会让需要下载该版本的客户端拿到死链。已安装用户不受影响，但清理必须在清单改指向新版本之后。
- 同版本无法靠重跑 `publish_plugin.py` 恢复（版本必须递增），只能重建同名 Release 并上传字节完全一致的资产。
- 判断资产是否可用用 GET，不要用 `curl -I`（GitHub 资产恒返回 404）。
- 兼容清单的停止同步以「确认无 1.9.4 及更早活跃用户」为前提，且不得影响 1.9.5 的独立清单。
- 标准构建与发布事务流程见 [构建与发布](releases.md)。
