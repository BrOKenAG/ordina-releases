# ordina

ordina 是 macOS 上的 Resolve 项目目录与素材管理工具。产品名称统一为 ordina，工程为 `Ordina.xcodeproj`，共享 Scheme 为 `Ordina`，App 源码位于 `Ordina/`。

只修改当前工程。为兼容已安装版本，保留应用标识 `com.example.MagicTools.Youth`、已有用户设置、归档协议及项目元数据文件名；具体兼容项见 `docs/compatibility.md`。这些标识不用于产品展示。

使用 `python3 scripts/build_app.py --output dist/ordina.app` 构建。使用 `python3 scripts/verify.py` 运行源码验证，`python3 Tests/verify_app.py dist/ordina.app` 验证打包资源与签名。构建缓存和分发产物不提交 Git，不打包源码 ZIP。

Resolve 的 Project Setup 源码位于 `Integrations/Project Setup.py`；本机运行副本位于用户 Library 下的 Resolve `Fusion/Scripts/Utility/Project Setup.py`。改动该集成时同步两处。验证默认仅使用临时文件和替代 Resolve 对象，生产工程操作需要用户授权。

本仓库同时承载源码及更新分发。不得改写已发布安装包，或修改已有签名 `appcast.xml` 的内容而不重新签名。更新私钥保存在本机钥匙串，禁止提交私钥、令牌、用户配置或实际素材。

界面文案只保留必要标题、标签、按钮、数据和状态。详细帮助按需展示，错误提示说明问题与下一步。修改页面时同步清理冗余文案。

## 插件规范与按需读取

插件开发先读 `docs/PLUGIN_DEVELOPMENT.md`；涉及 Manifest、安装器或发布工具时再读 `docs/PLUGIN_PROTOCOL.md`。以实际代码、测试和已发布协议为准，目标或草案不代表已实现；发现冲突先记录，不擅自修改兼容契约。

## 增量开发与发布安全

从当前分支、`git status`、`git diff` 和相关测试定位变更，优先使用 `rg` 精准查找，复用现有实现，避免无关重构和重复读取。开发期间执行受影响的专项测试，功能闭环后统一回归和必要的真实构建；最终发布须验证最新源码的 Debug 和 Release。仅提取必要日志和结果，不把计划、类型检查、模拟测试或历史构建当成真实验收。最终报告原则上不超过 300 字，说明检查点、验证、未完成项及验收命令。

软件、插件和开发者发布工具使用独立版本及更新通道。生产写入必须独立核验官方仓库授权和可信签名；隐藏入口和本地 TOTP 不是最终授权边界。测试使用隔离目录、Keychain 标识、测试凭据及后端，禁止回落生产凭据，不触碰真实用户数据。不得提交密钥、令牌、TOTP 密钥或会话数据，不覆盖已发布的不可变资产。未经明确授权，不 push、merge、正式签名、发布或修改线上资源。
