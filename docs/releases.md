# 构建与发布

发布准备先校验每份兼容和新版插件清单及其资产，然后由**持久化跨仓库事务**一次性写入插件仓库、主仓库兼容清单与 registry。线上 registry 内容必须与预期一致，且完整检查通过；独立的客户端验证完成后才能执行 appcast 阶段。跨仓库发布不是原子操作，事务记录会保存每个仓库的前后状态，中断后可续发或回滚。

> **阶段 A 已完成**：软件与插件正式发布入口已接入持久化事务；跨仓库断点续发、并发保护、Pages 延迟恢复和回滚已在私有测试仓库完成真实端到端验证。正式发布仍需显式 `--execute` 和有效的验证报告；软件 appcast 须在真实客户端验证后单独发布。`build_feeds.py --push` 与 `rollback_feeds.py --push` 等旧的直接推送入口保持停用。

源码、安装包和更新清单共用 `BrOKenAG/ordina-releases` 仓库。GitHub Pages 从 `main` 分支根目录读取 `appcast.xml` 与 `registry.json`，下载文件位于 GitHub Releases。

## 构建环境：标准 Xcode 流程

`scripts/build_app.py` 使用标准 `xcodebuild`，**不含任何环境专用编译参数或临时替代方案**。它必须在**本地非沙盒终端**（如「终端」App）或 Xcode 图形界面内运行。

原因是 SwiftPM 的 `Resolve Package Graph` 阶段会调用 `sandbox-exec`，而该调用在受沙盒限制的自动化环境中被系统拒绝：

```
xcodebuild: error: Could not resolve package dependencies:
  sandbox-exec: sandbox_apply: Operation not permitted
```

这与工程配置无关：Swift 编译器本身可用（`xcrun swiftc` 正常），绕过工具层沙盒后该错误仍然存在。用下面的命令检查一个终端是否具备构建条件：

```sh
/usr/bin/sandbox-exec -p '(version 1)(allow default)' /bin/echo OK
```

若返回 `OK`，在该终端运行 `build_app.py`；若返回 `Operation not permitted`，改用 Xcode 打开工程按 ⌘B，或换一台标准 macOS 构建机。**不要**把 `swiftc` 手动编译、复用旧 `.app` Bundle 之类的临时手段写进仓库或当作正式流程——那些只是特定受限环境下的应急产物，无法保证资源与签名完整。

私钥保存在本机钥匙串账号 `ordina-sparkle` 下，不进入仓库，也不需要 CI Secrets。

### 真实 Xcode 构建的编译设置（2026-11-25 修复）

标准 Xcode 流程会把**全部** app 源码一起编译并开启工程的
`SWIFT_UPCOMING_FEATURE_MEMBER_IMPORT_VISIBILITY = YES`（Debug/Release
两个配置都在 `project.pbxproj`）。该特性要求：凡使用 `Combine` 定义的
`ObservableObject` / `@Published` / `Publisher` 等符号的文件，必须显式
`import Combine`，不能依赖 SwiftUI 的传递导入。

`Ordina/OptionalPluginsView.swift` 的 `PluginCenterModel` 用了
`ObservableObject` 与 4 个 `@Published` 却缺少该 import，首次真实
`xcodebuild`（SwiftEmitModule normal arm64）因此失败：

```
OptionalPluginsView.swift:44:21: error: type 'PluginCenterModel' does not conform to protocol 'ObservableObject'
OptionalPluginsView.swift:45:6: error: initializer 'init(wrappedValue:)' is not available due to missing import of defining module 'Combine'
```

之前 `verify.py` 的 Swift 验证只把**部分**源码与各自的 harness 一起编译，
既不开 `MemberImportVisibility`，也不包含视图文件，所以从未暴露此错误。
修复是在该文件补一行 `import Combine`（不删功能、不改隔离验收、不降安全标准）。

防复发：`scripts/verify_swift_typecheck.py` 用 `swiftc -typecheck` 对**全部**
app 源码、按工程的全部 upcoming features 分别在 Debug / Release 配置下做
类型检查，并已接入 `verify.py`（检测到 `Sparkle.framework` 即自动运行，
干净工作区可用 `--no-typecheck` 跳过）。它是编译/类型检查，不替代完整
`xcodebuild`；真实构建仍须在非沙盒终端执行：

