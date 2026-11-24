# 构建与发布更新

源码、安装包和更新清单共用 `BrOKenAG/ordina-releases` 仓库。GitHub Pages 从 `main` 分支根目录读取 `appcast.xml`，下载文件位于 GitHub Releases。

## 准备

1. 在 Xcode 的 Debug、Release 配置中递增 `CURRENT_PROJECT_VERSION`，并设置 `MARKETING_VERSION`。
2. 同步 `Tests/verify_app.py` 与 README 中的当前版本。
3. 运行源码测试、构建 App、验证签名，再生成 DMG。

以 1.9.1、构建号 29 为例：

```sh
python3 scripts/verify.py
python3 -m pip install --target .build/dmg-libs -r requirements-build.txt
python3 scripts/build_distribution.py --output dist/build.29 --release-label build.29
```

## 签名

当前正式更新公钥位于 `config/Info.plist`，对应私钥保存在本机钥匙串账号 `ordina-sparkle` 下。私钥不在仓库中。源码使用者发布自己的分支时，需要生成自己的密钥并配置自己的更新地址，不能向官方更新地址发布未经授权的内容。

官方发布者获取上一版已签名清单后生成下一版清单：

```sh
curl --fail --location https://brokenag.github.io/ordina-releases/appcast.xml --output dist/previous-appcast.xml
python3 scripts/prepare_update.py \
  --app dist/build.29/ordina.app \
  --archive dist/build.29/ordina-1.9.1-build.29.dmg \
  --output dist/build.29/update \
  --previous-feed dist/previous-appcast.xml
```

首次发布时省略 `--previous-feed`。脚本会获取固定版本的官方 Sparkle 工具并验证下载校验值，使用钥匙串密钥签名，检查版本、最低系统、下载地址、文件大小和签名。生成时隔离当前安装包，以保留历史版本的下载链接。

## 发布顺序

1. 将安装包上传至 `v1.9.1-build.29` 对应的 GitHub Release，核对文件大小、SHA-256 和版本说明后发布。
2. 将新生成的 `appcast.xml` 原样提交到仓库根目录，保留 `.nojekyll`。
3. 等待 Pages 部署完成，运行 `python3 scripts/check_feeds.py --remote`。它会校验两份清单的签名，并逐个下载清单引用的资产，核对大小、SHA-256 与 Ed25519 签名；任一项不可用即以非零退出码结束。
4. 使用独立测试副本验证检查、下载、安装、重启和设置保留，验证篡改包被拒绝。

已发布安装包保持原内容；同一构建号不能用于不同安装包。修改签名清单的任何字节都需要重新签名。源代码提交、签名和 GitHub 发布是独立步骤，私钥不会随源码上传。

当前构建采用临时签名、Hardened Runtime 和 App 级库验证例外，以加载 Sparkle。它未经过 Developer ID 签名与 Apple 公证，首次启动可能受到系统拦截。用户需要手动安装一次包含 Sparkle 的版本，随后可以在软件内更新。

## 独立发布插件

插件有两类，都不需要发布新的 ordina 版本：

| 类型 | 插件 | 版本位置 | 发布命令 |
| --- | --- | --- | --- |
| 单脚本 | multicam、yueliu | 脚本内 `PLUGIN_VERSION` | `python3 scripts/publish_plugin.py multicam` |
| 插件包 | minutes（Minutes Roughcut） | `Minutes Roughcut/plugin.json` 的 `version` | `python3 scripts/publish_plugin.py minutes` |

维护者修改 `Integrations/OptionalPlugins` 下对应内容、递增版本，在已同步的 `main` 分支运行上表命令。例如：

```sh
python3 scripts/publish_plugin.py multicam
python3 scripts/publish_plugin.py yueliu
python3 scripts/publish_plugin.py minutes
```

脚本使用原有 Sparkle 钥匙串密钥对插件清单签名，先发布不可变插件文件，再提交并推送清单与该插件源码。需要 GitHub CLI 登录、发布权限和签名密钥。不要重用已发布的插件版本号；其他未提交改动不会被提交。发布后等待 GitHub Pages 更新。用户的旧脚本会在安装时备份；离线时仍可使用应用内置版本。

**插件包（bundle）**：`minutes` 包含 Resolve 脚本、本地通信组件和 Chrome 扩展文件，发布时打包为一个确定性的 JSON 容器（`minutes.json`），清单中用 `kind: "bundle"` 标记。客户端下载后校验整体 SHA-256、逐文件校验路径白名单与体积，再用**应用自带**的 `install.py` 安装——下载内容不包含可执行安装逻辑。`install.py` 属于应用本身，修改它需要发布新的 ordina 版本。安装后写入 `installed.json` 版本标记，客户端据此判断是否可更新，不会再因与内置副本字节不同而误报。

**不要删除清单仍引用的插件 Release**：`plugins/manifest.json` 的 `url` 直接指向 `plugin-<name>-v<version>` 的资产。删除该 Release 后清单变成死链，已安装用户不受影响，但任何需要下载该版本的客户端都会失败。发布脚本要求版本递增，同版本无法重跑恢复，只能手工重建同名 Release 并上传**字节完全一致**的资产：

```sh
gh release create plugin-minutes-v0.2.0 minutes.json \
  --title 'Minutes Roughcut v0.2.0' --notes '…' --latest=false
```

重建前先用清单里的 `sha256` 核对文件，不一致会被客户端拒绝。用 `curl -sL -o /dev/null -w '%{http_code}' <url>` 确认恢复，应为 200；GitHub 对 HEAD 请求（`curl -I`）返回 404，不能用它判断。清理旧插件版本应在新版本发布、清单改指向新 Release 之后进行。日常或误删后可用 `python3 scripts/check_feeds.py --remote` 一次性复核软件与插件两份清单。

递增插件包版本时，同时更新 `Minutes Roughcut/plugin.json` 与 `extension/manifest.json` 中的版本号，避免 Chrome 显示的扩展版本落后于插件版本。用户在插件管理中点击「更新」即可完成替换；浏览器扩展无需重新加载，仍需在首次使用时按「一键准备浏览器扩展」的提示手动加载一次。

**更新清单必须推送到仓库根目录**：客户端从 GitHub Pages 读取 `appcast.xml`，只上传 DMG 到 Release 而不提交清单，会导致用户收不到更新。发布顺序见上文「发布顺序」第 2 步。

如果文件已上传但清单尚未提交，可以在清单干净的情况下重跑同一命令，脚本只会复用内容完全一致的发布文件。如果提交完成但推送失败，检查 `git status` 和最近提交后执行 `git push origin main`；如果清单未提交，先检查并恢复清单再重试。
