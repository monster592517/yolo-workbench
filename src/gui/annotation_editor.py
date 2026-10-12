# -*- coding: utf-8 -*-
"""annotation_editor.py — 内置标注编辑器（工具箱式，单帧）。

## 这一版为什么整体重写

上一版是「**补标**工具」：进来就是一张已经标好的图，只能加/删关键点、续标多边形，
工具栏是两排用 `tk.Label` 手搓的按钮，没有对象概念、没有撤销、没有工具选择。

现在改成**通用标注编辑器**：左侧竖排工具箱（8 种）、中央画布、右侧对象列表 + 属性面板、
底部状态栏。用户自己挑工具画，画错了能删、能撤。

## 工具集（内置，用户自选）

| 键 | 工具 | 产出 shape_type |
|---|---|---|
| V | 选择 | 选中 / 拖动 / 把手缩放（不改类型） |
| R | 矩形 | `rectangle`（HBB） |
| O | 旋转框 | `rotation`（OBB，4 点） |
| P | 多边形 | `polygon`（seg） |
| K | 关键点 | `point`（14 个部位之一） |
| . | 点 | `point`（自由标签） |
| L | 线段 | `line`（2 点） |
| C | 圆 | `circle`（圆心 + 圆周点） |

## 数据模型

统一成 `shapes` 列表（labelme 风格），**坐标一律归一化 [0,1]**：

    {"type": "rectangle", "label": "摔倒", "points": [[x, y], ...], "group_id": None}

保存时用 `core.yolo_convert.write_yolo_labels()` 写回 YOLO txt。

## 向后兼容

构造签名与上一版一致（`bbox` / `poly` / `kpts` / `cls` / `discard`），内部转成 shapes；
`on_save` 回传的字典仍含 `bbox` / `poly` / `kpts` / `cls` / `discard`，**另外多给一个 `shapes`**。
所以老的调用方不用改。
"""
from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path

from PIL import ImageTk

from gui import components as _w
from gui.image_cache import open_scaled

from infrastructure.config import get_theme

C = get_theme().colors

# ── 关键点部位（14 点，中文显示名）──
_NM = {"forehead": "额头", "neck": "脖子",
       "LShoulder": "左肩", "RShoulder": "右肩",
       "LElbow": "左肘", "RElbow": "右肘",
       "LWrist": "左腕", "RWrist": "右腕",
       "LHip": "左髋", "RHip": "右髋",
       "LKnee": "左膝", "RKnee": "右膝",
       "LAnkle": "左踝", "RAnkle": "右踝"}
_NM_REV = {v: k for k, v in _NM.items()}

# ── 类别 ──
# 默认名与 data.yaml 的 names 对齐（**写进 txt 的就是这个名字**）。
# 真实类名由调用方通过  传进来（例如 data.yaml 里是 [person, fall]）；
# 这里的默认值只在没人传时兜底。
_DEFAULT_CLASSES = ["person", "fall", "squat", "other"]

# ── 工具箱定义：(键, 图标, 名称, 说明) ──
TOOLS = [
    ("select", "V", "↖", "选择",  "选中 / 拖动 / 缩放已有对象"),
    ("rect",   "R", "▭", "矩形",  "拖出矩形框（HBB）"),
    ("obb",    "O", "◺", "旋转框", "拖出旋转框（OBB，4 点）"),
    ("poly",   "P", "⬠", "多边形", "逐点连成多边形（seg），双击或回车收尾"),
    ("kpt",    "K", "•", "关键点", "放置 14 个部位之一（先在右侧选部位）"),
    ("point",  ".", "·", "点",    "放一个自由点"),
    ("line",   "L", "╱", "线段",  "两点连成一条线"),
    ("circle", "C", "◯", "圆",    "按住拖出圆（圆心 + 半径）"),
]
_TOOL_BY_KEY = {k.lower(): t for t, k, _i, _n, _d in TOOLS}
_TOOL_NAME = {t: n for t, _k, _i, n, _d in TOOLS}

_IMG_MAX_W, _IMG_MAX_H = 1180, 640
_HIT = 12          # 命中半径（画布 px）
_HANDLE = 9        # 框把手命中半径
_PT_R = 5          # 关键点绘制半径
_UNDO_MAX = 60     # 撤销栈深度


def _gid(shapes):
    """下一个 group_id（关键点与它的主体框归到同一组，供 pose 合成）。"""
    ids = [s.get("group_id") for s in shapes if isinstance(s.get("group_id"), int)]
    return (max(ids) + 1) if ids else 0


