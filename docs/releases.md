# 构建与发布

发布准备先校验每份兼容和新版插件清单及其资产，然后按顺序写入插件仓库、主仓库兼容清单与 registry。线上 registry 内容必须与预期一致，且完整检查通过；独立的客户端验证完成后才能执行 appcast 阶段。多仓库变更不是原子操作，任何失败都应先检查各仓库实际状态。

> **1.9.6 发布准备中：跨仓库插件推送和回滚推送已暂时停用。以下旧命令不应执行；先完成故障恢复验证。软件 appcast 须在真实客户端验证后单独发布。**

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

## 准备

1. 在 Xcode 的 Debug、Release 配置中递增 `CURRENT_PROJECT_VERSION`，并设置 `MARKETING_VERSION`。
2. 同步 `Tests/verify_app.py` 中的版本断言。
3. 运行源码测试、构建 App、验证签名，再生成 DMG：

```sh
python3 scripts/verify.py
python3 scripts/build_app.py --output dist/ordina.app
python3 Tests/verify_app.py dist/ordina.app
python3 -m pip install --target .build/dmg-libs -r requirements-build.txt
python3 scripts/build_distribution.py --app dist/ordina.app --output dist --release-label build.34
```

DMG 打包依赖 Pillow 的编译扩展，需用与安装依赖时相同的 Python 解释器运行（例如 `/usr/local/bin/python3`）。

## 生成签名更新清单

官方发布者获取上一版已签名清单后生成下一版清单：

```sh
curl --fail --location https://brokenag.github.io/ordina-releases/appcast.xml --output dist/previous-appcast.xml
python3 scripts/prepare_update.py \
  --app dist/ordina.app \
  --archive dist/ordina-1.9.6-build.34.dmg \
  --output dist/update \
  --previous-feed dist/previous-appcast.xml
```

脚本会验证版本、最低系统、下载地址、文件大小和 Ed25519 签名，并隔离当前安装包以保留历史版本的下载链接。`prepare_update.py` **只生成本地清单，不发布任何东西**。

当前正式更新公钥位于 `config/Info.plist`，对应私钥保存在本机钥匙串。源码使用者发布自己的分支时，需要生成自己的密钥并配置自己的更新地址。

## 发布事务（强制顺序）

发布由 `scripts/release_transaction.py` 统一执行，软件与插件共用同一状态机。**硬不变式：签名清单只有在它引用的资产已被下载并验证之后才会被写入或推送。**

| 阶段 | 做什么 | 失败后果 |
| --- | --- | --- |
| `preflight` | 检查 main 分支、暂存区、与远端同步、GitHub 登录、签名工具 | 立即终止，无任何改动 |
| `asset` | 创建 Release 并上传资产；同版本已存在则校验字节一致后复用 | 立即终止，不写清单 |
| `gate` | **GET 下载地址**：HTTP 200、文件大小、SHA-256；软件版另验 Ed25519 签名 | **立即终止，绝不写清单** |
| `feeds` | 生成并写入签名清单（含 registry、兼容清单、各插件清单） | 可能部分写入，需回滚 |
| `push` | 提交并推送 `main` | 同上 |
| `postflight` | 运行 `check_feeds.py --remote`，等待 Pages 生效（最多 6 次重试） | 报告失败，人工介入 |

软件发布：

```sh
python3 scripts/publish_app_release.py \
  --app dist/ordina.app \
  --archive dist/ordina-1.9.6-build.34.dmg \
  --feed dist/update/appcast.xml \
  --notes-file dist/release-notes.txt
```

软件发布分两阶段：先发布 registry，并等待线上内容一致且完整检查通过；真实客户端验证通过后，再以显式参数发布 appcast。跨仓库发布不是原子操作，失败时保留已发布的资产，检查当前线上状态后重试或按已知良好版本回滚。

插件发布：

```sh
python3 scripts/publish_plugin.py minutes \
  --repo-path minutes=../ordina-plugin-minutes \
  --repo-path multicam=../ordina-plugin-multicam
```

两个入口都支持 `--dry-run`：走完整状态机但不产生任何网络或磁盘副作用，用于发布前演练。任一阶段失败会打印已完成阶段与建议操作，并以非零退出码结束。

