# -*- coding: utf-8 -*-
"""YOLO工作台 v0.10.2 — Codex 风格 SPA 主窗口 (简洁布局 + 动画 + 主题切换)"""
from __future__ import annotations
import os, sys, time, logging, json, threading, threading
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))


def get_project_root() -> Path:
    """当前项目根目录 (可被路径中心动态切换)"""
    return _PROJECT_ROOT


def set_project_root(root) -> None:
    """路径中心联动: 切换主窗口的根目录并同步 sys.path (幂等)"""
    global _PROJECT_ROOT
    _PROJECT_ROOT = Path(root).resolve()
    sp = str(_PROJECT_ROOT / "src")
    if sp not in sys.path:
        sys.path.insert(0, sp)


import tkinter as tk
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _HAS_DND = True
except ImportError:
    TkinterDnD = None; DND_FILES = None; _HAS_DND = False
from tkinter import ttk

from infrastructure.config import get_config, get_theme
from gui.theme_manager import theme_manager
from gui import ui_perf as _up
from gui import ui_tokens as TK

_cfg = get_config()
_log = logging.getLogger(__name__)

FONT = ("Microsoft YaHei UI", 11); FONT_SM = TK.TYPE["label"]
FONT_NAV = TK.TYPE["body"]
FONT_H1 = TK.TYPE["h1"]; FONT_H2 = TK.TYPE["h2"]

SIDEBAR_W = 236
NAV_H = 42
BRAND_H = 68

# 顶部导航 (分组排版; settings 固定在侧边栏底部)
NAVS = [
    # ★ 2026-10-01（怪怪）：H3 AI 视频 / 仿真 / 部署三条线**整体剥离出本仓**，
    #   本工作台只留 YOLO 主线。导航由 8 项收敛到 4 项（settings 固定在侧边栏底部）。
    #   ⚠️ 撤掉的是**导航入口**；对应页面文件已随三条线搬出，清单见
    #     `_migration/_yolo_split_manifest.json`（可反向搬回）。
    ("detection", "🎯", "检测", "检测"),
    ("dataset", "🗂", "数据集与标注", "标注与训练"),
    ("training", "🎓", "模型训练", "标注与训练"),
    ("env", "🔧", "环境配置", "系统"),
]


