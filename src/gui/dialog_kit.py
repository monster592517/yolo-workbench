# -*- coding: utf-8 -*-
"""dialog_kit.py — 玻璃对话框套件 (子窗口统一美化)

全局替换 tkinter.messagebox / simpledialog 为主题化玻璃对话框:
- 标题栏区 (图标底衬 + 标题) / 内容区 (说明文字或输入框) / 按钮行 (右对齐)
- 相对父窗口居中, Esc=取消, Enter=确认, 主题动态适配
- install() 幂等; 调用方无需改任何代码
"""
from __future__ import annotations

import tkinter as tk
from tkinter import simpledialog, messagebox

from infrastructure.config import get_theme

_icons = {
    "info": ("ℹ️", "info"),
    "warn": ("⚠️", "warn"),
    "error": ("❌", "err"),
    "question": ("❓", "question"),
    "success": ("✅", "ok"),
}

_palette = {
    "info": "primary", "warn": "warning", "err": "danger",
    "question": "primary", "ok": "success",
}


class GlassDialog(tk.Toplevel):
    """主题化模态对话框"""

    def __init__(self, parent, title, message, kind="info", buttons=("确定",),
                 input_label=None, initial=None, width=440, bold_first=False,
                 default=0):
        super().__init__(parent)
        C = get_theme().colors
        self._C = C
        self.result = None
        self._buttons = buttons
        self._default = default
        self._esc_value = None

        self.title(title)
        self.configure(bg=C.bg)
        self.transient(parent.winfo_toplevel() if parent else None)
        self.attributes("-topmost", False)
        try:
            self.grab_set()
        except Exception:
            pass

        icon, _seat = _icons.get(kind, _icons["info"])
        # 图标底衬 v2: 径向柔光 (nav底色 → accent) + 标题
        head = tk.Frame(self, bg=C.bg)
        head.pack(fill="x", padx=18, pady=(16, 4))
        chip = tk.Canvas(head, width=42, height=42, bg=C.bg, highlightthickness=0)
        chip.pack(side="left")

        def _blend(c1, c2, t):
            def _v(s):
                return tuple(int(s.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
            r = tuple(int(a + (b - a) * t) for a, b in zip(_v(c1), _v(c2)))
            return f"#{r[0]:02x}{r[1]:02x}{r[2]:02x}"

        for i in range(20):
            tt = i / 19.0
            m = 1 + int(i * 0.95)
            chip.create_oval(m, m, 41 - m, 41 - m, fill=_blend(C.nav_active_bg, C.accent, tt),
                             outline="")
        chip.create_text(21, 21, text=icon, font=("Microsoft YaHei UI", 14))
        tk.Label(head, text=title, font=("Microsoft YaHei UI", 13, "bold"),
                 bg=C.bg, fg=C.text).pack(side="left", padx=(10, 0))

        # 内容
        body = tk.Frame(self, bg=C.card_bg, highlightbackground=C.border,
                        highlightthickness=1)
        body.pack(fill="both", expand=True, padx=18, pady=(6, 10))
        if message:
            msg_lbl = tk.Label(body, text=message, font=("Microsoft YaHei UI", 9),
                               bg=C.card_bg, fg=C.text_secondary, wraplength=width - 60,
                               justify="left", anchor="w")
            msg_lbl.pack(fill="x", padx=12, pady=(10, 4))
            self._msg_lbl = msg_lbl
        else:
            self._msg_lbl = None
        if input_label is not None:
            tk.Label(body, text=input_label, font=("Microsoft YaHei UI", 8),
                     bg=C.card_bg, fg=C.text_secondary).pack(anchor="w", padx=12, pady=(6, 0))
            self._var = tk.StringVar(master=self, value=initial or "")
            entry = tk.Entry(body, textvariable=self._var, font=("Microsoft YaHei UI", 9),
                             bg=C.input_bg, fg=C.text, insertbackground=C.text,
                             relief="flat", highlightbackground=C.border,
                             highlightthickness=1)
            entry.pack(fill="x", padx=12, ipady=4, pady=(2, 10))
            self._entry = entry
            self.protocol("WM_DELETE_WINDOW", self._cancel)
            entry.bind("<Return>", lambda e: self._ok())
            entry.focus_set()
        else:
            self._entry = None
            self.bind("<Return>", lambda e: self._ok())

        # 按钮行
        row = tk.Frame(self, bg=C.bg)
        row.pack(fill="x", padx=18, pady=(0, 14))
        for i, btext in enumerate(reversed(buttons)):
            palette = {
                "primary": (C.primary, "#FFFFFF"), "success": (C.success, "#FFFFFF"),
                "danger": (C.danger, "#FFFFFF"), "border": (C.border, C.text),
                "ghost": (C.card_bg, C.text_secondary),
            }
            if i == 0:  # 第一个按钮 (reversed 后 = 最右)
                bg, fg = palette.get("primary")
            else:
                bg, fg = palette.get("ghost")
            b = tk.Label(row, text="  " + btext + "  ", font=("Microsoft YaHei UI", 9, "bold"),
                         bg=bg, fg=fg, padx=14, pady=6, cursor="hand2")
            idx = len(buttons) - 1 - i
            b.pack(side="right", padx=(6, 0))
            b.bind("<Button-1>", lambda e, i=idx: self._select(i))
        self.bind("<Escape>", lambda e: self._cancel())

        # 尺寸与居中 (相对父窗口)
        self.update_idletasks()
        if self._entry is not None:
            w = width  # 输入型固定宽度 (输入框展开)
        else:
            w = min(width, self.winfo_reqwidth() + 40)
        h = self.winfo_reqheight()
        try:
            pw = parent.winfo_width(); ph = parent.winfo_height()
            px = parent.winfo_rootx(); py = parent.winfo_rooty()
            x = px + max(0, (pw - w) // 2)
            y = py + max(0, (ph - h) // 2)
        except Exception:
            x = y = 0
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _select(self, idx):
        self.result = self._buttons[idx]
        if self._entry is not None and idx == self._default:
            pass
        self.destroy()

    def _ok(self):
        self.result = self._buttons[self._default]
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


# ═══════════════ messagebox / simpledialog 替身 ═══════════════
def _run(parent, title, message, kind, buttons, default=0, input_label=None,
         initial=None):
    dlg = GlassDialog(parent, title, message, kind=kind, buttons=buttons,
                      default=default, input_label=input_label, initial=initial)
    parent = parent or tk._default_root
    try:
        dlg.wait_window()
    except Exception:
        pass
    res = dlg.result
    return res


def showinfo(title, message, **kw):
    _run(kw.get("parent"), title, message, "info", ("确定",))
    return "ok"


def showwarning(title, message, **kw):
    _run(kw.get("parent"), title, message, "warn", ("确定",))
    return "ok"


def showerror(title, message, **kw):
    _run(kw.get("parent"), title, message, "error", ("确定",))
    return "ok"


def askyesno(title, message, **kw):
    r = _run(kw.get("parent"), title, message, "question", ("否", "是"), default=1)
    return bool(r == "是")


def askokcancel(title, message, **kw):
    r = _run(kw.get("parent"), title, message, "question", ("取消", "确定"), default=1)
    return bool(r == "确定")


def askyesnocancel(title, message, **kw):
    r = _run(kw.get("parent"), title, message, "question", ("取消", "否", "是"), default=2)
    if r == "是":
        return True
    if r == "否":
        return False
    return None


def askstring(title, prompt, initialvalue="", **kw):
    """simpledialog.askstring 替身 (玻璃输入框对话框)"""
    parent = kw.get("parent")
    dlg = GlassDialog(parent, title, "", kind="question", buttons=("取消", "确定"),
                      default=1, input_label=prompt, initial=initialvalue)
    try:
        dlg.wait_window()
    except Exception:
        pass
    if dlg.result == "确定":
        try:
            return dlg._var.get()
        except Exception:
            pass
    return None


_installed = False


def install():
    """一次性替换 tkinter.messagebox / simpledialog (幂等)"""
    global _installed
    if _installed:
        return
    _installed = True
    messagebox.showinfo = showinfo
    messagebox.showwarning = showwarning
    messagebox.showerror = showerror
    messagebox.askyesno = askyesno
    messagebox.askokcancel = askokcancel
    messagebox.askyesnocancel = askyesnocancel
    simpledialog.askstring = askstring
