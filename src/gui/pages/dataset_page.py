# -*- coding: utf-8 -*-
"""「数据集与标注」页 — 训练数据集(拖拽生成) / 标注工具 / AI预标注 / 标注进度

2026-09-11 由 training_page.py 拆出 (v0.26.0)。
拆分前本页内容挂在 TrainingPage 的 ①数据准备 / ②标注预标注 两个 Tab 下, 与 ③训练
挤在同一页: 左侧容器被临时拉宽到 1240 承载单张卡, 结果卡内大片空白。
拆出后本页拥有独立的两列布局 (数据 420 定宽 + 标注操作弹性)。

## 跨页契约

当前数据集不再是本页私有状态, 而是 `infrastructure.dataset_state` 里的共享单例:
  - 本页写入 (用户选择) -> 训练页订阅 EV_CHANGED 自动刷新;
  - 后台扫描 (`ensure_scan`) 只跑一次, 两页共用同一份清单;
  - 训练完成时训练页 emit 模型变更, 本页订阅后刷新 .pt 下拉。

## 依赖

本模块**不** import torch/ultralytics —— 那些由 core.core.trainer 惰性拉取。
"""
from __future__ import annotations
from gui import poll_kit as _pk
import tkinter as tk, shutil, threading, sys, subprocess, os, time
import queue as _queue
from tkinter import ttk, messagebox, filedialog, simpledialog
from pathlib import Path
from infrastructure.config import get_config, get_theme
_tc = get_theme().colors
from infrastructure import dataset_state as ds_state
from core.trainer import (build_dataset_from_images, read_classes_from_folder)
from gui.components import pip_install_stream, step_card, autowrap
from gui import thumb_pool
from infrastructure.audit import audit

# 每个 drain tick 最多贴几张缩略图 (一次贴 16 张会造成 ~289ms 的单帧尖峰)
PLACE_PER_TICK = 4

_cfg = get_config()


def _persist_last_device(name: str) -> None:
    """后台把"上次用的设备"落盘 (2026-09-11 从 GUI 回调里搬出来)。

    坑: 原来这一行 write_text 直接在 `self.after(0, _apply)` 里跑, 卡顿看门狗实测
    它独占主线程 **328.7 ms** —— 为一个几十字节的小文件。慢的不是内容, 是磁盘本身
    (本机页面文件吃紧时单次写盘可到百毫秒级, 项目根还挂在外接 USB SSD 上)。
    它只是"记住上次选的设备"的**缓存**, 丢了下次自动重探, 完全不值得堵界面。

    同时加了"内容没变就不写": 设备列表每次刷新都调到这里, 而绝大多数时候值不变。
    """
    def _run():
        try:
            fp = _cfg.output_dir.parent / "last_device.txt"
            try:
                if fp.exists() and fp.read_text(
                        encoding="utf-8", errors="ignore").strip() == name:
                    return
            except Exception:
                pass
            fp.write_text(name, encoding="utf-8")
        except Exception:
            pass

    try:
        threading.Thread(target=_run, daemon=True, name="last-device").start()
    except Exception:
        pass


def run_auto_annotate(*a, **kw):
    """惰性转发到 core.annotator。

    core.annotator 顶层 import 了 cv2 + numpy, 冷导入 1~3 秒。放在模块顶层会让
    "打开数据集与标注页"平白背这一笔 —— 所以包一层, 首次真正标注时才付。
    """
    from core.annotator import run_auto_annotate as _f
    return _f(*a, **kw)

try:
    from tkinterdnd2 import DND_FILES
    _HAS_DND = True
except Exception:
    DND_FILES = None
    _HAS_DND = False


