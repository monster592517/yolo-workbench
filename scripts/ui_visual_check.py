# -*- coding: utf-8 -*-
"""ui_visual_check.py - Headless UI visual self-check.

Builds the main window, captures screenshots and verifies the glassmorphism
theme (sidebar/card/background/primary ratios) plus page-level snapshots.

Usage:
    python scripts/ui_visual_check.py [--out runs/ui_check_report.txt]
Exit code: 0 = all checks passed, 1 = failures found.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def ratio_of(arr, hex_color, tol=18):
    c = tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    mask = (abs(arr - c).max(axis=2) <= tol)
    return float(mask.mean())


def window_rect_logical(root):
    """窗口**客户区**的逻辑像素 (x, y, w, h) —— 与 tkinter 同一套坐标系。

    注意别拿 GetWindowRect/GetDpiForSystem 去"修正"它: 本机显示器缩放 125%, 而
    进程是 DPI-unaware, 那些 Win32 API 在 unaware 进程里**同样返回逻辑值**, 只会
    把标题栏和边框一起框进来, 坐标照样错。
    """
    return (root.winfo_rootx(), root.winfo_rooty(),
            root.winfo_width(), root.winfo_height())


class ScreenGrabber:
    """全屏截图 + 按逻辑矩形裁剪, 自校准物理/逻辑比例。

    为什么要自校准: `ImageGrab.grab(bbox=...)` 收的是**物理**像素, 而 tkinter
    报的是**逻辑**像素。直接把 winfo_rootx/rooty 丢进 bbox, 在 125% 缩放下整张
    截图会往左上偏四分之一屏 —— 左边混进别的窗口、上边混进本窗口标题栏、
    右边被切掉一条。之前的视觉自检就是在这种错位下算颜色占比的: 结论碰巧还行,
    但采样的根本不是该采的地方。

    自校准的依据: 全屏截图的宽度(物理) / 逻辑屏宽 = 比例, 不依赖任何 DPI API。
    """

    def __init__(self, root, image_grab_cls):
        self._grab = image_grab_cls
        self.scale = 1.0
        try:
            full = image_grab_cls.grab()
            phys_w = full.size[0]
            logical_w = root.winfo_screenwidth() or 0
            if logical_w > 0 and phys_w > logical_w:
                self.scale = phys_w / float(logical_w)
        except Exception:
            pass

    def shoot(self, rect_logical):
        """截取逻辑矩形对应的窗口画面 (返回 PIL Image)。"""
        lx, ly, lw, lh = rect_logical
        s = self.scale
        try:
            full = self._grab.grab()          # 全屏(物理像素)
            box = (int(round(lx * s)), int(round(ly * s)),
                   int(round((lx + lw) * s)), int(round((ly + lh) * s)))
            return full.crop(box)
        except Exception:
            # 退路: 直接用 bbox 抓 (没有缩放时是对的)
            return self._grab.grab(bbox=(lx, ly, lx + lw, ly + lh))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "runs" / "ui_check_report.json"))
    args = ap.parse_args()

    os.environ.setdefault("OPENCV_LOG_LEVEL", "OFF")
    from gui.main_window import App
    from gui.theme_manager import theme_manager
    from infrastructure.config import get_theme
    from PIL import ImageGrab

    app = App()
    app.root.attributes("-topmost", True)
    try:
        app.root.lift()
        app.root.focus_force()
    except Exception:
        pass
    for _ in range(30):
        app.root.update()
        time.sleep(0.05)

    rect = window_rect_logical(app.root)      # 客户区, 逻辑像素
    lx, ly, lw, lh = rect
    grabber = ScreenGrabber(app.root, ImageGrab)
    x, y, w, h = lx, ly, lw, lh
    _scale = grabber.scale

    # 主题色按当前生效主题动态取值 (浅色/深色/跟随系统时间)
    C = get_theme().colors
    checks = [
        ("sidebar", C.sidebar_bg, 0.08),
        ("card", C.card_bg, 0.10),
        ("bg", C.bg, 0.05),
        ("primary", C.primary, 0.0025),  # 主色为装饰性元素, 占比随窗口浮动, 0.25% 足以证明主题生效
    ]
    report = {"window": {"x": x, "y": y, "w": w, "h": h},
              "phys_logical_scale": round(_scale, 3),
              "checks": {}, "pages": {}, "passed": True}
    lines = ["UI visual check report (theme: %s)" % theme_manager.resolved(),
             "window(logical client): %dx%d at (%d,%d)  [phys/logical x%.3f]"
             % (w, h, x, y, _scale)]

    def _grab_checks():
        """截图并返回各主题色占比; 被其他窗口遮挡时可能偏离"""
        shot = grabber.shoot(rect)
        arr = __import__("numpy").asarray(shot.convert("RGB")).astype(int)
        return {name: ratio_of(arr, color) for name, color, _ in checks}

    # 遮挡保护: 重新置顶+截图重试, 直到主题占比合理或达到上限
    ratios = None
    for attempt in range(3):
        try:
            app.root.attributes("-topmost", True)
            app.root.lift()
        except Exception:
            pass
        for _ in range(8):
            app.root.update()
            time.sleep(0.04)
        ratios = _grab_checks()
        fails_tmp = sum(1 for name, _, minr in checks if ratios[name] < minr)
        if fails_tmp == 0 or attempt == 2:
            break
        time.sleep(0.3)  # 等待窗口重新层级后再试
    fails = 0
    for name, color, minr in checks:
        r = ratios[name]
        ok = r >= minr
        fails += (not ok)
        lines.append("  %-16s ratio=%.4f min=%.4f %s" % (name, r, minr, "PASS" if ok else "FAIL"))
        report["checks"][name] = {"ratio": round(r, 4), "min": minr, "pass": bool(ok)}

    # 快照: 循环体里的 show_page() 会惰性新建页面, 直接迭代 dict 会
    # RuntimeError: dictionary changed size during iteration (预建顺序一变就露).
    for key in list(app._pages):
        app.show_page(key)
        for _ in range(6):
            app.root.update()
            time.sleep(0.03)
        app.root.update()
        shot = grabber.shoot(rect)
        out = ROOT / "runs" / ("ui_check_%s.png" % key)
        shot.save(out)
        report["pages"][key] = str(out)
        lines.append("  page %-10s -> %s" % (key, out.name))

    report["passed"] = fails == 0
    lines.append("RESULT: %s (%d failures)" % ("PASS" if fails == 0 else "FAIL", fails))
    text = "\n".join(lines)
    print(text)
    (ROOT / "runs").mkdir(exist_ok=True)
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    app.root.destroy()
    sys.exit(0 if fails == 0 else 1)


if __name__ == "__main__":
    main()
