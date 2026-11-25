# 开发者中心与发布引擎

> **阶段 C 已完成（本地检查点，未上线）。** 插件发布不再依赖仓库里的命令行脚本：客户端内有一个默认隐藏的「开发者中心」，把 ZIP 交给本机独立部署的 Python 发布引擎，由引擎复用阶段 A 的六阶段持久事务完成发布。客户端只负责界面、进程和状态，不解析任何人类日志。

## 分层

```
ordina.app（SwiftUI）                        发布引擎（独立部署）
DeveloperCenterView   界面与三区布局   ──▶   ordina-publisher-<版本>.pyz
DeveloperCenterModel  状态机 / 事件           ├─ dev_publish.py      命令与契约
DevPublisherTool      进程 + JSON Lines       ├─ dev_credentials.py  授权校验
DevToolManager        工具下载/校验/回滚      ├─ dev_zip.py          ZIP 适配
      │                                       ├─ publish_plugin.py   插件协议（阶段 B）
      │  stdout 每行一个 JSON 对象            └─ durable_*/release_transaction.py
      └──────────────────────────────────────▶      （阶段 A 六阶段事务）
```

- 客户端**只认结构化结果**：`{"type":"result","ok":…}` / `{"type":"event",…}`，不解析人类日志，也不拼接 shell 命令（argv 逐项传递）。
- 所有副作用都在引擎里：读仓库、验签、写清单、推送、记录事务。客户端没有网络写权限相关的逻辑。
- 引擎以 zipapp 形式独立存在，可单独更新，不进入 `ordina.app` 包，也不影响 App 签名。

## 文件位置

全部位于用户目录，**不在 App 包内、不在任何工具版本目录内**：

```
~/Library/Application Support/ordina/Developer/
  Publisher/installed.json                 当前工具版本 + 历史版本摘要
  Publisher/versions/<版本>/ordina-publisher-<版本>.pyz
  Publisher/downloads/                     下载临时文件
  journals/<事务>.json                     事务日志（崩溃后可恢复）
  journals/<事务>.json.meta.json           插件/版本/说明等展示信息
  records.jsonl                            发布/恢复/回滚历史
  LocalTest/                               本地测试生成的临时资产
```

凭据**只在钥匙串**：服务 `ordina-dev-publisher`，账号 `default`，内容是签名信封，不是口令、不是 Token、不是私钥。仓库、日志、journal、JSON 输出里都不会出现凭据内容。

## 使用流程

入口：设置页（点击版本号 10 次）→「测试选项」→「开发者中心…」。默认隐藏，不占正常用户界面。

1. **安装发布工具**：工具区「检查更新」读取签名清单，校验 Ed25519 签名、SHA-256 与大小，下载后做健康检查（`hello` 的版本/协议必须与清单一致），才切到新版本。
2. **导入授权**：`credential-import` 校验签名、有效期、范围与仓库白名单后写入钥匙串。未授权时只能看到工具状态，不能预览或发布。
3. **拖入 ZIP**：自动识别 id / 名称 / 版本 / 最低客户端版本 / 能力，判断「新增 / 更新 / 同版本 / 降级」。
4. **本地测试**：把包解到隔离目录，按协议校验后只安装到本机（不动 Registry），可一键卸载。
5. **填写更新说明**：会进入 Release 正文与清单。
6. **预览**：展示文件级 diff、将写入的清单、需要的仓库步骤、阻塞项与**指纹**（64 位十六进制）。
7. **发布**：必须带上预览指纹；输入若在预览后变化会被拒绝（`preview.stale`）。新增插件需要显式确认创建仓库。
8. **管理与恢复**：事务列表显示阶段进度；中断的事务可「继续」（resume）或「回滚」（rollback）；发布历史保留在 `records.jsonl`。
9. **工具更新/回滚**：独立通道。存在未完成事务时禁止替换引擎；替换失败自动保留旧版本。

发布过程中的阶段事件来自引擎真实回调，UI 不推测进度：

```
preflight → asset → gate → feeds → push → postflight
```

## JSON 契约（协议 1）

```json
{"type":"event","event":"stage","stage":"feeds","state":"running","index":4,"total":6}
{"type":"event","event":"log","message":"…"}
{"type":"result","ok":true,"data":{…}}
{"type":"result","ok":false,"error":{"code":"publish.failed","message":"…","stage":"feeds","journal":"/…/tx.json","details":{…}}}
```