class DatasetPage(tk.Frame):
    # 标注工具候选 (可执行名候选, 包名) —— 由 _check_tool 消费。
    # 2026-09-11 拆分: 原来它是 training_page 的类属性, 只搬方法不搬属性会在
    # _check_tool 里 AttributeError (静态核对 runs/_split_audit.py 抓到的)。
    _TOOLS = {"anylabeling": (("xanylabeling", "x-anylabeling", "anylabeling"), "anylabeling")}

    def __init__(self, parent, controller=None):
        super().__init__(parent, bg=_tc.bg)
        self.controller = controller
        C = _tc.card_bg; T = _tc.text; S = _tc.text_secondary; P = _tc.primary

        from gui.components import (topbar, sub_card, btn, scrollable, autowrap)
        topbar(self, "🗂", "数据集与标注",
               "训练数据集 | 标注工具 | AI 预标注 | 标注进度",
               actions=[("📚 模型库管理", self._open_model_manager, "primary")])
        tk.Frame(self, height=1, bg=_tc.border).pack(fill="x", padx=24, pady=(0, 8))

        # ══ 两列布局: 左 = 数据集(定宽 420), 右 = 标注操作 + 日志(弹性) ══
        # 旧版把三张卡塞进一个宽度会变成 1240 的滚动列, 卡内 70% 是空白。
        body = tk.Frame(self, bg=_tc.bg)
        body.pack(fill="both", expand=True, padx=24, pady=(0, 12))
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=0, minsize=420)
        body.columnconfigure(1, weight=1, minsize=520)

        left_outer, left = scrollable(body, bg=_tc.bg)
        left_outer.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left_outer.grid_propagate(False)
        left_outer.config(width=420)
        self._ds_col = left_outer

        right = tk.Frame(body, bg=_tc.bg)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self._anno_col = right

        # ══════ 左列 ① 训练数据集 (拖拽生成训练集 · 标注目录/子目录) ══════
        c2, c2b = step_card(left, 1, "训练数据集", "拖拽生成训练集 · 标注目录/子目录",
                            stretch=True)
        c2.pack(fill="both", expand=True)
        self._ds_map = ds_state.get_map()
        self._tool_opened_dir = None   # 最近一次启动标注工具打开的目录 (切换跟随用)
        self._tool_proc = None         # 最近一次启动的标注工具进程 (看护用)
        self._splits = {}   # 标注目录显示名 -> DatasetSplit / "ALL"
        self._splits_ds = None  # _splits 当前对应的数据集名 (防御性校验用)
        self._last_ds_audit = None  # 数据集切换审计去重
        self._ds_var = tk.StringVar(master=self, value=ds_state.get_name())
        self._ds_var.trace_add("write", self._on_ds_var_write)
        self._ds_cb = ttk.Combobox(c2b, textvariable=self._ds_var, values=[],
                                   state="readonly", width=26, font=("Microsoft YaHei UI", 10))
        self._ds_cb.pack(fill="x", padx=12, pady=(8, 0))
        self._ds_cb.bind("<<ComboboxSelected>>", self._on_ds_change)
        self._ds_info = tk.Label(c2b, text="正在扫描数据集...", font=("Microsoft YaHei UI", 9), bg=C, fg=S)
        # fill="x" + autowrap: 空状态提示是多行长文本, 固定 wraplength 会因为列宽变化
        # 而"渲染比请求多一行" —— 让 wraplength 跟着实际宽度走 (见 避坑清单 #69)
        self._ds_info.pack(anchor="w", fill="x", padx=12, pady=(4, 8))
        autowrap(self._ds_info, min_px=200)
        # 标注目录: 自动检测数据集内 训练集/验证集/测试集/原始数据/对照测试 子目录
        split_row = tk.Frame(c2b, bg=C); split_row.pack(fill="x", padx=12, pady=(0, 2))
        tk.Label(split_row, text="标注目录", font=("Microsoft YaHei UI", 8), bg=C, fg=S).pack(side="left")
        self._split_var = tk.StringVar(master=self, value="")
        self._split_cb = ttk.Combobox(split_row, textvariable=self._split_var, values=[],
                                      state="readonly", width=21, font=("Microsoft YaHei UI", 9))
        self._split_cb.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self._split_cb.bind("<<ComboboxSelected>>", self._on_split_change)
        self._split_info = tk.Label(c2b, text="", font=("Microsoft YaHei UI", 8), bg=C, fg=S,
                                    justify="left")
        self._split_info.pack(anchor="w", fill="x", padx=12, pady=(2, 4))
        autowrap(self._split_info, min_px=120)
        # 子目录: 选择 YOLO 训练集后可打开该目录下的子文件夹 (如 train/images/scene)
        sub_row = tk.Frame(c2b, bg=C); sub_row.pack(fill="x", padx=12, pady=(0, 2))
        tk.Label(sub_row, text="子目录", font=("Microsoft YaHei UI", 8), bg=C, fg=S).pack(side="left")
        self._sub_var = tk.StringVar(master=self, value="")
        self._sub_cb = ttk.Combobox(sub_row, textvariable=self._sub_var, values=[],
                                    state="readonly", width=21, font=("Microsoft YaHei UI", 9))
        self._sub_cb.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self._sub_cb.bind("<<ComboboxSelected>>", self._on_sub_change)
        self._subs = {}   # 子目录显示名 -> None(自动) / Path / "CUSTOM"
        self._preconvert = False   # 后台预转换防重入
        self._q = _queue.Queue()

        # 样本预览 (最近样本缩略图, 同时把卡片下半部分填实)
        pv = tk.Frame(c2b, bg=C)
        pv.pack(fill="x", padx=12, pady=(4, 8))
        tk.Label(pv, text="样本预览（双击缩略图开始标注）", font=("Microsoft YaHei UI", 8, "bold"), bg=C, fg=S).pack(anchor="w")
        ph = tk.Frame(c2b, bg=C, height=108)
        ph.pack(fill="x", padx=12, pady=(0, 8))
        ph.pack_propagate(False)
        self._ds_prev_host = tk.Frame(ph, bg=C)
        self._ds_prev_host.pack(fill="both", expand=True)
        self._ds_prev_imgs = []
        # 拖拽生成训练集提示区
        drop = tk.Frame(c2b, bg=_tc.input_bg, highlightbackground=_tc.glow,
                        highlightthickness=1, cursor="hand2")
        drop.pack(fill="x", padx=12, pady=(8, 12))
        self._drop_lbl = tk.Label(drop, text="⬇ 拖拽图片文件夹到此处\n自动生成 YOLO 训练集\n(格式同跌倒数据集: train/valid/test)",
                                  font=("Microsoft YaHei UI", 9), bg=_tc.input_bg, fg=_tc.text_secondary,
                                  justify="center")
        self._drop_lbl.pack(fill="x", padx=8, pady=8)
        if _HAS_DND:
            self.drop_target_register(DND_FILES)
            self.dnd_bind("<<Drop>>", self._on_drop)
            drop.drop_target_register(DND_FILES)
            drop.dnd_bind("<<Drop>>", self._on_drop)
            self._drop_lbl.drop_target_register(DND_FILES)
            self._drop_lbl.dnd_bind("<<Drop>>", self._on_drop)
        else:
            self._drop_lbl.config(text="拖拽需要 tkinterdnd2 (pip install tkinterdnd2)")

        # ══════════════════════════════════════════════════════════════════
        # ★★ 2026-10-03 按功能重排右列 —— 原实现是 **纵向一路堆 5 张卡**。
        #   实测 (1440x900, 右列 701x765, 内容请求 1089px):
        #     标注工具 205 + AI预标注 310 + 进度 107 + 日志 443(req) = 1065 > 765
        #   ⇒ ④⑤ 被挤爆: 日志卡**只拿到 69px**(它请求 443px), 用户几乎看不到输出。
        #   而且左列 412x765 里只用了 467px ⇒ **左边空 300px、右边挤掉 374px**。
        #   重排为「**窄条目并排 / 宽面板独占**」:
        #     ├ ② 标注工具 (205) ┬ ③ AI 预标注 (310) ┤  ← 并排, 高由高者定 = 310
        #     ├ ④ 标注进度 (107, 通栏)                  ┤
        #     └ ⑤ 标注输出日志 (通栏, expand 吃满剩余)   ┘
        #   并排后省下 ~205px 全给日志, 日志从 69px → 300px+。
        #   ⚠ 用法严格照 Tk 规则: 并排用 `grid` 起一个容器, **同一个父容器不许混用
        #     pack/grid** —— `right` 其余子件仍是 pack, 所以并排容器本身必须是
        #     `tk.Frame(right)` 再用 pack 放进去, 里面才用 grid。
        # ══════════════════════════════════════════════════════════════════
        mid = tk.Frame(right, bg=_tc.bg)
        mid.pack(fill="x", pady=(0, 8))
        mid.columnconfigure(0, weight=1, uniform="annoc")
        mid.columnconfigure(1, weight=1, uniform="annoc")

        # ══════ 右列 ② 标注工具 (并排左) ══════
        c1, c1b = step_card(mid, 2, "标注工具", "X-AnyLabeling · 旋转框/关键点/AI辅助",
                            stretch=True)
        c1.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self._tool_var = tk.StringVar(master=self, value="anylabeling")
        # ⚠ 2026-10-03: 文案从「X-AnyLabeling · 旋转框/关键点/AI辅助」**缩短**为
        #   「X-AnyLabeling」—— 副标题已经写着"旋转框/关键点/AI辅助"了。
        #   原串在并排后只分到 ~346px 的列里, Radiobutton 的**请求宽 624px**
        #   把整个 mid 行顶到 772px ⇒ 页面 req 宽 1248 > 可用 1177 ⇒ **横向溢出**。
        #   缩到 1 个词后请求宽 ~130px, 列内富余。
        tk.Radiobutton(c1b, text="X-AnyLabeling", variable=self._tool_var,
                       value="anylabeling", font=("Microsoft YaHei UI", 9), bg=C, fg=T, selectcolor=C,
                       activebackground=C, command=self._check_tool).pack(anchor="w", padx=12, pady=(8, 2))
        # ⚠ 安装/启动两个按钮原来一个在 st_row(side=right)、一个在 c1b(整宽),
        #   并排后列窄, `btn` 的 padx=14 撑不住两个并排 ⇒ 改成**竖排**各占一行,
        #   请求宽由最长的"安装标注工具"决定 (~130px), 稳落在 346px 内。
        #   ⚠ `expand=True` 让这块**吃掉**卡片被 mid 行拉高后的余量 ——
        #   否则本卡只有 ~200px 内容、却被 AI预标注(310px) 拉到同高,
        #   下半截是一片空壳 (截图实测: 内容到 y≈250, 卡到 y≈315)。
        st_row = tk.Frame(c1b, bg=C); st_row.pack(fill="both", expand=True, padx=12, pady=(2, 8))
        self._tool_st = tk.Label(st_row, text="检测中...", font=("Microsoft YaHei UI", 9), bg=C, fg=S)
        self._tool_st.pack(anchor="w")
        self._btn_inst = btn(c1b, "  安装标注工具", self._install_tool, "border")
        self._btn_inst.pack(anchor="w", padx=12, pady=(0, 2))
        self._btn_launch = btn(c1b, "  启动标注工具", self._launch_tool, "border")
        self._btn_launch.pack(anchor="w", padx=12, pady=(0, 12))
        self.after(150, self._check_tool)  # 空闲时检查, 不阻塞页面构建

        # ══════ 右列 ③ AI 预标注 (.pt 推理 · 并排右) ══════
        c3, c3b = step_card(mid, 3, "AI 预标注", ".pt 推理 · 置信度/设备", stretch=True)
        c3.grid(row=0, column=1, sticky="nsew")
        out = Path(_cfg.output_dir)
        local_pts = [f.name for f in sorted(out.glob("*.pt"), key=lambda p: p.stat().st_mtime,
                                            reverse=True)] if out.exists() else []
        if not local_pts:
            local_pts = ["(请先训练/导入 .pt 模型)"]
        self._ai_mdl = tk.StringVar(master=self, value=local_pts[0])
        ar = tk.Frame(c3b, bg=C); ar.pack(fill="x", padx=12, pady=(8, 2))
        self._ai_cb = ttk.Combobox(ar, textvariable=self._ai_mdl, values=local_pts,
                                   state="readonly", width=18, font=("Microsoft YaHei UI", 9))
        self._ai_cb.pack(fill="x")
        cf_row = tk.Frame(c3b, bg=C); cf_row.pack(fill="x", padx=12, pady=(4, 0))
        tk.Label(cf_row, text="置信度", font=("Microsoft YaHei UI", 8), bg=C, fg=S).pack(side="left")
        self._ai_conf = tk.DoubleVar(master=self, value=0.5)
        # ⚠ 2026-10-03: `length=140` 是**像素**、是 Scale 的**请求长**。并排后列内
        #   可用宽 ~316px, 140 还算宽裕; 但上面两处 Combobox 的 `width=26` 是**字符宽**
        #   (≈182px) —— 叠上 "设备" 标签 + 🔄 + padx 后把内框顶到 531px。
        #   统一收到 18 字符 (~126px), 三件加起来 126+30+20+16 ≈ 192px, 稳落在 316px 内。
        tk.Scale(cf_row, from_=0.1, to=1.0, resolution=0.05, variable=self._ai_conf, orient="horizontal",
                 bg=C, fg=T, highlightbackground=C, troughcolor=_tc.border, length=140).pack(
            side="left", fill="x", expand=True, padx=(8, 0))
        # CUDA 查询惰性化: 后台线程探测, 不阻塞页面构建 (torch.cuda 初始化较慢)
        dr = tk.Frame(c3b, bg=C); dr.pack(fill="x", padx=12, pady=(2, 0))
        tk.Label(dr, text="设备", font=("Microsoft YaHei UI", 8), bg=C, fg=S).pack(side="left")
        self._ai_dev = tk.StringVar(master=self, value="cpu")
        self._ai_dev_cb = ttk.Combobox(dr, textvariable=self._ai_dev, values=["cpu"],
                                       state="readonly", width=18,
                                       font=("Microsoft YaHei UI", 9))
        self._ai_dev_cb.pack(side="left", padx=4)
        self._probe_devices_async("_ai_dev_cb", self._ai_dev)
        self._btn_ai_refresh = tk.Label(dr, text="🔄", font=("Microsoft YaHei UI", 9),
                                        bg=C, fg=_tc.primary, cursor="hand2")
        self._btn_ai_refresh.pack(side="left")
        self._btn_ai_refresh.bind(
            "<Button-1>",
            lambda e: self._probe_devices_async("_ai_dev_cb", self._ai_dev))
        self._kp_var = tk.BooleanVar(master=self, value=False)
        self._btn_ai = btn(c3b, "  ⚡ 开始智能标注", self._start_ai, "border")
        self._btn_ai.pack(anchor="w", padx=12, pady=(8, 2))
        # pt 模型管理 (删除/拖拽导入)
        mg = tk.Frame(c3b, bg=C); mg.pack(fill="x", padx=12, pady=(0, 2))
        self._btn_mdl_del = btn(mg, "  🗑 删除模型 ", self._delete_model, "danger")
        self._btn_mdl_del.pack(side="left", padx=(0, 8))
        self._btn_mdl_mgr = btn(mg, "  📚 模型库 ", self._open_model_manager, "border")
        self._btn_mdl_mgr.pack(side="left")
        pt_drop = tk.Frame(c3b, bg=_tc.input_bg, highlightbackground=_tc.purple,
                           highlightthickness=1, cursor="hand2")
        pt_drop.pack(fill="x", padx=12, pady=(2, 12))
        self._pt_drop_lbl = tk.Label(pt_drop, text="📥 拖拽 .pt 文件到此处导入模型",
                                     font=("Microsoft YaHei UI", 8), bg=_tc.input_bg,
                                     fg=_tc.text_secondary, justify="center")
        self._pt_drop_lbl.pack(fill="x", padx=4, pady=4)
        if _HAS_DND:
            c3b.drop_target_register(DND_FILES)
            c3b.dnd_bind("<<Drop>>", self._on_drop_pt)
            pt_drop.drop_target_register(DND_FILES)
            pt_drop.dnd_bind("<<Drop>>", self._on_drop_pt)
            self._pt_drop_lbl.drop_target_register(DND_FILES)
            self._pt_drop_lbl.dnd_bind("<<Drop>>", self._on_drop_pt)
        else:
            self._pt_drop_lbl.config(text="拖拽需要 tkinterdnd2 (pip install tkinterdnd2)")
        self._refresh_ai_models()

        # ══════ 左列 ② 数据集标注进度 ══════
        # ⚠ 2026-10-03 从右列搬到左列。理由（实测）:
        #   ① 它讲的是**数据集**的事（已标注 2842/1638 张、标注率 174%）, 挂在
        #      「训练数据集」卡下面才符合"按功能排列";
        #   ② 左列 412x765 里只用了 467px ⇒ **空 300px**; 右列却挤掉了日志的高度。
        #      108px 搬过去正好削掉左列的空洞。
        #   ⚠ 左列是 `scrollable` 的 inner（`fill="x"` 纵向堆叠）, 所以这里用 pack。
        c5, c5b = step_card(left, 2, "数据集标注进度", "已标注 / 总图 / 标注率")
        c5.pack(fill="x", pady=(8, 0))
        self._annot_prog = ttk.Progressbar(c5b, maximum=100)
        self._annot_prog.pack(fill="x", padx=12, pady=(8, 2))
        self._annot_prog.pack_forget()   # v0.23.4 平常隐藏, 标注率以文字摘要展示
        self._annot_lbl = tk.Label(c5b, text="--", font=("Microsoft YaHei UI", 9), bg=C, fg=S,
                                   anchor="w")
        self._annot_lbl.pack(fill="x", padx=12, pady=(0, 8))
        # ⚠ 2026-10-11 新增「标注体检」。它和上面那张卡**不是一回事**：
        #   上面数的是「标了多少张」（数量），这里查的是「标得对不对」（内容）。
        #   实测里最常见的训练失败成因不在数量上 —— 字段数不对、坐标没归一化、
        #   类别 ID 越界，ultralytics 要么直接报错、要么**静默当成零标签**，
        #   而报错信息通常指向别处（显存 / 超参），排查成本极高。
        #   逻辑全在 `core/dataset_qc.py`（纯函数，有单测与判据兜），
        #   这个按钮只是薄壳：取数据集路径 → 后台跑 → 报告倒进日志。
        self._qc_running = False
        self._btn_qc = btn(c5b, "  🔍 标注体检", self._run_qc, "border")
        self._btn_qc.pack(anchor="w", padx=12, pady=(0, 8))

        # ══════ 右列 ⑤ 标注输出日志 (弹性填充剩余高度) ══════
        def _bar(bar):
            self._pct_lbl = tk.Label(bar, text="0%", font=("Microsoft YaHei UI", 12, "bold"),
                                     bg=_tc.card_bg, fg=_tc.primary)
            self._pct_lbl.pack(side="right", padx=(8, 12))
        log_card, lc_body = sub_card(right, "📜 标注输出（AI标注/数据集/工具安装日志）", right=_bar)
        log_card.pack(fill="both", expand=True)
        self._status = tk.Label(lc_body, text="就绪 — 选择数据集后可启动标注工具",
                                font=("Microsoft YaHei UI", 9),
                                bg=_tc.card_bg, fg=_tc.text_secondary, anchor="w")
        self._status.pack(fill="x", padx=12, pady=(0, 4))
        # 进度条: 由 _set_pct 按需 pack(before=_train_console) / pack_forget。
        # 旧实现直接复用训练页的 _train_prog, 拆分后必须自己有一个 —— 否则
        # _set_pct 会被 try/except 吞掉, 进度条静默消失 (导入/安装时看不到进度)。
        self._train_prog = ttk.Progressbar(lc_body, maximum=100)
        self._train_console = tk.Text(lc_body, font=("Consolas", 9), bg=_tc.log_bg, fg=_tc.log_text,
                                      relief="flat", bd=0, padx=8, pady=8,
                                      highlightthickness=1, highlightbackground=_tc.border)
        self._train_console.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self._train_console.configure(state="disabled")
        for tag, color in (("err", _tc.danger), ("ok", _tc.success), ("warn", _tc.warning),
                           ("metric", _tc.text_secondary), ("hdr", _tc.accent)):
            self._train_console.tag_configure(tag, foreground=color)

        # 后台状态
        self._installing = False
        self._importing = False
        self._ann_thread = None
        self._ann_result = None

        # ── 跨页订阅: 扫描完成 / 训练页导出新模型 ──
        try:
            _bus = ds_state.bus()
            _bus.subscribe(ds_state.EV_SCANNED, self._on_state_scanned)
            _bus.subscribe(ds_state.EV_MODELS_CHANGED, self._on_models_changed)
        except Exception:
            pass
        # 清单若已被别的页扫过, 直接铺; 否则起一次后台扫描
        if ds_state.get_map():
            self._refresh_datasets()
        else:
            ds_state.ensure_scan()

        self._annot_refresh()   # 首屏也把标注摘要铺上 (否则空页只有一个 "--")
        self._poll_q()

    def _append_log(self, line, tag=None):
        try:
            self._train_console.configure(state="normal")
            self._train_console.insert("end", line, tag or "")
            self._train_console.see("end")
            self._train_console.configure(state="disabled")
        except Exception:
            pass

    def _set_pct(self, pct, text):
        try:
            self._pct_lbl.config(text=f"{int(pct)}%")
            try:
                if 0 < pct < 100 and not self._train_prog.winfo_ismapped():
                    self._train_prog.pack(fill="x", padx=12, pady=(0, 12),
                                          before=self._train_console)
                self._train_prog["value"] = pct
                if pct >= 100:
                    self._train_prog.pack_forget()  # 完成即收起
            except Exception:
                pass
            if text:
                self._status.config(text=text)
        except Exception:
            pass

    def _probe_devices_async(self, cb_attr, var):
        """设备探测 (健壮版, 不阻塞 UI):

        1. nvidia-smi 快速探测 (约1s) -> 立即把 GPU 选项放入下拉
        2. torch 确认 CUDA -> 成功后默认选中 GPU 加速
        3. 未确认时每 15 秒自动重试 (最多 4 次), 覆盖 GPU 被占/初始化慢
           导致下拉只剩 cpu 的场景; 也可点击 🔄 手动重试
        """
        def _do(attempt=0):
            names = ["cpu"]
            torch_ok = False
            # 性能修复 (2026-09-11): 本线程会 import torch (冷导入 ~9s 重 CPU)。
            # 降一级线程优先级, 避免和 UI 抢时间片导致界面发涩。
            try:
                from infrastructure.lazy_import import low_priority
                low_priority()
            except Exception:
                pass
            # 立即用上次探测结果填充下拉 (不等待 torch 初始化)
            try:
                last = (_cfg.output_dir.parent / "last_device.txt").read_text(
                    encoding="utf-8").strip()
                if last and "GPU" in last:
                    def _apply_last():
                        try:
                            cb = getattr(self, cb_attr, None)
                            if cb is not None:
                                cb["values"] = [last, "cpu"]
                                var.set(last)
                        except Exception:
                            pass
                    try:
                        self.after(0, _apply_last)
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                from infrastructure.device import detect_devices
                names, torch_ok = detect_devices()
            except Exception:
                names, torch_ok = ["cpu"], False

            def _apply():
                try:
                    cb = getattr(self, cb_attr, None)
                    if cb is not None:
                        cb["values"] = names
                        if torch_ok:
                            var.set(names[0])          # torch 确认 -> 默认 GPU
                            _persist_last_device(names[0])
                        elif var.get() not in names:
                            var.set(names[0])
                    try:
                        from infrastructure.audit import audit
                        audit("device_probe",
                              f"设备列表={names} 默认={var.get()} torch_ok={torch_ok}",
                              module="dataset_page")
                    except Exception:
                        pass
                except Exception:
                    pass
            try:
                self.after(0, _apply)
            except Exception:
                return
            if not torch_ok and attempt < 4:
                threading.Timer(
                    15.0,
                    lambda: threading.Thread(
                        target=_do, args=(attempt + 1,), daemon=True,
                        name="cuda-probe-retry").start()).start()

        threading.Thread(target=_do, daemon=True, name="cuda-probe").start()

    def _card(self, parent, title):
        f = tk.Frame(parent, bg=_tc.card_bg, highlightbackground=_tc.border, highlightthickness=1)
        f.pack(fill="x", pady=(0, 8))
        tk.Label(f, text=title, font=("Microsoft YaHei UI", 11, "bold"), bg=_tc.card_bg,
                 fg=_tc.text).pack(anchor="w", padx=12, pady=(12, 4))
        return f

    def _poll_q(self):
        """标注页消息循环 —— 只处理本页/本页后台线程产生的消息。

        2026-09-11 拆分时训练相关分支 (train_done) 已摘走; 数据集扫描改成走
        dataset_state 的 EV_SCANNED 广播, 这里不再有 ds_scan 分支。
        """
        busy = False
        try:
            while True:
                msg = self._q.get_nowait()
                busy = True
                kind = msg[0]
                if kind == "log":
                    self._append_log(msg[1], msg[2] if len(msg) > 2 else None)
                elif kind == "pct":
                    self._set_pct(msg[1], msg[2] if len(msg) > 2 else "")
                elif kind == "status":
                    try: self._status.config(text=msg[1])
                    except Exception: pass
                elif kind == "ai_progress":
                    self._set_pct(int(msg[1] / msg[2] * 100) if msg[2] else 0,
                                  f"AI标注: {msg[1]}/{msg[2]} {msg[3]}")
                elif kind == "install_done":
                    self._install_done(msg[1], msg[2])
                elif kind == "ds_done":
                    self._ds_done(msg[1], msg[2])
                elif kind == "qc_done":
                    self._qc_done(msg[1], msg[2])
                elif kind == "check":
                    self._check_tool()
                elif kind == "tool_status":
                    # 标注工具看护反馈: (text, color_key)
                    _ck = {"ok": _tc.success, "warn": _tc.warning,
                           "danger": _tc.danger, "info": _tc.text_secondary}
                    try:
                        self._status.config(text=msg[1], fg=_ck.get(msg[2], _tc.text_secondary))
                    except Exception:
                        pass
                elif kind == "ds_scan_failed":
                    try:
                        self._ds_info.config(text=f"扫描失败: {msg[1]}", fg=_tc.danger)
                    except Exception:
                        pass
        except _queue.Empty:
            pass
        except Exception:
            return  # 页面已销毁
        try:
            self.after(_pk.poll_delay(self, busy), self._poll_q)
        except Exception:
            pass

    def _refresh_datasets(self, select=None):
        self._ds_map = ds_state.get_map()   # v0.26.0: 清单真源在 dataset_state
        self._splits_ds = None   # 数据集清单刷新后强制重建 splits
        names = list(self._ds_map.keys())
        if names:
            self._ds_cb.config(values=names)
            last = ds_state.get_name()   # v0.26.0: 上次选择由 dataset_state 持有
            self._sync_ds_from_widget()
            cur = self._ds_var.get()
            kept = False
            if select and select in names:
                self._ds_var.set(select)
            elif cur in names:
                kept = True  # 保持用户当前选择: 后台扫描完成不得回滚
            elif last and last in names:
                self._ds_var.set(last)          # 恢复上次使用的数据集
            else:
                self._ds_var.set(names[0])
            info = self._ds_map.get(self._ds_var.get())
            if info:
                self._ds_info.config(text=f"图片: {info.image_count}张 | 标注: {info.label_count}个")
            if kept:
                self._refresh_splits()
        else:
            # 2026-09-11: 空清单不再只写"暂无数据集" —— 那句提示既不说是哪个目录,
            # 也不给出路, 用户会卡在这里反复点刷新 (数据目录被清空时就会撞上)。
            self._ds_info.config(
                text=f"暂无数据集 · 已扫描: {ds_state.get_config_data_dir()}\n"
                     f"把数据集放进该目录, 或用「H3 摔倒生成」/「抽帧工具」产出后回本页刷新",
                fg=_tc.warning, justify="left")
            self._refresh_splits()

    def _refresh_splits(self):
        """分析选中数据集结构, 填充标注目录下拉框"""
        self._splits = {}
        self._splits_ds = self._ds_var.get()
        ds_name = self._ds_var.get()
        info = self._ds_map.get(ds_name)
        if not info:
            self._split_cb["values"] = []
            self._split_var.set("")
            self._split_info.config(text="")
            return
        try:
            from core.dataset_profiler import profile_dataset
            prof = profile_dataset(info.path)
        except Exception:
            prof = None
        names = []
        if prof and prof.splits:
            for sp in prof.splits:
                fmt = {"yolo": "YOLO", "labelme": "JSON", "voc": "VOC",
                       "mixed": "混合", "none": "无标注"}.get(sp.label_format, sp.label_format)
                key = f"{sp.display} ({sp.image_count}张,{fmt})"
                self._splits[key] = sp
                names.append(key)
            if len(prof.splits) > 1:
                total = sum(sp.image_count for sp in prof.splits)
                key = f"整个数据集 ({total}张)"
                self._splits[key] = "ALL"
                names.append(key)
        if not names:
            # 回退: 整个数据集
            key = "整个数据集"
            self._splits[key] = "ALL"
            names = [key]
        self._split_cb["values"] = names
        # 默认: 训练集 > 整个数据集
        default = None
        for key in names:
            sp = self._splits.get(key)
            if sp is not None and sp != "ALL" and getattr(sp, "kind", "") == "train":
                default = key
                break
        self._split_var.set(default or names[0])
        self._on_split_change()

    def _on_ds_change(self, e=None):
        self._sync_ds_from_widget()
        self.after(120, self._ds_show_preview)
        self.after(150, self._annot_refresh)
        try:
            info = self._ds_map.get(self._ds_var.get())
            if info:
                self._ds_info.config(text=f"图片: {info.image_count}张 | 标注: {info.label_count}个")
            # v0.26.0: 持久化由 dataset_state 负责, 这里不再各写一份
            ds_state.set_selection(self._ds_var.get())
        except Exception:
            pass
        try:
            audit("ds_event",
                  f"下拉选择事件: var={self._ds_var.get()!r} widget={self._ds_cb.get()!r}",
                  module="dataset_page")
        except Exception:
            pass
        # 标注工具运行中且目标数据集不同: 询问是否跟随切换 (解决"切换数据集没反应")
        try:
            new_name = self._ds_var.get()
            new_info = self._ds_map.get(new_name)
            if (new_info and self._anylabeling_running()
                    and str(new_info.path.resolve()) != str(self._tool_opened_dir or "")):
                if messagebox.askyesno(
                        "标注工具正在运行",
                        f"标注工具当前打开的是之前的数据集。\n\n"
                        f"是否关闭旧窗口，并打开当前选择的数据集「{new_name}」？\n"
                        f"(选“否”则保留旧窗口，稍后可再次点击“启动标注工具”)",
                        parent=self):
                    self._close_anylabeling()
                    audit("tool_switch_follow",
                          f"切换数据集时关闭旧标注工具 -> {new_name}",
                          module="dataset_page")
                    # 关闭后自动用新数据集重新打开标注工具
                    try:
                        self.after(300, lambda: self._launch_tool())
                    except Exception:
                        pass
        except Exception:
            pass

    def _sync_ds_from_widget(self):
        """以数据集下拉框实际显示值为准, 同步 _ds_var。

        真实环境发现: 部分交互下 ttk.Combobox 显示值已变化但 textvariable
        未同步 (审计无 ds_select 记录), 导致启动标注工具仍用旧数据集。
        此处读取控件显示值兜底, 保证启动与显示一致。
        """
        try:
            shown = self._ds_cb.get().strip()
            if shown and shown in self._ds_map and shown != self._ds_var.get():
                self._ds_var.set(shown)
        except Exception:
            pass

    def _on_ds_var_write(self, *args):
        """_ds_var 任何变化都立即同步标注目录/子目录与统计信息。

        关键: 不依赖 <<ComboboxSelected>> 事件 (实测该事件在部分交互下不触发,
        导致 _ds_var 已变但 _splits 仍是旧数据集, 启动标注工具时打开旧目录)。
        """
        try:
            name = self._ds_var.get()
            info = self._ds_map.get(name)
            if info:
                self._ds_info.config(
                    text=f"图片: {info.image_count}张 | 标注: {info.label_count}个")
            # v0.26.0: 持久化/审计/跨页广播统一交给 dataset_state (唯一真源)
            if name and info:
                ds_state.set_selection(name)
        except Exception:
            pass
        self._refresh_splits()

    def _ds_preview_path(self):
        """解析当前数据集的**真实目录** —— 只认清单里的路径。

        坑 (2026-09-11 修, 潜在假数据源):
            旧实现写的是 `Path(self._ds_var.get())` —— 把**数据集名**当**路径**用,
            而且是相对**进程 CWD** 解释的。名字一旦恰好撞上 CWD 下的某个目录
            (如 "runs" / "tools"), 它就会去扫那个**毫不相干**的目录
            (实测 tools = 565 目录 / 5001 文件), 还全压在主线程。
            现在只认 `_ds_map[name].path`, 解析不出就返回 None (显示空状态)。
        """
        try:
            info = self._ds_map.get(self._ds_var.get())
            p = getattr(info, "path", None) if info else None
            if not p:
                return None
            pp = Path(p)
            return pp if pp.is_dir() else None
        except Exception:
            return None

    # ─────────── 内置标注编辑器（双击缩略图进入）───────────
    def _open_editor(self, img_path):
        """双击缩略图 → 打开内置标注编辑器：读现有标签 → 改 → 写回 labels/*.txt。

        路径解析只认页面自己解析出来的真实目录（`_ds_preview_path`），
        不二次拼路径 —— 数据集搬迁后 `_ds_map` 是唯一真源。
        """
        try:
            img_path = Path(str(img_path))
            if not img_path.is_file():
                return
            from core.dataset_profiler import find_labels_for_image_dir
            from core.yolo_convert import (convert_txt_to_labelme, get_image_size,
                                           load_classes, write_yolo_labels)
            from gui.annotation_editor import AnnotationEditor

            size = get_image_size(img_path)
            if size is None:
                self._append_log(">> 读不到图片尺寸, 无法标注: %s\n" % img_path.name, "err")
                return
            img_w, img_h = size

            ds_path = self._ds_preview_path()
            classes = (load_classes(ds_path) if ds_path else None) or []
            if not classes:
                classes = ["person", "fall"]
                self._append_log(">> 未读到 classes/data.yaml, 暂用 ['person','fall']\n")

            lab_dir = find_labels_for_image_dir(img_path.parent) or (img_path.parent.parent / "labels")
            txt = Path(lab_dir) / (img_path.stem + ".txt")

            # ── 读现有标注（labelme json 或 YOLO txt 都先摊成 shapes 再取）──
            bbox = poly = None
            kpts = []
            if txt.exists():
                data = convert_txt_to_labelme(txt, img_path, classes) or {}
                for sh in data.get("shapes", []):
                    st = sh.get("shape_type")
                    pts = sh.get("points") or []
                    if st == "rectangle" and bbox is None and len(pts) >= 2:
                        (x1, y1), (x2, y2) = pts[0], pts[1]
                        bbox = [(x1 + x2) / 2 / img_w, (y1 + y2) / 2 / img_h,
                                abs(x2 - x1) / img_w, abs(y2 - y1) / img_h]
                    elif st == "polygon" and poly is None and len(pts) >= 3:
                        poly = [v for p in pts for v in (p[0] / img_w, p[1] / img_h)]
                    elif st == "point" and len(pts) >= 1:
                        kpts.append({"name": sh.get("label"), "x": pts[0][0] / img_w,
                                     "y": pts[0][1] / img_h, "vis": 2})

            def _on_save(payload):
                # 编辑器内部是**归一化**坐标；YOLO 要的是**像素** ⇒ 换算只在这一处做，
                # 别散到各处 —— 散开迟早有一处漏乘，且看不出来。
                px_shapes = [dict(s, points=[[p[0] * img_w, p[1] * img_h] for p in s["points"]])
                             for s in payload.get("shapes", [])]
                n = write_yolo_labels(txt, px_shapes, img_w, img_h, classes)
                self._append_log(">> 已保存标注: %s (%d 个对象) → %s\n"
                                 % (img_path.name, n, txt.name), "ok")
                audit("annotate_save", path=str(txt), shapes=n)

            AnnotationEditor(self, img_path, bbox=bbox, poly=poly, kpts=kpts,
                             cls=1, on_save=_on_save, classes=classes)
        except Exception as e:
            self._append_log(">> 打开标注器失败: %s: %s\n" % (type(e).__name__, e), "err")

    def _ds_preview_empty(self, text):
        host = getattr(self, "_ds_prev_host", None)
        if host is None:
            return
        try:
            for w in host.winfo_children():
                w.destroy()
            tk.Label(host, text=text, bg=_tc.card_bg, fg=_tc.text_muted,
                     font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=24)
        except Exception:
            pass

    def _ds_show_preview(self):
        """数据集样本预览 —— 扫描与解码**全在后台**, 主线程只负责贴图。

        2026-09-11 性能修复 (实测: 本函数是用户报"使用到一半自己会卡"的**唯一主因**)
            12 秒观察窗口里它占 **22/28 次卡顿、累计 126 秒、单次最长 10 秒**。
            旧实现三笔成本全压在主线程:
              ① `sorted(rglob("*"))` 且对**每个条目** `is_file()` —— 每项一次 stat,
                 实测比 `os.walk` 慢 8.3~11.5 倍 (runs: 245.7 vs 29.6ms;
                 tools: 681.2 vs 59.2ms)。
              ② 只要最后 16 张, 却把**整棵树**排了序。
              ③ `Image.open().convert("RGB")` + `thumbnail` **同步**解 16 张 ——
                 实测单次 **10087ms** (大图 + E: USB 盘)。
            而它的 docstring 一直写着"最近 8 张" —— 实现取的却是**字母序最后** 16 张,
            文档与实现不符 (现在按 mtime 取最新的, 与 docstring 一致)。

            现在: 扫描走 `thumb_pool.scan_async` (os.scandir + 有界), 缩略图走
            `thumb_pool.submit` (常驻后台线程 + 磁盘缓存), 主线程只 create_image。
            切页、换数据集都不再阻塞事件循环。
        """
        host = getattr(self, "_ds_prev_host", None)
        if host is None:
            return
        try:
            for w in host.winfo_children():
                w.destroy()
        except Exception:
            pass
        self._ds_prev_imgs = []

        ds_path = self._ds_preview_path()
        if ds_path is None:
            self._ds_preview_empty("（选择数据集后显示样本）")
            return

        pstr = str(ds_path)
        # ── 幂等闸门 (2026-09-11) ──
        # 本函数会被反复触发 (on_show / EV_CHANGED / 下拉事件)。旧写法每次都重开
        # 一轮并把 token +1, 于是**上一轮已生成的结果全被丢弃** —— 实测切页后
        # 只贴出 1/16 张缩略图。同一目录且上一轮还没画完时, 让它画完就行。
        if (getattr(self, "_ds_prev_path", None) == pstr
                and not getattr(self, "_ds_prev_done", True)):
            self._ds_dbg("skip: 同一目录且上一轮未完成")
            return
        # 同一目录、上一轮已完成、且已经贴出内容 → 无事可做
        if (getattr(self, "_ds_prev_path", None) == pstr
                and getattr(self, "_ds_prev_done", False)
                and getattr(self, "_ds_prev_host", None) is not None
                and self._ds_prev_host.winfo_children()):
            self._ds_dbg("skip: 同一目录已完成且已有内容")
            return

        tok = getattr(self, "_ds_prev_token", 0) + 1
        self._ds_prev_token = tok
        self._ds_prev_path = pstr
        self._ds_prev_done = False
        self._ds_prev_ph = {}                 # 持有 PhotoImage, 防 GC
        slot = {"scanned": False, "imgs": []}
        self._ds_prev_slot = slot
        self._ds_dbg("start tok=%d path=%s" % (tok, pstr))

        if not thumb_pool.scan_async(str(ds_path), tok, want=16):
            # 已有一个扫描在跑: 稍后再试, 绝不阻塞主线程
            self._ds_dbg("scan busy, retry")
            self.after(120, lambda t=tok: (
                self._ds_show_preview()
                if t == getattr(self, "_ds_prev_token", 0) else None))
            return
        self._ds_preview_drain(tok, slot)

    def _ds_dbg(self, msg):
        """预览流水线的诊断留痕 (只保留最近 40 条, 供测试/排障读取)。"""
        try:
            log = getattr(self, "_ds_dbg_log", None)
            if log is None:
                log = self._ds_dbg_log = []
            log.append("%.3f %s" % (time.time() % 1000, msg))
            del log[:-40]
        except Exception:
            pass

    def _ds_preview_drain(self, tok, slot):
        """主线程: 取回后台结果并贴图; 干完就停 (不常驻轮询)。"""
        if tok != getattr(self, "_ds_prev_token", 0):
            return
        host = getattr(self, "_ds_prev_host", None)
        if host is None:
            return
        # 页面可能已被销毁 —— 销毁后再 after 回调会 Invalid command name
        try:
            if not host.winfo_exists() or not self.winfo_exists():
                return
        except Exception:
            return
        try:
            # ① 扫描结果 -> 逐张丢给缩略图池
            if not slot["scanned"]:
                got = thumb_pool.take_scan()
                if got is not None:
                    t2, res = got
                    if t2 == tok:
                        slot["scanned"] = True
                        slot["imgs"] = [fp for _m, fp in res]
                        self._ds_dbg("scan done: %d 张" % len(res))
                        if not res:
                            self._ds_preview_done = True
                            self._ds_preview_empty("（未发现图片样本）")
                            return
                        for fp in slot["imgs"]:
                            thumb_pool.submit(fp, (118, 104), tok)
                    else:
                        self._ds_dbg("scan token 过期 (%s != %s)" % (t2, tok))
            # ② 缩略图结果 -> 贴到 host
            #    ⚠ 限流: 一次 tick 只贴 PLACE_PER_TICK 张。
            #    实测把 16 张一次性贴完会造成 **288.9ms 的单帧尖峰**
            #    (16 次 解码jpg + ImageTk.PhotoImage 全在同一帧里) —— 那就又是"卡一下"。
            #    分帧贴之后帧间隔回落, 视觉上是"缩略图快速逐张浮现", 比"卡一下再全出"更好。
            for src, _size, t2, cache_f in thumb_pool.take_all():
                if t2 != tok or not cache_f:
                    continue
                slot.setdefault("ready", []).append((src, cache_f))
            placed = 0
            while slot.get("ready") and placed < PLACE_PER_TICK:
                src, cache_f = slot["ready"].pop(0)
                try:
                    ph = thumb_pool.photo(cache_f, key=src, master=host)
                except Exception as e:
                    self._ds_dbg("photo() 失败 %r" % (e,))
                    continue
                self._ds_prev_ph[src] = ph
                idx = len(self._ds_prev_imgs)
                self._ds_prev_imgs.append(ph)
                lbl = tk.Label(host, image=ph, bg=_tc.card_bg, bd=0,
                               highlightbackground=_tc.border,
                               highlightthickness=1)
                lbl.grid(row=idx // 8, column=idx % 8,
                         padx=(0, 4), pady=(0, 4), sticky="w")
                # 双击缩略图 → 内置标注编辑器（改完写回 labels/*.txt）
                lbl.bind("<Double-Button-1>", lambda e, f=src: self._open_editor(f))
                lbl.bind("<Enter>", lambda e, w=lbl: w.config(
                    highlightbackground=_tc.primary, highlightthickness=2))
                lbl.bind("<Leave>", lambda e, w=lbl: w.config(
                    highlightbackground=_tc.border, highlightthickness=1))
                placed += 1
            # ③ 收工判定: 扫描完了 + 池子空了 + 待贴队列空了
            if (slot["scanned"] and not thumb_pool.scan_busy()
                    and thumb_pool.pending() == 0 and not slot.get("ready")):
                self._ds_prev_done = True
                self._ds_dbg("done: 贴出 %d 张" % len(self._ds_prev_imgs))
                if not self._ds_prev_imgs:
                    self._ds_preview_empty("（未发现图片样本）")
                return
        except Exception as e:
            self._ds_dbg("drain 异常, 停止: %r" % (e,))
            self._ds_prev_done = True
            return
        self.after(90, lambda: self._ds_preview_drain(tok, slot))

    def _annot_refresh(self):
        """标注进度卡刷新 (标注数/总数/进度条)"""
        try:
            info = self._ds_map.get(self._ds_var.get())
            if not info:
                self._annot_lbl.config(text="（选择数据集后显示标注进度）")
                self._annot_prog["value"] = 0
                return
            total = getattr(info, "image_count", 0) or 0
            labeled = getattr(info, "label_count", 0) or 0
            pct = round(labeled / total * 100) if total else 0
            self._annot_prog["value"] = pct
            self._annot_lbl.config(
                text=f"已标注 {labeled} / 共 {total} 张 · 标注率 {pct}%"
                     + (" ✓ 可开始训练" if total and labeled >= total * 0.8 else " · 建议先补齐标注"))
        except Exception:
            pass

    def _on_split_change(self, e=None):
        """标注目录切换: 显示图片/标注目录映射, 并刷新可打开的子目录列表"""
        try:
            ds_path = Path(self._ds_map[self._ds_var.get()].path)
            sp = self._splits.get(self._split_var.get())
            if sp is None:
                self._split_info.config(text="")
                self._sub_cb["values"] = []
                self._sub_var.set("")
                self._subs = {}
                return
            self._subs = {}
            if sp == "ALL":
                base = ds_path
                self._split_info.config(text="打开数据集全部图片 (自动检测子目录)")
            else:
                base = sp.images_dir
                try:
                    img_rel = sp.images_dir.relative_to(ds_path)
                except Exception:
                    img_rel = sp.images_dir
                ld_txt = "标注目录: 无 (未检测到标注)"
                if sp.labels_dir:
                    try:
                        ld_rel = sp.labels_dir.relative_to(ds_path)
                    except Exception:
                        ld_rel = sp.labels_dir
                    ld_txt = f"标注目录: {ld_rel} ({sp.label_format})"
                self._split_info.config(
                    text=f"图片: {sp.image_count}张 | 标注: {sp.label_count}个 ({sp.label_format})\n"
                         f"图片目录: {img_rel}\n{ld_txt}")
            # 子目录列表 (该目录下含图片的子文件夹)
            from core.dataset_profiler import list_image_subdirs
            names = ["自动 (该目录全部图片)"]
            self._subs["自动 (该目录全部图片)"] = None
            for d, n in list_image_subdirs(base):
                try:
                    rel = str(d.relative_to(ds_path))
                except Exception:
                    rel = str(d)
                key = f"{rel} ({n}张)"
                self._subs[key] = d
                names.append(key)
            names.append("📁 自定义子文件夹...")
            self._subs["📁 自定义子文件夹..."] = "CUSTOM"
            prev = self._sub_var.get()
            self._sub_cb["values"] = names
            self._sub_var.set(prev if prev in names else names[0])
            self._preconvert_schedule()
        except Exception:
            pass

    def _on_sub_change(self, e=None):
        """子目录切换: 自定义选择或显示选中子目录信息"""
        try:
            sel = self._subs.get(self._sub_var.get())
            if sel == "CUSTOM":
                ds_path = Path(self._ds_map[self._ds_var.get()].path)
                sp = self._splits.get(self._split_var.get(), "ALL")
                base = ds_path if sp == "ALL" else sp.images_dir
                from tkinter import filedialog
                d = filedialog.askdirectory(title="选择图片子文件夹",
                                            initialdir=str(base), parent=self)
                if d:
                    key = f"自定义: {os.path.basename(d)}"
                    self._subs[key] = Path(d)
                    vals = list(self._sub_cb["values"])
                    if key not in vals:
                        vals.insert(-1, key)
                    self._sub_cb["values"] = vals
                    self._sub_var.set(key)
                    self._split_info.config(text=f"已选择子目录:\n{Path(d)}")
                    audit("tool_subdir", f"自定义子目录: {d}", module="dataset_page")
                else:
                    self._sub_var.set("自动 (该目录全部图片)")
                return
            if sel is None:
                self._split_info.config(text="自动: 打开所选目录全部图片")
            else:
                try:
                    n = sum(1 for f in sel.iterdir()
                            if f.is_file() and f.suffix.lower() in
                            {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"})
                except Exception:
                    n = 0
                self._split_info.config(text=f"子目录: {sel}\n图片: {n}张")
        except Exception:
            pass

    def _preconvert_schedule(self):
        """切换标注目录后后台预转换 YOLO->JSON (增量, 点启动时痕迹已就绪)

        避免大数据集 (如 5072 张) 首次启动标注工具时长时间等待转换。
        """
        if getattr(self, "_preconvert", False):
            return
        try:
            ds_path = Path(self._ds_map[self._ds_var.get()].path)
            sel = self._splits.get(self._split_var.get(), "ALL")
            jobs = []
            if sel == "ALL":
                from core.dataset_profiler import profile_dataset
                prof = profile_dataset(ds_path)
                for sp in prof.splits if prof else []:
                    if sp.labels_dir and sp.label_format in ("yolo", "mixed"):
                        jobs.append((sp.images_dir, sp.labels_dir))
            elif sel is not None and sel != "ALL":
                if sel.labels_dir and sel.label_format in ("yolo", "mixed"):
                    jobs.append((sel.images_dir, sel.labels_dir))
            if not jobs:
                return
            from core.yolo_convert import load_classes, sync_yolo_to_labelme
            classes = load_classes(ds_path)
            self._preconvert = True

            def _do():
                try:
                    for img_d, lab_d in jobs:
                        sync_yolo_to_labelme(img_d, lab_d, classes)
                    try:
                        self._q.put(("log",
                                     ">> 数据集标注预转换完成 (标注工具打开即可见痕迹)\n", "ok"))
                    except Exception:
                        pass
                except Exception:
                    pass
                finally:
                    self._preconvert = False
            threading.Thread(target=_do, daemon=True, name="preconvert").start()
        except Exception:
            pass

    def _on_drop(self, event):
        try:
            from utils.file_utils import parse_dnd_paths
            paths = parse_dnd_paths(event.data)
        except Exception:
            paths = [event.data]
        folder = None
        for p in paths:
            try:
                if Path(p).is_dir():
                    folder = Path(p)
                    break
            except Exception:
                continue
        if folder is None:
            messagebox.showwarning("提示", "请拖入包含图片的文件夹", parent=self)
            return
        self._import_dataset(folder)

    def _import_dataset(self, folder):
        if self._importing:
            return
        imgs = [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in
                {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}]
        if not imgs:
            messagebox.showwarning("提示", f"文件夹中没有图片: {folder}", parent=self)
            return
        name = simpledialog.askstring("生成训练集", f"检测到 {len(imgs)} 张图片\n请输入数据集名称:",
                                      initialvalue=folder.name, parent=self)
        if not name:
            return
        name = "".join(ch for ch in name.strip() if ch not in '\\/:*?"<>|')
        if not name:
            return
        from core.trainer import (build_dataset_from_images, import_dataset_copy,
                                  is_dataset_folder, normalize_dataset_in_place,
                                  read_classes_from_folder)
        # 目标数据集已存在: 提供"原位规范化"入口 (备份并删除标注 + 整理三分结构)
        existing = _cfg.data_dir / name
        if existing.exists():
            if messagebox.askyesno(
                    "数据集已存在",
                    f"数据集「{name}」已存在。\n\n"
                    "是否原位规范化该数据集？\n"
                    "(备份并删除全部标注文件, 整理为 train/valid/test 三分结构)",
                    parent=self):
                self._run_normalize(existing)
            return
        default_classes = read_classes_from_folder(folder) or ["person", "fall"]
        cls_str = simpledialog.askstring("生成训练集", "类别 (逗号分隔):",
                                         initialvalue=",".join(default_classes), parent=self)
        if cls_str is None:
            return
        classes = [c.strip() for c in cls_str.split(",") if c.strip()]
        if not classes:
            classes = ["person", "fall"]

        # 拖入的本身就是数据集目录: 询问是复制规范副本还是按 80/10/10 重分
        if is_dataset_folder(folder):
            choice = messagebox.askyesnocancel(
                "检测到数据集目录",
                f"拖入的文件夹「{folder.name}」本身就是数据集目录。\n\n"
                "是: 复制为规范副本 (备份并剔除全部标注, 保留原划分)\n"
                "否: 按 80/10/10 重新生成纯图片三分数据集\n"
                "取消: 中止",
                parent=self)
            if choice is None:
                return
            if choice:
                self._importing = True
                self._append_log(f">> 复制规范数据集副本: {name} (剔除标注, 保留划分)\n", "hdr")
                self._set_pct(0, "正在复制数据集副本...")
                audit("dataset_import_copy_start", f"复制副本 {name} 源={folder.name} 图片={len(imgs)}",
                      module="dataset_page")
                def _copy():
                    try:
                        dest = import_dataset_copy(
                            folder, dest_name=name, classes=classes,
                            log_cb=lambda m, tag="info": self._q.put(("log", "  " + m + "\n", tag)),
                            progress_cb=lambda pct, txt: self._q.put(("pct", pct, txt)))
                        self._q.put(("ds_done", True, str(dest)))
                    except Exception as e:
                        self._q.put(("log", f">> 复制失败: {e}\n", "err"))
                        self._q.put(("ds_done", False, str(e)))
                threading.Thread(target=_copy, daemon=True).start()
                return

        self._importing = True
        self._append_log(f">> 生成训练集: {name} ({len(imgs)} 张图片, 类别 {classes}, 不携带标注)\n", "hdr")
        self._set_pct(0, "正在生成训练集...")
        audit("dataset_import", f"生成训练集 {name} 源={folder.name} 图片={len(imgs)}", module="dataset_page")

        def _do():
            try:
                dest = build_dataset_from_images(
                    folder, dest_name=name, classes=classes,
                    log_cb=lambda m, tag="info": self._q.put(("log", "  " + m + "\n", tag)),
                    progress_cb=lambda pct, txt: self._q.put(("pct", pct, txt)),
                    strip_annotations=True)
                self._q.put(("ds_done", True, str(dest)))
            except Exception as e:
                self._q.put(("log", f">> 生成失败: {e}\n", "err"))
                self._q.put(("ds_done", False, str(e)))
        threading.Thread(target=_do, daemon=True).start()

    def _run_normalize(self, ds_path, classes=None):
        """原位规范化已有数据集: 备份并删除标注 + 整理 train/valid/test + data.yaml"""
        if self._importing:
            return
        from core.trainer import normalize_dataset_in_place
        ds = Path(ds_path)
        self._importing = True
        self._append_log(f">> 原位规范化数据集: {ds.name} (备份并删除标注, 整理三分结构)\n", "hdr")
        self._set_pct(0, "正在规范化数据集...")
        audit("dataset_normalize_start", f"原位规范化 {ds}", module="dataset_page")

        def _do():
            try:
                dest = normalize_dataset_in_place(
                    ds, classes=classes,
                    log_cb=lambda m, tag="info": self._q.put(("log", "  " + m + "\n", tag)),
                    progress_cb=lambda pct, txt: self._q.put(("pct", pct, txt)))
                self._q.put(("ds_done", True, str(dest)))
            except Exception as e:
                self._q.put(("log", f">> 规范化失败: {e}\n", "err"))
                self._q.put(("ds_done", False, str(e)))
        threading.Thread(target=_do, daemon=True).start()

    def _ds_done(self, ok, info):
        self._importing = False
        if ok:
            self._set_pct(100, "训练集生成完成 ✓")
            self._append_log(f">> 训练集生成完成: {info}\n", "ok")
            self._refresh_datasets(select=Path(info).name)
            if self.controller:
                self.controller.notify(f"训练集已生成: {Path(info).name}", "success")
        else:
            self._set_pct(0, "训练集生成失败")
            self._append_log(f">> 生成失败: {info}\n", "err")

    # ── 标注体检 (2026-10-11) ────────────────────────────────────────────────

    def _run_qc(self, e=None):
        """审**当前数据集的标注内容**（不只是数量）。逻辑在 core/dataset_qc.py。"""
        if self._qc_running:
            return
        name = self._ds_var.get().strip()
        info = getattr(self, "_ds_map", {}).get(name)
        if not info:
            messagebox.showwarning("提示", "请先选择一个数据集", parent=self)
            return
        ds_path = Path(info.path)
        if not ds_path.is_dir():
            messagebox.showwarning("提示", f"数据集目录不存在:\n{ds_path}", parent=self)
            return

        self._qc_running = True
        try:
            self._btn_qc.config(state="disabled", text="  体检中...")
        except Exception:
            pass
        self._append_log(f">> 标注体检: {name}\n", "hdr")
        self._set_pct(0, "正在体检标注内容...")
        audit("dataset_qc_start", f"标注体检 {name}", module="dataset_page")

        def _do():
            try:
                from core.dataset_qc import audit_dataset, render_text
                res = audit_dataset(ds_path)
                self._q.put(("qc_done", res, render_text(res)))
            except Exception as ex:
                self._q.put(("qc_done", None, f">> 体检失败: {ex}\n"))
        threading.Thread(target=_do, daemon=True, name="dataset-qc").start()

    def _qc_done(self, res, text):
        self._qc_running = False
        try:
            self._btn_qc.config(state="normal", text="  🔍 标注体检")
        except Exception:
            pass
        self._set_pct(0, "就绪")
        for line in str(text).splitlines():
            self._append_log(line + "\n")
        if res is None:
            self._append_log(">> 体检未完成（见上）\n", "err")
            if self.controller:
                self.controller.notify("标注体检失败", "error")
            return
        if res.ok:
            self._append_log(">> 标注体检通过（无致命问题）\n", "ok")
            if self.controller:
                self.controller.notify(f"标注体检通过: {res.name}", "success")
        else:
            self._append_log(f">> 发现 {res.fatal_count} 类致命问题，建议先修复再训练\n", "err")
            if self.controller:
                self.controller.notify(f"标注体检: {res.name} 有致命问题", "error")

    def _check_tool(self, e=None):
        """检测标注工具是否已安装 (2026-09-11: PATH 扫描移到后台线程)。

        实测 shutil.which() 在本机 PATH 上单次 416ms —— 它逐条 os.path.exists /
        os.access 扫 PATH×PATHEXT。原实现由 `after(150, ...)` 直接在主线程调用,
        正好落在启动后的观察窗口里, 一卡就是 0.4 秒 (真机看门狗抓到
        416ms 停滞停在 shutil.which -> _access_check)。现在主线程只负责写状态。
        """
        t = self._tool_var.get()
        cmds, mods = self._TOOLS.get(t, ((t,), t))
        seq = getattr(self, "_tool_seq", 0) + 1
        self._tool_seq = seq
        try:                       # 先置"检测中", 避免旧结果留在屏幕上误导读
            self._tool_st.config(text="检测中…", fg=_tc.text_muted)
            self._btn_launch.config(state="disabled")
        except Exception:
            pass

        def _work():
            ok = False
            for cand in cmds:
                try:
                    if shutil.which(cand):
                        ok = True
                        break
                except Exception:
                    pass
            if not ok:
                try:
                    ok = __import__("importlib.util").util.find_spec(mods) is not None
                except Exception:
                    ok = False

            def _apply():
                # 期间又切过工具 → 丢弃过期结果, 别把旧状态盖上去
                if getattr(self, "_tool_seq", 0) != seq:
                    return
                try:
                    self._tool_st.config(text="已安装" if ok else "未安装",
                                         fg=_tc.success if ok else _tc.danger)
                    self._btn_launch.config(state="normal" if ok else "disabled")
                except Exception:
                    pass

            try:
                self.after(0, _apply)
            except Exception:
                pass

        threading.Thread(target=_work, daemon=True, name="tool-probe").start()

    def _install_tool(self, e=None):
        if self._installing:
            return
        self._installing = True
        pkg = "x-anylabeling-cvhub"
        self._btn_inst.config(state="disabled", text="  安装中...")
        self._append_log(f">> 开始安装 {pkg} (官方源: {_cfg.pip_mirror})\n", "hdr")
        self._set_pct(0, "正在安装标注工具...")
        audit("tool_install", f"安装 {pkg}", module="dataset_page")

        def _do():
            rc = pip_install_stream(
                pkg,
                on_line=lambda line: self._q.put(("log", line + "\n")),
                mirror=_cfg.pip_mirror,
                timeout=_cfg.timeout_pip)
            self._q.put(("install_done", rc, pkg))
        threading.Thread(target=_do, daemon=True).start()

    def _install_done(self, rc, pkg):
        self._installing = False
        try:
            self._btn_inst.config(state="normal", text="  重新安装")
        except Exception:
            pass
        if rc == 0:
            self._set_pct(100, f"{pkg} 安装完成 ✓")
            self._append_log(f">> {pkg} 安装成功\n", "ok")
        else:
            hint = ""
            if sys.version_info < (3, 11):
                hint = f"\n>> 提示: 当前 Python {sys.version_info.major}.{sys.version_info.minor}, X-AnyLabeling 官方要求 Python 3.11+"
            self._set_pct(0, "安装失败, 详见日志")
            self._append_log(f">> {pkg} 安装失败 (exit={rc}){hint}\n", "err")
        self._check_tool()

    def _anylabeling_running() -> bool:
        """检测 X-AnyLabeling 是否已在运行 (避免多窗口混淆数据集)"""
        try:
            import subprocess as _sp
            r = _sp.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'anylabeling' }).Count"],
                capture_output=True, timeout=15,
                creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
            return (r.stdout or b"0").decode("utf-8", "ignore").strip().isdigit() and int(r.stdout.decode("utf-8", "ignore").strip() or 0) > 0
        except Exception:
            return False

    def _close_anylabeling() -> bool:
        """关闭已在运行的 X-AnyLabeling 实例, 返回是否已确认无残留"""
        try:
            import subprocess as _sp
            _sp.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'anylabeling' } | "
                 "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
                capture_output=True, timeout=20,
                creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
        except Exception:
            pass
        # 等待进程真正退出 (最多约 6 秒), 仍残留则二次补杀
        for _ in range(6):
            try:
                if not TrainingPage._anylabeling_running():
                    return True
            except Exception:
                return True
            time.sleep(1)
        try:
            import subprocess as _sp
            _sp.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'anylabeling' } | "
                 "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
                capture_output=True, timeout=20,
                creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
        except Exception:
            pass
        return not TrainingPage._anylabeling_running()

    def _launch_tool(self, e=None):
        # 以控件显示值为准兜底同步 (修复真实环境下变量未随选择更新的问题)
        self._sync_ds_from_widget()
        pkg = self._tool_var.get()
        # 重复实例防护: 已有标注工具窗口时先询问关闭, 避免新旧窗口混淆数据集
        try:
            if self._anylabeling_running():
                ds_display = self._ds_var.get() or "当前选择的数据集"
                if messagebox.askyesno(
                        "标注工具已在运行",
                        "检测到 X-AnyLabeling 已在运行 (可能打开的是旧数据集)。\n\n"
                        f"是否关闭旧实例，并打开当前选择的「{ds_display}」？\n"
                        "(选“否”则保留旧窗口，本次不会启动新窗口)",
                        parent=self):
                    closed = self._close_anylabeling()
                    audit("tool_close_old",
                          f"关闭旧标注工具实例 成功={closed} -> 准备打开 {ds_display}",
                          level="info" if closed else "warn", module="dataset_page")
                else:
                    return
        except Exception:
            pass
        ds_name = self._ds_var.get()
        if ds_name not in self._ds_map:
            messagebox.showwarning("提示", "请先选择有效的数据集", parent=self)
            return
        ds_path = self._ds_map[ds_name].path
        if not ds_path.exists():
            messagebox.showwarning("提示", f"数据集目录不存在: {ds_path}", parent=self)
            return
        # 防御: 确保 _splits 与当前选择一致 (防止选择事件丢失导致打开旧数据集目录)
        try:
            if self._splits_ds != ds_name:
                self._refresh_splits()
        except Exception:
            pass

        # 1) 解析标注目录选择 (训练集/验证集/测试集/原始数据/对照测试/整个数据集) + 子目录
        from core.yolo_convert import load_classes
        from core.dataset_profiler import profile_dataset, find_labels_for_image_dir
        classes = load_classes(ds_path)
        sel = self._splits.get(self._split_var.get(), "ALL")
        sub = self._subs.get(self._sub_var.get()) if getattr(self, "_subs", None) else None
        jobs = []   # [(images_dir, labels_dir)] — 需要转换 YOLO 标注的目录对
        if sel == "ALL":
            if isinstance(sub, Path):
                open_dir = sub
                lab = find_labels_for_image_dir(sub)
                if lab:
                    jobs.append((sub, lab))
            else:
                open_dir = ds_path
                try:
                    prof = profile_dataset(ds_path)
                    for sp in prof.splits if prof else []:
                        if sp.labels_dir and sp.label_format in ("yolo", "mixed"):
                            jobs.append((sp.images_dir, sp.labels_dir))
                except Exception:
                    pass
        else:
            if isinstance(sub, Path):
                open_dir = sub
                lab = find_labels_for_image_dir(sub)
                if lab is None and sel.labels_dir and sel.label_format in ("yolo", "mixed"):
                    lab = sel.labels_dir
                if lab:
                    jobs.append((sub, lab))
            else:
                open_dir = sel.images_dir
                if sel.labels_dir and sel.label_format in ("yolo", "mixed"):
                    jobs.append((sel.images_dir, sel.labels_dir))

        # 2) 后台转换 YOLO txt -> labelme json (标注痕迹可见), 完成后自动启动
        def _convert_jobs():
            from core.yolo_convert import sync_yolo_to_labelme
            total = conv = 0
            for img_d, lab_d in jobs:
                st = sync_yolo_to_labelme(img_d, lab_d, classes)
                total += st["total"]
                conv += st["converted"]
            return total, conv

        def _finish(total, conv):
            self._status.config(
                text=f"YOLO 标注就绪 ({conv}/{total} 转换) -> 启动标注工具..." if total else
                     "启动标注工具...")
            if conv > 0:
                audit("tool_launch", f"YOLO标注转换 {conv}/{total}", module="dataset_page")
            # classes.txt 兜底 (写入打开的目录, 供标注工具读取类别)
            cls_path = open_dir / "classes.txt"
            if not cls_path.exists() and classes:
                try:
                    cls_path.write_text("\n".join(classes) + "\n", encoding="utf-8")
                except Exception:
                    pass
            # 启动 X-AnyLabeling (打开精确目录: 与下方选择保持一致)
            img_abs = str(open_dir.resolve())
            try:
                # 用 pythonw 启动, 不弹命令行窗口 (xanylabeling.EXE 是控制台程序)
                # 关键: pythonw 无控制台 (sys.stdout/stderr=None), X-AnyLabeling
                # 启动时会写日志横幅, None 流会导致进程启动数秒后静默崩溃,
                # 因此必须把子进程 stdout/stderr 重定向到 runs/logs/anylabeling.log。
                import anylabeling as _al
                pyw = str(Path(sys.executable).with_name("pythonw.exe"))
                app_py = str(Path(_al.__file__).resolve().parent / "app.py")
                al_log = _cfg.log_dir / "anylabeling.log"
                al_log.parent.mkdir(parents=True, exist_ok=True)
                log_fh = open(al_log, "a", encoding="utf-8", buffering=1)
                if Path(pyw).exists() and Path(app_py).exists():
                    args = [pyw, app_py, "--no-auto-update-check", "--filename", img_abs]
                    if classes:
                        args += ["--labels", ",".join(classes)]
                    self._tool_proc = subprocess.Popen(
                        args, stdout=log_fh, stderr=log_fh,
                        creationflags=subprocess.DETACHED_PROCESS
                        if sys.platform == "win32" else 0)
                else:
                    exe = (shutil.which("xanylabeling") or shutil.which("x-anylabeling")
                           or shutil.which("anylabeling"))
                    if not exe:
                        raise FileNotFoundError("标注工具未安装")
                    args = [exe, "--filename", img_abs]
                    if classes:
                        args += ["--labels", ",".join(classes)]
                    self._tool_proc = subprocess.Popen(
                        args, stdout=log_fh, stderr=log_fh,
                        creationflags=subprocess.DETACHED_PROCESS
                        if sys.platform == "win32" else 0)
                try:
                    log_fh.close()  # 子进程已持有自己的句柄
                except Exception:
                    pass
                self._status.config(
                    text=f"X-AnyLabeling 启动中: 数据集 [{ds_name}] (正在加载目录...)")
                self._tool_opened_dir = str(open_dir.resolve())
                self._append_log(
                    f">> 标注工具打开目录: {img_abs}\n", "hdr")
                self._append_log(
                    f">> 标注工具输出日志: {al_log} (启动异常时排查此处)\n", "hdr")
                audit("tool_launch",
                      f"启动标注工具 -> 数据集 {ds_name} 目录 {img_abs} 日志 {al_log}",
                      module="dataset_page",
                      extra={"var": self._ds_var.get(), "widget": self._ds_cb.get(),
                             "splits_ds": self._splits_ds,
                             "split": self._split_var.get()})
                self._watch_tool_proc(self._tool_proc, ds_name, img_abs)
            except Exception as ex:
                self._status.config(text=f"启动失败: {ex}")
                audit("tool_launch", f"启动失败: {ex}", level="error", module="dataset_page")

        # 立即启动标注工具 (不等待标注转换), 转换放后台并行进行
        # 此前实现是先转换完 (跌倒数据集 5000+ 张需数十秒到数分钟) 才弹窗,
        # 表现为"点击启动后没反应"。
        _finish(0, 0)
        # 后台准备标注 (JSON imagePath 修正 + YOLO->JSON 转换), 不阻塞启动
        if jobs:
            self._status.config(
                text="YOLO 标注后台转换中 -> 标注工具已启动 (转换完成即可见标注痕迹)")

        def _prepare_labels():
            # 1) 修正 json 内 imagePath 为纯文件名 (保证标注痕迹与图片一一对应)
            try:
                import json as _json
                for jf in open_dir.rglob("*.json"):
                    try:
                        d = _json.loads(jf.read_text(encoding="utf-8"))
                        imp = d.get("imagePath", "")
                        if os.path.isabs(imp) or "\\" in imp or ":" in imp:
                            d["imagePath"] = os.path.basename(imp)
                            jf.write_text(
                                _json.dumps(d, ensure_ascii=False, indent=2),
                                encoding="utf-8")
                    except Exception:
                        pass
            except Exception:
                pass
            # 2) YOLO -> labelme JSON 增量转换
            total = conv = 0
            try:
                from core.yolo_convert import sync_yolo_to_labelme
                for img_d, lab_d in jobs:
                    st = sync_yolo_to_labelme(img_d, lab_d, classes)
                    total += st["total"]
                    conv += st["converted"]
            except Exception as ex:
                audit("tool_launch_convert", f"后台标注转换异常: {ex}",
                      level="warn", module="dataset_page")
                return
            if conv > 0:
                audit("tool_launch_convert", f"后台标注转换 {conv}/{total}",
                      module="dataset_page")
            try:
                self.after(0, lambda: self._status.config(
                    text=f"YOLO 标注就绪 ({conv}/{total} 转换) -> 标注工具运行中"
                    if total else "标注工具运行中"))
            except Exception:
                pass

        threading.Thread(target=_prepare_labels, daemon=True,
                         name="yolo-convert").start()

    def _watch_tool_proc(self, proc, ds_name, img_abs):
        """后台看护标注工具进程: 实时反馈 加载中/已就绪/退出原因, 不再无声等待"""
        def _watch():
            try:
                self._append_log(
                    f">> 标注工具加载中: {img_abs} (大目录首次打开可能需 1-3 分钟)\n", "hdr")
                self._q.put(("tool_status",
                             f"X-AnyLabeling 启动中: 数据集 [{ds_name}] (加载目录可能需要一段时间)",
                             "info"))
            except Exception:
                pass
            try:
                import subprocess as _sp
                import time as _t
                pid = proc.pid
                for _ in range(90):  # 最长约 7.5 分钟
                    if proc.poll() is not None:
                        code = proc.returncode
                        self._q.put(("tool_status",
                                     f"✗ 标注工具已退出 (码 {code}), 请查看 runs/logs/anylabeling.log",
                                     "danger"))
                        audit("tool_exit", f"标注工具退出 码={code} 目录 {img_abs}",
                              level="warn", module="dataset_page")
                        return
                    title = ""
                    try:
                        r = _sp.run(
                            ["powershell", "-NoProfile", "-Command",
                             f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue).MainWindowTitle"],
                            capture_output=True, timeout=10,
                            creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
                        title = (r.stdout or b"").decode("utf-8", "ignore").strip()
                    except Exception:
                        pass
                    # 标题出现 "[1/N]" 表示首图已加载, 工具就绪
                    if title and "[" in title:
                        self._q.put(("tool_status",
                                     f"✔ 标注工具已就绪: 数据集 [{ds_name}] "
                                     f"({title.split(' - ')[-1][:40]})", "ok"))
                        audit("tool_ready", f"标注工具就绪: {title}", module="dataset_page")
                        return
                    elif title:
                        # 窗口已出现但图片仍在加载 (大目录), 给用户正向反馈
                        self._q.put(("tool_status",
                                     f"✔ 标注工具已启动: 窗口已出现 (正在加载图片: 数据集 [{ds_name}])",
                                     "ok"))
                    _t.sleep(5)
                self._q.put(("tool_status",
                             f"标注工具仍在加载: 数据集 [{ds_name}] (大目录较慢, 详见 anylabeling.log)",
                             "warn"))
            except Exception:
                pass
        threading.Thread(target=_watch, daemon=True, name="tool-watch").start()

    def _start_ai(self, e=None):
        m = self._ai_mdl.get()
        if not m or m == "(请先训练模型)":
            messagebox.showwarning("提示", "请先选择AI预标注模型 (.pt 推理文件)", parent=self)
            return
        ds_name = self._ds_var.get()
        if ds_name not in self._ds_map:
            messagebox.showwarning("提示", "请先选择数据集", parent=self)
            return
        ds_info = self._ds_map[ds_name]
        model_path = Path(_cfg.output_dir) / m
        if not model_path.exists():
            model_path = Path(m)
        if not model_path.exists():
            messagebox.showwarning("提示", f"模型不存在: {model_path}", parent=self)
            return
        img_dir = _find_image_dir(ds_info.path)
        if not img_dir.exists():
            messagebox.showwarning("提示", f"图片目录不存在: {img_dir}", parent=self)
            return

        existing = list(img_dir.glob("*.json"))
        overwrite = False
        if existing:
            if messagebox.askyesno("覆盖确认", f"已有 {len(existing)} 个标注文件, 是否覆盖?",
                                   parent=self):
                overwrite = True
                for f in img_dir.glob("*.json"):
                    try:
                        f.unlink()
                    except Exception:
                        pass
                label_dir = img_dir.parent / "labels"
                if label_dir.exists():
                    for f in label_dir.glob("*.txt"):
                        try:
                            f.unlink()
                        except Exception:
                            pass
            else:
                return

        self._btn_ai.config(state="disabled")
        self._append_log(f">> AI标注开始: 模型={model_path.name} 数据集={ds_name} 置信度={self._ai_conf.get()}\n", "hdr")
        self._set_pct(0, "AI标注中... 0/0")
        audit("ai_annotate", f"模型={model_path.name} 数据集={ds_name}", module="dataset_page")
        self._ann_q = _queue.Queue()
        self._ann_thread, self._ann_result = run_auto_annotate(
            str(model_path), str(img_dir), self._ai_conf.get(), self._kp_var.get(),
            self._parse_device(self._ai_dev.get()),
            progress_cb=lambda cur, total, name: self._q.put(("ai_progress", cur, total, name)),
            overwrite=overwrite)
        self.after(200, self._poll_ai)

    @staticmethod
    def _parse_device(val: str) -> str:
        """把设备下拉框显示名 (如 'GPU 0: NVIDIA GeForce RTX 3050 T') 转为
        Ultralytics 设备字符串 (如 '0'); cpu 原样返回。"""
        v = str(val or "").strip()
        if v.startswith("GPU"):
            return v.split(":")[0].replace("GPU", "").strip() or "0"
        return v or "cpu"

    def _poll_ai(self):
        if not hasattr(self, "_ann_result"):
            return
        r = self._ann_result
        if r.get("done", False):
            try:
                self._btn_ai.config(state="normal")
            except Exception:
                pass
            ok = r.get("success", 0)
            fail = r.get("failed", 0)
            total = r.get("total", 0)
            self._set_pct(100 if fail == 0 else max(0, int(ok / max(1, total) * 100)),
                          f"AI标注完成: {ok}成功, {fail}失败")
            self._append_log(f">> AI标注完成: 共{total}张, 成功{ok}, 失败{fail}\n",
                             "ok" if fail == 0 else "warn")
            audit("ai_annotate", f"完成: {ok}/{total} 成功, {fail} 失败",
                  level="warn" if fail else "info", module="dataset_page")
            messagebox.showinfo("完成", f"AI标注完成: {ok}成功, {fail}失败", parent=self)
        else:
            try:
                # AI 标注进行中: 保持快速轮询; 页面在后台/空闲时由 poll_kit 退避
                self.after(_pk.poll_delay(self, True, 200), self._poll_ai)
            except Exception:
                pass

    def _refresh_ai_models(self, select=None):
        """重新扫描 runs/outputs 下的 .pt 并刷新下拉框"""
        try:
            out = Path(_cfg.output_dir)
            pts = [f.name for f in sorted(out.glob("*.pt"), key=lambda p: p.stat().st_mtime,
                                          reverse=True)] if out.exists() else []
            if not pts:
                pts = ["(请先训练模型)"]
            self._ai_cb["values"] = pts
            if select and select in pts:
                self._ai_mdl.set(select)
            elif self._ai_mdl.get() not in pts:
                self._ai_mdl.set(pts[0])
        except Exception:
            pass

    def _delete_model(self, e=None):
        name = self._ai_mdl.get()
        if not name or name == "(请先训练模型)":
            messagebox.showinfo("提示", "当前没有可删除的模型", parent=self)
            return
        path = Path(_cfg.output_dir) / name
        if not path.exists():
            messagebox.showwarning("提示", f"模型文件不存在: {path}", parent=self)
            return
        if not messagebox.askyesno("删除模型", f"确定删除模型 {name} 吗？\n该操作不可恢复。", parent=self):
            return
        try:
            path.unlink()
            for suf in (".md5",):
                m = path.with_suffix(path.suffix + suf)
                if m.exists():
                    m.unlink()
            self._append_log(f">> 已删除模型: {name}\n", "ok")
            audit("pt_delete", f"删除模型 {name}", module="dataset_page")
            self._refresh_ai_models()
        except Exception as ex:
            self._append_log(f">> 删除模型失败: {ex}\n", "err")
            audit("pt_delete_failed", f"{name}: {ex}", level="error", module="dataset_page")

    def _open_model_manager(self, e=None):
        """打开独立模型库管理窗口 (列表/删除/拖拽导入/浏览导入/打开文件夹)"""
        try:
            from gui.pt_manager import ModelManagerDialog
            dlg = ModelManagerDialog(self, on_changed=lambda: self._refresh_ai_models())
            dlg.grab_set()
            audit("pt_manager_open", "打开模型库管理窗口", module="dataset_page")
        except Exception as ex:
            self._append_log(f">> 打开模型库失败: {ex}\n", "err")
            audit("pt_manager_open_failed", str(ex), level="error", module="dataset_page")

    def _on_drop_pt(self, event):
        try:
            from utils.file_utils import parse_dnd_paths
            paths = parse_dnd_paths(event.data)
        except Exception:
            paths = [event.data]
        out = Path(_cfg.output_dir)
        try:
            out.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        added = []
        for fp in paths:
            fp = Path(fp)
            if fp.suffix.lower() != ".pt":
                self._append_log(f">> 跳过非 .pt 文件: {fp.name}\n", "warn")
                continue
            try:
                dst = out / fp.name
                if dst.resolve() == fp.resolve():
                    added.append(dst.name)
                    continue
                shutil.copy2(str(fp), str(dst))
                added.append(dst.name)
            except Exception as ex:
                self._append_log(f">> 导入失败 {fp.name}: {ex}\n", "err")
        if added:
            self._append_log(f">> 已导入模型: {', '.join(added)}\n", "ok")
            audit("pt_import", f"拖拽导入模型: {', '.join(added)}", module="dataset_page")
            self._refresh_ai_models(select=added[0])
        else:
            self._status.config(text="未导入任何 .pt 模型")

    # ═══════ 跨页事件 (emit 可能来自后台线程, 一律 after(0) 回主线程) ═══════
    def _on_state_scanned(self, count=0, **_kw):
        """数据集清单刷新广播 (来自 dataset_state.ensure_scan 的后台线程)。"""
        def _apply():
            self._ds_map = ds_state.get_map()
            if count is None or count < 0:
                try:
                    self._ds_info.config(text="正在扫描数据集...")
                except Exception:
                    pass
                return
            self._refresh_datasets()
        try:
            self.after(0, _apply)
        except Exception:
            pass

    def _on_models_changed(self, **_kw):
        """训练页导出新模型 -> 刷新本页 .pt 下拉。"""
        try:
            self.after(0, self._refresh_ai_models)
        except Exception:
            pass

    def on_show(self):
        """切到本页: 清单可能已被别的页扫过/刷新过, 重新铺一次。"""
        try:
            if ds_state.get_map():
                self._ds_map = ds_state.get_map()
                self._refresh_datasets()
            else:
                ds_state.ensure_scan()
            self.after(140, self._ds_show_preview)
            self.after(170, self._annot_refresh)
        except Exception:
            pass

    def destroy(self):
        """退订 —— 页面被销毁后回调再触发会 Invalid command name。"""
        try:
            _b = ds_state.bus()
            _b.unsubscribe(ds_state.EV_SCANNED, self._on_state_scanned)
            _b.unsubscribe(ds_state.EV_MODELS_CHANGED, self._on_models_changed)
        except Exception:
            pass
        super().destroy()