```sh
bash scripts/local_xcode_build.sh --debug     # Debug 完整构建（隔离验收用此产物）
bash scripts/local_xcode_build.sh --release   # Release 构建 + 签名校验
```

结果见 `docs/validation/swift-typecheck.json`。

## 准备

1. 在 Xcode 的 Debug、Release 配置中递增 `CURRENT_PROJECT_VERSION`，并设置 `MARKETING_VERSION`。
2. 同步 `Tests/verify_app.py` 中的版本断言。
3. 运行源码测试、构建 App、验证签名，再生成 DMG：

```sh
python3 scripts/verify.py
python3 scripts/build_app.py --output dist/ordina.app
python3 Tests/verify_app.py dist/ordina.app
python3 -m pip install --target .build/dmg-libs -r requirements-build.txt
python3 scripts/build_distribution.py --app dist/ordina.app --output dist --release-label build.36
```

DMG 打包依赖 Pillow 的编译扩展，需用与安装依赖时相同的 Python 解释器运行（例如 `/usr/local/bin/python3`）。

## 生成签名更新清单

官方发布者获取上一版已签名清单后生成下一版清单：

```sh
curl --fail --location https://brokenag.github.io/ordina-releases/appcast.xml --output dist/previous-appcast.xml
python3 scripts/prepare_update.py \
  --app dist/ordina.app \
  --archive dist/ordina-1.10.0-build.36.dmg \
  --output dist/update \
  --previous-feed dist/previous-appcast.xml
```

脚本会验证版本、最低系统、下载地址、文件大小和 Ed25519 签名，并隔离当前安装包以保留历史版本的下载链接。`prepare_update.py` **只生成本地清单，不发布任何东西**。

当前正式更新公钥位于 `config/Info.plist`，对应私钥保存在本机钥匙串。源码使用者发布自己的分支时，需要生成自己的密钥并配置自己的更新地址。

## 发布事务（强制顺序）

发布由 `scripts/durable_release.py` 驱动，软件与插件共用同一状态机；`scripts/release_transaction.py` 提供计划数据结构、资产 GATE 与清单过渡规则等基础组件。**硬不变式：签名清单只有在它引用的资产已被下载并验证之后才会被写入或推送。**

真实发布会把完整计划（资产哈希、每个仓库要写入的文件、事务前 HEAD）持久化到 `.transactions/<id>.json`，由 `scripts/durable_transaction.py` 执行。每个远端写入都是条件更新（HEAD 不匹配即失败），提交信息带事务标记，因此「远端已提交但响应丢失」也能被安全识别并续发。事务完成前必须读回线上 Pages 内容；Pages 尚未生效时事务保持未完成，重跑同一命令即可恢复。

| 阶段 | 做什么 | 失败后果 |
| --- | --- | --- |
| `preflight` | 检查 main 分支、暂存区、与远端同步、GitHub 登录、签名工具、验证报告 | 立即终止，无任何改动 |
| `asset` | 创建 Release 并上传资产；同版本已存在则校验字节一致后复用 | 立即终止，不写清单 |
| `gate` | **GET 下载地址**：HTTP 200、文件大小、SHA-256；软件版另验 Ed25519 签名 | **立即终止，绝不写清单** |
| `feeds` | 持久化事务一次性写入所有仓库的签名清单、registry 与源；含资产复核、清单过渡校验、并发 HEAD 保护、Pages 生效复核 | 事务保持未完成，可续发或回滚 |
| `push` | 检查点：确认事务状态为 `complete` 且每个仓库都记录了提交 | 报告失败，人工核对事务记录 |
| `postflight` | 运行 `check_feeds.py --remote`，等待 Pages 生效 | 报告失败，人工介入 |

`feeds` 阶段内部每次恢复都会**重新下载并核对资产**，因此不会出现「资产已被删除但清单仍被推送」的情况。`.transactions/` 已加入 `.gitignore`，不进入仓库。

分布式锁使用主仓库的 `refs/heads/ordina-transaction-lock` 引用，事务完成后标记为 `free`；它只用于发布互斥，不改变 `main` 分支的内容。生产后端只允许 `BrOKenAG/ordina-releases` 与 `BrOKenAG/ordina-plugin-*` 仓库、对应 github.com 下载地址与 `brokenag.github.io` Pages 地址，其余一律拒绝。

软件发布（第一阶段只发布 registry 与插件清单）：