- `protocol` 只在**不兼容变更**时递增；新增字段是加法，客户端必须忽略未知字段。
- 命令：`hello doctor credential-status credential-import inspect local-test preview publish status resume rollback history`。
- 退出码：`0` 成功，`1` 工具失败，`2` 用法错误。
- 错误码前缀稳定：`package.*` ZIP 拒绝、`auth.*` 授权、`repository.*` 仓库、`publish.*` 发布、`journal.*` 事务、`release.invalid` 版本/协议、`tool.error` 兜底。

## ZIP 规范

一个包 = 一个插件，支持「压缩文件夹」和「压缩文件夹内容」两种形状（单一顶层目录会被剥离）。

必填 `plugin.json`：

| 字段 | 说明 |
| --- | --- |
| `id` | 小写字母/数字/短横线，与仓库路径一致 |
| `name` / `summary` / `symbol` | 展示字段 |
| `version` | 必须高于线上版本（新增插件任意） |
| `minClientVersion` | 客户端最低版本，低于则拒绝安装 |
| `protocol` | 当前为 `2` |
| `capabilities` | 只能是客户端已实现的能力白名单 |
| `files` | `{路径: {role, installName?}}`，`extension/` 下可省略并自动归为 `extension` |
| `supportDirectoryName` / `nativeMessagingHost` / `legacyNames` | 可选 |

适配层在**写入任何字节之前**拒绝：

| 检查 | 上限 / 规则 | 错误码 |
| --- | --- | --- |
| ZIP 本体 | 4 MB | `package.size` |
| 条目数 | 96 | `package.count` |
| 解压总量 | 8 MB | `package.size` |
| 单文件 | 1 MB | `package.file` |
| 压缩比 | 200× | `package.bomb` |
| 路径长度 | 200 字符、≤4 层 | `package.entry` |
| 绝对路径 / 反斜杠 / 盘符 / 控制字符 / dotfile | 拒绝 | `package.entry` |
| `..` 穿越、空名、非法字符 | 拒绝 | `package.entry` |
| 符号链接、设备、硬链接、加密条目 | 拒绝 | `package.symlink` / `package.special` / `package.encrypted` |
| 重复路径（含大小写折叠与 NFC 归一） | 拒绝 | `package.duplicate` |
| 缺少根 `plugin.json` | 拒绝 | `package.manifest` |

解压用 `O_EXCL` 逐文件创建 + 解析后父目录校验，条目不可能覆盖隔离目录之外的文件。

## 安全模型

**信任链**：客户端内置一把 Ed25519 公钥（`SUPublicEDKey`），Registry、插件清单、软件 appcast、**工具清单**都由同一把密钥签名；客户端逐层验签，任何一层失败都拒绝，**签名失败绝不回退到缓存**（只有网络故障才复用已验签的磁盘缓存，7 天有效）。

**授权是引擎自己校验的**，不是靠隐藏入口：

- 凭据 = 签名文档 `{format, developer, keyId, issuedAt, notBefore, expires, scopes, repositories}`。
- `keyId` 必须等于 `ordina-feed-1`；有效期 ≤ 400 天；`notBefore` 之前 5 分钟内可用；过期即拒绝。
- `scopes` 只能是 `plugin.publish / plugin.localTest / plugin.rollback / tool.install / repository.create`。
- `repositories` 必须是 `BrOKenAG/` 下、不含通配前缀、≤80 字符的 1–8 条；实际仓库还要匹配 `BrOKenAG/ordina-plugin-*` 策略。
- 文件形式读取只允许在 `ORDINA_DEV_LAB=1` 且权限 0600 时使用；生产只读钥匙串。

**Lab 注入是编译期隔离的**：`ORDINA_PLUGIN_LAB` / `ORDINA_PLUGIN_REGISTRY` / `ORDINA_PLUGIN_PUBLIC_KEY` / `ORDINA_DEV_*` 这些开关全部包在 `#if DEBUG || ORDINA_LAB` 里，Release 构建里根本不存在这些分支，环境变量无法把线上客户端指向别的 Registry 或公钥。

**发布工具是独立签名产物**：生产构建**不包含** `dev_lab_backend.py`（隔离后端）和 `lab_signing.py`（实验室签名器），`tests/test_build_dev_tool.py` 会验证这一点。

**预览指纹**覆盖 ZIP 摘要、版本、说明、工具版本、协议、客户端版本、Registry 源摘要与资产摘要，防止「预览之后偷换输入」。

## 发布工具打包规范（供其它工具遵循）

```bash
python3 scripts/build_dev_tool.py --version 1.0.0 --sign   # 构建 + 写签名清单
python3 scripts/build_dev_tool.py --check                  # 校验已提交清单与源码一致
```

