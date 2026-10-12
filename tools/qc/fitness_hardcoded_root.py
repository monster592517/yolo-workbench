# -*- coding: utf-8 -*-
r"""适应度函数 ③：硬编码项目根路径的数量**不得增长**（冻结现状，只管增量）。

为什么有它 —— 以及为什么不是"一次清干净"
--------------------------------------
口径 = `src/ tools/ scripts/` 下的 .py/.md/.json/.yaml/.bat/.ps1/.cmd（排除第三方）
里**硬编码了项目根**的处数。这个数**一直在涨**。

**这些处里绝大多数功能上是正确的。** 一次性改完的代价：

- 改几百处 = 几百次"可能改坏"的机会，而它们**现在都工作**；
- 本机是**环境态驱动**的项目（换机器、换盘位很常见），
  但抄一遍路径的地方**已经存在**，改它们不会让今天更好，只会让今天更险。

⇒ 真正的风险不是"现在有 N 处"，而是"**它还在涨**"。
   所以策略是：**把现状冻结成基线，只对增量报警。**

⚠️ 本文件**刻意不写任何具体处数**。写下的那一刻它就开始烂 ——
   本仓真实教训：这份 docstring 原来写着"137 个文件 / 222 处"，
   2026-09-18 搬迁之后它已经是一句**假数**（现值见基线文件与运行输出）。
   要看现值：跑一次脚本，或读 `.fitness_baseline.json` 的 `frozen_at`。

量具必须跟着根走（2026-09-18 真实事故：这个守卫曾经"睡着了"）
----------------------------------------------------------
搬迁（`D:\yolo_lab` → `<项目根>\01_yolo_lab`）之后，本判据曾一度失效：

| 口径 | 口径内命中 |
|---|---|
| 新的真根 | **134 个文件 / 217 处** ← 活代码里正在写 |
| 写死的旧根 | 3 个文件 / 5 处 ← **全是守卫文件自己** |

而外部基线仍冻在旧根的量值上，于是判据当时的输出是
「有 134 个文件的硬编码**减少**了（好事，可重新冻结）」——
一句听起来像好消息的话，**把"它已经看不见真根了"洗成了"它在变好"**。

⇒ 所以本文件的 `NEEDLES` **由现场的 `ROOT` 派生**，绝不写死：
   根一移动，量具跟着移动，这一类漂移从结构上不可能再发生。
   写死 = 把量具焊在旧根上；而判据量不到的东西会一路报绿 ——
   本仓最贵的一次误判正是这个形状（见 `tools/qc/path_guard.py` 头部那次 junction 事故）。
   运行前有 `selftest_needles()` 正负对照把关，跑不过就拒绝出报告。

⚠️ 已知、已量、**刻意不做**（别顺手加回来）：
   旧根 `D:\yolo_lab` **不作为判据**。它现已不在任何活代码里，
   口径内只剩 3 个文件 5 处，且全是守卫文件自己
   （记录那次事故的史实、以及被参数化的默认标签）。
   为 5 处文档措辞养一张永久豁免表 ⇒ 收益近零、噪音确定。
   真正该守的是"**当前**根不许被写死" —— 那是活的，而且它一直在涨。

基线为什么不写在这个脚本里
------------------------
手册 **#200**：*"期望值绝对不许写进判据脚本"*。
写进去的那一刻，它就成了过期快照 —— 生产默认一改，
从**现场现值**读的基线反而会被判成失败。

所以基线存在外部文件 `tools/qc/.fitness_baseline.json`，本脚本只负责比对。

⚠️ **换过尺子之后必须重新冻结，并且必须在冻结点写明"这次是换了尺子"。**
   新旧基线默认**不可比**：基线是"某个量具在某个时刻的读数"，
   量具变了（改 NEEDLES / 改扫描口径 / 项目根移动）就换了尺子。
   拿新尺子的读数去比旧尺子的基线，得到的是**两个工具的差**，不是漂移量。

用法
----
    python tools/qc/fitness_hardcoded_root.py            # 检查（CI/自检用）
    python tools/qc/fitness_hardcoded_root.py --freeze   # 重新冻结基线（人工，改动后）
    python tools/qc/fitness_hardcoded_root.py --freeze --note "换了尺子：…"

`--freeze` 只在**确认当前状态更好或同等**时用；涨了就不该冻结（那是掩盖漂移）。
`--note` 是给**未来的人**看的（含未来的你）：这一行会在每次检查时挨着读数打出来。
冻结时顺手写一句"为什么"，比事后去翻变更记录便宜得多。

正/负对照见 `tools/qc/smoke_fitness_hardcoded.py`。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fitness_common import (ROOT, SKIP_DIRS, VENDOR_PREFIXES, Reporter,  # noqa: E402
                            guard_self_reference, load_baseline, read_text, save_baseline)

KEY = "hardcoded_root"

# 扫这些扩展名 —— 硬编码路径不只出现在 .py 里（.bat/.ps1/.json 同样会烂）
EXTS = (".py", ".md", ".json", ".yaml", ".yml", ".bat", ".ps1", ".cmd", ".toml", ".ini")

# 口径：只扫本项目源码树（与 fitness_common.SRC_TREES 同义，这里用前缀形态便于判文件）
SCAN_PREFIXES = ("src\\", "tools\\", "scripts\\")


def root_variants(root) -> tuple[str, ...]:
    r"""一条根路径在源码文本里的常见形态（反斜杠 / 正斜杠 / 转义 1~2 层 / 小写）。

    为什么必须逐层转义 —— 同一个根在不同载体里写法不同：
    `.py` 里是 1 层（或 `r"..."`），`.json` 里是 2 层，JSON-in-JSON / 正则里是 4 层。
    **只认一种写法 ⇒ 其余写法天然在视野外**，判据会在那些文件上假绿。
    （同族事故见 `fitness_common.py` 头部引的 `studio_doctor.py:2578` 点名清单。）

    为什么带小写 —— `src/infrastructure/path_guard.py::_abs_root_strings`
    自己在生成别名时都带 lowercase，说明"路径被整条小写抄一遍"是真实存在的写法。

    长串优先排序：先匹配长形态，短形态不会在长形态内部二次命中
    （1 层针 `D:\A` 在 `D:\\A` 里对不上 —— 第三个字符是 `\` 不是 `A`），因此不会重复计数。
    """
    s = str(root).replace("/", "\\")
    forms = {
        s,                            # 1 层反斜杠（最常见）
        s.replace("\\", "/"),         # 正斜杠
        s.replace("\\", "\\\\"),      # 文本内转义 1 次（= 实际 2 个反斜杠）
        s.replace("\\", "\\\\\\\\"),  # 文本内转义 2 次（= 实际 4 个反斜杠）
    }
    forms |= {f.lower() for f in tuple(forms)}
    # 全序排序（长度降序，同长度按字典序）—— 不靠 set 的迭代顺序：
    # 字符串哈希受 PYTHONHASHSEED 随机化影响，同长度元素在**不同进程**里顺序会变。
    # 计数本身不在乎顺序，但"同一把尺子两次跑出来必须一模一样"是判据的基本体面。
    return tuple(sorted(forms, key=lambda s: (-len(s), s)))


# ★ 由现场 ROOT 派生，不写死 —— 根移动，量具跟着移动。理由见文件头 "量具必须跟着根走"。
NEEDLES = root_variants(ROOT)


def selftest_needles() -> tuple[bool, list[str]]:
    """量具自检：NEEDLES 必须**非空、指向当前根、且真的能被触发**。

    纪律与 `_selftest_free_gb` / `rewrite_refs._selftest_tier` 同款：
    跑不过就**拒绝出报告** —— 否则报告会"看着正常"，而结论是错的。

    这里同时给正对照与负对照：
    - 正：造一处"当前根的真硬编码"，必须被认出；
    - 负：换一个盘符的同名路径，**不许**被认出（否则是判据太宽，会产生噪音）。
    """
    bad: list[str] = []
    flat = str(ROOT).replace("/", "\\")

    if not NEEDLES:
        bad.append("NEEDLES 为空 ⇒ 判据永远报绿（不可能失败的判据）")
    if flat not in NEEDLES:
        bad.append(f"NEEDLES 里没有当前根的原样形态 {flat!r} ⇒ 量具没跟着根走")

    # 正对照：Python repr 出来的路径（实际 2 个反斜杠）必须能被数到
    pos = "ROOT = %r\n" % flat
    if not any(n in pos for n in NEEDLES):
        bad.append("正对照失败：造一处真硬编码，NEEDLES 认不出来 ⇒ 量具失效")

    # 负对照：换盘符的同名路径不许命中
    neg = "ROOT = %r\n" % ("Z:" + flat[2:])
    if any(n in neg for n in NEEDLES):
        bad.append("负对照失败：换盘符的路径也被判成硬编码 ⇒ 判据过宽，会产生噪音")

    return (not bad), bad


def count_hardcoded(root: Path | str, exts=EXTS) -> dict[str, int]:
    """纯函数：返回 `{rel_path: 出现次数}`，只含**非零**的文件。

    `root` 可注入（负对照要喂假仓库）。
    跳过目录/第三方清单都从 `fitness_common` 取 —— **不在本文件里再抄一份**：
    抄一份就会漂移，而"排除就是盲区"（见 `fitness_common.py` 的 VENDOR_PREFIXES 注释）。
    """
    root = Path(root)
    out: dict[str, int] = {}
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            if fn.startswith("."):          # 跳过点文件（含 .fitness_baseline.json）
                continue
            if not fn.lower().endswith(tuple(exts)):
                continue
            p = Path(dp) / fn
            try:
                rel = str(p.relative_to(root)).replace("/", "\\")
            except ValueError:
                continue
            if not rel.startswith(SCAN_PREFIXES):
                continue
            if rel.startswith(VENDOR_PREFIXES):
                continue
            if guard_self_reference(rel):
                continue
            txt = read_text(p)
            n = sum(txt.count(x) for x in NEEDLES)
            if n:
                out[rel] = n
    return out


def diff_against_baseline(cur: dict[str, int], base: dict[str, int]):
    """纯函数：返回 `(增量, 新增文件, 减少的文件)`。三个都是 `[(rel, cur, base)]`。"""
    grew, added, shrank = [], [], []
    for rel, n in cur.items():
        b = base.get(rel, 0)
        if b == 0:
            added.append((rel, n, 0))
        elif n > b:
            grew.append((rel, n, b))
    for rel, b in base.items():
        n = cur.get(rel, 0)
        if n < b:
            shrank.append((rel, n, b))
    return (sorted(grew, key=lambda x: -(x[1] - x[2])),
            sorted(added, key=lambda x: -x[1]),
            sorted(shrank, key=lambda x: x[1] - x[2]))


def main() -> int:
    argv = sys.argv[1:]
    freeze = "--freeze" in argv
    # `--note "..."`：这次为什么冻（人写，不写死在代码里）
    note = ""
    if "--note" in argv:
        _i = argv.index("--note")
        if _i + 1 < len(argv):
            note = argv[_i + 1]
    rep = Reporter("fitness_hardcoded_root")

    # ★ 先跑量具自检。跑不过就**拒绝出报告** ——
    #   否则报告会"看着正常"，而结论是错的（本仓既有纪律，见 selftest_needles 的 docstring）。
    ok, bad = selftest_needles()
    if not ok:
        rep.fail("量具自检未通过 ⇒ 拒绝出报告", "\n".join("        " + b for b in bad))
        return 2                       # 2 = ERROR（判据自己跑不起来），**不是** 1（架构漂移）
    rep.ok(f"量具自检通过：{len(NEEDLES)} 种形态，全部指向当前根 {ROOT}")

    cur = count_hardcoded(ROOT)
    cur_total = sum(cur.values())
    bl = load_baseline()
    base = bl.get(KEY)

    if freeze:
        rep.ok(f"重新冻结基线：{len(cur)} 个文件 / {cur_total} 处")
        bl[KEY] = cur
        bl[KEY + "_total"] = cur_total
        import datetime
        bl[KEY + "_frozen_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # 把"这次是拿哪把尺子量的"一并冻进去 —— 否则将来无法判断两个读数可不可比
        bl[KEY + "_root"] = str(ROOT)
        bl[KEY + "_needles"] = len(NEEDLES)
        if note:
            bl[KEY + "_note"] = note
        save_baseline(bl)
        return rep.finish()

    if not base:
        rep.warn(
            f"还没有基线（当前 {len(cur)} 个文件 / {cur_total} 处）",
            "首次使用请跑：python tools/qc/fitness_hardcoded_root.py --freeze\n"
            "⚠ 这不是「通过」—— 只是「还没建立比较基准」。")
        rep.skip("无基线 ⇒ 本次没有做任何比对")
        return rep.finish()

    base_total = bl.get(KEY + "_total", sum(base.values()))
    grew, added, shrank = diff_against_baseline(cur, base)

    rep.ok(f"当前 {len(cur)} 个文件 / {cur_total} 处（基线 {len(base)} 个 / {base_total} 处，"
           f"冻结于 {bl.get(KEY + '_frozen_at', '?')}）")

    # 冻结点为什么冻 —— 直接放在读数旁边，省得人再去翻变更记录
    if bl.get(KEY + "_note"):
        rep.ok(f"基线备注：{bl[KEY + '_note']}")

    # ★ 换过尺子就**必须**先重新冻结，否则下面那些"涨/跌"是两把尺子的差，不是漂移量。
    _b_root = bl.get(KEY + "_root")
    _b_needles = bl.get(KEY + "_needles")
    if _b_root is not None and _b_root != str(ROOT):
        rep.warn("基线是在**另一个项目根**下冻的 ⇒ 本行下面的涨/跌不可直接当漂移量",
                 f"基线根={_b_root}  当前根={ROOT}\n"
                 "请先确认状态，再跑 --freeze，并在改动记录里写明「换了尺子」。")
    if _b_needles is not None and _b_needles != len(NEEDLES):
        rep.warn(f"基线是用 {_b_needles} 种形态冻的，现在是 {len(NEEDLES)} 种 ⇒ 也换了尺子",
                 "同样：不可直接比较，需重新冻结并注明。")

    if added or grew:
        lines = []
        for rel, n, b in added:
            lines.append(f"        + {rel}  ({n} 处，新文件)")
        for rel, n, b in grew:
            lines.append(f"        ↑ {rel}  {b} → {n}  (+{n - b})")
        rep.fail(
            f"硬编码项目根 {ROOT} 的处数增加了：{len(added)} 个新文件 / "
            f"{len(grew)} 个文件变多（总 {base_total} → {cur_total}）",
            "新增处应改用 path_center / infrastructure.config 之类的真源。\n"
            "（存量不要求清 —— 冻结策略见本文件 docstring）\n" + "\n".join(lines[:25]))

    if shrank:
        rep.warn(f"有 {len(shrank)} 个文件的硬编码**减少**了（好事）",
                 "可考虑重新冻结基线：python tools/qc/fitness_hardcoded_root.py --freeze\n"
                 + "\n".join(f"        ↓ {r}  {b} → {n}" for r, n, b in shrank[:10]))
    elif not (added or grew):
        rep.ok("没有新增硬编码（存量按冻结策略不动）")

    return rep.finish()


if __name__ == "__main__":
    sys.exit(main())
