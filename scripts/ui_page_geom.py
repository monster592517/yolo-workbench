# -*- coding: utf-8 -*-
"""通用页面几何探针 —— dump 指定页的顶层分栏结构与各块实高。

用途: 重排布局前先量清楚"哪块多高、空白在哪", 而不是靠看截图猜。

用法:
    python scripts/ui_page_geom.py dataset
    python scripts/ui_page_geom.py training --depth 3
"""
from __future__ import annotations
import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("OPENCV_LOG_LEVEL", "OFF")


def dump(w, depth=0, maxdepth=6, tag=""):
    try:
        if not w.winfo_exists():
            return
    except Exception:
        return
    cls = w.winfo_class()
    mgr = w.winfo_manager() or "-"
    m = 1 if w.winfo_ismapped() else 0
    rw, rh = w.winfo_width(), w.winfo_height()
    qw, qh = w.winfo_reqwidth(), w.winfo_reqheight()
    name = str(w).split(".")[-1] or "root"
    print("  " * depth + f"{tag}{cls:<11} {name:<12} {mgr:<6} map={m} "
          f"{rw}x{rh} @({w.winfo_x()},{w.winfo_y()}) req={qw}x{qh}")
    if depth >= maxdepth:
        return
    for c in w.winfo_children():
        dump(c, depth + 1, maxdepth)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("page")
    ap.add_argument("--depth", type=int, default=4)
    ap.add_argument("--settle", type=float, default=2.0)
    args = ap.parse_args()

    from gui.main_window import App
    app = App()
    for _ in range(20):
        app.root.update(); time.sleep(0.04)
    app.show_page(args.page)
    t_end = time.time() + args.settle
    while time.time() < t_end:
        app.root.update(); time.sleep(0.05)

    page = app._pages.get(args.page)
    print("=" * 110)
    if page is None:
        print(f"页面 {args.page!r} 未构建")
        app.root.destroy()
        return 1
    print(f"PAGE {args.page}: {type(page).__name__}  "
          f"{page.winfo_width()}x{page.winfo_height()} "
          f"req={page.winfo_reqwidth()}x{page.winfo_reqheight()}")
    dump(page, 0, args.depth)
    print("=" * 110)
    print("页面直接子块 (顶层分栏):")
    for c in page.winfo_children():
        mgr = c.winfo_manager() or "-"
        print(f"  {c.winfo_class():<11} {mgr:<7} "
              f"{c.winfo_width()}x{c.winfo_height()} @({c.winfo_x()},{c.winfo_y()}) "
              f"req={c.winfo_reqwidth()}x{c.winfo_reqheight()} "
              f"map={1 if c.winfo_ismapped() else 0}")
    app.root.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
