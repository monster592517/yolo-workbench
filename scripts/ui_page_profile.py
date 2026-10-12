# -*- coding: utf-8 -*-
"""ui_page_profile.py — 定位页面首建耗时的真实热点 (2026-09-11)

    python scripts/ui_page_profile.py [page_key ...]     # 默认 detection training env

只做测量, 不改任何东西。输出 cProfile 累计耗时 Top-N, 用来判断优化该打哪里
(而不是凭感觉去改控件)。

注意: App() 启动时后台分片预建会把页面提前建好, 于是 show_page 只花十几毫秒。
要量"真·首建", 必须先把已建页面清掉、把预建队列停掉, 再直接构造页面类。
"""
from __future__ import annotations

import cProfile
import io
import os
import pstats
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def _filter_line(line: str) -> bool:
    """保留有信息量的行: 丢掉统计表头与模块顶层 import。"""
    s = line.strip()
    if not s:
        return False
    if "{" in s or "Ordered by" in s or "restriction" in s:
        return False
    if "ncalls" in s or "function calls" in s:
        return False
    return True


def profile_page(app, key: str, top: int = 35) -> float:
    cls = app._page_class(key)
    if cls is None:
        print("  !! cannot resolve class for", key)
        return 0.0

    pr = cProfile.Profile()
    t0 = time.perf_counter()
    pr.enable()
    page = None
    try:
        page = cls(app._page_host, controller=app)
        page.grid(row=0, column=0, sticky="nsew")
        app.root.update_idletasks()
    finally:
        pr.disable()
    ms = (time.perf_counter() - t0) * 1000

    buf = io.StringIO()
    # cumulative 排序: 看调用树里谁最贵 (tottime 会被上千个 import 辅助函数摊平)
    pstats.Stats(pr, stream=buf).sort_stats("cumulative").print_stats(top)
    print("\n" + "=" * 74)
    print("page %-12s cold build %.1f ms" % (key, ms))
    print("=" * 74)
    for line in buf.getvalue().splitlines():
        if _filter_line(line):
            print(line)

    if page is not None:
        try:
            if hasattr(page, "on_hide"):
                page.on_hide()
            page.destroy()
        except Exception:
            pass
    return ms


def main() -> int:
    keys = [a for a in sys.argv[1:] if not a.startswith("-")]
    keys = keys or ["detection", "training", "env"]
    with_warmup = "--warmup" in sys.argv
    os.environ.setdefault("OPENCV_LOG_LEVEL", "OFF")

    from gui.main_window import App
    if not with_warmup:
        # 默认关掉后台 torch 预热: 它会持着 GIL 跑十几秒的 import,
        # 把主线程页面构建的墙钟时间拖长一倍以上, 量出来的不是页面本身的成本。
        App._kick_warmup = lambda self: None
    app = App(visible=False)

    # 清场: 停掉预建队列 + 销毁已提前建好的页面, 否则量到的是"二次绑定"耗时
    try:
        app._prebuild_queue.clear()
    except Exception:
        pass
    for p in list(getattr(app, "_pages", {}).values()):
        try:
            if hasattr(p, "on_hide"):
                p.on_hide()
        except Exception:
            pass
        try:
            p.destroy()
        except Exception:
            pass
    app._pages = {}
    app.root.update_idletasks()

    total = 0.0
    for k in keys:
        if k not in app._PAGE_SPEC:
            print("skip unknown page:", k)
            continue
        total += profile_page(app, k)

    print("\nCOLD BUILD TOTAL %.1f ms" % total)
    try:
        app._on_close()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
