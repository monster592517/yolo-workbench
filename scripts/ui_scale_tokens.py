# -*- coding: utf-8 -*-
"""ui_scale_tokens.py — 把离格的 padx/pady 半档值吸附回 8px 栅格令牌。

背景 (2026-10-03, 怪怪: "有些界面太挤太乱了"):
    项目里早已有 `src/gui/ui_tokens.py` 定义了 SPACE = {xxs:2, xs:4, sm:8,
    md:12, lg:16, xl:24, xxl:32} 与 CARD_PAD / PAGE_PAD / GRID_GAP。
    但页面代码基本没在用它 —— `training_page.py` 里 `SPACE[` 出现 **0 次**,
    全是手写数字。实测两页共 187 处 padx/pady 字面量, 其中 **39 处是"半档"**
    (6px / 10px / 3px / 30px), 与 8/12/16 混用时同一屏出现两种间距节奏,
    这才是"乱"的结构性来源 —— 不是没设计, 是设计没被用。

口径 (严格, 不猜):
    · **只动 padx / pady**。不动 width / height / font / 颜色 / 任何布局结构。
    · **只动离格值**。已在栅格上的 (0/2/4/8/12/16/24/32) 一个都不碰。
    · 吸附方向 = **就近向上到最近的上档**, 只对 6/10/3 这类"夹在两档中间"的:
        3  -> 4  (xs)   6  -> 8  (sm)   10 -> 12 (md)   30 -> 24 (xl, 例外)
      ⚠ 详见 SNAP 上方注释: 向上是因为诉求是"别那么挤"; 30 例外向下是因为
        它会推高总高触发滚动条 = 改变布局。
    · 0 是显式"无间距", 是合法值, 不动。
    · 向上吸附会**微量抬高**局部间距 (每处 +2px), 但**不改变结构**:
      没有 insert/remove 控件, 没有改 grid 行列, 没有改控件高度。

验收 (配对, 不用统计推断):
    改完逐处回读, 输出 旧值->新值 的配对清单, 并断言:
        ① 改动条数 == 预期条数
        ② 文件里再无离格 pad 值
        ③ 行数不变 且 掩掉全部数字后全文逐字节相同 (结构零变化)

⚠️ **本工具的能力边界 (2026-10-03 实测漏过一次, 必须知道)**:
    正则只吃 `padx=<字面数字>` 与 `padx=(<数字>, <数字>)` 两种形态。
    **抓不到表达式形式**, 例如:
        padx=(6 if ci == 0 else 2, 2)      ← 那个 6 是离格值, 但扫不到
    实测: training_page.py 里这行**在"离格=0"的验收下漏过去了**, 是后来用
    另一条宽松正则复核才发现的。⇒ 本工具跑完**必须再跑一次 `--scan-expr`**,
    把表达式里的离格值列出来人工处理。
    **别把"本工具报 0"当成"这页没有离格值"。**

用法:
    python scripts/ui_scale_tokens.py FILE [FILE ...] [--apply]
    python scripts/ui_scale_tokens.py FILE [FILE ...] --scan-expr   # 只扫表达式形态
    不带 --apply = 预演 (默认)。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 8px 栅格: 值 -> 令牌名
GRID = {0: "0", 2: "xxs", 4: "xs", 8: "sm", 12: "md", 16: "lg", 24: "xl", 32: "xxl"}

# 离格 -> 吸附目标
# ⚠ 方向选择 (2026-10-03 修正): 怪怪抱怨的是"太挤", 所以除 30 外的离格值一律
#   **就近向上吸附到最近的上档** —— 6->8, 10->12, 3->4。两个理由:
#     ① 6 和 8 只差 2px, 向上吸附不会挤掉下方内容, 只会让节奏松开;
#        向下吸附 (6->4) 在"太挤"的诉求下是反向操作。
#     ② 向上后 8/12 与既有的 8/12 完全同档, 一屏里只有一个间距节奏。
#   **30 例外**: 它是大留白 (空状态提示的上下留白), 30->32 会推高总高、
#   可能触发滚动条 = 改变布局, 与"不动布局"口径冲突 ⇒ 改为向下 24。
SNAP = {1: 2, 3: 4, 5: 4, 6: 8, 7: 8, 9: 8, 10: 12, 11: 12, 13: 12, 14: 16,
        15: 16, 18: 16, 20: 24, 30: 24}

# padx=NN / pady=NN / padx=(NN, / padx=(NN, MM)   —— 只匹配参数区, 不碰别处
RE_SINGLE = re.compile(r"(pad[xy]\s*=\s*)(\d+)(?![0-9])")
RE_PAIR = re.compile(r"(pad[xy]\s*=\s*\(\s*)(\d+)(\s*,\s*)(\d+)(\s*\))")


def transform(src: str) -> tuple[str, list[tuple[int, str, str, str]]]:
    """返回 (新内容, [(行号, 参数名, 旧片段, 新片段)])。"""
    changes: list[tuple[int, str, str, str]] = []
    lines = src.split("\n")
    out_lines = []
    for i, line in enumerate(lines, 1):
        # ★ 只在**代码区**替换: `#` 之后是注释, 里面的数字不许动
        #   (实测踩过: 注释里写了旧值 `padx=(6 if ...)`, 扫描器把它当代码报假阳性;
        #    反向也一样糟 —— 替换器会去改注释里的数字, 把历史记录改坏)
        cut = line.find("#")
        code, tail = (line, "") if cut < 0 else (line[:cut], line[cut:])
        new = code

        def _pair(m):
            a, b = int(m.group(2)), int(m.group(4))
            na = SNAP.get(a, a)
            nb = SNAP.get(b, b)
            if na == a and nb == b:
                return m.group(0)
            seg_old = m.group(0)
            seg_new = f"{m.group(1)}{na}{m.group(3)}{nb}{m.group(5)}"
            changes.append((i, "pair", seg_old.strip(), seg_new.strip()))
            return seg_new

        new = RE_PAIR.sub(_pair, new)

        def _single(m):
            v = int(m.group(2))
            nv = SNAP.get(v, v)
            if nv == v:
                return m.group(0)
            seg_old = m.group(0)
            seg_new = f"{m.group(1)}{nv}"
            changes.append((i, "single", seg_old.strip(), seg_new.strip()))
            return seg_new

        new = RE_SINGLE.sub(_single, new)
        out_lines.append(new + tail)
    return "\n".join(out_lines), changes


def strip_comments(src: str) -> str:
    """剥掉 `#` 注释 (保留行数与缩进, 用等长空格替换, 便于按行号对照)。

    ⚠ 为什么必须剥: 2026-10-03 实测 —— 我在代码里写了条注释
      「这里原是 `padx=(6 if ci == 0 else 2, 2)`」, 结果**扫描器把这行注释
      当代码**, 报出一个根本不存在的"离格值 6" ⇒ 假阳性。
      项目记忆里的同族教训: **扫源码的判据必须剥注释 (更稳用 AST)**。
      这里用最朴素的"剥 `#` 之后的内容", 不处理字符串里的 `#`
      (pad 语句里不会出现含 `#` 的字符串字面量, 够用; 若将来不够再上 AST)。
    """
    out = []
    for line in src.split("\n"):
        # 只认行首到第一个 # 的代码部分; `#` 出现在字符串里这种情况 pad 行不会遇到
        idx = line.find("#")
        if idx < 0:
            out.append(line)
        else:
            out.append(line[:idx] + " " * (len(line) - idx))
    return "\n".join(out)


def audit(src: str) -> dict[int, int]:
    """统计所有 pad 值 (含成对里的两个)。**先剥注释**, 避免把注释里的旧值算进来。"""
    src = strip_comments(src)
    vals: dict[int, int] = {}
    for m in RE_SINGLE.finditer(src):
        vals[int(m.group(2))] = vals.get(int(m.group(2)), 0) + 1
    for m in RE_PAIR.finditer(src):
        for g in (2, 4):
            vals[int(m.group(g))] = vals.get(int(m.group(g)), 0) + 1
    return vals


def scan_expressions(src: str) -> list[tuple[int, str]]:
    """扫**表达式形态**的 pad 值 (主正则抓不到的那类)。

    做法: 找所有 `pad[xy]=` 后面**不是**"数字开头"或"(数字"的行, 列出来人工看。
    宁可多报, 不要漏 —— 漏一次就是残留一处离格值。

    ⚠ 用**剥注释后**的文本做判断 (注释里提到旧值不算), 但**展示原始行**给人看。
    """
    raw_lines = src.split("\n")
    code_lines = strip_comments(src).split("\n")
    out = []
    for i, code in enumerate(code_lines):
        for m in re.finditer(r"pad[xy]\s*=\s*", code):
            rest = code[m.end():]
            # 标准形态: 数字, 或 (数字...
            if re.match(r"\d", rest) or re.match(r"\(\s*\d", rest):
                continue
            out.append((i + 1, raw_lines[i].strip()))
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--scan-expr", action="store_true",
                    help="只扫表达式形态的 pad 值 (主正则抓不到的那类)")
    args = ap.parse_args()

    if args.scan_expr:
        total = 0
        for f in args.files:
            p = Path(f)
            if not p.is_absolute():
                p = ROOT / p
            src = p.read_bytes().decode("utf-8")
            hits = scan_expressions(src)
            print(f"=== {p.relative_to(ROOT)}: 表达式形态 {len(hits)} 处")
            for ln, line in hits:
                print(f"    L{ln:<5} {line[:110]}")
            total += len(hits)
        print(f"\n合计 {total} 处 —— 这些**主正则改不到**, 需人工按令牌改。")
        return 0

    rc = 0
    for f in args.files:
        p = Path(f)
        if not p.is_absolute():
            p = ROOT / p
        raw = p.read_bytes()
        # 行尾保真: **严格按原字节判定, 不设任何"多数票"阈值**
        # ⚠ 2026-10-03 事故: 原实现写的是
        #     eol = "\r\n" if crlf and crlf > new_src.count("\n") * 0.5 else "\n"
        #   这个阈值判据在**二次运行时**会自我放大 —— 第一次跑把文件转成 CRLF 后,
        #   第二次跑读到的 crlf 就是满值, 于是又按 CRLF 写回, 再也回不去。
        #   而本项目的铁律是"各子项目行尾本来就不统一, 任何'统一行尾'都是
        #   没被要求的改动" ⇒ 这里必须**原样进、原样出**:
        #     · 全 LF (crlf==0)          -> 写回全 LF
        #     · 全 CRLF (crlf==总\n数)    -> 写回全 CRLF
        #     · 混合                      -> **拒绝写盘**, 交给人处理 (不许替它统一)
        total_lf = raw.count(b"\n")
        crlf = raw.count(b"\r\n")
        if crlf == 0:
            eol_in, eol = "LF", "\n"
        elif crlf == total_lf:
            eol_in, eol = "CRLF", "\r\n"
        else:
            eol_in, eol = f"混合({crlf}/{total_lf})", None
        src = raw.decode("utf-8")
        has_bom = src.startswith("\ufeff")

        print("=" * 70)
        print(f"{p.relative_to(ROOT)}  (行尾 {eol_in}, BOM={has_bom})")
        if eol is None:
            print("  [中止] 行尾是混合的 —— 本工具不替它统一, 请人工确认后再处理")
            rc = 1
            continue
        before = audit(src)
        off_before = {v: c for v, c in before.items() if v not in GRID}
        print(f"  改前: pad 值 {sum(before.values())} 处, 离格 {sum(off_before.values())} 处 {dict(sorted(off_before.items()))}")

        new_src, changes = transform(src)
        if not changes:
            print("  [跳过] 没有离格 pad 值需要吸附")
            continue

        print(f"  ★ 将改 {len(changes)} 处:")
        for ln, kind, old, nw in changes:
            print(f"      L{ln:<4} {old:<24} -> {nw}")

        after = audit(new_src)
        off_after = {v: c for v, c in after.items() if v not in GRID}
        print(f"  改后: 离格 {sum(off_after.values())} 处 {dict(sorted(off_after.items()))}")
        if off_after:
            print(f"  [!!] 仍有离格值未吸附: {dict(sorted(off_after.items()))}")
            rc = 1

        # ③ 结构不变判据 —— 硬判据, 不用 diff 的行匹配
        # ⚠ keepends 的 diff 在"多个相同空行"处会错位配对, 报出 ['-\r\n','+\r\n']
        #   这种**纯噪音** (实测 environment_page 698->698 行、内容无差, 却被报成
        #   "有非 pad 行被改动")。用"行匹配"去证结构不变本来就不可靠。
        #   改成更硬的两条断言:
        #     ① 行数必须相等 (增删行 = 改了结构, 直接 FAIL)
        #     ② 把所有数字替换成 '#' 后, 全文必须**逐字节相同**
        #        (数字是唯一允许变的; pad 以外的数字如果被动了, 这里就会红)
        n_old = len(src.splitlines())
        n_new = len(new_src.splitlines())
        mask_old = re.sub(r"\d+", "#", src)
        mask_new = re.sub(r"\d+", "#", new_src)
        if n_old != n_new:
            print(f"  [!!] 行数变了: {n_old} -> {n_new} (结构被改动)")
            rc = 1
        elif mask_old != mask_new:
            # 定位第一处差异, 方便排查
            i = next(k for k in range(min(len(mask_old), len(mask_new)))
                     if mask_old[k] != mask_new[k])
            print(f"  [!!] 数字之外的内容有变 (首个差异在第 {i} 字符):")
            print(f"       - {mask_old[max(0,i-40):i+40]!r}")
            print(f"       + {mask_new[max(0,i-40):i+40]!r}")
            rc = 1
        else:
            print(f"  [OK] 结构零变化: 行数 {n_old} 不变, 掩掉数字后全文字节相同")

        if args.apply:
            # 严格按 eol_in 写回 (eol 在文件头已判定; None 的情况已在上面中止)
            # 先把现有行尾统一成 \n 再按目标展开, 避免出现 \r\r\n
            txt = new_src.replace("\r\n", "\n")
            if eol == "\r\n":
                txt = txt.replace("\n", "\r\n")
            out = txt.encode("utf-8")
            p.write_bytes(out)
            # 回读确认: 行尾必须与改前一致, 否则就是又犯了"静默统一行尾"的老错
            back = p.read_bytes()
            b_crlf, b_lf = back.count(b"\r\n"), back.count(b"\n")
            back_kind = "CRLF" if (b_crlf and b_crlf == b_lf) else ("LF" if b_crlf == 0 else "混合")
            ok_eol = (back_kind == eol_in)
            print(f"  [写盘] {len(raw)} -> {len(back)} B, 行尾 {eol_in} -> {back_kind} "
                  f"{'✓ 保真' if ok_eol else '✗ 变了!'}")
            if not ok_eol:
                rc = 1
        else:
            print("  [dry-run] 未写盘")

    return rc


if __name__ == "__main__":
    sys.exit(main())
