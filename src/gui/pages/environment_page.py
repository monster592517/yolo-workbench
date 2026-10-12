# -*- coding: utf-8 -*-
"""环境配置页 — 自动检测 / 一键安装 / 安装日志"""
from __future__ import annotations
import queue as _queue
import threading
import tkinter as tk
import tkinter.scrolledtext as st
import tkinter.ttk as ttk
from tkinter import messagebox
from infrastructure.config import get_config, get_theme
from infrastructure.audit import audit
from gui.components import pip_install_stream, DiagnosticsPanel, step_card
from gui import ui_perf as _up
from gui import poll_kit as _pk

_tc = get_theme().colors
_cfg = get_config()
from infrastructure.environment import get_environment, INSTALL_EXCLUDE, detect_py311_exe

# 日志文本上限 (行) —— Text 内容无限增长会让 see()/insert 退化成 O(n),
# 实测单次 see("end") 可达 600ms+。超过上限从头部裁剪。
_CONSOLE_MAX_LINES = 500
_POLL_FAST_MS = 120      # 有活干时的轮询间隔
_POLL_IDLE_MS = 700      # 空闲时退避间隔 (不再 120ms 空转一辈子)

# ★ 本机概况卡的**显式请求高** (px)。
#   不是"好看"用的数字 —— 它是打破 "卡高由父定 / 父高由卡定" 死循环的唯一手段:
#   卡上挂了 `pack_propagate(False)`, Frame 默认请求高 = 1px, 于是 pack 的 expand
#   和 grid 的 sticky="nsew" **都拿不到高度可分**, 整行/整列塌成 1px (2026-10-03 实测)。
#   ⚠ 这个数**必须 >= 实测所需**, 小一点就会把末行裁掉 —— 而 `pack_propagate(False)`
#     下它**不会报错、也不会自己变高**, 只是静默裁掉 (第一版取 80, 内存卡
#     "已用 83% (11.4/14G)" 就被切了下半截)。
#   实测所需 (2026-10-03, 本机): 标签行 22 + 数值行 57 + 上下内边距 16 = **95**
#     ⇒ 就近取 **96** (8px 栅格)。
#   改字体、改 `height=3`、改 padx/pady 时**必须回来重测这个数**
#     （探针: 读 `card.winfo_children()` 两个 Label 的 `winfo_reqheight()` 相加）。
_CARD_H = 96


