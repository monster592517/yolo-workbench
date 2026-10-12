"""分段控件「激活段被补间涂黑」判据 —— 悬停后点击，激活段必须还是 accent 底色。

为什么单独一个冒烟
------------------
2026-09-23 用户报：「**子页面切换的时候对应的按键会彻底黑掉**。」

根因（实测复现）：`SegmentedControl` 的激活段配色是 **accent 实底 + `C.bg` 近黑字**
（`bg=#38BDF8` / `fg=#0A0A0A`），而 `<Enter>` 触发的 hover 补间目标是
**`nav_hover_bg=#161616`（也近黑）**。用户"悬停 → 点击"这个再正常不过的动作序列下：

    Enter 起补间(→#161616)  →  set() 直接配成 #38BDF8  →  **补间继续写帧，最后落在近黑**
    ⇒ 近黑底 + 近黑字 = 整块看不见（"彻底黑掉"）

修法（`gui.components.SegmentedControl`，三处）：
  ① `set()` 先 `motion_kit.cancel(seg)` 掐掉所有在飞的补间（⚠ `cancel` **只接一个参数**，
     第一版写成 `cancel(s, "bg")` ⇒ TypeError 被吞 ⇒ 取消没发生、看着像没修）
  ② `_hover()` **跳过激活段**（已选中的那段本来就不该再变 hover 色）
  ③ `_settle()`：越过补间窗口后兜底再刷一次配色

判据
----
| # | 内容 | 期望 |
|---|---|---|
| S1 | 模拟"悬停到某段 → 点击它" | 该段底色 == accent（**不是**近黑） |
| S2 | 激活段的**可读性** | 底/字亮度差 ≥ 0x40（防"深字压深底"） |
| S3 | **注入式负对照**：把 ① ② ③ 撤掉（还原旧行为）后同一套模拟 | 必须**黑掉** |

★ S3 是这把尺子的自证：还原旧行为后都测不出异常 ⇒ **整表作废**（EXIT=2）。
★ S1/S2 两条一起才够 —— 单看"是不是 accent"会漏掉**别的**导致不可读的组合。

跑法：`PY tools/qc/smoke_segmented_fill.py`；退出码 0 全如期 / 1 未如期 / 2 量具自证不过
"""
from __future__ import annotations

import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import tkinter as tk                                                   # noqa: E402


def _lum(hexcolor: str) -> int:
    h = str(hexcolor).lstrip("#")
    return int(h[0:2], 16) + int(h[2:4], 16) + int(h[4:6], 16)


def _pump(win, sec):
    t0 = time.time()
    while time.time() - t0 < sec:
        try:
            win.update()
        except Exception:                                            # noqa: BLE001
            break
        time.sleep(0.01)


def _sim(seg, target):
    """模拟真实鼠标：**先悬停到目标段，再点它**（顺序不能反 —— 反了就复现不出来）。"""
    seg._segs[target].event_generate("<Enter>")
    _pump(seg, 0.05)
    seg.set(target, fire=True)


def _run_case(root, patch_old: bool, hold=0.7):
    """建一个 4 段控件，悬停第 4 段后点它，返回 (底色, 字色, 检出的黑)。"""
    for w in root.winfo_children():
        w.destroy()
    root.update()
    from gui.components import segmented
    from gui.motion_kit import animate
    seg = segmented(root, ["① 甲", "② 乙", "③ 丙", "④ 丁"], index=0,
                    command=lambda i: None)
    seg.pack(fill="x", padx=10, pady=10)
    _pump(seg, 0.3)
    if patch_old:
        # 把三处修复**逐条撤掉**，还原成出故障的版本
        def old_hover(self, i, on):
            try:
                s = self._segs[i]
                base, fg, hover = self._target(i)
                animate(s, "bg", hover if on else base, ms=140)
            except Exception:                                        # noqa: BLE001
                pass

        def old_set(self, i, fire=False):
            if not (0 <= i < len(self._segs)) or i == self._index:
                return
            self._index = i
            self._apply(animate_first=False)     # 不 cancel、不 settle

        def noop_settle(self, delay=220):
            return None

        seg._hover = types.MethodType(old_hover, seg)
        seg.set = types.MethodType(old_set, seg)
        seg._settle = types.MethodType(noop_settle, seg)
    _sim(seg, 3)
    _pump(seg, hold)                     # 越过 140ms 补间窗口
    w = seg._segs[3]
    bg, fg = str(w.cget("bg")), str(w.cget("fg"))
    dark = _lum(bg) < 0x30 * 3 and _lum(fg) < 0x30 * 3
    return bg, fg, dark


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                                                # noqa: BLE001
        pass
    root = tk.Tk()
    root.title("smoke-segmented-fill")
    root.geometry("760x200+70+70")
    root.deiconify()
    _pump(root, 0.4)

    bg_old, fg_old, dark_old = _run_case(root, patch_old=True)
    bg_new, fg_new, dark_new = _run_case(root, patch_old=False)

    rows = [
        ("S1", "悬停后点击 ⇒ 激活段底色必须是 accent（不是近黑）",
         _lum(bg_new) > 0x60 * 3,
         "激活段 bg=%s（旧行为下是 %s）" % (bg_new, bg_old)),
        ("S2", "激活段可读性：底/字亮度差 ≥ 0x40（防深字压深底）",
         abs(_lum(bg_new) - _lum(fg_new)) >= 0x40,
         "bg=%s ⟷ fg=%s ⇒ 差 %d" % (bg_new, fg_new, abs(_lum(bg_new) - _lum(fg_new)))),
        ("S3", "负对照：撤掉修复后同一套模拟**必须**黑掉", bool(dark_old),
         "旧行为 bg=%s fg=%s ⇒ %s" % (bg_old, fg_old,
                                      "黑掉（如预期）" if dark_old else "★ 竟然没黑")),
    ]
    print("分段控件「激活段被补间涂黑」判据")
    print("=" * 78)
    n_ok = 0
    for cid, desc, ok, detail in rows:
        print("[%s] %-4s %-44s —— %s" % ("OK  " if ok else "FAIL", cid, desc, detail))
        n_ok += 1 if ok else 0
    print("=" * 78)
    print("合计 %d 条：如期 %d / 未如期 %d" % (len(rows), n_ok, len(rows) - n_ok))

    try:
        root.destroy()
    except Exception:                                                # noqa: BLE001
        pass
    if not rows[2][2]:
        print("★ 量具自证不过（还原旧行为也测不出异常）⇒ **整表作废** EXIT=2。")
        return 2
    if n_ok != len(rows):
        print("★ 有未如期 —— 先看 S1：激活段底色不是 accent 就是被补间涂了。")
        return 1
    print("全如期：悬停后点击，激活段仍是 accent 底色且可读；负对照能拦下旧行为。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
