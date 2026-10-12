# -*- coding: utf-8 -*-
"""⌘K 命令面板 — 全局快速调起: 页面/步骤/操作 模糊搜索直达 (v8 旗舰交互)"""
from __future__ import annotations

import tkinter as tk

from infrastructure.config import get_theme
from gui.components import gradient_pill

# 与 main_window.NAVS 对齐 —— 少了键的页面在 ⌘K 里就搜不到。
# 2026-10-01: H3/仿真/部署三条线搬出本仓，页面收敛到 5 个；
#             detection(检测) 重新回到导航。
KEYS = {"detection": "🎯", "dataset": "🗂", "training": "🎓",
        "env": "🔧", "settings": "⚙️"}


class CommandPalette(tk.Toplevel):
    """Ctrl+K 命令面板 (搜索 + 执行)"""

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        C = get_theme().colors
        self.configure(bg=C.bg)
        self.title("命令面板")
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self._sel = 0
        self._cmds = self._build_commands()
        w, h = 620, 470
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2 - 60}")
        try:
            self.grab_set()
        except Exception:
            pass

        # 搜索框
        top = tk.Frame(self, bg=C.card_bg, highlightbackground=C.border,
                       highlightthickness=1)
        top.pack(fill="x", padx=14, pady=(12, 4))
        tk.Label(top, text="⌘K  ", font=("Microsoft YaHei UI", 13, "bold"),
                 bg=C.card_bg, fg=C.accent).pack(side="left", padx=(10, 0))
        self._var = tk.StringVar()
        e = tk.Entry(top, textvariable=self._var, font=("Microsoft YaHei UI", 12),
                     bg=C.card_bg, fg=C.text, relief="flat", insertbackground=C.text)
        e.pack(side="left", fill="x", expand=True, padx=6, ipady=5)
        self._entry = e
        e.bind("<KeyRelease>", lambda ev: self._filter())
        e.bind("<Return>", lambda ev: self._run())
        e.bind("<Escape>", lambda ev: self.destroy())
        e.bind("<Up>", lambda ev: self._move(-1))
        e.bind("<Down>", lambda ev: self._move(1))

        # 结果列表
        box = tk.Frame(self, bg=C.bg)
        box.pack(fill="both", expand=True, padx=14, pady=(2, 8))
        self._items = []
        host = tk.Frame(box, bg=C.bg)
        host.pack(fill="both", expand=True)
        self._host = host
        self._filter()
        e.focus_set()
        self.bind("<Escape>", lambda ev: self.destroy())

    # ── 命令构建 ──
    def _build_commands(self):
        cmds = []
        # 页面
        for key in KEYS:
            cmds.append((f"打开页面 · {self._page_label(key)}", "page", key))
        # 动作
        def _act(page_key, fn, label):
            cmds.append((label, "action", (page_key, fn)))
        _act("training", lambda: self._run_page_fn("training", "_open_model_manager"), "📚 模型库管理")
        _act("dataset", lambda: self._run_page_fn("dataset", "on_show"), "🗂 数据集与标注")
        cmds.append(("🎨 切换主题", "action", (None, lambda: self.app.apply_theme())))
        return cmds

    def _page_label(self, key):
        try:
            from gui.main_window import NAVS
            for k, icon, label, group in NAVS:
                if k == key:
                    return label
            return key
        except Exception:
            return key


    def _run_page_fn(self, page_key, fn):
        try:
            page = self.app._ensure_page(page_key)
            if page is not None and hasattr(page, fn):
                self.app.show_page(page_key, animate=False)
                self.app.root.after(120, lambda: getattr(page, fn)())
        except Exception:
            pass

    # ── 过滤与执行 ──
    def _blend(self, c1, c2, t):
        def _v(s):
            return tuple(int(s.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
        r = tuple(int(a + (b - a) * t) for a, b in zip(_v(c1), _v(c2)))
        return f"#{r[0]:02x}{r[1]:02x}{r[2]:02x}"

    def _sel_bg(self):
        C = get_theme().colors
        return self._blend(C.card_bg, C.accent, 0.14)  # 选中行: accent 淡染

    def _filter(self):
        q = self._var.get().strip().lower()
        rows = [c for c in self._cmds if not q or q in c[0].lower()]
        for w in self._host.winfo_children():
            w.destroy()
        self._items = rows
        self._sel = 0
        C = get_theme().colors
        for i, (label, kind, payload) in enumerate(rows[:9]):
            r = tk.Frame(self._host, bg=self._sel_bg() if i == 0 else C.card_bg,
                         cursor="hand2")
            r.pack(fill="x", pady=2)
            tag = {"page": "页", "step": "步", "action": "⚡"}.get(kind, "·")
            t1 = tk.Label(r, text=f"  {tag}  ", font=("Microsoft YaHei UI", 9, "bold"),
                          bg=r.cget("bg"), fg=C.accent if i == 0 else C.text_secondary)
            t1.pack(side="left", padx=(8, 2))
            tk.Label(r, text=label, font=("Microsoft YaHei UI", 10),
                     bg=r.cget("bg"), fg=C.text, anchor="w").pack(
                side="left", fill="x", expand=True, pady=6)
            arrow = tk.Label(r, text="↵", font=("Microsoft YaHei UI", 9, "bold"),
                             bg=r.cget("bg"),
                             fg=C.accent if i == 0 else C.card_bg)
            arrow.pack(side="right", padx=8)
            r.bind("<Button-1>", lambda e, idx=i: self._select(idx))
            r.bind("<Enter>", lambda e, idx=i: self._select(idx))
            r._idx = i

    def _select(self, idx):
        self._sel = idx
        C = get_theme().colors
        for i, r in enumerate(self._host.winfo_children()):
            try:
                bg = self._sel_bg() if i == idx else C.card_bg
                r.config(bg=bg)
                kids = r.winfo_children()
                for c in kids[:-1]:
                    c.config(bg=bg)
                    if c is kids[0]:
                        c.config(fg=C.accent if i == idx else C.text_secondary)
                if kids:
                    kids[-1].config(text="↵" if i == idx else "",
                                    fg=C.accent if i == idx else C.card_bg,
                                    bg=bg)
            except Exception:
                pass

    def _move(self, d):
        n = len(self._host.winfo_children())
        if n:
            self._select(min(n - 1, max(0, self._sel + d)))

    def _run(self):
        if not self._items:
            self.destroy()
            return
        kind, payload = self._items[self._sel][1], self._items[self._sel][2]
        self.destroy()
        if kind == "page":
            self.app.show_page(payload, animate=False)
        else:
            pkey, fn = payload
            if pkey:
                self._run_page_fn(pkey, fn)
            else:
                try:
                    fn()
                except Exception:
                    pass
