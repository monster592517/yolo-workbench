# -*- coding: utf-8 -*-
"""YOLO工作台 — splash with real loading progress"""
import sys, threading, time, os as _os
from pathlib import Path

# MUST be before any cv2 import — suppress DSHOW/FFMPEG warnings
_os.environ["OPENCV_LOG_LEVEL"] = "OFF"
_os.environ["OPENCV_VIDEOIO_LOG_LEVEL"] = "OFF"
_os.environ["OPENCV_FFMPEG_LOG_LEVEL"] = "OFF"

_PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

# ── DPI 觉察 (v0.34.1) —— **必须最先执行, 早于任何 Tk 窗口** ──
# 不声明的话, 「桌面/窗口」两个源第一次调 `mss.mss()` 时会把进程的 DPI 觉察度
# 从 UNAWARE 翻成 PER_MONITOR, Windows 随即停止 1.354x 插值放大 ⇒ **整个主窗口
# 在屏幕上缩一圈** (实测占屏宽 100% -> 73.8%)。完整真因链见
# `src/infrastructure/dpi.py` 的模块 docstring (含 mss 源码行号与 A/B 实测)。
# 这里的调用必须早于 `_start_splash()` 里的 `tk.Tk()`。
try:
    from infrastructure.dpi import enable_per_monitor_dpi_awareness as _dpi_declare
    _DPI = _dpi_declare()
    print(f"[DPI] 觉察度声明: {_DPI}")
except Exception as _e:
    print(f"[DPI] 声明失败 (不影响启动): {_e}")

