# -*- coding: utf-8 -*-
"""
YOLO-S UI组件库 v4 — 按钮无位移hover + 动画 + 简洁美观
"""

from __future__ import annotations

import logging
import tkinter as tk
import tkinter.font as tkfont
import os, time, threading, subprocess, sys
from tkinter import ttk, messagebox, filedialog
from typing import Callable, Optional, Tuple, List

_log = logging.getLogger(__name__)

from infrastructure.config import Theme, get_config, get_theme
from gui.motion_kit import animate, fade_in, hover_bind, pulse, stop_pulse
from gui import ui_perf as _up
from gui import ui_tokens as TK

_T = get_theme()
C = _T.colors
F = _T.fonts

# ═══════════════════════ 设计系统 (2026-09-11 视觉重设计) ═══════════════════════
# 这里过去自己定义了一套 F4(15/12/10/9/8/7pt) 和 SP4(6/12/20/28) —— 与
# ui_tokens 的 8px 栅格 + 统一字阶**互相打架**: 同一个页面里两种节奏、六种字号,
# 正是用户说的"排版乱糟糟"。现在只做别名, 唯一的标尺是 ui_tokens。
F4 = {
    "h1": TK.TYPE["h1"],        # 17 页标题
    "h2": TK.TYPE["h2"],        # 13 卡标题
    "h3": TK.TYPE["h3"],        # 11 小节标题
    "body": TK.TYPE["body"],    # 10 正文
    "label": TK.TYPE["label"],  # 9  标签
    "tiny": TK.TYPE["tiny"],    # 8  元信息 (7pt 已废弃: 1920 屏上等于糊)
    "mono": TK.TYPE["mono"],    # 10 代码/数值
}
SP4 = dict(TK.SP4)

# 内容列最大宽度: 超过这个宽度就不再横向拉伸, 改为居中 —— 解决"卡片被拉到
# 1150px 宽而内容只占左边 400px、右边一大片空白"的观感问题。
PAGE_MAXW = 1180


def step_card(parent, idx, title, subtitle=None, bg=None, stretch=False):
    """v2 (v0.23.6): 小圆形序号徽标 (accent 12% 淡染) + 精排标题 + 低对比收束下划线 — 全站分区卡统一视觉
    返回 (card_canvas, body) — 与旧签名一致

    `stretch=True` 让卡片能真正被外部 `pack(fill="both", expand=True)` 撑高,
    内部内容 (`body`) 的 expand 才会生效。见 `rounded_card` 的说明。
    """
    T = _tk5()
    bg = bg or C.card_bg
    cv, body_base = rounded_card(parent, bg=bg, pad=(18, 12), radius=T["radius"] + 2,
                                 stretch=stretch)
    head = tk.Frame(body_base, bg=bg)
    head.pack(fill="x")
    if idx is not None:
        tile = tk.Canvas(head, width=18, height=18, bg=bg, highlightthickness=0)
        tile.pack(side="left", padx=(0, 10))

        def _tile(_e=None):
            tile.delete("all")
            tile.create_oval(0.5, 0.5, 17.5, 17.5,
                             fill=_blend(bg, C.accent, 0.12),
                             outline=_blend(bg, C.accent, 0.30))
            tile.create_text(9, 9, text=str(idx),
                             font=("Microsoft YaHei UI", 9, "bold"), fill=C.accent)

        tile.bind("<Configure>", _tile)
        tile.after(60, _tile)
    t = tk.Frame(head, bg=bg)
    t.pack(side="left", fill="x", expand=True)
    tk.Label(t, text=title, font=TK.TYPE["h2"], bg=bg,
             fg=C.text).pack(anchor="w")
    if subtitle:
        tk.Label(t, text=subtitle, font=TK.TYPE["label"], bg=bg,
                 fg=C.text_secondary).pack(anchor="w", pady=(1, 0))
    # 收束下划线: accent → bg 低对比淡出 (信息层级线)
    #
    # ★★ 2026-10-03 修：**少了 `width=1`, 每张 step_card 都被撑到请求宽 378px。**
    #   `tk.Canvas` 不给 `width` 时的默认请求宽是 **378px**（Tk 内建默认值, 与主题无关）。
    #   这条线用 `fill="x"` 铺开, 看着"宽度随便", 但它**照旧上报 378** 给 pack 父容器
    #   `head` —— 于是**每一个 step_card 的请求宽都不小于 378**, 无论里面装的是
    #   一句短标题还是一堆控件。单列布局时看不出来（列本来就有 700px）;
    #   一旦把两张 step_card **并排**放, 378+378+间隙 直接顶破可用的 701px
    #   ⇒ 整页横向溢出 (实测 dataset 页 `req=1248` > 可用 1177)。
    #   与 `rounded_card` / `page_header` 里那些 canvas 一样, 明确写 `width=1`
    #   才是"我只要高度、宽度交给 fill" 的正确表达。
    #   ⚠ 同一个坑在 ④⑤ 两处也存在 (本文件外的自绘 canvas 若没写 width 同理)。
    ul = tk.Canvas(head, width=1, height=1, bg=bg, highlightthickness=0)
    ul.pack(fill="x", pady=(8, 2))
    _ul_stops = (_blend(C.accent, bg, 0.42), _blend(C.accent, bg, 0.92))

    def _ul(_e=None):
        w = ul.winfo_width()
        if w < 20:
            return
        # 单条缓存位图取代逐像素 create_line (实测 11.2ms -> 0ms 命中)
        _up.draw_gradient_strip(ul, "ul", 0, 0, w, 1, _ul_stops, orient="h", bg=bg)

    ul.bind("<Configure>", lambda e: _up.debounce(ul, "ul", 50, _ul))
    ul.after(60, _ul)
    body = tk.Frame(body_base, bg=bg)
    body.pack(fill="both", expand=True)
    return cv, body


def action_bar(parent, bg=None):
    """统一操作条 (36px 高按钮组, 8px 间距) — 返回按钮条 frame"""
    bar = tk.Frame(parent, bg=bg or C.bg)
    return bar


def h2(parent, text, bg=None):
    """小节标题行"""
    bg = bg or _up.safe_bg(parent, C.bg)
    tk.Label(parent, text=text, font=F4["h2"], bg=bg, fg=C.text).pack(anchor="w")


def collapse_group(parent, title, icon="", bg=None, open_default=True):
    """极简可折叠分区 (无边框): 头部把手 + 内容宿主; 返回 (frame, body, toggle)"""
    bg = bg or _up.safe_bg(parent, C.bg)
    frm = tk.Frame(parent, bg=bg)
    head = tk.Frame(frm, bg=bg)
    head.pack(fill="x", padx=20, pady=(8, 2))
    caret = tk.Label(head, text="▾" if open_default else "▸", font=("Microsoft YaHei UI", 10),
                     bg=bg, fg=C.text_muted, cursor="hand2")
    caret.pack(side="left")
    if icon:
        tk.Label(head, text=icon, font=(TK.TYPE["body"][0], 10), bg=bg).pack(side="left", padx=(2, 6))
    tk.Label(head, text=title, font=("Microsoft YaHei UI", 12, "bold"), bg=bg, fg=C.text).pack(side="left")
    body = tk.Frame(frm, bg=bg)
    state = {"open": open_default}
    if open_default:
        body.pack(fill="both", expand=True)
    else:
        body.pack_forget()

    def toggle():
        state["open"] = not state["open"]
        if state["open"]:
            body.pack(fill="both", expand=True)
            caret.config(text="▾")
        else:
            body.pack_forget()
            caret.config(text="▸")

    head.bind("<Button-1>", lambda e: toggle())
    caret.bind("<Button-1>", lambda e: toggle())
    return frm, body, toggle


# ═══════════════════════ UI v6 (v2 重设计: 单 accent 电光蓝 + 克制动效) ═══════════════════════
def _tk5():
    """当前主题 token (v6): 全部由 get_theme().colors 派生, 主题切换后即时生效"""
    try:
        from gui.theme_manager import theme_manager
        m = theme_manager.resolved() or "dark"
    except Exception:
        m = "dark"
    return {
        "bg": C.bg, "card": C.card_bg, "card2": C.bg2, "hover": C.nav_hover_bg,
        "text": C.text, "text2": C.text_secondary, "text3": C.text_muted,
        "border": C.border, "accent": C.accent, "accent_press": C.primary_hover,
        "success": C.success, "warning": C.warning, "danger": C.danger,
        "glass": C.glass_highlight, "radius": TK.RADIUS["md"], "radius_lg": TK.RADIUS["lg"],
    }