```sh
python3 scripts/publish_app_release.py \
  --app dist/ordina.app \
  --archive dist/ordina-1.10.0-build.36.dmg \
  --feed dist/update/appcast.xml \
  --notes-file dist/release-notes.txt \
  --execute --lab-report docs/validation/lab-release.json
```

软件发布分两阶段：先发布 registry，并等待线上内容一致且完整检查通过；真实客户端验证通过后，再以 `--publish-appcast --validation-report <记录>` 单独执行第二个事务发布 appcast。两个事务各有独立的 journal，互不复用。

插件发布：

```sh
python3 scripts/publish_plugin.py minutes \
  --execute --lab-report docs/validation/lab-release.json
```

`--repo-path` 仅用于预演时生成本地副本；真实发布由事务直接写入各插件仓库，同时更新 `manifest.json`（兼容清单）与 `manifest-v2.json`（新版清单）。

两个入口都支持 `--dry-run`：走**与真实发布完全相同的六阶段持久事务**，但使用内存后端和临时 journal，不产生网络或仓库副作用。真实发布必须显式 `--execute`，且要求 `docs/validation/lab-release.json` 中五项隔离仓库检查全部为 `pass`；缺任一条件即拒绝执行。任一阶段失败会打印已完成阶段、事务路径与建议操作，并以非零退出码结束。

事务恢复与回滚（不需要重复提供发布参数，直接从 journal 重建后端）：

```sh
python3 scripts/durable_journal.py --status .transactions/<id>.json
python3 scripts/durable_journal.py --resume .transactions/<id>.json --postflight
python3 scripts/durable_journal.py --rollback .transactions/<id>.json
```

**绝不要**把「上传 DMG/插件资产」和「推送清单」拆成两条独立命令手工执行。1.9.3 与 1.9.5 发布时都曾因这种拆分导致清单短暂指向不存在的 Release：前者是清单漏推，客户端收不到更新；后者是 `gh release create` 网络报错但清单仍被推送。

## 发布前检查

```sh
python3 scripts/verify_lab_release.py --execute
python3 scripts/build_feeds.py --check \
  --repo-path minutes=../ordina-plugin-minutes \
  --repo-path multicam=../ordina-plugin-multicam
python3 scripts/check_feeds.py --preflight \
  --repo-path minutes=../ordina-plugin-minutes \
  --repo-path multicam=../ordina-plugin-multicam
```

- `verify_lab_release.py --execute` 在两个**私有测试仓库**真实演练多文件跨仓库事务、远端提交后断线续发、并发提交拒绝、安全回滚，以及本地 HTTP 服务模拟的 Pages 延迟恢复。通过后写入 `docs/validation/lab-release.json`；正式发布入口会拒绝没有这份报告的执行。脚本只访问 `ordina-release-lab-*` 仓库，不修改正式仓库。
- `build_feeds.py --check` 确认所有现有签名清单都能由唯一源 `plugins/registry.source.json` 逐字节派生，防止有人手工改过清单。
- `check_feeds.py --preflight` 验签本地清单、检查 registry 结构，并用**与客户端完全相同的规则**校验每个下载地址（github.com HTTPS、registry 下发的前缀、扩展名、版本与哈希格式）。预演模式不下载资产，因为此时资产尚未上传。

## 发布后体检

`postflight` 会自动运行，也可手动执行：

```sh
python3 scripts/check_feeds.py --remote
```

它从线上 Pages 读取 `appcast.xml`、`registry.json`、兼容清单和每个插件的独立清单，验签后逐个下载清单引用的资产，核对大小、SHA-256 与 Ed25519 签名；任一项不可用即以非零退出码结束。插件列表、feed 地址和允许的下载地址前缀全部来自签名 registry，脚本中不硬编码。

## 失败与重试

