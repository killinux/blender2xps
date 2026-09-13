# Blender2XPS

把 Blender 里的模型（网格 + 骨架 + 权重 + 贴图）导出成 XNALara / XPS 能直接加载的
`.xps` / `.mesh` / `.mesh.ascii`。Blender 3.6 开发，兼容 4.x（带 `blender_manifest.toml`）。

导出核心完全独立，不依赖 XNALaraMesh 或任何其他插件。

## 为什么再写一个

已有方案（XNALaraMesh 及其各种分支、b2xps）在骨骼/权重上有几类共同问题，本插件逐一处理：

| 问题 | 旧插件 | Blender2XPS |
|---|---|---|
| 骨骼位置用 `matrix_local`、网格用 `matrix_world`，骨架有父级/缩放时对不上 | XNALaraMesh | 骨骼与顶点统一用世界矩阵 |
| 权重不归一化；超过 4 个权重直接截断，剩下的和 < 1，XPS 里顶点缩向原点 | XNALaraMesh / b2xps | 按大小截断后重新归一化，可选不限权重数（v3 格式） |
| 没有权重的顶点写成全 0，XPS 里塌到原点 | 两者 | 绑到根骨 / 最近骨骼（可选） |
| 顶点组按顺序全部写入，`mmd_edge_scale` 这类非骨骼顶点组占掉权重槽 | XNALaraMesh | 只认骨骼名的顶点组 |
| 用 `mesh.data` 原始数据，修改器/形态键不生效 | XNALaraMesh | 用评估后的网格（骨架修改器按静止姿态处理） |
| MMD 模型的 足D/腕捩 等辅助骨带着权重，XPS 里转主骨骼网格不动 | 两者 | 可选合并到主骨，子骨重新挂接 |
| 日文骨名 / 非标准骨名，XPS 标准姿势用不了 | 两者 | 识别 MMD、Bip001、Mixamo、VRoid、Rigify 骨名并改成 XPS 标准名 |
| 只认 `XPS Shader` 节点组的贴图，其他材质全是 missing.png | XNALaraMesh | 支持 XPS Shader、mmd_tools 的 MMDShaderDev、Principled BSDF、任意节点组、裸贴图节点 |
| 没有校验 | 两者 | 写完立即回读文件做结构校验，输出报告 |

完整的操作说明（安装、每个选项、按模型来源的推荐设置、报告解读、批量导出、排错）见
[docs/使用指南.md](docs/使用指南.md)。

## 安装

```powershell
.\install.ps1            # 在 %APPDATA%\Blender Foundation\Blender\3.6\scripts\addons 建联接
.\install.ps1 3.6,4.2    # 多个版本
```

然后在 Edit > Preferences > Add-ons 启用 **Blender2XPS (XPS / XNALara Exporter)**。
Blender 正在运行且装了 blender-mcp 时：`python tools/blender_mcp.py enable`。

## 使用

* 侧边栏（N）> **XPS** 页签：选骨架、设输出文件，点 **检查模型** 看会怎么处理，点 **导出...**。
* 或 File > Export > XPS / XNALara。

默认设置就是给 MMD（mmd_tools 导入的 PMX）角色准备的：范围 = 骨架绑定的全部网格，
合并 MMD 辅助骨，骨名映射为 XPS 标准名，每顶点 4 个权重，贴图复制到输出目录，
写出 `.xps` v2.15（XPS 11.x 全部能读）。

导出后同目录会有 `<文件>.report.txt`，列出骨骼改名/合并、每个部件的顶点数、渲染组、贴图和所有警告。

### 材质 → 渲染组的规则

* 贴图来源依次尝试：`XPS Shader` 节点组、mmd_tools 的 `MMDShaderDev`（Base Tex）、Principled BSDF
  （Base Color / Normal / Specular / Emission）、其他节点组的常见输入名、最后是任意图像节点按文件名猜。
* 有法线 → 4/6，有高光 → 40/41，有自发光 → 36/37，只有漫反射 → 5/7（偶数/奇数对应不透明/透明）；
  勾选“无光照”则用 10/21。
* **透明判定（自动模式）**：材质混合模式不是 Opaque，且把漫反射贴图的 alpha 在**这个部件自己的 UV**
  （顶点 + 三角形中心）上采样，超过 8% 的采样点落在透明像素才算透明。这样贴图集空白区透明的身体
  贴图（`*_rgbx_Albedo.png`）不会被误判，头发片、睫毛、蕾丝仍能正确识别。mmd 材质 alpha < 1 直接算透明。
