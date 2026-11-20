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
3. 等待 Pages 部署完成，从线上下载清单和安装包，重新核对签名及 SHA-256。
4. 使用独立测试副本验证检查、下载、安装、重启和设置保留，验证篡改包被拒绝。

已发布安装包保持原内容；同一构建号不能用于不同安装包。修改签名清单的任何字节都需要重新签名。源代码提交、签名和 GitHub 发布是独立步骤，私钥不会随源码上传。

当前构建采用临时签名、Hardened Runtime 和 App 级库验证例外，以加载 Sparkle。它未经过 Developer ID 签名与 Apple 公证，首次启动可能受到系统拦截。用户需要手动安装一次包含 Sparkle 的版本，随后可以在软件内更新。
