# -*- coding: utf-8 -*-
r"""`fitness_hardcoded_root.py` 的正/负对照。

本文件测三件事：

1. **计数判据**（`count_hardcoded`）看得见硬编码 —— 正对照必须 >0；
2. **增量判据**（`diff_against_baseline`）只在**涨了**的时候报 ——
   ★ 这是「冻结现状、只管增量」策略的核心，必须用人造数据把三种情况
   （涨 / 新增 / 减少）各测一遍。
3. ★ **量具跟着根走**（2026-09-18 补）——
   判据的 `NEEDLES` 必须由**现场 `ROOT`** 派生，不许焊死在某个历史路径上。

第 3 条是从一次真实故障里补出来的，值得写清楚
--------------------------------------------
搬迁（`D:\yolo_lab` → `<项目根>\01_yolo_lab`）之后，这个判据**睡着了**：
它数的还是旧根，而活代码里写的已经是新根。实测口径内：

    新根 134 个文件 / 217 处     ← 一处也看不见
    旧根   3 个文件 /   5 处     ← 全是守卫文件自己

后果不是"报错"，而是判据输出了一句**听起来像好消息的话**
（「有 134 个文件的硬编码减少了，可重新冻结」），
把"它已经看不见真根了"洗成了"它在变好"。

⇒ 所以本文件里最关键的对照组是 **②「当前根 ⇒ 必须数到」** 与
   **③「旧根 ⇒ 不许数到」**。两条一起，才证明量具是指向**现在**的。

为什么增量判据要单独测：本仓库的存量是三位数级别。
如果判据写错成"有硬编码就报"，它会永远 FAIL ⇒ 变成"永远红的灯" ⇒
按 `studio_doctor.py:2324-2328` 的话，**那会训练人忽略自检**。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fitness_common import ROOT, load_baseline  # noqa: E402
from fitness_hardcoded_root import (KEY, NEEDLES, count_hardcoded,  # noqa: E402
                                    diff_against_baseline, root_variants,
                                    selftest_needles)

RESULTS: list[tuple[str, bool, str]] = []

#: 夹具一律由**现场根**派生 —— 不在本文件里抄一条路径字面量。
#: （抄了就会和判据一起漂移；而且本文件里出现路径字面量本身就是它要禁止的那种写法。）
R = str(ROOT).replace("/", "\\")
RS = R.replace("\\", "/")
OLD_R = "D:\\yolo_lab"          # 搬迁前的旧根：只用来做**负对照**（它已不该被数到）


def chk(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, cond, detail))
    print(("  OK   " if cond else "  FAIL ") + name
          + (f"\n         {detail}" if detail and not cond else ""))


def mk(root: str, rel: str, content: str) -> None:
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _variants_via_subprocess(seed: str) -> str:
    """换一个 PYTHONHASHSEED 起新进程，取回 `NEEDLES` 的 repr。

    为什么要跨进程量：`NEEDLES` 由 set 派生，而**字符串哈希受 PYTHONHASHSEED 随机化**，
    同长度元素在不同进程里的 set 迭代顺序可能不同。
    计数本身不在乎顺序，但"同一把尺子两次跑出来必须一模一样"是判据的基本体面 ——
    顺序会漂的尺子，迟早会有人拿它做顺序相关的断言，然后得到一个只在某些进程里成立的结论。
    """
    env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONIOENCODING="utf-8")
    code = ("import sys; sys.path.insert(0, %r); "
            "import fitness_hardcoded_root as f; print(repr(f.NEEDLES))"
            % str(Path(__file__).resolve().parent))
    try:
        r = subprocess.run([sys.executable, "-X", "utf8", "-c", code],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, cwd=str(ROOT), timeout=120,
                           creationflags=0x08000000)     # Windows: 不弹控制台窗
        return (r.stdout or "").strip()
    except Exception as e:                               # noqa: BLE001
        return "SUBPROCESS-FAIL: %r" % (e,)


def main() -> int:
    print("== smoke_fitness_hardcoded ==")

    # ---------------------------------------------------------- 量具自检
    _ok, _bad = selftest_needles()
    chk("★量具自检 selftest_needles() 通过（非空 / 指向当前根 / 正负对照都能触发）",
        _ok, "; ".join(_bad))

    chk("量具派生自现场 ROOT：当前根原样形态必在 NEEDLES 里",
        R in NEEDLES, f"R={R!r}  NEEDLES={list(NEEDLES)}")
    chk("量具形态数 ≥ 4（反斜杠 / 正斜杠 / 转义 1 层 / 转义 2 层）",
        len(NEEDLES) >= 4, f"got={len(NEEDLES)}")
    chk("量具**不含**旧根（刻意不守旧路径 —— 取舍见 fitness_hardcoded_root.py 头部）",
        not any(OLD_R in n for n in NEEDLES), f"NEEDLES={list(NEEDLES)}")

    # ---- ★ 正对照：当前根的三种写法都要数到 ----
    t = tempfile.mkdtemp()
    mk(t, "src/a.py", 'ROOT = r"%s"\nX = "%s/x"\n' % (R, RS))
    got = count_hardcoded(t)
    chk("① ★正对照：反斜杠 + 正斜杠两种写法都要数到（同一文件 2 处）",
        got.get("src\\a.py") == 2, f"got={got}")

    t = tempfile.mkdtemp()
    mk(t, "scripts/b.bat", "set ROOT=%s\r\n" % R)
    got = count_hardcoded(t)
    chk("② ★正对照：.bat 里的硬编码也要数到（不只看 .py）",
        got.get("scripts\\b.bat") == 1, f"got={got}")

    # ---- ②b/②c 转义层数：只认一种写法 = 其余写法天然在视野外 ----
    t = tempfile.mkdtemp()
    mk(t, "tools/c.json", '{"root": "%s"}\n' % R.replace("\\", "\\\\"))
    got = count_hardcoded(t)
    chk("②b 文本内转义 1 次（.json 常见形态）也要数到",
        got.get("tools\\c.json") == 1, f"got={got}  (notepad 里看到 2 个反斜杠)")

    t = tempfile.mkdtemp()
    mk(t, "tools/d.json", '{"root": "%s"}\n' % R.replace("\\", "\\\\\\\\"))
    got = count_hardcoded(t)
    chk("②c 文本内转义 2 次（JSON-in-JSON / 正则）也要数到",
        got.get("tools\\d.json") == 1, f"got={got}  (notepad 里看到 4 个反斜杠)")

    t = tempfile.mkdtemp()
    mk(t, "src/e.py", "ROOT = %r\n" % R)          # repr 形态
    got = count_hardcoded(t)
    chk("②d repr 形态（Python 打印出来的那种）也要数到",
        got.get("src\\e.py") == 1, f"got={got}")

    # ---- ③ ★ 负对照：旧根不许被数到 ----
    t = tempfile.mkdtemp()
    mk(t, "src/legacy.py", 'ROOT = r"%s"\n' % OLD_R)
    got = count_hardcoded(t)
    chk("③ ★负对照：**旧根**不许被数到（判据只守当前根 —— 这是刻意取舍，不是漏）",
        got == {}, f"got={got}")

    # ---- ☆ 负对照：不该数的 ----
    t = tempfile.mkdtemp()
    mk(t, "src/ok.py", "from infrastructure import path_center\nROOT = path_center.get_root()\n")
    chk("④ ☆负对照：走 path_center 的真源写法 ⇒ 不得报",
        count_hardcoded(t) == {}, f"got={count_hardcoded(t)}")

    t = tempfile.mkdtemp()
    mk(t, "docs/x.md", "这里提到 %s 但是 docs/ 不在扫描范围\n" % R)
    chk("⑤ ☆负对照：docs/ 不在口径内 ⇒ 不得报（口径 = src/tools/scripts）",
        count_hardcoded(t) == {}, f"got={count_hardcoded(t)}")

    # 判据过宽也会产生噪音：换一个盘符的同名路径，不许命中
    t = tempfile.mkdtemp()
    mk(t, "src/other.py", "ROOT = %r\n" % ("Z:" + R[2:]))
    chk("⑥ ☆负对照：换盘符的同名路径 ⇒ 不得报（判据不许过宽）",
        count_hardcoded(t) == {}, f"got={count_hardcoded(t)}")

    # 点文件必须跳过（否则基线文件自己会被扫进来 → 自指）
    t = tempfile.mkdtemp()
    mk(t, "tools/qc/.hidden.json", '{"x": "%s"}\n' % R.replace("\\", "\\\\"))
    chk("⑦ 自指守卫：点文件（含 .fitness_baseline.json）必须跳过",
        count_hardcoded(t) == {}, f"got={count_hardcoded(t)}")

    # ---- ★ 增量判据三分支 ----
    # ⚠️ 键必须用 `\` 分隔 —— 与 `count_hardcoded()` 的真实产出保持一致
    #    （本 smoke 第一版用了 `/`，于是断言全对不上。这就是对照的价值。）
    cur = {"src\\a.py": 5, "src\\b.py": 2, "src\\c.py": 7}
    base = {"src\\a.py": 5, "src\\b.py": 1, "src\\c.py": 9}
    grew, added, shrank = diff_against_baseline(cur, base)
    chk("⑧ ★增量判据：涨的必须报（b.py 1→2）",
        len(grew) == 1 and grew[0][0] == "src\\b.py", f"grew={grew}")
    chk("⑨ ★增量判据：新文件必须报（d.py）",
        (lambda c2, b2: len(diff_against_baseline(c2, b2)[1]) == 1)(
            {**cur, "src\\d.py": 3}, base),
        "新文件漏报 ⇒ 新增硬编码会静默通过")
    chk("⑩ 减少的不该报成违规（c.py 9→7 是好消息）",
        len(shrank) == 1 and shrank[0][0] == "src\\c.py", f"shrank={shrank}")

    # 完全一致 ⇒ 三个都空（这是"冻结"能成立的前提）
    e, a, s = diff_against_baseline(cur, cur)
    chk("⑪ ★冻结语义：与基线完全一致 ⇒ 什么都不报（存量不算违规）",
        e == [] and a == [] and s == [], f"grew={e} added={a} shrank={s}")

    # ---- 量的自洽：派生函数本身 ----
    chk("⑫ root_variants 幂等（同一根两次派生结果全等）",
        root_variants(R) == root_variants(R))
    _a = _variants_via_subprocess("0")
    _b = _variants_via_subprocess("12345")
    chk("⑬ ★NEEDLES 跨进程可复现（换 PYTHONHASHSEED 输出必须一致）",
        _a == _b and len(_a) > 50,
        f"seed=0 → {_a[:140]!r}\n         seed=12345 → {_b[:140]!r}")

    # ---- 哨兵 ----
    chk("⑭ 哨兵：空目录 ⇒ 返回 0 且不报",
        count_hardcoded(tempfile.mkdtemp()) == {})

    # ---- 真仓库现状 ----
    bl = load_baseline()
    base_real = bl.get(KEY) or {}
    cur_real = count_hardcoded(ROOT)
    total = sum(cur_real.values())
    print(f"  --   真仓库：{len(cur_real)} 个文件 / {total} 处"
          f"（基线 {len(base_real)} 个 / {bl.get(KEY + '_total', '?')} 处，"
          f"冻于 {bl.get(KEY + '_frozen_at', '?')}）")
    chk("⑮ 真仓库：没有基线以外的新增（冻结状态）",
        not bl.get(KEY) or diff_against_baseline(cur_real, base_real)[:2] == ([], []),
        "若失败：说明有新增硬编码，应按判据输出改回真源写法")
    chk("⑯ ★换尺子检测：基线的根与当前根一致（不一致则两个读数不可比）",
        bl.get(KEY + "_root") in (None, str(ROOT)),
        f"基线根={bl.get(KEY + '_root')!r}  当前根={str(ROOT)!r} "
        "⇒ 需重新冻结并在记录里写明『换了尺子』")

    n_ok = sum(1 for _, c, _ in RESULTS if c)
    print(f"\n对照结果 {n_ok}/{len(RESULTS)} 通过")
    if n_ok != len(RESULTS):
        print("FAIL  smoke_fitness_hardcoded")
        return 1
    print("PASS  smoke_fitness_hardcoded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