- 产物：`dist/dev-tool/ordina-publisher-<版本>.pyz`（zipapp，固定时间戳，可重现构建）。
- 清单：`dev-tool/manifest.source.json`（明文源）→ `dev-tool/manifest.json`（签名信封）。
- 版本号写进入口脚本与 `VERSION` 文件，**必须与该版本的清单条目一致**，否则客户端的健康检查会拒绝安装。
- 只能包含 `build_dev_tool.MODULES` 列出的引擎模块；`LAB_MODULES` 仅用于隔离验收构建。
- 更新必须：清单验签 → SHA-256/大小校验 → 下载 → 健康检查（`hello.version == 清单版本`）→ 切换；任一步失败保留旧版本。
- 存在未完成事务时拒绝替换引擎（`DevToolManager.install` 直接读磁盘上的 journal，不依赖工具本身）。
- 分发通道独立：工具版本、Release 资产、清单、回滚都与软件/插件无关。

## 验收

```bash
python3 scripts/verify_developer_center.py   # 26 项端到端，写 docs/validation/developer-center.json
python3 scripts/verify.py                    # 全部 Python 回归 + Swift 验证 + 上面的端到端
python3 scripts/verify.py --no-acceptance    # 跳过端到端，只跑回归
```

端到端在一个临时 `HOME`、临时 lab 仓库与 loopback HTTP 服务里跑完 26 个场景：工具缺失/清单/安装/完整性、inspect、本地测试与卸载、预览、过期指纹、恶意 ZIP、ZIP bomb、降级、发布新增插件、**由客户端 harness 真实发现并安装该插件**、更新、中断、并发锁、未完成事务阻塞工具更新、恢复、工具更新、再发布、回滚、工具回滚、无签名授权、越权授权、记录保留。

实验室使用一次性 Ed25519 密钥签清单与授权（`lab_signing.py`，与 Sparkle 逐字节一致），因此验收不需要开发者的钥匙串，也不会碰真实仓库。

## 安全与故障审计

| 威胁 | 控制手段 | 证据 |
| --- | --- | --- |
| ZIP 路径穿越 / 绝对路径 / 反斜杠 / 盘符 | 归一化后逐段校验，任一异常在写盘前拒绝；解压时再校验解析后的目标仍在隔离目录内 | `test_dev_zip.py` 穿越、绝对路径、Windows 路径用例 |
| 符号链接 / 设备 / 硬链接 / 加密条目 | 按 `external_attr` 的 `S_IFMT` 与 general purpose bit 判定，直接拒绝 | `test_dev_zip.py` symlink/special/encrypted 用例 |
| ZIP bomb | 压缩比 200×、单文件 1 MB、总量 8 MB、条目 96、路径 200 字符与 4 层深度，全部在解压前按中央目录判定；解压时按实际字节再计数 | `test_dev_zip.py` bomb/oversize/count 用例；端到端 `zip_bomb` |
| 重复路径 / 大小写折叠 / NFC 变体 | `casefold()` 去重 + NFC 归一；解压用 `O_EXCL`，已存在即拒绝 | `test_dev_zip.py` duplicate 用例、覆盖保护用例 |
| 一个插件写另一个插件的文件 | 安装路径来自签名清单的 `path`，必须是相对安全路径；安装器按插件 id 的目录与既有能力写入，插件无法声明任意目标 | `test_dev_zip.py`、`test_plugin_protocol.py`、`VerifyPluginInstaller` |
| 签名覆盖范围不足 | 清单签名覆盖 `descriptor` 与 `files[]`（路径/大小/哈希/角色/安装名）；容器里多出的文件会被 `result.count == container.files.count` 拒绝 | `verify_plugin_protocol.py`、`test_plugin_protocol.py` |
| 降级/重放旧版本 | 引擎拒绝不高于线上版本（`release.invalid`）；客户端 `newer()` 拒绝降级；清单不含可重放的旧签名 | 端到端 `version_downgrade`、`test_dev_publish.py` |
| 用缓存绕过签名 | 磁盘缓存只在**网络故障**时回退，回退时重新验签；签名失败绝不回退；缓存记录绑定 registry URL 与 7 天有效期 | `verify_plugin_protocol.py` 的 `offline_cache` / `bad_signature` |
| lab 注入绕过信任边界 | 所有 lab 开关在 `#if DEBUG || ORDINA_LAB` 内，Release 构建没有这些分支；生产工具包不含 lab 模块 | `VerifyDeveloperCenter`、`test_build_dev_tool.py` |
| 进程被杀 / 断电 | journal 每阶段 `fsync` + 原子 `os.replace` + 目录 `fsync`；恢复只依赖 journal 与资产，不需要工具在内存里保留状态 | 端到端 `interrupted_publish` → `resume_publish`；`test_durable_*` |
| 写入失败 / 磁盘满 | 写入前先写 journal；写入异常统一转成 `publish.failed` 并保留可恢复状态，资产与旧清单不被半写 | 端到端注入写失败；`ENOSPC` 走同一 OSError 路径（未单独模拟） |
| 并发发布 | 隔离后端用独占锁文件，第二个发布者拿到 `release lock is held`；生产后端用 git 非强制引用更新，天然并发失败 | 端到端 `concurrent_blocked` |
| Pages 延迟 / 读回不一致 | 发布事务在 postflight 前读回线上内容并内置重试（阶段 A） | `verify_lab_release.py`、`test_release_transaction.py` |
| 未完成事务时替换引擎 | `DevToolManager.install` 与 `rollback` 先数磁盘上的未完成 journal，>0 直接拒绝 | 端到端 `pending_blocks_tool_update` |
| 凭据泄漏 | 只存钥匙串；状态输出只有 `valid/developer/keyId/expires/scopes/repositories`；仓库、日志、journal、stdout 都不含凭据 | `test_dev_credentials.py` 状态字段断言、端到端授权失败用例 |
| 参数注入（仓库名 / 备注 / 文件名） | GitHub API 全部走 argv 参数（`gh api` 显式参数），不拼 shell；仓库名必须匹配 `BrOKenAG/ordina-plugin-<id>` 形状 | `test_dev_publish.py`、`test_github_production_backend.py` |

