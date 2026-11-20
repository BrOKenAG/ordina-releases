# 数据与升级兼容

产品、Xcode 工程、源码目录和内部界面类型统一使用 ordina / Ordina。以下早期标识属于已部署的数据或调用接口，继续保留。

| 标识 | 用途 |
| --- | --- |
| `com.example.MagicTools.Youth` | 已安装 App 的 Bundle ID、用户偏好、书签、归档服务与插件备份目录 |
| `.magic-resolve.json` | 项目库、Resolve 工程唯一 ID 及库内文件夹路径 |
| `.magic-archive-history.json` | 已复制素材、校验信息及重链接记录 |
| `.magic-archive-legacy` | 旧版归档整理时保留的回退副本 |
| `.ordo-storage.json` | 项目素材位置、原始素材登记和归档决策 |
| `OrdoExpectedProjectID` | 打开 Project Setup 时校验目标工程的脚本参数 |
| `ORDO_UI_TEST` 等测试变量 | 既有验证及预览入口 |
| `Ordo.app` 等历史 App 名称 | 发现旧安装，选择可用的归档组件 |
| `导入阅流.py` | Import YueLiu 安装时识别、备份及迁移的旧脚本名 |

Project Setup 的既有媒体池模板保存在用户目录的 `Library/Application Support/13rOKen4nGeL/DaVinciScripts/start_work_template.json`。归档组件位于 `Library/Application Support/com.example.MagicTools.Youth/Archive`。

重命名这些持久化标识需要迁移方案，不能通过全局替换改动。新界面和文档展示使用 ordina。测试中的旧名称用于验证对既有安装的兼容。
