# -*- coding: utf-8 -*-
"""ui_startup_bench.py — 启动体验基准 (2026-09-11)

量的是**用户真正感知到的那条线**: 进程起来 -> 主界面可交互 -> 之后 10 秒里
主线程有没有被卡住。不是量某个函数跑多快。

复刻真实入口 (YOLO工作台.py) 的启动时序:
    1. import        `import gui.main_window`
    2. shell         App(visible=False) 构造外壳 (启动画面期间隐藏)
    3. prebuild      启动画面阶段的页面预建 (带时间预算, 重页面优先)
    4. interactive   app.show() —— 用户看到主界面
    5. settle        主界面出来之后, 后台空闲队列把剩下的页面补齐

加一个**主线程卡顿采样**: 主界面出来之后的 10 秒里, 每轮 `root.update()`
的耗时。UI 卡不卡直接看这个 —— 单次超过 100ms 就是肉眼可见的顿。

    python scripts/ui_startup_bench.py               # 当前实现
    python scripts/ui_startup_bench.py --legacy-gpu  # A/B: 塞回旧的"后台 import torch
                                                     # 探 GPU", 复现 GIL 霸占
    python scripts/ui_startup_bench.py --legacy-splash  # A/B: 塞回"启动画面无条件建完
                                                     # 9 页"的旧行为
"""
from __future__ import annotations

import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

STALL_WINDOW_S = 10.0
STALL_MS = 100.0
BUDGET_S = 1.8          # 与 YOLO工作台.py 的 _PREBUILD_BUDGET_S 保持一致


def _install_legacy_gpu() -> None:
    """把 2026-09-11 之前的 GPU 探测塞回去: 第一句就是 `import torch`。"""
    import subprocess
    from infrastructure import environment as env

    def _legacy_impl():
        try:
            import torch
            if torch.cuda.is_available():
                props = torch.cuda.get_device_properties(0)
                mem = getattr(props, "total_memory", getattr(props, "total_mem", 0))
                return True, "%s (%.1fGB)" % (torch.cuda.get_device_name(0), mem / (1024 ** 3))
        except Exception:
            pass
        try:
            r = subprocess.run(["nvidia-smi"], capture_output=True, timeout=env._cfg.timeout_gpu)
            if r.returncode == 0:
                return True, "NVIDIA GPU"
        except Exception:
            pass
        return False, ""

    env._check_gpu_impl = _legacy_impl
    print("  [legacy-gpu] 已注入旧的 `import torch` GPU 探测")


def main() -> int:
    legacy_gpu = "--legacy-gpu" in sys.argv
    legacy_splash = "--legacy-splash" in sys.argv
    label = []
    if legacy_gpu:
        label.append("legacy-gpu")
    if legacy_splash:
        label.append("legacy-splash")
    os.environ.setdefault("OPENCV_LOG_LEVEL", "OFF")

    t_proc = time.perf_counter()

    t0 = time.perf_counter()
    from gui.main_window import App
    t_import = (time.perf_counter() - t0) * 1000

    if legacy_gpu:
        _install_legacy_gpu()

    t_shell0 = time.perf_counter()
    app = App(visible=False)
    t_shell = (time.perf_counter() - t_shell0) * 1000

    # ── 启动画面阶段的预建 (复刻 YOLO工作台.py::_build_main_and_go) ──
    n_pre = 0
    t_pre0 = time.perf_counter()
    try:
        specs = list(app._prebuild_order() if not legacy_splash
                     else (k for k in app._PAGE_SPEC))
        deadline = time.time() + (1e9 if legacy_splash else BUDGET_S)
        for i, key in enumerate(specs):
            if key not in app._pages:
                if (not legacy_splash) and i > 0 and time.time() > deadline:
                    break
                app._ensure_page(key)
                n_pre += 1
            app.root.update()
        app._prebuild_queue = app._prebuild_order()
    except Exception as e:
        print("  prebuild error:", e)
    t_pre = (time.perf_counter() - t_pre0) * 1000

    # ── 主界面现身 ──
    t_show0 = time.perf_counter()
    app.show()
    app.root.update()
    deadline = time.perf_counter() + 5.0
    while time.perf_counter() < deadline:
        app.root.update()
        try:
            if app.root.winfo_viewable() and app.root.winfo_width() > 100:
                break
        except Exception:
            break
        time.sleep(0.01)
    t_show = (time.perf_counter() - t_show0) * 1000
    t_interactive = (time.perf_counter() - t_proc) * 1000

    print("=" * 70)
    print("启动基准 %s" % ("[" + ",".join(label) + "]" if label else "[当前实现]"))
    print("=" * 70)
    print("  1. import gui.main_window          %8.1f ms" % t_import)
    print("  2. App(visible=False) 建外壳        %8.1f ms" % t_shell)
    print("  3. 启动画面阶段预建 %d 页/9         %8.1f ms" % (n_pre, t_pre))
    print("  4. app.show() -> 主界面可交互       %8.1f ms" % t_show)
    print("  ------------------------------------------------")
    print("  用户看到主界面 (@进程启动起算)       %8.1f ms" % t_interactive)

    # ── 主界面出来之后 10 秒的主线程卡顿采样 ──
    stalls: list[float] = []
    t_end = time.perf_counter() + STALL_WINDOW_S
    left_then = len(getattr(app, "_prebuild_queue", []) or [])
    while time.perf_counter() < t_end:
        a = time.perf_counter()
        app.root.update()
        stalls.append((time.perf_counter() - a) * 1000)
        time.sleep(0.004)

    bad = [s for s in stalls if s > STALL_MS]
    print("  5. 后台补齐剩余 %d 页               %8s" % (
        left_then, "已完成" if not (app._prebuild_queue) else "未完成"))
    print("-" * 70)
    print("  主线程 update() 采样 %d 轮 (%.0f 秒窗口)" % (len(stalls), STALL_WINDOW_S))
    print("    中位 %6.1f ms | p95 %6.1f ms | 最大 %7.1f ms"
          % (statistics.median(stalls),
             sorted(stalls)[int(len(stalls) * 0.95)] if stalls else 0,
             max(stalls) if stalls else 0))
    print("    >%.0fms 的卡顿次数: %d  (累计 %.0f ms)"
          % (STALL_MS, len(bad), sum(bad)))
    print("  进程总耗时                          %8.1f ms" % ((time.perf_counter() - t_proc) * 1000))

    verdict = "SMOOTH" if not bad else "JANKY"
    print("  VERDICT: %s" % verdict)

    try:
        app._on_close()
    except Exception:
        pass
    return 0 if verdict == "SMOOTH" else 1


if __name__ == "__main__":
    sys.exit(main())
