# -*- coding: utf-8 -*-
"""守卫: **变更记录里声明「已同步 §X」，那个 §X 必须真的存在**。

为什么有它（2026-09-14，自查抓到的一条"假同步"）:
  我在 `CHANGELOG.md` v0.31.21 开头写下
  「文档同步：避坑清单 §T.13/§T.14/§T.15/§T.16 · 判决纪律 ㊼/㊽/㊾/㊿ · 手册 §L」，
  而当时 **㊿ 还没写**、**手册根本没有 §L**（章节只到 §K）。
  它比"忘了写文档"更坏：**读者会默认已同步，于是不去核对** —— 一条只存在于变更记录里的承诺。
  （见 `判决纪律_全编号.md` 的 `51 ·`；同族纪律 ㉕「判据强度 = 事实 > 结构 > 文案」。）

判据（纯函数，可单独调用）:
  find_broken_refs(changelog_text, root) -> list[dict]
      逐行找 `文档同步：…`，按 `·` 切段，每段 = `<昵称> **<锚点…>**（可选注）`；
      把该段的锚点全部解析到目标文档里，解析不到的记一条。
      返回 `[{"line":行号, "doc":昵称, "token":锚点, "path":相对路径, "why":说明}]`。

  anchor_present(text, token, mode) -> bool
      mode="heading"：锚点必须出现在**标题行**（`^#{2,6} §?<token>`），
                     用于「单字母/家族」锚点 —— 裸子串搜索会把正文里的一个字母当命中；
      mode="substr" ：裸子串（用于 ㊼/㊽ 这类圈号，天然不会误命中）。

为什么单字母必须锚在标题上：手册里 `L` 作为裸子串出现几百次 ⇒ 裸搜 = **永远绿**的判据。

正/负对照见 `tools/qc/smoke_doc_refs.py`（必须都跑出来才算过）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent.parent

# ⚠️ CHANGELOG 的**唯一真源**。判据本体与它的体检（`smoke_doc_refs.py`）都从这里取，
#    两边各写一份路径的结果是：2026-09-26 文档搬到项目第一层后，
#    本体指向了 `ROOT/CHANGELOG.md`，体检还在读 `ROOT/"docs"/CHANGELOG.md` ⇒ 体检先断。
CHANGELOG = ROOT / "CHANGELOG.md"

# 昵称 → (相对路径**列表**, 锚点模式)。写全昵称别名：文档里同一个文件被叫过好几种名字。
#
# ⚠️ 为什么是**列表**而不是单路径：大部头文档在 2026-10-01 被 `_migration/_split_bigdocs.py`
#    拆成了两半（"后半"留在项目第一层、"前半"进 `_archive/`，**原文逐字节保留**）。
#    `避坑清单_20260814.md` 的 `T.1~T.6` 就在 `_archive/避坑清单_归档_前半.md` 里。
#    ⇒ 只认单路径时，CHANGELOG 里同步 `§T`（T.1~T.8）的历史声明会**误报 6 处"漏写"**。
#    实测（2026-10-10）：本仓文件从 `### T.7` 开始，`T.7` 之前的一半全在归档里。
#    （纪律：文档被拆分后，**判据的射程必须跟着扩**，否则它报的是自己的盲区、不是真缺陷。）
DOCS: dict[str, tuple[list[str], str]] = {
    "避坑清单": (["避坑清单_20260814.md", "_archive/避坑清单_归档_前半.md"], "heading"),
    "维修手册": (["AI动画产线_自检维修手册.md"], "heading"),
    "手册":     (["AI动画产线_自检维修手册.md"], "heading"),
    "判决纪律": ([".workbuddy/memory/判决纪律_全编号.md"], "substr"),
}

SYNC_MARK = "文档同步："

# ⚠️ 「目标文档不在本仓」的**显式豁免表**（2026-10-10 开源清理新增）。
#
# 为什么需要它：CHANGELOG 是**史实**（不许改），而其中若干历史声明同步的是
# **H3 动画产线的文档** —— 那些文档随「四线剥离」搬去了
# `18_legacy_h3_sim/`，**不归本仓管**。
# ⇒ 「目标文档不存在」这件事，对本仓是**正常状态**，不是「声明了却没写」。
#    两者必须分开：
#      · 文档不存在 + 不在豁免表  ⇒ **报**（真漏写 / 路径写错）
#      · 文档不存在 + 在豁免表    ⇒ **跳过**（对象不归本仓，判据管不着）
#      · 文档存在   + 锚点找不到  ⇒ **一律报**（口径不变）
#
# ★ 这张表必须**可审计**：每一条都要写明「搬去哪了」。
#   （纪律：豁免不等于放水 —— 放水是"总之不报"，豁免是"指名道姓地说这条不归我管"。）
# ⚠️ 只登记**真的不在本仓**的路径。把「存在但内容被拆分」的文档放进来 = 用豁免
#    掩盖判据的射程缺口（`避坑清单_20260814.md` 就是这样被我误登记过一次，
#    实际它有 299KB、只是 `T.1~T.6` 在归档里 ⇒ 正确做法是扩 DOCS 射程，不是豁免）。
EXEMPT_MISSING_DOCS: dict[str, str] = {
    "AI动画产线_自检维修手册.md": "H3 动画产线文档，四线剥离时搬到 18_legacy_h3_sim/",
}
# ⚠️ 判据必须是**结构性**的：**只有「行首的引用块 + 标记」才算声明**。
#    为什么（2026-09-14，本守卫上线当天就抓到我自己）：我在 v0.31.22 的正文里
#    **引用了** v0.31.21 那句声明（「文档同步：… 手册 §L」），
#    而只要用「行里含 `文档同步：`」当判据，这句**引用**就会被当成**真声明**并报红。
#    ⇒ 引用与声明在**文字上**无法区分，在**结构上**是分得开的（声明一定在 `>` 块里）。
#    （同族纪律 ㉕：判据强度 = 事实 > 结构 > 文案。）
#    计数与解析必须用**同一个**判据，否则会出现「报了 5 条、解析了 4 条」的对不上。
SYNC_RE = re.compile(r"^[ \t]*>[ \t]*文档同步[：:]")


def is_sync_line(line: str) -> bool:
    """这一行是不是一条**声明**（行首引用块 + 标记）。"""
    return SYNC_RE.match(line) is not None


def count_sync(changelog_text: str) -> int:
    return sum(1 for l in changelog_text.splitlines() if is_sync_line(l))
# 一行里的分段符（中文间隔号）
SEP = "·"
BOLD = re.compile(r"\*\*(.+?)\*\*")
# 范围锚点：T.1~T.8 / K.1~K.6（先吃掉，避免只登记首尾）
RANGE = re.compile(r"§?\b([A-Z])\.(\d+)\s*[~～]\s*§?\b([A-Z])\.(\d+)")
# 一般锚点：§T.13 / T.10 / §L / §T
ANCHOR = re.compile(r"§?([A-Z])(?:\.(\d+))?")
# 圈号锚点（㊲..㊿）
CIRCLED = re.compile(r"[㊲-㊿]")
# ⚠️ Unicode 的圈号**止于 ㊿**，判决纪律从这里往后改用 `51 · …` / `52 · …`。
#    漏了这条的话，`判决纪律 ㊿ / 51` 里的 `51` 会被**静默跳过**（声明了但没人查）。
#    前后都排掉数字与点：`T.13` 的 `13`（前面是 `.`）与 `T.999` 的 `99`（后面是数字）
#    都不算锚点 —— 它们各自的锚点已经由 ANCHOR 明确取到了。
#    （第一版写成「两位数紧跟 `·`」，结果只认定义写法、不认引用写法 `**㊿ / 51**`，
#      负对照 `99` 直接漏过 —— 这条是被 smoke 抓出来的，不是想出来的。）
PLAIN_NUM = re.compile(r"(?<![\d.\w])(\d{2,})(?![\d.])")


def anchor_present(text: str, token: str, mode: str = "heading") -> bool:
    """`token` 形如 `T.13` / `T` / `㊿` / `J.1`（**不带 §**，由调用方剥掉）。

    mode="heading" ⇒ 必须是标题；mode="substr" ⇒ 裸子串。
    宽松度刻意收紧：宁可漏报（假阴性可被人发现），不要满屏误报（会训练人忽略自检）。
    """
    if not token:
        return False
    if mode == "substr":
        return token in text
    fam, num = token, None
    if "." in token:
        fam, _, num = token.partition(".")
    body = re.escape(fam) + (r"\." + re.escape(num) if num else r"(?=[\s.]|$)")
    # `(?=[\s.]|$)`：家族锚点 `T` 允许后面跟 `.`（即 `## T.`）或空白，但不许 `Timing`
    pat = re.compile(r"(?m)^#{2,6}[ \t]*§?" + body + (r"(?=[\s\.、:：]|$)" if num else ""))
    return pat.search(text) is not None


def _tokens(seg: str) -> list[str]:
    """从一段里取全部锚点（去重保序）。范围锚点展开成逐个。"""
    out: list[str] = []

    def add(t: str) -> None:
        if t and t not in out:
            out.append(t)

    s = seg
    for m in RANGE.finditer(seg):
        a, i, b, j = m.group(1), int(m.group(2)), m.group(3), int(m.group(4))
        if a != b or j < i:
            continue
        for k in range(i, j + 1):
            add("%s.%d" % (a, k))
    s = RANGE.sub(" ", s)
    for m in ANCHOR.finditer(s):
        add(m.group(1) + ("." + m.group(2) if m.group(2) else ""))
    for m in CIRCLED.finditer(s):
        add(m.group(0))
    for m in PLAIN_NUM.finditer(s):
        add(m.group(1))
    return out


def _docs_in(seg: str) -> tuple[str | None, list[str]]:
    """→ (昵称, 锚点列表)。昵称 = `**` 之前的文字（去掉空白与标点）。"""
    m = BOLD.search(seg)
    body = seg
    if m:
        nick = seg[:m.start()].strip(" :：·、,，")
        body = seg
    else:
        nick = ""
    for k in DOCS:
        if nick and (nick == k or nick.endswith(k)):
            return k, _tokens(body)
    return (nick or None), _tokens(body)


def find_broken_refs(changelog_text: str, root: Path | str = ROOT,
                     docs: dict | None = None) -> list[dict]:
    """扫 `文档同步：…` 行，返回解析不到的锚点列表。`root` 不存在 ⇒ 空列表（不抛）。"""
    docs = docs if docs is not None else DOCS
    root = Path(root)
    if not root.exists():
        return []
    cache: dict[str, str | None] = {}

    def text_of(rels: list[str]) -> tuple[str | None, list[str]]:
        """→ (合并文本, 实际存在的路径列表)。

        拆分文档的口径：**任一分片里有，就算有** —— 把存在的分片拼接后统一搜。
        （若改成"逐个分片分别判"，家族锚点会因分片边界被漏判，故拼接更稳。）
        """
        parts: list[str] = []
        present: list[str] = []
        for rel in rels:
            if rel not in cache:
                p = root / rel
                cache[rel] = p.read_text(encoding="utf-8") if p.is_file() else None
            if cache[rel] is not None:
                parts.append(cache[rel])
                present.append(rel)
        return ("\n".join(parts) if parts else None), present

    bad: list[dict] = []
    for ln, line in enumerate(changelog_text.splitlines(), 1):
        m = SYNC_RE.match(line)
        if m is None:
            continue
        payload = line[m.end():]        # 用正则的切点，不用 split（兼容半角/全角冒号）
        for seg in payload.split(SEP):
            if not seg.strip():
                continue
            nick, toks = _docs_in(seg)
            if not nick:
                continue
            hit = None
            for k, (rels, mode) in docs.items():
                if nick == k or nick.endswith(k):
                    hit = (k, rels, mode)
                    break
            if hit is None:
                bad.append({"line": ln, "doc": nick, "token": "-", "path": "",
                            "why": "昵称不在 DOCS 表里，解析不到目标文档（新增文档请登记 DOCS）"})
                continue
            _, rels, mode = hit
            txt, present = text_of(rels)
            if txt is None:
                # ★ 第三态：文档不在本仓（见 EXEMPT_MISSING_DOCS 的说明）。
                #   ⚠️ 豁免只在**判真源根**时生效（root 解析后 == 真 ROOT，且用默认 docs 表）。
                #      为什么必须卡这两条：`smoke_doc_refs` 有一条负对照
                #      「目标文档不存在 ⇒ 报」，它跑在**临时目录**上、用默认 DOCS 表。
                #      若只按 `docs is DOCS` 判，那条负对照会被豁免吃掉 ⇒ 体检变假绿。
                #      ⚠️ 用 `==` 不用 `is`：`Path` 每次构造都是新对象，`is` 永远为假
                #      （我第一版就写了 `is`，结果豁免静默失效）。
                #      （纪律：豁免的作用域必须小于判据的作用域。）
                if (root == ROOT and docs is DOCS
                        and all(r in EXEMPT_MISSING_DOCS for r in rels)):
                    continue
                bad.append({"line": ln, "doc": nick, "token": "-", "path": rels[0],

                            "why": "目标文档不存在"})
                continue
            for t in toks:
                if not anchor_present(txt, t, mode):
                    # path 报**实际存在的分片**，便于人定位。多个分片用 ` , ` 分隔，
                    # ⚠️ 不用 `+`：`a.md+b.md` 看着像一个文件名（我第一版就用了 `+`，
                    #    在注入测试的输出里读成了"这个文件叫这名儿"）。
                    # 一个分片都没有的极端情形已在上面 continue 掉。
                    bad.append({"line": ln, "doc": nick, "token": t,
                                "path": " , ".join(present) if present else rels[0],
                                "why": "该锚点在目标文档里找不到（声明了却没写）"})
    return bad


def main() -> int:
    cl = CHANGELOG
    if not cl.exists():
        print(f"FAIL  找不到 {cl}")
        return 1
    txt = cl.read_text(encoding="utf-8")
    bad = find_broken_refs(txt)
    n_sync = count_sync(txt)
    print(f"== doc_refs ==  扫到声明行（行首引用块 + `{SYNC_MARK}`）{n_sync} 条")
    if bad:
        for b in bad:
            print("  BAD  L%-5d [%s] %s %s :: %s"
                  % (b["line"], b["doc"], b["token"], b["path"], b["why"]))
        print(f"\nFAIL  {len(bad)} 处「声明了却没写」——要么补文档，要么改声明的锚点。")
        return 1
    print("  OK  每条声明都能解析到真实锚点")
    print("\nPASS  doc_refs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