def _blend(c1, c2, t):
    """线性混合两个 hex 颜色"""
    def _v(s):
        return tuple(int(s.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    r = tuple(int(a + (b - a) * t) for a, b in zip(_v(c1), _v(c2)))
    return f"#{r[0]:02x}{r[1]:02x}{r[2]:02x}"


def _grad_bar(cv, c1, c2, c3):
    """画布横向三段渐变 (内容区顶部光条)。

    性能重写 (2026-09-11): 原实现逐列 create_line, 实测 1200px 宽 **38.2ms/次**,
    且往场景里塞 1200 个图元 —— 每次窗口重绘都要回放, 拖窗口/切页明显发糊。
    现改为「单张缓存位图」(ui_perf.gradient_image, 首次 ~6ms, 之后命中缓存 0ms),
    图元数 1200 -> 1, 视觉完全一致 (c1→c2→c3→c1 等距四停靠)。
    """
    try:
        w = cv.winfo_width(); h = max(1, cv.winfo_height())
        if w < 20:
            return
        # 位图路径: 1 个图元
        if _up.draw_gradient(cv, "gradbar", w, h, (c1, c2, c3, c1), orient="h"):
            return
        # 回退路径 (无 PIL): 分带绘制, 48 带视觉几乎无差, 比逐列快 16 倍
        cv.delete("all")
        bands = 48
        seg = max(1, w // 3)
        for k in range(bands):
            x = int(w * k / bands); x2 = int(w * (k + 1) / bands)
            t = (x % seg) / seg
            if x < seg:
                col = _blend(c1, c2, t)
            elif x < seg * 2:
                col = _blend(c2, c3, t)
            else:
                col = _blend(c3, c1, t)
            cv.create_rectangle(x, 0, x2, h, fill=col, outline="")
    except Exception:
        pass


# ── 品牌 Logo 位图缓存 (进程级, 只解一次盘) ──
_BRAND_LOGO: list = []


def _brand_logo_images():
    """返回 [42px, 47px] 两个 Logo PhotoImage (缓存)。

    原实现把 Image.open + LANCZOS 缩放放在 _draw_brand 内部, 而 _draw_brand
    绑在 <Configure> 上 —— 拖一次窗口就重复解盘+缩放几十次 (实测 15.3ms/次)。
    这里改为进程级缓存, 首次 ~15ms, 之后 0ms。
    """
    if _BRAND_LOGO:
        return _BRAND_LOGO
    try:
        from PIL import Image, ImageTk
        src = _PROJECT_ROOT / "assets" / "app_icon.png"
        if not src.exists():
            return []
        im = Image.open(str(src)).convert("RGBA")
        _BRAND_LOGO.extend([ImageTk.PhotoImage(im.resize((42, 42), Image.LANCZOS)),
                            ImageTk.PhotoImage(im.resize((47, 47), Image.LANCZOS))])
    except Exception:
        return []
    return _BRAND_LOGO


class App:
    """SPA 主窗口 — 左侧简洁导航 + 右侧内容区, 支持主题热切换"""

    def __init__(self, visible: bool = True):
        theme_manager.apply()  # 启动时按配置模式(浅色/深色/跟随时间)应用主题
        self.root = (TkinterDnD.Tk() if _HAS_DND else tk.Tk())
        try:
            from gui import dialog_kit
            dialog_kit.install()  # v2: 全局玻璃对话框 (替换原生 messagebox/simpledialog)
        except Exception:
            pass
        if not visible:
            self.root.withdraw()  # 启动动画期间隐藏, 构建完成后统一显示 (避免遮挡)
        self.root.title(f"{_cfg.name} v{_cfg.version}")
        try:
            _ico = _PROJECT_ROOT / "assets" / "app_icon.ico"
            if _ico.exists():
                self.root.iconbitmap(str(_ico))
        except Exception:
            pass
        # 窗口尺寸自适应 (2026-09-09 UI 整理): 小屏不溢出、大屏不空旷, 始终居中
        # ⚠ 2026-09-30: 尺寸存进 `_final_w/_final_h`, 供 `show(anchor_geom=…)` 做
        #   「先锚定到 splash 位置、再切到终态尺寸」—— 见 show() 的说明。
        #
        # ★★ 2026-10-03 修 (v0.34.1) —— **坐标单位跟着 DPI 觉察度走**:
        #   本程序现在启动即声明 `PER_MONITOR_DPI_AWARE` (见 infrastructure/dpi.py,
        #   真因是 mss 中途翻转觉察度导致切源时窗口缩一圈)。**副作用**: 
        #   `winfo_screenwidth()/winfo_screenheight()` 从此返回**物理像素**
        #   (本机 1890x1182 逻辑 -> 2560x1600 物理, 比 1.354)。
        #   原来的 `min(1440, ...)` / `min(900, ...)` 是按**逻辑像素**定的上限,
        #   直接沿用的话窗口只有 1440/2560 = 56% 屏宽 (比修复前的 76% 还窄),
        #   等于"修了一个坏观感、引入另一个坏观感"。
        #   ⇒ 上限改为**按屏幕比例**表达, 单位无关: 宽 76%、高 76%, 并保留
        #     与原来同量级的绝对下限/上限。实测 2560x1600 -> 1946x1216。
        try:
            _sw, _sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            _w = max(1024, min(int(_sw * 0.76), _sw - 40))
            _h = max(700, min(int(_sh * 0.76), _sh - 80))
            self._final_w, self._final_h = _w, _h
            self.root.geometry(f"{_w}x{_h}+{max(0, (_sw - _w) // 2)}+{max(0, (_sh - _h) // 2)}")
        except Exception:
            self._final_w, self._final_h = 1360, 840
            self.root.geometry("1360x840")
        self.root.minsize(1024, 700)
        self.root.configure(bg=get_theme().colors.bg)
        self._apply_ttk_theme()
        # 图标字体预热 (2026-09-11): 把 emoji 的字体回退搜索**提前付掉**。
        # 不预热的话, 这笔钱会以"每个新字符 ~13ms、每个新字号再来一次"的形式
        # 摊在用户第一次切页/第一次看某个页面的时候。
        #
        # ⚠ 放哪很关键 (踩过一次): 第一版放在这里**同步**跑, 结果是
        #   `App(visible=False)` 从 ~1.4s 涨到 2.9s, 总启动 10.3s ——
        #   启动画面(SPLASH)本身就是在这个主线程上 update() 驱动的, 同步预热
        #   把 splash 自己也卡住了, 观感反而更差。
        #   现在改成**空闲态再预热** (after 80ms + 分片): 外壳先画出来、splash
        #   先动起来, 预热让位给首屏渲染。预热总成本实测 ~1.5s, 但拆成 per-family
        #   的 9 小片后, 没有任何单片能卡住一帧。
        #   `YOLO_NO_FONT_WARM=1` 可整体关掉 (供 A/B 与排障)。
        if not os.environ.get("YOLO_NO_FONT_WARM"):
            try:
                self.root.after(80, self._warm_fonts_idle)
            except Exception:
                pass
        self._current_page = "detection"
        self._build_sidebar()
        self._build_content()
        self._build_statusbar()
        # 性能: 启动不预建全部页面 — 当前页由 show_page 懒建, 其余由 _prebuild_queue 空闲分片 (60ms/页)
        self._pages = {}
        self._prebuild_queue = self._prebuild_order()
        self.show_page(self._last_page(), animate=False)
        self.root.bind("<Escape>", lambda e: self.show_page("detection"))
        # v8 快捷键: Ctrl+K 命令面板 · Alt+1..8 直达页面
        self.root.bind("<Control-k>", self._open_palette)
        self.root.bind("<Control-K>", self._open_palette)
        for _i, _k in enumerate(self._nav_order):
            try:
                self.root.bind(f"<Alt-Key-{_i + 1}>",
                               lambda e, k=_k: self.show_page(k))
            except Exception:
                pass
        self.root.after(60000, self._check_auto_theme)
        self._warn_python_version()
        # 空闲分片预构建其余页面。首片故意延后到 900ms: 原实现 80ms 就开干,
        # 于是外壳刚 mapped 就被 0.1~1.2s 的页面构建压住, 用户第一眼看到的是
        # 一顿一顿的卡顿; 让外壳先完整画完、有近 1 秒的可交互窗口, 观感完全不同。
        self.root.after(900, self._start_prebuild)
        # 输入感应闸门 (2026-09-11): 页面预建是**同步**的 (单页 0.1~1.3s, 9 页合计
        # 约 2.9s), 期间事件循环被完全占住。原来只按固定节拍往下推, 用户拖窗口 /
        # 扫导航栏时正好撞上构建, 就是"一顿一顿"的观感。现在只要 1.2s 内有任何
        # 鼠标/键盘动作就让位, 卡顿只会落在用户真正空闲的时候。
        self._last_input_ts = 0.0
        for _seq in ("<Motion>", "<Button-1>", "<Button-3>", "<Key>", "<MouseWheel>"):
            try:
                self.root.bind_all(_seq, self._mark_input, add="+")
            except Exception:
                pass
        # 干净的退出流程 (原实现没有 WM_DELETE_WINDOW: 直接关窗会在后台线程
        # 仍在跑、after 回调仍挂着的时候销毁根窗口 —— Windows 上典型结局是
        # 进程 0xC0000005 段错误, 或者录像/摄像头句柄没释放导致下次打不开设备)
        try:
            self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        except Exception:
            pass

    # ── 退出 ──
    def _on_close(self):
        """关闭前先停定时器 / 动画, 再释放相机、录像、子进程, 最后销毁窗口。"""
        if getattr(self, "_closing", False):
            return
        self._closing = True
        # 1) 停掉所有 after 回调 (动画 / 去抖 / 轮询)
        try:
            from gui import motion_kit as _mk
            _mk.cancel_all()
        except Exception:
            pass
        try:
            _up.cancel_all_debounce()
        except Exception:
            pass
        # 2) 让各页面收起自己 (停录制、停预览线程)
        for p in list(getattr(self, "_pages", {}).values()):
            try:
                if hasattr(p, "on_hide"):
                    p.on_hide()
            except Exception:
                pass
        # 3) 释放相机 / VideoWriter / 子进程
        try:
            from infrastructure.resource_manager import get_resource_manager
            get_resource_manager().release_all()
        except Exception:
            pass
        try:
            from infrastructure.audit import audit as _a
            _a("app_close", "窗口关闭, 资源已释放", module="main_window")
        except Exception:
            pass
        # 4) 销毁
        try:
            self.root.destroy()
        except Exception:
            pass

    def _warn_python_version(self):
        """Python < 3.11 时在状态栏给出统一版本警告 (不影响程序运行)"""
        try:
            from infrastructure.environment import python_version_report
            ok, cur, req, tip = python_version_report()
            if not ok:
                _log.warning("Python %s < 要求 %s+ — %s", cur, req, tip.splitlines()[0] if tip else "")
                self.set_status(f"⚠️ Python {cur} 低于要求 {req}+，标注工具不可用；建议使用 Python 3.11+ 启动")
        except Exception:
            pass

    # ── ttk 主题 ──
    def _apply_ttk_theme(self):
        try:
            # ttkbootstrap 基座 (现代圆润 ttk 控件), 再叠加自定义色板
            try:
                import ttkbootstrap as _ttb
                _ttb.Style(theme="flatly" if theme_manager.resolved() == "light" else "darkly")
            except Exception:
                pass
            C = get_theme().colors
            style = ttk.Style(self.root)
            style.theme_use("clam")
            style.configure("TCombobox",
                            fieldbackground=C.input_bg,
                            background=C.input_bg,
                            foreground=C.text,
                            arrowcolor=C.text_secondary,
                            bordercolor=C.border,
                            lightcolor=C.border,
                            darkcolor=C.border,
                            selectbackground=C.primary,
                            selectforeground=C.text)
            style.map("TCombobox",
                      fieldbackground=[("readonly", C.input_bg)],
                      foreground=[("readonly", C.text)],
                      bordercolor=[("focus", C.glow)])
            style.configure("Vertical.TScrollbar", background=C.border,
                            troughcolor=C.input_bg, bordercolor=C.input_bg,
                            arrowcolor=C.text_muted)
            # 滚动条: 本机 `tk.Scrollbar` 由 Windows 原生绘制, 颜色选项全部无效,
            # 唯一生效路径是 `ttk.Scrollbar` + clam style (取证见 components.SB_STYLE
            # 上方注释)。这里主动注册一次, 保证"在任何页面建控件之前"就已就位;
            # 主题切换时 `_apply_ttk_theme` 会被再调一次, 顺带刷新调色板。
            try:
                from gui.components import _ensure_sb_style
                _ensure_sb_style(self.root)
            except Exception as e:
                _log.warning("深色滚动条 style 注册失败: %s", e)
        except Exception as e:
            _log.warning("ttk theme apply failed: %s", e)

    # ── 侧边栏 ──
    def _build_sidebar(self):
        C = get_theme().colors
        side = tk.Frame(self.root, bg=C.sidebar_bg, width=SIDEBAR_W)
        side.pack(side="left", fill="y"); side.pack_propagate(False)
        self._sidebar = side
        # 右侧 1px 描边: 导航面 (sidebar_bg) 与画布 (bg) 明度接近时, 靠这条线
        # 明确"侧栏到哪里结束" —— 旧版纯白侧栏贴纯白画布, 边界完全消失。
        tk.Frame(side, bg=C.border, width=1).pack(side="right", fill="y")

        # 品牌区 v3: 玻璃渐变横条 + Logo + 名称 + 版本芯片
        brand = tk.Canvas(side, height=BRAND_H, bg=C.sidebar_bg, highlightthickness=0)
        brand.pack(fill="x")
        brand.bind("<Configure>",
                   lambda e: _up.debounce(brand, "brand", 60, lambda: self._draw_brand(brand)))
        self._brand = brand
        self._logo_cv = brand
        self._logo_item = None
        self._logo_imgs = []
        tk.Frame(side, height=1, bg=C.border).pack(fill="x", padx=TK.SPACE["md"])

        # 导航项 (分组排版)
        self._nav_buttons = {}
        last_group = None
        first_of_group = True
        for key, icon, label, group in NAVS:
            if group != last_group:
                g = tk.Frame(side, bg=C.sidebar_bg)
                g.pack(fill="x", padx=TK.SPACE["xl"], pady=(TK.SPACE["md"], TK.SPACE["xxs"]))
                tk.Label(g, text=group, font=TK.TYPE["label_bold"],
                         bg=C.sidebar_bg, fg=C.text_muted).pack(anchor="w")
                last_group = group
                first_of_group = True
            item = self._make_nav_item(side, icon, label, key)
            item.pack(fill="x", padx=TK.SPACE["md"],
                      pady=(TK.SPACE["xs"] if first_of_group else TK.SPACE["xxs"], 0))
            first_of_group = False
            self._nav_buttons[key] = item

        tk.Frame(side, bg=C.sidebar_bg).pack(expand=True)

        # 底部: 设置入口
        tk.Frame(side, height=1, bg=C.border).pack(fill="x", padx=TK.SPACE["md"])
        item = self._make_nav_item(side, "⚙️", "设置", "settings")
        item.pack(fill="x", padx=TK.SPACE["md"], pady=(TK.SPACE["sm"], TK.SPACE["lg"]))
        self._nav_buttons["settings"] = item
        self._refresh_nav()

    @staticmethod
    def _blend_hex(c1, c2, t):
        def _v(s): return tuple(int(s.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
        r = tuple(int(a + (b - a) * t) for a, b in zip(_v(c1), _v(c2)))
        return f"#{r[0]:02x}{r[1]:02x}{r[2]:02x}"

    def _draw_brand(self, cv):
        C = get_theme().colors
        w, h = cv.winfo_width(), BRAND_H
        if w < 40:
            return
        cv.delete("all")
        # 背景: 单图元缓存位图 (原为 64 条 create_rectangle)
        if not _up.draw_gradient(cv, "brandbg", w, h, (C.sidebar_bg, C.bg2), orient="v"):
            cv.create_rectangle(0, 0, w, h, fill=C.sidebar_bg, outline="")
        cv.create_line(0, h - 1, w, h - 1, fill=C.border)
        # Logo: 位图缓存 + 呼吸动画只挂一次
        # (原实现每次 <Configure> 都 Image.open + 2xLANCZOS 解图 = 15.3ms,
        #  且每次都 after(700, _logo_pulse) —— 拖窗口 20 次就有 20 条呼吸定时器在抢图)
        imgs = _brand_logo_images()
        if imgs:
            self._logo_imgs = imgs
            self._logo_item = cv.create_image(32, h // 2, image=imgs[0])
            self._logo_pulse_state = 0
            if not getattr(self, "_logo_pulse_running", False):
                self._logo_pulse_running = True
                self.after(700, self._logo_pulse)
        else:
            cv.create_oval(8, 14, 52, 58, fill=C.primary, outline=C.glow, width=2)
        cv.create_text(64, h // 2 - 9, text=_cfg.name, font=TK.TYPE["h2"],
                       fill=C.text, anchor="w")
        # 版本芯片: 圆角 + 等宽小字 (原本是直角矩形 + 7pt 日期, 太小)
        chip = f"v{_cfg.version}"
        cw = 14 + len(chip) * 6
        self._round_pill(cv, 64, h // 2 + 4, 64 + cw, h // 2 + 22, r=9,
                         fill=_up.safe_bg(cv, C.bg2), outline=C.border)
        cv.create_text(64 + cw / 2, h // 2 + 13, text=chip,
                       font=TK.TYPE["mono_sm"], fill=C.text_secondary, anchor="c")
    @staticmethod
    def _round_pill(cv, x1, y1, x2, y2, r=10, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
               x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return cv.create_polygon(pts, smooth=True, **kw)

    def _draw_nav(self, item, force=False):
        """重绘单个导航项。

        性能重写 (2026-09-11): 原实现活跃项要画 40 个渐变分带 + 多边形 + 辉光描边,
        单项 ~46 个图元; 而 _refresh_nav 一次就重画全部 9 项 = ~450 个图元被删除重建,
        切页/鼠标扫过导航栏时肉眼可见顿挫。
        现在: 活跃胶囊改为**单张缓存圆角位图**(1 个图元), 并加状态记忆 —— 内容没变
        直接 return, 于是 _refresh_nav 全量调用也只是一次廉价比对。9 项合计 46→约 30 个图元。
        """
        C = get_theme().colors
        w = item.winfo_width()
        h = NAV_H
        if w < 10:
            # 重建/未布局时自愈重绘, 避免遗留旧高亮 (nav 变色异常根因防御)
            try:
                item.after(40, lambda: self._draw_nav(item))
            except Exception:
                pass
            return
        active = item._key == self._current_page
        # 状态记忆: 切页时 _refresh_nav 会把 9 项全过一遍, 没变就不重画
        state = (active, round(float(item._hover), 2), w, C.accent, C.purple,
                 C.sidebar_bg, C.nav_hover_bg, C.text_secondary)
        if not force and getattr(item, "_nav_state", None) == state:
            return
        item._nav_state = state
        item.delete("all")
        x0, y0, x1, y1 = 4, 2, w - 4, h - 2
        if active:
            # 渐变胶囊 (accent→紫) + 辉光描边 —— 单张缓存位图, 1 个图元
            img = _up.rounded_image(max(2, int(x1 - x0)), max(2, int(y1 - y0)),
                                    TK.RADIUS["md"],
                                    (C.accent, C.purple), orient="h", outline=C.glow,
                                    owner=item)
            if img is not None:
                item.create_image(x0, y0, image=img, anchor="nw")
                item._nav_img_ref = img          # 防 GC
            else:
                self._round_pill(item, x0, y0, x1, y1, r=TK.RADIUS["md"],
                                 fill=C.accent, outline=C.glow)
            fg = "#FFFFFF"
            item.create_line(w - 22, h // 2 - 5, w - 14, h // 2, fill="#FFFFFF", width=2)
            item.create_line(w - 14, h // 2, w - 22, h // 2 + 5, fill="#FFFFFF", width=2)
        else:
            bg = _blend(C.sidebar_bg, C.nav_hover_bg, min(1.0, item._hover)) if item._hover > 0 else C.sidebar_bg
            fg = C.text if item._hover > 0 else C.text_secondary
            self._round_pill(item, x0, y0, x1, y1, r=TK.RADIUS["md"], fill=bg, outline="")
        # 图标底衬
        cy = h // 2
        item.create_oval(14, cy - 13, 40, cy + 13,
                         fill=C.nav_active_bg if active else C.sidebar_hover, outline="")
        item.create_text(27, cy, text=item._icon, font=TK.ICON["md"],
                         fill=fg if not active else C.accent, anchor="c")
        item.create_text(50, cy, text=item._label, font=FONT_NAV, fill=fg, anchor="w")

    def _make_nav_item(self, parent, icon, label, key):
        C = get_theme().colors
        item = tk.Canvas(parent, bg=C.sidebar_bg, highlightthickness=0,
                         cursor="hand2", height=NAV_H)
        item._key = key; item._hover_job = None; item._hover = 0.0
        item._icon = icon; item._label = label; item._nav_state = None
        item.bind("<Enter>", lambda e: self._nav_enter(key))
        item.bind("<Leave>", lambda e: self._nav_leave(key))
        item.bind("<Button-1>", lambda e, k=key: self.show_page(k))
        item.bind("<Configure>", lambda e: self._draw_nav(item, force=True))
        return item
    def _nav_enter(self, key):
        item = self._nav_buttons.get(key)
        if not item:
            return
        if item._hover_job:
            try: self.root.after_cancel(item._hover_job)
            except Exception: pass

        def _step(k=0):
            if not item.winfo_exists(): return
            item._hover = min(1.0, (k + 1) / 4.0)
            self._draw_nav(item)
            if k < 3:
                item._hover_job = self.root.after(28, lambda: _step(k + 1))
            else:
                item._hover_job = None
        _step(0)
    def _nav_leave(self, key):
        item = self._nav_buttons.get(key)
        if not item:
            return
        if item._hover_job:
            try: self.root.after_cancel(item._hover_job)
            except Exception: pass
            item._hover_job = None
        item._hover = 0.0
        try:
            self._draw_nav(item)   # 只重绘该导航项 (原全量刷新导致切页抖动)
        except Exception:
            pass
    def _logo_pulse(self):
        """Logo 呼吸动画: 46px ↔ 51px 缓慢切换"""
        try:
            if not self.root.winfo_exists() or len(self._logo_imgs) < 2 or self._logo_item is None:
                return
            self._logo_pulse_state = getattr(self, "_logo_pulse_state", 0) ^ 1
            self._logo_cv.itemconfigure(self._logo_item, image=self._logo_imgs[self._logo_pulse_state])
            self.after(800, self._logo_pulse)
        except Exception:
            pass

    def _build_content(self):
        C = get_theme().colors
        # 内容区底色改用**平铺色**(bg) 代替整窗渐变画布。
        # 原因: 页面宿主 _page_host 几乎铺满内容区 (只露 12px 边), 而旧实现仍要
        # 按窗口尺寸渲染一张 ~1130x800 的渐变位图 —— 拖窗口时每 60ms 重渲 ~90 万
        # 像素, 却只有一圈 12px 的边看得见。现在零渲染成本, 卡片衬底更干净。
        self._content = tk.Frame(self.root, bg=C.bg)
        self._content.pack(side="left", fill="both", expand=True)
        # 页面宿主面板: 内缩留出衬底, 形成卡片式内容区 (白卡 on 冷灰蓝画布)
        self._page_host = tk.Frame(self._content, bg=C.card_bg,
                                   highlightbackground=C.border, highlightthickness=1)
        self._page_host.place(relx=0, rely=0, relwidth=1, relheight=1,
                              x=TK.SPACE["md"], y=TK.SPACE["md"],
                              width=-2 * TK.SPACE["md"], height=-2 * TK.SPACE["md"])
        self._page_host.grid_rowconfigure(0, weight=1)
        self._page_host.grid_columnconfigure(0, weight=1)
        # 顶部强调光条 (液态玻璃细节; 单张缓存位图, 1 个图元)
        accent = tk.Canvas(self._page_host, height=2, bg=C.card_bg, highlightthickness=0)
        accent.place(relx=0, rely=0, relwidth=1)
        accent.bind("<Configure>",
                    lambda e: _up.debounce(accent, "accent", 80,
                                           lambda: _grad_bar(accent, C.accent, C.glow, C.purple)))
        self._host_accent = accent
    def _build_statusbar(self):
        C = get_theme().colors
        bar = tk.Frame(self.root, bg=C.sidebar_bg, height=30,
                       highlightbackground=C.border, highlightthickness=1)
        bar.pack(side="bottom", fill="x"); bar.pack_propagate(False)
        self._statusbar = bar
        self._status_lbl = tk.Label(bar, text="就绪", font=TK.TYPE["label"], bg=C.sidebar_bg,
                                    fg=C.text_secondary, anchor="w")
        self._status_lbl.pack(side="left", padx=TK.SPACE["sm"], fill="x", expand=True)
        # 右侧分段: 主题 | 时间 | 版本 (等宽, 细分隔线)
        tk.Frame(bar, width=1, bg=C.border, height=16).pack(side="right", padx=TK.SPACE["xs"])
        self._ver_lbl = tk.Label(bar, text=f"v{_cfg.version}", font=TK.TYPE["mono_sm"],
                                 bg=C.sidebar_bg, fg=C.text_secondary)
        self._ver_lbl.pack(side="right", padx=TK.SPACE["md"])
        tk.Frame(bar, width=1, bg=C.border, height=16).pack(side="right")
        self._time_lbl = tk.Label(bar, text="", font=TK.TYPE["mono_sm"],
                                  bg=C.sidebar_bg, fg=C.text_muted)
        self._time_lbl.pack(side="right", padx=TK.SPACE["sm"])
        tk.Frame(bar, width=1, bg=C.border, height=16).pack(side="right")
        # ⚠ 这里不能用 mono_sm (Consolas) —— 本标签的内容是 "☀️ 浅色"/"🌙 深色",
        #   等宽对非数字文本没有意义, 而 Consolas 没有这两个 emoji 的字形, 首绘要走
        #   字体回退。实测 (runs/_theme_font_cost.out): Consolas 9 是 505.4 ms,
        #   换 UI 字体 362.4 ms, 纯中文那段也从 62.0 降到 47.5 ms。
        #   (右边那个时钟标签是纯数字, 继续用 mono_sm 是对的。)
        self._theme_lbl = tk.Label(bar, text="", font=TK.TYPE["label"],
                                   bg=C.sidebar_bg, fg=C.text_muted)
        self._theme_lbl.pack(side="right", padx=TK.SPACE["sm"])
        self._update_time()
    def _update_time(self):
        try:
            if not self.root.winfo_exists(): return
        except Exception:
            return
        # 只在文本真的变了才 configure —— 每秒无条件改两个 Label 会白白触发重绘
        txt = time.strftime("%Y-%m-%d %H:%M:%S")
        if txt != getattr(self, "_last_clock_txt", None):
            self._last_clock_txt = txt
            self._time_lbl.config(text=txt)
        theme_txt = "☀️ 浅色" if theme_manager.resolved() == "light" else "🌙 深色"
        if theme_txt != getattr(self, "_last_theme_txt", None):
            self._last_theme_txt = theme_txt
            self._theme_lbl.config(text=theme_txt)
        self.root.after(1000, self._update_time)

    # ── 页面懒加载 (启动提速: 首屏仅构建当前页, 其余按需构建) ──
    _PAGE_CLASS_CACHE = {}
    _PAGE_SPEC = {
        # ★ 2026-10-01 剥离后回到 5 页。**检测页重新挂回导航**：
        #   2026-09-30 它曾被并进「交付中心」，而交付中心整页随仿真/部署三条线
        #   一起搬出了本仓 ⇒ 检测必须在场（它是 YOLO 工作台的主功能之一）。
        "detection": ("gui.pages.detection_page", "DetectionPage"),
        "dataset": ("gui.pages.dataset_page", "DatasetPage"),
        "training": ("gui.pages.training_page", "TrainingPage"),
        "env": ("gui.pages.environment_page", "EnvironmentPage"),
        "settings": ("gui.pages.settings_page", "SettingsPage"),
    }
    # ★★ 2026-10-01：H3 / 仿真 / 部署三条线搬出本仓后，下面这些旧 key 都**没有对应页面**了。
    #   它们仍可能来自 `last_page.json` 的恢复、命令面板、或页面之间的跳转 ——
    #   **一律转发到语义最近的现存页面，不许静默什么都不做**
    #   （静默 = 点了没反应，会被当成"启动卡住了"）。
    _LEGACY_SUB = {
        # 数据生成 / 抽帧 → 数据集
        "h3factory": "dataset", "framextract": "dataset",
        # 交付中心（容器页）及其全部子页 / 仿真 / 部署 → 检测
        "delivery": "detection", "mech": "detection", "mech3d": "detection",
        "lib": "detection", "deploy": "detection", "control": "detection",
        "board": "detection", "mechlib": "detection",
        # 局域网分享 → 设置
        "share": "settings",
    }
    _nav_order = ["detection", "dataset", "training",
                  "env", "settings"]

    def _open_palette(self, e=None):
        """⌘K 命令面板"""
        try:
            from gui.command_palette import CommandPalette
            CommandPalette(self).lift()
        except Exception:
            pass
        return "break"

    def _page_class(self, key):
        if key in self._PAGE_CLASS_CACHE:
            return self._PAGE_CLASS_CACHE[key]
        spec = self._PAGE_SPEC.get(key)
        if not spec:
            return None
        try:
            import importlib
            m = importlib.import_module(spec[0])
            c = getattr(m, spec[1])
        except Exception:
            c = None
        self._PAGE_CLASS_CACHE[key] = c
        return c

    def _ensure_page(self, key):
        page = self._pages.get(key)
        if page is not None:
            return page
        cls = self._page_class(key)
        if cls is None:
            return None
        page = cls(self._page_host, controller=self)
        page.grid(row=0, column=0, sticky="nsew")
        # 关键性能点: 建好后立刻 grid_remove (不映射), 而不是 lower() (映射但压在底层)。
        # 旧实现 9 个页面全部处于映射状态, 于是**每次窗口缩放 9 个页面都要重排重绘**,
        # 实测换一个尺寸 ~1.0s。改成只映射当前页后, 隐藏页完全不参与几何协商。
        try:
            page.grid_remove()
        except Exception:
            try:
                page.lower()
            except Exception:
                pass
        try:
            page._is_active = False   # 默认后台; show_page 才置 True
        except Exception:
            pass
        self._pages[key] = page
        return page

    def _preload_pages(self):
        """重建页面: 启动时预建【全部】页面 (切换零等待); 单页失败给占位不挡启动"""
        if hasattr(self, "_pages"):
            for p in self._pages.values():
                try:
                    if hasattr(p, "on_hide"):
                        p.on_hide()
                except Exception:
                    pass
                try: p.destroy()
                except Exception: pass
        self._pages = {}
        self._raised_page = None    # 旧页面已销毁, 清掉"当前抬起页"引用
        for key in self._PAGE_SPEC:
            try:
                self._ensure_page(key)
            except Exception as e:
                try:
                    ph = tk.Frame(self._page_host, bg=get_theme().colors.bg2)
                    ph.grid(row=0, column=0, sticky="nsew")
                    tk.Label(ph, text=f"⚠️ 页面加载失败: {key}\n{str(e)[:120]}",
                             bg=ph.cget("bg"), fg=get_theme().colors.warning,
                             font=("Microsoft YaHei UI", 10), justify="center").pack(expand=True)
                    ph.grid_remove()
                    self._pages[key] = ph
                except Exception:
                    pass

    # ── 页面切换 (带滑入动画) ──
    def show_page(self, key, animate=True):
        # ★★ 旧 key 重定向（2026-09-22）：原「交付」组的 5 个页面已并进「交付中心」的
        #   子页。老 key（`mech`/`deploy`/`control`/`board`/`mechlib`）可能来自
        #   `last_page.json` 的恢复、命令面板、页面之间的跳转 —— **一律转成
        #   「切到交付中心 + 打开对应子页」**，不许静默什么都不做。
        # ★ 2026-10-01：旧 key 转发（表见 `_LEGACY_SUB`）。原实现是转发到
        #   「交付中心 + 子页」，而交付中心整页已随仿真/部署搬出本仓 ⇒ 直接转发到目标页。
        _dst = self._LEGACY_SUB.get(key)
        if _dst is not None and _dst != key:
            self.show_page(_dst, animate=animate)
            return
        # 兜底：既不在 _PAGE_SPEC 也不在 _LEGACY_SUB 的 key（旧配置 / 手滑 / 将来改名）
        #   —— **不许静默返回**（静默会被当成"启动卡住了"），落到首屏并记账。
        if key not in self._PAGE_SPEC:
            _log.warning("show_page: 未知页面 key=%r → 回退到 %s", key, self._nav_order[0])
            key = self._nav_order[0]
        old = self._pages.get(self._current_page) if hasattr(self, "_current_page") else None
        if key not in self._pages:
            try:
                if self._ensure_page(key) is None:
                    return
            except Exception as e:
                _log.warning("page build %s failed: %s", key, e)
                self._pages[key] = self._page_fallback(key, str(e)[:120])
        self._current_page = key
        self._last_switch_ts = time.time()
        try:
            from infrastructure.audit import audit as _a
            _a("page_switch", f"切换到 {key}", module="main_window")
        except Exception:
            pass
        page = self._pages[key]
        if old is not None and old is not page:
            try:
                if hasattr(old, "on_hide"):
                    old.on_hide()
            except Exception as e:
                _log.warning("on_hide %s: %s", key, e)
        # 性能: 只放下"上一个被抬起的页", 不再对全部 9 页挨个 lower()。
        # 用 grid_remove (取消映射) 而不是 lower (堆叠在底层): 隐藏页脱离几何协商,
        # 窗口缩放时只重排当前这一页。
        prev = getattr(self, "_raised_page", None)
        if prev is not None and prev is not page:
            try:
                prev.grid_remove()
            except Exception:
                try:
                    prev.lower()
                except Exception:
                    pass
        try:
            page.grid()
        except Exception:
            pass
        # 标记活动页: 各页的队列轮询据此升降档 (后台页不再每秒醒 7 次)
        try:
            from gui import poll_kit as _pk
            _pk.mark_active(page, True)
            if prev is not None and prev is not page:
                _pk.mark_active(prev, False)
        except Exception:
            try:
                page._is_active = True
                if prev is not None and prev is not page:
                    prev._is_active = False
            except Exception:
                pass
        page.tkraise()
        self._raised_page = page
        if animate:
            self._animate_in(page)
        if hasattr(page, "on_show"):
            try: page.on_show()
            except Exception as e: _log.warning("on_show %s: %s", key, e)
        try:
            import json as _json
            _lp = _json.loads((_PROJECT_ROOT / "runs" / "last_page.json").read_text(encoding="utf-8"))
            if _lp.get("step") is not None and hasattr(page, "_show_step"):
                page._show_step(int(_lp["step"]))
        except Exception:
            pass
        self._refresh_nav()
        # 首次显示时强制刷一次布局 (让首屏立刻成型); 之后不再阻塞式 update_idletasks,
        # 否则每次切页都要把整页几何重算一遍 (大页面 100~1000ms 顿挫的主要来源)。
        if not getattr(self, "_first_page_shown", False):
            self._first_page_shown = True
            try:
                self.root.update_idletasks()
            except Exception:
                pass
        self._save_page(key)

    def _page_fallback(self, key, msg):
        """页面构建失败兜底: 展示错误占位, 绝不留空白"""
        try:
            ph = tk.Frame(self._page_host, bg=get_theme().colors.bg2)
            ph.grid(row=0, column=0, sticky="nsew")
            tk.Label(ph, text=f"⚠️ 页面加载失败: {key}\n{msg}",
                     bg=ph.cget("bg"), fg=get_theme().colors.warning,
                     font=("Microsoft YaHei UI", 10), justify="center").pack(expand=True)
            ph.grid_remove()
            return ph
        except Exception:
            return None

    def _animate_in(self, page):
        # 即时显示: 堆叠页面 tkraise, 无滑动动画
        try:
            page.tkraise()
        except Exception:
            pass

    def _refresh_nav(self, _retry=0):
        """去重重绘 (切页/悬浮/自愈合并为一次), 避免 9 个画布逐列重绘风暴"""
        job = getattr(self, "_nav_job", None)
        if job:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass

        def _do():
            self._nav_job = None
            for k, item in self._nav_buttons.items():
                try:
                    item._hover = 0.0
                    self._draw_nav(item)
                except Exception:
                    pass

        try:
            self._nav_job = self.root.after(40, _do)
        except Exception:
            _do()
        # 宽度未就绪时自愈重试 (最多3轮; 就绪后不再重复排程)
        if _retry < 3:
            try:
                if any(i.winfo_width() < 10 for i in self._nav_buttons.values()):
                    self.root.after(90, lambda: self._refresh_nav(_retry + 1))
            except Exception:
                pass

    # ── 主题切换 ──
    def apply_theme(self, mode=None):
        """切换主题并整窗重建 (浅色/深色/跟随系统时间)"""
        resolved = theme_manager.apply(mode)
        C = get_theme().colors
        # 主题换了 → 全部派生位图缓存失效 (否则会拿旧色渐变糊在新主题上)
        try:
            _up.invalidate()
        except Exception:
            pass
        try:
            from gui import motion_kit as _mk
            _mk.cancel_all()
        except Exception:
            pass
        try:
            _up.cancel_all_debounce()
        except Exception:
            pass
        self.root.configure(bg=C.bg)
        for w in (self._sidebar, self._content, self._statusbar):
            try: w.destroy()
            except Exception: pass
        self._apply_ttk_theme()
        self._build_sidebar()
        self._build_content()
        self._build_statusbar()
        self._preload_pages()
        self.show_page(self._current_page, animate=False)
        self._prebuild_queue = self._prebuild_order()
        self.root.after(300, self._start_prebuild)
        self.notify("已应用" + ("浅色" if resolved == "light" else "深色") + "主题")
        try:
            self._status_lbl.config(text="✅ 主题已切换为" + ("浅色" if resolved == "light" else "深色"))
        except Exception:
            pass

    def _check_auto_theme(self):
        try:
            if not self.root.winfo_exists(): return
        except Exception:
            return
        if theme_manager.get_mode() == "auto":
            now_resolved = theme_manager.resolve_mode()
            if now_resolved != theme_manager.resolved():
                self.apply_theme()
        self.root.after(60000, self._check_auto_theme)

    # ── 记忆页面 ──
    def _last_page(self):
        try:
            cp = _PROJECT_ROOT / "runs" / "last_page.json"
            if cp.exists():
                return json.loads(cp.read_text(encoding="utf-8")).get("page", "detection")
        except Exception:
            pass
        return "detection"

    def _save_page(self, key):
        """记住当前页 —— 后台线程写盘。

        原实现每次切页都在主线程同步 write_text (慢盘上 1~5ms, 偶发抖动)。
        """
        self._current_page_memo = key

        def _w():
            try:
                (_PROJECT_ROOT / "runs").mkdir(exist_ok=True)
                (_PROJECT_ROOT / "runs" / "last_page.json").write_text(
                    json.dumps({"page": key}), encoding="utf-8")
            except Exception:
                pass

        try:
            threading.Thread(target=_w, daemon=True, name="save-page").start()
        except Exception:
            _w()

    def set_status(self, msg):
        try: self._status_lbl.config(text=msg)
        except Exception: pass

    def notify(self, msg, kind="info", duration=2600):
        """右上角轻提示 (v2: 复用同一条 toast, 滑入用 motion_kit 全局时钟)。

        旧实现每次 notify 都 new 一个 tk.Label —— 连续提示会堆一串控件,
        且滑入动画自己开一条 after 链 (13 帧 × 独立定时器)。
        """
        C = get_theme().colors
        bg = {"success": C.success, "error": C.danger, "info": C.primary}.get(kind, C.primary)
        toast = getattr(self, "_toast", None)
        try:
            if toast is None or not toast.winfo_exists():
                toast = tk.Label(self.root, text=msg, font=FONT_SM, bg=bg, fg="#FFFFFF",
                                 padx=TK.SPACE["lg"], pady=TK.SPACE["sm"])
                self._toast = toast
            else:
                toast.config(text=msg, bg=bg)
            # 2026-09-11: 从"右上角压在页眉上"改到**右下角、状态栏之上**。
            # 页眉右侧正是"打开目录/协同打标/导出数据"这些按钮的位置, toast 压
            # 上去既挡点击又像排版事故。
            toast.place(relx=1.0, rely=1.0, anchor="se",
                        x=52, y=-(TK.CONTROL_H + TK.SPACE["lg"]))
            toast.lift()
        except Exception:
            return
        # 滑入: 单次插值动画 (从右侧 52px 滑到 0)
        st = {"p": 0.0}

        def _step():
            st["p"] = min(1.0, st["p"] + 0.16)
            e = 1 - (1 - st["p"]) ** 3
            try:
                toast.place_configure(x=int(52 * (1 - e)))
            except Exception:
                return
            if st["p"] < 1.0:
                self.root.after(16, _step)

        _step()
        job = getattr(self, "_toast_job", None)
        if job:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        try:
            self._toast_job = self.root.after(duration, self._kill_toast)
        except Exception:
            pass

    def _kill_toast(self, toast=None):
        t = toast or getattr(self, "_toast", None)
        self._toast_job = None
        try:
            if t is not None and t.winfo_exists():
                t.place_forget()
        except Exception:
            pass



    def _prebuild_order(self):
        """重型页面优先预建 (实测首建: training ~5.2s/deploy ~1.5s), 用户首触即热."""
        # 2026-09-11 修: 原表的键是 "developer", 而真实页面键是 "develop"
        # —— 从未命中, 板卡页一直排在 99。改名 board 后一并校正。
        # 2026-09-22 补 "control": 全页审计（tools/qc/audit_ui_all_pages.py）的
        # 「登记五处」抓到它不在表里 —— 漏一处不会报错, 只是预建顺序默默退到 99。
        # 2026-10-01 剥离后只剩 5 页。检测页要开摄像头 / 加载模型，首建偏重 ⇒
        #   排在训练之后、其余之前。
        _heavy = {"dataset": 0, "training": 1, "detection": 2,
                  "settings": 6, "env": 8}
        return sorted((k for k in self._PAGE_SPEC if k not in self._pages),
                      key=lambda k: _heavy.get(k, 99))

    def _mark_input(self, _e=None):
        """记录最近一次用户输入时间 (预建闸门用)。开销 = 一次属性赋值。"""
        self._last_input_ts = time.time()

    def _start_prebuild(self):
        """启动后空闲分片预构建其余页面 (消除切换卡顿); 用户有动作时让位"""
        if not hasattr(self, "_prebuild_queue"):
            self._prebuild_queue = [k for k in self._PAGE_SPEC if k not in self._pages]
        if not self._prebuild_queue:
            self._kick_warmup()
            return
        backoff = getattr(self, "_last_switch_ts", 0)
        if time.time() - backoff < 0.6:
            try:
                self.root.after(300, self._start_prebuild)
            except Exception:
                pass
            return
        # 输入闸门: 1.2s 内有鼠标/键盘动作就先不建 (见 __init__ 里的说明)
        if time.time() - getattr(self, "_last_input_ts", 0) < 1.2:
            try:
                self.root.after(250, self._start_prebuild)
            except Exception:
                pass
            return
        key = self._prebuild_queue.pop(0)
        # 先把积压的**重绘/几何**事件冲掉, 再干这一票重的。
        # 实测(2026-09-11): 预建 9 页合计约 2.9s, 单页 0.1~1.2s (training 630ms /
        # h3factory 1240ms 最重)。这段时间整个事件循环被占住, 屏幕上一个新像素
        # 都不会出 —— 用户看到的就是"外壳出来了但界面一顿一顿的"。
        # 每次动手前 update_idletasks, 保证上一页画完再开下一页。
        try:
            self.root.update_idletasks()
        except Exception:
            pass
        try:
            self._ensure_page(key)
        except Exception:
            pass
        try:
            self.root.after(60, self._start_prebuild)
        except Exception:
            pass

    # ── 图标字体预热 (自适应分片, 不阻塞首屏) ──
    # 字符块大小**不写死**: 本机可用内存在 1~2GB 之间抖, 同一个片在内存紧张时能
    # 慢 5~10 倍。定长切片等于把粒度钉死在"最理想的那次实测"上 —— 一遇到压力
    # 就重新长出百毫秒尖峰。所以按实测单片耗时自己调 (见 _warm_fonts_idle)。
    # 初始块从 12 收到 6: 实测"首次遇到某个字形"要 10~30 ms/字, 12 字一片在
    # 有竞争时能到 401 ms (看门狗实测), 6 字一片把最坏单片直接砍半。
    _FONT_WARM_STEP0 = 6
    _FONT_WARM_STEP_MIN = 2
    _FONT_WARM_STEP_MAX = 12
    _FONT_WARM_TARGET = 45.0     # 单片耗时上限 (ms); 超过就缩块
    # 累计预算**收到 3s**: 见 _font_warm_plan 的"为什么只留两个规格"。
    # 广谱预热在内存吃紧时是净亏 —— 预热一个字形要 50ms, 而它防住的冷渲染是
    # 200~550ms, 看着"值", 但 24 个规格 × 65 字形 = 1560 对, 就算每对只付
    # 50ms 也要 78 秒的主线程时间, 全花在用户根本不会看到的字形上。
    _FONT_WARM_BUDGET_MS = 3000.0

    def _warm_fonts_idle(self):
        """在空闲态把图标字体的回退搜索**提前付掉**, 一片一片来。

        为什么要付这笔钱: 每个 (字体, 字形) 组合的**首次**渲染都要走一次字体回退
        搜索, 实测单次 200~550 ms (见 runs/_pill_text_cost.out), 叠到一次
        create_text 上就是卡顿看门狗记的那 **987 ms**。预热之后同样一次 create_text
        只要 0.1~6.6 ms。

        为什么分片: 一次性跑完会让外壳构建从 ~1.4s 变成 ~2.9s, 而启动画面正靠主
        线程 update() 驱动 ⇒ splash 自己被卡住, 观感更糟。

        ⚠ 与 `_start_prebuild` 共享主线程: 页面预建优先 (用户马上要用), 所以片间
          也检查输入闸门, 用户一有动作就让位。

        ⚠ 切片粒度踩过两次坑, 记在这里别重犯:
          第一版按"字体族"切 ⇒ 单片 585~1000 ms, 照样卡住一帧;
          第二版按 (族 × 字号 × 字符块) 定长切 12 字 ⇒ 理想 10ms, 但内存一紧又回到
          133~160 ms。教训: **粒度不能按"逻辑上算一类"定, 也不能定死** ——
          必须按**实测单片耗时**动态调。现在: 单片 >60ms 就把字符块折半,
          <15ms 就放大 2 字。
        """
        if getattr(self, "_font_warm_done", False):
            return
        plan = self._font_warm_plan()
        si = getattr(self, "_font_warm_spec", 0)
        if si >= len(plan):
            self._font_warm_done = True
            return
        # ⚠ 这里曾经有一条"预建还没做完就让位"的闸门 (2026-09-11), **撤掉了**。
        #   理由: 它把预热推到预建结束之后, 而预热的消费者恰恰是在页面构建时画的
        #   (gradient_pill 在页面建出来的时候就 _paint 一次) ⇒ **预热永远晚于它的
        #   消费者**, 一片也没省到。实测撤掉前: 顶栏主题标签在 t=1s 的第一次 tick
        #   仍付 848 ms 全价 (那 4 个字符的预热被闸门挡到 ~4.3s 之后才跑)。
        #   教训: "让位给更重要的活"听着对, 但**如果那个活才是被优化对象的消费者,
        #   让位就等于让这笔钱白花**。顺序错了, 优化就变成负优化。
        # ② 总预算到顶就收工 (这是个尽力而为的优化, 不值得为它无限期地占主线程)
        spent = getattr(self, "_font_warm_ms", 0.0)
        if spent >= self._FONT_WARM_BUDGET_MS:
            self._font_warm_done = True
            _log.info("字体预热收工: 累计 %.0f ms / 预算 %.0f ms, 已覆盖 %d/%d 规格"
                      % (spent, self._FONT_WARM_BUDGET_MS, si, len(plan)))
            return
        # ③ 用户正在操作 → 让位 (和页面预建同一套闸门)
        if time.time() - getattr(self, "_last_input_ts", 0) < 1.2:
            self._reschedule_font_warm(250)
            return
        # ⚠ `_up` 必须**在分支之前**导入。第一版把它写进了 `if want is None:` 里,
        #    结果另一个分支下 `_up` 从未绑定 ⇒ 顶栏那条预热抛 UnboundLocalError,
        #    被下面的 except 吞成一句 "跳过: UnboundLocalError(...)" ——
        #    预热一次没做, 日志还看不出是代码写错了。本项目第 N 次栽在自指的静默失败上。
        try:
            from gui import ui_perf as _up
        except Exception:
            _up = None
        if _up is None:
            self._font_warm_done = True
            return
        fam, size, weight, want = plan[si]
        if want is None:
            chars = _up._ICON_CHARS
        else:
            chars = want          # 指定字符集: 只预这几个字, 一片就完
        if not chars:
            self._font_warm_done = True
            return
        step = getattr(self, "_font_warm_step", self._FONT_WARM_STEP0)
        off = getattr(self, "_font_warm_off", 0)
        if off >= len(chars):
            off = 0
        chunk = chars[off:off + step]
        if not chunk:                    # 尾巴兜底: 游标越界时从头取一块
            off = 0
            chunk = chars[:step]
        try:
            # 每片只渲染 (1 族 × 1 字号 × 1 字重 × step 字符) —— key 各不同, 片片真干活。
            _w = _up.warm_fonts(self.root, chars=chunk, sizes=(size,),
                                families=(fam,), weights=(weight,))
            _ms = _w.get("ms", 0.0)
            self._font_warm_ms = getattr(self, "_font_warm_ms", 0.0) + _ms
            if _ms > self._FONT_WARM_TARGET and step > self._FONT_WARM_STEP_MIN:
                self._font_warm_step = max(self._FONT_WARM_STEP_MIN, step // 2)
            elif _ms < 15.0 and step < self._FONT_WARM_STEP_MAX:
                self._font_warm_step = min(self._FONT_WARM_STEP_MAX, step + 2)
            if _ms > 120:
                _log.warning("字体预热单片偏慢: %s/%dpt/%s %.0f ms (块=%d, 目标 <%.0fms)"
                             % (fam, size, weight, _ms, step, self._FONT_WARM_TARGET))
            if not _w.get("skipped"):
                _log.debug("字体预热 %s %dpt %s [%d+%d/%d字]: %.1f ms"
                           % (fam, size, weight, off, len(chunk), len(chars), _ms))
            unresolved = _w.get("unresolved_families") or []
            if unresolved:
                _log.warning("字体未解析 (静默回退): %s" % (unresolved,))
        except Exception as e:
            _log.warning("字体预热 [%s/%s/%s] 跳过: %r" % (fam, size, weight, e))
        # 游标前进: 覆盖是连续的 (off -> off+len(chunk)), 缩块只会重复不会漏。
        off += len(chunk)
        if off >= len(chars):
            off = 0
            si += 1
        self._font_warm_spec = si
        self._font_warm_off = off
        self._reschedule_font_warm(16)

    def _font_warm_plan(self):
        """预热计划: [(字体族, 字号, 字重), ...] —— 顺序即优先级 (只留 2 条)。

        ⚠ 2026-09-11 加**字重**这一维, 因为预热非粗体**不覆盖**粗体:
          实测预热 (族, 12) 之后, (族, 12, "bold") 首绘仍要 **994 ms**
          (对照组 runs/_bold_warm_probe.out: A 冷 994.0 / B 预热非粗体 15.3 /
           C 预热粗体 6.6 ms)。而卡顿看门狗记的那 987 ms 正落在
          components.gradient_pill._paint 的 create_text(font=(..., "bold")) 上。

        ⚠ **为什么只留两个规格**(2026-09-11 第三轮, 原来有 24 条):
          第一版把真实出现过的 24 个 (族×字号×字重) 全排进计划, 结果预热自己
          成了最大的卡顿源 —— 看门狗实测单片 618 / 546 / 291 ms, 自适应缩块到
          2 个字符仍要 131 ms ⇒ 负载下**每个字形约 50 ms**。
          账要这么算: 预热一个字形付 50ms, 只为省掉一次 200~550ms 的冷渲染;
          但 24 规格 × 65 字形 = **1560 对**, 全付就是 ~78 秒主线程时间, 而其中
          绝大多数字形用户根本不会看到。**广谱预热是净亏。**
          留下来的只有 gradient_pill 的粗体 —— 它在**每个页面**按按钮就画,
          是唯一"必然被用到 + 单次代价最大(994ms)"的组合。
          其余规格的冷渲染(200~500ms, 且多数只在特定页面)不值得为它付这笔钱。
        """
        cached = getattr(self, "_font_warm_plan_cache", None)
        if cached is not None:
            return cached
        # 条目 = (族, 字号, 字重, 字符集)。字符集为 None 表示用全量 _ICON_CHARS。
        #
        # ⚠ 顺序 = 截止时间顺序, 不只是"价值"顺序: 预热必须赶在它的消费者之前做完,
        #   否则等于没做。顶栏那条只有一个 tick 的时间余量 (1000ms 后第一次
        #   _update_time), 所以排第一; pill 的消费者是各页面, 页面在预建里陆续建,
        #   排在后面。
        plan = [
            # ① 顶栏主题标签 ("☀️ 浅色" / "🌙 深色") —— 4 个字符, 一片就完
            #    看门狗记的 546 ms 就在这条上 (main_window._update_time 里
            #    `self._theme_lbl.config(text="🌙 深色")` 的首次绘制)。
            #    ⚠ 这一条**换字体救不了** —— 实测 (runs/_theme_font_cost.out,
            #      每规格单独起进程量, 避免字形缓存串味):
            #        Consolas 9    中文 62.0 / ☀️ 211.6 / 🌙 505.4 ms
            #        雅黑 9        中文 47.5 / ☀️ 205.5 / 🌙 362.4 ms
            #        Segoe UI Emoji 9  中文 57.0 / ☀️ 18.3 / 🌙 **597.3** ms
            #      🌙 连 emoji 专字体都更慢 ⇒ 贵的是"找哪个字体有这个字形"那次
            #      搜索本身, 跟目标字体无关。只能提前付掉。
            #    字符集只列这 4 个字, 一片就完 —— 精确到不用碰全量图标集。
            ("Microsoft YaHei UI", 9, "normal", "☀️🌙浅色深色"),
            # ② gradient_pill 的粗体 12/10pt —— 每个页面按按钮就画, 全量预热。
            #    (消费者是各页面; 页面在预建里陆续建出来, 所以只能排在这两条上面
            #     能覆盖到的范围尽量靠前的位置 —— 早一片就少几个按钮付全价。)
            ("Microsoft YaHei UI", 12, "bold", None),
            ("Microsoft YaHei UI", 10, "bold", None),
        ]
        self._font_warm_plan_cache = plan
        return plan

    def _reschedule_font_warm(self, delay):
        try:
            self.root.after(delay, self._warm_fonts_idle)
        except Exception:
            pass

    # ── 后台预热 torch / ultralytics ──
    def _kick_warmup(self):
        """页面预建完成后, 在后台线程预热 torch (冷导入 ≈9.9s)。

        这样首屏已经可交互, torch 在背后慢慢加载; 用户第一次点"训练/检测"时
        通常已经就绪, 不再额外等待。
        """
        if getattr(self, "_warmup_started", False):
            return
        self._warmup_started = True
        try:
            from infrastructure.lazy_import import warm_up_async, warm_up_state
            if warm_up_state() == "ready":
                return

            def _done(status, ms):
                try:
                    if status == "ready":
                        self.set_status(f"✅ AI 推理引擎已就绪 ({ms} ms)")
                except Exception:
                    pass

            warm_up_async(_done)
        except Exception:
            pass

    def show(self, anchor_geom: str | None = None):
        """启动动画完成后显示主窗口。

        ⚠ 2026-09-30 修「窗口忽大忽小」(怪怪报):
          实测 (`runs/_probe_window_handoff.py`) splash = **500x340 硬编码**,
          主窗口 = 屏幕自适应 (最大 1440x900) ⇒ **面积落差 7.5~7.6 倍**;
          而交接段原来只有 `destroy() → deiconify()+lift()`, **零几何过渡** ⇒
          肉眼就是"splash 消失 → 一个大窗口凭空弹出/窗口忽大忽小"。

        修法 (怪怪拍板 A「锚定」) —— 两步, 顺序**不能反**:
          ① **先 deiconify 再摆几何**: 实测 withdraw 状态下 `geometry()` 只记位置、
            **尺寸被忽略** (设 1440x900 仍报 500x340), 只有映射后才生效 ⇒ 必须先显示。
          ② 显示那一刻**就在 splash 的 (x,y)**, 所以视线起点与消失的画面重合;
            再切到终态尺寸 (位置不变) ⇒ 跳变只剩"尺寸", 且是**同一锚点向外长**,
            不再有"凭空出现在另一个地方"的错位感。
          终态尺寸仍取 `_final_w/_final_h` ⇒ 不会被永久钉在 500x340。
        """
        # 解析 splash 几何: 拿 (x,y) 做锚
        anchor_pos = ""
        if anchor_geom:
            try:
                parts = anchor_geom.split("+", 1)
                if len(parts) == 2 and parts[1]:
                    anchor_pos = "+" + parts[1]
            except Exception:
                anchor_pos = ""

        try:
            # ① 先显示 —— 必须早于 geometry(尺寸), 否则尺寸被忽略 (见上)
            self.root.deiconify()
            self.root.lift()
        except Exception:
            pass

        # ② 在 splash 的位置落位, 再切到终态尺寸 (位置锚定不变)
        if anchor_pos:
            try:
                self.root.geometry(anchor_pos if anchor_pos.startswith("+") else "")
                self.root.update_idletasks()
            except Exception:
                pass
        try:
            if anchor_pos:
                self.root.geometry(f"{self._final_w}x{self._final_h}{anchor_pos}")
            else:
                self.root.geometry(f"{self._final_w}x{self._final_h}")
            self.root.update_idletasks()
        except Exception:
            pass

        # 兜底: 若预建队列意外为空, 也要保证预热被触发
        try:
            self.root.after(1500, self._kick_warmup)
        except Exception:
            pass
        self._anchor_done = True

def main():
    _log.info("%s v%s SPA start (theme=%s)", _cfg.name, _cfg.version, theme_manager.get_mode())
    App().root.mainloop()


if __name__ == "__main__":
    main()
