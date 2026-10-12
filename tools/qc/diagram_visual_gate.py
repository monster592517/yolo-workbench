# -*- coding: utf-8 -*-
"""diagram_visual_gate.py — 介绍图的「可读性门」（archify `visual-check` 的薄封装）

为什么要单独有一个门（维修手册 #198）
    `scripts/sync_everything.py` 此前只跑 `validate + deliver`：
      · `validate` 判的是**结构**（标签超宽 / 连线穿节点 / 边长过短），
        它**管不了**"渲染出来字够不够大"；
      · `deliver` 只保证**文件写出来了**。
    ⇒ 「7 图全部交付完成」与「7 图能看清」是两件事，
      而那条链上**没有任何一步**在看后者。

    更阴的是 archify 在**找不到浏览器**时是
    `status: skipped` + **退出码 2**（`viewer/chrome-unavailable`），
    stdout 只有一行 `visual-check skipped: <path>` ——
    链照走、日志照写"全部交付完成"，**"跳过"和"通过"在终端里几乎没区别**。
    本机没有 Chrome，但 Edge 在标准路径上 ⇒ 本工具**显式找浏览器**：
        `--browser`  >  环境变量 `ARCHIFY_CHROME`  >  常见 Edge / Chrome 路径

★ 两个口径（都是踩出来的，别改回去）
    1. **一个宽度有两张截图（亮 / 暗主题各一张）**，回执里是两条同 width 的记录。
       若按 width 做 dict key ⇒ **后写覆盖先写**，主题相关的回归会**静默漏掉**。
       本工具按 `(width, theme)` 全收，报告里取**双主题最小值**并标出主题。
       （2026-09-17 实测 7 张图两主题数值完全一致 ⇒ 当前不丢信息，
        但那是**当场巧合**，不是结构性保证。）
    2. **`skipped` 不等于 `pass`**。`judge()` 里 skipped 记成"未运行"，
       全部 skipped ⇒ 退出码 2，**绝不当通过**。

退出码语义（与 archify 对齐，方便上链 / 被别的脚本判断）:
    0 = 全部达标        1 = 有图未达标或有图未运行        2 = 一张都没跑成（无浏览器）

用法:
    python tools/qc/diagram_visual_gate.py
    python tools/qc/diagram_visual_gate.py --json runs/logs/h3/diagram_gate.json
    python tools/qc/diagram_visual_gate.py 双系统_共享与边界图.html

⚠️ 本工具**只读**真图、只写自己那份 json（默认落 `runs/logs/h3/`，
   **不落 `archify/`**）。
   ⚠️ 但 archify 自己会在产物旁写 sidecar：`<图名>.visual-check.html` / `.json`
   + 4 张 png（1440×900 / 2048×1320 × 亮/暗）—— 7 张图 = **42 个 sidecar 文件**。
   所以 `archify/` 里**同时躺着真图和"检查报告"**，
   兜底清单必须排除它们（见 `fallback_htmls()`），否则下次会去"检查那份检查报告"。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
ARCHIFY = ROOT / "tools" / "archify" / "archify" / "bin" / "archify.mjs"
DIAGRAM_DIR = ROOT / "archify"
DEFAULT_JSON = ROOT / "runs" / "logs" / "h3" / "diagram_gate.json"

#: 常见浏览器路径（本机无 Chrome，Edge 有；顺序 = 优先 Edge 再 Chrome）
BROWSER_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
)

#: 回执里没给阈值时的兜底（archify: MIN_PROJECTED_NODE_TEXT_PX）
FALLBACK_MIN_PX = 6.0

#: 与 sync_everything.DIAGRAMS 的 html 列一致；这里**不 import 那个脚本**
#: （它 import 期就建目录、跑东西）—— 只读它的源码文本抽 DIAGRAMS，避免副作用。
DIAGRAMS_SRC = ROOT / "scripts" / "sync_everything.py"


# ── 纯函数区（可单独喂数据做正负对照，不碰磁盘不碰网络）──────────────

def pick_browser(env: dict, exists=None) -> str | None:
    """选浏览器：`--browser` 由调用方先行处理，这里只管 env 与标准路径。

    判据吃注入的 `exists`（便于用假文件系统做正负对照）。
    返回 None = **找不到浏览器** ⇒ 调用方必须走"未运行"分支，不许当通过。
    """
    if exists is None:
        exists = os.path.isfile
    forced = (env or {}).get("ARCHIFY_CHROME")
    if forced and exists(forced):
        return forced
    for cand in BROWSER_CANDIDATES:
        if exists(cand):
            return cand
    return None


def rows_from_receipt(name: str, receipt: dict) -> list[dict]:
    """把一份 visual-check 回执摊成"每宽度一行"，**双主题取最小值**。

    返回行字段:
        name / width / min_px / required_px / themes / overflow_x / overflow_y
        / readability_ok / containment_ok / status
    `status` 在该宽度为 `skipped` 时原样保留 ⇒ 上层 `judge()` 才能分辨
    "没跑成"与"跑过了"。
    """
    shots = ((receipt or {}).get("captures") or {}).get("screenshots") or []
    by_width: dict = {}
    for s in shots:
        w = s.get("width")
        if w is None:
            continue
        by_width.setdefault(w, []).append(s)

    rows: list[dict] = []
    for w in sorted(by_width, key=lambda x: (x is None, x)):
        group = by_width[w]
        pxs = [g.get("minimumProjectedNodeTextPx") for g in group
               if g.get("minimumProjectedNodeTextPx") is not None]
        req = next((g.get("minimumRequiredNodeTextPx") for g in group
                    if g.get("minimumRequiredNodeTextPx") is not None), None)
        themes = [str(g.get("theme") or g.get("resolvedTheme") or "?") for g in group]
        # 双主题全过才算过（不用 any —— 有一张糊了就是糊了）
        read_ok = bool(group) and all(g.get("readabilityOk") is not False for g in group)
        # ★ containment 判 **overflowX / overflowY 本身**，
        #   不判它那个派生字段 `ok`（`ok = !overflowX && !overflowY`）。
        #   理由：派生字段一旦和原始读数打架，信派生字段等于**把两个判据压成一个**
        #   —— overflow 那格就再也守不住了（smoke 里那条 ★负对照就是逮它的）。
        #   原始读数缺席时才退回 `ok`。
        cont_ok = bool(group) and all(
            ((not g.get("overflowX")) and (not g.get("overflowY")))
            if ("overflowX" in g or "overflowY" in g) else (g.get("ok") is True)
            for g in group)
        unrun = all(g.get("readabilityOk") is None for g in group)
        rows.append({
            "name": name,
            "width": w,
            "min_px": min(pxs) if pxs else None,
            "required_px": req,
            "themes": themes,
            "overflow_x": any(bool(g.get("overflowX")) for g in group),
            "overflow_y": any(bool(g.get("overflowY")) for g in group),
            "readability_ok": read_ok,
            "containment_ok": cont_ok,
            "status": "skipped" if unrun else ("pass" if (read_ok and cont_ok) else "fail"),
        })
    return rows


def judge(rows: list[dict]) -> tuple[int, list[str]]:
    """判这批行该退什么码，以及"该说出来的话"。

    0 = 全达标 · 1 = 有未达标或有未运行 · 2 = 一张都没跑成
    ★ skipped **不进 pass**：拿不到读数就是**不知道**，不是"没问题"。
    """
    if not rows:
        return 2, ["没有任何回执（可能全部解析失败）"]
    failures: list[str] = []
    ran = 0
    for r in rows:
        tag = f"{r['name']} {r['width']}px"
        if r["status"] == "skipped" or r["min_px"] is None:
            failures.append(f"{tag}: 未运行（拿不到读数）")
            continue
        ran += 1
        req = r["required_px"] or FALLBACK_MIN_PX
        if not r["readability_ok"] or (r["min_px"] or 0) < req:
            failures.append(f"{tag}: 最小投影字号 {r['min_px']:.2f}px < {req:g}px")
        if not r["containment_ok"]:
            axis = []
            if r["overflow_x"]:
                axis.append("overflowX")
            if r["overflow_y"]:
                axis.append("overflowY")
            failures.append(f"{tag}: containment fail（{'+'.join(axis) or '未知'}）")
    if ran == 0:
        return 2, failures or ["全部未运行"]
    return (0 if not failures else 1), failures


def fallback_htmls(names) -> list[str]:
    """目录兜底清单：**必须排除视觉门自己的 sidecar**。

    ⚠️ 自指陷阱（写这个工具时差点踩进去）：`visual-check` 会在产物旁边写
    `<图名>.visual-check.html`（以及 `.json` 和 4 张 png），
    所以 `archify/` 里**同时躺着真图和"检查报告"**。
    兜底若直接 `glob("*.html")`，下一次就会去"检查那份检查报告" ——
    49KB 的 viewer 页当然读不出节点字号，于是报一堆莫名其妙的 fail。
    **扫描/替换类工具的默认陷阱是扫到自己的输出，不是意外。**

    判据吃**名字列表**（便于注入假清单做正负对照），只留 `.html` 且**不含** `.visual-check.`。
    """
    return sorted(str(n) for n in names
                  if str(n).endswith(".html") and ".visual-check." not in str(n))


def diagrams_from_sync_source(src: str) -> list[tuple[str, str, str]]:
    """从 `sync_everything.py` 源码文本里抽 `DIAGRAMS`（**不 import** 它）。

    用 AST 解析，避免 import 副作用（那个脚本 import 期就会建目录）。
    抽不出来 ⇒ 返回空列表，由调用方降级为"扫目录下全部 .html"。
    """
    import ast
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "DIAGRAMS" not in names:
            continue
        try:
            val = ast.literal_eval(node.value)
        except Exception:  # noqa: BLE001
            return []
        out = []
        for item in val:
            if isinstance(item, (list, tuple)) and len(item) == 3:
                out.append((str(item[0]), str(item[1]), str(item[2])))
        return out
    return []


# ── 执行区 ──────────────────────────────────────────────────────────

def _run_one(html: Path) -> tuple[dict | None, str]:
    """跑一次 visual-check，返回 (回执, 说明)。非 JSON 输出也如实带回来。"""
    cmd = ["node", str(ARCHIFY), "visual-check", str(html), "--json"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180)
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"
    txt = (r.stdout or "").strip()
    if not txt:
        return None, f"空输出 rc={r.returncode} {(r.stderr or '').strip()[:120]}"
    try:
        return json.loads(txt), f"rc={r.returncode}"
    except Exception:  # noqa: BLE001
        return None, f"非 JSON rc={r.returncode}: {txt[:120]}"


def main() -> int:
    ap = argparse.ArgumentParser(description="介绍图可读性门（archify visual-check 薄封装）")
    ap.add_argument("html", nargs="*", help="指定图（默认取 sync_everything.DIAGRAMS 全量）")
    ap.add_argument("--browser", default=None, help="浏览器可执行文件路径（优先于环境变量）")
    ap.add_argument("--json", default=str(DEFAULT_JSON), help="回执落盘路径")
    ap.add_argument("--quiet", action="store_true", help="只打一行结论")
    args = ap.parse_args()

    env = dict(os.environ)
    if args.browser:
        env["ARCHIFY_CHROME"] = args.browser

    if not ARCHIFY.exists():
        print(f"!! 找不到 {ARCHIFY}")
        return 2
    if not DIAGRAM_DIR.exists():
        print(f"!! 找不到 {DIAGRAM_DIR}")
        return 2

    # 目标清单：命令行 > sync_everything.DIAGRAMS > 目录下全部 html
    if args.html:
        targets = [(h if h.endswith(".html") else h + ".html") for h in args.html]
    else:
        src = DIAGRAMS_SRC.read_text(encoding="utf-8", errors="replace") if DIAGRAMS_SRC.exists() else ""
        targets = [html for _dtype, _j, html in diagrams_from_sync_source(src)]
        if not targets:
            targets = fallback_htmls(p.name for p in DIAGRAM_DIR.glob("*.html"))

    browser = pick_browser(env)
    if not browser:
        print("=" * 62)
        print("未运行：本机找不到 Chrome / Chromium，且未设 ARCHIFY_CHROME。")
        for c in BROWSER_CANDIDATES:
            print(f"   试过: {c}")
        print("   ⇒ 这不等于通过。设 --browser <路径> 或环境变量 ARCHIFY_CHROME 后重跑。")
        print("=" * 62)
        return 2
    env["ARCHIFY_CHROME"] = browser
    print(f"[浏览器] {browser}")

    all_rows: list[dict] = []
    raw: dict[str, dict] = {}
    for name in targets:
        p = DIAGRAM_DIR / name
        if not p.exists():
            all_rows.append({"name": name, "width": None, "min_px": None, "required_px": None,
                             "themes": [], "overflow_x": False, "overflow_y": False,
                             "readability_ok": False, "containment_ok": False, "status": "skipped"})
            print(f"  !! 缺文件: {p}")
            continue
        old = os.environ.get("ARCHIFY_CHROME")
        os.environ["ARCHIFY_CHROME"] = browser
        try:
            receipt, note = _run_one(p)
        finally:
            if old is None:
                os.environ.pop("ARCHIFY_CHROME", None)
            else:
                os.environ["ARCHIFY_CHROME"] = old
        if receipt is None:
            print(f"  !! {name}: {note}")
            all_rows.append({"name": name, "width": None, "min_px": None, "required_px": None,
                             "themes": [], "overflow_x": False, "overflow_y": False,
                             "readability_ok": False, "containment_ok": False, "status": "skipped"})
            continue
        raw[name] = receipt
        rows = rows_from_receipt(name, receipt)
        all_rows.extend(rows)

    rc, failures = judge(all_rows)

    if not args.quiet:
        print("-" * 62)
        print(f"{'图':<22}{'宽':>6}{'最小字号':>9}{'门槛':>6}  主题        readability / containment")
        for r in all_rows:
            thr = f"{r['required_px']:g}" if r["required_px"] else "-"
            px = f"{r['min_px']:.2f}" if r["min_px"] is not None else "未运行"
            if r["status"] == "skipped":
                # ★ 两个判据**分开报**：状态图就是 readability pass / containment fail，
                #   合成一个 "FAIL" 会让人以为字号也不达标（那是**另一种**缺陷、
                #   而且修法相反）—— #198 里这俩互斥，混着报就白记了。
                rd, ct = "未运行", "未运行"
            else:
                rd, ct = ("pass" if r["readability_ok"] else "FAIL"), \
                         ("pass" if r["containment_ok"] else "FAIL")
            print(f"{r['name']:<22}{str(r['width']):>6}{px:>9}{thr:>6}  "
                  f"{','.join(r['themes'])[:10]:<11} {rd} / {ct}")
    print("=" * 62)
    if rc == 0:
        print(f"视觉门通过：{len(all_rows)} 行读数全部达标")
    elif rc == 2:
        print("视觉门**未运行**（拿不到读数 ≠ 没问题）")
    else:
        print(f"视觉门有 {len(failures)} 处不达标/未运行：")
        for f in failures:
            print("   - " + f)

    try:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"rc": rc, "browser": browser, "rows": all_rows,
                                   "failures": failures}, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        if not args.quiet:
            print(f"[落盘] {out}")
    except Exception as e:  # noqa: BLE001
        print(f"!! 回执落盘失败（不影响结论）: {e}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
