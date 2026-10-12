# -*- coding: utf-8 -*-
"""设置页 — 外观主题 + 系统日志 + 会话记录

2026-09-11: 「环境自检」整卡迁往 environment_page —— 自检检的是依赖包/磁盘/
CUDA/部署包/Python环境, 与环境配置页的"检测+一键装依赖"是同一件事的两半。
"""
from __future__ import annotations
import os
import queue
import threading
import tkinter as tk
from gui import poll_kit as _pk
import tkinter.scrolledtext as st
from tkinter import ttk, messagebox

from infrastructure.config import get_config, get_theme
from gui.theme_manager import theme_manager
from gui.components import DiagnosticsPanel, sub_card, step_card, scrollable
from infrastructure.audit import audit

_tc = get_theme().colors
_cfg = get_config()


class SettingsPage(tk.Frame):
    def __init__(self, parent, controller=None):
        super().__init__(parent, bg=_tc.bg)
        self.controller = controller
        BG = _tc.bg; C = _tc.card_bg; T = _tc.text; S = _tc.text_secondary

        from gui.components import topbar, sub_card
        topbar(self, "⚙️", "设置", "外观主题 | 系统日志 | 会话记录")
        tk.Frame(self, height=1, bg=_tc.border).pack(fill="x", padx=24, pady=(0, 8))

        body_o, body = scrollable(self, bg=BG)
        body_o.pack(fill="both", expand=True, padx=24, pady=(0, 12))
        # ★ 2026-10-03 按功能重排 (v0.34.0)。
        #   重排前: 左列只有「外观主题」(550×265), 右列竖排「系统日志」(550×518)
        #   + 「会话记录」(550×291) ⇒ **左列 550 宽只用了 265px, 一半是空的**
        #   (实测 `scripts/ui_page_geom.py settings`)。
        #   重排后按"谁需要大高度"分:
        #     左 = 外观主题(定高 265) + 会话记录(291) = 556  ← 两个矮块叠起来正好
        #     右 = 系统日志(518, 唯一需要"越高越好"的: 它是滚动文本框)
        #   日志是**唯一会因为变高而更有用**的块, 所以让它独占一列、吃满 765;
        #   主题和会话是"看两眼就走"的块, 叠在左列, 不浪费右列的纵向空间。
        body.grid_rowconfigure(0, weight=0)
        body.grid_rowconfigure(1, weight=0)
        body.grid_columnconfigure(0, weight=1, uniform="set")
        body.grid_columnconfigure(1, weight=1, uniform="set")

        # ── 左列: 外观主题 (上) + 会话记录 (下) ──
        left = tk.Frame(body, bg=BG)
        self._left_col = left
        left.grid(row=0, column=0, sticky="new", padx=(0, 8))
        self._build_theme_card(left)
        self._build_session_card(left)

        # ── 右列: 系统日志 (独占, 吃满整列高) ──
        self._build_log_card(body)

    def _build_theme_card(self, parent):
        BG = _tc.bg; C = _tc.card_bg; T = _tc.text; S = _tc.text_secondary
        c, cb = step_card(parent, 1, "外观主题", "浅色/深色/跟随系统时间")
        c.pack(fill="x", pady=(0, 8))
        self._set_theme_card = c
        self._mode_var = tk.StringVar(master=self, value=theme_manager.get_mode())
        for label, val in [("☀️  浅色模式", "light"), ("🌙  深色模式", "dark"), ("🕐  跟随系统时间", "auto")]:
            rb = tk.Radiobutton(cb, text=label, variable=self._mode_var, value=val,
                                command=self._on_mode, bg=C, fg=T, selectcolor=C,
                                activebackground=C, activeforeground=T,
                                font=("Microsoft YaHei UI", 10), anchor="w")
            rb.pack(fill="x", padx=16, pady=2)

        self._mode_hint = tk.Label(cb, text="", font=("Microsoft YaHei UI", 9), bg=C,
                                   fg=_tc.text_muted, anchor="w", justify="left", wraplength=400)
        self._mode_hint.pack(fill="x", padx=16, pady=(4, 0))

        # 预览色块
        pv = tk.Frame(cb, bg=C); pv.pack(fill="x", padx=16, pady=(8, 16))
        tk.Label(pv, text="预览:", font=("Microsoft YaHei UI", 9), bg=C, fg=S).pack(side="left", padx=(0, 8))
        self._sw_side = tk.Frame(pv, width=64, height=28, highlightbackground=_tc.border,
                                 highlightthickness=1)
        self._sw_side.pack(side="left", padx=(0, 8))
        self._sw_card = tk.Frame(pv, width=64, height=28, highlightbackground=_tc.border,
                                 highlightthickness=1)
        self._sw_card.pack(side="left", padx=(0, 8))
        self._sw_btn = tk.Frame(pv, width=64, height=28)
        self._sw_btn.pack(side="left")
        self._update_preview()

    def _on_mode(self):
        mode = self._mode_var.get()
        if self.controller and hasattr(self.controller, "apply_theme"):
            # 延迟执行, 避免在事件处理中销毁自身
            self.controller.root.after(80, lambda: self.controller.apply_theme(mode))

    def _update_preview(self):
        resolved = theme_manager.resolve_mode()
        from gui.theme_manager import LIGHT_COLORS, DARK_COLORS
        src = LIGHT_COLORS if resolved == "light" else DARK_COLORS
        for sw, color in ((self._sw_side, src.sidebar_bg), (self._sw_card, src.card_bg)):
            try:
                sw.config(bg=color)
            except Exception:
                pass
        try:
            self._sw_btn.config(bg=src.primary)
        except Exception:
            pass
        mode = theme_manager.get_mode()
        hint = "跟随系统时间: 6:00-18:00 自动浅色, 其余时间深色"
        if mode == "light":
            hint = "当前固定为浅色模式"
        elif mode == "dark":
            hint = "当前固定为深色模式"
        try:
            self._mode_hint.config(text=hint + f"  (当前生效: {'浅色' if resolved == 'light' else '深色'})")
        except Exception:
            pass

    # ═══════ 系统日志 ═══════
    def _build_log_card(self, parent):
        BG = _tc.bg; C = _tc.card_bg; T = _tc.text; S = _tc.text_secondary
        c, cb = step_card(parent, 2, "系统日志", "运行/审计日志")
        # ★ 2026-10-03: 日志独占右列, 直接铺满整列高 (它本来是"越高越好"的滚动框)。
        c.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self._set_log_card = c
        self._log_desc = tk.Label(cb, text="runs/logs/app.log  (运行日志, 自动滚动保存 10MB × 5 份)",
                                  font=("Microsoft YaHei UI", 9), bg=C, fg=S)
        self._log_desc.pack(anchor="w", padx=16, pady=(8, 2))

        row = tk.Frame(cb, bg=C); row.pack(fill="x", padx=16, pady=(4, 8))
        # 坑 (2026-09-11 修): 计数标签原本是**最后**才 pack 的, 而 Tk 的 packer
        # 按打包顺序分配空间 —— 前面四个左靠按钮先把宽度吃掉, 轮到它时只剩 66px,
        # "6948 KB / 600 行" 需要 102px, 于是右边被直接切掉 (用户报"文字没全放出来")。
        # 修法: 先 pack 它 (side="right"), 让它优先拿到自然宽度, 再放左边那组。
        self._log_count = tk.Label(row, text="", font=("Microsoft YaHei UI", 9), bg=C, fg=S)
        self._log_count.pack(side="right", padx=(8, 0))
        self._log_mode = "app"  # app | audit
        self._tab_app = tk.Label(row, text=" 运行日志 ", font=("Microsoft YaHei UI", 9, "bold"),
                                 bg=_tc.sidebar_hover, fg=T, padx=8, pady=4, cursor="hand2")
        self._tab_app.pack(side="left", padx=(0, 4))
        self._tab_app.bind("<Button-1>", lambda e: self._switch_log("app"))
        self._tab_audit = tk.Label(row, text=" 审计日志 ", font=("Microsoft YaHei UI", 9, "bold"),
                                   bg=_tc.border, fg=T, padx=8, pady=4, cursor="hand2")
        self._tab_audit.pack(side="left", padx=(0, 8))
        self._tab_audit.bind("<Button-1>", lambda e: self._switch_log("audit"))
        refresh = tk.Label(row, text="  🔄 刷新 ", font=("Microsoft YaHei UI", 9, "bold"),
                           bg=_tc.sidebar_hover, fg=T, padx=12, pady=4, cursor="hand2")
        refresh.pack(side="left", padx=(0, 8))
        refresh.bind("<Button-1>", lambda e: self._refresh_logs())
        opendir = tk.Label(row, text="  📂 打开日志目录 ", font=("Microsoft YaHei UI", 9),
                           bg=_tc.border, fg=T, padx=12, pady=4, cursor="hand2")
        opendir.pack(side="left")
        opendir.bind("<Button-1>", lambda e: self._open_log_dir())

        from gui.components import dark_scrolledtext
        self._log_text = st.ScrolledText(cb, font=("Consolas", 9),
                                         bg=_tc.log_bg, fg=_tc.log_text,
                                         relief="flat", bd=0, padx=8, pady=8)
        # ⚠ 2026-09-12: 旧写法 `dark_scrollbar(self._log_text.vbar)` 对
        # `tk.Scrollbar` **一个字都不生效** (本机它由 Windows 原生绘制), 而且
        # ScrolledText 自带的 `frame` 底色也没跟主题走 ⇒ 深色卡片里一圈浅边 + 一条近白竖带。
        dark_scrolledtext(self._log_text, frame_bg=_tc.card_bg)
        self._log_text.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self._log_text.configure(state="disabled")

        # 坑 (2026-09-11, 静默失败): `self._log_q` 与 `self._log_cache` **从来没有被
        # 初始化过** —— 后台读日志线程里的 `self._log_q.put(...)` 抛 AttributeError,
        # 被 `except Exception: pass` 吞掉; `_poll_log_q` 也从未被调度。
        # 结果: 整个"系统日志"面板永远空白 (实测构造后跑 2 秒, 0 行文本),
        # 而界面上看不出任何报错 —— 典型的静默失效。
        self._log_q = queue.Queue()
        self._log_cache = {}
        self._log_loaded = False
        # 常驻读日志线程**在这里**就起 —— `Thread.start()` 的首次等待避不开
        # (实测 539~950ms, 见 _log_ensure_worker), 那就把它放在"页面预建"阶段,
        # 别留给用户第一次切到本页时。此后每次刷新只是往队列塞一个请求, 零创建成本。
        try:
            self._log_ensure_worker()
        except Exception:
            pass
        self.after(300, self._poll_log_q)

        return None

    def _open_log_dir(self, e=None):
        """打开日志目录。

        2026-09-11 补: 按钮 (📂 打开日志目录) 原本绑到一个**不存在的方法**,
        Tk 回调里的 AttributeError 只写 stderr, 界面上表现为"点了没反应"。
        """
        try:
            d = _cfg.log_dir
            d.mkdir(parents=True, exist_ok=True)
            from utils.file_utils import open_file_explorer
            open_file_explorer(d)
            audit("log_dir_open", f"打开日志目录: {d}", module="settings_page")
        except Exception as ex:
            messagebox.showerror("打开日志目录失败", str(ex), parent=self)

    # ═══════ 外观主题 ═══════
    def _switch_log(self, mode):
        self._log_mode = mode
        try:
            hl = _tc.sidebar_hover
            dim = _tc.border
            self._tab_app.config(bg=hl if mode == "app" else dim)
            self._tab_audit.config(bg=hl if mode == "audit" else dim)
        except Exception:
            pass
        self._refresh_logs()

    def _build_session_card(self, parent):
        """会话记录卡 (原开发者页历史会话迁入)。

        ★ 2026-10-03: 挂载点由 `body` 的 (row=1, col=1) 改为**左列内部 pack** ——
          左列现在有两个矮块 (外观主题 265 + 会话记录 291), 右列让给日志独占。
        """
        BG = _tc.bg; C = _tc.card_bg; T = _tc.text; S = _tc.text_secondary
        c, cb = step_card(parent, 3, "会话记录", ".codex / DSH 会话浏览")
        c.pack(fill="x", pady=(8, 0))
        row = tk.Frame(cb, bg=C); row.pack(fill="x", padx=16, pady=(8, 0))
        self._sess_list = tk.Listbox(row, height=5, bg=_tc.input_bg, fg=T,
                                     selectbackground=_tc.primary, relief="flat",
                                     font=("Microsoft YaHei UI", 9), exportselection=False)
        self._sess_list.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self._sess_list.bind("<<ListboxSelect>>", lambda e: self._sess_show())
        rl = tk.Label(row, text="  🔄 刷新 ", font=("Microsoft YaHei UI", 9, "bold"), bg=_tc.purple,
                      fg="white", padx=12, pady=4, cursor="hand2")
        rl.pack(side="left")
        rl.bind("<Button-1>", lambda e: self._sess_load(force=True))
        self._sess_txt = tk.Text(cb, height=7, font=("Consolas", 8), bg=_tc.log_bg,
                                 fg=_tc.log_text, relief="flat",
                                 highlightthickness=1, highlightbackground=_tc.border,
                                 state="disabled", wrap="word")
        self._sess_txt.pack(fill="both", expand=True, padx=16, pady=(4, 12))
        self._sess_sessions = []
        self._sess_busy = False
        self._sess_q = queue.Queue()
        # 坑 (2026-09-11): 这里原来是 self.after(300, self._sess_load), 而 _sess_load
        # 在主线程里全树扫 ~/.dsh/sessions (暖盘 112ms / 冷盘 0.5~1.4s)。
        # 设置页是**预建**的, 页面压根没显示也会触发 —— 看门狗实测主线程停滞
        # 540 / 956 / 1367 ms, 这才是"启动后卡一下"的真凶。
        # 现在: 后台线程扫描 → 结果进队列 → 主线程轮询取出渲染
        # (与 _refresh_logs 同一套路; 绝不从子线程碰 Tcl 控件)。
        self.after(300, self._sess_load)

    def _sess_ensure_worker(self):
        """常驻会话扫描线程 (只在首次刷新时创建一次)。

        2026-09-11: 原实现每次刷新都 `threading.Thread(...).start()`。看门狗实测
        `Thread.start()` 会在主线程 `self._started.wait()` 里等新线程抢到 GIL 完成
        bootstrap, 偶发 120ms+ —— 缩略图解码线程踩的是同一个坑 (见 h3_factory_page
        ._pf_ensure)。改成常驻线程 + 单槽请求队列, 之后每次刷新零线程创建成本。
        """
        th = getattr(self, "_sess_th", None)
        if th is not None and th.is_alive():
            return
        self._sess_req = queue.Queue(maxsize=1)
        self._sess_th = threading.Thread(target=self._sess_worker, daemon=True,
                                         name="sess-scan")
        self._sess_th.start()

    def _sess_worker(self):
        """常驻扫描线程 —— 只读文件, **绝不碰任何 Tk 控件**。"""
        while True:
            try:
                force = self._sess_req.get()
            except Exception:
                return
            if force is None:
                return
            rows = []
            try:
                from core.history_viewer import list_sessions
                rows = list_sessions(use_cache=not force)
            except Exception:
                rows = []
            try:
                self._sess_q.put(rows)
            except Exception:
                pass

    def _sess_load(self, force: bool = False):
        """会话列表: 后台线程扫描 (绝不阻塞主线程), 完成后经队列回主线程渲染。"""
        if getattr(self, "_sess_busy", False):
            return
        self._sess_busy = True
        try:
            self._sess_list.delete(0, "end")
            self._sess_list.insert("end", "（正在后台扫描会话…）")
        except Exception:
            pass
        try:
            self._sess_ensure_worker()
        except Exception:
            self._sess_busy = False
            return
        try:                       # 单槽: 丢掉上一次还没被处理的请求
            while True:
                self._sess_req.get_nowait()
        except Exception:
            pass
        try:
            self._sess_req.put_nowait(force)
        except Exception:
            self._sess_busy = False

    def _sess_done(self, rows):
        """主线程渲染 (只有这里碰控件)。

        ⚠ 2026-09-11 卡顿修复: 原来**逐行** `insert("end", ...)`, 卡顿看门狗实测这一条
        独占主线程 **772 ms** (12s 窗口内单次最长的一条之一)。瓶颈不是行数, 而是
        Listbox 挂着 yscrollcommand ⇒ **每一行** insert 都会回调一次 Tcl 命令,
        N 行 = N 次 Python 往返。`insert` 本来就接受可变个元素, 一次调用插完全部,
        N 次往返塌成 1 次。
        附带: 列表本来就空时不再无脑 `delete(0, "end")` —— 那个 delete 同样会触发
        一次滚动条重算。
        """
        self._sess_busy = False
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        self._sess_sessions = rows or []
        try:
            lb = self._sess_list
            if lb.size():
                lb.delete(0, "end")
            if self._sess_sessions:
                lb.insert("end", *[
                    "[%s] %s %sMB  %s" % (r["source"], r["mtime"],
                                          r["size_mb"], r["title"][:36])
                    for r in self._sess_sessions])
            else:
                lb.insert("end", "（未找到会话记录）")
        except Exception:
            pass

    def _sess_show(self):
        sel = self._sess_list.curselection()
        if not sel or sel[0] >= len(self._sess_sessions):
            return
        r = self._sess_sessions[sel[0]]
        try:
            from core.history_viewer import preview
            msgs = preview(r["file"], max_messages=24)
        except Exception as e:
            msgs = []
        self._sess_txt.config(state="normal")
        self._sess_txt.delete("1.0", "end")
        for m in msgs[:24]:
            tag = "🙋" if m["role"] == "user" else "🤖"
            self._sess_txt.insert("end", f"{tag} {m['text'].replace(chr(10), ' ')[:160]}\n")
        if not msgs:
            self._sess_txt.insert("end", "（没有可显示的消息）\n")
        self._sess_txt.config(state="disabled")

    def _log_ensure_worker(self):
        """常驻日志读取线程 —— **绝不要**每次刷新都新建一条线程。

        坑 (2026-09-11, 已用看门狗定位): 原来这里是
            threading.Thread(target=_read, daemon=True).start()
        而 `Thread.start()` 内部会 `self._started.wait()` —— 同步等新线程抢到 GIL
        完成 bootstrap。卡顿看门狗实测这一步把主线程卡住 **539 / 950 ms**
        (同机裸标定 `Thread.start()` 只要 0.14~0.8 ms/次 ⇒ 时间不在"创建"上,
        在"等待")。同一个坑 `h3_factory_page._pf_ensure` 已经踩过, 这里照改成常驻线程。
        """
        th = getattr(self, "_log_th", None)
        if th is not None and th.is_alive():
            return
        self._log_req = queue.Queue(maxsize=1)
        self._log_th = threading.Thread(target=self._log_worker, daemon=True,
                                        name="log-read")
        self._log_th.start()

    def _log_worker(self):
        """常驻 worker: 只读文件, **绝不碰任何 Tk 控件**。"""
        while True:
            try:
                job = self._log_req.get()
            except Exception:
                return
            if job is None:
                return
            mode, fp = job
            try:
                content, count_txt, desc = self._log_read(mode)
                self._log_q.put(("ok", mode, content, count_txt, desc, fp))
            except Exception as e:
                try:
                    self._log_q.put(("err", str(e)))
                except Exception:
                    pass

    @staticmethod
    def _log_read(mode):
        """纯读盘 + 组文本, 返回 (content, count_txt, desc)。不依赖 Tk。"""
        if mode == "audit":
            from infrastructure.audit import recent_operations
            recs = recent_operations(600)
            rows = ["%s [%s] %-18s %s %s" % (
                r.get("ts", ""), r.get("level", ""), r.get("action", ""),
                r.get("module", ""), str(r.get("detail", ""))[:150]) for r in recs]
            content = chr(10).join(rows) if recs else "暂无审计记录"
            count_txt = f"{len(recs)} 条审计记录" if recs else ""
            return content, count_txt, "runs/logs/operations.log  (企业级审计: 全部运行情况与报错)"
        lp = _cfg.log_dir / "app.log"
        if lp.exists():
            file_lines = lp.read_text(encoding="utf-8").splitlines()[-600:]
            content = "\n".join(file_lines)
            count_txt = f"{lp.stat().st_size // 1024} KB / {len(file_lines)} 行"
        else:
            content = "暂无日志"
            count_txt = ""
        return content, count_txt, "runs/logs/app.log  (运行日志, 自动滚动保存 10MB × 5 份)"

    def _refresh_logs(self):
        """日志读取: 常驻后台线程 + 文件指纹缓存 (切换页面不卡顿)"""
        mode = self._log_mode
        fp = None
        try:
            lp = _cfg.log_dir / ("operations.log" if mode == "audit" else "app.log")
            fp = (mode, lp.stat().st_mtime_ns, lp.stat().st_size) if lp.exists() else (mode, 0, 0)
            if self._log_cache.get("fp") == fp:
                self._apply_logs(*self._log_cache["data"])
                return
        except Exception:
            pass
        try:
            self._log_ensure_worker()
        except Exception:
            return
        try:                       # 单槽: 丢掉上一次还没被处理的请求
            while True:
                self._log_req.get_nowait()
        except Exception:
            pass
        try:
            self._log_req.put_nowait((mode, fp))
        except Exception:
            pass

    def _poll_log_q(self):
        busy = False
        try:
            while True:
                msg = self._log_q.get_nowait()
                busy = True
                if msg[0] == "ok":
                    _, mode, content, count_txt, desc, fp = msg
                    if mode == self._log_mode:
                        self._apply_logs(content, count_txt, desc)
                        self._log_cache = {"fp": fp, "data": (content, count_txt, desc)}
                elif msg[0] == "err":
                    try:
                        self._log_count.config(text="日志读取失败")
                    except Exception:
                        pass
        except queue.Empty:
            pass
        except Exception:
            return
        # 会话扫描结果 (后台线程 → 主线程的唯一合法通道)
        try:
            while True:
                self._sess_done(self._sess_q.get_nowait())
                busy = True
        except queue.Empty:
            pass
        except Exception:
            pass
        # 首次进入前台才真正加载日志 —— 预建阶段不跟启动抢磁盘
        if not getattr(self, "_log_loaded", False):
            try:
                if _pk.active_page(self):
                    self._log_loaded = True
                    self._refresh_logs()
            except Exception:
                pass
        try:
            self.after(_pk.poll_delay(self, busy), self._poll_log_q)
        except Exception:
            pass

    def _apply_logs(self, content, count_txt, desc):
        try:
            self._log_count.config(text=count_txt)
            self._log_desc.config(text=desc)
            self._log_text.configure(state="normal")
            self._log_text.delete("1.0", "end")
            self._log_text.insert("1.0", content)
            self._log_text.see("end")
            self._log_text.configure(state="disabled")
        except Exception:
            pass