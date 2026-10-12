# 自检体系说明书（`tools/qc/`）

> 改判据前先读这一页。它回答三件事：**现在有哪些判据在册**、**各自守什么**、
> **哪些守卫被隔离了以及为什么**。

## 一、怎么跑

```bash
python -m pip install -r requirements.txt   # 自检需要 mss / tkinter
python tools/qc/run_all_fitness.py          # 唯一入口：跑一次，给一张表，返回一个退出码
```

**退出码是三档的**（「架构漂移」和「检查坏了」是两件事，必须分开报）：

| 码 | 含义 | 该做什么 |
|---|---|---|
| `0` | 全绿（或「有跳过」） | 没事。若打印「有跳过」，说明有项**未验证**，不是通过 |
| `1` | **架构漂移** —— 仓库违反了一条判据 | 判据本身是好的，按它给的修法改仓库 |
| `2` | **判据失效** —— 某个 `smoke_*` 自己坏了 | ⚠️ 此时 fitness 报的「通过」**不可信**。先修判据 |
| `3` | **SKIP** —— 环境不满足前置条件（如裸解释器缺 `mss`） | 装依赖后重跑。**这不是通过，也不是失效** |

> 为什么 `2` 比 `1` 严重：`smoke_*` 是 `fitness_*` 的**体检**。体检坏了，
> 「体检结果正常」这句话就没有意义了（假绿）。

## 二、在册判据

### fitness（判据本体）

| 文件 | 守什么 |
|---|---|
| `fitness_hardcoded_root.py` | **硬编码根路径不得增长**。冻结的是**文件清单**（`.fitness_baseline.json`），只管增量 |
| `fitness_personal_paths.py` | **不许带出本机家目录路径**（`C:\Users\<账户>\…`）。与上一条是两个病：上条问"搬家会不会断"，这条问"带出去会不会暴露账户名 / 让别人照抄一个不存在的路径" |

> ⚠️ 冻结的是「清单」不是「阈值」。**换过尺子（改口径/改扫描范围）之后必须重新冻结，
> 并且必须在冻结点的 `note` 里写明"这次是换了尺子"** —— 否则下次没人分得清
> 「数字变小了」是因为仓库干净了，还是因为尺子松了。

### smoke（判据的体检）—— 在册 6 个

| 文件 | 守什么 | 项数 |
|---|---|---|
| `smoke_fitness_hardcoded.py` | `fitness_hardcoded_root` 的正/负对照（计数看得见、增量只在涨时报） | — |
| `smoke_dpi_awareness.py` | v0.34.1「切检测源时主窗口变小」的复发守卫（`mss` 把进程 DPI 觉察度从 UNAWARE 翻成 PER_MONITOR） | A/B/C/D 四段 |
| `smoke_doc_refs.py` | `doc_refs`：`CHANGELOG.md` 里「文档同步：」声明的锚点必须真实存在 | 31 |
| `smoke_diagram_visual_gate.py` | `diagram_visual_gate`：图的**可读性门**（交付出来 ≠ 能看清）+ **自指陷阱**（扫描工具扫到自己的输出） | 59 |
| `smoke_dataset_qc.py` | `core/dataset_qc.py`：**标注内容**体检既灵敏又不误报（坏标注必报 / **空标签不许误报** / 分级正确 / 读不到 nc 时必须说"未执行"） | 35 |
| `smoke_personal_paths.py` | `fitness_personal_paths`：四种家目录形态必报 / **六种占位写法一个都不许报** / 豁免台账完整性 | 14 |

**不注册进总检但仍在册的**（各自守活代码，需要时单独跑）：

| 文件 | 守什么 | 为什么不进总检 |
|---|---|---|
| `smoke_btn_click.py` | `gui.components.btn` 造出的按钮，点了必须响 | 需要 GUI 上下文 |
| `smoke_segmented_fill.py` | 分段控件激活段被 hover 补间涂黑（v0.33.x 用户报的 bug） | 同上 |
| `smoke_doctor_probe.py` | `doctor_probe_files.py` 的正/负对照 | 独立工具 |
| `smoke_torch_live.py` | `torch_live_check.py` 的正/负对照 | 依赖 torch |
| `smoke_venv_torch_abi.py` | `venv_torch_abi_check.py` 的正/负对照 | 依赖 venv |

## 三、被隔离的守卫（32 个）—— 为什么

**结论：不是删掉，是隔离。** 实体在 `.trash/2026-10-10_smokes_h3_legacy/`，
逐条分类与原因见 **`tools/qc/SMOKES_H3_LEGACY.md`**（被跟踪；含 32 个文件的 sha256 清单）。

