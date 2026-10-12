# -*- coding: utf-8 -*-
"""模型库管理窗口 — 拖拽导入 .pt / 删除模型 / 浏览导入 / 打开模型文件夹

独立模态窗口, 从训练页 "模型库管理" 按钮打开。
所有操作写入审计日志 (operations.log), 支持企业维护追踪。
"""
from __future__ import annotations

import os
import shutil
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog, messagebox

from infrastructure.config import get_config
from infrastructure.audit import audit
from gui.components import dark_scrollbar

_cfg = get_config()
_FONT = ("Microsoft YaHei UI", 10)
_FONT_SM = ("Microsoft YaHei UI", 9)
_FONT_MONO = ("Consolas", 9)


def _fmt_size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n} B"


class ModelManagerDialog(tk.Toplevel):
    """模型库管理: 列表展示 runs/outputs 下全部 .pt, 支持拖拽导入与删除"""

    def __init__(self, parent, on_changed=None):
        super().__init__(parent)
        self.on_changed = on_changed
        self.title("模型库管理")
        self.geometry("680x430")
        self.minsize(560, 340)
        self.configure(bg=parent.cget("bg"))
        self.transient(parent.winfo_toplevel())
        try:
            self.resizable(True, True)
        except Exception:
            pass

        try:
            from gui.theme_manager import theme_manager
            theme_manager.apply()
        except Exception:
            pass
        from infrastructure.config import get_theme
        C = get_theme().colors
        self._C = C

        # ── 玻璃头部: 图标底衬 + 标题 ──
        head = tk.Frame(self, bg=C.bg)
        head.pack(fill="x", padx=14, pady=(12, 2))
        chip = tk.Canvas(head, width=38, height=38, bg=C.bg, highlightthickness=0)
        chip.pack(side="left")
        chip.create_oval(1, 1, 37, 37, fill=C.nav_active_bg, outline=C.border)
        chip.create_text(19, 18, text="📦", font=("Microsoft YaHei UI", 13))
        tk.Label(head, text="模型库管理", font=("Microsoft YaHei UI", 14, "bold"),
                 bg=C.bg, fg=C.text).pack(side="left", padx=(10, 0))
        self._count_lbl = tk.Label(head, text="", font=_FONT_SM, bg=C.bg, fg=C.text_muted)
        self._count_lbl.pack(side="right")

        # ── 顶部提示 ──
        tip = tk.Label(self, text=f"模型目录: {_cfg.output_dir}   ·   拖拽 .pt 文件到窗口任意位置即可导入",
                       font=_FONT_SM, bg=C.bg, fg=C.text_secondary, anchor="w")
        tip.pack(fill="x", padx=14, pady=(2, 6))

        # ── 工具栏 ──
        bar = tk.Frame(self, bg=C.bg)
        bar.pack(fill="x", padx=14, pady=(0, 6))
        self._btn_import = self._tool_btn(bar, "   📂 浏览导入  ", self._browse_import, C.primary)
        self._btn_import.pack(side="left")
        self._btn_refresh = self._tool_btn(bar, "   🔄 刷新  ", self.refresh, C.card_bg, fg=C.text)
        self._btn_refresh.pack(side="left", padx=(6, 0))
        self._btn_open = self._tool_btn(bar, "   📁 打开文件夹  ", self._open_dir, C.card_bg, fg=C.text)
        self._btn_open.pack(side="left", padx=(6, 0))

        # ── 模型列表 ──
        wrap = tk.Frame(self, bg=C.card_bg, highlightbackground=C.border, highlightthickness=1)
        wrap.pack(fill="both", expand=True, padx=14, pady=(0, 6))
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("ModelList.Treeview",
                        background=C.input_bg, fieldbackground=C.input_bg,
                        foreground=C.text, borderwidth=0, relief="flat",
                        font=_FONT_SM, rowheight=26)
        style.configure("ModelList.Treeview.Heading",
                        background=C.sidebar_hover, foreground=C.text,
                        font=_FONT_SM, relief="flat", borderwidth=1)
        style.map("ModelList.Treeview",
                  background=[("selected", C.primary)],
                  foreground=[("selected", C.text)])
        self._tree = ttk.Treeview(wrap, columns=("name", "size", "time"),
                                  show="headings", style="ModelList.Treeview")
        self._tree.heading("name", text="模型名称")
        self._tree.heading("size", text="大小")
        self._tree.heading("time", text="修改时间")
        self._tree.column("name", width=300, anchor="w")
        self._tree.column("size", width=90, anchor="center")
        self._tree.column("time", width=150, anchor="center")
        style.configure("ModelList.Vertical.TScrollbar",
                        background=C.border, troughcolor=C.input_bg,
                        arrowcolor=C.text_muted, bordercolor=C.input_bg)
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=self._tree.yview,
                            style="ModelList.Vertical.TScrollbar")
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self._tree.bind("<Delete>", lambda e: self._delete_selected())
        self._tree.bind("<Double-1>", lambda e: self._delete_selected())

        # ── 底部: 拖拽导入区 + 删除按钮 ──
        bottom = tk.Frame(self, bg=C.bg)
        bottom.pack(fill="x", padx=14, pady=(0, 12))
        drop = tk.Frame(bottom, bg=C.input_bg, highlightbackground=C.glow,
                        highlightthickness=1, cursor="hand2")
        drop.pack(side="left", fill="x", expand=True)
        self._drop_lbl = tk.Label(drop, text="  📥 拖拽 .pt 文件到此处导入 (可多选)  ",
                                  font=_FONT_SM, bg=C.input_bg, fg=C.text_secondary)
        self._drop_lbl.pack(fill="x", padx=6, pady=6)
        self._btn_del = self._tool_btn(bottom, "   🗑 删除选中模型  ", self._delete_selected, C.danger)
        self._btn_del.pack(side="left", padx=(8, 0))

        # 状态栏
        self._status = tk.Label(self, text="就绪", font=_FONT_SM, bg=C.bg, fg=C.text_muted, anchor="w")
        self._status.pack(fill="x", padx=14, pady=(0, 10))

        # ── 拖拽注册 (窗口/列表/拖放区) ──
        try:
            from tkinterdnd2 import DND_FILES
            for w in (self, wrap, self._tree, drop, self._drop_lbl):
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            self._drop_lbl.config(text="拖拽不可用 (缺少 tkinterdnd2), 请用“浏览导入”")

        self.refresh()

    # ── 工具 ──
    def _tool_btn(self, parent, text, cmd, bg, fg="white"):
        from gui.components import _lighten, _darken
        lbl = tk.Label(parent, text=text, font=_FONT_SM, bg=bg, fg=fg,
                       padx=10, pady=4, cursor="hand2")
        lbl.bind("<Enter>", lambda e: lbl.config(bg=_lighten(bg, 15)))
        lbl.bind("<Leave>", lambda e: lbl.config(bg=bg))
        lbl.bind("<ButtonPress-1>", lambda e: lbl.config(bg=_darken(bg, 12)))
        lbl.bind("<ButtonRelease-1>", lambda e: (lbl.config(bg=_lighten(bg, 15)), cmd()))
        return lbl

    def _set_status(self, text):
        try:
            self._status.config(text=text)
        except Exception:
            pass

    # ── 数据 ──
    def _models(self):
        out = _cfg.output_dir
        if not out.exists():
            return []
        pts = sorted(out.glob("*.pt"), key=lambda p: p.stat().st_mtime, reverse=True)
        return [{"name": p.name, "size": p.stat().st_size, "time": p.stat().st_mtime,
                 "path": p} for p in pts]

    def refresh(self):
        try:
            for row in self._tree.get_children():
                self._tree.delete(row)
            models = self._models()
            for m in models:
                self._tree.insert("", "end", values=(
                    m["name"], _fmt_size(m["size"]),
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(m["time"]))))
            self._count_lbl.config(text=f"共 {len(models)} 个模型")
            self._set_status(f"就绪 · {len(models)} 个模型")
        except Exception as e:
            self._set_status(f"刷新失败: {e}")

    # ── 拖拽导入 ──
    def _on_drop(self, event):
        try:
            from utils.file_utils import parse_dnd_paths
            paths = parse_dnd_paths(event.data)
        except Exception:
            paths = [event.data]
        self._import_paths(paths)

    def _browse_import(self):
        files = filedialog.askopenfilenames(
            parent=self, title="选择 .pt 模型文件",
            filetypes=[("PyTorch 模型", "*.pt"), ("所有文件", "*.*")])
        if files:
            self._import_paths(list(files))

    def _import_paths(self, paths):
        out = _cfg.output_dir
        try:
            out.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        added, skipped = [], []
        for fp in paths:
            fp = Path(fp)
            if fp.suffix.lower() != ".pt":
                skipped.append(f"{fp.name} (非 .pt)")
                continue
            try:
                dst = out / fp.name
                if dst.resolve() == fp.resolve():
                    added.append(dst.name)
                    continue
                shutil.copy2(str(fp), str(dst))
                # 导入即登记 .md5, 启动校验不弹窗
                try:
                    import hashlib
                    md5_path = dst.with_suffix(dst.suffix + ".md5")
                    if not md5_path.exists():
                        h = hashlib.md5()
                        with open(dst, "rb") as f:
                            while chunk := f.read(8192):
                                h.update(chunk)
                        md5_path.write_text(h.hexdigest(), encoding="utf-8")
                except Exception:
                    pass
                added.append(dst.name)
            except Exception as ex:
                skipped.append(f"{fp.name} ({ex})")
        msg = ""
        if added:
            msg += f"已导入 {len(added)} 个: {', '.join(added[:5])}"
            audit("pt_import", f"导入模型: {', '.join(added)}", module="pt_manager")
        if skipped:
            msg += ("；" if msg else "") + f"跳过 {len(skipped)} 个"
        self._set_status(msg or "未导入任何文件")
        self.refresh()
        if self.on_changed:
            try:
                self.on_changed()
            except Exception:
                pass

    # ── 删除 ──
    def _delete_selected(self):
        sel = self._tree.selection()
        if not sel:
            self._set_status("请先选中要删除的模型")
            return
        names = [self._tree.item(s, "values")[0] for s in sel]
        if not messagebox.askyesno("删除模型",
                                   "确定删除以下模型吗？\n" + "\n".join(names) + "\n\n该操作不可恢复。",
                                   parent=self):
            return
        out = _cfg.output_dir
        ok, fail = [], []
        for name in names:
            p = out / name
            try:
                if p.exists():
                    p.unlink()
                for suf in (".md5",):
                    m = p.with_suffix(p.suffix + suf)
                    if m.exists():
                        m.unlink()
                ok.append(name)
            except Exception as ex:
                fail.append(f"{name} ({ex})")
        if ok:
            audit("pt_delete", f"删除模型: {', '.join(ok)}", module="pt_manager")
            self._set_status(f"已删除 {len(ok)} 个模型")
        if fail:
            self._set_status(f"删除失败: {', '.join(fail)}")
        self.refresh()
        if self.on_changed:
            try:
                self.on_changed()
            except Exception:
                pass

    # ── 打开文件夹 ──
    def _open_dir(self):
        try:
            out = _cfg.output_dir
            out.mkdir(parents=True, exist_ok=True)
            os.startfile(str(out))
            audit("pt_open_dir", f"打开模型目录: {out}", module="pt_manager")
        except Exception as e:
            self._set_status(f"打开失败: {e}")