| 场景 | 处理 |
| --- | --- |
| `preflight` / `asset` 失败 | 修复后重跑同一命令，无副作用需清理 |
| `gate` 失败 | 清单未被写入；检查 Release 资产后重跑同一命令（幂等） |
| `feeds` / `push` 中途失败 | 事务记录保持未完成；先 `durable_journal.py --status` 查看各仓库前后 HEAD，再用 `--resume` 续发或 `--rollback` 回滚 |
| 远端提交成功但响应丢失 | 事务标记 + 条件写可安全识别；重跑同一命令或 `--resume` 即可，不会重复写入 |
| 其他发布者并发提交 | 事务前快照与 HEAD 条件写会拒绝覆盖，事务安全失败并保留对方内容 |
| `postflight` 因 Pages 未生效失败 | 生产后端内置 4 次重试；仍失败则事务保持未完成，等 Pages 生效后 `--resume --postflight` |
| 同版本资产已存在且字节一致 | 自动复用，不重复创建 Release |
| 同版本资产已存在但内容不同 | 拒绝发布，递增版本号 |
| 误删了清单仍引用的 Release | 重建同名 Release 并上传**字节完全一致**的资产；源码未变时 `build_bundle()` 是确定性的 |

判断资产是否可用要用 GET：`curl -sL -o /dev/null -w '%{http_code}' <url>` 应为 200。GitHub 对 HEAD 请求（`curl -I`）返回 404，不能用它判断。

`publish_plugin.py` 要求版本递增，所以同版本**无法靠重跑脚本自愈**，只能手工重建 Release。清理旧插件版本必须在新版本发布、清单改指向新 Release 之后进行。

## 回滚

```sh
python3 scripts/durable_journal.py --status .transactions/<id>.json
python3 scripts/durable_journal.py --rollback .transactions/<id>.json
python3 scripts/check_feeds.py --remote
```

事务回滚会把**所有参与仓库**恢复到记录中的事务前字节，并且只在 HEAD 与记录一致时执行；出现他人提交或已回滚的远端变化会直接拒绝，不会覆盖。回滚后运行 `check_feeds.py --remote` 复核。

`rollback_feeds.py --dry-run` 仍可用于对照历史提交，但其 `--push` 入口保持停用，不再作为跨仓库回滚手段。**绝不删除 Release**：删除旧清单仍引用的资产会给缓存了该清单的客户端造成死链。

## 兼容期

`plugins/manifest.json` 只服务于 1.9.4 及更早版本，由 `build_feeds.py` 从同一份源自动生成，不需要人工维护。确认没有这类活跃用户后，可在源中停止同步并删除该产物，但不要影响 1.9.5 读取的独立清单。

当前构建采用临时签名、Hardened Runtime 和 App 级库验证例外，以加载 Sparkle；未经过 Developer ID 签名与 Apple 公证，首次启动可能受到系统拦截。

插件分发的仓库拓扑、签名信任链与新增插件流程见 [插件分发架构](plugin-distribution.md)。

## 1.9.6 分阶段恢复检查（历史记录）

以下是 1.9.6 准备期的手工恢复指引，现已由持久化事务覆盖，保留作事故排查参考。

恢复前先暂停其他发布。记录 `git status -sb`、`git log --oneline origin/main..HEAD`，再只读检查各仓库 `manifest.json`、`manifest-v2.json`、主仓库兼容清单与 registry 的线上内容。不要删除 Release 或覆盖存在未知差异的清单。跨仓库并非原子操作：某个插件仓库成功后，其他仓库仍可能失败。

- 插件仓库中途失败：保留已发布的清单；确认每个已发布清单签名、版本、哈希与准备文件一致后，才能从同一准备结果重试。发现更高版本或同版本不同哈希则停止。
- Git 提交成功但推送失败：比较 `origin/main..HEAD` 的提交与文件，只允许确认过的清单提交进入远端；无法确认时不要执行推送。解决后先重新读取线上状态，再重试。
- Pages 延迟或网络中断：不要发布 appcast；等待后重新检查 registry 精确内容和完整资产验证。HTTP 错误不能当作资源不存在。
- 需要回滚：先用 `rollback_feeds.py --to <已知良好提交> --dry-run` 核对主仓库文件；当前自动推送回滚已禁用，插件仓库需逐一审查签名、引用和实际线上状态后制定恢复操作。禁止删除仍可能被旧清单引用的 Release。

真实客户端验证完成前不执行 `--publish-appcast`。软件构建须由操作者在 macOS 原生终端运行，不使用单独编译的可执行文件或旧 App 替换。

## 阶段 A 完成状态

