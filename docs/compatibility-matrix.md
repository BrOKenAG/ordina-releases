# ordina 三通道兼容矩阵（1.10.0 发布准备）

本文档只描述版本关系与上线顺序，**不执行任何发布动作**。线上只读核实结果（2026-11-25）：

- `appcast.xml`：最新条目 `sparkle:shortVersionString = 1.9.6`、`sparkle:version = 35`。
- `registry.json`：`format: 2`，插件 `minutes`、`multicam`（只追加字段，旧客户端继续读兼容字段）。
- `dev-tool/manifest.json`：线上 **404**，尚未上线。
- 本仓库 Xcode 工程已配置 `MARKETING_VERSION = 1.10.0`、`CURRENT_PROJECT_VERSION = 36`（Debug 与 Release 两处）。

## 三个独立通道

| 通道 | 版本 | 清单 / 资产 | 签名 | 目录 | 回滚 |
| --- | --- | --- | --- | --- | --- |
| 软件本体 | **1.10.0 (build 36)**（线上 1.9.6 / 35） | `appcast.xml` + `ordina-1.10.0-build.36.dmg` | Sparkle Ed25519（`ordina-sparkle`） | GitHub Releases + Pages 根 | 回退 appcast 到已知良好提交，资产不删 |
| 插件 | 协议 **v2**（`descriptor` + `files[]`）；v1 保留兼容 | `registry.json`（format 2）+ 各插件 `manifest-v2.json`（format 1）+ Release 资产 | 同一把 `ordina-sparkle` Ed25519 | 主仓 + 各 `ordina-plugin-*` 仓 | 清单回退（事务/rollback_feeds 审查），资产不删 |
| 开发者发布工具 | **1.8.0 候选**（protocol 1，`minClientVersion 1.10.0`） | `dev-tool/manifest.json` + `ordina-publisher-1.8.0.pyz` | 同一把 `ordina-sparkle` Ed25519 | `~/Library/Application Support/ordina/Developer/Publisher/versions/` | 清单回退 + 客户端本地工具回滚，资产不删 |

三者共用同一个信任根（客户端编译期固定的 Ed25519 公钥），但**版本号、清单、目录、更新触发与回滚流程完全独立**：升级工具不会重签 App，发布插件不会改 appcast，App 更新不会替换已安装的工具或凭据。

## 客户端 × 通道能力矩阵

| 客户端 | 软件更新 (appcast) | registry format 2 | v2 插件安装 | 开发者中心 | 工具 1.0.0 |
| --- | --- | --- | --- | --- | --- |
| 1.9.4 / 1.9.5 | 支持 | 只读兼容字段 | 否（走旧 `install.py`） | 不存在 | 不适用 |
| 1.9.6（线上） | 支持 | 只读兼容字段 | 否（走旧 `install.py`） | 不存在 | 不适用（`minClientVersion` 拒绝） |
| **1.10.0** | 支持 | 完整读取 | 支持（能力白名单四项） | 默认隐藏，设置页「测试选项」进入 | 支持 |

## 插件协议矩阵

| 协议 | 清单文件 | 字段 | 可服务客户端 | 限制 |
| --- | --- | --- | --- | --- |
| v1 | `manifest.json` | `version` / `url` / `sha256` / `length` | 1.9.6 及更早 | 只读兼容，不再新增 |
| v2 | `manifest-v2.json` | 兼容条目 + `descriptor` + `files[]` | 1.10.0 | 能力白名单仅 `resolve.utilityScript`、`resolve.support`、`chrome.nativeMessagingHost`、`chrome.unpackedExtension` |

`registry.json` 保持 `format: 2`；1.9.6 的真实升级兼容性仍需实际验证。v2 清单里的 `descriptor` / `files[]` 对旧客户端是未知字段，会被忽略。

## 工具版本矩阵

| 工具版本 | protocol | minClientVersion | 状态 | 说明 |
| --- | --- | --- | --- | --- |
| 1.0.0–1.7.0 | 1 | 1.10.0 | 本地测试候选，未发现线上 Release | 不纳入正式清单，资产仍保存在本机 |
| 1.8.0 | 1 | 1.10.0 | 清单已签名，未发布 | 隔离 lab 包使用不同信任链和内容 |

线上未发现 1.0.0 至 1.7.0 的工具 Release；正式候选清单仅列 1.8.0，避免引用不存在的资产。旧本地测试产物保存在 dist/local-unsigned-tool-history-20261127，lab 包不能用于生产发布。

## 首次上线顺序（严格按此顺序）

工具通道不依赖任何现网客户端，插件通道向后兼容，软件通道是唯一会通知所有用户更新的动作，因此：

1. **工具通道先行（不影响任何现网用户）**
   - 本地构建并签名 `dev-tool/manifest.json`（`build_dev_tool.py --sign`）。
   - 经明确授权后，由同一持久事务上传资产并发布清单；重试须核对事务记录。
   - 通过阶段 A 持久事务发布 `dev-tool/manifest.json`（`publish_dev_tool.py --publish-manifest --execute`）。
   - 此时 1.9.6 客户端没有开发者中心，不会读取该清单。
2. **插件通道（保持兼容，可随 1.10.0 一起）**
   - registry 保持 format 2；如需新增插件，先发 registry（事务），再发各插件 `manifest-v2.json`（事务），确认旧客户端仍可读兼容字段。
   - **本次线上只读体检发现**：当前线上 `registry.json` 的插件描述仍只有 `downloadPrefix / feed / kind / repository`，缺少阶段 B 的 `protocol / symbol / minClientVersion / capabilities`；`check_feeds.py --remote` 因此报 4 项 FAIL。首次上线必须用当前 `build_feeds.py` 重新生成并发布 registry 与各 `manifest-v2.json`，补齐 v2 描述，否则 1.10.0 客户端与发布后体检都会拒绝。
   - 顺序原因：客户端先 bootstrap registry 才知道插件 feed 地址。
3. **软件本体最后**
   - 本机 Xcode 构建 1.10.0 (build 36)，完成真实客户端验证并写报告。
   - `publish_app_release.py --execute`（registry 事务，不通知更新）。
   - 最后单独执行 `--publish-appcast`，此时才向全体用户广播 1.10.0。

**禁止**：直接覆盖线上清单、裸 `git push`、`gh api` 写 feed、`curl` 上传 Pages；所有清单写入必须走阶段 A 的六阶段持久事务（journal + 条件写 + 资产 GATE + 线上读回）。

## 回滚顺序（反向，且永远不删 Release 资产）

1. **软件**：停止 appcast 广播 —— `rollback_feeds.py --to <已知良好提交> --dry-run` 核对后人工审查推送；不要删除仍被旧清单引用的 DMG。
2. **插件**：清单回退到上一个签名版本，保留资产；逐一核对签名、版本、哈希与引用。
3. **工具**：通过 `durable_journal.py --rollback <发布事务记录>` 恢复已验证的事务快照 → 首次上线没有可回滚到的线上工具版本，客户端需使用其本地已验证版本；历史资产不删除。

未完成事务（journal `publishing` / `preparing`）存在时，客户端与脚本都会拒绝替换发布工具，避免恢复数据丢失。

## 相关文档

- [releases.md](releases.md) — 阶段 A/B/C 状态与可直接复制的上线/回滚命令清单
- [developer-center.md](developer-center.md) — ZIP 规范、JSON 契约、安全模型与人工验收
- [plugin-distribution.md](plugin-distribution.md) — 插件仓库拓扑与协议字段
