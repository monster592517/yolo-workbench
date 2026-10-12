# -*- coding: utf-8 -*-
"""导航高亮/切页复现: 连续切换 3 页, 每页截取侧边栏区域 (宽~260px)."""
from __future__ import annotations
import sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PIL import ImageGrab


def main():
    from gui.main_window import App
    app = App(visible=True)
    app.root.update_idletasks(); app.root.update()
    time.sleep(0.8)
    app.root.update()
    # 获取窗口几何
    x = app.root.winfo_rootx(); y = app.root.winfo_rooty()
    for key in ("detection", "dataset", "training", "env", "settings", "detection"):
        app.show_page(key, animate=False)
        app.root.update_idletasks(); app.root.update()
        time.sleep(0.6)
        app.root.update()
        shot = ImageGrab.grab(bbox=(x - 2, y + 60, x + 258, y + 560))
        path = ROOT / "runs" / f"navchk_{key}.png"
        shot.save(path)
        print("saved", path.name)
    app.root.destroy()
    print("DONE")


if __name__ == "__main__":
    main()
