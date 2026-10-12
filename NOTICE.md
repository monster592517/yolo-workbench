# 第三方组件与许可声明（NOTICE）

本项目（YOLO工作台）整体以 **GNU AGPL-3.0** 发布，全文见同目录 `LICENSE`。

下面列出**随本仓库分发**的第三方组件及其许可。所有第三方组件均保留其原始许可证。

---

## 一、运行时依赖（通过 pip 安装，**不随本仓库分发**）

这些库由使用者按 `requirements.txt` 自行安装，其源码不在本仓库内。
此处列出仅为说明**许可兼容性**。

| 组件 | 许可 | 说明 |
|---|---|---|
| **ultralytics** | **AGPL-3.0** | ⚠️ **本项目整体采用 AGPL-3.0 的直接原因。** 训练/推理/导出均经其 API 完成。 |
| torch / torchvision | BSD-3-Clause | |
| opencv-python | Apache-2.0 | |
| numpy | BSD-3-Clause | |
| Pillow | MIT-CMU | |
| PyYAML | MIT | |
| mss | MIT | |
| psutil | BSD-3-Clause | |
| zstandard | BSD-3-Clause | |
| tkinterdnd2 | MIT | |
| x-anylabeling-cvhub | MIT / 见上游 | 外部标注工具，可选；本工作台内置编辑器不依赖它 |

> **给商用者的提示**：`ultralytics` 的 AGPL-3.0 带有网络条款（§13）。
> 若你把本工作台（或其修改版）作为**网络服务**提供给他人使用，须按 AGPL 向用户提供完整源码。
> 若你需要闭源商用，应向 Ultralytics 申请其**企业授权**（Enterprise License），
> 或自行替换推理后端。这是 Ultralytics 的许可要求，与本项目无关。

---

## 二、随仓库分发的第三方工具

### 2.1 Archify（架构图渲染器）

- 位置：`tools/archify/`
- 上游：**https://github.com/tt-a1i/archify**（本仓内是 **v2.16.0-dev.0** 的解压副本）
- 许可：**MIT License**
- 版权：Copyright (c) 2026 tt-a1i (Archify) / Copyright (c) 2025 Cocoon AI (original "architecture-diagram-generator")
- 全文：`tools/archify/LICENSE`
- 用途：本项目用它把 `archify/*.json`（typed JSON IR）确定性编译成
  `archify/YOLO工作台_*.html` 四张架构/流程/时序/状态图。
- 说明：**只保留运行所必需的包本体**（`tools/archify/archify/`，**54 个**文件）
  与归属文档（`LICENSE` / `README*` / `CHANGELOG` / `DESIGN` / `PRODUCT` / `CONTRIBUTING` / `ROADMAP`）。
  以下**不随本仓库分发**（上游有、本仓无）：
  `docs/` · `experiments/` · `benchmarks/` · `archify.zip` ·
  以及 **`archify/test/`（95 个）与 `archify/examples/`（19 个）**。
  > 2026-10-11 裁剪后：`tools/archify/` 被跟踪文件 **180 → 66**。
  > 被裁的 114 个文件隔离在 `.trash/2026-10-11_archify_trim/`
  > （含 `_MANIFEST.json`，114 条：相对路径 + 字节 + mtime）。
  > **恢复**：把该目录整份搬回 `tools/archify/` 即可（同盘 rename，一个动作）。
- 裁剪安全性依据（2026-10-11 实测，三条）：
  ① 本项目只经 `scripts/sync_everything.py` 调 `bin/archify.mjs` 的 `validate` / `deliver`；
  ② 全包 grep `from ...test/` / `from ...examples/` **零引用**；
  ③ `examples/` 只被 archify 自己的 `doctor` 自检与 `demo` 子命令用到，**主渲染链路不碰它**。
  ⚠️ **未做动态验证**：本机 `spawnSync <node>` 报 `EBUSY`（node v22 / v24 皆然），
  archify 的 renderer 子进程在本会话起不来 ⇒ **拿不到"裁剪前后产物 sha256 一致"这条判据**。
  这是**环境态**（node 连 spawn 自己都 EBUSY），不是仓库缺陷。

> MIT 与 AGPL-3.0 兼容：MIT 代码可被 AGPL 项目包含，且 Archify 自身仍按 MIT 授权。

### 2.2 SAM2（Segment Anything 2）

- 位置：`tools/sam2/`（**未入库**，见 `.gitignore`；由使用者按上游说明自行获取）
- 许可：Apache-2.0（Meta Platforms）
- 用途：标注编辑器「本地重标」链路的掩码精修环节。

---

## 三、模型权重

本仓库**不分发**模型权重（`.pt` / `.onnx` / `.pth` / `.safetensors`，见 `.gitignore`）。
使用者从上游获取后放入项目根目录。

| 权重 | 来源 | 许可 |
|---|---|---|
| `yolo11n.pt` / `yolo11s.pt` / `yolo11n-pose.pt` / `yolo11s-seg.pt` / `yolov8n*.pt` / `yolo26n.pt` | Ultralytics | **AGPL-3.0** |

> ⚠️ 权重与代码同属 AGPL-3.0 范围。**训练/微调后产出的新权重**同样受 AGPL 约束
> —— 这是 Ultralytics 许可条款的常见约束点，商用前请自行确认。

---

## 四、数据集

本仓库**不分发**数据集（`data/` 在 `.gitignore` 内）。
`data/datasets/` 为空骨架，使用者自备。

---

*本文件的维护约定：新增任何随仓库分发的第三方组件时，必须在此登记其许可与版权，
并在仓库里保留其原始 LICENSE 文件。*