- 软件与插件正式发布入口均已接入 `durable_release.py` + 持久化 journal；`--dry-run` 仍走零副作用预演路径。
- 事务记录覆盖全部参与仓库、资产哈希与前后 HEAD；支持 `--resume` 续发、`--rollback` 回滚、`--status` 只读查看。
- 生产后端在写入前复核资产 GET/哈希/Ed25519、签名清单引用、清单过渡规则与白名单 URL；完成前读回 Pages 内容并内置重试。
- `scripts/verify_lab_release.py --execute` 在私有测试仓库完成五项真实端到端验证；报告 `docs/validation/lab-release.json` 是正式发布的前置条件。
- `build_feeds.py --push`、`rollback_feeds.py --push` 等旧直接推送入口仍停用；标准 Xcode 构建流程未改动，未引入 CI Secrets。
- 阶段划分与验收目标见仓库根目录的 [阶段A进度与阶段B方案.md](../阶段A进度与阶段B方案.md)。

## 阶段 B 完成状态

- 插件描述协议 v2 已落地（`Ordina/PluginProtocol.swift`，Python 侧同构实现 `scripts/plugin_protocol.py`）：`descriptor` 声明 id/名称/图标/版本/最低客户端版本/类型/能力/支持目录/宿主清单名/旧文件名，`files[]` 声明路径、大小、哈希、角色、安装名与可执行位。
- 签名 Registry（`format: 2`）与签名插件清单（`format: 1`）保持向后兼容，只追加字段；1.9.4 / 1.9.5 / 1.9.6 旧客户端继续读取原字段。
- 客户端不再有 `OptionalPlugin.allCases` 或任何插件 id 分支：`PluginCatalog` 从 Registry 动态发现并合并本地安装记录，`OptionalPluginsView` 只渲染 `PluginCatalog.Item`。
- 安装、更新、卸载、完整性校验、兼容性判断、并发锁与失败回滚统一收敛到 `PluginInstaller`；文件清单由签名清单声明，客户端内不再写死。
- 能力白名单只有 `resolve.utilityScript`、`resolve.support`、`chrome.nativeMessagingHost`、`chrome.unpackedExtension` 四项；descriptor 无法携带 shell 命令、绝对路径或任意目标路径，安装行为仍完全由客户端编译期逻辑决定。
- 验收：`scripts/verify_plugin_protocol.py` 编译一份快照客户端，在 loopback 隔离环境发布客户端源码完全不认识的新插件 ID，完成发现/展示/安装/更新/卸载/回滚，并覆盖 Registry 不可用、签名错误、资产损坏、版本不兼容、安装中断；报告 [validation/plugin-protocol.json](validation/plugin-protocol.json) 全项 `pass`。
- 新插件上线流程（无需重编译客户端）与新协议字段说明见 [plugin-distribution.md](plugin-distribution.md)。

## 阶段 C 实现状态（代码与自动化验收完成，真实 App 验收待确认）

- **三条独立通道**：软件（`appcast.xml` + Sparkle）、插件（`registry.json` + 各插件 `manifest-v2.json`）、发布工具（`dev-tool/manifest.json` + `ordina-publisher-<版本>.pyz`）。三者版本号、签名清单、目录与回滚流程互不影响。
- **开发者中心**（默认隐藏在设置页的「测试选项」里）：拖入 ZIP → 自动识别 → 本地测试 → 填写说明 → 预览（含指纹）→ 发布，复用阶段 A 的六阶段持久事务；另有事务管理、继续、回滚、工具更新与工具回滚。
- **发布引擎独立部署**：Python zipapp，位于 `~/Library/Application Support/ordina/Developer/Publisher/versions/`，与客户端之间是版本化 JSON Lines 契约（`protocol: 1`），客户端不解析人类日志、不拼接 shell。
- **凭据只走钥匙串**（`ordina-dev-publisher`），签名文档声明范围与仓库白名单，由引擎自己校验；仓库、日志、journal 与 JSON 输出都不含凭据内容。
- **安全收口**：ZIP 适配层在写盘前拒绝穿越/符号链接/ZIP bomb/重复路径；预览指纹防止预览后偷换；参数化 GitHub API 防止仓库名注入；lab 注入改为编译期 `#if DEBUG || ORDINA_LAB`，Release 构建无法通过环境变量切换 Registry 或公钥；生产工具包不含隔离后端与实验室签名器。
- **工具更新独立**：先验签清单、再验 SHA-256/大小、下载后做健康检查（`hello.version` 必须等于清单版本），失败保留旧版本；存在未完成事务时拒绝替换引擎。
- 验收：`python3 scripts/verify_developer_center.py` 27 项全 `pass`，报告 [validation/developer-center.json](validation/developer-center.json)。使用说明与人工验收清单见 [developer-center.md](developer-center.md)。

