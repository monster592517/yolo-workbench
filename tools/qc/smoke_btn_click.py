"""按钮点击契约 —— 「`btn()` 造出来的按钮，点了必须响」。

为什么值得单独一个冒烟
----------------------
2026-09-22 实测 `gui.components.btn` 里：

    b.bind("<Button-1>", lambda e, c=cmd: c())          # 命令
    ...
    b.bind("<ButtonPress-1>", lambda e: b.config(...))  # 按下变暗（视觉反馈）

**Tk 里 `<Button-1>` 与 `<ButtonPress-1>` 是同一个事件序列** ⇒ 第二条
**把第一条覆盖掉** ⇒ **全站按钮变成死的**（只剩按下变暗的效果），而且
**不报任何错**：状态条照常、日志干净、冒烟全绿。

用户侧的表现：**"点了没反应"**、**"点击播放没有动画"** —— 极难查，
因为所有"看数据"的手段都正常，唯独那个回调没被调用。

判据
----
| # | 内容 | 期望 |
|---|---|---|
| B1 | `btn()` 造按钮 → 真发一次 `<Button-1>` | 命令**必须执行** |
| B2 | 该序列上的脚本条数 | **≥ 2**（命令 + 视觉各一条） |
| B3 | **注入式负对照**：用旧写法（视觉绑定不带 `add="+"`）重造一个 | 命令**必须不执行** |

★ B3 是这把尺子的自证：如果"旧写法"也能通过 B1，说明本冒烟根本测不出这个故障
⇒ **整表作废**（EXIT=2），不是 SKIP。

跑法
----
    python tools/qc/smoke_btn_click.py          # 需要装了 tkinter 的解释器
退出码：0 全如期 / 1 有未如期 / 2 **量具自证不过（整表作废）**
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import tkinter as tk                                                   # noqa: E402


def _pump(win, sec=0.35):
    t0 = time.time()
    while time.time() - t0 < sec:
        try:
            win.update()
        except Exception:                                            # noqa: BLE001
            break
        time.sleep(0.02)


def _click(w):
    w.event_generate("<Button-1>", x=6, y=6, when="now")


def _old_style_btn(parent, text, cmd):
    """**反面教材**：复刻修之前的写法（视觉绑定不带 `add="+"`）。"""
    b = tk.Label(parent, text=text, padx=10, pady=6, cursor="hand2")
    b.bind("<Button-1>", lambda e, c=cmd: c())
    b.bind("<ButtonPress-1>", lambda e: b.config(bg="#888"))     # ← 覆盖命令
    return b


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                                                # noqa: BLE001
        pass
    from gui.components import btn

    win = tk.Tk()
    win.title("smoke-btn-click")
    win.geometry("360x220+80+80")
    win.deiconify()
    _pump(win, 0.4)

    rows = []

    # ---- B1/B2 正：组件造的按钮必须真的会响 ----
    fired = []
    good = btn(win, "点我", lambda: fired.append(1))
    good.pack(pady=8)
    _pump(win, 0.3)
    n_scripts = str(good.bind("<Button-1>")).count("if {")
    _click(good)
    _pump(win, 0.4)
    rows.append(("B1", "btn() 造的按钮点了要执行命令", bool(fired),
                 "命令执行 = %s（False ⇒ 按钮是死的）" % bool(fired)))
    rows.append(("B2", "该序列上命令与视觉各留一条绑定", n_scripts >= 2,
                 "脚本条数 = %d（<2 ⇒ 有一条被覆盖了）" % n_scripts))

    # ---- B3 负对照（量具自证）：旧写法必须**不响** ----
    fired_old = []
    old = _old_style_btn(win, "旧写法", lambda: fired_old.append(1))
    old.pack(pady=8)
    _pump(win, 0.3)
    _click(old)
    _pump(win, 0.4)
    neg_ok = not fired_old
    rows.append(("B3", "负对照：旧写法（无 add）必须点不动", neg_ok,
                 "旧写法命令执行 = %s（应为 False；为 True ⇒ 这把尺子测不出该故障）"
                 % bool(fired_old)))

    print("按钮点击契约（`gui.components.btn`）")
    print("=" * 78)
    n_ok = 0
    for cid, desc, ok, detail in rows:
        print("[%s] %-4s %-34s —— %s" % ("OK  " if ok else "FAIL", cid, desc, detail))
        n_ok += 1 if ok else 0
    print("=" * 78)
    print("合计 %d 条：如期 %d / 未如期 %d" % (len(rows), n_ok, len(rows) - n_ok))

    try:
        win.destroy()
    except Exception:                                                # noqa: BLE001
        pass

    if not neg_ok:
        print("★ 量具自证不过 ⇒ **整表作废**：负对照都拦不住，说明本冒烟测不出该故障。")
        return 2
    if n_ok != len(rows):
        print("★ 有未如期 —— 先看 B1：命令没执行 = 按钮是死的（全站都会中招）。")
        return 1
    print("全如期：btn() 造的按钮确实会响，且负对照能拦下旧写法。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