**绝不要**把「上传 DMG/插件资产」和「推送清单」拆成两条独立命令手工执行。1.9.3 与 1.9.5 发布时都曾因这种拆分导致清单短暂指向不存在的 Release：前者是清单漏推，客户端收不到更新；后者是 `gh release create` 网络报错但清单仍被推送。

## 发布前检查

```sh
python3 scripts/build_feeds.py --check \
  --repo-path minutes=../ordina-plugin-minutes \
  --repo-path multicam=../ordina-plugin-multicam
python3 scripts/check_feeds.py --preflight \
  --repo-path minutes=../ordina-plugin-minutes \
  --repo-path multicam=../ordina-plugin-multicam
```

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
| `feeds` / `push` 中途失败 | 先运行 `check_feeds.py --remote` 确认线上状态，再决定重试或回滚 |
| `postflight` 因 Pages 未生效失败 | 事务内置重试；也可等几分钟后手动重跑体检 |
| 同版本资产已存在且字节一致 | 自动复用，不重复创建 Release |
| 同版本资产已存在但内容不同 | 拒绝发布，递增版本号 |
| 误删了清单仍引用的 Release | 重建同名 Release 并上传**字节完全一致**的资产；源码未变时 `build_bundle()` 是确定性的 |

判断资产是否可用要用 GET：`curl -sL -o /dev/null -w '%{http_code}' <url>` 应为 200。GitHub 对 HEAD 请求（`curl -I`）返回 404，不能用它判断。

`publish_plugin.py` 要求版本递增，所以同版本**无法靠重跑脚本自愈**，只能手工重建 Release。清理旧插件版本必须在新版本发布、清单改指向新 Release 之后进行。

## 回滚

```sh
python3 scripts/rollback_feeds.py --to HEAD~1 --dry-run
python3 scripts/rollback_feeds.py --to HEAD~1 --push
```

它把 `appcast.xml`、`plugins/manifest.json`、`registry.json` 恢复到已知良好的提交。**绝不删除 Release**：删除旧清单仍引用的资产会给缓存了该清单的客户端造成死链。回滚后运行 `check_feeds.py --remote` 复核；各插件仓库的 `manifest.json` 需单独恢复，或用 `build_feeds.py --push` 从源重新生成。

## 兼容期

`plugins/manifest.json` 只服务于 1.9.4 及更早版本，由 `build_feeds.py` 从同一份源自动生成，不需要人工维护。确认没有这类活跃用户后，可在源中停止同步并删除该产物，但不要影响 1.9.5 读取的独立清单。

当前构建采用临时签名、Hardened Runtime 和 App 级库验证例外，以加载 Sparkle；未经过 Developer ID 签名与 Apple 公证，首次启动可能受到系统拦截。

插件分发的仓库拓扑、签名信任链与新增插件流程见 [插件分发架构](plugin-distribution.md)。

## 1.9.6 分阶段恢复检查

恢复前先暂停其他发布。记录 `git status -sb`、`git log --oneline origin/main..HEAD`，再只读检查各仓库 `manifest.json`、`manifest-v2.json`、主仓库兼容清单与 registry 的线上内容。不要删除 Release 或覆盖存在未知差异的清单。跨仓库并非原子操作：某个插件仓库成功后，其他仓库仍可能失败。

- 插件仓库中途失败：保留已发布的清单；确认每个已发布清单签名、版本、哈希与准备文件一致后，才能从同一准备结果重试。发现更高版本或同版本不同哈希则停止。
- Git 提交成功但推送失败：比较 `origin/main..HEAD` 的提交与文件，只允许确认过的清单提交进入远端；无法确认时不要执行推送。解决后先重新读取线上状态，再重试。
- Pages 延迟或网络中断：不要发布 appcast；等待后重新检查 registry 精确内容和完整资产验证。HTTP 错误不能当作资源不存在。
- 需要回滚：先用 `rollback_feeds.py --to <已知良好提交> --dry-run` 核对主仓库文件；当前自动推送回滚已禁用，插件仓库需逐一审查签名、引用和实际线上状态后制定恢复操作。禁止删除仍可能被旧清单引用的 Release。

真实客户端验证完成前不执行 `--publish-appcast`。软件构建须由操作者在 macOS 原生终端运行，不使用单独编译的可执行文件或旧 App 替换。
