# -*- coding: utf-8 -*-
"""卡片裁剪探针 v2 —— 在**真实映射**的窗口上量, 不靠肉眼, 也不靠未映射状态。

v1 的教训: `root.withdraw()` 时所有控件 `winfo_height()`=1, 卡片的圆角多边形
压根没画 (`rounded_card._draw` 在 h<12 时直接 return), 于是探针只认出 1 张卡、
clip 全是 0 —— **看着"没问题", 因为它什么都没测到**。典型的手段没覆盖目标。
v2 改为: 真窗口 + 真等待 (`after()` 定时器要真的跑过), 再逐卡量
"内容需要高 vs 卡可用高"。

用法: py -3.13 scripts/ui_card_probe.py dataset training env settings
"""
from __future__ import annotations

import os
import sys
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

PAGES = ["detection", "dataset", "training", "env", "settings"]


def cards_in(w, out):
    try:
        kids = w.winfo_children()
    except Exception:
        return
    for k in kids:
        try:
            if isinstance(k, tk.Canvas):
                sh = k.find_withtag("shape")
                if sh and k.type(sh[0]) == "polygon":
                    out.append(k)
        except Exception:
            pass
        cards_in(k, out)


def inner_of(cv):
    try:
        for i in cv.find_all():
            if cv.type(i) == "window":
                return cv.nametowidget(cv.itemcget(i, "window"))
    except Exception:
        pass
    return None


def label_of(cv):
    """给卡片起个能认出来的名字 (取 inner 里第一个有文字的 Label)。"""
    inner = inner_of(cv)
    if inner is None:
        return "?"

    def dig(w, d=0):
        if d > 3:
            return ""
        try:
            for k in w.winfo_children():
                if isinstance(k, tk.Label):
                    t = str(k.cget("text") or "").strip()
                    if t:
                        return t[:24]
                r = dig(k, d + 1)
                if r:
                    return r
        except Exception:
            pass
        return ""
    return dig(inner) or "(空)"


def settle(root, seconds=1.4, step=0.03):
    """真等待 —— after() 定时器 (摘要/预览/渐变) 要真的有机会跑。"""
    end = time.time() + seconds
    while time.time() < end:
        try:
            root.update()
        except Exception:
            return
        time.sleep(step)


def main():
    os.chdir(ROOT)
    from gui.main_window import App
    app = App(visible=True)
    root = app.root
    try:
        root.attributes("-topmost", True)
        root.lift()
    except Exception:
        pass
    settle(root, 1.6)

    keys = [a for a in sys.argv[1:] if not a.startswith("-")] or PAGES
    total_bad = 0
    for key in keys:
        try:
            app.show_page(key, animate=False)
        except Exception as ex:
            print(f"=== {key}: 切页失败 {ex}")
            continue
        settle(root, 1.5)
        page = app._pages.get(key)
        if page is None:
            print(f"=== {key}: 页面缺失")
            continue
        cards = []
        cards_in(page, cards)
        rows = []
        for cv in cards:
            try:
                inner = inner_of(cv)
                if inner is None:
                    continue
                ch = cv.winfo_height()
                ih = inner.winfo_height()
                rh = inner.winfo_reqheight()
                avail = ch - 22
                rows.append({
                    "y": cv.winfo_rooty() - page.winfo_rooty(),
                    "ch": ch, "ih": ih, "rh": rh, "clip": rh - avail,
                    "name": label_of(cv),
                })
            except Exception:
                pass
        bad = [r for r in rows if r["clip"] > 3]
        total_bad += len(bad)
        print(f"=== {key:<11} 卡片 {len(rows)} 张  被裁 {len(bad)} 张")
        for r in sorted(rows, key=lambda x: -x["clip"])[:5]:
            mark = "  <== 内容超出卡高!" if r["clip"] > 3 else ""
            print(f"      y={r['y']:<5} 卡高={r['ch']:<5} 内容实高={r['ih']:<5} "
                  f"内容需高={r['rh']:<5} clip={r['clip']:+5}{mark}  [{r['name']}]")
    print(f"\n合计被裁卡片: {total_bad}")
    try:
        root.destroy()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