> ⚠️ 为什么不放 `tools/qc/_scratch/`：那里被 `.gitignore` 忽略，而 `.gitignore` 的
> 注释明确写着「正式自检资产（`fitness_`/`smoke_`/`verify_`）**不在此列**」。
> 把 `smoke_*` 放进去与既有约定冲突。改放 `.trash/`（项目既有的隔离区约定），
> 并把**哈希清单落到被跟踪的 `.md` 里** —— 否则那些文件在版本历史里永远隐形。

**根因一条**：2026-10-01 **四线剥离**把「H3 AI 视频产线 / 机械仿真 / 板卡部署」
三条线整体搬去了同级的 `18_legacy_h3_sim/from_01_yolo_lab/`（在 `AI_projects` 根下）。
那些 `smoke_*` 的**被判对象不在本仓了**，于是它们**恒 FAIL**
（0.3 秒内 `FileNotFoundError` / `ModuleNotFoundError`）。

| 组 | 个数 | 典型症状 |
|---|---|---|
| H3 doctor 组（`studio_doctor` 系） | 11 | `FileNotFoundError: tools/h3/studio_doctor.py` |
| H3 标注链组（`auto_label_fall` 系） | 11 | `ModuleNotFoundError: No module named 'auto_label_fall'` |
| H3 其他（配乐 / 关键点 / 交付 / 联动） | 6 | 被判对象是 `tools/h3/*` 或 H3 页面 |
| 被判对象已先被移走的 | 3 | `smoke_doc_coverage` / `smoke_log_reading` / `smoke_instrument_selfselect` |
| `smoke_path_guard` | 1 | 见下（**同名不同物**，易混） |

### ⚠️ 一个必须分清的同名陷阱

本仓有**两个** `path_guard.py`：

- `tools/qc/path_guard.py`（**已不存在**）—— 守 H3「批量搬根」事故 ⇒ `smoke_path_guard` 归隔离
- `src/infrastructure/path_guard.py`（**活代码**）—— 迁移自动适配，被
  `src/gui/pages/environment_page.py` 与 `src/infrastructure/diagnostics.py` 引用

⇒ **活代码那个 `path_guard` 目前没有守卫**，是真实覆盖缺口（见下）。

## 四、已知覆盖缺口（如实记录，不粉饰）

1. **`src/infrastructure/path_guard.py` 无守卫** —— 它管的是「项目目录被搬走后自动适配」，
   而本仓 2026-09-18 真的搬过一次（从盘根 `<X>:\yolo_lab` 迁到当前 `AI_projects/01_yolo_lab`），
   那次暴露过它的静默失效（候选位置写死成 `<X>:\yolo_lab`）。值得补一条守卫。
2. **无 GUI 目检** —— 本机内存受限（WorkBuddy 常驻 ~6GB，可用仅 1.7–2GB），
   无法启动完整 GUI 做视觉验收。`smoke_btn_click` / `smoke_segmented_fill`
   属于契约级断言，**替代不了目检**。
3. **`archify/` 里的探针残留** —— 2026-10-10 实测 66 个 `.archify-*` 条目
   （52 个 0 字节文件 + 14 个 `.archify-delivery-*/` 目录），是历史探针残留。
   已被 `.gitignore` 忽略、不进版本库、不影响交付，但目录看着脏。
   ⚠️ **不能连带删掉**：同目录下的 `workbench.*.json` 与 4 张 `.html` 是出图的**输入与产物**。
   要清就得先留清单（相对路径 + 字节数）再动手。

## 五、改判据的纪律（照做）

1. **新判据必须带正 + 负 + 未改动三类对照**，只测「该报的报了」证明不了
   「不该报的没报」—— 一个恒返回 FAIL 的判据也能过。
2. **必须真跑一次反向注入**：改坏 → 必红 → 还原 → 复绿。
   只写「已加对照」不算，要看到红。
3. **别在代码里写死会变的数字** —— 条数、日期、文件名清单，写下的那一刻就开始烂。
   能从真源派生的就派生（例：`smoke_diagram_visual_gate` 的图数来自
   `sync_everything.py` 的 `DIAGRAMS`）。
4. **判据的输出格式也是接口** —— `run_all_fitness.py` 靠行首的
   `PASS ` / `FAIL ` / `SKIP ` 取摘要行。带前导空格就取不到，
   表现是「判据在跑，摘要列却空白」。
5. **夹具不要选会漂移的外部状态** —— 例：某条对照曾依赖
   `archify/` 根目录里「恰好残留着」sidecar，而 sidecar 实际生成在子目录里，
   搬运后它静默变成假失败。**夹具要自造**，确定性才可控。
6. **豁免不等于放水** —— 需要豁免时，写成**指名道姓的白名单**并注明原因
   （例：`doc_refs.EXEMPT_MISSING_DOCS`，每条写明「搬去哪了」），
   而且**豁免的作用域必须小于判据的作用域**，否则会把体检的负对照一起吃掉。
