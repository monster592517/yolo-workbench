# -*- coding: utf-8 -*-
"""smoke_diagram_visual_gate.py — `tools/qc/diagram_visual_gate.py` 的正/负对照。

**改 `rows_from_receipt` / `judge` / `pick_browser` 的判据之前，先跑这个。**

它守的是两条真实教训（2026-09-17，维修手册 #198）：

1. **一个宽度有两张截图（亮/暗主题各一张），回执里是两条同 `width` 的记录。**
   按 `width` 做 dict key ⇒ **后写覆盖先写**。若恰好"暗的那张糊、亮的清楚"、
   且亮的排在后面 ⇒ 覆盖后**读出通过**。这是**假绿**，不是漏判。
   所以这里最关键的负对照是那条**故意复现旧写法**的
   `_old_rows_by_width()`：它必须在这一格给出 pass，而新判据必须 fail。

2. **`skipped` 不是 `pass`。** archify 在找不到浏览器时给
   `status: skipped` + **退出码 2**，日志长得像"没什么可看的"。
   拿不到读数 = **不知道**，不是"没问题" ⇒ 全部 skipped 必须退 **2**，绝不当 0。

用法：`python tools/qc/diagram_visual_gate.py` 之外单独跑
      `python tools/qc/smoke_diagram_visual_gate.py`   （exit 0 = 全过）
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diagram_visual_gate as g  # noqa: E402

checks: list[tuple[str, object, object]] = []


def chk(name: str, got: object, want: object) -> None:
    checks.append((name, got, want))


# ---------------------------------------------------------------- 造回执
def shot(width, theme, px, read_ok, ok=True, ox=False, oy=False, req=6):
    return {"width": width, "theme": theme, "minimumProjectedNodeTextPx": px,
            "minimumRequiredNodeTextPx": req, "readabilityOk": read_ok,
            "ok": ok, "overflowX": ox, "overflowY": oy}


def receipt(*shots):
    return {"status": "pass", "captures": {"status": "pass", "screenshots": list(shots)}}


# ★ 核心样本：1440 下 **暗的糊(4.5) / 亮的清楚(7.0)**，且**亮的排在后面**。
#   任何"后写覆盖"的写法都会读出 7.0 ⇒ 假绿。
TRAP = receipt(
    shot(1440, "dark", 4.5, False),
    shot(1440, "light", 7.0, True),
    shot(2048, "light", 9.0, True),
    shot(2048, "dark", 9.0, True),
)

# 干净的对照组（两宽 × 两主题全达标）—— 用来验"确实给 0"那一格。
CLEAN = receipt(
    shot(1440, "light", 7.2, True),
    shot(1440, "dark", 7.2, True),
    shot(2048, "light", 9.0, True),
    shot(2048, "dark", 9.0, True),
)

# ---------------------------------------------------------------- 双主题取最小
_rows = g.rows_from_receipt("X.html", TRAP)
_w1440 = next(r for r in _rows if r["width"] == 1440)
_2048 = next(r for r in _rows if r["width"] == 2048)
chk("正对照 宽度去重后是 2 行", len(_rows), 2)
chk("★负对照 双主题取**最小**（4.5 不是 7.0）", _w1440["min_px"], 4.5)
chk("★负对照 有一张糊 ⇒ readability_ok 必须 False", _w1440["readability_ok"], False)
chk("★负对照 该行 status 必须 fail", _w1440["status"], "fail")
chk("负对照 两个主题都记下来（便于定位是哪张糊）", sorted(_w1440["themes"]), ["dark", "light"])
chk("正对照 两主题都达标的那行仍是 pass", _2048["status"], "pass")
chk("正对照 门槛取自回执（req=6）", _w1440["required_px"], 6)

# ★ 负对照（核心）：把**旧写法**就地抄一遍 —— 按 width 建 dict，后写覆盖。
#   它在 TRAP 这一格给的是 pass，也就是那条假绿。有这条，才证明新判据
#   是**多量了一张截图**，而不是把本来就对的东西换个写法。
def _old_rows_by_width(rcpt):
    out = {}
    for s in rcpt["captures"]["screenshots"]:
        out[s["width"]] = s          # ← 后写覆盖先写
    return out


_old = _old_rows_by_width(TRAP)
chk("★负对照 旧写法(按 width 覆盖) 读出 7.0 = 那条假绿", _old[1440]["minimumProjectedNodeTextPx"], 7.0)
chk("★负对照 旧写法读出的 readabilityOk 是 True（假通过）", _old[1440]["readabilityOk"], True)
chk("★负对照 新判据在同一格给 False（与旧写法相反）", _w1440["readability_ok"], False)

# 亮暗顺序反过来也必须给同一个结论（不许依赖回执里的顺序）
TRAP_REV = receipt(
    shot(1440, "light", 7.0, True),
    shot(1440, "dark", 4.5, False),
    shot(2048, "light", 9.0, True),
)
chk("未改动 主题顺序颠倒 ⇒ 结论不变",
    g.rows_from_receipt("X.html", TRAP_REV)[0]["min_px"], 4.5)

# containment 同理：任一主题 overflow 就算 fail
_rows_ovf = g.rows_from_receipt("Y.html", receipt(shot(1440, "light", 7.0, True, ox=False, oy=False),
                                                  shot(1440, "dark", 7.0, True, ox=False, oy=True)))
chk("负对照 一个主题 overflowY ⇒ containment_ok False", _rows_ovf[0]["containment_ok"], False)
chk("负对照 overflowY 被记下来", _rows_ovf[0]["overflow_y"], True)
chk("负对照 overflowX 没被顺手记脏", _rows_ovf[0]["overflow_x"], False)

# ---------------------------------------------------------------- skipped 不许当通过
SKIP = {"status": "skipped", "captures": {"screenshots": [
    shot(1440, "light", None, None), shot(2048, "light", None, None)]},
    "error": "Chrome or Chromium is unavailable."}
_srows = g.rows_from_receipt("Z.html", SKIP)
chk("正对照 skipped 回执 ⇒ 行 status=skipped", _srows[0]["status"], "skipped")
chk("正对照 skipped 行 min_px 是 None（不是 0，也不是 6）", _srows[0]["min_px"], None)

_rc, _f = g.judge(_srows)
chk("★负对照 全 skipped（无浏览器）⇒ 退出码 **2**", _rc, 2)
chk("★负对照 退出码 2 不是 0（不许当通过）", _rc == 0, False)
chk("负对照 说了"+"未运行"+"这几个字", any("未运行" in x for x in _f), True)

# 空回执也不能当通过
chk("★负对照 空行列表 ⇒ 2", g.judge([])[0], 2)
chk("负对照 空回执不产出任何行（没有读数就是没有行）", g.rows_from_receipt("E.html", {}), [])
chk("★负对照 空回执进 judge ⇒ 2（不是 0）", g.judge(g.rows_from_receipt("E.html", {}))[0], 2)

# ---------------------------------------------------------------- judge 三态
_rc_ok, _f_ok = g.judge(g.rows_from_receipt("X.html", CLEAN))
chk("正对照 全部达标 ⇒ 0", _rc_ok, 0)
chk("正对照 达标时没有抱怨", len(_f_ok), 0)
chk("正对照 干净样本两宽都过", [r["status"] for r in g.rows_from_receipt("X.html", CLEAN)],
    ["pass", "pass"])

_rc_bad, _f_bad = g.judge(g.rows_from_receipt("X.html", TRAP))
chk("负对照 有图不达标 ⇒ 1", _rc_bad, 1)
chk("负对照 抱怨里带图名与宽度", any("X.html" in x and "1440" in x for x in _f_bad), True)
chk("负对照 抱怨里带实测值", any("4.50" in x for x in _f_bad), True)

# 一半 skipped 一半过 ⇒ 1（不是 0，也不是 2）：**部分未运行也是缺口**
_rc_mix, _f_mix = g.judge(g.rows_from_receipt("M.html", CLEAN)[:1] + g.rows_from_receipt("N.html", SKIP))
chk("★负对照 一半未运行 ⇒ 1（不许因为"+"有读数"+"就给 0）", _rc_mix, 1)
chk("负对照 混用时 2 与 1 必须不同", _rc_mix == 2, False)
chk("负对照 混用时有 3 行读数（1 过 + 2 未运行）", len(g.rows_from_receipt("M.html", CLEAN)[:1]
                                                   + g.rows_from_receipt("N.html", SKIP)), 3)
chk("负对照 混用时 3 行里只有 1 行有读数",
    sum(1 for r in (g.rows_from_receipt("M.html", CLEAN)[:1]
                    + g.rows_from_receipt("N.html", SKIP)) if r["min_px"] is not None), 1)

# containment fail 但 readability pass ⇒ 仍判 1（两者是**两个**判据）
_rc_c, _f_c = g.judge(g.rows_from_receipt("C.html", receipt(shot(1440, "light", 7.0, True, oy=True))))
chk("★负对照 containment 单独挂也要报 1（老图状态图就是这形态）", _rc_c, 1)
chk("负对照 抱怨点名 overflowY", any("overflowY" in x for x in _f_c), True)

# ---------------------------------------------------------------- 找浏览器
_OK_PATH = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
chk("正对照 env 指定且存在 ⇒ 用它", g.pick_browser({"ARCHIFY_CHROME": _OK_PATH}, lambda p: p == _OK_PATH), _OK_PATH)
# ★ 负对照：env 指了个**不存在**的路径 ⇒ 必须**回落**标准路径，不许直接返回 None
chk("★负对照 env 路径不存在 ⇒ 回落标准路径（不是 None）",
    g.pick_browser({"ARCHIFY_CHROME": r"C:\nope\chrome.exe"}, lambda p: p == _OK_PATH), _OK_PATH)
chk("★负对照 一个浏览器都没有 ⇒ None（=未运行，不等于通过）",
    g.pick_browser({}, lambda p: False), None)
chk("负对照 空 env 也能回落", g.pick_browser({}, lambda p: p.endswith("msedge.exe")), _OK_PATH)

# ---------------------------------------------------------------- 从同步链源码抽图
_src_ok = ('DIAGRAMS = [\n'
           '    ("architecture", "a.json", "甲.html"),\n'
           '    ("workflow", "b.json", "乙.html"),\n]\n')
chk("正对照 能抽出 DIAGRAMS 两条", len(g.diagrams_from_sync_source(_src_ok)), 2)
chk("正对照 抽出的第 3 列是 html 名", g.diagrams_from_sync_source(_src_ok)[1][2], "乙.html")

# ★ 负对照：抽不出来必须给**空列表**（调用方据此降级扫目录），不许抛异常、不许给半截
chk("★负对照 语法坏了的源码 ⇒ []（不抛异常）", g.diagrams_from_sync_source("DIAGRAMS = [((("), [])
chk("★负对照 没有 DIAGRAMS ⇒ []", g.diagrams_from_sync_source("X = 1\n"), [])
chk("负对照 DIAGRAMS 是表达式（非字面量）⇒ []", g.diagrams_from_sync_source("DIAGRAMS = f()\n"), [])

# 真源码能抽到的图 = **同步链自己声明的那些**（不写死数量！）
#
# ⚠️ 这里曾经写死 `7`，并在 2026-10-10 变成 5 处假失败。
#    真因：2026-10-01 四线剥离把「AI创作工坊」搬走，DIAGRAMS 里原本 7 条
#    减到 4 条（工坊的架构图/共享边界图等随之外迁）。
#    ⇒ 断言写死数量 = **断言会随仓库演化而过期**，而且失败信息指向"判据坏了"，
#      与事实（"资产搬走了"）**完全对不上**，是典型的误导性红灯。
#    正确口径：`want` 从 **DIAGRAMS 真源**派生，本仓加图/减图都不会误报。
#    （纪律：别在代码里写死会变的数字 —— 写下的那一刻它就开始烂。）
_real = (g.DIAGRAMS_SRC.read_text(encoding="utf-8", errors="replace")
         if g.DIAGRAMS_SRC.exists() else "")
_real_pairs = g.diagrams_from_sync_source(_real)
_real_names = [h for _t, _j, h in _real_pairs]
_want_n = len(_real_pairs)

# ★ 前提自检：抽不出来时必须**报**，而不是让下面「len==0 等于 0」假通过。
#   （同族教训：判据参数取到边界值 ⇒ 断言恒真。）
chk("前提 真同步链抽得到图（抽不到则下面全部无意义）", _want_n > 0, True)

chk(f"正对照 真同步链抽出 {_want_n} 张", len(_real_names), _want_n)
# ★ 从 DIAGRAMS 里取一张**确实声明了**的图当"必须含"的样本 —— 同样不写死文件名。
_first_html = _real_pairs[0][2] if _real_pairs else None
chk(f"正对照 含同步链声明的 `{_first_html}`",
    _first_html in _real_names, True)

# ---------------------------------------------------------------- 兜底清单不许自指
chk("正对照 只有真图 ⇒ 原样保留", g.fallback_htmls(["A.html", "B.html"]), ["A.html", "B.html"])
# ★ 负对照（核心）：视觉门自己写的 sidecar **必须被排除** ——
#   否则下次会去"检查那份检查报告"（49KB viewer 页读不出节点字号 ⇒ 一堆莫名 fail）。
chk("★负对照 排除自己的 .visual-check.html", g.fallback_htmls(["A.html", "A.visual-check.html"]), ["A.html"])
chk("★负对照 排除 .visual-check.json",
    g.fallback_htmls(["A.html", "A.visual-check.json", "A.visual-check.1440x900.light.png"]), ["A.html"])
chk("负对照 非 html 一律不进", g.fallback_htmls(["A.png", "B.json", "C.html"]), ["C.html"])
chk("负对照 空清单 ⇒ 空", g.fallback_htmls([]), [])
chk("未改动 输出有序（不依赖传入顺序）",
    g.fallback_htmls(["B.html", "A.html"]), g.fallback_htmls(["A.html", "B.html"]))

# 拿**真目录**再验一遍：同步链声明的图都在，且**没有一条带 visual-check**
# ★ 口径与上面一致：数量从 DIAGRAMS 派生，不写死。
_real_dir = sorted(p.name for p in g.DIAGRAM_DIR.glob("*.html")) if g.DIAGRAM_DIR.exists() else []
_filtered = g.fallback_htmls(_real_dir)
# ★★ 2026-10-10 改：原来这条断言是
#      chk("★负对照 真目录里 sidecar 确实存在（这条证明过滤不是在防空气）",
#          any(".visual-check." in n for n in _real_dir), True)
#    它的**本意**是"证明过滤不是在防空气"，但**夹具选错了对象** ——
#    它依赖 `archify/` 根目录里**恰好残留着** sidecar，而 sidecar 实际生成在
#    `.archify-delivery-*/` 子目录里，根目录从来没有过。
#    ⇒ 这是「拿会漂移的外部状态当夹具」：搬运/换版本后它就**静默变成假失败**，
#      而失败信息指向"判据坏了"，与事实完全不符。
#    正确做法：**自己造夹具**（临时目录里造一个真 sidecar），把"过滤在防真东西"
#    这件事**确定性地**证出来。
with tempfile.TemporaryDirectory() as _td3:
    _t3 = Path(_td3)
    (_t3 / "真图.html").write_text("<html></html>", encoding="utf-8")
    (_t3 / "真图.visual-check.html").write_text("<html>检查报告</html>", encoding="utf-8")
    (_t3 / "真图.visual-check.1440x900.light.png").write_text("", encoding="utf-8")
    _names3 = sorted(p.name for p in _t3.glob("*.html"))
    chk("★负对照 夹具里 sidecar 确实存在（证明过滤不是在防空气）",
        any(".visual-check." in n for n in _names3), True)
    chk("★负对照 同一夹具过滤后一条 sidecar 都不剩",
        any(".visual-check." in n for n in g.fallback_htmls(_names3)), False)
chk("★负对照 过滤后一条 sidecar 都不剩", any(".visual-check." in n for n in _filtered), False)
# ⚠️ 这里**只断言真图 ≥ 同步链声明的张数**，不要求"恰好相等"：
#    真目录是产物目录，可能残留尚未清理的历史图（如 `_archive` 里捞回来的）。
#    要求"恰好相等"会把"目录有陈货"判成"判据坏了"—— 那是另一件事。
chk(f"正对照 真目录过滤后真图 ≥ {_want_n} 张（≥ 同步链声明数）",
    len(_filtered) >= _want_n, True)

# ---------------------------------------------------------------- 未改动项
chk("未改动 幂等：同一回执两次结果全等",
    g.rows_from_receipt("X.html", TRAP), g.rows_from_receipt("X.html", TRAP))
chk("未改动 判据不依赖行顺序",
    g.judge(list(reversed(g.rows_from_receipt("X.html", TRAP))))[0], 1)
chk("未改动 本工具自己的 json 不落在 archify",
    "archify" in str(g.DEFAULT_JSON), False)


# ---------------------------------------------------------------- 汇总
npass = sum(1 for _, got, want in checks if got == want)
bad = [(n, got, want) for n, got, want in checks if got != want]
print("")
print("=" * 78)
for n, got, want in bad:
    print(f"  ❌ {n}\n       got={got!r}  want={want!r}")
# ⚠️ 首列必须是 `PASS ` / `FAIL `（**行首、无前导空格**）——
#    `run_all_fitness.py` 靠 `ln.startswith(("FAIL ", "PASS ", "SKIP "))` 取摘要行。
#    （我第一版写成 `  smoke: 59/59 通过`：格式不对 ⇒ 总检摘要列空白；
#      第二版写成 `  PASS ...`：**有前导空格 ⇒ 依然取不到**。
#      两次都是"看着有输出、其实没接上"。判据的输出格式也是接口的一部分。）
_tag = "PASS " if not bad else "FAIL "
print(f"{_tag} smoke_diagram_visual_gate: {npass}/{len(checks)} 通过")
print("=" * 78)
sys.exit(0 if not bad else 1)
