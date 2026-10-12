# -*- coding: utf-8 -*-
"""守卫对照: `tools/qc/doc_refs.py` 的判据必须**既灵敏又不误报**。

为什么要带对照（项目纪律：**新守卫必须带正对照**）：
  doc_refs 的全部价值在于「声明了 §X 却没写 §X 时它会报红」。
  如果它对任何输入都返回空（解析规则写错、昵称对不上表），
  它就是**恒绿的假检查** —— 比没有更坏（会训练人忽略自检）。
  反过来，如果它把正文里的一个字母当成命中，就会**满屏误报**，同样会被忽略。
  ⇒ 必须证明三件事：① 真声明全过；② 植入假锚点必报；③ 判据**区分得开**
     （标题 vs 正文、本文件 vs 别的文件、范围首尾 vs 范围中间）。

对照清单（全部必须通过）:
  正  : 真实 CHANGELOG 的 3 条 `文档同步：` 行          -> 0 处
  正  : 标题里有锚点                                    -> anchor_present=True
  负  : **正文**里有锚点（非标题）                       -> anchor_present=False  ← 防"永远绿"
  负  : 单字母锚点在别的文档里存在、本文件没有           -> 报（防跨文件假绿）
  负  : 植入 §Z                                        -> 报 1 处且 token=Z
  负  : 植入不存在的 §T.99                             -> 报
  负  : 范围 `T.1~T.999` 中间的 T.50                    -> 报（证明范围真的展开了）
  负  : 没登记的昵称                                    -> 报 1 处
  负  : 目标文档不存在                                  -> 报
  负  : 不声明 `文档同步：` 的行（如历史"同步记录"）      -> 不参与（0 处）
  畸形: root 不存在 / 空文本                            -> 不抛

⚠️ 临时树上的夹具**落地路径从 `doc_refs.DOCS` 派生**，不手写 ——
   文档一旦再搬家，本体与体检自动同向，不会有"判据搬了家、体检没跟上"。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "tools" / "qc"))

from doc_refs import (CHANGELOG, DOCS, SYNC_MARK, anchor_present,  # noqa: E402
                      count_sync, find_broken_refs, is_sync_line)

fails = 0
cases: list[tuple[str, bool]] = []


def chk(name: str, ok: bool, extra: str = "") -> None:
    global fails
    fails += 0 if ok else 1
    cases.append((name, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{('  ' + extra) if extra else ''}")


def main() -> int:
    # ── 1. 纯函数：锚点强度（正 / 负） ──────────────────────────────────
    chk("正: 标题里的 T.13 算命中",
        anchor_present("## T. ComfyUI\n### T.13 🔴 x\n", "T.13", "heading"))
    chk("负: **正文**里的 T.13 不算命中（防恒绿）",
        not anchor_present("前文提到 T.13 这件事，但标题里没有\n", "T.13", "heading"))
    chk("正: 家族锚点 §T 命中 `## T.`",
        anchor_present("## T. ComfyUI 起不来\n", "T", "heading"))
    chk("正: 手册 §L 命中 `## §L.`",
        anchor_present("## §L. 锚点：一镜一图\n", "L", "heading"))
    chk("负: 相邻字母不误命中（L 不该被 `## §K.` 命中）",
        not anchor_present("## §K. 出片通路\n", "L", "heading"))
    chk("负: 家族锚点不许吃掉同前缀长词（T 不该命中 `## Timing`）",
        not anchor_present("## Timing 说明\n", "T", "heading"))
    chk("正: 圈号用裸子串（substr）",
        anchor_present("- **㊿ 判据强度…**\n", "㊿", "substr"))
    chk("负: 圈号裸子串也不许凭空命中",
        not anchor_present("- **㊼ 均值…**\n", "㊿", "substr"))

    # ── 2. 真实回测 ────────────────────────────────────────────────────
    cl = CHANGELOG
    if not cl.exists():
        chk("回测: CHANGELOG 存在", False, str(cl))
    else:
        txt = cl.read_text(encoding="utf-8")
        bad = find_broken_refs(txt)
        n = count_sync(txt)
        chk("正: 真实声明全部解析得到（且计数=解析口径）",
            bad == [], f"n={n} bad={bad[:4]}")
        chk("正: 真实 CHANGELOG 里声明 ≥ 4 条", n >= 4, f"n={n}")

    # ── 2b. 结构性判据：引用 vs 声明（这条是本守卫上线当天被自己踩出来的） ──
    chk("正: `> 文档同步：` 开头 ⇒ 算声明",
        is_sync_line("> 文档同步：手册 **§L**。"))
    chk("负: **正文里引用**一句声明 ⇒ 不算声明",
        not is_sync_line("*我在上一条里引用了「文档同步：… 手册 §L」*"))
    chk("负: 正文里引用 ⇒ 也不该参与解析",
        find_broken_refs("*引用：「文档同步：某新文档 **§A**」*", ROOT) == [])

    # ── 3. 临时树上的注入对照 ──────────────────────────────────────────
    # ⚠️ 夹具路径**从 `DOCS` 派生**，不许在这里手写 —— 布局只留一份真源。
    #    2026-09-26 文档从 `docs/` 搬到项目第一层时，本体改了、这里没改，
    #    于是 26 项里 7 项报「目标文档不存在」：**判据搬了家，体检没跟上**。
    #    （同族纪律：同一条规则只允许有一份实现。）
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        def put(nick: str, text: str) -> None:
            """按 `doc_refs.DOCS[nick]` 声明的**全部**路径落文件（父目录自动建）。

            ⚠️ `DOCS[nick][0]` 现在是**路径列表**（支持拆分文档，2026-10-10 起）。
              别写回成单路径 —— 夹具必须与本体同口径，否则「本体支持拆分、体检只建一片」
              ⇒ 体检会报一堆假的"锚点找不到"。
            """
            for rel in DOCS[nick][0]:
                p = tmp / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text, encoding="utf-8")

        # ⚠️ `### 150 c` 是**三位数标题**，专给下面那组"三位数编号"对照用 ——
        #    旧 `PLAIN_NUM` 只认两位数时它压根不参与检查（见下方注释）。
        put("避坑清单", "# 避坑\n## T. ComfyUI\n### T.1 a\n### T.13 b\n### 150 c\n")
        put("手册", "# 手册\n## §J. x\n### J.1 y\n## §K. z\n### K.1 w\n## §L. v\n")
        put("判决纪律", "- **㊼ a**\n- **㊽ b**\n- **㊾ c**\n- **㊿ d**\n- **51 · e**\n")

        def run(s: str) -> list[dict]:
            return find_broken_refs(s, tmp)

        chk("正: 合法声明 ⇒ 0 处",
            run("> 文档同步：避坑清单 **§T.13** · 手册 **§L** · 判决纪律 **㊿**。") == [])

        chk("正: 圈号用完后的 `51 ·` 编号也认（判决纪律）",
            run("> 文档同步：判决纪律 **㊿ / 51**。") == [],
            str(run("> 文档同步：判决纪律 **㊿ / 51**。")))
        b = run("> 文档同步：判决纪律 **㊿ / 99**。")
        chk("负: 不存在的 `99 ·` ⇒ 报", len(b) == 1 and b[0]["token"] == "99", str(b))
        b = run("> 文档同步：避坑清单 **§T.13**。")
        chk("负: `T.13` 里的 `13` **不许**被当成编号锚点", len(b) == 0, str(b))

        b = run("> 文档同步：手册 **§Z**。")
        chk("负: 植入 §Z ⇒ 报且 token=Z", len(b) == 1 and b[0]["token"] == "Z", str(b))

        b = run("> 文档同步：避坑清单 **§T.99**。")
        chk("负: 不存在的 §T.99 ⇒ 报", len(b) == 1 and b[0]["token"] == "T.99", str(b))

        # ── 三位数编号（2026-10-11 扩射程）──────────────────────────────────
        # `PLAIN_NUM` 原为 `(\d{2})` —— **只认两位数**（为「判决纪律」的 `51 · 52 · …` 设计）。
        # 后果：`避坑清单 §150` 这种三位数声明**压根不参与检查**，是最隐蔽的一种射程缺口
        # （不是报错，是"声明了却没人查"）。改成 `\d{2,}` 后，这两条对照把新射程钉住。
        # ⚠️ `777` 必须**在夹具里确实不存在** —— 否则这条负对照恒不报，等于没测。
        #    夹具当前只有 `T.1 / T.13 / 150`，故 777 是安全的假锚点。
        chk("正: 三位数编号 §150 命中夹具里的 `### 150`",
            run("> 文档同步：避坑清单 **§150**。") == [],
            str(run("> 文档同步：避坑清单 **§150**。")))
        b = run("> 文档同步：避坑清单 **§777**。")
        chk("负: 不存在的三位数 §777 ⇒ 报",
            len(b) == 1 and b[0]["token"] == "777", str(b))

        b = run("> 文档同步：避坑清单 **§T**（T.1~T.999）。")
        toks = {x["token"] for x in b}
        chk("负: 范围 T.1~T.999 **中间**也要查（T.50 被报）",
            "T.50" in toks and "T.1" not in toks and "T.13" not in toks, str(sorted(toks)[:6]))

        b = run("> 文档同步：手册 **§T.13**。")
        chk("负: 跨文件不算命中（手册里没有 T.13）", len(b) == 1, str(b))

        b = run("> 文档同步：某新文档 **§A**。")
        chk("负: 没登记的昵称 ⇒ 报", len(b) == 1 and "DOCS" in b[0]["why"], str(b))

        b = run("> 文档同步：避坑清单 **§T.13** · 手册 **§L**（并给 §Q.9 加批注）。")
        chk("负: 括号注里的锚点也要查（§Q.9 被报）",
            len(b) == 1 and b[0]["token"] == "Q.9", str(b))

        # ── 3b. 拆分文档：锚点落在**任一分片**里都算命中（2026-10-10 新增） ──
        # 背景：大部头在 2026-10-01 被 `_split_bigdocs.py` 拆成"后半留本仓 / 前半进 _archive/`"。
        # 只认单路径时，同步 `§T`（T.1~T.8）的历史声明会**误报 6 处漏写**
        # —— 报的是判据的射程不足，不是真缺陷。
        # ★ 这条必须有**正负两个方向**：光测"能命中了"证明不了"没命中会报"。
        with tempfile.TemporaryDirectory() as td2:
            tmp2 = Path(td2)
            rels = DOCS["避坑清单"][0]
            assert len(rels) >= 2, "本对照要求避坑清单是**多分片**的（拆分文档）"
            # 前半只有 T.1~T.6；后半只有 T.7+
            (tmp2 / rels[0]).parent.mkdir(parents=True, exist_ok=True)
            (tmp2 / rels[0]).write_text("# 避坑(后半)\n### T.7 b\n### T.13 c\n", encoding="utf-8")
            (tmp2 / rels[1]).parent.mkdir(parents=True, exist_ok=True)
            (tmp2 / rels[1]).write_text("# 避坑(前半归档)\n## T. ComfyUI\n### T.1 a\n### T.6 f\n",
                                        encoding="utf-8")

            def run2(s: str) -> list[dict]:
                return find_broken_refs(s, tmp2)

            chk("正(拆分): 锚点在前半分片里也算命中（§T.1 在归档那份）",
                run2("> 文档同步：避坑清单 **§T.1**。") == [],
                str(run2("> 文档同步：避坑清单 **§T.1**。")))
            chk("正(拆分): 锚点在后半分片里也算命中（§T.13）",
                run2("> 文档同步：避坑清单 **§T.13**。") == [],
                str(run2("> 文档同步：避坑清单 **§T.13**。")))
            chk("正(拆分): 家族锚点 `§T` 在前半（`## T.`）也算命中",
                run2("> 文档同步：避坑清单 **§T**。") == [],
                str(run2("> 文档同步：避坑清单 **§T**。")))
            b = run2("> 文档同步：避坑清单 **§T.99**。")
            chk("负(拆分): 两片都没有的 T.99 ⇒ 必须报（防『只要是多分片就全绿』）",
                len(b) == 1 and b[0]["token"] == "T.99", str(b))

        chk("负: 无 `文档同步：` 的行不参与",
            run("> 2026-08-29 全量同步记录: 手册/自检/安装包 已同步 ✅") == [])

        # 畸形
        try:
            a = find_broken_refs("> 文档同步：手册 **§L**。", tmp / "不存在")
            c = find_broken_refs("", tmp)
            chk("畸形: root 不存在 / 空文本 不抛", a == [] and c == [], f"{a} {c}")
        except Exception as e:  # pragma: no cover
            chk("畸形: root 不存在 / 空文本 不抛", False, f"{type(e).__name__}: {e}")

        # 目标文档缺失：**全部**分片都删掉（拆分文档有多个分片，只删一个不算"不存在"）
        for rel in DOCS["手册"][0]:
            (tmp / rel).unlink()
        b = run("> 文档同步：手册 **§L**。")
        chk("负: 目标文档不存在 ⇒ 报", len(b) == 1 and "不存在" in b[0]["why"], str(b))

    # ── 4. 未改动项：DOCS 表本身没被改坏 ───────────────────────────────
    # ⚠️ 不写死"3 个"——数量会变（我第一版写 3、实际是 4，标题就在撒谎）。
    _expect = {"避坑清单", "维修手册", "手册", "判决纪律"}
    chk("未改动: DOCS 里 4 个昵称齐全",
        _expect <= set(DOCS), str(sorted(DOCS)))

    print(f"\n{'PASS' if not fails else 'FAIL'}  smoke_doc_refs: {len(cases)} 项, {fails} 失败")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