* 完全透明的材质（mmd alpha = 0、Principled Alpha = 0、只有 Transparent BSDF）整块跳过，不导出。
* 没有漫反射贴图时生成一张 4×4 纯色 PNG（材质基础色）。
* 部件名：只有一个网格物体时用材质名，多个物体时用“物体名-材质名”，Blender 的 `.001` 后缀、下划线
  （XPS 的分隔符）都会清理掉。

### 材质烘焙

颜色或透明由节点算出来的材质（颜色渐变驱动的头发、拆通道当 alpha 的睫毛、程序化虹膜、带明显
色调的皮肤等）没有 XPS 能用的贴图。“材质烘焙 = 自动”时，这类材质会用 Cycles 发光烘焙把
Principled 的 Base Color 和 Alpha 烘成一张 RGBA PNG（尺寸取材质里贴图的尺寸，最大 2048），
写到模型旁边；普通“贴图直连 Base Color”的材质不烘，保持原分辨率。固定 alpha < 0.5 的
半透明辅助壳（眼部遮蔽壳、泪线、假反射片）默认跳过。

### 骨骼列表瘦身

游戏骨架常带几百根扭转骨/肌肉修正骨/IK/附件骨。“隐藏辅助骨”给它们加 `unused_` 前缀：
XPS 的骨骼列表里不再显示，但仍参与蒙皮（这是 XNALara 的约定）。“隐藏面部骨”对 MetaHuman
风格的 `FACIAL_*` 骨做同样处理（下颌、眼球、舌头除外）。识别出的 XPS 标准骨永远不会被隐藏。

### 朝向

XPS 里模型应面向观众（Blender 的 -Y）。“自动转正朝向”按脚踝→脚趾的方向判断角色朝向，
整体绕 Z 轴旋转（骨骼和网格一起）；没有识别到腿骨时不做处理。头和身体来自不同骨架、
朝向相反的拼装模型（如 Vindictus 的 Shiningwill_legacy）以身体为准。

### 批量导出

```powershell
blender -b --python tools/batch_export_blends.py -- --out D:\out --scale 0.01 --bake AUTO a.blend b.blend
```

每个 .blend 输出到 `<out>\<名字>\<名字>.xps`，每个文件打印一行 `BLENDER2XPS_BATCH={json}` 汇总；
多开几个 Blender 进程并行即可。厘米单位的模型（UE 导出）用 `--scale 0.01`。

### 可覆盖的规则

* 材质自定义属性 `xps_render_group`（整数）强制渲染组；`xps_alpha`（0/1）强制透明；`xps_specular` 高光值。
* 物体名以 `数字_` 开头（XNALaraMesh 的命名习惯，如 `7_hair`）也当作渲染组覆盖。
* 物体名以 `+` / `-` 开头 → XPS 可选部件（默认显示 / 隐藏）。

### 脚本调用

```python
from blender2xps import export_xps
res = export_xps.export_model(bpy.context, export_xps.Settings(
    filepath=r'D:\out\model.xps', scope='ARMATURE', bone_naming='XPS'))
print(res.summary(), res.warnings)
```

## 工具

* `tools/xpsdump.py model.xps` —— 不用 Blender 检查任何 XPS 文件（格式、骨骼、部件、权重和、索引范围）。
* `tools/xpsdump.py --compare a.xps b.mesh.ascii` —— 数值比较两个文件。
* `tools/blender_mcp.py exec script.py` —— 通过 blender-mcp 在正在运行的 Blender 里执行脚本。
* `tests/test_export_headless.py` —— 端到端测试：
  `blender -b --python tests/test_export_headless.py -- --out <dir>`
  （合成骨架 + 镜像多材质网格 + 混乱权重，四种格式导出、回读校验，再用 XNALaraMesh 导回比对）。

## 文件格式速查

| 格式 | 文件头 | 权重 | 切线 | 谁能读 |
|---|---|---|---|---|
| XPS2 (`.xps`/`.mesh` v2.15) | 有 | 固定 4 | 无 | XPS 11.x 全部、XNALaraMesh |
| XPS3 (v3.15) | 有 | 每顶点可变 | 无 | XPS 11.8.9+、XNALaraMesh |
| MESH（经典） | 无 | 固定 4 | 每 UV 层 4 float | XNALara、所有 XPS |
| ASCII (`.mesh.ascii`) | 无 | 可变 | 无 | 全部 |

坐标：Blender (x, y, z) → XPS (x, z, -y)，三角面顶点序 (0, 2, 1)，UV v 翻转。
