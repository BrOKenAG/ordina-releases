# ordina

[中文](README.md) · [English](README.en.md)

为 DaVinci Resolve 剪辑师简化项目搭建和素材整理的 macOS 工具。创建项目文件夹、关联 Resolve 工程，并在剪辑过程中自动整理符合条件的外部素材。

**让项目从一开始就井然有序。**

[下载安装包](https://github.com/BrOKenAG/ordina-releases/releases/latest) · [提交问题](https://github.com/BrOKenAG/ordina-releases/issues)

## 适合什么场景

- 每次新建项目都要重复建立相同的文件夹和 Resolve 工程。
- 剪辑时使用了下载文件夹、桌面或外置磁盘里的素材，希望项目文件更容易管理。
- 希望在 Resolve 中集中完成工程初始化和查看素材归档状态。

## 系统要求

- Apple 芯片 Mac，macOS 26 或更新版本。
- 创建文件夹可以独立使用。
- Resolve 工程创建及插件功能需要 DaVinci Resolve Studio，并将「偏好设置 → 系统 → 常规 → 外部脚本使用」设为「本地」。
- Resolve 连接组件使用系统 `/usr/bin/python3`。若系统提示缺少开发工具，先安装 Xcode 命令行工具。
- Minutes Roughcut 额外需要 Google Chrome；从浏览器直接发送时，Resolve 需已打开工程。

## 安装与使用

1. 下载 DMG，将 `ordina.app` 拖入「应用程序」。
2. 首次打开时选择默认保存位置、已有 Resolve 项目库，并按需安装插件。也可以稍后到设置中完成。
3. 在首页输入项目名称，点击「+新建」，选择保存位置和文件夹模板。
4. 按需开启「同步创建 Resolve 工程」，登记原始素材目录，或选择「稍后设置」。

同步创建前，请在 Resolve 中切换到所选项目库，并保存、关闭当前正式工程。创建完成后，ordina 会尝试打开新工程的 Project Setup 窗口。

当前安装包采用临时签名，尚未经过 Apple 公证。首次打开可能需要到「系统设置 → 隐私与安全性」确认打开。

## 功能

| 功能 | 用途 |
| --- | --- |
| 项目目录 | 自定义文件夹模板、记住保存位置，支持本地磁盘、外置盘和已挂载 NAS |
| Resolve 工程 | 支持已登记的本地及 PostgreSQL 项目库，保存工程关联，按关联打开工程 |
| 全部项目 | 查看项目目录，打开文件夹或已关联的 Resolve 工程 |
| 插件管理 | 查看已安装脚本，安装内置插件，拖入或选择 `.py` 脚本安装到 Resolve，检查并更新在线插件 |
| 素材归档 | 登记原始素材位置，扫描临时素材，复制、校验后更新 Resolve 引用 |
| 软件更新 | 在设置或应用菜单检查更新，可选择自动检查，更新包与清单均校验签名 |

### Resolve 插件

| 插件 | 用途 |
| --- | --- |
| Project Setup | 设置分辨率与帧率、建立媒体池模板及锁帧时间线、查看归档状态 |
| Import YueLiu | 导入阅流导出的审片意见，筛选意见并跟踪处理状态 |
| Multicam Tools | 角度命名与多机位合板操作 |
| Minutes Roughcut | 将飞书妙记的划稿保留区间发送到 Resolve，生成粗剪时间线 |

在「设置 → 插件管理」安装或更新。安装后从 Resolve 的「工作区 → 脚本 → Utility」打开；菜单未刷新时重启 Resolve。Import YueLiu 需要在 Resolve 使用的 Python 环境中安装 `openpyxl`。Minutes Roughcut 还包含浏览器扩展，安装方式见下节。

### Minutes Roughcut

Minutes Roughcut 把飞书妙记页面上的划稿保留区间发送到 DaVinci Resolve，按区间生成粗剪时间线。它由 Resolve 脚本、Chrome 扩展和本地通信组件三部分组成。

**安装**：在「设置 → 插件管理」安装 Minutes Roughcut，然后点「一键准备浏览器扩展」。ordina 会复制扩展文件夹路径、在 Chrome 中打开扩展程序页面，并在访达中选中该文件夹。接着在 Chrome 里开启「开发者模式」，选择「加载已解压的扩展程序」，按 ⌘⇧G 粘贴已复制的路径。Chrome 不允许第三方应用静默安装未上架的扩展，这两步无法省略。

**使用**：打开飞书妙记页面，在扩展中点「发送到达芬奇」，把保留区间直接发送给已打开的 Resolve 项目；也可以导出 JSON，再从 Resolve 的「工作区 → 脚本 → Minutes Roughcut」导入。需要 macOS、Chrome 和 DaVinci Resolve Studio；浏览器直连时 Resolve 需已打开工程。

**更新**：Minutes Roughcut 通过独立的签名发布仓库分发，与 ordina 软件更新分开。插件管理会检查在线版本并直接更新，不需要重新安装 ordina。

> 该插件为测试版。生成粗剪会重建时间线，可能无法保留部分特效、调色及链接，建议先在工程副本中试用并核对素材来源与时间起点。飞书页面或内部接口变化可能影响划稿提取。安装不会上传素材；被替换的旧文件会备份到 `~/Library/Application Support/Minutes Roughcut/backups`。

### 素材归档

在 Project Setup 中打开「归档状态」，可以手动扫描、登记素材位置、启用自动归档和调整扫描间隔。后台扫描默认间隔为 5 秒。

登记为原始素材的目录保留引用。自动归档在时间线停稳等条件满足后处理符合策略的临时素材，复制并校验成功后才重链接；源文件保留，同名不同内容的文件使用独立目录。序列帧、多文件素材包等无法安全处理的情况需要人工确认。

### 项目命名

以 2026 年 11 月 20 日创建「示例项目」为例：

```text
磁盘目录    20261120_示例项目/
Resolve     2026/Q4/1120_示例项目/示例项目
```

也可以输入 `20261120_示例项目`，明确指定日期。Resolve 中的项目分组使用 `MMDD_项目名`，该层不重复年份。

## 从源码构建

需要完整 Xcode 26 或更新版本、命令行工具和 Python 3。Swift Package Manager 会获取固定版本的 Sparkle 2.10.1。

```sh
git clone https://github.com/BrOKenAG/ordina-releases.git ordina
cd ordina
python3 scripts/build_app.py --output dist/ordina.app
```

也可以用 Xcode 打开 `Ordina.xcodeproj`，选择共享 Scheme `Ordina`。构建脚本默认输出到桌面；指定 `--output` 可以改变位置。

生成 DMG 还需要 Python 3.10 或更新版本及打包依赖：

```sh
python3 -m pip install --target .build/dmg-libs -r requirements-build.txt
python3 scripts/build_distribution.py --output dist --release-label build.30
```

运行验证：

```sh
python3 scripts/verify.py
python3 Tests/verify_app.py dist/ordina.app
```

测试使用临时目录和替代 Resolve 对象。App 构建与签名验证单独执行。

## 源码结构

```text
Ordina.xcodeproj/       Xcode 工程、共享 Scheme 和依赖锁定
Ordina/                SwiftUI 界面、Resolve 连接与归档服务
  Design/              主题、控件和图标几何
Integrations/           Project Setup 及可选 Resolve 插件
config/Info.plist       更新地址、验证公钥与更新偏好默认值
scripts/               构建、DMG 打包、验证和更新清单生成
Tests/                 Python 回归测试与 Swift 验证
design/                当前图标母版及生成说明
installer/             DMG 安装背景
docs/                  发布流程、数据兼容与使用说明
appcast.xml            已发布版本的签名更新清单
```

发布更新的方法见 [发布说明](docs/releases.md)。早期标识、数据文件和脚本兼容关系见 [数据兼容说明](docs/compatibility.md)。使用与数据处理说明见 [使用说明](docs/usage-notice.md)，第三方授权见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