class AnnotationEditor(tk.Toplevel):
    """单帧标注编辑器（置顶；保存/取消后关闭）。"""

    # ─────────────────────────── 构造 ───────────────────────────
    def __init__(self, master, path, bbox=None, poly=None, kpts=None,
                 cls=1, discard=False, on_save=None, on_discard=None,
                 classes=None):
        super().__init__(master)
        self.title(f"标注 - {Path(str(path)).name}")
        self.configure(bg=C.bg2)
        self.attributes("-topmost", True)

        self._on_save = on_save
        self._on_discard = on_discard
        self._path = path
        self._cls = int(cls if cls is not None else 1)
        self._discard = bool(discard)
        # 真实类名（写进 YOLO txt 的就是它）。调用方没给就用默认兜底。
        self._classes = [str(x) for x in (classes or _DEFAULT_CLASSES)]

        # ── 图像（JPEG draft 解码，尽量少占内存）──
        im = open_scaled(path, _IMG_MAX_W, _IMG_MAX_H)
        self._iw, self._ih = im.size          # 归一化坐标对应的画布尺寸
        self._photo = ImageTk.PhotoImage(im, master=self)

        # ── 数据 ──
        self._shapes = self._legacy_to_shapes(bbox, poly, kpts)
        self._sel = None                       # 选中对象下标
        self._tool = "select"
        self._undo = []                        # 撤销栈（存 shapes 深拷贝）
        self._redo = []
        self._drag = None                      # 拖动状态
        self._undo_armed = False               # 已按下但还没真正改数据（用于「选中不进撤销栈」）
        self._pending = []                     # 多边形/线的逐点缓冲（归一化）
        self._pending_obb = None
        self._cursor = None                    # 画布光标（归一化），用于预览
        self._redraw_job = None

        self._kpt_name = tk.StringVar(value=_NM["forehead"])
        self._cls_var = tk.IntVar(value=self._cls)

        self._build_ui()
        self._bind_keys()
        self._redraw()

    # ─────────────────────── legacy 互转 ───────────────────────
    def _legacy_to_shapes(self, bbox, poly, kpts):
        """老接口（bbox/poly/kpts，归一化）-> shapes 列表。"""
        out = []
        if bbox and len(bbox) == 4:
            cx, cy, bw, bh = [float(v) for v in bbox]
            out.append({"type": "rectangle", "label": self._cls_label(self._cls),
                        "points": [[cx - bw / 2, cy - bh / 2], [cx + bw / 2, cy + bh / 2]],
                        "group_id": 0})
        if poly and len(poly) >= 6:
            pts = [[float(poly[i]), float(poly[i + 1])] for i in range(0, len(poly) - 1, 2)]
            out.append({"type": "polygon", "label": self._cls_label(self._cls),
                        "points": pts, "group_id": 0})
        for k in (kpts or []):
            if not isinstance(k, dict):
                continue
            out.append({"type": "point", "label": str(k.get("name") or "point"),
                        "points": [[float(k.get("x", 0.0)), float(k.get("y", 0.0))]],
                        "vis": int(k.get("vis", 2)), "group_id": 0})
        return out

    def _shapes_to_legacy(self):
        """shapes -> (bbox, poly, kpts)，供老调用方消费。"""
        bbox = poly = None
        kpts = []
        for s in self._shapes:
            t = s["type"]
            if t == "rectangle" and bbox is None and len(s["points"]) >= 2:
                (x1, y1), (x2, y2) = s["points"][0], s["points"][1]
                bbox = [round((x1 + x2) / 2, 6), round((y1 + y2) / 2, 6),
                        round(abs(x2 - x1), 6), round(abs(y2 - y1), 6)]
            elif t == "polygon" and poly is None and len(s["points"]) >= 3:
                poly = [round(v, 6) for p in s["points"] for v in (p[0], p[1])]
            elif t == "point" and str(s.get("label")) in _NM:
                kpts.append({"name": s["label"],
                             "x": round(float(s["points"][0][0]), 6),
                             "y": round(float(s["points"][0][1]), 6),
                             "vis": int(s.get("vis", 2))})
        return bbox, poly, kpts

    def _cls_label(self, cid):
        """类别号 -> **写进 YOLO txt 的类名**（不是给人看的中文）。"""
        i = int(cid)
        if 0 <= i < len(self._classes):
            return self._classes[i]
        return self._classes[0] if self._classes else "object"

    # ───────────────────────── UI 构建 ─────────────────────────
    def _build_ui(self):
        self._build_topbar()
        body = tk.Frame(self, bg=C.bg2)
        body.pack(fill="both", expand=True)
        self._build_toolbar(body)
        self._build_canvas(body)
        self._build_sidebar(body)
        self._build_statusbar()

    def _build_topbar(self):
        bar = tk.Frame(self, bg=C.card_bg, height=46)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        tk.Label(bar, text="🖊", bg=C.card_bg, fg=C.primary,
                 font=("Microsoft YaHei UI", 13, "bold")).pack(side="left", padx=(12, 6))
        tk.Label(bar, text=Path(str(self._path)).name, bg=C.card_bg, fg=C.text,
                 font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        tk.Label(bar, text="  %d×%d" % (self._iw, self._ih), bg=C.card_bg, fg=C.text_muted,
                 font=("Microsoft YaHei UI", 8)).pack(side="left")

        # 右侧：取消 / 保存
        _w.btn(bar, "  取消  ", self.destroy, "ghost").pack(side="right", padx=(0, 10))
        _w.btn(bar, "  ✅ 保存  ", self._save, "primary").pack(side="right", padx=(0, 6))

        # 中间：AI 辅助
        _w.btn(bar, " 🔄 本地重标 ", lambda: self._ai_locate("relabel"), "ghost").pack(side="left", padx=(14, 6))
        _w.btn(bar, " 🧹 收框 ", lambda: self._ai_locate("snap"), "ghost").pack(side="left")
        self._ai_status = tk.Label(bar, text="", bg=C.card_bg, fg=C.text_muted,
                                   font=("Microsoft YaHei UI", 8))
        self._ai_status.pack(side="left", padx=(8, 0))
        self._ai_busy = False

        # 放弃/恢复
        self._disc_btn = _w.btn(bar, " 🗑 放弃本帧 " if not self._discard else " ↩ 恢复本帧 ",
                                self._toggle_discard, "ghost")
        self._disc_btn.pack(side="left", padx=(14, 0))

    def _build_toolbar(self, parent):
        tb = tk.Frame(parent, bg=C.card_bg, width=62)
        tb.pack(side="left", fill="y")
        tb.pack_propagate(False)
        self._tool_btns = {}
        for tool, key, icon, name, desc in TOOLS:
            b = tk.Label(tb, text="%s\n%s" % (icon, key), bg=C.card_bg, fg=C.text_secondary,
                         font=("Microsoft YaHei UI", 11, "bold"), cursor="hand2",
                         width=4, height=2, justify="center")
            b.pack(pady=(8 if tool == "select" else 3, 0))
            b.bind("<Button-1>", lambda e, t=tool: self._set_tool(t))
            b.bind("<Enter>", lambda e, d=desc: self._status_tip(d))
            b.bind("<Leave>", lambda e: self._status_tip(""))
            self._tool_btns[tool] = (b, name, desc)

    def _build_canvas(self, parent):
        mid = tk.Frame(parent, bg=C.bg2)
        mid.pack(side="left", fill="both", expand=True)
        self._cv = tk.Canvas(mid, width=self._iw + 4, height=self._ih + 4,
                             bg=C.log_bg, highlightthickness=1,
                             highlightbackground=C.border)
        self._cv.pack(padx=8, pady=8)
        self._cv.create_image(2 + self._iw // 2, 2 + self._ih // 2, image=self._photo)
        self._cv.bind("<Button-1>", self._press)
        self._cv.bind("<B1-Motion>", self._motion)
        self._cv.bind("<ButtonRelease-1>", self._release)
        self._cv.bind("<Button-3>", self._right)
        self._cv.bind("<Motion>", self._hover)
        self._cv.bind("<Double-Button-1>", self._double)

    def _build_sidebar(self, parent):
        sb = tk.Frame(parent, bg=C.card_bg, width=252)
        sb.pack(side="right", fill="y")
        sb.pack_propagate(False)

        # 类别
        head = tk.Label(sb, text="类别", bg=C.card_bg, fg=C.text_secondary,
                        font=("Microsoft YaHei UI", 9, "bold"), anchor="w")
        head.pack(fill="x", padx=12, pady=(12, 4))
        for cid, txt in enumerate(self._classes):
            tk.Radiobutton(sb, text="%d %s" % (cid, txt), variable=self._cls_var, value=cid,
                           bg=C.card_bg, activebackground=C.card_bg, selectcolor=C.input_bg,
                           fg=C.text, activeforeground=C.text, anchor="w",
                           font=("Microsoft YaHei UI", 9),
                           command=self._on_cls_change).pack(fill="x", padx=14)

        # 关键点部位（仅 K 工具用）
        self._kpt_lbl = tk.Label(sb, text="关键点部位", bg=C.card_bg, fg=C.text_secondary,
                                 font=("Microsoft YaHei UI", 9, "bold"), anchor="w")
        self._kpt_lbl.pack(fill="x", padx=12, pady=(12, 4))
        self._kpt_combo = tk.OptionMenu(sb, self._kpt_name, *_NM.values())
        self._kpt_combo.config(bg=C.input_bg, fg=C.text, activebackground=C.border,
                               highlightthickness=0, bd=0, anchor="w",
                               font=("Microsoft YaHei UI", 9))
        self._kpt_combo["menu"].config(bg=C.input_bg, fg=C.text,
                                       font=("Microsoft YaHei UI", 9))
        self._kpt_combo.pack(fill="x", padx=14)

        # 对象列表
        tk.Frame(sb, height=1, bg=C.border).pack(fill="x", padx=12, pady=(14, 0))
        tk.Label(sb, text="对象", bg=C.card_bg, fg=C.text_secondary,
                 font=("Microsoft YaHei UI", 9, "bold"), anchor="w").pack(
            fill="x", padx=12, pady=(10, 4))
        wrap = tk.Frame(sb, bg=C.card_bg)
        wrap.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self._list = tk.Listbox(wrap, bg=C.input_bg, fg=C.text, bd=0, highlightthickness=0,
                                selectbackground=C.primary, selectforeground="#ffffff",
                                font=("Microsoft YaHei UI", 9), activestyle="none")
        sbar = _w.themed_scrollbar(wrap, command=self._list.yview)
        self._list.config(yscrollcommand=sbar.set)
        sbar.pack(side="right", fill="y")
        self._list.pack(side="left", fill="both", expand=True)
        self._list.bind("<<ListboxSelect>>", self._on_list_select)

        # 底部按钮
        row = tk.Frame(sb, bg=C.card_bg)
        row.pack(fill="x", padx=12, pady=(0, 12))
        _w.btn(row, " 🗑 删除 ", self._del_selected, "ghost").pack(side="left")
        _w.btn(row, " ⧉ 复制 ", self._dup_selected, "ghost").pack(side="left", padx=(6, 0))
        _w.btn(row, " ✖ 清空 ", self._clear_all, "ghost").pack(side="right")

    def _build_statusbar(self):
        bar = tk.Frame(self, bg=C.card_bg, height=26)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)
        self._status_xy = tk.Label(bar, text="—", bg=C.card_bg, fg=C.text_muted,
                                   font=("Microsoft YaHei UI", 8, "bold"))
        self._status_xy.pack(side="left", padx=(12, 10))
        self._status_tip_lbl = tk.Label(bar, text="", bg=C.card_bg, fg=C.text_secondary,
                                        font=("Microsoft YaHei UI", 8), anchor="w")
        self._status_tip_lbl.pack(side="left", fill="x", expand=True)
        self._status_count = tk.Label(bar, text="", bg=C.card_bg, fg=C.text_muted,
                                      font=("Microsoft YaHei UI", 8))
        self._status_count.pack(side="right", padx=12)

    def _bind_keys(self):
        for tool, key, _i, _n, _d in TOOLS:
            try:
                self.bind("<KeyPress-%s>" % key.lower(), lambda e, t=tool: self._set_tool(t))
                self.bind("<KeyPress-%s>" % key.upper(), lambda e, t=tool: self._set_tool(t))
            except Exception:
                pass
        for seq, fn in (("<Escape>", self.destroy),
                        ("<Return>", self._on_enter),
                        ("<Delete>", self._del_selected),
                        ("<Control-s>", self._save),
                        ("<Control-z>", self._undo_step),
                        ("<Control-y>", self._redo_step),
                        ("<Control-d>", self._dup_selected)):
            try:
                self.bind(seq, lambda e, f=fn: f())
            except Exception:
                pass

    # ────────────────────────── 工具 ──────────────────────────
    def _set_tool(self, tool):
        self._tool = tool
        self._pending = []
        self._pending_obb = None
        for t, (b, _n, _d) in self._tool_btns.items():
            on = (t == tool)
            try:
                b.config(bg=C.primary if on else C.card_bg,
                         fg="#ffffff" if on else C.text_secondary)
            except Exception:
                pass
        tips = {"select": "选择：点对象选中；拖内部=移动；拖把手=缩放",
                "rect": "矩形：按住左键拖出一个框",
                "obb": "旋转框：拖出四个角（按先后顺序）",
                "poly": "多边形：逐点点击，双击或回车收尾；Esc 取消",
                "kpt": "关键点：先在右侧选部位，再点图放置",
                "point": "点：点图放置一个自由点",
                "line": "线段：点两下成一条线",
                "circle": "圆：按住拖出圆心与半径"}
        self._status_tip(tips.get(tool, ""))
        try:
            self._cv.config(cursor="crosshair" if tool != "select" else "")
        except Exception:
            pass
        self._redraw()

    def _status_tip(self, text):
        try:
            self._status_tip_lbl.config(text=text)
        except Exception:
            pass

    # ───────────────────── 坐标 / 命中 ─────────────────────
    def _to_img(self, x, y):
        return (min(1.0, max(0.0, (x - 2) / self._iw)),
                min(1.0, max(0.0, (y - 2) / self._ih)))

    def _to_cv(self, nx, ny):
        return 2 + nx * self._iw, 2 + ny * self._ih

    def _hit_shape(self, x, y):
        """从**后往前**找（后画的在上层）。"""
        for i in range(len(self._shapes) - 1, -1, -1):
            if self._hit_one(self._shapes[i], x, y):
                return i
        return None

    def _hit_one(self, s, x, y):
        pts = s["points"]
        px = [self._to_cv(float(p[0]), float(p[1])) for p in pts]
        if s["type"] in ("point",):
            return px and abs(px[0][0] - x) <= _HIT and abs(px[0][1] - y) <= _HIT
        if s["type"] in ("rectangle", "line") and len(px) >= 2:
            x1, y1 = px[0]
            x2, y2 = px[-1]
            if s["type"] == "rectangle":
                lo_x, hi_x = min(x1, x2), max(x1, x2)
                lo_y, hi_y = min(y1, y2), max(y1, y2)
                return lo_x - _HIT <= x <= hi_x + _HIT and lo_y - _HIT <= y <= hi_y + _HIT
            return _pt_seg_dist(x, y, x1, y1, x2, y2) <= _HIT
        if s["type"] == "circle" and len(px) >= 2:
            import math
            cx0, cy0 = px[0]
            r = math.hypot(px[1][0] - cx0, px[1][1] - cy0)
            return abs(math.hypot(x - cx0, y - cy0) - r) <= _HIT + 3
        if len(px) >= 3:
            return _pt_in_poly(x, y, px)
        return False

    def _handle_pts(self, s):
        """把手的**画布坐标**列表。

        ⚠ 画的时候和判命中的时候必须用**同一份**把手表，否则会出现
        「看得到把手、拖不动」这种最让人恼火的错位（绘制画 4 角、命中只认 2 点就是这类 bug）。
        """
        px = [self._to_cv(float(p[0]), float(p[1])) for p in s["points"]]
        if s["type"] == "rectangle" and len(px) >= 2:
            (x1, y1), (x2, y2) = px[0], px[1]
            # 顺序固定：tl / tr / bl / br
            return [(x1, y1), (x2, y1), (x1, y2), (x2, y2)]
        return px

    def _hit_handle(self, s, x, y):
        if s["type"] not in ("rectangle", "rotation", "line", "circle", "polygon"):
            return None
        for i, (hx, hy) in enumerate(self._handle_pts(s)):
            if abs(hx - x) <= _HANDLE and abs(hy - y) <= _HANDLE:
                return i
        return None

    def _move_handle(self, s, hi, nx, ny):
        """把第 hi 个把手拖到归一化坐标 (nx, ny)。矩形按「对角两点」记账。"""
        pts = s["points"]
        if s["type"] == "rectangle" and len(pts) >= 2:
            (x1, y1), (x2, y2) = pts[0], pts[1]
            if hi == 0:
                pts[0] = [nx, ny]
            elif hi == 1:
                pts[0] = [x1, y1]
                pts[1] = [x2, y2]
                pts[0][0], pts[1][1] = nx, ny
            elif hi == 2:
                pts[0] = [x1, y1]
                pts[1] = [x2, y2]
                pts[0][1], pts[1][0] = ny, nx
            else:
                pts[1] = [nx, ny]
        elif 0 <= hi < len(pts):
            pts[hi] = [nx, ny]

    # ────────────────────────── 绘制 ──────────────────────────
    def _request_redraw(self):
        if self._redraw_job is None:
            self._redraw_job = self.after(20, self._do_redraw)

    def _do_redraw(self):
        self._redraw_job = None
        self._redraw()

    def _redraw(self):
        cv = self._cv
        try:
            cv.delete("ann")
        except Exception:
            return
        for i, s in enumerate(self._shapes):
            self._draw_shape(s, i == self._sel, tag="ann")
        self._draw_pending(tag="ann")
        self._refresh_list()
        n = len(self._shapes)
        kinds = {}
        for s in self._shapes:
            kinds[s["type"]] = kinds.get(s["type"], 0) + 1
        self._status_count.config(
            text="共 %d 个对象  " % n + "  ".join("%s×%d" % (_TYPE_CN.get(k, k), v)
                                                 for k, v in kinds.items()))

    def _draw_shape(self, s, selected, tag="ann"):
        cv = self._cv
        t = s["type"]
        px = [self._to_cv(float(p[0]), float(p[1])) for p in s["points"]]
        if not px:
            return
        main = C.primary if selected else _shape_color(t)
        wdt = 2 if selected else 1
        if t == "point":
            r = _PT_R + (2 if selected else 0)
            cv.create_oval(px[0][0] - r, px[0][1] - r, px[0][0] + r, px[0][1] + r,
                           fill=main, outline="#000000", width=1, tags=tag)
            lbl = _NM.get(str(s.get("label")), str(s.get("label")))
            cv.create_text(px[0][0] + 8, px[0][1] - 8, text=lbl, fill=main, anchor="w",
                           font=("Microsoft YaHei UI", 8, "bold"), tags=tag)
            return
        if t == "rectangle":
            x1, y1 = px[0]
            x2, y2 = px[-1]
            cv.create_rectangle(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2),
                                outline=main, width=wdt, tags=tag)
            if selected:
                for hx, hy in self._handle_pts(s):
                    cv.create_rectangle(hx - 3, hy - 3, hx + 3, hy + 3,
                                        fill=main, outline="#000000", tags=tag)
        elif t in ("polygon", "rotation"):
            flat = [c for p in px for c in p]
            cv.create_polygon(*flat, outline=main, fill="", width=wdt, tags=tag)
            if selected:
                for hx, hy in px:
                    cv.create_oval(hx - 3, hy - 3, hx + 3, hy + 3, fill=main,
                                   outline="#000000", tags=tag)
        elif t == "line":
            cv.create_line(px[0][0], px[0][1], px[-1][0], px[-1][1],
                           fill=main, width=wdt + 1, tags=tag)
            if selected:
                for hx, hy in (px[0], px[-1]):
                    cv.create_oval(hx - 3, hy - 3, hx + 3, hy + 3, fill=main,
                                   outline="#000000", tags=tag)
        elif t == "circle":
            import math
            cx0, cy0 = px[0]
            r = math.hypot(px[-1][0] - cx0, px[-1][1] - cy0)
            cv.create_oval(cx0 - r, cy0 - r, cx0 + r, cy0 + r, outline=main,
                           width=wdt, tags=tag)
        # 标签
        if t != "point":
            ax = sum(p[0] for p in px) / len(px)
            ay = min(p[1] for p in px)
            cv.create_text(ax, ay - 8, text=str(s.get("label")), fill=main, anchor="s",
                           font=("Microsoft YaHei UI", 8, "bold"), tags=tag)

    def _draw_pending(self, tag="ann"):
        """正在画的多边形/线的预览。"""
        pend = list(self._pending)
        if self._cursor and pend:
            pend = pend + [self._cursor]
        if len(pend) < 1:
            return
        px = [self._to_cv(float(p[0]), float(p[1])) for p in pend]
        if len(px) >= 2:
            self._cv.create_line(*[c for p in px for c in p], fill=C.warning,
                                 width=2, dash=(4, 3), tags=tag)
        for hx, hy in px:
            self._cv.create_oval(hx - 3, hy - 3, hx + 3, hy + 3, fill=C.warning,
                                 outline="#000000", tags=tag)

    # ────────────────────────── 鼠标 ──────────────────────────
    def _press(self, e):
        x, y = e.x, e.y
        t = self._tool
        if t == "select":
            # ⚠ **点一下选中不算一次编辑** —— 撤销栈只记真正改了数据的操作。
            #   否则连点几下就把历史刷满，Ctrl+Z 变成「取消我的选中」，很难用。
            self._undo_armed = True
            idx = self._hit_shape(x, y)
            self._sel = idx
            if idx is not None:
                h = self._hit_handle(self._shapes[idx], x, y)
                self._drag = (("handle", idx, h) if h is not None
                              else ("move", idx, self._to_img(x, y)))
            else:
                self._drag = None
            self._redraw()
            return

        if t in ("poly", "line"):
            self._pending.append(self._to_img(x, y))
            if t == "line" and len(self._pending) >= 2:
                self._commit_pending("line")
            self._redraw()
            return

        if t in ("rect", "circle", "obb"):
            self._drag = ("create", t, (self._to_img(x, y), self._to_img(x, y)))
            return

        # 一次性放置
        nx, ny = self._to_img(x, y)
        if t == "kpt":
            name = _NM_REV.get(self._kpt_name.get(), self._kpt_name.get())
            self._push_undo()
            self._shapes.append({"type": "point", "label": name, "points": [[nx, ny]],
                                 "vis": 2, "group_id": 0})
            self._sel = len(self._shapes) - 1
        elif t == "point":
            self._push_undo()
            self._shapes.append({"type": "point", "label": self._cls_label(self._cls),
                                 "points": [[nx, ny]], "group_id": None})
            self._sel = len(self._shapes) - 1
        self._redraw()

    def _motion(self, e):
        nx, ny = self._to_img(e.x, e.y)
        if self._drag:
            kind = self._drag[0]
            if kind == "create":
                self._drag = ("create", self._drag[1], (self._drag[2][0], (nx, ny)))
                self._draw_preview()
            elif kind == "move":
                idx, start = self._drag[1], self._drag[2]
                s = self._shapes[idx]
                # 整体平移：偏移量按**整体边界** clamp，
                # 不能逐点 clamp —— 那会把出界的形状压扁（形状被改坏且不易察觉）
                dx, dy = nx - start[0], ny - start[1]
                xs = [p[0] for p in s["points"]]
                ys = [p[1] for p in s["points"]]
                dx = max(-min(xs), min(dx, 1.0 - max(xs)))
                dy = max(-min(ys), min(dy, 1.0 - max(ys)))
                if dx or dy:
                    if self._undo_armed:
                        self._push_undo()
                        self._undo_armed = False
                    s["points"] = [[p[0] + dx, p[1] + dy] for p in s["points"]]
                    self._drag = ("move", idx, (start[0] + dx, start[1] + dy))
                    self._request_redraw()
            elif kind == "handle":
                idx, hi = self._drag[1], self._drag[2]
                if self._undo_armed:
                    self._push_undo()
                    self._undo_armed = False
                self._move_handle(self._shapes[idx], hi, nx, ny)
                self._request_redraw()
        else:
            self._cursor = (nx, ny)
            if self._pending:
                self._redraw()
        self._status_xy.config(text="x %.3f  y %.3f" % (nx, ny))

    def _draw_preview(self):
        if not self._drag or self._drag[0] != "create":
            return
        t, (p0, p1) = self._drag[1], self._drag[2]
        a = self._to_cv(*p0)
        b = self._to_cv(*p1)
        try:
            self._cv.delete("preview")
        except Exception:
            return
        if t == "rect":
            self._cv.create_rectangle(a[0], a[1], b[0], b[1], outline=C.primary, width=2,
                                      dash=(5, 3), tags="preview")
        elif t == "circle":
            import math
            r = math.hypot(b[0] - a[0], b[1] - a[1])
            self._cv.create_oval(a[0] - r, a[1] - r, a[0] + r, a[1] + r,
                                 outline=C.primary, width=2, dash=(5, 3), tags="preview")
        elif t == "obb":
            self._cv.create_line(a[0], a[1], b[0], b[1], fill=C.primary, width=2,
                                 dash=(5, 3), tags="preview")

    def _release(self, e):
        self._undo_armed = False
        if not self._drag or self._drag[0] != "create":
            self._drag = None
            return
        t, (p0, p1) = self._drag[1], self._drag[2]
        self._drag = None
        try:
            self._cv.delete("preview")
        except Exception:
            pass
        dx = abs(p1[0] - p0[0])
        dy = abs(p1[1] - p0[1])
        if dx < 0.005 and dy < 0.005:
            self._redraw()
            return
        self._push_undo()
        if t == "rect":
            self._shapes.append({"type": "rectangle", "label": self._cls_label(self._cls),
                                 "points": [[p0[0], p0[1]], [p1[0], p1[1]]], "group_id": None})
        elif t == "circle":
            self._shapes.append({"type": "circle", "label": self._cls_label(self._cls),
                                 "points": [[p0[0], p0[1]], [p1[0], p1[1]]], "group_id": None})
        elif t == "obb":
            self._shapes.append({"type": "rotation", "label": self._cls_label(self._cls),
                                 "points": [[p0[0], p0[1]], [p1[0], p0[1]],
                                            [p1[0], p1[1]], [p0[0], p1[1]]], "group_id": None})
        self._sel = len(self._shapes) - 1
        self._redraw()

    def _double(self, e):
        if self._tool == "poly" and len(self._pending) >= 3:
            self._commit_pending("polygon")

    def _on_enter(self):
        if self._tool == "poly" and len(self._pending) >= 3:
            self._commit_pending("polygon")
        else:
            self._save()

    def _commit_pending(self, shape_type):
        if len(self._pending) < (2 if shape_type == "line" else 3):
            self._pending = []
            self._redraw()
            return
        self._push_undo()
        self._shapes.append({"type": shape_type, "label": self._cls_label(self._cls),
                             "points": list(self._pending), "group_id": None})
        self._pending = []
        self._sel = len(self._shapes) - 1
        self._redraw()

    def _right(self, e):
        """右键：多边形的续标点回退 / 删除命中的对象。"""
        if self._pending:
            self._pending.pop()
            self._redraw()
            return
        idx = self._hit_shape(e.x, e.y)
        if idx is not None:
            self._push_undo()
            self._shapes.pop(idx)
            self._sel = None
            self._redraw()

    def _hover(self, e):
        idx = self._hit_shape(e.x, e.y)
        try:
            self._cv.config(cursor=("hand2" if idx is not None else
                                    ("crosshair" if self._tool != "select" else "")))
        except Exception:
            pass

    # ───────────────────── 对象列表 / 属性 ─────────────────────
    def _refresh_list(self):
        try:
            self._list.delete(0, "end")
            for i, s in enumerate(self._shapes):
                lbl = str(s.get("label"))
                if s["type"] == "point":
                    lbl = _NM.get(lbl, lbl)
                self._list.insert("end", "%2d  %s  %s" % (i + 1, _TYPE_CN.get(s["type"], s["type"]), lbl))
            if self._sel is not None and 0 <= self._sel < len(self._shapes):
                self._list.selection_clear(0, "end")
                self._list.selection_set(self._sel)
                self._list.see(self._sel)
        except Exception:
            pass

    def _on_list_select(self, _e=None):
        try:
            sel = self._list.curselection()
            if sel:
                self._sel = int(sel[0])
                self._redraw()
        except Exception:
            pass

    def _del_selected(self):
        if self._sel is None or not (0 <= self._sel < len(self._shapes)):
            return
        self._push_undo()
        self._shapes.pop(self._sel)
        self._sel = None
        self._redraw()

    def _dup_selected(self):
        if self._sel is None or not (0 <= self._sel < len(self._shapes)):
            return
        import copy
        self._push_undo()
        s = copy.deepcopy(self._shapes[self._sel])
        s["points"] = [[min(1.0, p[0] + 0.02), min(1.0, p[1] + 0.02)] for p in s["points"]]
        self._shapes.append(s)
        self._sel = len(self._shapes) - 1
        self._redraw()

    def _clear_all(self):
        if not self._shapes:
            return
        self._push_undo()
        self._shapes = []
        self._sel = None
        self._redraw()

    def _on_cls_change(self):
        self._cls = int(self._cls_var.get())
        if self._sel is not None and 0 <= self._sel < len(self._shapes):
            s = self._shapes[self._sel]
            if s["type"] in ("rectangle", "polygon", "circle", "rotation"):
                s["label"] = self._cls_label(self._cls)
        self._redraw()

    # ───────────────────────── 撤销 ─────────────────────────
    def _push_undo(self):
        import copy
        self._undo.append(copy.deepcopy(self._shapes))
        if len(self._undo) > _UNDO_MAX:
            self._undo.pop(0)
        self._redo.clear()

    def _undo_step(self):
        if not self._undo:
            return
        import copy
        self._redo.append(copy.deepcopy(self._shapes))
        self._shapes = self._undo.pop()
        self._sel = None
        self._pending = []
        self._redraw()

    def _redo_step(self):
        if not self._redo:
            return
        import copy
        self._undo.append(copy.deepcopy(self._shapes))
        self._shapes = self._redo.pop()
        self._sel = None
        self._redraw()

    # ───────────────────── AI 辅助（保留） ─────────────────────
    def _toggle_discard(self):
        self._discard = not self._discard
        try:
            self._disc_btn.config(
                text="↩ 恢复本帧" if self._discard else "🗑 放弃本帧")
        except Exception:
            pass

    def _ai_locate(self, mode: str):
        if mode == "snap":
            self._snap_to_kpts()
            return
        if self._ai_busy:
            return
        self._ai_busy = True
        try:
            self._ai_status.config(text="🔄 本地重标中（首次约 10-30s）…", fg=C.warning)
        except Exception:
            pass
        path = self._path
        cls = int(self._cls_var.get())

        def _work():
            try:
                from core.relabel import relabel_frame
                r = relabel_frame(path, cls=cls)
            except Exception as e:
                r = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
            try:
                self.after(0, lambda: self._ai_apply(r))
            except Exception:
                pass

        threading.Thread(target=_work, daemon=True).start()

    def _ai_apply(self, r: dict):
        self._ai_busy = False
        try:
            if not r.get("ok"):
                self._ai_status.config(text="✘ %s" % str(r.get("error"))[:44], fg=C.danger)
                return
            self._push_undo()
            # 用重标结果替换「同一组的框/多边形/关键点」，自由标注保持不动
            keep = [s for s in self._shapes
                    if s["type"] in ("line", "circle")
                    or (s["type"] == "point" and str(s.get("label")) not in _NM)]
            fresh = self._legacy_to_shapes(r.get("bbox"), r.get("poly"), r.get("kpts"))
            self._shapes = fresh + keep
            self._sel = None
            self._ai_status.config(
                text="✔ 本地重标完成（%s）" % r.get("engine", "local"), fg=C.success)
        except Exception as e:
            self._ai_status.config(text="✘ %s" % str(e)[:44], fg=C.danger)
        self._redraw()

    def _snap_to_kpts(self, grow: float = 1.25, floor_margin: float = 0.045):
        """把框/多边形夹进「当前关键点外接范围 + 余量」（纯本地几何，防背景抢框）。"""
        pts = [s["points"][0] for s in self._shapes
               if s["type"] == "point" and str(s.get("label")) in _NM and int(s.get("vis", 2)) >= 1]
        if len(pts) < 6:
            self._ai_status.config(text="✘ 可视关键点不足 6 个，无法收框", fg=C.danger)
            return
        kx1 = min(p[0] for p in pts); kx2 = max(p[0] for p in pts)
        ky1 = min(p[1] for p in pts); ky2 = max(p[1] for p in pts)
        mx = max(floor_margin, (kx2 - kx1) * (grow - 1.0) / 2)
        my = max(floor_margin, (ky2 - ky1) * (grow - 1.0) / 2)
        lo_x, lo_y = max(0.0, kx1 - mx), max(0.0, ky1 - my)
        hi_x, hi_y = min(1.0, kx2 + mx), min(1.0, ky2 + my)
        self._push_undo()
        changed = 0
        for s in self._shapes:
            if s["type"] == "rectangle" and len(s["points"]) >= 2:
                (x1, y1), (x2, y2) = s["points"][0], s["points"][1]
                nx1, nx2 = max(lo_x, min(x1, x2)), min(hi_x, max(x1, x2))
                ny1, ny2 = max(lo_y, min(y1, y2)), min(hi_y, max(y1, y2))
                if nx2 - nx1 >= 0.02 and ny2 - ny1 >= 0.02:
                    s["points"] = [[nx1, ny1], [nx2, ny2]]
                    changed += 1
            elif s["type"] == "polygon":
                s["points"] = [[max(lo_x, min(p[0], hi_x)), max(lo_y, min(p[1], hi_y))]
                               for p in s["points"]]
                changed += 1
        self._ai_status.config(
            text=("✔ 已收 %d 个对象到关键点范围" % changed) if changed else "✘ 无框可收",
            fg=C.success if changed else C.danger)
        self._redraw()

    # ────────────────────────── 保存 ──────────────────────────
    def _save(self):
        bbox, poly, kpts = self._shapes_to_legacy()
        payload = {
            "shapes": [dict(s, points=[list(p) for p in s["points"]]) for s in self._shapes],
            "bbox": bbox,
            "poly": poly,
            "kpts": kpts or None,
            "cls": int(self._cls_var.get()),
            "discard": bool(self._discard),
        }
        if self._on_save:
            try:
                self._on_save(payload)
            except Exception:
                pass
        self.destroy()


# ───────────────────────── 小工具 ─────────────────────────

_TYPE_CN = {"rectangle": "矩形", "rotation": "旋转框", "polygon": "多边形",
            "point": "点", "line": "线段", "circle": "圆"}





def _shape_color(t):
    return {"rectangle": "#ff4d9d", "rotation": "#d98cff", "polygon": "#2ce8f5",
            "point": "#3ddc97", "line": "#ffd166", "circle": "#ff9f43"}.get(t, C.primary)


def _pt_in_poly(x, y, poly):
    """射线法判点是否在多边形内。"""
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xin = (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-9) + x1
            if x < xin:
                inside = not inside
    return inside


def _pt_seg_dist(px, py, x1, y1, x2, y2):
    """点到线段距离。"""
    import math
    dx, dy = x2 - x1, y2 - y1
    l2 = dx * dx + dy * dy
    if l2 < 1e-9:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / l2))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))