未自动覆盖、必须人工确认的项：真实 Xcode Debug/Release 构建、真实 App 内交互（拖拽、sheet、状态动画）、真实 GitHub Pages 延迟下的端到端发布、磁盘写满的真实系统行为。

## 人工验收清单（必须在本地 Xcode 构建的 App 上完成）

> **优先用简版清单**：`python3 scripts/prepare_app_acceptance.py` 会生成 `dist/app-acceptance/README.md`，把环境准备压缩成 4 条命令、把 App 内操作压缩成 8 步，且全部数据隔离在临时 HOME 里，不接触正式生产资源。下面这份是覆盖更广的完整清单，供需要逐项核对时使用。注意隔离验收必须启动 **Debug** 产物，Release 构建会编译剔除 lab 开关并回落到生产地址。

自动验收覆盖了引擎、协议、事务与客户端逻辑，但**真实 App 构建与人工交互必须在本机 Xcode 里做**（当前会话的 `xcodebuild` 受 `sandbox-exec: Operation not permitted` 限制，无法解析 SwiftPM 依赖）：

1. Xcode 里 Debug、Release 各构建一次，确认无警告级错误。
2. 从「测试选项」打开开发者中心，确认默认隐藏、三个区域布局正确、深色/浅色主题下的 `Paper` 视觉一致。
3. 无工具时：状态显示「未安装」，按钮引导安装；有工具无授权时：预览/发布被阻止且提示导入授权。
4. 拖入 1.10.0 的新插件 ZIP → 本地测试 → 在 Resolve 里确认脚本出现 → 卸载 → 确认文件移除。
5. 预览 → 发布 → 观察真实六阶段进度 → 完成后**自动回到拖入页**（不再残留已选 ZIP、更新说明与进度清单，底部保留成功提示）→ 确认 Registry 与插件清单一致。
6. 发布中断电（Cmd+Q 或断网）→ 重新打开 → 事务显示可恢复 → 继续 → 完成后状态一致。
7. 回滚该次发布 → 确认 Registry 恢复到上一版本、Release 资产保留。
8. 工具更新期间存在未完成事务 → 确认被阻止并提示。
9. 打开「高级」折叠区，确认路径与诊断信息可复制，且**不显示任何凭据内容**。

## 故障排查

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `auth.missing` | 钥匙串没有 `ordina-dev-publisher` | 导入授权 |
| `auth.signature` | 授权被改动或密钥不匹配 | 重新签发 |
| `auth.scope` / `auth.repository` | 授权范围不足 | 签发时补 scope / repository |
| `preview.stale` | 预览后输入变化 | 重新预览 |
| `repository.missing` | 新插件的目标仓库不存在 | 勾选「创建仓库」 |
| `publish.failed` + `stage` | 某阶段失败（网络、Pages 延迟、资产校验） | 看 `details.completed`，`resume` 或 `rollback` |
| 工具更新被拒「存在未完成的发布事务」 | 有 `preparing`/`publishing` 事务 | 先恢复或回滚 |
| `package.*` | ZIP 不合规 | 对照上面的限制表 |

## 与旧流程的关系

`scripts/publish_plugin.py` 仍然可用（命令行、CI、应急），开发者中心只是它的图形入口，两者共用同一套协议、事务与签名代码，不存在两份发布逻辑。
