# -*- coding: utf-8 -*-
"""「模型训练」页 — 数据集核对 / 参数配置 / 开始训练 / 训练日志

2026-09-11 由 training_page.py 拆出 (v0.26.0)。
数据集的选择与标注工具已迁到「数据集与标注」页; 本页只负责"拿当前选定的数据集跑训练"。

## 跨页契约

当前数据集**不在本页存副本**, 一律从 `infrastructure.dataset_state` 读:
  - 订阅 EV_CHANGED -> 摘要行跟着刷新;
  - 训练完成 emit EV_MODELS_CHANGED -> 标注页刷新 .pt 下拉。
"""
from __future__ import annotations
from gui import poll_kit as _pk
import tkinter as tk, threading
import queue as _queue
from tkinter import ttk, messagebox
from pathlib import Path
from infrastructure.config import get_config, get_theme
_tc = get_theme().colors
from infrastructure import dataset_state as ds_state
from core.trainer import build_model_list, prepare_training_yaml, run_training
from infrastructure.audit import audit
# ⚠ 2026-10-03: `step_card` **必须模块级导入**。
#   原先只在 `__init__` 里 `from gui.components import ...` —— 构造期用没问题,
#   但 `_build_summary_card()` 是**实例方法**, 它的作用域里没有那个局部名字 ⇒
#   `NameError: name 'step_card' is not defined`, 而 `App.show_page` 会把这类
#   异常吞掉换成占位 Frame（"假绿"）, 页面看着在、内容全无。
#   判据: `scripts/ui_page_geom.py` 输出的 `page build training failed:` 一行 ——
#   那是唯一的证据, 别只看到"页面在"就以为通过。
from gui.components import step_card as _step_card
# ★ 2026-10-03: 摘要区改用设计令牌排版 (原先整页都是手写像素值)。
#   令牌真源 = src/gui/ui_tokens.py; 这里只取需要的几档。
from gui.ui_tokens import SPACE, TYPE
SPACE_XXS = SPACE["xxs"]  # 2   参数行微距
SPACE_XS = SPACE["xs"]    # 4   行内微距
SPACE_SM = SPACE["sm"]    # 8   列间距
SPACE_MD = SPACE["md"]    # 12  卡片内边距

_cfg = get_config()

# 参数行标签 -> run_training 参数键
_KEY_MAP = {"模型": "model", "轮数": "epochs", "批次": "batch", "尺寸": "imgsz",
            "学习率": "lr0", "lr因子": "lrf", "动量": "momentum", "权重衰减": "weight_decay",
            "预热": "warmup_epochs", "优化器": "optimizer", "早停": "patience"}
_INT_KEYS = {"epochs", "batch", "imgsz", "patience", "warmup_epochs"}
_FLOAT_KEYS = {"lr0", "lrf", "momentum", "weight_decay"}


