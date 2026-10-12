# -*- coding: utf-8 -*-
"""fitness_common.py — 适应度函数（fitness function）的共享骨架。

什么是「适应度函数」
------------------
一条**可执行的架构约束**。它不测功能对不对，只测**架构有没有漂移**：

    某条规矩 → 一个能跑出 0/1 的脚本 → 接进自检 → 漂移在发生的那一刻就被拦住

本项目已经有 `smoke_*.py`（约 100 个）在守**行为**；适应度函数守的是**结构**。
两者的分工：

    smoke_*.py     喂输入 → 看输出对不对   （行为对不对）
    fitness_*.py   扫源码 → 看约束守没守   （结构有没有烂）

为什么用「冻结现状 + 只管增量」而不是「一次清干净」
------------------------------------------------
本仓库有不少历史遗留（例如硬编码根路径 222 处）。**一次性清完是错的**：

- 222 处里绝大多数**功能上是正确的**，改它们就是"改一个赔三个"；
- 真正的风险不是"现在有 222 处"，而是"**它还在涨**"。

所以本套检查的策略是：**把当前状态记成基线，只对"基线之外的增量"报警。**
基线存在外部文件（`tools/qc/.fitness_baseline.json`），**不写进判据脚本** ——
这是手册 #200 的直接应用：*"期望值绝对不许写进判据脚本"*，
否则生产默认一改，判据就变成过期快照，从现场读的值会被判成失败。

三条设计纪律（都是从本项目真实事故里长出来的）
--------------------------------------------
1. **准入门用全集合扫描，不用点名清单。**
   反例：`studio_doctor.py:2578` 的 `launchers = ["autostart_workbench.py", "comfy_keeper.py"]`
   —— 只检查点名的 2 个文件，于是 `src/core/h3_env.py`、`src/gui/pages/h3_factory_page.py`、
   `scripts/fall_pipeline_ctl.py`、`scripts/watch_fall400.py` 4 处**天然在视野外**。
   ⇒ **准入必须是"扫全集，逐个判定"**；豁免可以点名，但每条豁免要能说出理由（`EXEMPT` 表）。

2. **每条判据必须自带「传空名单 ⇒ 必须报出全部」的负对照。**
   手册 #204：*"任何'扫某个集合'的判据，都必须自带一条'把集合清空/传空名单，
   它必须报出全部'的对照"*。否则「它没报」和「它没长眼睛」长得一模一样。

3. **判据要窄到不产生噪音。**
   `studio_doctor.py:2324-2328` 的教训：*"凡裸命令都报"在本仓库命中 112 处，
   绝大多数是 System32 里的正常命令 ⇒ **112 条告警就是噪音，会训练人忽略自检**。*
   所以每条检查都要问：**这条报警，值不值得人放下手头的事去看？**

判据本体见同目录 `fitness_*.py`，正负对照见 `smoke_fitness_*.py`。
"""
from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

# 项目根 = tools/qc/ 往上三层
ROOT = Path(__file__).resolve().parent.parent.parent

BASELINE_FILE = Path(__file__).resolve().parent / ".fitness_baseline.json"

# 本项目源码树（架构约束的作用域）
SRC_TREES = ("src", "tools", "scripts")

# ---------------------------------------------------------------- 第三方目录
# 这些**不是本项目的代码**，本项目的架构约束对它们不适用。
# ⚠️ 每条都要能说出理由 —— 当年 `studio_doctor` 就是靠"我心里想的名单"漏掉了 4 处。
# ⚠️ 更要记住：**排除就是盲区**。2026-09-17 我自己在这里踩过一次：
#    为求快把 `tools/h3lite-main` 排除在外，而它正是默认工作流 JSON 的所在目录，
#    差点据此得出"某权重文件 0 引用"的错误结论。
#    ⇒ 排除只许用于「**确定是别人写的、且不可能引用我们的约束**」的目录；
#      凡是"可能相关但懒得扫"的，不许排除。
VENDOR_PREFIXES = (
    "tools\\sam2",                # 上游开源
    "tools\\archify",             # 上游开源
)
# ★ 2026-10-01：原表里的 ComfyUI-master / go-ai-studio / go-ai-studio-bin /
#   blender-4.5 / pifuhd / realesrgan-ncnn 六条已删 —— 那些目录随 H3/仿真三条线
#   搬出了本仓。**留着不是错，只是再也命中不到**；但"指向不存在目录的排除项"
#   会让人误以为仓库里还有这些东西，所以一并清掉。

SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv", "site-packages",
             ".trash", "_archive", "_scratch", "_migration_backup"}


def _is_vendor(rel: str) -> bool:
    return rel.startswith(VENDOR_PREFIXES)


def iter_sources(exts=(".py",), include_tests=True):
    """遍历本项目源码树，yield `(abs_path, rel_path)`（rel 用 `\\` 分隔，与 VENDOR_PREFIXES 一致）。

    `rel` 形如 `src\\core\\studio\\studio.py`。
    """
    for tree in SRC_TREES:
        base = ROOT / tree
        if not base.is_dir():
            continue
        for dp, dns, fns in os.walk(base):
            dns[:] = [d for d in dns if d not in SKIP_DIRS]
            for fn in fns:
                if not fn.lower().endswith(tuple(exts)):
                    continue
                ap = Path(dp) / fn
                rel = os.path.relpath(ap, ROOT)
                if _is_vendor(rel):
                    continue
                yield ap, rel


def read_text(p) -> str:
    """容错读文件（编码坏了也要能扫，否则一个坏文件会让整条检查静默失效）。"""
    try:
        return io.open(p, encoding="utf-8", errors="replace").read()
    except Exception:
        return ""


# ---------------------------------------------------------------- 报告与退出码
class Reporter:
    """统一输出与退出码。

    退出码协议（与 `tools/qc/doc_coverage.py` 等既有脚本一致）：
        0 = PASS（约束守住）
        1 = FAIL（有违规）
        2 = ERROR（判据自己跑不起来 —— **必须与 FAIL 区分**，
                  否则"检查坏了"会伪装成"架构漂移了"，反向也会）

    状态词只用这四个：
        OK    —— 通过
        WARN  —— 有情况但不必立刻动手（存量、或已知假阴性）
        FAIL  —— 违反架构约束
        SKIP  —— 本次没查（**绝不能当通过** —— "skipped ≠ pass"）
    """

    def __init__(self, name: str):
        self.name = name
        self.fails: list[str] = []
        self.warns: list[str] = []
        self.oks: list[str] = []
        self.skips: list[str] = []
        print(f"== {name} ==")

    def ok(self, msg: str, detail: str = ""):
        self.oks.append(msg)
        print(f"  OK    {msg}" + (f"\n        {detail}" if detail else ""))

    def warn(self, msg: str, detail: str = ""):
        self.warns.append(msg)
        print(f"  WARN  {msg}" + (f"\n        {detail}" if detail else ""))

    def fail(self, msg: str, detail: str = ""):
        self.fails.append(msg)
        print(f"  FAIL  {msg}" + (f"\n        {detail}" if detail else ""))

    def skip(self, msg: str, detail: str = ""):
        self.skips.append(msg)
        print(f"  SKIP  {msg}" + (f"\n        {detail}" if detail else ""))

    def finish(self) -> int:
        if self.skips:
            # skipped ≠ pass：本次没查的部分必须显式声明，不能混进 PASS
            print(f"\n  ⚠ 本次有 {len(self.skips)} 项 SKIP —— '没查'不等于'通过'")
        if self.fails:
            print(f"\nFAIL  {self.name}  （{len(self.fails)} 项违规）")
            return 1
        print(f"\nPASS  {self.name}"
              + (f"  （含 {len(self.warns)} 项 WARN，见上）" if self.warns else ""))
        return 0


# ---------------------------------------------------------------- 基线读写
def load_baseline() -> dict:
    """读基线文件；不存在 ⇒ 返回空 dict（不抛，便于首次运行）。"""
    if not BASELINE_FILE.exists():
        return {}
    try:
        return json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_baseline(data: dict) -> None:
    BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8")
    print(f"  [基线已写入 {BASELINE_FILE}]")


def guard_self_reference(rel_or_text) -> bool:
    """自指守卫：判断一个路径/文本是不是本套检查自己。

    为什么需要（2026-09-11 真实事故）：**扫描/替换类脚本必须显式排除自己的输出**。
    当年更名脚本扫到了自己生成的报告（内容正是"旧名→新名"对照表），
    篡改了自己的证据 ⇒ 反向断言假通过。自指是这类工具的**默认陷阱，不是意外**。
    """
    if not isinstance(rel_or_text, str):
        return False
    s = rel_or_text.replace("/", "\\")
    if s.startswith("tools\\qc\\fitness_") or s.startswith("tools\\qc\\smoke_fitness_"):
        return True
    if s.startswith("tools\\qc\\.fitness_baseline"):
        return True
    # 本骨架模块自身
    return s.endswith("fitness_common.py")