### 本次收尾已解决项（自动化可复验）

- `sparkle_tools.ROOT` 改为用户级稳定缓存目录（`~/Library/Application Support/ordina/Publisher/tools/sparkle-2.10.1`），不再由 `__file__` 或 `ORDINA_DEV_REPO` 推导；`ORDINA_SPARKLE_TOOLS` 只是显式部署覆盖。独立部署验收 `docs/validation/standalone-tool.json`（11 项全 pass）在**没有源码树、没有 lab 变量**的最小环境里跑通 `hello / doctor / inspect / credential-status / 正式签名流程`。
- 修复 `doctor` 在 `gh` 不存在时崩溃的问题（独立部署下 `gh` 可能不在 PATH）。
- 工具清单改为**隔离测试信任链**：lab/debug 构建可用 `ORDINA_DEV_MANIFEST_PUBLIC_KEY` 注入一次性公钥，Release 构建编译期剔除；验收不再读取生产签名密钥。生产清单仍由发版人本机 `ordina-sparkle` 密钥签名（`build_dev_tool.py --sign`），这是客户端固定公钥验签的必要条件。
- 新增 ENOSPC 专项测试（`Tests/test_enospc.py` 5 项 + 端到端 `enospc_consistency`）：写资产/写清单/写 journal 三个位置注入真实 `errno.ENOSPC`，验证无半写文件、事务记录可恢复且恢复后无重复。开发者中心验收现为 **27 项全 pass**。
- 当前发布工具源码候选版本为 1.6.0，历史 1.0.0 元数据保留。候选清单尚未使用正式密钥签名；独立的 lab 工具包即使版本号相同，也不属于生产资产。
- `publish_dev_tool.py` 统一工具通道的构建/签名/上传/清单发布/回滚准备；默认只预演（六阶段事务走 `DryRunBackend`，零副作用），`--publish-manifest --execute` 走阶段 A 持久事务，无裸 push / `gh api` / curl。回归见 `Tests/test_publish_dev_tool.py`（7 项）。
- Xcode 工程已配置 1.10.0 (build 36)；完整三通道兼容矩阵与首次上线/回滚顺序见 [compatibility-matrix.md](compatibility-matrix.md)。

### 上线命令清单（只准备与预演，未执行）

下列命令仅供发布前复核；本轮未授权正式签名、上传或修改线上资源。工具候选清单仍待正式签名和完整发布审查。

严格顺序：**工具 → 插件 → 软件**；软件最后，因为它是唯一会通知全体用户的动作。所有清单写入都必须走阶段 A 六阶段持久事务。

```bash
# ===== 通道 1：开发者发布工具（先上线，不影响任何现网客户端） =====
python3 scripts/build_dev_tool.py --version 1.6.0 --sign
python3 scripts/build_dev_tool.py --check
python3 scripts/check_dev_tool_versions.py

# 资产上传与清单发布均由持久事务执行；仅获授权后运行。
python3 scripts/publish_dev_tool.py --publish-manifest --execute \
  --lab-report docs/validation/lab-release.json

# ===== 通道 2：插件（registry 保持 format 2，向后兼容 1.9.6） =====
python3 scripts/publish_plugin.py <plugin-id> --source <源目录> --dry-run
python3 scripts/publish_plugin.py <plugin-id> --source <源目录> --execute \
  --lab-report docs/validation/lab-release.json

# ===== 通道 3：软件本体 1.10.0 (build 36)，最后 =====
python3 scripts/publish_app_release.py --app <ordina.app> \
  --archive dist/ordina-1.10.0-build.36.dmg --feed appcast.xml \
  --notes-file <notes.txt> --execute --lab-report docs/validation/lab-release.json
python3 scripts/publish_app_release.py --app <ordina.app> \
  --archive dist/ordina-1.10.0-build.36.dmg --feed appcast.xml \
  --notes-file <notes.txt> --publish-appcast --execute \
  --validation-report <真实客户端验证报告.json>

# ===== 上线后只读体检 =====
python3 scripts/check_feeds.py --remote
python3 scripts/verify_standalone_tool.py
python3 scripts/verify_developer_center.py

# ===== 回滚（反向顺序，永不删除 Release 资产） =====
# 软件：先停止 appcast 广播
python3 scripts/rollback_feeds.py --to <已知良好提交> --dry-run
# 插件：清单回退，逐一核对签名 / 版本 / 哈希
python3 scripts/rollback_feeds.py --to <已知良好提交> --dry-run
# 工具：清单回退 + 客户端本地工具回滚
python3 scripts/durable_journal.py --rollback <发布事务记录>
# 客户端：开发者中心 → 工具 → 回滚到 1.0.0
```

