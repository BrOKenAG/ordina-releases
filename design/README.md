# ordina 图标

标志以 ordina 的 o 与 d 为基础，使用暖银白几何与石墨灰底。菜单栏复用相同几何。

几何源为 `Ordina/Design/OrdinaMark.swift`，母版为 `design/ordina-monogram.png`。从工程根目录运行以下命令可重新生成全部 macOS 图标尺寸：

```sh
swiftc Ordina/Design/OrdinaMark.swift scripts/RenderOrdinaIcon.swift -o /tmp/render-ordina-icon
/tmp/render-ordina-icon "$PWD"
```

App 资源位于 `Ordina/Assets.xcassets/AppIcon.appiconset`，首页字标位于 `WordMark.imageset`。
