# ordina

ordina 是 macOS 上的 Resolve 项目目录与素材管理工具。产品名称统一为 ordina，工程为 `Ordina.xcodeproj`，共享 Scheme 为 `Ordina`，App 源码位于 `Ordina/`。

只修改当前工程。为兼容已安装版本，保留应用标识 `com.example.MagicTools.Youth`、已有用户设置、归档协议及项目元数据文件名；具体兼容项见 `docs/compatibility.md`。这些标识不用于产品展示。

使用 `python3 scripts/build_app.py --output dist/ordina.app` 构建。使用 `python3 scripts/verify.py` 运行源码验证，`python3 Tests/verify_app.py dist/ordina.app` 验证打包资源与签名。构建缓存和分发产物不提交 Git，不打包源码 ZIP。

Resolve 的 Project Setup 源码位于 `Integrations/Project Setup.py`；本机运行副本位于用户 Library 下的 Resolve `Fusion/Scripts/Utility/Project Setup.py`。改动该集成时同步两处。验证默认仅使用临时文件和替代 Resolve 对象，生产工程操作需要用户授权。

本仓库同时承载源码及更新分发。不得改写已发布安装包，或修改已有签名 `appcast.xml` 的内容而不重新签名。更新私钥保存在本机钥匙串，禁止提交私钥、令牌、用户配置或实际素材。

界面文案只保留必要标题、标签、按钮、数据和状态。详细帮助按需展示，错误提示说明问题与下一步。修改页面时同步清理冗余文案。