# ── 无控制台启动兜底 (pythonw 修复, v0.10.16) ──
# 桌面快捷方式用 pythonw 启动时 sys.stdout/stderr 为 None, ultralytics
# TQDM.close() 会因 self.file.write("\n") 崩溃 ("NoneType' object has no
# attribute 'write'")。这里把缺失的控制台流重定向到 runs/logs/console.log。
if sys.stdout is None or sys.stderr is None:
    try:
        _console_dir = _PROJECT_ROOT / "runs" / "logs"
        _console_dir.mkdir(parents=True, exist_ok=True)
        _console_stream = open(_console_dir / "console.log", "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = _console_stream
        if sys.stderr is None:
            sys.stderr = _console_stream
    except Exception:
        pass

# Initialize logging first
import infrastructure.logger  # noqa

# ── 企业级审计钩子: 全量记录运行情况与报错 (runs/logs/operations.log) ──
try:
    from infrastructure.audit import install_audit_hooks, audit
    install_audit_hooks()
    audit("app_start", "YOLO 工作台启动 (启动画面)", module="YOLO工作台")
except Exception:
    pass

# ── 路径卫士: 目录迁移自动适配 + 实时监测 (v0.10.7+) ──
try:
    from infrastructure.path_guard import ensure_paths_adapted
    _path_report = ensure_paths_adapted(start_monitor=True)
    if _path_report.get("migrated"):
        print(f"[路径卫士] 检测到项目目录迁移 -> {_path_report['root']}, 已自动适配")
except Exception:
    pass

# ── 路径中心: 全程序路径唯一真相源, 启动即审计 (v0.10.15+) ──
try:
    from infrastructure import path_center
    from infrastructure.audit import audit as _audit_pc
    _pc_issues = path_center.verify_consistency()
    _audit_pc("path_center_init",
              f"路径中心就绪: 根目录={path_center.get_root()} "
              f"一致性={'OK' if not _pc_issues else 'WARN:' + str(_pc_issues)}",
              level="info" if not _pc_issues else "warn",
              module="path_center")
except Exception:
    pass

# ── Python 版本统一检查 (v0.10.0+: 项目统一要求 Python 3.11+) ──
try:
    from infrastructure.environment import python_version_report
    _py_ok, _py_cur, _py_req, _py_tip = python_version_report()
    if not _py_ok:
        print(f"[警告] 当前 Python {_py_cur} 低于项目要求 {_py_req}+")
        print(f"        {_py_tip}")
except Exception:
    pass

# Read version from config
try:
    from infrastructure.config import get_config
    _version = get_config().version
except Exception:
    _version = "0.10.4"

# ── 模型 MD5 完整性校验 (企业安全: 防止模型被替换; 开发时设 YOLO_SKIP_VERIFY=1 跳过) ──
if not _os.environ.get("YOLO_SKIP_VERIFY", ""):
    try:
        scripts_dir = _PROJECT_ROOT / "scripts"
        sys.path.insert(0, str(scripts_dir))
        from verify_model import verify_model
        if not verify_model(show_errors=True):
            sys.exit(1)
    except Exception:
        pass  # 开发模式: scripts 不可用时跳过

import queue
import tkinter as tk

SPLASH = None
_SPLASH_CV = None
_SPLASH_STATUS = None
_SPLASH_THEME = "dark"

# 启动画面里预建页面的时间预算 (秒)。预算内按重页面优先建完一部分, 其余交给
# 主窗口的后台空闲队列; 调大 = 切换更热但主界面出得更晚。
_PREBUILD_BUDGET_S = 1.8

# ═══════════════ 启动画面主题 (跟随系统深浅色) ═══════════════
_SPLASH_COLORS = {
    "light": {
        "bg": "#F6F8FB", "card": "#FFFFFF", "border": "#D8E1EE",
        "title": "#1D4ED8", "sub": "#64748B", "status": "#64748B",
        "bar_bg": "#E2E8F0", "bar_fill": "#2563EB", "bar_glow": "#93C5FD",
        "particle": ("#2563EB", "#60A5FA", "#93C5FD"),
        "ok": "#16A34A", "err": "#DC2626",
    },
    "dark": {
        "bg": "#0A0A0A", "card": "#141414", "border": "#2A2A2A",
        "title": "#60A5FA", "sub": "#9CA3AF", "status": "#9CA3AF",
        "bar_bg": "#1F2937", "bar_fill": "#3B82F6", "bar_glow": "#1E40AF",
        "particle": ("#38BDF8", "#3B82F6", "#818CF8"),
        "ok": "#34D399", "err": "#F87171",
    },
}


def _system_theme() -> str:
    """读取 Windows 系统深浅色主题 (AppsUseLightTheme), 失败返回 dark"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            v, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
            return "light" if v == 1 else "dark"
    except Exception:
        return "dark"


def _resolve_splash_theme() -> str:
    """启动画面主题: config theme_mode -> light/dark 直接采用; auto/未知 -> 跟随系统"""
    try:
        from infrastructure.config import get_config
        mode = get_config().get("ui", {}).get("theme_mode", "auto") if hasattr(get_config(), "get") else None
    except Exception:
        mode = None
    if mode is None:
        try:
            import re
            text = Path(__file__).resolve().parent.joinpath("config.yaml").read_text(encoding="utf-8")
            m = re.search(r"theme_mode\s*:\s*['\"]?([\w-]+)", text)
            mode = m.group(1) if m else "auto"
        except Exception:
            mode = "auto"
    if mode in ("light", "dark"):
        return mode
    return _system_theme()


def _gradient(cv, w, h, top, bottom):
    """绘制垂直渐变背景"""
    def _mix(a, b, t):
        av = [int(a[i:i+2], 16) for i in (1, 3, 5)]
        bv = [int(b[i:i+2], 16) for i in (1, 3, 5)]
        return "#" + "".join(f"{int(av[i] + (bv[i] - av[i]) * t):02x}" for i in range(3))
    steps = 64
    for i in range(steps):
        y0 = int(h * i / steps)
        y1 = int(h * (i + 1) / steps)
        cv.create_rectangle(0, y0, w, y1, fill=_mix(top, bottom, i / steps), outline="")


def _start_splash():
    """创建启动画面: 静态 Logo + 进度条动画 (UI 保持不动)"""
    global SPLASH, _SPLASH_THEME
    _SPLASH_THEME = _resolve_splash_theme()
    C = _SPLASH_COLORS[_SPLASH_THEME]
    SPLASH = tk.Tk()
    SPLASH.title("YOLO工作台")
    SPLASH.overrideredirect(True)
    SPLASH.configure(bg=C["bg"])

    W, H = 500, 340
    sw, sh = SPLASH.winfo_screenwidth(), SPLASH.winfo_screenheight()
    SPLASH.geometry(f"{W}x{H}+{(sw - W) // 2}+{(sh - H) // 2}")

    cv = tk.Canvas(SPLASH, width=W, height=H, bg=C["bg"], highlightthickness=0)
    cv.pack()
    _gradient(cv, W, H, C["bg"], C["card"])
    # 顶部高光线
    cv.create_rectangle(0, 0, W, 2, fill=C["border"], outline="")

    # Logo 静态区块 (UI 保持不动, 仅进度条动画; 文字与图形上下分离不重叠)
    CX = W // 2
    CY = 84
    _logo_path = Path(__file__).resolve().parent / "assets" / "app_icon.png"
    try:
        _logo_img = tk.PhotoImage(file=str(_logo_path)).subsample(11)  # 1024 -> ~93px
        cv.create_image(CX, CY, image=_logo_img)
        SPLASH._logo_img = _logo_img  # 防止被垃圾回收
    except Exception:
        R = 40
        cv.create_oval(CX - R, CY - R, CX + R, CY + R, fill=C["bar_bg"],
                       outline=C["border"], width=2)
        cv.create_oval(CX - R + 6, CY - R + 6, CX + R - 6, CY + R - 6,
                       fill=C["card"], outline=C["bar_fill"], width=2)
        cv.create_text(CX, CY - 2, text="Y", font=("Consolas", 32, "bold"),
                       fill=C["title"])
    cv.create_text(CX, 144, text="YOLO工作台", font=("Microsoft YaHei UI", 14, "bold"),
                   fill=C["sub"])
    cv.create_text(CX, 166, text="智能检测平台 v" + _version,
                   font=("Microsoft YaHei UI", 9), fill=C["sub"])

    # 状态文字 (静态位置, 内容随加载更新)
    status = cv.create_text(CX, 200, text="正在初始化...", font=("Microsoft YaHei UI", 9),
                            fill=C["status"])

    # 进度条 (唯一动画元素: 宽度 + 百分比)
    PB_X, PB_Y, PB_W, PB_H = CX - 180, 228, 360, 9
    cv.create_rectangle(PB_X, PB_Y, PB_X + PB_W, PB_Y + PB_H, fill=C["bar_bg"],
                        outline=C["border"], width=1)
    pfill = cv.create_rectangle(PB_X, PB_Y, PB_X, PB_Y + PB_H, fill=C["bar_fill"], outline="")
    pct = cv.create_text(PB_X + PB_W + 14, PB_Y + PB_H // 2, text="0%",
                         font=("Consolas", 9, "bold"), fill=C["title"], anchor="w")
    SPLASH._pct = 0.0

    def _frame():
        """仅更新进度条与百分比 (无粒子/旋转/脉动动画)"""
        try:
            w0 = PB_W * (SPLASH._pct / 100.0)
            cv.coords(pfill, PB_X, PB_Y, PB_X + w0, PB_Y + PB_H)
            cv.itemconfig(pct, text=f"{int(SPLASH._pct)}%")
            SPLASH._sid = SPLASH.after(40, _frame)
        except Exception:
            pass

    _frame()
    global _SPLASH_CV, _SPLASH_STATUS
    _SPLASH_CV = cv
    _SPLASH_STATUS = status
    return cv, status



# 后台加载 -> 队列 -> 主线程更新 (线程安全)
_LOAD_Q = queue.Queue()

_LOAD_MODULES = [
    ("infrastructure.config", "读取配置..."),
    ("infrastructure.diagnostics", "初始化日志系统..."),
    ("infrastructure.environment", "检测运行环境..."),
    ("core.trainer", "加载训练引擎..."),
    ("core.detector", "加载检测引擎..."),
    ("gui.components", "构建界面组件..."),
    ("gui.theme_manager", "应用界面主题..."),
    ("gui.pages.training_page", "预载训练页面..."),
    # ★ 2026-10-01：检测页重新回到导航（交付中心已随仿真/部署搬出本仓）⇒ 恢复预载。
    ("gui.pages.detection_page", "预载检测页面..."),
    ("gui.pages.environment_page", "预载环境页面..."),
    ("gui.pages.settings_page", "预载设置页面..."),
    ("gui.main_window", "准备主窗口..."),
]


def _load_worker():
    total = len(_LOAD_MODULES) + 5  # N 模块 + 5 页面实例（detection/dataset/training/env/settings）
    for i, (mod, desc) in enumerate(_LOAD_MODULES):
        t0 = time.time()
        try:
            __import__(mod)
            dt = (time.time() - t0) * 1000
            _LOAD_Q.put(("step", i + 1, total, f"{desc} ({dt:.0f}ms)"))
        except Exception as e:
            _LOAD_Q.put(("fail", f"{mod}: {e}"))
            return
    _LOAD_Q.put(("main", total, "正在启动主界面..."))


def _build_main_and_go():
    """主线程构建主窗口, 完成后销毁启动画面并进入主循环 (无空窗过渡)"""
    global SPLASH
    try:
        from gui.main_window import App
        app = App(visible=False)  # 构建期间隐藏, 避免遮挡启动动画
    except Exception as e:
        import traceback
        traceback.print_exc()
        try:
            if SPLASH is not None:
                SPLASH.destroy()
        except Exception:
            pass
        return
    # 预构建页面 (splash 阶段尽量多做, 切换秒开)。
    # SPEED (2026-09-11): 原来是无条件建完 9 页。实测这 9 页纯控件创建合计约 2.9s,
    # 单页 0.1~1.2s (training 630ms / h3factory 1240ms 最重), 而这期间事件循环被占死,
    # 启动画面只能一顿一顿地跳。现在改成**给一个时间预算**: 按"重页面优先"的顺序
    # 在预算内尽量多做, 剩下的交给主窗口的空闲分片队列 (App._start_prebuild) 在
    # 后台补齐 —— 主界面早出来约 2 秒, 而重页面本来就在队列最前面, 切换照样是热的。
    try:
        specs = list(app._prebuild_order())   # 重页面优先, 不是 _PAGE_SPEC 的声明顺序
        total = len(_LOAD_MODULES) + len(specs)
        done = len(_LOAD_MODULES)
        _deadline = time.time() + _PREBUILD_BUDGET_S
        for i, key in enumerate(specs):
            if key not in app._pages:
                if i > 0 and time.time() > _deadline:
                    break          # 预算用完: 剩下的交给后台队列
                app._ensure_page(key)
            done += 1
            SPLASH._pct = done / total * 100
            try:
                _SPLASH_CV.itemconfig(
                    _SPLASH_STATUS,
                    text=f"正在预构建页面 ({i + 1}/{len(specs)}) ...")
            except Exception:
                pass
            SPLASH.update()
        # 队列按"实际还没建好的页面"重算, 避免后台空转已建好的页
        app._prebuild_queue = app._prebuild_order()
    except Exception:
        pass

    splash = SPLASH
    SPLASH = None
    # ⚠ 2026-09-30 修「窗口忽大忽小」(怪怪报, 拍板 A「锚定」):
    #   先把 splash 的几何**读下来**, 再销毁它 —— 让主窗口从"视线所在的位置"长出来。
    #   不读的话就是 destroy() → 一个大窗口凭空弹出 (实测面积落差 7.5~7.6 倍)。
    _splash_geom = ""
    try:
        if splash is not None:
            _splash_geom = splash.geometry()      # "500x340+X+Y"
    except Exception:
        _splash_geom = ""
    try:
        splash.destroy()
    except Exception:
        pass
    # 启动画面曾是 tkinter 默认根窗口, 销毁后重新注册主窗口为默认根,
    # 否则主题切换重建页面时无 master 的 StringVar 会报
    # "no default root window" 导致界面重建中断卡死 (v0.10.28 修复)
    try:
        import tkinter as _tk
        _tk._default_root = app.root
    except Exception:
        pass
    try:
        app.show(anchor_geom=_splash_geom)
    except Exception:
        pass
    app.root.mainloop()


def _poll_loader(cv, status):
    try:
        while True:
            msg = _LOAD_Q.get_nowait()
            if msg[0] == "step":
                _, i, total, desc = msg
                SPLASH._pct = i / total * 100
                cv.itemconfig(status, text=desc)
            elif msg[0] == "main":
                _, total, desc = msg
                SPLASH._pct = (total - 5) / total * 100
                cv.itemconfig(status, text=desc)
                SPLASH.after(120, _build_main_and_go)
                return
            elif msg[0] == "fail":
                cv.itemconfig(status, text="加载失败: " + msg[1],
                              fill=_SPLASH_COLORS[_SPLASH_THEME]["err"])
                SPLASH.after(3500, lambda: SPLASH and SPLASH.destroy())
                return
    except queue.Empty:
        pass
    except Exception:
        return
    try:
        SPLASH.after(40, lambda: _poll_loader(cv, status))
    except Exception:
        pass


def main():
    # ── 玻璃对话框套件: 全局替换 messagebox/simpledialog (子窗口统一美化) ──
    try:
        from gui.dialog_kit import install as _install_ddk
        _install_ddk()
    except Exception:
        pass
    cv, status = _start_splash()
    threading.Thread(target=_load_worker, daemon=True, name="splash-loader").start()
    SPLASH.after(60, lambda: _poll_loader(cv, status))
    SPLASH.mainloop()


if __name__ == "__main__":
    main()
