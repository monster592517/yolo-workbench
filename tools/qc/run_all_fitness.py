# -*- coding: utf-8 -*-
"""一键跑完在册的适应度函数 + 它们的正负对照。

为什么需要它
----------
判据分散在多个文件里，人不会记得逐个跑。
本文件是**唯一入口**：跑一次，给一张表，返回一个退出码。

⚠️ 「在册」是**动态读来的**，不是写死的。标题里的条数由 `FITNESS` / `SMOKES`
   两张表的长度决定 —— 不写死数字。原因：2026-10-10 开源清理搬走了 32 个守卫，
   而本文件的标题还写着"4 条判据 + 4 个正负对照"，**输出在撒谎**。
   （同族教训：文档里的条数、日期这类会变的东西写下的那一刻就开始烂。）

退出码分级（这三档必须分开 —— "检查坏了"和"架构漂移了"是两件事）
---------------------------------------------------------------
    0 = 全绿
    1 = **架构漂移**：至少一条 fitness 报 FAIL（仓库有违规，判据本身是好的）
    2 = **判据失效**：至少一个 smoke 报 FAIL（检查自己坏了 —— 这比 1 更严重，
        因为此时 fitness 报的"通过"不可信）

为什么 2 比 1 严重：`smoke_*` 是 `fitness_*` 的体检。体检坏了，
"体检结果正常"这句话就没有意义了（假绿 —— 见 `silent-failures` skill）。

弹窗纪律
-------
所有子进程都带 `CREATE_NO_WINDOW`。本仓库吃过这个亏（手册 **#204**：
`scripts/` 整条路曾在"弹窗纪律"视野之外，9 处 `subprocess.run` 全部无 `creationflags`）。

用法
----
    python tools/qc/run_all_fitness.py            # 跑全部
    python tools/qc/run_all_fitness.py --quiet    # 只打印汇总表
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

HERE = Path(__file__).resolve().parent

# ⚠️ 用 `sys.executable`（即跑本文件的解释器）。
#    安全前提：这 8 个脚本**只用标准库**，不依赖 venv 里的第三方包。
#    （反面教材：手册 #203 —— `sync_everything.py` 用 `PY = sys.executable`，
#     误用 managed python 时整条链静默降级，同一根因被拆成三条互不相干的问题。
#     这里不适用，因为无第三方依赖；但**将来若给 fitness 加了依赖，必须改这里**。）
PY = sys.executable

# (显示名, 文件名, 期望退出码语义)
# ★ 2026-10-01：H3 / 仿真 / 部署三条线搬出本仓后，守它们的判据也一并搬走了
#   （fitness_gui_seam / fitness_studio_consumers / fitness_subprocess_truth
#    及各自 smoke —— 它们判的是 `core.studio` 与 ComfyUI 启动参数）。
#   本仓只剩 YOLO 主线，判据收敛到「硬编码根路径」这一条架构约束。
FITNESS = [
    ("① 硬编码根路径不得增长", "fitness_hardcoded_root.py"),
    # ★ 2026-10-11 注册：**射程缺口是实测撞出来的** —— 全仓 34 处 `C:\Users\<账户>\…`
    #   一直没人守（①守的是**项目根路径**，问"搬家会不会断"；这条守的是**家目录路径**，
    #   问"开源带出去会不会暴露账户名 / 让别人照抄一个不存在的路径"—— 是两个病）。
    ("② 不许带出本机家目录路径", "fitness_personal_paths.py"),
]
SMOKES = [
    ("smoke ①", "smoke_fitness_hardcoded.py"),
    # ★ 2026-10-03 (v0.34.1): 「切检测源时整个主窗口变小」的复发守卫。
    #   真因是 mss 在第一次抓屏时把进程 DPI 觉察度从 UNAWARE 翻成 PER_MONITOR,
    #   Windows 随即停止 1.354x 插值放大 ⇒ 占屏宽 100% -> 73.8% (见 CHANGELOG v0.34.1)。
    #   这条守卫的 D 段直接断言"切源前后觉察度不变", 是唯一对应"窗口会不会缩"的判据。
    ("smoke ②", "smoke_dpi_awareness.py"),
    # ★ 2026-10-10 注册：`doc_refs` 的体检（31 项正/负/拆分/畸形对照）。
    #   为什么值得进总检：它守的是 `CHANGELOG.md` 里「文档同步：」声明与真实锚点
    #   必须对得上 —— 管的是**文档诚实性**，而开源项目的 README/CHANGELOG
    #   正是外人最先信的东西。
    #   （2026-10-10 之前它不在册，因为当时它 FAIL：判据不知道大部头被拆成两半。
    #    修法是**扩判据射程**（认 `_archive/` 分片），不是把 CHANGELOG 改到不报。）
    ("smoke ③", "smoke_doc_refs.py"),
    # ★ 2026-10-10 注册：`diagram_visual_gate` 的体检（59 项）。
    #   守的是 `sync_everything.py` 的**可读性门** —— 图"交付出来了"和"能看清"
    #   是两件事，而只跑 `validate`（结构）管不了字体大小。
    #   ⚠️ 这条特别值得进门：它的核心负对照是**自指陷阱**（扫描工具扫到自己的输出），
    #      而这正是本项目反复踩的头号坑 —— 值得每次提交都跑一遍。
    #   （2026-10-10 之前它 FAIL 5 项，但失败信息全是**断言过期**：
    #    "真同步链抽出 7 张"里的 7 是四线剥离前的旧数，与本仓实际 4 张对不上。
    #    修法：数量从 `DIAGRAMS` 真源派生；sidecar 夹具改为**自造**，
    #    不再依赖 `archify/` 根目录里恰好有残留。）
    ("smoke ④", "smoke_diagram_visual_gate.py"),
    # 2026-10-11 新增。守的是「**标注内容**」这一层 —— 前面几个 smoke 守的是
    # 文档与架构图，而实测里最高频的训练失败成因在标注内容上（字段数错 /
    # 坐标没归一化 / 类别 ID 越界），且 ultralytics 对这些要么报错指向别处、
    # 要么**静默当成零标签**。本 smoke 自带正负对照：
    # 干净数据集必过 · 坏标注必报 · **空标签不许误报**（YOLO 里空 = 背景图，合法）。
    ("smoke ⑤", "smoke_dataset_qc.py"),
    # ★ 2026-10-11 注册：判据 ② 的正负对照 —— 负（四种家目录形态必报）+
    #   正（`%USERPROFILE%`/`yourname`/`&lt;你&gt;`/`...`/`$USER` **一个都不许报**，
    #   误报会训练人忽略自检）+ 台账完整性（史实豁免与自指豁免必须还在）。
    ("smoke ⑥", "smoke_personal_paths.py"),
]

CF = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if sys.platform == "win32" else 0


def run_one(fn: str, quiet: bool) -> tuple[int, str]:
    p = HERE / fn
    if not p.is_file():
        return 2, f"缺文件 {fn}"
    r = subprocess.run([PY, str(p)], capture_output=True, cwd=str(HERE.parent.parent),
                       creationflags=CF)
    out = (r.stdout or b"").decode("utf-8", "replace")
    err = (r.stderr or b"").decode("utf-8", "replace")
    if not quiet:
        print(out.rstrip())
        if err.strip():
            print("  [stderr] " + err.strip()[:400])
        print()
    return r.returncode, out


def main() -> int:
    quiet = "--quiet" in sys.argv
    rows: list[tuple[str, int, str]] = []

    print("=" * 74)
    print(f"  适应度函数总检 —— {len(FITNESS)} 条判据 + {len(SMOKES)} 个正负对照")
    print("=" * 74)
    print()

    for label, fn in FITNESS:
        rc, out = run_one(fn, quiet)
        tail = ""
        for ln in out.splitlines():
            if ln.startswith(("FAIL ", "PASS ", "SKIP ")):
                tail = ln.strip()
        rows.append((label, rc, tail))

    print("-" * 74)
    print("  正负对照（判据的体检）")
    print("-" * 74)
    print()
    smoke_rc = 0
    for label, fn in SMOKES:
        rc, out = run_one(fn, quiet)
        # ★ 退出码 3 = SKIP：**环境不满足前置条件**（如缺 mss/tkinter），
        #   既不是"判据通过"也不是"判据失效" —— 不参与 max()，也不报红。
        #   （2026-10-10：裸解释器跑会缺依赖；把它当 FAIL 会让人对红叉免疫。）
        if rc != 3:
            smoke_rc = max(smoke_rc, rc)
        tail = ""
        for ln in out.splitlines():
            if ln.startswith(("FAIL ", "PASS ", "SKIP ")) or "对照结果" in ln:
                tail = ln.strip()
        rows.append((label, rc, tail))

    print("=" * 74)
    print("  汇总")
    print("=" * 74)
    for label, rc, tail in rows:
        mark = {0: "OK  ", 1: "FAIL", 2: "ERR ", 3: "SKIP"}.get(rc, "?   ")
        print(f"  [{mark}] {label:<38} {tail}")

    fit_fail = [lab for lab, rc, _t in rows[:len(FITNESS)] if rc not in (0, 3)]
    smk_fail = [lab for lab, rc, _t in rows[len(FITNESS):] if rc not in (0, 3)]
    skipped = [lab for lab, rc, _t in rows if rc == 3]

    print()
    if skipped:
        print(f"  ⏭ 跳过（环境不满足前置条件，未验证）：{skipped}")
        print("    ⇒ 建议先 pip install -r requirements.txt 再重跑。")
    if smk_fail:
        print(f"  ⚠ 判据体检失败：{smk_fail}")
        print("    ⇒ 此时 fitness 报的\"通过\"**不可信**（假绿）。先修判据本身。")
        return 2
    if fit_fail:
        print(f"  架构漂移：{fit_fail}")
        print("    ⇒ 判据本身是好的，是仓库有违规。按上面各条的修法处理。")
        return 1
    if skipped:
        # ★ 有跳过就**不许**说"全绿" —— 没验证过的东西不能算通过。
        print(f"  通过（有跳过）：{len(rows) - len(skipped)} 项通过，"
              f"{len(skipped)} 项因环境未就绪**未验证**：{skipped}")
        return 0
    print("  全绿：判据守住，体检通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
