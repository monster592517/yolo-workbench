# -*- coding: utf-8 -*-
"""ui_stall_watchdog.py — 主线程卡顿看门狗 (定位"卡一下"用, 不靠猜)

为什么需要它:
    Tkinter 里"启动后卡一下 / 切页顿一下"这类问题, 靠肉眼和埋点都很低效。
    本工具的思路是**直接抓主线程栈**: 主循环每轮打一次时间戳, 看门狗线程发现
    时间戳停滞超过阈值, 立刻 dump 主线程当前调用栈。

    实战效果 (2026-09-11): 启动后 540 / 956 / 1367 ms 的三次卡顿, 一次就被定位到
        settings_page._sess_load -> history_viewer.list_sessions
        -> DSH_DIR.rglob("session.jsonl.zstd")   ← 主线程全树扫描
    随后换成定深 glob + 后台线程, 卡顿降到 120ms 且栈里不再有自身函数。

用法:
    python scripts/ui_stall_watchdog.py                    # 默认: 全页预建 → 观察 10s
    python scripts/ui_stall_watchdog.py --seconds 20 --threshold 80
    python scripts/ui_stall_watchdog.py --no-prebuild      # 只建当前页 (看首屏卡顿)
    python scripts/ui_stall_watchdog.py --json runs/stall.json

退出码: 0 = 无超阈值卡顿; 1 = 有卡顿 (可直接当 CI 门禁)

⚠ 环境提醒: 本工具会构建 GUI 与 9 个页面。内存紧 (可用 <1GB, 常见于 ComfyUI 常驻) 时
   可能撞 `OpenBLAS error: Memory allocation still failed` —— 那是环境态不是代码 bug,
   先 `psutil` 看一眼可用内存与 Top 进程。

⚠ 读数口径 (2026-09-11 实测踩出来的):
  1. **栈只回答"当时在哪儿", 不回答"时间花在哪儿"。** 它是在超过阈值的**检测
     时刻**抓的快照, 真正吃时间的代码可能在这帧之前就跑完了 —— 表现为"片尾字幕"。
     所以看到 _pf_th.start() 卡 508ms 时, 别急着改那一行: 同机裸标定
     `threading.Thread(...).start()` 只有 0.8ms/次 (中位 0.14ms), 差了 500 倍,
     说明真正的耗时不在这儿。
  2. **这台机器的墙钟噪声地板很高。** 88% 内存占用 + MemCompression + Defender 的
     状态下, 单次 `font.measure()` 实测中位 1.02ms / 最大 605ms (正常机器是微秒级),
     Tcl 往返普遍慢两个数量级。所以:
       - 阈值 120ms 会稳定报出 4~6 次"卡顿", 换一次运行热区还会变 —— 这是环境;
       - 要分清"可复现的代码成本", 用**裸标定**(空进程里给候选调用计时)和
         cProfile 的 tottime (纯进程内 CPU 归属), 而不是只看这个栈;
       - 真正可信的信号是"同一帧在多次运行里反复出现", 以及"计数类指标"
         (调用次数、字符量、控件个数) —— 那些不受调度噪声影响。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("OPENCV_LOG_LEVEL", "OFF")

TICK = [time.perf_counter()]
STOP = [False]
HITS: list[tuple[float, str]] = []
STACK_DEPTH = 10


def _watch(threshold_s: float) -> None:
    """看门狗: 主线程时间戳停滞超阈值 → dump 它的栈 (只看主线程, 零侵入)"""
    main_id = threading.main_thread().ident
    while not STOP[0]:
        time.sleep(0.008)
        gap = time.perf_counter() - TICK[0]
        if gap > threshold_s:
            fr = sys._current_frames().get(main_id)
            if fr is not None:
                # 掐掉看门狗/本文件自身的栈帧, 只留业务调用链
                frames = traceback.format_stack(fr)[-STACK_DEPTH:]
                HITS.append((gap * 1000.0, "".join(frames)))
            time.sleep(0.4)   # 同一次卡顿别重复抓


def main() -> int:
    ap = argparse.ArgumentParser(description="主线程卡顿看门狗 (抓栈定位)")
    ap.add_argument("--seconds", type=float, default=10.0, help="观察窗口秒数 (默认 10)")
    ap.add_argument("--threshold", type=float, default=120.0, help="卡顿阈值 ms (默认 120)")
    ap.add_argument("--no-prebuild", action="store_true", help="不预建全部页面 (只看当前页)")
    ap.add_argument("--dump", type=int, default=4, help="最多打印几条栈 (默认 4)")
    ap.add_argument("--json", type=str, default="", help="把结果写到该 json 文件")
    args = ap.parse_args()
    thr_s = args.threshold / 1000.0

    print("=" * 70)
    print("主线程卡顿看门狗  |  窗口 %.0fs  阈值 %.0fms  %s"
          % (args.seconds, args.threshold, "不预建" if args.no_prebuild else "全页预建"))
    print("=" * 70, flush=True)

    from gui.main_window import App
    t0 = time.perf_counter()
    app = App(visible=False)
    t_shell = (time.perf_counter() - t0) * 1000

    n_pre = 0
    if not args.no_prebuild:
        t1 = time.perf_counter()
        for key in list(app._prebuild_order()):
            if key not in app._pages:
                app._ensure_page(key)
                n_pre += 1
        t_pre = (time.perf_counter() - t1) * 1000
    else:
        t_pre = 0.0

    app._prebuild_queue = []
    app.show()

    # ⚠ 打点顺序: 必须"先起看门狗线程, 再打时间戳"。
    #   旧顺序是 TICK[0] 先打点、然后 th.start() —— 于是**启动监控线程本身**的耗时
    #   会被算进第一个采样, 而那时监控线程还没开始轮询、栈也抓不到, 表现为
    #   "样本最大 911ms, 但抓到的栈只有 508ms" (2026-09-11 实测的仪器伪影)。
    samples: list[float] = []
    n_tick = [0]
    th = threading.Thread(target=_watch, args=(thr_s,), daemon=True, name="stall-watch")
    th.start()
    TICK[0] = time.perf_counter()

    # ⚠ 采样方式很关键: 不能用 `while: root.update()` 驱动。
    #   Tk 的 update() 会把**当前所有到期的 after 回调一口气跑完**, 于是应用里
    #   `after(30, ...)` 之类的分片构建会被当成"一次长卡顿"误报 (本工具第一版就踩了:
    #   8 秒窗口报出 16 次 >150ms, 栈全落在 h3 的分片建卡循环里 —— 那是测量伪影)。
    #   改成用 Tk 自己的定时器驱动, 语义才与真实 mainloop 一致:
    #   分片之间会让出事件循环, 真正占住循环的才会被记成卡顿。
    last = [time.perf_counter()]
    t_end = time.perf_counter() + args.seconds

    def _tick():
        now = time.perf_counter()
        samples.append(max(0.0, (now - last[0] - 0.004) * 1000))
        last[0] = now
        TICK[0] = now
        n_tick[0] += 1
        if now < t_end:
            app.root.after(4, _tick)
        else:
            app.root.quit()

    app.root.after(4, _tick)
    app.root.mainloop()

    STOP[0] = True
    time.sleep(0.1)

    samples.sort()
    med = samples[len(samples) // 2] if samples else 0.0
    p95 = samples[int(len(samples) * 0.95)] if samples else 0.0
    mx = samples[-1] if samples else 0.0
    bad = [s for s in samples if s > args.threshold]

    print("  App() 建外壳            %8.1f ms" % t_shell)
    print("  预建页面                    %3d 页 / %8.1f ms" % (n_pre, t_pre))
    print("  采样 %5d 次 (4ms 间隔)  |  中位 %5.1f   p95 %5.1f   最大 %7.1f ms"
          % (len(samples), med, p95, mx))
    print("  >%.0fms 的卡顿: %d 次 (累计 %.0f ms)" % (args.threshold, len(bad), sum(bad)))
    print("-" * 70)

    if not HITS:
        print("  ✅ 无超阈值卡顿 —— 栈里干净")
    for gap, st in HITS[:args.dump]:
        print("\n  ❌ 停滞 %.0f ms 时主线程在:" % gap)
        print(st)
    if len(HITS) > args.dump:
        print("  (还有 %d 条未打印)" % (len(HITS) - args.dump))

    if args.json:
        outp = Path(args.json)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(
            {"threshold_ms": args.threshold, "samples": len(samples),
             "median_ms": round(med, 2), "p95_ms": round(p95, 2), "max_ms": round(mx, 2),
             "stall_count": len(HITS),
             "stalls": [{"gap_ms": round(g, 1), "stack": s} for g, s in HITS]},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n  报告: %s" % outp)

    try:
        app._on_close()
    except Exception:
        pass
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