class EnvironmentPage(tk.Frame):
    def __init__(self, parent, controller=None):
        super().__init__(parent, bg=_tc.bg)
        self.controller = controller
        self._loaded = False
        self._scanning = False
        self._installing = False
        BG = _tc.bg; T = _tc.text; S = _tc.text_secondary

        from gui.components import topbar, sub_card, btn, scrollable
        topbar(self, "🔧", "环境配置", "启动即检测 | 一键安装依赖 | 环境自检")
        tk.Frame(self, height=1, bg=_tc.border).pack(fill="x", padx=24, pady=(0, 8))

        # Python 版本警告横幅 (Python<3.11 时显示)
        self._ver_banner = tk.Frame(self, bg=_tc.warning_bg, highlightbackground=_tc.warning,
                                    highlightthickness=1)
        self._ver_banner_lbl = tk.Label(self._ver_banner, text="", font=("Microsoft YaHei UI", 9),
                                        bg=_tc.warning_bg, fg=_tc.warning, anchor="w",
                                        justify="left", wraplength=1100)
        self._ver_banner_lbl.pack(fill="x", padx=12, pady=8)

        # ── 滚动主体 (内容超高滚动; 底部日志/按钮固定) ──
        self._body_outer, self._body = scrollable(self, bg=BG)
        self._body_outer.pack(fill="both", expand=True)

        # ══════════════════════════════════════════════════════════════════
        # ★★ 2026-10-03 按功能重排: 原布局是 **Tab 切换 + 纵向单列**
        #   （① 环境概览 / ② 依赖与诊断，看一个藏一个）。
        #   实测三个后果:
        #   ① **概览态整片空白** —— 概览只有一行 5 张卡, 而可用 726px
        #      ⇒ 下方大片空; 且切到概览时"依赖/诊断"整块被藏起来,
        #      用户看到的就是一个空页面。
        #   ② 依赖状态/自检/日志全挤在②里, 又要滚动。
        #   ③ 5 张"本机概况"卡其实**一直是 1px 高、从未显示**（见下方长注释）。
        #   重排为 **1 行 2 栏 + 1 行通栏**, 按功能分区:
        #     ┌─ 左 460px ────┬─ 右 吃满剩余 ────────────┐
        #     │ 本机概况 5 卡 │ 依赖状态 (缺什么/怎么补) │  ← 两栏高度天然接近
        #     ├───────────────┴──────────────────────────┤
        #     │ 环境自检 (全宽 · 2 列节点动画本来就适合宽栏) │
        #     └──────────────────────────────────────────┘
        #   ⚠ 自检卡**不放进任何一栏** —— 它是最大的单块 (折叠 187 / 展开 548),
        #     放哪栏就把那栏顶到 800+ 而另一栏停在 264 (实测 836 vs 336, 差 2.5 倍)。
        #     通栏后左右两栏只需互相配平: 折叠 264 vs 246, 双展开 264 vs 531。
        #   ⚠ 底部安装日志保持 side="bottom"（原实现如此, 不动）。
        # ══════════════════════════════════════════════════════════════════
        self._env_cols = tk.Frame(self._body, bg=BG)
        self._env_cols.pack(fill="x", padx=24, pady=(2, 0))
        self._env_cols.columnconfigure(0, weight=0, minsize=460)
        self._env_cols.columnconfigure(1, weight=1)

        self._env_s1 = tk.Frame(self._env_cols, bg=BG)
        self._env_s1.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self._env_s2 = tk.Frame(self._env_cols, bg=BG)
        self._env_s2.grid(row=0, column=1, sticky="nsew")

        # ── 左栏·本机概况 (原来 5 卡一行, 现按 2 列铺开) ──
        info = tk.Frame(self._env_s1, bg=BG); info.pack(fill="x")
        self._cards = {}
        for _i, (key, label) in enumerate([("python", "Python"), ("gpu", "GPU"),
                                           ("cameras", "摄像头"), ("disk", "磁盘"),
                                           ("ram", "内存")]):
            card = tk.Frame(info, bg=_tc.card_bg)
            # 5 张卡改 2 列网格: 左栏 460px 放 5 个横排会挤成"一行五小块"
            card.grid(row=_i // 2, column=_i % 2, sticky="nsew", padx=(0, 8), pady=(0, 8))
            info.columnconfigure(_i % 2, weight=1)
            # ★★ 2026-09-22：**把每张卡的"请求宽"钉死。**
            #   5 张卡原本 `side="left", expand=True` 排一行 ⇒ 空间按**请求宽**分摊；
            #   只要某一张的读数变长（"检测中..." → "OK 3.13.13" → 一长串路径），
            #   整行就重新分配 ⇒ **五张卡一起"忽大忽小"**
            #   （`audit_layout_jitter` 实测 Δw=43/66/76）。
            #   钉死请求宽之后：内容再变，分摊也不再变。
            #   ⚠ 必须用 `pack_propagate(False)` —— 子控件是 **pack** 进来的。
            #
            # ══════════════════════════════════════════════════════════════════
            # ★★ 2026-10-03 修：**这 5 张卡一直是 1px 高，从来没显示过。**
            #   `pack_propagate(False)` 冻结的是卡的**请求尺寸**，而 Frame 的默认请求
            #   高是 1px —— 只写 `width=196` 时它报 `req=196x1`。于是：
            #     ① 旧版 `pack(side="left", fill="both", expand=True)`：
            #        `expand` 分的是**父容器多余的空档**，而父容器 `info` 是
            #        `pack(fill="x")`（高度由内容决定）⇒ 没有空档可分 ⇒ 卡高 = 1；
            #     ② `grid(sticky="nsew")`：`nsew` 同样只能填**网格单元**，
            #        单元高由行里最高的控件决定 ⇒ 全是 1px ⇒ 整列塌成一条。
            #   两次都是同一个循环依赖：**卡高由父定、父高由卡定，谁都拿不到数**。
            #   破环的唯一办法 = 给卡一个**真实请求高**（下面 `height=_CARD_H`）。
            #   ⇒ 实测取证 (改动前 dump: `card[python]: 220x1, 两个子 Label 1x1/未映射`;
            #      补上 height 后 `216x80`，5 卡 3 行共 264px 正常显示)。
            #   ⚠ 别用 `pack_propagate(True)` 救 —— 那会连 width 一起放开，
            #      2026-09-22 修好的"五张卡一起忽大忽小"立刻复发。
            # ══════════════════════════════════════════════════════════════════
            card.configure(width=196, height=_CARD_H)
            card.pack_propagate(False)
            tk.Label(card, text=label, font=("Microsoft YaHei UI", 8), bg=_tc.card_bg,
                     fg=_tc.text_muted).pack(anchor="w", padx=12, pady=(8, 0))
            val = tk.Label(card, text="检测中...", font=("Microsoft YaHei UI", 9, "bold"),
                           bg=_tc.card_bg, fg=_tc.text, anchor="w", justify="left",
                           wraplength=176, height=3)
            val.pack(anchor="w", fill="x", padx=12, pady=(0, 8))
            self._cards[key] = val

        # ── 右栏·缺什么怎么补: 依赖状态 ──
        # ⚠ 重排后本块挂在**右栏内部** ⇒ padx 由整页留白 24 改成列内 0
        #   （列间距已由 _env_cols 的 padx=(0,12) 给足; 再写 24 会双重缩进、与卡片错位）
        self._dep_summary = tk.Label(self._env_s2, text="依赖包状态 · 尚未检测", font=("Microsoft YaHei UI", 9),
                                     bg=BG, fg=_tc.text_secondary, anchor="w")
        self._dep_summary.pack(fill="x", padx=0, pady=(0, 2))
        self._dep_toggle = tk.Label(self._env_s2, text=" ▾ 收起明细",
                                    font=("Microsoft YaHei UI", 8, "bold"),
                                    bg=BG, fg=_tc.accent, cursor="hand2")
        self._dep_toggle.pack(anchor="w", padx=0, pady=(0, 4))
        self._dep_toggle.bind("<Button-1>", lambda e: self._set_dep_detail(not self._dep_detail.winfo_manager()))

        # ── 依赖状态列表 (2 列网格; **默认展开** —— 理由见 _set_dep_detail docstring) ──
        dc, dbody = sub_card(self._env_s2, "依赖包状态")
        self._dep_detail = dc
        dc.pack(fill="both", expand=True, padx=0, pady=(0, 8))
        list_head = tk.Frame(dbody, bg=_tc.card_bg)
        list_head.pack(fill="x", padx=12, pady=(8, 2))
        self._scan_info = tk.Label(list_head, text="", font=("Microsoft YaHei UI", 9),
                                   bg=_tc.card_bg, fg=_tc.text_muted)
        self._scan_info.pack(side="right")
        self._list = tk.Frame(dbody, bg=_tc.card_bg)
        self._list.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self._list.grid_columnconfigure(0, weight=1)
        self._list.grid_columnconfigure(1, weight=1)
        self._pkg_rows = {}

        # ── 通栏·环境自检 (2026-09-11 由设置页迁入; 与环境概况同页, 不再两头跑) ──
        self._env_bottom = tk.Frame(self._body, bg=BG)
        self._env_bottom.pack(fill="x", padx=24, pady=(8, 8))
        self._build_diag_card(self._env_bottom)

        # ── 底部: 安装日志 (bottom 栈最底) + 操作按钮 (其上方) ──
        lc, lcb = sub_card(self, "安装日志")
        lc.pack(side="bottom", fill="x", padx=24, pady=(0, 12))
        self._env_lc = lc
        from gui.components import dark_scrolledtext
        self.after(200, lambda: self._env_show(0))
        self._console = st.ScrolledText(lcb, height=7, font=("Consolas", 9),
                                        bg=_tc.log_bg, fg=_tc.log_text,
                                        relief="flat", bd=0, padx=8, pady=8)
        # ⚠ 2026-09-12: 同上 —— ScrolledText 自带的 vbar 是 tk.Scrollbar, 本机无法
        # 换肤; 它的 frame 也是系统默认浅底。统一交给 dark_scrolledtext() 补齐。
        dark_scrolledtext(self._console, frame_bg=_tc.input_bg)
        self._console.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self._console.configure(state="disabled")
        self._console.tag_configure("err", foreground=_tc.danger)
        self._console.tag_configure("ok", foreground=_tc.success)

        btnrow = tk.Frame(self, bg=BG); btnrow.pack(side="bottom", fill="x", padx=24, pady=(8, 8))
        self._btn_scan = btn(btnrow, "  🔄 重新检测 ", self._do_scan, "border")
        # ★★ 2026-09-23：**钉死宽度**。这个按钮的文案会在
        #   `"  🔄 重新检测 "` ↔ `"  扫描中..."` 之间来回换（`_do_scan` / `_scan_done`），
        #   两串宽度差 **28px** ⇒ 每次扫描都把整行重排一次（`audit_hover_jitter` 实测
        #   "Δw=28 且不可逆"）。钉死后文案换、布局不动。
        try:
            self._btn_scan.configure(width=14)
        except Exception:                                        # noqa: BLE001
            pass
        self._btn_scan.pack(side="left", padx=(0, 8))
        self._btn_install = btn(btnrow, "  ⚡ 一键安装缺失依赖 ", self._install_missing, "primary")
        self._btn_install.pack(side="left")
        self._btn_log = tk.Label(btnrow, text="📋 安装日志 (默认收起)", font=("Microsoft YaHei UI", 8),
                                 bg=BG, fg=_tc.text_muted, cursor="hand2")
        self._btn_log.pack(side="right", padx=(8, 0))
        self._btn_log.bind("<Button-1>", lambda e: self._show_log(not self._env_lc.winfo_manager()))
        self._inst_status = tk.Label(btnrow, text="", font=("Microsoft YaHei UI", 9), bg=BG,
                                     fg=_tc.text_muted)
        self._inst_status.pack(side="right")
        self._show_log(False)  # 安装日志默认收起 (v0.23.4 按需显示)

    def _env_show(self, i):
        """兼容旧调用: 重排后**双栏常显, 不再切 Tab**。

        保留本方法是因为 `__init__` 尾部仍在调用它, 且 `_env_mode` 可能被别处读取。
        ⚠ 不再做 pack/pack_forget —— 那正是"看一个藏一个、概览态整片空白"的旧行为。
        """
        try:
            for w in (getattr(self, "_env_s1", None), getattr(self, "_env_s2", None)):
                if w is not None and not w.winfo_manager():
                    w.grid()
            # ⚠ Tab 条已随重排移除, 不再同步高亮
            self._env_mode = i
        except Exception:
            pass

    # ═══════ 扫描 ═══════
    def on_show(self):
        if not self._loaded:
            self._loaded = True
            # ★ 自动扫**不展开**明细面板（理由见 `_do_scan` 的 docstring）
            self._do_scan(auto=True)

    def _append_log(self, line, tag=None):
        """追加一行日志。

        性能修复 (2026-09-11): 原实现每行都 `see("end")` —— 实测单次最深可达
        **619ms** (Text 内容越长越慢), 环境页扫描/安装时一屏日志就是几秒卡顿。
        现在: ① 内容超 _CONSOLE_MAX_LINES 从头部裁剪; ② see() 合并为 120ms 一次;
        ③ 控件不可见时根本不 see() (不可见时滚不滚没人看得见)。
        """
        try:
            self._console.configure(state="normal")
            self._console.insert("end", line, tag or "")
            try:
                if int(self._console.index("end-1c").split(".")[0]) > _CONSOLE_MAX_LINES:
                    self._console.delete("1.0", "101.0")
            except Exception:
                pass
            self._console.configure(state="disabled")
            _up.debounce(self._console, "see", 120, self._scroll_console)
        except Exception:
            pass

    def _scroll_console(self):
        try:
            if self._console.winfo_exists() and self._console.winfo_ismapped():
                self._console.see("end")
        except Exception:
            pass

    def _set_dep_detail(self, show):
        """依赖明细: 默认**常显**（2026-10-03 改）。

        ⚠ 2026-10-03 变更: 原实现默认折叠、只在检测/安装或手点时展开。重排后它独占
          右栏, 折叠态只剩两行字 ⇒ **右栏 264px 里只用了 51px**, 与左栏 3 行卡明显失衡
          （截图实测）。而它承载的正是本页**唯一可操作**的信息（哪些包装、缺几个）。
        ⇒ 改默认展开。**不会**重新引入 2026-09-23 修掉的"页面自己变形":
          那次变形是"折叠 → 展开"这**一次跳变**叠加扫描流式填充; 现在从建页起就是
          展开态, 扫描只是往**已存在的网格**里填行, 高度在扫描开始时就已经定格。
        ⚠ padx 由 **24 改 0** —— 本块已挂进右栏内部, 列间距由 `_env_cols` 的
          `padx=(0, 12)` 给足; 再写 24 会双重缩进、与左栏卡片错位。
        """
        try:
            if show:
                self._dep_detail.pack(fill="both", expand=True, padx=0, pady=(0, 8))
                self._dep_toggle.config(text=" ▾ 收起明细")
            else:
                self._dep_detail.pack_forget()
                self._dep_toggle.config(text=" ▸ 展开明细 (检测/安装时自动展开)")
        except Exception:
            pass

    def _dep_summary_set(self, text, ok_color=None):
        try:
            self._dep_summary.config(text=text, fg=ok_color or _tc.text_secondary)
        except Exception:
            pass

    def _do_scan(self, auto=False):
        """`auto=True` = 进页时自动扫的那一次 —— **不展开依赖明细面板**。

        ★★ 2026-09-23（`audit_hover_jitter` 查出来的）：原来自动扫也 `展开`，
        而扫描结果是**流式一条条填进那个列表**的 ⇒ 面板高度一节节往上长
        （实测 `!frame4.!canvas.!frame Δh=161`，而且**反复复现**，不是一次性）。
        用户看到的就是"页面自己在那儿变形"。
        ⇒ 自动扫只更新摘要那一行；**手点「🔄 重新检测」才展开**（那是用户在等，
          展开是应当的反馈）。这样"自动发生的事"不改变布局。
        """
        if self._scanning:
            return
        self._scanning = True
        if not auto:
            self._set_dep_detail(True)
        self._btn_scan.config(state="disabled", text="  扫描中...")
        self._scan_info.config(text="正在扫描环境...")
        try:
            from infrastructure.audit import audit as _a
            _a("env_scan_start", "开始环境扫描", module="environment_page")
        except Exception:
            pass
        for v in self._cards.values():
            v.config(text="检测中...")

        # 线程安全: 后台线程只写队列, 主线程轮询更新 UI
        self._scan_q = _queue.Queue()

        def _scan():
            try:
                mgr = get_environment()
                s = mgr.scan()
                self._scan_q.put(("render", s))
            except Exception as e:
                self._scan_q.put(("error", str(e)))
            finally:
                self._scan_q.put(("done", None))
        threading.Thread(target=_scan, daemon=True).start()
        self._poll_scan_q()

    def _poll_scan_q(self):
        busy = False
        try:
            while True:
                kind, payload = self._scan_q.get_nowait()
                busy = True
                if kind == "render":
                    self._render(payload)
                elif kind == "error":
                    try: self._scan_info.config(text=f"扫描失败: {payload}", fg=_tc.danger)
                    except Exception: pass
                elif kind == "done":
                    self._scan_done()
        except _queue.Empty:
            pass
        except Exception:
            return  # 页面已销毁
        try:
            self.after(_pk.poll_delay(self, busy or self._scanning,
                                      _POLL_FAST_MS, _POLL_IDLE_MS),
                       self._poll_scan_q)
        except Exception:
            pass

    def _scan_done(self):
        self._scanning = False
        try:
            self._btn_scan.config(state="normal", text="  🔄 重新检测 ")
        except Exception:
            pass
        # ⚠ 2026-10-03: **不再在这里折叠依赖明细**。
        #   原实现 `self._set_dep_detail(False)`（"检测完成 → 折叠回摘要"）。
        #   现在该面板是**常显**的右栏主体（见 `_set_dep_detail` docstring）,
        #   扫完就把它收掉 = 用户刚看到结果就被抽走, 而且右栏瞬间空掉。
        #   要收起交给用户点「▾ 收起明细」。
        try:
            from infrastructure.audit import audit as _a
            _a("env_scan_done", "环境扫描完成", module="environment_page")
        except Exception:
            pass

    def _render(self, s):
        try:
            info_widget = self._cards["python"].master
            if s.get("python_ok"):
                self._cards["python"].config(text=s["python"] + " ✓", fg=_tc.success)
                self._ver_banner.pack_forget()
            else:
                self._cards["python"].config(text=s["python"] + " ✗ (需" + s.get("python_required", "3.11") + "+)",
                                             fg=_tc.danger)
                exe = detect_py311_exe()
                tip = ("检测到本机 Python 3.11+，请使用 YOLO工作台.bat 或该解释器启动程序"
                       if exe else "请安装 Python 3.11+ 后重试")
                self._ver_banner_lbl.config(text="⚠️ 当前 Python " + s["python"] +
                                             " 低于项目要求 " + s.get("python_required", "3.11") +
                                             "+：标注工具(X-AnyLabeling)无法安装。" + tip)
                # pack 顺序固定: 横幅始终位于信息卡之前
                self._ver_banner.pack(fill="x", padx=24, pady=(0, 8), before=info_widget)
            gpu_ok, gpu_name = s["gpu"]
            self._cards["gpu"].config(text=gpu_name or "未检测到(N/A)",
                                      fg=_tc.success if gpu_ok else _tc.text_muted)
            _cam_note = s.get("camera_note", "")
            self._cards["cameras"].config(
                text=f"{s['cameras']} 个" + (f" · {_cam_note}" if _cam_note else ""),
                fg=_tc.warning if _cam_note else _tc.text)
            mem = s.get("memory", {})
            self._cards["disk"].config(
                text=f"剩余 {mem.get('disk_free_gb', 0):.0f}G / 共 {mem.get('disk_total_gb', 0):.0f}G")
            self._cards["ram"].config(
                text=f"已用 {mem.get('ram_pct', 0):.0f}% ({mem.get('ram_used_gb', 0):.1f}/{mem.get('ram_total_gb', 0):.0f}G)")

            # 依赖列表
            for w in self._list.winfo_children():
                w.destroy()
            self._pkg_rows = {}
            pkgs = list(s["packages"].items())
            installed_n = sum(1 for info in s["packages"].values() if info["installed"])
            self._scan_info.config(text=f"{installed_n}/{len(pkgs)} 已安装")
            self._last_pkg_summary = (installed_n, len(pkgs))
            missing = len(pkgs) - installed_n
            self._dep_summary_set(
                f"依赖包状态 · {installed_n}/{len(pkgs)} 已安装" +
                (f" · {missing} 项缺失 (点「一键安装缺失依赖」)" if missing else " · 全部就绪"),
                _tc.success if missing == 0 else (_tc.warning if missing else _tc.text_secondary))
            for idx, (nm, info) in enumerate(pkgs):
                ok = info["installed"]
                row = tk.Frame(self._list, bg=_tc.card_bg, highlightbackground=_tc.border,
                               highlightthickness=1)
                row.grid(row=idx // 2, column=idx % 2, sticky="ew", padx=(0, 8), pady=2)
                dot = tk.Label(row, text="●" if ok else "○", font=("Microsoft YaHei UI", 10),
                               fg=_tc.success if ok else _tc.danger, bg=_tc.card_bg)
                dot.pack(side="left", padx=(8, 2))
                tk.Label(row, text=info["desc"], font=("Microsoft YaHei UI", 9, "bold"),
                         fg=_tc.text if ok else _tc.text_secondary, bg=_tc.card_bg).pack(side="left")
                ver = tk.Label(row, text=info["version"], font=("Consolas", 8),
                               fg=_tc.text_secondary, bg=_tc.card_bg)
                ver.pack(side="left", padx=8)
                if not ok and nm in INSTALL_EXCLUDE:
                    tip = tk.Label(row, text="(需 Py3.11+, 请在训练页安装)", font=("Microsoft YaHei UI", 8),
                                   fg=_tc.warning, bg=_tc.card_bg)
                    tip.pack(side="left", padx=4)
                self._pkg_rows[nm] = (row, dot, ver)
        except Exception as e:
            self._scan_info.config(text=f"渲染失败: {e}", fg=_tc.danger)

    # ═══════ 一键安装 ═══════
    def _show_log(self, show):
        """安装日志: 只在安装期间 (或手动点开) 显示, 平时收起。"""
        try:
            if show:
                self._env_lc.pack(side="bottom", fill="x", padx=24, pady=(0, 12))
                self._btn_log.config(text="📋 安装日志 (收起)")
            else:
                self._env_lc.pack_forget()
                self._btn_log.config(text="📋 安装日志 (默认收起)")
        except Exception:
            pass

    def _install_missing(self):
        if self._installing:
            return
        self._installing = True
        self._show_log(True)          # 安装期间显示日志
        self._set_dep_detail(True)    # 安装期间展开明细
        self._btn_install.config(state="disabled", text="  安装中...")
        self._inst_status.config(text="准备中...", fg=_tc.warning)
        self._append_log(">> 开始检测缺失依赖...\n")
        try:
            from infrastructure.audit import audit as _a
            _a("env_install_start", "开始一键安装缺失依赖", module="environment_page")
        except Exception:
            pass

        # 线程安全: 后台线程只写队列, 主线程轮询更新 UI
        self._inst_q = _queue.Queue()

        def _do():
            try:
                mgr = get_environment()
                s = mgr.scan()
                missing = [nm for nm, info in s["packages"].items()
                           if not info["installed"] and nm not in INSTALL_EXCLUDE]
                if not missing:
                    self._inst_q.put(("log", ">> 所有依赖均已安装 ✓\n"))
                    self._inst_q.put(("status", "全部已安装", _tc.success))
                    try:
                        from infrastructure.audit import audit as _a
                        _a("env_install_done", "依赖全部已安装", module="environment_page")
                    except Exception:
                        pass
                    return
                total = len(missing)
                for i, pkg in enumerate(missing):
                    self._inst_q.put(("log", f">> 安装 {pkg} ({i + 1}/{total})...\n"))
                    self._inst_q.put(("status", f"正在安装 {pkg}...", _tc.warning))
                    rc = pip_install_stream(
                        pkg,
                        on_line=lambda line, p=pkg: self._inst_q.put(("log", line + "\n")),
                        mirror=_cfg.pip_mirror,
                        timeout=_cfg.timeout_pip)
                    if rc == 0:
                        self._inst_q.put(("log", f">> {pkg} 安装成功 ✓\n", "ok"))
                    else:
                        self._inst_q.put(("log", f">> {pkg} 安装失败 (exit={rc})\n", "err"))
                    try:
                        from infrastructure.audit import audit as _a
                        _a("env_install_pkg", f"{pkg} -> {'成功' if rc == 0 else '失败(exit=%d)' % rc}",
                           level="info" if rc == 0 else "error", module="environment_page")
                    except Exception:
                        pass
                self._inst_q.put(("status", "安装结束", _tc.text_secondary))
                self._inst_q.put(("rescan", None))
            finally:
                self._inst_q.put(("btn", None))
                self._installing = False
        threading.Thread(target=_do, daemon=True).start()
        self._poll_install_q()

    def _poll_install_q(self):
        busy = False
        try:
            while True:
                msg = self._inst_q.get_nowait()
                busy = True
                kind = msg[0]
                if kind == "log":
                    self._append_log(msg[1], msg[2] if len(msg) > 2 else None)
                elif kind == "status":
                    try: self._inst_status.config(text=msg[1], fg=msg[2])
                    except Exception: pass
                elif kind == "rescan":
                    self._do_scan()
                elif kind == "btn":
                    try: self._btn_install.config(state="normal", text="  ⚡ 一键安装缺失依赖 ")
                    except Exception: pass
        except _queue.Empty:
            pass
        except Exception:
            return  # 页面已销毁
        try:
            self.after(_pk.poll_delay(self, busy or self._installing,
                                      _POLL_FAST_MS, _POLL_IDLE_MS),
                       self._poll_install_q)
        except Exception:
            pass

    DIAG_ITEMS = [
        ("ultralytics", "YOLO核心"), ("opencv-python", "OpenCV"),
        ("numpy", "NumPy"), ("torch", "PyTorch"),
        ("mss", "屏幕捕获"), ("camera", "摄像头"), ("Pillow", "图像处理"),
        ("pyyaml", "YAML"), ("torchvision", "视觉扩展"),
        ("tkinterdnd2", "拖放支持"), ("psutil", "系统监控"),
        ("x-anylabeling-cvhub", "标注工具"), ("disk", "磁盘"),
        ("config", "配置"), ("cuda", "CUDA"),
        ("python_env", "Python环境"),
        ("files", "关键文件"), ("path", "路径适配"), ("path_center", "路径中心"),
        ("logs", "日志系统"),
        ("self_repair", "自我修复"),
        ("annotation_deps", "标注工具依赖"),
        ("modules", "核心模块"), ("datasets", "数据集"),
        ("dataset_format", "数据集格式"),
        ("model", "模型文件"),
    ]

    def _build_diag_card(self, parent):
        BG = _tc.bg; C = _tc.card_bg; T = _tc.text; S = _tc.text_secondary
        c, cb = step_card(parent, 3, "环境自检", "依赖包/磁盘/配置/CUDA/部署包/Python环境 · 实时动画")
        self._set_diag_card = c
        c.pack(fill="x", pady=(0, 8))
        # ⚠ 2026-10-03 删除一行重复的副标题。
        #   原实现把同一串"依赖包 / 磁盘 / 配置 / CUDA / 部署包 / Python环境 · 实时动画"
        #   **写了两遍**：一次作为 `step_card` 的 `subtitle`（上面那行），一次是这里
        #   显式 `tk.Label`。实测截图里卡片标题下出现**两行一模一样的灰字**。
        #   删掉显式那行（step_card 的副标题排版与字号统一，优先留它）。

        row = tk.Frame(cb, bg=C); row.pack(fill="x", padx=16, pady=(4, 4))
        self._diag_btn = tk.Label(row, text="  ▶ 开始自检 ", font=("Microsoft YaHei UI", 10, "bold"),
                                  bg=_tc.primary, fg="white", padx=16, pady=8, cursor="hand2")
        self._diag_btn.pack(side="left")
        self._diag_btn.bind("<Button-1>", self._run_diag)
        self._diag_status = tk.Label(row, text="就绪", font=("Microsoft YaHei UI", 9), bg=C, fg=S)
        self._diag_status.pack(side="left", padx=12)
        # 第二行: 适配路径 + 自动修复 + 修复进度条 (次要操作, 中性化, 主焦点让给「开始自检」)
        row2 = tk.Frame(cb, bg=C); row2.pack(fill="x", padx=16, pady=(0, 4))
        self._path_btn = tk.Label(row2, text="  🔄 立即适配路径 ", font=("Microsoft YaHei UI", 10, "bold"),
                                  bg=_tc.nav_hover_bg, fg=_tc.text, padx=12, pady=4, cursor="hand2")
        self._path_btn.pack(side="left")
        self._path_btn.bind("<Button-1>", self._adapt_paths_now)
        self._repair_btn = tk.Label(row2, text="  🔧 自动修复 ", font=("Microsoft YaHei UI", 10, "bold"),
                                    bg=_tc.border, fg=_tc.text_secondary, padx=12, pady=4, cursor="hand2")
        self._repair_btn.pack(side="left", padx=8)
        self._repair_btn.bind("<Button-1>", self._auto_repair_now)

        self._diag_panel = DiagnosticsPanel(cb, self.DIAG_ITEMS, bg=C, cols=2, row_h=24)
        self._diag_panel.pack(fill="x", padx=16, pady=(0, 16))
        self._diag_done_count = 0
        self._diag_total = len(self.DIAG_ITEMS)
        # 自我修复进度条: 置于自检卡片底部 (不占用按钮行)
        self._repair_bar = ttk.Progressbar(cb, mode="determinate", maximum=9)
        self._repair_bar.pack(fill="x", padx=16, pady=(0, 12))

        # v0.23.4 按需显示: 自检面板/修复进度条平常隐藏, 仅在自检或修复时出现
        self._diag_panel.pack_forget()
        self._repair_bar.pack_forget()
        self._diag_status.config(text="就绪 · 进度在点击「开始自检」/「自动修复」时显示")

    
        return None
    def _run_diag(self, e=None):
        from infrastructure.diagnostics import run_diagnostics_polling
        audit("env_self_check", "开始环境自检", module="environment_page")
        try:
            self._diag_btn.config(state="disabled", text="  自检中...")
            self._diag_status.config(text="正在检测...", fg=_tc.warning)
            self._diag_panel.pack(fill="x", padx=16, pady=(0, 16))
            self._diag_panel.reset()
            self._diag_panel.start_animation()
            self._diag_panel.set_status("正在检测...")
            self._diag_done_count = 0
            done, q, t = run_diagnostics_polling()
        except Exception as ex:
            messagebox.showerror("自检失败", str(ex), parent=self)
            return

        def _poll():
            try:
                while True:
                    msg = q.get_nowait()
                    kind = msg[0]
                    if kind == "status":
                        self.after(0, lambda s=msg[1]: self._diag_status.config(text=s))
                    elif kind == "item":
                        _, key, item = msg
                        st_ = item.get("status", "pending")
                        state = "ok" if st_ == "ok" else ("warn" if st_ in ("warn", "pending") else "fail")
                        detail = str(item.get("detail", ""))
                        self.after(0, lambda k=key, s2=state, d=detail: self._diag_panel.set_state(k, s2, d))
                        self.after(0, lambda: self._diag_panel.set_progress(
                            min(99, int((self._diag_done_count + 1) / self._diag_total * 100)),
                            "实时同步自检结果..."))
                        self._diag_done_count += 1
                    elif kind == "done":
                        result = msg[1] if len(msg) > 1 else {}
                        ok_n = sum(1 for v in result.values() if isinstance(v, dict) and v.get("status") == "ok")
                        warn_n = sum(1 for v in result.values() if isinstance(v, dict) and v.get("status") in ("warn", "pending"))
                        fail_n = sum(1 for v in result.values() if isinstance(v, dict) and v.get("status") == "fail")
                        audit("env_self_check", f"完成: {ok_n} 正常 / {warn_n} 警告 / {fail_n} 异常",
                              level="warn" if fail_n else "info", module="environment_page")
                        # 自我修复: 根据自检结果自动修复常见损坏 (后台执行)
                        def _repair(res=result):
                            try:
                                from infrastructure.self_repair import auto_repair
                                rep = auto_repair(res, progress_cb=self._repair_progress)
                                if rep["fixed"]:
                                    audit("self_repair",
                                          f"自检后自动修复 {rep['fixed']} 项: {rep['actions']}",
                                          level="warn", module="environment_page")
                                    self.after(0, lambda: self._diag_status.config(
                                        text=f"已自动修复 {rep['fixed']} 项", fg=_tc.success))
                                else:
                                    self.after(0, lambda: self._repair_bar.config(value=0))
                            except Exception:
                                pass
                        threading.Thread(target=_repair, daemon=True,
                                         name="self-repair").start()
                        self.after(0, lambda: self._diag_panel.set_progress(
                            100, f"自检完成: {ok_n} 正常 / {warn_n} 警告 / {fail_n} 异常"))
                        self.after(0, self._diag_done)
                        return
            except Exception:
                pass
            try:
                if done.is_set():
                    self.after(0, self._diag_done)
                else:
                    self.after(120, _poll)
            except Exception:
                pass  # 窗口已销毁
        _poll()
    def _adapt_paths_now(self, e=None):
        """用户手动触发: 根据主体文件夹路径立即重配全部程序路径"""
        try:
            audit("path_adapt_manual", "用户点击「立即适配路径」", module="environment_page")
            self._diag_btn.config(state="disabled")
            self._diag_status.config(text="正在适配路径...", fg=_tc.warning)
            from infrastructure import path_guard, path_center
            report = path_guard.ensure_paths_adapted(start_monitor=False, force=True)
            path_center.resync_all()
            issues = path_center.verify_consistency()
            ok = not issues
            detail = (
                f"路径适配完成 ✓\n\n"
                f"根目录: {report['root']}\n"
                f"修复 data.yaml: {len(report['fixed_yamls'])} 个\n"
                f"快捷方式刷新: {report['shortcut_refreshed']}\n"
                f"全程序路径联动: {'所有模块一致 ✓' if ok else '仍有 ' + str(len(issues)) + ' 个模块不一致'}"
            )
            audit("path_adapt_manual",
                  f"完成: 根目录={report['root']} data.yaml修复={len(report['fixed_yamls'])} "
                  f"快捷方式={report['shortcut_refreshed']} 一致={ok}",
                  level="info" if ok else "warn", module="environment_page")
            self._diag_status.config(text="路径适配完成" if ok else "适配完成, 存在警告",
                                     fg=_tc.success if ok else _tc.warning)
            messagebox.showinfo("路径适配", detail, parent=self)
        except Exception as ex:
            audit("path_adapt_manual", f"失败: {ex}", level="error", module="environment_page")
            self._diag_status.config(text="路径适配失败", fg=_tc.danger)
            messagebox.showerror("路径适配失败", str(ex), parent=self)
        finally:
            try:
                self._diag_btn.config(state="normal")
            except Exception:
                pass
    def _auto_repair_now(self, e=None):
        """手动触发自我修复 (对常见损坏项全部检查一遍)"""
        try:
            audit("self_repair_manual", "用户点击「自动修复」", module="environment_page")
            self._diag_status.config(text="正在自动修复...", fg=_tc.warning)
            self._repair_bar.pack(fill="x", padx=16, pady=(0, 12))  # 修复期间显示进度
        except Exception:
            pass

        def _do():
            try:
                from infrastructure.self_repair import auto_repair
                force = {"path": {"status": "warn"}, "files": {"status": "warn"},
                         "logs": {"status": "warn"}, "config": {"status": "warn"},
                         "model": {"status": "warn"}, "cuda": {"status": "warn"},
                         "datasets": {"status": "fail"},
                         "annotation_deps": {"status": "warn"}}
                rep = auto_repair(force, progress_cb=self._repair_progress)
                msg = f"自动修复完成 ({rep['fixed']} 项)"
                if rep["fixed"]:
                    audit("self_repair", f"手动自动修复 {rep['fixed']} 项: {rep['actions']}",
                          level="warn", module="environment_page")
                self.after(0, lambda: (self._diag_status.config(text=msg, fg=_tc.success),
                                       self._repair_bar.pack_forget()))
            except Exception as ex:
                self.after(0, lambda: (self._diag_status.config(
                    text=f"自动修复失败: {ex}", fg=_tc.danger),
                    self._repair_bar.pack_forget()))
        threading.Thread(target=_do, daemon=True, name="self-repair-manual").start()
    def _repair_progress(self, i, total, label):
        """自我修复进度回调 (后台线程 -> 主线程更新进度条)"""
        try:
            self.after(0, lambda: (self._repair_bar.config(maximum=max(1, total), value=i),
                                   self._diag_status.config(
                                       text=f"自动修复 {i}/{total}: {label}",
                                       fg=_tc.warning)))
        except Exception:
            pass
    def _diag_done(self):
        try:
            self._diag_btn.config(state="normal", text="  ▶ 重新自检 ")
            self._diag_status.config(text="自检完成", fg=_tc.success)
            self._diag_panel.stop_animation()
            self._diag_panel.pack_forget()  # 完成即收起 (仅摘要状态可见)
        except Exception:
            pass