class TrainingPage(tk.Frame):

    def __init__(self, parent, controller=None):
        super().__init__(parent, bg=_tc.bg)
        self.controller = controller
        C = _tc.card_bg; T = _tc.text; S = _tc.text_secondary; P = _tc.primary

        from gui.components import (topbar, sub_card, btn, scrollable, step_card,
                                    autowrap)
        topbar(self, "🎓", "模型训练", "核对数据集 | 参数配置 | 开始训练 | 训练日志",
               actions=[("📚 模型库管理", self._open_model_manager, "primary")])
        tk.Frame(self, height=1, bg=_tc.border).pack(fill="x", padx=24, pady=(0, 8))

        # ══ 两列 (2026-10-03 按功能重排, v0.34.0) ══
        #   左 = 核对 · 定宽 380: ① 训练数据集 → ② 训练设置与检查 → ③ 参数摘要
        #   右 = 执行 · 弹性:      ② 训练工作台 → ⑤ 训练输出日志(吃满剩余)
        #   分栏依据 = "跑之前要看/要改的" vs "跑本身 + 跑出来的东西"。
        #   左列此前只放一张卡 ⇒ 347/765 (55% 空); 现由三张卡填实。
        #   右列此前 461+296 挤满 ⇒ 摘要搬走后日志能拿到全部余量。
        body = tk.Frame(self, bg=_tc.bg)
        body.pack(fill="both", expand=True, padx=24, pady=(0, 12))
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=0, minsize=380)
        body.columnconfigure(1, weight=1, minsize=560)

        left_outer, left = scrollable(body, bg=_tc.bg)
        left_outer.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left_outer.grid_propagate(False)
        left_outer.config(width=380)

        right = tk.Frame(body, bg=_tc.bg)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self._train_right = right

        # ══════ 左列 ① 训练数据集 (只读核对; 选择权在「数据集与标注」页) ══════
        # ★ 2026-10-03 按功能重排 (v0.34.0):
        #   原先左列**只有这一张卡**、还是 `stretch=True` + `fill="both", expand=True`
        #   —— 卡片只顾自己长，但左列本身在 765px 高里只用了 **347px (45%)**,
        #   底下 418px 全是页面背景色, 看着像"内容没加载出来"(实测 `scripts/ui_page_geom.py`)。
        #   左列本来缺的是"内容", 不是"拉伸", 所以这里**去掉 stretch**:
        #   卡随内容自然高即可, 多出来的高度留给下面新增的两块。
        cd, cdb = step_card(left, 1, "训练数据集", "只读 · 在「数据集与标注」页选择")
        cd.pack(fill="x")
        self._ds_name_lbl = tk.Label(cdb, text="—", font=("Microsoft YaHei UI", 12, "bold"),
                                     bg=C, fg=T, anchor="w", justify="left")
        self._ds_name_lbl.pack(fill="x", padx=12, pady=(8, 2))
        self._ds_stat_lbl = tk.Label(cdb, text="", font=("Microsoft YaHei UI", 9),
                                     bg=C, fg=S, anchor="w", justify="left")
        self._ds_stat_lbl.pack(fill="x", padx=12, pady=(0, 4))
        autowrap(self._ds_stat_lbl, min_px=120)
        self._ds_ready_lbl = tk.Label(cdb, text="", font=("Microsoft YaHei UI", 9, "bold"),
                                      bg=C, fg=S, anchor="w", justify="left")
        self._ds_ready_lbl.pack(fill="x", padx=12, pady=(0, 8))
        _dsrow = tk.Frame(cdb, bg=C); _dsrow.pack(fill="x", padx=12, pady=(0, 8))
        self._btn_goto_ds = btn(_dsrow, "  🗂 去选择数据集 ", self._goto_dataset, "border")
        self._btn_goto_ds.pack(side="left", padx=(0, 8))
        self._btn_rescan = btn(_dsrow, "  🔄 重新扫描 ", self._rescan_datasets, "ghost")
        self._btn_rescan.pack(side="left")

        # ══════ 左列 ② 训练设置与自检 ══════
        # ★ 2026-10-03 从上面那张卡里拆出来独立成卡。
        #   理由（按功能排列）: 「训练数据集」讲的是**要用哪份数据**,
        #   而设备/检查讲的是**用什么跑、跑之前要确认什么** —— 两件事不同族,
        #   挤在一张卡里既让那张卡过高、又让左列没有分区可读性。
        #   拆完后左列 = ①数据 → ②设备与检查 → ③参数摘要, 三块各司其职。
        cd2, cd2b = step_card(left, 2, "训练设置与检查", "设备 / 训练前自检清单")
        cd2.pack(fill="x", pady=(8, 0))

        # 训练设备
        dev_row = tk.Frame(cd2b, bg=C); dev_row.pack(fill="x", padx=12, pady=(4, 4))
        tk.Label(dev_row, text="训练设备", font=("Microsoft YaHei UI", 8), bg=C, fg=S).pack(anchor="w")
        _dr2 = tk.Frame(cd2b, bg=C); _dr2.pack(fill="x", padx=12, pady=(0, 12))
        self._train_dev = tk.StringVar(master=self, value="cpu")
        self._train_dev_cb = ttk.Combobox(_dr2, textvariable=self._train_dev, values=["cpu"],
                                          state="readonly", width=18, font=("Microsoft YaHei UI", 9))
        self._train_dev_cb.pack(side="left", fill="x", expand=True, padx=(0, 4))
        # ★ 2026-09-22：设备探测**不再在构造期起线程**。
        #   原因（实测 `tools/qc/perf_audit.py`）：主窗口启动后会**后台预建全部 13 页**，
        #   而本函数会 `import torch` ⇒ **一开机就 +296 MB 常驻、且不归还**，
        #   哪怕用户从来不训练。改到 `on_show`（真的切到本页）时才探：
        #   下拉仍会先用 `last_device.txt` 的缓存瞬时填好，观感不变。
        # （手动重试的 🔄 按钮仍然直接调 `_probe_devices_async`，见下面那行。）
        self._btn_train_refresh = tk.Label(_dr2, text="🔄", font=("Microsoft YaHei UI", 9),
                                           bg=C, fg=_tc.primary, cursor="hand2")
        self._btn_train_refresh.pack(side="left")
        self._btn_train_refresh.bind(
            "<Button-1>",
            lambda e: self._probe_devices_async("_train_dev_cb", self._train_dev))
        # 就绪清单 (把"训练前该看什么"讲清楚)
        tk.Label(cd2b, text="训练前检查", font=("Microsoft YaHei UI", 8, "bold"), bg=C, fg=S).pack(
            anchor="w", padx=12, pady=(8, 2))
        self._chk_lbl = tk.Label(cd2b, text="", font=("Microsoft YaHei UI", 9), bg=C, fg=S,
                                 anchor="w", justify="left")
        self._chk_lbl.pack(fill="x", padx=12, pady=(0, 12))

        # ══════ 左列 ③ 参数摘要 (实时汇总; 2026-10-03 从右列搬来) ══════
        # ★ 为什么搬到左列: 参数摘要**不改布局也不影响训练**, 属于"跑之前看一眼"
        #   的核对信息 —— 和左列「核对」的定位同族。而右列当时只有 722px 宽、
        #   摘要还要分 2 列 (各 341px), 术语挤在一起;
        #   左列 391px 单列竖排反而更清爽, 且正好补上左列的 418px 空洞。
        #   右列让出来的高度全部给日志 (日志原先被压到 296px)。
        self._sum_card, self._sum_grid = self._build_summary_card(left)
        self._sum_card.pack(fill="x", pady=(8, 0))

        # ══════ 右列 ② 训练工作台 (参数 chips + 主操作 + 大进度) ══════
        c4, c4b = sub_card(right, "⚙️ 训练工作台")
        c4.pack(fill="x", pady=(0, 8))
        self._pvars = {}
        models = build_model_list()
        rows = [
            [("模型", _cfg.training("default_model", "yolo11n.pt"), "c", models[:25]),
             ("轮数", str(_cfg.training("epochs", 100)), "e"), ("批次", str(_cfg.training("batch", 16)), "e"),
             ("尺寸", str(_cfg.training("imgsz", 640)), "e")],
            [("学习率", str(_cfg.training("lr0", 0.01)), "e"), ("lr因子", str(_cfg.training("lrf", 0.01)), "e"),
             ("动量", str(_cfg.training("momentum", 0.937)), "e"),
             ("权重衰减", str(_cfg.training("weight_decay", 0.0005)), "e")],
            [("预热", str(_cfg.training("warmup_epochs", 3)), "e"),
             ("优化器", _cfg.training("optimizer", "auto"), "c", ["auto", "SGD", "Adam", "AdamW", "RMSProp"]),
             ("早停", str(_cfg.training("patience", 50)), "e")],
        ]
        _adv = None
        for ri, row_data in enumerate(rows):
            if ri == 1:
                from gui.components import collapse_group
                _adv, _adb, _tg = collapse_group(c4b, "高级参数 (学习率/动量/优化器/早停…)",
                                                 icon="⚙", open_default=False)
                _adv.pack(fill="x", padx=12)
            parent = c4b if ri == 0 else _adb
            chiprow = tk.Frame(parent, bg=_tc.bg)
            chiprow.pack(fill="x", padx=12, pady=(4 if ri == 0 else 0, 2))
            for ci, (lbl, dflt, ptype, *rest) in enumerate(row_data):
                opts = rest[0] if rest else []
                cell = tk.Frame(chiprow, bg=_tc.bg)
                # ⚠ 2026-10-03: 这里原先是「列首 6px / 其余 2px」的三元 padx ——
                #   `6` 是离格半档, 但它是**三元表达式**, ui_scale_tokens.py 的
                #   正则只吃字面数字 ⇒ 扫不到它 (验收时被"离格=0"漏过)。
                #   手改成令牌: 列首 8 (sm) / 其余 2 (xxs)。
                #   注: 注释里不写回原字面量, 否则扫描工具会把注释当代码报假阳性。
                cell.pack(side="left", fill="x", expand=True,
                          padx=(SPACE_SM if ci == 0 else SPACE_XXS, SPACE_XXS), pady=SPACE_XXS)
                tk.Label(cell, text=lbl, font=("Microsoft YaHei UI", 8), bg=_tc.bg, fg=S).pack(anchor="w", padx=(2, 0))
                if ptype == "c":
                    var = tk.StringVar(master=self, value=dflt)
                    ttk.Combobox(cell, textvariable=var, values=opts, state="readonly",
                                 font=("Microsoft YaHei UI", 9)).pack(fill="x", padx=(2, 8))
                    self._pvars[lbl] = var
                else:
                    var = tk.StringVar(master=self, value=dflt)
                    tk.Entry(cell, textvariable=var, font=("Microsoft YaHei UI", 9), bg=_tc.input_bg,
                             fg=T, insertbackground=T, relief="flat",
                             highlightthickness=1, highlightbackground=_tc.border,
                             highlightcolor=P).pack(fill="x", padx=(2, 8), ipady=4)
                    self._pvars[lbl] = var

        # 主操作行 + 大号训练进度条
        bl = tk.Frame(c4b, bg=C); bl.pack(fill="x", padx=12, pady=(8, 2))
        self._btn_train = btn(bl, "  ▶ 开始训练 ", self._start_train, "primary", big=True)
        self._btn_train.pack(side="left", padx=(0, 8))
        self._btn_reset = btn(bl, "  🔄 恢复默认参数 ", self._reset_params, "ghost")
        self._btn_reset.pack(side="left", padx=(0, 8))
        self._btn_stop = btn(bl, "  ⏹ 停止 ", self._stop_train, "border")
        self._btn_stop.pack(side="left")
        self._train_prog = ttk.Progressbar(c4b, maximum=100)
        self._train_prog.pack(fill="x", padx=12, pady=(0, 12))
        self._train_prog.pack_forget()   # v0.23.4 训练中才显示

        # ══════ 右列 ③ 统一训练日志区 (百分比 + 日志) ══════
        def _bar(bar):
            self._pct_lbl = tk.Label(bar, text="0%", font=("Microsoft YaHei UI", 12, "bold"),
                                     bg=_tc.card_bg, fg=_tc.primary)
            self._pct_lbl.pack(side="right", padx=(8, 12))
        log_card, lc_body = sub_card(right, "📜 训练输出（训练日志）", right=_bar)
        log_card.pack(fill="both", expand=True)
        self._status = tk.Label(lc_body, text="就绪 — 核对左侧数据集后点击开始训练",
                                font=("Microsoft YaHei UI", 9),
                                bg=_tc.card_bg, fg=_tc.text_secondary, anchor="w")
        self._status.pack(fill="x", padx=12, pady=(0, 4))
        self._train_console = tk.Text(lc_body, font=("Consolas", 9), bg=_tc.log_bg, fg=_tc.log_text,
                                      relief="flat", bd=0, padx=8, pady=8,
                                      highlightthickness=1, highlightbackground=_tc.border)
        self._train_console.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self._train_console.configure(state="disabled")
        for tag, color in (("err", _tc.danger), ("ok", _tc.success), ("warn", _tc.warning),
                           ("metric", _tc.text_secondary), ("hdr", _tc.accent)):
            self._train_console.tag_configure(tag, foreground=color)

        # 后台状态
        self._q = _queue.Queue()
        self._cancel_train = threading.Event()
        self._train_thread = None
        self._btn_mdl_mgr_top = None   # 顶栏统一承载右侧动作

        # ── 跨页订阅: 数据集选择变化 -> 刷新左侧核对卡 ──
        try:
            ds_state.bus().subscribe(ds_state.EV_CHANGED, self._on_ds_changed_evt)
        except Exception:
            pass
        if not ds_state.get_map():
            ds_state.ensure_scan()

        self.after(400, self._train_summary)
        self._poll_q()

    def _build_summary_card(self, parent):
        """参数摘要卡 (左列 ③) — 返回 (card, grid)。

        ★ 2026-10-03 从"右列挤 2 列"改为"左列单列竖排" (v0.34.0)。
        右列只有 722px 宽, 摘要再分 2 列 ⇒ 每列 341px, 「权重衰减 0.0005」
        这种长词条几乎贴到列边; 而左列 391px 单列反而有富余。
        结构: 顶行 = 数据集 + 张数 + 就绪 (跨列), 下面 = 「训练结构」「优化超参」
        两个分组标题 + 各自条目, **单列一列到底**。
        """
        C = _tc.card_bg
        card, body = _step_card(parent, 3, "参数摘要", "当前将用于训练的真实取值")
        grid = tk.Frame(body, bg=C)
        grid.pack(fill="x", padx=SPACE_MD, pady=(SPACE_XS, SPACE_MD))
        grid.columnconfigure(0, weight=1)
        # 顶行: 数据集 + 张数 + 就绪 (一行说完, 用 success/warning 上色)
        # ⚠ 2026-10-03: 左列净宽只有 391-24(卡内边距) ≈ 340px, 而这一行文案
        #   ("📚 fall_all_v1 · 图片 1638 张 / 标注 2842 个 · ✅ 可开始训练")
        #   实测需要 400+px ⇒ **截屏里被硬切在 "✅ 可开"**。
        #   Label 不会自动折行, 所以必须自己开 `wraplength`; 但左列宽度是布局
        #   算出来的, 写死一个数会在别的窗口宽度下重新出错。
        #   解法: 绑 `<Configure>` 按**实际宽度**更新 `wraplength`
        #   （比 autowrap 的固定 min_px 更准 —— 这里宽度会随窗口变）。
        self._sum_head = tk.Label(grid, text="", font=TYPE["label"],
                                  bg=C, fg=_tc.text_secondary, anchor="w", justify="left")
        self._sum_head.grid(row=0, column=0, sticky="ew", pady=(0, SPACE_SM))
        self._sum_head.bind(
            "<Configure>",
            lambda e: e.widget.config(wraplength=max(80, e.width - 4)))
        self._sum_lbl = self._sum_head        # 兼容旧引用
        self._sum_cells = []
        # 单列: (分组标题, 4) + (分组标题, 7) 依次往下排
        # ⚠ 这里刻意**不再用 uniform + 双列** —— 单列下 weight=1 已足够。
        row = 1
        for title, n in (("训练结构", 4), ("优化超参", 7)):
            hd = tk.Label(grid, text=title, font=TYPE["label_bold"],
                          bg=C, fg=_tc.text_muted, anchor="w")
            hd.grid(row=row, column=0, sticky="ew",
                    pady=(SPACE_MD if row > 1 else SPACE_XS, SPACE_XXS))
            row += 1
            for _i in range(n):
                lb = tk.Label(grid, text="", font=TYPE["label"],
                              bg=C, fg=_tc.text_secondary, anchor="w")
                lb.grid(row=row, column=0, sticky="ew", pady=SPACE_XXS)
                self._sum_cells.append((0, lb))
                row += 1
        return card, grid

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
                    self._train_prog.pack(fill="x", padx=12, pady=(0, 12))
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
                            try:
                                (_cfg.output_dir.parent / "last_device.txt").write_text(
                                    names[0], encoding="utf-8")
                            except Exception:
                                pass
                        elif var.get() not in names:
                            var.set(names[0])
                    try:
                        from infrastructure.audit import audit
                        audit("device_probe",
                              f"设备列表={names} 默认={var.get()} torch_ok={torch_ok}",
                              module="training_page")
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

    @staticmethod
    def _parse_device(val: str) -> str:
        """把设备下拉框显示名 (如 'GPU 0: NVIDIA GeForce RTX 3050 T') 转为
        Ultralytics 设备字符串 (如 '0'); cpu 原样返回。"""
        v = str(val or "").strip()
        if v.startswith("GPU"):
            return v.split(":")[0].replace("GPU", "").strip() or "0"
        return v or "cpu"

    def _train_summary(self):
        """训练配置摘要刷新 (数据集/参数/就绪状态)"""
        try:
            info = ds_state.get_info()
            ds_txt = f"{Path(ds_state.get_name()).name}" if info else "未选择数据集 ⚠"
            p = self._pvars if hasattr(self, "_pvars") else {}
            vals = {k: v.get() for k, v in p.items()}
            # 2026-09-11 修: 原来读 getattr(self, "_model_cb") —— 这个属性
            # 从头到尾没被赋过值, 摘要行里的模型名永远是 "-"。改读参数变量。
            md = vals.get("模型", "-")
            dev = getattr(self, "_train_dev", None)
            ic = getattr(info, "image_count", 0)
            lc = getattr(info, "label_count", 0)
            ready = bool(info and ic > 0)
            # 首行: 数据集 + 张数/标注 + 就绪状态 (单列宽度够, 不再折行)
            self._sum_head.config(
                text=f"📚 {ds_txt}  ·  图片 {ic} 张 / 标注 {lc} 个  ·  "
                     f"{'✅ 可开始训练' if ready else '⚠ 先选择数据集'}",
                fg=(_tc.success if ready else _tc.warning))
            # ★ 2026-10-03 单列版: 所有条目按 (结构 4 项) + (超参 6 项 + 设备) 依次排。
            #   旧的双列版靠 `used[col]` 分流, 现在只有一列, 直接按序取池子即可。
            pool = [("模型", str(md)), ("轮数", str(vals.get("轮数", "?"))),
                    ("批次", str(vals.get("批次", "?"))), ("尺寸", str(vals.get("尺寸", "?"))),
                    ("学习率", str(vals.get("学习率", "?"))),
                    ("动量", str(vals.get("动量", "?"))),
                    ("权重衰减", str(vals.get("权重衰减", "?"))),
                    ("预热", str(vals.get("预热", "?"))),
                    ("优化器", str(vals.get("优化器", "?"))),
                    ("早停", str(vals.get("早停", "?"))),
                    ("设备", str(dev.get() if dev else "cpu"))]
            for k, (_col, lb) in enumerate(self._sum_cells):
                lb.config(text=(f"{pool[k][0]}  {pool[k][1]}" if k < len(pool) else ""))
        except Exception:
            pass

    def _start_train(self, e=None):
        if self._train_thread and self._train_thread.is_alive():
            messagebox.showwarning("提示", "训练正在进行中, 请先停止", parent=self)
            return
        ds_name = ds_state.get_name()
        ds_info = ds_state.get_info()
        if not ds_name or ds_info is None:
            messagebox.showwarning("提示", "请先选择数据集（「数据集与标注」页）", parent=self)
            return
        model_name = self._pvars["模型"].get()
        if not model_name:
            messagebox.showwarning("提示", "请选择训练模型", parent=self)
            return
        device = self._parse_device(self._train_dev.get())

        params = {}
        for lbl, var in self._pvars.items():
            key = _KEY_MAP.get(lbl, lbl)
            params[key] = var.get()
        try:
            for k in _INT_KEYS:
                params[k] = int(float(params[k]))
            for k in _FLOAT_KEYS:
                params[k] = float(params[k])
        except Exception:
            messagebox.showwarning("提示", "参数格式错误, 请检查数值", parent=self)
            return
        if params.get("epochs", 100) <= 0:
            messagebox.showwarning("提示", "训练轮数必须大于0", parent=self)
            return

        try:
            yaml_path = prepare_training_yaml(ds_info)
        except Exception as ex:
            messagebox.showerror("错误", f"数据集配置失败: {ex}", parent=self)
            return

        # ── 训练前守门 (2026-10-11) ──────────────────────────────────────────
        # 实测里最高频的训练失败成因是**标注内容坏掉**（字段数错 / 坐标没归一化 /
        # 类别 ID 越界），而 ultralytics 对这些的反馈要么指向别处（显存、超参），
        # 要么**静默当成零标签** —— 数据白训一遍才发现 mAP 恒 0。
        # ⇒ 训练前跑一次内容体检，**有致命问题就拦下来问**，别让它在几小时后才暴露。
        # ⚠ 这里只「问」不「改」：修复仍走路径明确的既有链路（原位规范化 / 标注编辑器），
        #   守门器自己动手改数据会造出第二套真源。
        try:
            from core.dataset_qc import audit_dataset, KIND_ZH
            _qc = audit_dataset(Path(ds_info.path))
        except Exception as ex:
            _qc = None
            self._append_log(f">> 训练前体检跳过（{ex}）\n", "warn")

        if _qc is not None and not _qc.ok:
            _detail = []
            for _s in _qc.splits:
                for _k, _v in _s.counts.items():
                    if _k in ("field_count", "non_numeric", "cls_negative", "cls_oorrange"):
                        _detail.append(f"  · [{_s.name}] {KIND_ZH.get(_k, _k)}：{_v} 处")
            self._append_log(
                f">> ⚠ 训练前体检：发现 {_qc.fatal_count} 类致命问题\n"
                + "".join(x + "\n" for x in _detail), "err")
            if not messagebox.askyesno(
                    "标注体检未通过",
                    f"数据集「{ds_name}」的标注内容有致命问题：\n\n"
                    + ("\n".join(_detail) if _detail else "（明细见日志）") +
                    "\n\n继续训练很可能直接失败，或训完 mAP 一直是 0。\n"
                    "建议先到「数据集与标注」页点「🔍 标注体检」看明细并修复。\n\n"
                    "确定仍要继续训练吗？",
                    icon="warning", parent=self):
                self._append_log(">> 已按用户选择取消训练\n", "warn")
                return
            self._append_log(">> 用户选择忽略体检问题，继续训练\n", "warn")

        self._cancel_train = threading.Event()
        self._btn_train.config(state="disabled", text="  训练中...")
        self._append_log(f">> 训练开始: 模型={model_name} 数据集={ds_name} 轮数={params['epochs']} "
                         f"设备={device}\n", "hdr")
        self._set_pct(0, "训练准备中...")
        audit("training_start", f"模型={model_name} 数据集={ds_name} 设备={device}", module="training_page")

        def _do():
            try:
                ok = run_training(
                    model_name, yaml_path, params, device,
                    log_cb=lambda line, tag="metric": self._q.put(("log", "  " + line + "\n", tag)),
                    progress_cb=lambda pct, txt: self._q.put(("pct", pct, "训练: " + txt)),
                    cancel_event=self._cancel_train)
                self._q.put(("train_done", ok, ""))
            except Exception as ex:
                self._q.put(("log", f">> 训练异常: {ex}\n", "err"))
                self._q.put(("train_done", False, str(ex)))
        self._train_thread = threading.Thread(target=_do, daemon=True)
        self._train_thread.start()

    def _stop_train(self, e=None):
        if self._train_thread and self._train_thread.is_alive():
            self._cancel_train.set()
            self._append_log(">> 已发送停止请求...\n", "warn")
            audit("training_stop", "用户请求停止训练", module="training_page")
        else:
            messagebox.showinfo("提示", "当前没有正在进行的训练", parent=self)

    def _train_done(self, ok, info):
        try:
            self._btn_train.config(state="normal", text="  ▶ 开始训练 ")
        except Exception:
            pass
        if not ok and info and any(k in str(info) for k in ("NoneType", "'write'", "write'")):
            self._append_log(
                ">> 提示: 该报错多为瞬时环境问题(内存/磁盘/驱动), 建议直接重试一次; "
                "若仍失败请查看上方完整堆栈\n", "warn")
        if ok:
            self._set_pct(100, "训练完成 ✓ 模型已导出")
            self._append_log(">> 训练完成, 模型已导出到 runs/outputs/\n", "ok")
            audit("training_end", "训练成功", module="training_page")
        else:
            self._set_pct(0, "训练失败, 详见日志")
            self._append_log(f">> 训练失败: {info or '未知错误'}\n", "err")
            audit("training_end", f"训练失败: {info}", level="error", module="training_page")
        # 广播模型变更 —— .pt 下拉在「数据集与标注」页, 本页没有它的控件
        try:
            ds_state.notify_models_changed()
        except Exception:
            pass
        if self.controller:
            self.controller.notify("训练完成" if ok else "训练失败",
                                   "success" if ok else "error")

    def _default_params(self):
        return {
            "模型": _cfg.training("default_model", "yolo11n.pt"),
            "轮数": str(_cfg.training("epochs", 100)),
            "批次": str(_cfg.training("batch", 16)),
            "尺寸": str(_cfg.training("imgsz", 640)),
            "学习率": str(_cfg.training("lr0", 0.01)),
            "lr因子": str(_cfg.training("lrf", 0.01)),
            "动量": str(_cfg.training("momentum", 0.937)),
            "权重衰减": str(_cfg.training("weight_decay", 0.0005)),
            "预热": str(_cfg.training("warmup_epochs", 3)),
            "优化器": _cfg.training("optimizer", "auto"),
            "早停": str(_cfg.training("patience", 50)),
        }

    def _reset_params(self, e=None):
        d = self._default_params()
        for k, v in d.items():
            if k in self._pvars:
                try:
                    self._pvars[k].set(v)
                except Exception:
                    pass
        self._status.config(text="训练参数已恢复为 config.yaml 默认值")
        self._append_log(">> 训练参数已恢复默认\n", "hdr")
        audit("training_params_reset", "恢复默认训练参数", module="training_page")

    # ═══════ 跨页事件 (emit 可能来自后台线程, 一律 after(0) 回主线程) ═══════
    def _on_ds_changed_evt(self, name="", **_kw):
        """数据集选择变化广播 (来自「数据集与标注」页)。"""
        try:
            self.after(0, self._sync_ds_card)
        except Exception:
            pass

    def _sync_ds_card(self):
        """把共享状态铺到左侧核对卡 + 配置摘要。"""
        try:
            name = ds_state.get_name()
            info = ds_state.get_info()
            if info:
                total = getattr(info, "image_count", 0) or 0
                labeled = getattr(info, "label_count", 0) or 0
                pct = round(labeled / total * 100) if total else 0
                self._ds_name_lbl.config(text=Path(name).name, fg=_tc.text)
                self._ds_stat_lbl.config(
                    text=f"图片 {total} 张 · 标注 {labeled} 个 · 标注率 {pct}%")
                ready = total > 0
                self._ds_ready_lbl.config(
                    text="✅ 可开始训练" if ready else "⚠ 数据集里没有图片",
                    fg=_tc.success if ready else _tc.warning)
                try:
                    _mdl = self._pvars.get("模型")
                    self._chk_lbl.config(text=(
                        f"· 数据集    {'✓' if total else '✗'}\n"
                        f"· 标注覆盖  {'✓' if total and labeled >= total * 0.8 else '⚠ 建议补齐'}\n"
                        f"· 训练模型  {'✓ ' + (_mdl.get() if _mdl else '') if _mdl and _mdl.get() else '✗'}"))
                except Exception:
                    pass
            else:
                self._ds_name_lbl.config(text="未选择数据集", fg=_tc.warning)
                self._ds_stat_lbl.config(text="请到「数据集与标注」页选择或生成训练集")
                self._ds_ready_lbl.config(text="⚠ 无法开始训练", fg=_tc.warning)
                self._chk_lbl.config(text="· 数据集    ✗")
        except Exception:
            pass
        self._train_summary()

    def _goto_dataset(self, e=None):
        """跳到「数据集与标注」页 (选择权在那里, 本页只读)。"""
        try:
            if self.controller and hasattr(self.controller, "show_page"):
                self.controller.show_page("dataset")
        except Exception:
            pass

    def _rescan_datasets(self, e=None):
        """强制重扫数据集清单 —— 清单是共享的, 标注页会跟着更新。"""
        try:
            ds_state.ensure_scan(force=True)
            self._status.config(text="正在重新扫描数据集...")
        except Exception:
            pass

    def _open_model_manager(self, e=None):
        """打开模型库管理窗口 (训练页顶栏入口)。"""
        try:
            from gui.pt_manager import ModelManagerDialog
            dlg = ModelManagerDialog(self, on_changed=ds_state.notify_models_changed)
            dlg.grab_set()
            audit("pt_manager_open", "打开模型库管理窗口", module="training_page")
        except Exception as ex:
            self._append_log(f">> 打开模型库失败: {ex}\n", "err")
            audit("pt_manager_open_failed", str(ex), level="error", module="training_page")

    def on_show(self):
        """切到本页: 数据集可能刚在标注页改过, 重新同步一次。

        ★ 顺便在这里（而不是构造期）触发设备探测 —— 见 `_build_ui` 里的注释：
          构造期探 = 预建时白付 ~296 MB。
        """
        if not getattr(self, "_dev_probed", False):
            self._dev_probed = True
            try:
                self._probe_devices_async("_train_dev_cb", self._train_dev)
            except Exception:                                    # noqa: BLE001
                pass
        try:
            if not ds_state.get_map():
                ds_state.ensure_scan()
            self._sync_ds_card()
        except Exception:
            pass

    def destroy(self):
        """退订 —— 页面被销毁后回调再触发会 Invalid command name。"""
        try:
            ds_state.bus().unsubscribe(ds_state.EV_CHANGED, self._on_ds_changed_evt)
        except Exception:
            pass
        super().destroy()

    # ═══════ 统一日志/百分比 (与标注页同形, 刻意各留一份) ═══════
    def _poll_q(self):
        """训练页消息循环 —— 只处理本页产生的消息类型。

        2026-09-11 拆分时**刻意**把 _poll_q / _append_log / _set_pct 各留一份,
        而不是抽公共基类: 两页的消息类型已经不同 (训练页只有 log/pct/status/
        train_done), 抽基类反而要引入"谁的控件"这类间接层。代价是 30 行重复,
        换两个页面各自可独立读懂。
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
                    try:
                        self._status.config(text=msg[1])
                    except Exception:
                        pass
                elif kind == "train_done":
                    self._train_done(msg[1], msg[2])
        except _queue.Empty:
            pass
        except Exception:
            return  # 页面已销毁
        try:
            self.after(_pk.poll_delay(self, busy), self._poll_q)
        except Exception:
            pass