> 上述命令在获得明确授权前**全部不执行**；`publish_dev_tool.py` 不带 `--execute` 时只做六阶段预演。

### 最终收尾补充（本次真实执行）

- **真实 App 隔离验收环境**：`scripts/prepare_app_acceptance.py` 一键生成 `dist/app-acceptance/`（独立 HOME、loopback Registry、隔离 lab 后端、一次性签名密钥、两个 lab 工具版本、5 类测试 ZIP、已通过真实六阶段事务发布的初始插件）。`scripts/app_acceptance_server.py` 提供全部只读路由；`scripts/verify_app_acceptance.py` 7 项校验工具清单/资产/Registry/插件清单/资产的签名与 SHA-256，并断言所有 URL 都指向 `127.0.0.1`，报告 [validation/app-acceptance-server.json](validation/app-acceptance-server.json)。
- **隔离验收必须用 Debug 构建**：`DevBridge.isLab`、`PluginEnvironment.current`、`DevBridge.toolManifestPublicKey` 的隔离分支都由 `#if DEBUG || ORDINA_LAB` 控制，Release 构建会编译剔除并静默回落到正式 Pages / Registry / 生产公钥。因此 `launch.sh` 默认指向 `.build/Build/Products/Debug/ordina.app`，README 明确要求 `bash scripts/local_xcode_build.sh --debug`。正式 1.10.0 出包用 `--all`，与隔离验收分开。
- **线上资源只读体检**：`python3 scripts/check_feeds.py --remote` 当前结果为 **4 项 FAIL**：`appcast 签名 OK`、`appcast 35 1.9.6 OK`（2,132,937 字节）、`registry 签名 OK`、`兼容清单 签名 OK`；但 `registry minutes/multicam` 报「缺少 v2 插件描述」，`兼容清单` 中两个插件报「registry 中未登记」。原因是线上 `registry.json` 仍是阶段 B 之前的描述（仅 `downloadPrefix/feed/kind/repository`），而当前 `build_feeds.py` 会额外写入 `protocol/symbol/minClientVersion/capabilities`。`dev-tool/manifest.json` 线上为 `HTTP 404`。这 4 项是**插件通道尚未按阶段 B v2 发布**的预期未上线状态，不是代码回归；也正说明首次上线必须按「工具 → 插件 → 软件」顺序，先让 registry / `manifest-v2.json` 带上完整 v2 描述，再发布 1.10.0。线上资源本轮只读，未做任何写入。
- **真实 Xcode 构建阻塞仍在**：本轮按「优先申请正常本机执行权限」重试，关闭 agent 沙盒后直接运行 `xcodebuild -list` / `-resolvePackageDependencies` / `build`，仍在 `Resolve Package Graph` 阶段报 `sandbox-exec: sandbox_apply: Operation not permitted`（exit 74）。`/usr/bin/sandbox-exec -p '(version 1)(allow default)' /bin/echo` 单独执行成功，说明失败特定于 Xcode 26.3 / SwiftPM 的 package-graph sandbox profile；`SWIFTPM_DISABLE_SANDBOX=1` 只让一次 `-resolvePackageDependencies -skipPackageUpdates` 短暂成功，重复执行与正式 `build` 均再次失败；仓库没有独立 `Package.swift`，无法单独预热。**完整 Xcode 构建与真实 App 人工验收仍须由用户在本机 Xcode.app 或原生终端完成**，不得用 `swiftc -typecheck` 或单独编译的 harness 替代。完整命令见 `scripts/local_xcode_build.sh`。
- **阶段 A 脚本健壮性修复**：`verify_lab_release.py` 的残留事务锁清理原本只在 `finally` 执行，若上一次运行被中断，下一次会在 `multi_file_transaction` 直接失败「release lock is held」；现改为运行前与结束后各清理一次，保证可重复执行。