def rounded_card(parent, radius=None, bg=None, border=None, glass_top=True, pad=(14, 12),
                 stretch=False):
    """圆角玻璃卡 (Canvas 自绘圆角矩形 + 顶高光) — 返回 (canvas, inner)
    用法: cv, inner = rounded_card(parent); cv.pack(fill="x"); 控件 pack 到 inner

    `stretch` (2026-09-11 新增, 默认关):
        默认行为是"画布高度 = 内容请求高度"(`_fit` 里 `cv.config(height=want)`),
        于是外部即使 `pack(fill="both", expand=True)` 把**画布**拉高了, 承载内容的
        `inner` 窗口仍然只有自然高度 —— 卡片边框被撑到 490px, 内容却只占 384px,
        下半截是一层空壳。

        实测: deploy 页右栏因此残留约 190px 空白。`stretch=True` 时改为
        "画布高度交给外部布局, `inner` 跟随画布高" —— 卡片内部的 expand 才能真的生效。
        默认关, 是为了不动其余全部页面的既有行为。
    """
    T = _tk5()
    radius = radius if radius is not None else T["radius"]
    bg = bg or C.card_bg
    border = border or C.border
    # ⚠ 2026-10-03: 必须显式写 `width=1`。
    #   `tk.Canvas` 不给 width 时默认请求宽是 **378px**, 于是**每张卡片都至少请求 378**
    #   ——单列布局看不出来, 把两张卡**并排**就会 378+378 顶破可用宽度、整页横向溢出
    #   (实测 dataset 页 req 1248 > 可用 1177)。宽度本来就由 `_w()` 里的
    #   `itemconfig(_win, width=...)` 跟随父容器决定, 请求宽只需是个下限。
    cv = tk.Canvas(parent, width=1, highlightthickness=0, bd=0,
                   bg=_up.safe_bg(parent, C.bg))
    inner = tk.Frame(cv, bg=bg)
    _win = cv.create_window((pad[0] - 1, pad[1] - 1), window=inner, anchor="nw")
    _st = {"w": -1, "h": -1, "fit": -1}

    def _draw(e=None):
        w_, h_ = cv.winfo_width(), cv.winfo_height()
        if w_ < 12 or h_ < 12:
            return
        if (w_, h_) == (_st["w"], _st["h"]):
            return  # 尺寸未变则不重画 (切断 Configure 回环)
        _st["w"], _st["h"] = w_, h_
        cv.delete("shape")
        r_ = min(radius, h_ // 2, w_ // 2)
        pts = [r_, 0, w_ - r_, 0, w_, 0, w_, r_, w_, h_ - r_, w_, h_,
               w_ - r_, h_, r_, h_, 0, h_, 0, h_ - r_, 0, r_, 0, 0]
        cv.create_polygon(pts, smooth=True, splinesteps=12, fill=bg,
                          outline=border, tags="shape")
        if glass_top:
            cv.create_line(r_ + 4, 1.5, w_ - r_ - 4, 1.5,
                           fill=C.glass_highlight, tags="shape")
        cv.tag_lower("shape")

    def _fit(e=None):
        # 只在请求高度真的变化时才改画布高度 —— 旧实现每次 Configure 都 config(),
        # 会触发 "inner Configure -> cv.config -> cv Configure -> _draw" 回环,
        # 是页面构建期多轮布局的主要来源。
        if not stretch:
            try:
                want = inner.winfo_reqheight() + 2 * (pad[1] - 1)
                if want != _st["fit"] and want > 0:
                    _st["fit"] = want
                    cv.config(height=want)
            except Exception:
                pass
        _up.debounce(cv, "shape", 40, _draw)

    inner.bind("<Configure>", _fit)
    cv.bind("<Configure>", lambda e: _up.debounce(cv, "shape", 40, _draw))
    cv.after(60, _draw)

    def _w(e):
        try:
            want = max(10, e.width - 2 * (pad[0] - 1))
            if cv.itemcget(_win, "width") != str(want):
                cv.itemconfig(_win, width=want)
        except Exception:
            pass

    cv.bind("<Configure>", _w, add="+")

    if stretch:
        def _fit_inner(e=None):
            """stretch 模式: 画布高度是"地板 + 天花板"双职责。

            地板 —— 画布请求高度不小于内容高。**2026-09-11 修**: 旧实现无条件跳过
            `_fit`, 于是 Canvas 的 reqheight 停在默认的 ≈0; 放进 `scrollable` 这种
            "父容器高度由内容决定"的地方时, 整张卡被压成一条, 内容从中间被裁掉
            (实测 dataset/training 左列)。`expand=True` 救不了 —— 高度是卡片**自己
            请求**的, 父容器撑不动它 (见避坑清单 65)。

            天花板 —— 画布比内容高时, 让内容窗跟随画布高 (grid/sticky="nsew" 场景:
            卡片被外部拉高, 内容跟着铺满, 不留空壳)。
            """
            try:
                req = inner.winfo_reqheight()
                need = req + 2 * (pad[1] - 1)
                h = cv.winfo_height()
                if need > 0 and h < need:
                    # 比内容矮 -> 抬高画布, 把真实高度撑出来
                    if cv.itemcget(_win, "height") != str(req):
                        cv.itemconfig(_win, height=req)
                    if _st["fit"] != need:
                        _st["fit"] = need
                        cv.config(height=need)
                    return
                avail = max(10, h - 2 * (pad[1] - 1))
                want = max(avail, req)
                if cv.itemcget(_win, "height") != str(want):
                    cv.itemconfig(_win, height=want)
            except Exception:
                pass

        # inner 尺寸变了要重算 (文字换行/控件增减), cv 尺寸变了也要重算 (被外部拉伸)
        inner.bind("<Configure>", lambda e: _up.debounce(cv, "sfit", 40, _fit_inner),
                   add="+")
        cv.bind("<Configure>", lambda e: _up.debounce(cv, "sfit", 40, _fit_inner),
                add="+")
        cv.after(80, _fit_inner)

    return cv, inner


def pill_btn(parent, text, cmd, kind="primary", big=False):
    """圆角胶囊按钮 (v2: accent 实底 + 160ms hover + 100ms 按压, 中性=透明+描边)"""
    if kind in ("primary", "accent"):
        bgc, fg = C.accent, "#FFFFFF"
        hover, press = C.primary_hover, _darken(C.accent, 16)
        border_bg, border_hover = C.accent, C.glow
    elif kind == "success":
        bgc, fg = C.success, "#FFFFFF"
        hover, press = C.success_hover, _darken(C.success, 16)
        border_bg, border_hover = C.success, C.glow
    elif kind == "danger":
        bgc, fg = C.danger, "#FFFFFF"
        hover, press = C.danger_hover, _darken(C.danger, 16)
        border_bg, border_hover = C.danger, C.glow
    else:  # border / ghost → 中性
        bgc = _up.safe_bg(parent, C.bg)
        fg = C.text_secondary
        hover, press = C.nav_hover_bg, _darken(C.nav_hover_bg, 12)
        border_bg, border_hover = C.border, C.border
    b = tk.Label(parent, text=("  " + text + "  ") if big else (" " + text + " "),
                 font=("Microsoft YaHei UI", 11, "bold") if big else TK.TYPE["body_bold"],
                 bg=bgc, fg=fg, padx=16 if big else 12, pady=8 if big else 6,
                 cursor="hand2", highlightthickness=1, highlightbackground=border_bg)
    b._hover = False
    hover_bind(b, "bg", base=bgc, hover=hover, ms=160)

    def _press(_e=None):
        animate(b, "bg", press, ms=100)

    def _release(_e=None):
        animate(b, "bg", hover if b._hover else bgc, ms=160)
        cmd()

    def _enter(_e=None):
        b._hover = True
        animate(b, "highlightbackground", border_hover, ms=160)

    def _leave(_e=None):
        b._hover = False
        animate(b, "highlightbackground", border_bg, ms=160)

    b.bind("<Enter>", _enter, add="+")
    b.bind("<Leave>", _leave, add="+")
    b.bind("<ButtonPress-1>", _press, add="+")
    b.bind("<ButtonRelease-1>", _release, add="+")
    return b


# ═══════════════════════ UI v8 (真渐变/玻璃艺术/空状态) ═══════════════════════
_PILL_FONT_CACHE: dict = {}


def _pill_font(size: int):
    """按字号缓存 Font —— 每次都 new 一个 Font 会往 Tk 注册新命令, 纯浪费。"""
    f = _PILL_FONT_CACHE.get(size)
    if f is None:
        try:
            import tkinter.font as _tkfont
            f = _tkfont.Font(font=("Microsoft YaHei UI", size, "bold"))
        except Exception:
            f = False
        _PILL_FONT_CACHE[size] = f
    return f or None


def gradient_pill(parent, text, cmd, c1=None, c2=None, bg=None, big=False,
                  fg="#06263A", border=None, icon=""):
    """v8 渐变圆角按钮 (v3 性能版: 缓存圆角渐变位图, hover 只换图不重画)

    2026-09-11 修: 宽度以前是写死的 (big=200 / 普通=130)。Canvas **会裁掉超出
    画布的字**, 所以文案一长 (如主操作"🚀 一键开始（少图→批量生成）") 就从中间
    被切掉, 右边还紧贴着下一个按钮, 看着像界面坏了。现在按实测字形宽度算,
    并保留原来的下限宽度当最小尺寸。
    """
    T = _tk5()
    c1 = c1 or C.accent
    c2 = c2 or _blend(c1, C.glass_highlight, 0.35)
    bg = bg or _up.safe_bg(parent, C.bg)
    border = border or C.border
    if fg == "#06263A":  # 旧默认深字 → v6 accent 实底下改白字
        fg = "#FFFFFF"
    fsize = 12 if big else 10
    disp = (icon + "  " + text) if icon else text
    w = 200 if big else 130          # 最小宽度
    h = 44 if big else 34
    try:
        _f = _pill_font(fsize)
        if _f is not None:
            w = max(w, _f.measure(disp) + (52 if big else 38))
    except Exception:
        pass
    r = min(14, h // 2)
    cv = tk.Canvas(parent, width=w, height=h, bg=bg, highlightthickness=0, cursor="hand2")
    _st = {"hover": False, "img": None, "shape": None}

    def _paint(hover=False):
        # 圆角渐变 + 边框一次性做成位图, 一个 create_image 取代 130 条 create_line
        a, b = (_lighten(c1, 12), _lighten(c2, 12)) if hover else (c1, c2)
        img = _up.rounded_image(w, h, r, (a, b), orient="h",
                                outline=_lighten(border, 16) if hover else border,
                                outline_w=1,
                                owner=cv)
        if img is None:  # 无 PIL 兜底
            cv.delete("all")
            cv.create_rectangle(0, 0, w, h, fill=a, outline=border)
        else:
            _st["img"] = img
            cv.delete("paint")
            cv.create_image(0, 0, image=img, anchor="nw", tags="paint")
            cv.tag_lower("paint")
        # ⚠ 2026-09-11 (卡顿修复): 高光线与文字**与 hover 无关** —— hover 只换
        #   背景位图 (a,b 两个颜色), disp/fg/字号都不变。原来每次 _paint 都
        #   `delete + create_text`, 而实测这个 delete 本身约 6 ms (删一个 canvas
        #   text item), 鼠标进出一次就白付两遍。改成"没有才建"。
        #   守卫用 find_withtag 而不是自建标志位: 上面"无 PIL 兜底"分支里的
        #   `cv.delete("all")` 会把它们一起删掉, 这里会自动补回来, 不用另一份状态。
        # ⚠ 别顺手把字体换成 `_pill_font(fsize)` 返回的 tkfont.Font 对象 ——
        #   那个对象是在**创建时**给 Tk 注册的, 而 Font(font=...) 不传 master 时
        #   注册在 tkinter._default_root 上。本项目进程里存在第二个 Tk 解释器
        #   (splash / 冒烟测试 / 主题重建), 那样拿到的 font 名属于别人家,
        #   create_text 会报 "font ... doesn't exist"。元组写法没有这个问题,
        #   而且预热到位后一次 create_text 只要 0.1 ms (见 ui_perf.warm_fonts)。
        if not cv.find_withtag("deco"):
            cv.create_line(r + 4, 2.0, w - r - 4, 2.0,
                           fill=C.glass_highlight, tags="deco")
        if not cv.find_withtag("txt"):
            cv.create_text(w // 2, h // 2, text=disp, fill=fg,
                           font=("Microsoft YaHei UI", fsize, "bold"), tags="txt")

    def _enter(_e=None):
        if _st["hover"]:
            return
        _st["hover"] = True
        # 轻微提亮: 只换配色重新生成一次位图 (命中缓存的成本约 0.1ms)
        _paint(True)

    def _leave(_e=None):
        if not _st["hover"]:
            return
        _st["hover"] = False
        _paint(False)

    cv.bind("<Button-1>", lambda e: cmd())
    cv.bind("<Enter>", _enter)
    cv.bind("<Leave>", _leave)
    cv.after(50, _paint)
    return cv


def empty_state(parent, icon, title, hint, bg=None):
    """v2 空状态 (v0.23.6): accent 淡染圆盘 + 图标 + 标题 + 提示 — 消灭空白页"""
    T = _tk5()
    bg = bg or _up.safe_bg(parent, C.bg)
    f = tk.Frame(parent, bg=bg)
    cv = tk.Canvas(f, width=96, height=96, bg=bg, highlightthickness=0)
    cv.pack(pady=(22, 6))
    # 低对比柔光圆盘: accent 10% 淡染 + 22% 细环 (无高饱和渐变)
    cv.create_oval(8, 8, 88, 88, fill=_blend(bg, C.accent, 0.10),
                   outline=_blend(bg, C.accent, 0.22))
    cv.create_text(48, 46, text=icon, font=(TK.TYPE["h1"][0], 25), fill=C.accent)
    tk.Label(f, text=title, font=TK.TYPE["h2"], bg=bg,
             fg=C.text).pack()
    tk.Label(f, text=hint, font=TK.TYPE["body"], bg=bg,
             fg=C.text_secondary).pack(pady=(TK.SPACE["xs"], 0))
    return f


# ═══════════════════════ Utilities ═══════════════════════

def center_window(win, w, h):
    win.update_idletasks()
    sw = win.winfo_screenwidth(); sh = win.winfo_screenheight()
    win.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

def _blend(c1, c2, t):
    """线性混合两个 hex 颜色 (委托 ui_perf, 非 hex 输入不再抛异常)"""
    return _up.blend(c1, c2, t)

def _darken(hex_color, amount=30):
    return _up.darken(hex_color, amount)


def _lighten(hex_color, amount=30):
    return _up.lighten(hex_color, amount)


# ═══════════════════════ Core Widgets ═══════════════════════

def label(parent, text="", font=None, fg=None, bg=None, anchor="w", bold=False, wl=None):
    f = font or (F.bold if bold else F.normal)
    return tk.Label(parent, text=text, font=f, bg=bg or _up.safe_bg(parent, C.bg),
                    fg=fg or C.text, anchor=anchor,
                    wraplength=wl or 0, justify="left" if wl else "center")


def autowrap(widget, min_px=140, extra=0):
    """让 Label 的 wraplength 跟随自身实际宽度 —— 请求高度与渲染高度对齐。

    坑 (2026-09-11): 写死 `wraplength=720` 的 Label 放进 560px 宽的列时, 文字
    实际按 560px 折行 (多出一行), 但它上报的 `winfo_reqheight()` **仍是按 720px
    算的** —— 卡片 `_fit` 拿这个值定高, 于是差一行, 末行被卡的底边裁掉
    (训练页「训练工作台」摘要实测)。

    治因的修法: 让 wraplength 跟着宽度走。不要靠"把 wraplength 改小" —— 那只是
    把同一个坑推给下一个更窄的容器。
    """
    def _fit(_e=None):
        try:
            w = widget.winfo_width()
            if w <= min_px:
                return
            want = max(min_px, w - extra)
            if str(widget.cget("wraplength")) != str(want):
                widget.config(wraplength=want)
        except Exception:
            pass
    widget.bind("<Configure>", lambda e: _up.debounce(widget, "awrap", 60, _fit), add="+")
    try:
        widget.after(120, _fit)
    except Exception:
        pass
    return widget


def button(parent, text, command, *, fs=10, bg=None, fg=None, hbg=None,
           px=14, py=6, bold=False, state="normal", icon=""):
    """
    按钮 — highlightthickness 预分配为 2, 避免 hover 时位移 BUG。
    未 hover: highlightbackground 与 btn_bg 同色 (透明效果)
    hover:    highlightbackground = PRIMARY (蓝色边框显现)
    所有事件绑定到 Frame 和 Label 上。
    """
    btn_bg = bg or C.primary
    btn_fg = fg or "white"
    hover_bg = hbg or _lighten(btn_bg, 18)
    press_bg = _darken(btn_bg, 15)
    enabled = state != "disabled"

    font = (F.family, fs, "bold") if bold else (F.family, fs)
    display = (icon + " " + text).strip() if icon else text

    outer = tk.Frame(parent, bg=btn_bg,
                     highlightbackground=btn_bg,   # 初始与背景同色 → 看不见边框
                     highlightthickness=2,
                     cursor="hand2" if enabled else "arrow")
    lbl = tk.Label(outer, text=display, font=font, bg=btn_bg, fg=btn_fg,
                   padx=px, pady=py, cursor="hand2" if enabled else "arrow")
    lbl.pack()

    def _norm():
        if outer._enabled:
            outer.config(bg=btn_bg, highlightbackground=btn_bg)
            lbl.config(bg=btn_bg, fg=btn_fg, pady=py)

    def _enter(e):
        if not outer._enabled: return "break"
        outer.config(bg=hover_bg, highlightbackground=C.glow)
        lbl.config(bg=hover_bg)

    def _leave(e):
        if not outer._enabled: return "break"
        _norm()

    def _press(e):
        if not outer._enabled: return "break"
        outer.config(bg=press_bg, highlightbackground=C.primary)
        lbl.config(bg=press_bg)
        if py >= 6:
            lbl.config(pady=(py - 2, py + 2))

    def _release(e):
        if not outer._enabled: return "break"
        outer.config(bg=hover_bg, highlightbackground=C.glow)
        lbl.config(bg=hover_bg)
        lbl.config(pady=py)
        command()

    def _enable():
        outer._enabled = True; _norm()
        outer.config(cursor="hand2"); lbl.config(state="normal", cursor="hand2")

    def _disable():
        outer._enabled = False
        outer.config(bg="#cbd5e1", highlightbackground="#cbd5e1", cursor="arrow")
        lbl.config(bg="#cbd5e1", fg="#94a3b8", state="disabled", cursor="arrow")

    outer._enabled = enabled
    outer.enable = _enable
    outer.disable = _disable
    outer.lbl = lbl

    if enabled:
        _norm()
    else:
        _disable()

    # 始终绑定事件 — 否则 .enable() 后 hover/click 失效
    for w in (outer, lbl):
        w.bind("<Enter>", _enter, add="+")
        w.bind("<Leave>", _leave, add="+")
        w.bind("<ButtonPress-1>", _press, add="+")
        w.bind("<ButtonRelease-1>", _release, add="+")

    return outer


def card(parent, padx=18, pady=16, bg=None, bc=None, bw=1, cursor=None, glow=True):
    """玻璃卡片 — 高光顶线 + 细边框，hover 时边框发光"""
    bg = bg or C.card_bg
    bc = bc or C.border
    frm = tk.Frame(parent, bg=bg, highlightbackground=bc,
                   highlightthickness=bw, cursor=cursor or "arrow")
    if glow:
        tip = tk.Frame(frm, bg=C.glass_highlight, height=2)
        tip.pack(fill="x")
        frm.bind("<Enter>", lambda e: frm.config(highlightbackground=C.glow), add="+")
        frm.bind("<Leave>", lambda e: frm.config(highlightbackground=bc), add="+")
    return frm


def section_title(parent, icon="", title="", desc=""):
    frm = tk.Frame(parent, bg=_up.safe_bg(parent, C.bg))
    label(frm, f"{icon}  {title}", font=F.h3, fg=C.text, bold=True).pack(anchor="w")
    if desc:
        label(frm, desc, font=F.sm, fg=C.text_muted, wl=420).pack(anchor="w", pady=(2, 0))
    return frm


def _txt_w(s: str, pt: int = 9) -> int:
    """粗略测量文本像素宽 (CJK 按字宽 ~1.05*pt, 西文 ~0.55*pt)。

    旧代码用 `len(text)*11` 估算胶囊宽度 —— 中文按 1 字 = 11px 算严重偏窄,
    于是"局域网分享"这类按钮文字直接顶破胶囊边框。现在按字符类别分别估。
    """
    w = 0.0
    for ch in str(s):
        w += pt * (1.06 if ord(ch) > 0x2E80 else 0.56)
    return int(w + 0.5)


def page_header(parent, icon="", title="", subtitle="", badge=None, actions=None):
    """Hero 页眉 v4 (2026-09-11 视觉重设计)。

    设计: 一块**圆角柔光面** (card 白 + 7% accent 淡染 + 14 圆角 + 1px 描边),
    左侧 accent 淡染图标磁贴, 17pt 标题 / 10pt 副标题, 右侧徽章与操作胶囊,
    底部一条 accent→透明 的发丝渐变线收束。全部由单张缓存位图组成 (1 图元/层)。

    旧版是一条铺满整屏的 #nav_active_bg→#glass_highlight→#purple 三色渐变带 ——
    高饱和、无圆角、和下面的白卡片撞在一起, 是"乱"的主要来源之一。
    """
    bg = _up.safe_bg(parent, C.bg)
    head = tk.Frame(parent, bg=bg)
    head.pack(fill="x", padx=TK.PAGE_PAD, pady=(TK.SPACE["lg"], TK.SPACE["sm"]))
    cv = tk.Canvas(head, height=72, bg=bg, highlightthickness=0)
    cv.pack(fill="x")

    def _rr(x1, y1, x2, y2, r=12, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
               x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return cv.create_polygon(pts, smooth=True, splinesteps=12, **kw)

    def _draw(_e=None):
        w = cv.winfo_width()
        if w < 120:
            return
        h = 72
        cv.delete("all")
        # ① 圆角柔光底: 白 → 7% accent → 5% purple, 单张缓存位图
        img = _up.rounded_image(
            w, h, TK.RADIUS["lg"],
            (_blend(C.card_bg, C.accent, 0.09), C.card_bg,
             _blend(C.card_bg, C.purple, 0.055)),
            orient="h", bg=bg, outline=C.border, outline_w=1,
            owner=cv)
        if img is not None:
            cv._ph_img = img                      # 防 GC
            cv.create_image(0, 0, image=img, anchor="nw")
        else:
            _rr(0.5, 0.5, w - 0.5, h - 0.5, r=TK.RADIUS["lg"],
                fill=C.card_bg, outline=C.border)
        # ② 玻璃高光顶线
        cv.create_line(TK.RADIUS["lg"] + 6, 1.5, w - TK.RADIUS["lg"] - 6, 1.5,
                       fill=C.glass_highlight)
        # ③ 图标磁贴 (accent 淡染圆角 + accent 图标)
        _rr(16, 15, 58, 57, r=TK.RADIUS["md"],
            fill=_blend(C.card_bg, C.accent, 0.10),
            outline=_blend(C.card_bg, C.accent, 0.22))
        cv.create_text(37, 36, text=icon, font=TK.ICON["lg"], fill=C.accent)
        # ④ 标题 + 副标题 (锚定在磁贴右侧, 基线对齐到 72px 高的视觉中轴)
        cv.create_text(74, 29, text=title, anchor="w",
                       font=TK.TYPE["h1"], fill=C.text)
        if subtitle:
            cv.create_text(75, 51, text=subtitle, anchor="w",
                           font=TK.TYPE["body"], fill=C.text_secondary)
        # ⑤ 右侧: 徽章 + 动作胶囊 (圆角 + tag_bind)
        x = w - 16
        if badge:
            _b = str(badge)
            ok = _b.startswith(("✔", "●", "在线", "就绪"))
            bw = _txt_w(_b, 9) + 24
            _rr(x - bw, 22, x, 50, r=14,
                fill=C.success_bg if ok else C.warning_bg,
                outline=_blend(C.card_bg, C.success if ok else C.warning, 0.25))
            cv.create_text(x - bw / 2, 36, text=_b, anchor="center",
                           font=TK.TYPE["label_bold"],
                           fill=C.success if ok else C.warning)
            x -= bw + TK.SPACE["sm"]
        for _text, cmd, kind in reversed(actions or []):
            pal = {"primary": (C.accent, "#FFFFFF"), "danger": (C.danger, "#FFFFFF"),
                   "border": (C.card_bg, C.text),
                   "ghost": (_blend(C.card_bg, C.accent, 0.10), C.accent)
                   }.get(kind, (C.card_bg, C.text))
            bw = _txt_w(_text, 10) + 30
            rid = _rr(x - bw, 21, x, 51, r=15, fill=pal[0],
                      outline=C.border if kind in ("border", "ghost") else pal[0])
            tid = cv.create_text(x - bw / 2, 36, text=_text,
                                 font=("Microsoft YaHei UI", 10, "bold"), fill=pal[1])
            cv.tag_bind(rid, "<Button-1>", lambda e, c=cmd: c())
            cv.tag_bind(tid, "<Button-1>", lambda e, c=cmd: c())
            _hl = _blend(pal[0], C.accent if kind == "ghost" else "#FFFFFF",
                         0.16 if kind != "ghost" else 0.10)
            cv.tag_bind(rid, "<Enter>", lambda e, r=rid, p=_hl: cv.itemconfig(r, fill=p))
            cv.tag_bind(rid, "<Leave>", lambda e, r=rid, p=pal[0]: cv.itemconfig(r, fill=p))
            x -= bw + TK.SPACE["sm"]

    cv.bind("<Configure>", lambda e: _up.debounce(cv, "ph", 50, _draw))
    cv.after(60, _draw)
    return head


def chip(parent, text, kind="ok"):
    """状态芯片 (ok/warn/err/off) — 9pt 小胶囊, 统一内边距"""
    m = {"ok": (C.success_bg, C.success), "warn": (C.warning_bg, C.warning),
         "err": (C.danger_bg, C.danger), "off": (_up.safe_bg(parent, C.bg2), C.text_secondary)}
    bg, fg = m.get(kind, m["off"])
    return tk.Label(parent, text=text, font=TK.TYPE["label_bold"],
                    bg=bg, fg=fg, padx=9, pady=3)


# ═══════════════ 设计系统 v3 (主流桌面风格: 8px 栅格 / 统一控件 / 紧凑顶栏) ═══════════════
_SP = 8


def btn(parent, text, cmd, kind="ghost", big=False):
    """统一按钮: 主操作蓝系/危险红/幽灵 — 简洁配色 (success 收敛为 accent)。

    2026-09-09 深色玻璃: 补 hover 提亮 / 按下压暗（无位移），全站按钮手感一致。
    2026-09-11 视觉重设计: **幽灵/描边按钮补上 1px 边框** —— 旧版 bg=C.input_bg
    (浅色下=纯白) 且无边框, 卡在白色卡片上等于隐身, 用户根本看不出那是按钮。
    """
    pal = {"primary": (C.primary, "#FFFFFF"), "accent": (C.accent, "#FFFFFF"),
           "success": (C.accent, "#FFFFFF"), "danger": (C.danger, "#FFFFFF"),
           "border": (C.card_bg, C.text), "ghost": (C.card_bg, C.text_secondary)}
    bg, fg = pal.get(kind, pal["ghost"])
    outlined = kind in ("border", "ghost")
    b = tk.Label(parent, text=text, font=("Microsoft YaHei UI", 10 if big else 9, "bold"),
                 bg=bg, fg=fg, padx=20 if big else 14, pady=8 if big else 6,
                 cursor="hand2",
                 highlightthickness=1,
                 highlightbackground=C.border if outlined else bg)
    b.bind("<Button-1>", lambda e, c=cmd: c())
    try:
        _solid = kind in ("primary", "accent", "success", "danger")
        hover_bg = _blend(bg, C.accent if outlined else "#FFFFFF", 0.10 if outlined else 0.12)
        press_bg = _blend(bg, "#000000", 0.20)
        hb = C.glow if outlined else (C.glow if _solid else bg)
        # ★★ 2026-09-22（严重）：下面四个**视觉反馈**绑定必须 `add="+"`。
        #   Tk 里 `<Button-1>` 与 `<ButtonPress-1>` 是**同一个事件序列** ——
        #   不带 `add="+"` 的第二次 `bind` 会**把上面那条命令绑定覆盖掉**。
        #   后果：**全站按钮全部变成死的**（只有按下变暗的效果，命令永不执行），
        #   而且**不报任何错**。
        #   实测（最小复现）：
        #     ① 只绑 `<Button-1>` ⇒ 该序列上有 1 条脚本
        #     ② 再绑 `<ButtonPress-1>` ⇒ 仍是 1 条（第一条被替换）
        #     ③ `btn(r,'测试',lambda: fired.append(1))` → 真发一次 `<Button-1>` ⇒
        #        `fired == []`（命令未执行）★ 修好后为 `[1]`
        #   用户侧的表现就是"**点了没反应**""**点击播放没有动画**"——
        #   而状态条照常、日志干净，所以查了很久才落到这里。
        #   ⇒ 判据见 `tools/qc/smoke_btn_click.py`（含注入式负对照）。
        b.bind("<Enter>", lambda e: b.config(bg=hover_bg, highlightbackground=hb),
               add="+")
        b.bind("<Leave>", lambda e: b.config(bg=bg, highlightbackground=C.border
                                             if outlined else bg), add="+")
        b.bind("<ButtonPress-1>", lambda e: b.config(bg=press_bg), add="+")
        b.bind("<ButtonRelease-1>", lambda e: b.config(bg=hover_bg), add="+")
    except Exception:
        pass
    return b


def field(parent, label_text, var, width=None, show=None, tip=None):
    """标签在上 + 输入在下 (主流表单样式) — 控件高度统一 30px"""
    bg = _up.safe_bg(parent, C.bg)
    frm = tk.Frame(parent, bg=bg)
    tk.Label(frm, text=label_text, font=TK.TYPE["label"],
             bg=bg, fg=C.text_secondary).pack(anchor="w", pady=(0, TK.SPACE["xs"]))
    e = tk.Entry(frm, textvariable=var, bg=C.input_bg, fg=C.text, relief="flat",
                 font=TK.TYPE["body"],
                 insertbackground=C.text, highlightbackground=C.border,
                 highlightthickness=1, width=width or 20, show=show or "")
    e.pack(fill="x", ipady=5)
    if tip:
        tk.Label(frm, text=tip, font=TK.TYPE["tiny"],
                 bg=bg, fg=C.text_muted).pack(anchor="w", pady=(TK.SPACE["xxs"], 0))
    return frm


def topbar(parent, icon, title, subtitle=None, actions=None, height=64, layout="pack"):
    """宽松页顶栏: 图标+标题左侧, 动作组右侧 — 24px 页边距

    layout="grid": 由调用方负责 bar.grid(...) (页面顶层改用 grid 时用)
    """
    bg = _up.safe_bg(parent, C.bg)
    bar = tk.Frame(parent, bg=bg, height=height)
    if layout != "grid":
        bar.pack(fill="x", padx=TK.PAGE_PAD, pady=(TK.SPACE["lg"], TK.SPACE["sm"]))
    bar.pack_propagate(False)
    tk.Label(bar, text=icon, font=TK.ICON["lg"], bg=bg).pack(side="left", padx=(0, TK.SPACE["md"]))
    t = tk.Frame(bar, bg=bg)
    t.pack(side="left")
    tk.Label(t, text=title, font=TK.TYPE["h1"], bg=bg, fg=C.text).pack(anchor="w")
    if subtitle:
        tk.Label(t, text=subtitle, font=TK.TYPE["body"], bg=bg,
                 fg=C.text_secondary).pack(anchor="w", pady=(TK.SPACE["xxs"], 0))
    for text, cmd, kind in reversed(actions or []):
        b = btn(bar, text, cmd, kind)
        b.pack(side="right", padx=(TK.SPACE["sm"], 0))
    return bar


def section(parent, title, right=None, pad=0):
    """分区: 标题行 + 主体 (卡片间 12px 栅格)"""
    bg = _up.safe_bg(parent, C.bg)
    frm = tk.Frame(parent, bg=bg)
    frm.pack(fill="both", expand=True, pady=(0, TK.GRID_GAP))
    hd = tk.Frame(frm, bg=bg)
    hd.pack(fill="x")
    tk.Label(hd, text=title, font=TK.TYPE["h3"],
             bg=bg, fg=C.text).pack(side="left")
    if right:
        right(hd)
    body = tk.Frame(frm, bg=bg)
    body.pack(fill="both", expand=True, pady=(TK.SPACE["sm"], 0))
    return frm, body


def sub_card(parent, title, right=None):
    """分区卡 v3: 渐变竖条 + 13pt 标题 (细分隔线) — 返回 (frm, body)"""
    bg = _up.safe_bg(parent, C.bg)
    frm = tk.Frame(parent, bg=bg)
    bar = tk.Frame(frm, bg=bg)
    bar.pack(fill="x")
    dot = tk.Canvas(bar, width=TK.SPACE["xs"], height=16, bg=bg, highlightthickness=0)
    dot.pack(side="left", padx=(TK.PAGE_PAD - 6, TK.SPACE["md"]), pady=(18, 5))
    _d = _up.rounded_image(TK.SPACE["xs"], 16, 2, (C.accent, C.purple), orient="v",
                           bg=bg, owner=dot)
    if _d is not None:
        dot._img = _d
        dot.create_image(0, 0, image=_d, anchor="nw")
    else:
        dot.create_rectangle(0, 0, TK.SPACE["xs"], 16, fill=C.accent, outline="")
    tk.Label(bar, text=title, font=TK.TYPE["h2"],
             bg=bg, fg=C.text).pack(side="left", pady=(14, 5))
    if right is not None:
        right(bar)
    _hairline(frm, C.border, padx=TK.PAGE_PAD - 6)
    body = tk.Frame(frm, bg=bg)
    body.pack(fill="both", expand=True)
    return frm, body


def _hairline(parent, color, padx=16, pady=0):
    """1px 分隔线 (静态, 无渐变开销)"""
    f = tk.Frame(parent, bg=color, height=1)
    f.pack(fill="x", padx=padx, pady=pady)
    return f


def separator(parent, padx=16):
    """细分隔线 (v4: 安静的 1px 发丝线)。

    旧版是 bg→accent35%→border→accent35%→bg 的五段渐变 —— 在浅色主题下渲染成
    一条醒目的蓝线, 整屏出现 4~5 条就成了"视觉噪音", 也是 h3 页面里那条横贯
    中间的蓝带的来源。现在统一为主题 border 色, 只做"轻分隔"这一件事。
    """
    _pbg = _up.safe_bg(parent, C.bg)
    s = tk.Canvas(parent, height=1, bg=_pbg, highlightthickness=0)

    def _draw(_e=None):
        w = s.winfo_width()
        if w < 4:
            return
        s.delete("all")
        s.create_line(0, 0.5, w, 0.5, fill=C.border)

    s.bind("<Configure>", lambda e: _up.debounce(s, "sep", 60, _draw))
    s.after(60, _draw)
    return s


def gradient_canvas(parent, top=None, bottom=None, bands=24):
    """垂直渐变背景 Canvas (v3: 单张缓存位图, 原为 24 条 create_rectangle 逐帧重建)"""
    top = top or C.bg
    bottom = bottom or C.bg2
    cv = tk.Canvas(parent, bg=top, highlightthickness=0, bd=0)

    def _draw(e=None):
        w = cv.winfo_width(); h = cv.winfo_height()
        if w < 2 or h < 2:
            return
        _up.draw_gradient(cv, "grad", w, h, (top, bottom), orient="v")

    cv.bind("<Configure>", lambda e: _up.debounce(cv, "grad", 60, _draw))
    cv.after(50, _draw)
    return cv


# ═══════════ 滚动条: 本机**唯一能换肤**的路径 (2026-09-12 定案) ═══════════
# 事故: 深色主题下**每一页**都挂着一条近白竖带 (#F0F0F0, 几乎满高), 看着像界面坏了。
# 排查经过与结论 (三次独立测量, 全部留档):
#   ① `runs/_tk_sb_repro.py` —— 同一窗口里并排两条 `tk.Scrollbar`: 一条配了
#      bg=#28384E / troughcolor=#1B2636 / relief=flat / borderwidth=0 /
#      highlightthickness=0, 另一条**完全不配色**。两者渲染出的像素**一模一样**,
#      都是 #F0F0F0。⇒ 本机 `tk.Scrollbar` 被交给 Windows 原生滚动条绘制,
#      **所有颜色选项一个都不生效**。
#   ② 可 `cget` 会老老实实回读你设的深色 ⇒ 旧 `dark_scrollbar()` **看着成功、
#      实为空操作**, 代码在骗自己 (与避坑清单第 74/76/81 条同族: 异常/无效果被吞)。
#   ③ `runs/_ttk_sb_repro.py` —— `ttk.Scrollbar` 在 clam 主题下四种写法
#      (默认 style / Vertical.TScrollbar / 自定义多级名 / 再加 style.map)
#      **全部**渲染成 #28384E。⇒ 唯一出路是 ttk。
# ⚠ 前提: ttk 主题必须是 clam。vista / xpnative 走原生绘制, style 里的颜色同样不生效
#   (本机默认就是 vista, 所以 theme_use("clam") 这一步不能省)。
# ⚠ 宽度只能写在 style 的 `width` 上 —— `ttk.Scrollbar` **没有** `-width` 选项,
#   写在控件上会抛 TclError 再被上层 except 吞掉 (又是一次假成功)。
SB_STYLE = "Dark.Vertical.TScrollbar"
SB_STYLE_H = "Dark.Horizontal.TScrollbar"
_SB_KEY = {"k": None}


def _ensure_sb_style(master=None):
    """注册/刷新深色滚动条 style (幂等, 按调色板指纹缓存)。

    ttk style 是**按名字全局**的, 所以调色板一变, 只要重新 configure 一次,
    已经建好的所有滚动条会一起换过来 —— 不需要遍历控件树。
    """
    key = (C.border, C.input_bg, C.text_muted, C.glow, C.bg2)
    if _SB_KEY["k"] == key:
        return
    try:
        st = ttk.Style(master)
        if st.theme_use() != "clam":
            st.theme_use("clam")
        for name in (SB_STYLE, SB_STYLE_H):
            st.configure(name, background=C.border, troughcolor=C.input_bg,
                         bordercolor=C.input_bg, arrowcolor=C.text_muted,
                         lightcolor=C.border, darkcolor=C.input_bg,
                         relief="flat", borderwidth=0, width=12, arrowsize=12)
            st.map(name,
                   background=[("pressed", C.glow), ("active", C.text_muted)],
                   arrowcolor=[("pressed", C.bg2), ("active", C.bg2)],
                   troughcolor=[("disabled", C.input_bg)])
    except Exception as e:
        _log.warning("注册深色滚动条 style 失败: %r", e)
        return
    _SB_KEY["k"] = key


def themed_scrollbar(parent, command=None, orient="vertical"):
    """深色主题下**真的会变暗**的滚动条 (本机唯一可行方案, 取证见上方注释)。"""
    _ensure_sb_style(parent)
    return ttk.Scrollbar(parent, orient=orient, command=command,
                         style=SB_STYLE if orient == "vertical" else SB_STYLE_H)


def dark_scrollbar(sb, width=12):
    """兼容旧调用点, 但**不再假装**能给 `tk.Scrollbar` 换肤。

    2026-09-12 改: 旧实现只调 `sb.config(bg=..., troughcolor=...)` —— 对
    `tk.Scrollbar` 一个字都不生效, 对 `ttk.Scrollbar` 会抛 TclError 被吞掉,
    两种情况都是"看着成功、其实没做"。现在 ttk 的显式挂深色 style, tk 的记 warning
    点明怎么改 (warning 不报错, 保证旧代码不会因此崩)。
    """
    try:
        if sb.winfo_class() == "TScrollbar":
            _ensure_sb_style(sb)
            orient = str(sb.cget("orient")) or "vertical"
            sb.configure(style=SB_STYLE if orient == "vertical" else SB_STYLE_H)
        else:
            _log.warning(
                "dark_scrollbar() 收到 tk.Scrollbar —— 本机此控件由 Windows 原生"
                "绘制, bg/troughcolor 全部无效; 请改用 components.themed_scrollbar()."
                " 控件: %s", sb)
    except Exception:
        pass
    return sb


def dark_scrolledtext(widget, frame_bg=None):
    """给 `tkinter.scrolledtext.ScrolledText` 补上它自带的两块浅色。

    ScrolledText 内部有两处**没跟主题走**的默认色, 深色卡片里都在发光:
      · `widget.frame` —— 它自带的 `tk.Frame`, 建的时候**没设底色** ⇒ 系统默认
        SystemButtonFace (#F0F0F0), 表现为文字区外面一圈浅边;
      · `widget.vbar`  —— 一个 `tk.Scrollbar`, 本机无法换肤 ⇒ 一条近白竖带。
    做法: frame 底色调成主题色; 用 ttk 滚动条**顶替** vbar, 并把原来的 pack 参数
    一并交接, 最后把 `widget.vbar` 指回新控件 —— 调用方无需改动。
    """
    try:
        widget.frame.configure(bg=frame_bg or C.input_bg)
    except Exception as e:
        _log.warning("ScrolledText 外框底色未生效: %r", e)
    try:
        old = widget.vbar
        info = {k: v for k, v in old.pack_info().items() if k != "in"}
        parent = old.master
        old.destroy()
        new = themed_scrollbar(parent, command=widget.yview)
        new.pack(**info)
        widget.configure(yscrollcommand=new.set)
        widget.vbar = new
    except Exception as e:
        _log.warning("ScrolledText 滚动条替换失败: %r", e)
    return widget


def bind_scroll_tree(widget, canvas=None):
    """兼容旧调用: 内容重建后不再需要递归重绑 (滚轮改为 Enter 时 bind_all 收放)。

    保留本函数是为了不破坏页面里已有的 `bind_scroll_tree(...)` 调用点;
    现在只在最外层挂一次 Enter/Leave, O(1) 完成。
    """
    canvas = canvas or widget
    _up.bind_wheel(canvas, widget)


def scrollable(parent, bg=None, maxw=PAGE_MAXW, padx=0, pady=0, fill=False):
    """竖向滚动容器 — 返回 (outer, content)。

    2026-09-11 视觉重设计, 关键行为是 `maxw`:

    `maxw` 内容列最大宽度 —— 超过则**横向居中**, 不再无限拉伸。这是"布局充斥着
    空白"的核心修复之一: 旧版内容列严格等于画布宽度, 于是 1456px 窗口里卡片被
    拉到 1150px 宽, 而卡片内容只占左边 400px, 右边 750px 全是空白。
    实现: canvas 里放 3 列 grid 的 `col` (弹性|定宽|弹性)。

    `fill=True` (备用, 默认关) 会把内容框撑到视口高度, 让 `rowconfigure(weight=1)`
    的行铺开。**默认必须关**: 撑高需要每次读 `inner.winfo_reqheight()`, 而它是
    整棵子树的重算 —— 页面预建期间 `inner` 频繁 Configure, 实测把启动阶段的
    最长卡顿从 132ms 拉到 1841ms。真要"卡片铺满"就在页面里给卡片自己的容器加
    weight, 让留白落在页面背景 (画布色) 上, 而不是把卡片拉空。
    """
    b = bg or C.bg
    outer = tk.Frame(parent, bg=b)
    canvas = tk.Canvas(outer, bg=b, highlightthickness=0)
    sb = themed_scrollbar(outer, command=canvas.yview)
    col = tk.Frame(canvas, bg=b)
    inner = tk.Frame(col, bg=b)
    _st = {"cw": -1, "wh": (-1, -1)}

    if maxw:
        # 内容列 maxw 居中: 两侧 spacer 列 (weight=0) + 中间内容列 (weight=1)。
        #
        # ⚠️ 2026-09-11 修「方块式黑块」——根因在 `_layout`, 但列配置必须与它配对:
        # 中间列的 minsize 会在 `_layout` 里设成 cw (=min(w-2*padx, maxw))。
        # 少了这个下限, 内容里的 `fill="x"` 控件会把中间列撑到画布全宽,
        # 于是按 1180 收住的卡片右侧留出一大块画布色 (#090E16) —— 深色主题下
        # 被卡片圆角边框圈着, 就是用户报的"方块式黑块"。
        col.grid_columnconfigure(0, weight=0)
        col.grid_columnconfigure(2, weight=0)
        col.grid_columnconfigure(1, weight=1)
        inner.grid(row=0, column=1, sticky="nsew")
    else:
        col.grid_columnconfigure(0, weight=1)
        inner.grid(row=0, column=0, sticky="nsew")
    # 关键: 让内容行吃掉 `col` 的剩余高度。少了这一行, 即使下面把 col 撑到视口
    # 高度, grid 里的第 0 行仍然只有"内容自然高度", 内容框不会跟着变高, 页面
    # 底部那圈空白照旧存在 —— 这正是我第一版 fill 改了却没生效的原因。
    col.grid_rowconfigure(0, weight=1)

    _win = canvas.create_window((0, 0), window=col, anchor="nw")

    def _layout():
        w = canvas.winfo_width()
        if w <= 2:
            return
        # 宽度: 铺满画布, 内容列再按 maxw 居中
        cw = w
        if maxw:
            cw = max(160, min(w - 2 * padx, maxw))
        if fill:
            # 备用路径: 把内容框撑到视口高, 让 weight 行铺开 (代价见函数注释)
            h = canvas.winfo_height()
            if h <= 2:
                return
            try:
                need_h = max(h, inner.winfo_reqheight() + 2 * pady)
            except Exception:
                need_h = h
            if _st["wh"] == (w, need_h):
                return
            _st["wh"] = (w, need_h)
            try:
                canvas.itemconfig(_win, width=w, height=need_h)
            except Exception:
                return
        else:
            # 常规路径: 只定宽, 高度交给内容自身 (绝不强制高度 —— 强制会把
            # 比视口高的内容裁掉, 滚动条就废了)
            #
            # 宽度取 w (画布宽) 而不是 cw —— 居中由 col 内部的三列 grid 负责:
            # 内容窗口铺满画布, 中间列用 minsize=spacer 把内容推到视觉中心。
            # 若这里传 cw, 内容窗口本身就只有 1180 宽, col 里再排
            # `spacer + inner + spacer` 会超出 col 宽度, 居中直接失效。
            if _st["wh"] == (w, -1):
                return
            _st["wh"] = (w, -1)
            try:
                canvas.itemconfig(_win, width=w)
            except Exception:
                return
        if maxw and cw != _st["cw"]:
            _st["cw"] = cw
            # 居中: 两侧 spacer 列 (weight=0) 用 minsize 占位, 中间列 weight=1
            # 吃掉余量。三者之和恰好等于 w, 所以内容被精确推到中心。
            # 注意: Tk 的 grid **没有** maxsize 选项 (写它会 TclError)。
            spacer = max(0, (w - cw) // 2)
            # ⚠️ 关键: 中间列不能只靠 weight —— 内容里的 `fill="x"` 控件会把它
            # 撑过 cw (这正是"方块式黑块"的成因: 卡片按 1180 收住, 其余控件
            # 溢出到画布全宽, 空出来的那一大块画布色就是黑块)。
            # 用 minsize=cw 给下限 + 让 outer 侧 spacer 用 minsize 占满余量,
            # 中间列的自然宽度就恰好落在 cw (±spacer 的取整误差 1px)。
            col.grid_columnconfigure(0, weight=0, minsize=spacer)
            col.grid_columnconfigure(2, weight=0, minsize=spacer)
            col.grid_columnconfigure(1, weight=1, minsize=cw)

    def _cc(_e=None):
        _up.debounce(canvas, "scc", 40, _layout)

    def _sr(_e=None):
        # bbox + scrollregion 每帧都会算; 合并到 40ms 一次即可
        _up.debounce(canvas, "sr", 40,
                     lambda: canvas.configure(scrollregion=canvas.bbox("all")))

    inner.bind("<Configure>", _sr)
    canvas.bind("<Configure>", _cc)
    canvas.configure(yscrollcommand=sb.set)
    canvas.after(80, _layout)

    # 滚轮: 进区绑定 / 出区解绑 (取代原来的三遍递归绑定 + 内容重建后漏绑)
    _up.bind_wheel(canvas, outer)

    canvas.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    outer._canvas = canvas
    outer._col = col
    return outer, inner


def combo(parent, values, var=None, w=22, readonly=True):
    if var is None:
        var = tk.StringVar(master=parent)
    cb = ttk.Combobox(parent, textvariable=var, values=values,
                      state="readonly" if readonly else "normal", width=w)
    cb.configure(font=F.normal)
    if values and not var.get():
        cb.current(0)
    return cb


# ═══════════════════════ Toast & Dialog ═══════════════════════

def toast(parent, msg, duration=2000, bg=None):
    bg = bg or C.success
    toaster = tk.Toplevel(parent)
    toaster.overrideredirect(True)
    toaster.attributes("-topmost", True)
    toaster.configure(bg=bg)
    tk.Label(toaster, text=msg, font=F.normal, bg=bg, fg="white",
             padx=24, pady=12).pack()
    parent.update_idletasks()
    try:
        pw, px, py = parent.winfo_width(), parent.winfo_rootx(), parent.winfo_rooty()
        tw = toaster.winfo_reqwidth()
        toaster.geometry(f"+{px + (pw - tw) // 2}+{py + 60}")
    except Exception:
        pass

    def _fade():
        n = max(1, duration // 40)
        for i in range(n, 0, -1):
            try: toaster.attributes("-alpha", i / n)
            except Exception: break
            time.sleep(0.04)  # thread-safe: runs in background Thread
        try: toaster.destroy()
        except Exception: pass
    threading.Thread(target=_fade, daemon=True).start()


def show_error(parent, title, msg):
    messagebox.showerror(title, msg, parent=parent)

def show_info(parent, title, msg):
    messagebox.showinfo(title, msg, parent=parent)

def show_warning(parent, title, msg):
    messagebox.showwarning(title, msg, parent=parent)

def ask_openfile(parent, title, filetypes, initialdir=None):
    return filedialog.askopenfilename(title=title, filetypes=filetypes,
                                       initialdir=initialdir or "", parent=parent) or ""


# ═══════════════════════ Animated Spinner ═══════════════════════

class Spinner(tk.Canvas):
    """旋转加载动画 — 30fps 循环, 简洁小圆"""
    def __init__(self, parent, size=20, color=None):
        super().__init__(parent, width=size + 6, height=size + 6,
                         bg=_up.safe_bg(parent, C.bg), highlightthickness=0)
        self._color = color or C.primary
        self._size = size
        self._angle = 0
        self._running = False
        self._after_id = None
        self._dots = []

    def start(self):
        if not self._running:
            self._running = True
            self._tick()

    def stop(self):
        self._running = False
        if self._after_id:
            try: self.after_cancel(self._after_id)
            except: pass
            self._after_id = None
        try:
            if self.winfo_exists():
                self.delete("all")
        except: pass

    def _tick(self):
        if not self._running:
            return
        try:
            if not self.winfo_exists():
                self._running = False
                return
        except Exception:
            self._running = False
            return
        cx, cy = (self._size + 6) / 2, (self._size + 6) / 2
        r = self._size / 2
        import math
        # v3: 8 个圆点只建一次, 之后每帧只移动/换色 (原为 delete("all") + 重建 8 个椭圆)
        if len(self._dots) != 8:
            self.delete("all")
            self._dots = [self.create_oval(0, 0, 0, 0, fill=self._color, outline="")
                          for _ in range(8)]
        for i in range(8):
            rad = math.radians(self._angle + i * 45)
            x = cx + r * 0.6 * math.cos(rad)
            y = cy + r * 0.6 * math.sin(rad)
            c = _blend("#ffffff", self._color, 0.3 + (i / 8) * 0.7)
            try:
                self.coords(self._dots[i], x - 2, y - 2, x + 2, y + 2)
                self.itemconfig(self._dots[i], fill=c)
            except Exception:
                self._dots = []
                self._running = False
                return
        self._angle = (self._angle - 20) % 360
        try:
            if self._running and self.winfo_exists():
                self._after_id = self.after(33, self._tick)
        except Exception:
            self._running = False


# ═══════════════════════ Progress Panel ═══════════════════════

class ProgressPanel(tk.Frame):
    """Spinner + 进度条 + 步骤文字 — 统一加载面板"""

    def __init__(self, parent, bg=None):
        bg = bg or _up.safe_bg(parent, C.bg)
        super().__init__(parent, bg=bg)

        # row 0: spinner + status text
        top = tk.Frame(self, bg=bg)
        top.pack(fill="x", pady=(0, 4))
        self._spinner = Spinner(top, size=16)
        self._spinner.pack(side="left", padx=(0, 6))
        self._status = tk.Label(top, text="就绪", font=F.sm, fg=C.text_sec,
                                bg=bg, anchor="w")
        self._status.pack(side="left")

        # row 1: progress bar
        self._pvar = tk.DoubleVar(master=self, value=0)
        self._pbar = tk.Canvas(self, height=16, bg=C.input_bg,
                               highlightthickness=1, highlightbackground=C.border)
        self._pbar.pack(fill="x")
        self._pfill = self._pbar.create_rectangle(0, 0, 0, 16, fill=C.primary, outline="")
        self._ptext = self._pbar.create_text(0, 8, text="",
                                             font=TK.TYPE["tiny"], fill="#FFFFFF")
        self._pbar.bind("<Configure>", self._update_bar)

        # row 2: step description
        self._step = tk.Label(self, text="", font=F.sm, fg=C.text_muted, bg=bg, anchor="w", justify="left")
        self._step.pack(fill="x", pady=(2, 0))

    def _update_bar(self, e=None):
        bw = self._pbar.winfo_width()
        bh = self._pbar.winfo_height()
        pct = self._pvar.get() / 100.0
        if bw > 2:
            self._pbar.coords(self._pfill, 0, 0, int(bw * pct), bh)
            self._pbar.coords(self._ptext, bw // 2, bh // 2)

    def start(self, status="加载中..."):
        self._spinner.start()
        self.set_status(status)
        self.set_progress(0)

    def stop(self):
        self._spinner.stop()
        self.set_progress(100)

    def set_progress(self, pct: float, step_text: str = ""):
        self._pvar.set(pct)
        self._update_bar()
        self._pbar.itemconfig(self._ptext, text=f"{int(pct)}%")
        if step_text:
            self._step.config(text=step_text)

    def set_status(self, text: str):
        self._status.config(text=text)


# ═══════════════════════ Animated Card ═══════════════════════

class AnimatedCard(tk.Frame):
    """
    带动画的卡片 — hover 时边框从 BORDER 渐变为 PRIMARY。
    由于 tkinter 限制，用 3 步离散过渡模拟。
    """
    def __init__(self, parent, **kw):
        super().__init__(parent, bg=C.card_bg,
                         highlightbackground=C.border,
                         highlightthickness=1, cursor="hand2", **kw)
        self._hover_state = False

    def bind_click(self, action):
        self._action = action
        for w in [self] + list(self.winfo_children()):
            w.bind("<Enter>", self._enter, add="+")
            w.bind("<Leave>", self._leave, add="+")
            w.bind("<ButtonPress-1>", self._press, add="+")
            w.bind("<ButtonRelease-1>", self._release, add="+")

    def _enter(self, e):
        self._hover_state = True
        self._transition_to(C.glow, 2, _blend(C.card_bg, C.accent, 0.08))

    def _leave(self, e):
        self._hover_state = False
        self._transition_to(C.border, 1, C.card_bg)

    def _press(self, e):
        self.config(bg=_blend(C.card_bg, C.accent, 0.14))

    def _release(self, e):
        if self._hover_state:
            self.config(bg=_blend(C.card_bg, C.accent, 0.08))
        else:
            self.config(bg=C.card_bg)
        if hasattr(self, '_action'):
            self._action()

    def _transition_to(self, target_bc, target_bw, target_bg):
        try:
            self.config(highlightbackground=target_bc,
                        highlightthickness=target_bw,
                        bg=target_bg)
        except Exception:
            pass


# ═══════════════════════ Real-time Installer ═══════════════════════

def pip_install_stream(package: str, on_line: Callable, mirror: str = None,
                       timeout: int = 600) -> int:
    """
    pip 安装 — 实时流式输出每一行, 界面不卡顿。
    默认使用 config.yaml 配置的镜像源; no_proxy=True 时绕过失效系统代理。
    Returns: returncode
    """
    cfg = get_config()
    m = mirror or cfg.pip_mirror
    cmd = [sys.executable, "-m", "pip", "install", package, "-i", m,
           "--progress-bar", "off", "--timeout", "20", "--retries", "2"]
    env = None
    if cfg.pip_no_proxy:
        env = dict(os.environ)
        env["NO_PROXY"] = "*"; env["no_proxy"] = "*"
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
            env.pop(k, None)
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        for line in proc.stdout:
            stripped = line.strip()
            if stripped:
                on_line(stripped)
        proc.wait(timeout=timeout)
        return proc.returncode
    except subprocess.TimeoutExpired:
        try: proc.kill()
        except Exception: pass
        on_line("[超时] 安装超过 {} 秒".format(timeout))
        return -1
    except Exception as e:
        on_line(f"[错误] {e}")
        return -1


def install_packages_stream(packages: List[str], on_status: Callable,
                            on_progress: Callable, on_log: Callable) -> bool:
    """
    批量安装包 — 每个包都流式输出, 实时更新进度。
    on_status: (pct, label)
    on_progress: (package_name, current_line)
    on_log: (package_name, line)
    Returns: all_success
    """
    total = len(packages)
    success_count = 0
    for idx, pkg in enumerate(packages):
        pct = int(idx / total * 100)
        on_status(pct, f"安装 {pkg} ({idx + 1}/{total})...")
        on_log(f"\n>> {pkg}", "hdr")

        rc = pip_install_stream(pkg, on_line=lambda line: on_progress(pkg, line))
        if rc == 0:
            success_count += 1
            on_status(int((idx + 1) / total * 100), f"{pkg} ✓")
        else:
            on_status(int((idx + 1) / total * 100), f"{pkg} ✗ (code={rc})")
    on_status(100, "安装完成!")
    return success_count == total


# ═══════════════════════ 可视化自检面板 ═══════════════════════

class DiagnosticsPanel(tk.Frame):
    """可视化自检面板 — 节点实时动画 (待检/检测中/通过/警告/失败) + 百分比 + 状态行"""

    STATE_COLORS = ("pending", "checking", "ok", "warn", "fail")
    _PULSE_STEP_MS = 260

    def __init__(self, parent, items, bg=None, cols=2, row_h=26):
        bg = bg or _up.safe_bg(parent, C.bg)
        super().__init__(parent, bg=bg)
        self._items = list(items)
        self._cols = max(1, cols)
        self._row_h = row_h
        rows = (len(self._items) + self._cols - 1) // self._cols
        self._canvas_h = rows * row_h + 14
        self._nodes = {}
        self._pulse_on = False
        self._pulse_phase = False
        self._after_id = None

        # 节点画布
        self._cv = tk.Canvas(self, height=self._canvas_h, bg=C.log_bg,
                             highlightthickness=1, highlightbackground=C.border)
        self._cv.pack(fill="x")
        self._cv.bind("<Configure>", lambda e: _up.debounce(self._cv, "diag", 60, self._redraw))
        self._cv.bind("<Button-1>", self._on_click)

        # 进度行: 百分比纯文字 + 状态
        row = tk.Frame(self, bg=bg); row.pack(fill="x", pady=(6, 2))
        self._pct_lbl = tk.Label(row, text="0%", font=("Microsoft YaHei UI", 11, "bold"),
                                 fg=C.primary, bg=bg)
        self._pct_lbl.pack(side="left", padx=(2, 8))
        self._status_lbl = tk.Label(row, text="待检", font=("Microsoft YaHei UI", 9),
                                    fg=C.text_muted, bg=bg, anchor="w")
        self._status_lbl.pack(side="left", fill="x", expand=True)
        self.reset()

    # ── 绘制 (v3: 布局只建一次图元, 状态变化只改颜色, 不再 delete("all") 重建) ──
    def _redraw(self, e=None):
        """整表重排 (尺寸/条目变化时调用)"""
        try:
            self._cv.delete("all")
        except Exception:
            return
        w = max(2, self._cv.winfo_width())
        col_w = w / self._cols
        for idx, (key, label) in enumerate(self._items):
            col = idx % self._cols
            row = idx // self._cols
            x = col * col_w + 14
            y = row * self._row_h + 12
            color = self._state_color(self._nodes.get(key, {}).get("state", "pending"), key)
            dot = self._cv.create_oval(x, y - 5, x + 10, y + 5, fill=color, outline="")
            txt = self._cv.create_text(x + 18, y, text=label, anchor="w",
                                       font=("Microsoft YaHei UI", 9), fill=C.text_secondary)
            node = self._nodes.setdefault(key, {})
            node["dot"] = dot
            node["label"] = txt
            node["row"] = y
            node["col"] = x

    def _repaint(self):
        """只更新已存在图元的颜色 —— 25 项脉冲时从 50 次图元创建降到 25 次变色"""
        for key, node in self._nodes.items():
            dot = node.get("dot")
            if dot is None:
                self._redraw()
                return
            try:
                self._cv.itemconfig(dot, fill=self._state_color(node.get("state", "pending"), key))
            except Exception:
                pass

    def _state_color(self, state, key=None):
        if state == "ok":
            return C.success
        if state == "warn":
            return C.warning
        if state == "fail":
            return C.danger
        if state == "checking":
            return _lighten(C.warning, 30) if self._pulse_phase else C.warning
        return C.text_muted

    def _on_click(self, e):
        """点击节点显示详情"""
        try:
            for key, node in self._nodes.items():
                y = node.get("row", 0)
                x0 = node.get("col", 0)
                if abs(y - e.y) < 12 and x0 - 4 <= e.x <= x0 + 180:
                    detail = node.get("detail", "")
                    self._status_lbl.config(text=f"{dict(self._items).get(key, key)}: {detail or '无详情'}")
                    return
        except Exception:
            pass

    # ── 状态接口 ──
    def set_state(self, key, state, detail=""):
        node = self._nodes.setdefault(key, {})
        node["state"] = state
        node["detail"] = detail
        self._repaint()

    def set_progress(self, pct, text=""):
        try:
            self._pct_lbl.config(text=f"{int(pct)}%")
            if text:
                self._status_lbl.config(text=text)
        except Exception:
            pass

    def set_status(self, text):
        try:
            self._status_lbl.config(text=text)
        except Exception:
            pass

    def set_pct_color(self, color):
        try:
            self._pct_lbl.config(fg=color)
        except Exception:
            pass

    def reset(self):
        for key, _ in self._items:
            self._nodes.setdefault(key, {})["state"] = "pending"
            self._nodes[key]["detail"] = ""
        self.set_progress(0, "待检 — 点击节点查看详情")
        self._redraw()

    # ── 动画 (检测中节点脉冲) ──
    def start_animation(self):
        self._pulse_on = True
        self._tick()

    def stop_animation(self):
        self._pulse_on = False
        if self._after_id:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        self._repaint()

    def _tick(self):
        if not self._pulse_on:
            return
        try:
            if not self.winfo_exists():
                self._pulse_on = False
                return
            self._pulse_phase = not self._pulse_phase
            self._repaint()
            self._after_id = self.after(self._PULSE_STEP_MS, self._tick)
        except Exception:
            self._pulse_on = False


# ═══════════════════════ v2 新原语: 分段控件 + 状态点 ═══════════════════════

class SegmentedControl(tk.Frame):
    """单条分段控件 (注意力布局: 每屏一条, 激活=accent 实底, 其余中性).

    用法: seg = segmented(parent, ["配置", "素材", "需求"], index=0, command=lambda i: ...)
         seg.set(1); seg.get()
    性能: 事件一次绑定, set() 只重绘不重复 bind (避免切换积压)。
    """

    def __init__(self, parent, options, index=0, command=None,
                 bg=None, accent=None, height=30, font=None):
        super().__init__(parent, bg=bg or C.bg)
        self._opts = list(options)
        self._index = max(0, min(index, len(self._opts) - 1))
        self._command = command
        self._accent = accent or C.accent
        self._font = font or ("Microsoft YaHei UI", 10, "bold")
        self._segs = []
        for i, txt in enumerate(self._opts):
            s = tk.Label(self, text=f"  {txt}  ", font=self._font, padx=10, pady=6,
                         cursor="hand2", borderwidth=0, highlightthickness=0)
            s.pack(side="left", padx=(0, 6))
            s.bind("<Button-1>", lambda e, i=i: self.set(i, fire=True))
            s.bind("<Enter>", lambda e, i=i: self._hover(i, True))
            s.bind("<Leave>", lambda e, i=i: self._hover(i, False))
            self._segs.append(s)
        self._apply(animate_first=False)

    def _target(self, i):
        """当前状态下该段的 (底色, 字色, hover色)"""
        if i == self._index:
            return self._accent, C.bg, _darken(self._accent, 8)
        return self.cget("bg"), _lighten(self._accent, 88), C.nav_hover_bg

    def _hover(self, i, on):
        # ★★ 2026-09-23：**激活段不吃 hover**。
        #   为什么必须跳过：激活段的配色是 `accent 实底 + C.bg 近黑字`
        #   （实测 `bg=#38BDF8` / `fg=#0A0A0A`），而 hover 动画的目标是
        #   `nav_hover_bg=#161616`（**也近黑**）。一旦 hover 的补间把激活段底色写回深色、
        #   字色还是近黑 ⇒ **深字压深底 = 整块看不见** ——
        #   用户报的原话就是"子页面切换的时候对应的按键会彻底黑掉"。
        #   而且从交互上讲：**已经选中的那一段本来就不该再变 hover 色**。
        try:
            if i == self._index:
                return
            s = self._segs[i]
            base, fg, hover = self._target(i)
            try:
                animate(s, "bg", hover if on else base, ms=140)
            except Exception:
                s.configure(bg=hover if on else base, fg=fg)
        except Exception:
            pass

    def _apply(self, animate_first=True):
        for i, s in enumerate(self._segs):
            base, fg, _hover = self._target(i)
            try:
                s.configure(bg=base, fg=fg)
            except Exception:
                pass

    def set(self, i, fire=False):
        if not (0 <= i < len(self._segs)) or i == self._index:
            if 0 <= i < len(self._segs) and i == self._index:
                # 同 index 也强制刷新一次配色 (修复偶发"不变色": 动画中断所致)
                try:
                    self._apply(animate_first=False)
                except Exception:
                    pass
            return
        self._index = i
        # ★★ 2026-09-23：**先掐掉所有在飞的底色补间**，再配色。
        #   为什么：hover/leave 的补间是按"**旧 index** 算出来的目标色"在写帧的
        #   （实测目标 `nav_hover_bg=#161616`，而激活段应是 `#38BDF8`）。
        #   不取消的话，它会在 `_apply()` 之后继续写 140ms，把刚配好的颜色覆盖掉 ⇒
        #   激活段最后停在近黑底 + 近黑字 = "彻底黑掉"（用户报的就是这个）。
        try:
            from gui import motion_kit as _mk
            for s in self._segs:
                try:
                    # ⚠ `motion_kit.cancel(widget)` **只接一个参数**（它清掉该控件的
                    #   全部动画）。我第一版写成 `cancel(s, "bg")` ⇒ TypeError ⇒ 被
                    #   下面的 `except` 吞掉 ⇒ **取消根本没发生、修复看着像没生效**。
                    _mk.cancel(s)
                except Exception:                                # noqa: BLE001
                    pass
        except Exception:                                        # noqa: BLE001
            pass
        # 直接配色 (不用动画: 动画中断会留下未完成状态导致不变色/卡顿)
        self._apply(animate_first=False)
        self._settle()
        if fire and self._command:
            try:
                self._command(i)
            except Exception:
                pass

    def _settle(self, delay=220):
        """兜底：越过补间窗口后再刷一次配色。

        ★ 为什么留这一道：本控件这一类"颜色被动画写回去"的故障**反复出现过**
          （上面那句"偶发不变色"就是它）。`set()` 里已经做了 cancel + 跳过激活段，
          但只要有别的路径起过一次补间，颜色就还可能被拽走。
          延迟重刷一次的成本 = 一次 `configure`，换的是"绝不留下错的颜色"。
        """
        try:
            job = getattr(self, "_settle_job", None)
            if job:
                self.after_cancel(job)
        except Exception:                                        # noqa: BLE001
            pass
        try:
            self._settle_job = self.after(delay, self._apply)
        except Exception:                                        # noqa: BLE001
            pass

    def get(self):
        return self._index

    def bind_command(self, cmd):
        self._command = cmd


def segmented(parent, options, index=0, command=None, bg=None, accent=None, height=30):
    """分段控件工厂 (见 SegmentedControl)."""
    return SegmentedControl(parent, options, index=index, command=command,
                           bg=bg, accent=accent, height=height)


class StatusDot(tk.Canvas):
    """状态圆点: set(color) 换色; pulse_on() 呼吸 (仅运行中状态)."""

    def __init__(self, parent, color=None, size=8, bg=None):
        super().__init__(parent, width=size + 6, height=size + 6,
                         bg=bg or C.bg, highlightthickness=0)
        self._size = size
        self._color = color or C.text_muted
        self._pulsing = False
        self._after_id = None
        self._redraw()

    def _redraw(self):
        self.delete("all")
        s = self._size
        pad = 3
        self.create_oval(pad, pad, pad + s, pad + s, fill=self._color, outline="")

    def set(self, color):
        self._color = color
        self._redraw()

    def pulse_on(self, ms=800):
        if self._pulsing:
            return
        self._pulsing = True
        self._tick(ms)

    def pulse_off(self):
        self._pulsing = False
        try:
            if self._after_id:
                self.after_cancel(self._after_id)
        except Exception:
            pass
        self._after_id = None

    def _tick(self, ms):
        if not self._pulsing:
            return
        try:
            self._after_id = self.after(ms, lambda: self._tick(ms))
        except Exception:
            self._pulsing = False


def status_dot(parent, color=None, size=8, bg=None):
    """状态点工厂 (见 StatusDot)."""
    return StatusDot(parent, color=color, size=size, bg=bg)
