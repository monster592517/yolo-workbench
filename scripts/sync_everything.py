# -*- coding: utf-8 -*-
"""sync_everything.py — 一键全量同步链 (基本规则: 每次改动后必须运行)

运行:
    python scripts/sync_everything.py "本次变更说明"

同步内容 (顺序即依赖):
    1. 介绍图       : archify validate + deliver 7 个 HTML
                      (工作台 架构/流程/时序/状态 · 工坊 架构/流程 · 双系统 共享与边界)
                      + 可选 `--visual` 跑可读性门 tools/qc/diagram_visual_gate.py
    2. 发布同步链   : scripts/sync_release.py
                     (路径/审计日志/ultimate_audit 自检/自我修复/CHANGELOG/系统部署与维护手册/桌面快捷方式/安装包重建)
    3. 介绍手册     : 提示人工核对 README/维护手册/使用说明书/项目进度总览 是否已登记本次变更
                       (自动部分由 sync_release 完成; 手册正文条目建议与变更说明同步更新)

为什么 `--visual` 默认关（2026-09-17 实测, 维修手册 #198）:
    `validate` 判的是**结构**(标签超宽/连线穿节点), 它管不了"渲染出来字够不够大";
    `deliver` 只保证**文件写出来了** ⇒ "7 图全部交付完成"与"7 图能看清"是两件事。
    门(`diagram_visual_gate.py`)补的正是这一环, 但**代价是 7 次浏览器进程 / 实测 43 秒**
    (本机内存只余 2 GB 量级, 且同步链本身还要重建安装包) ⇒ 不塞进默认路径。
    改图之后、或发布前跑一次:
        python tools/qc/diagram_visual_gate.py
    或让本链顺手带上:  python scripts/sync_everything.py "说明" --visual
    ⚠️ 门报红**不阻塞**本链(只打印) —— 否则 4 张老图在 1440 宽下天天报红,
       反而会训练人忽略告警(#198 处置 ②)。
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

#: 子进程一律**不弹控制台窗口**（项目「弹窗纪律」，维修手册 §N 一族）。
#: ★ 为什么本文件要单独加（2026-09-17 实测）：`scripts/` 整条路**不在 doctor 的
#:   控制台纪律视野里** —— 那个检查只扫 `tools/h3`（`studio_doctor.py` 的 `H3`/
#:   `_scan_h3_sources`）⇒ 同步链每拉一个 node / python 子进程，Windows 就
#:   **分配一个新控制台**，屏幕上弹一个窗；一次同步要拉十几个 ⇒ **满屏弹窗**。
#:   与 §N.6 的教训同型：**修一条实现路径，弹窗来自另一条**（那次是"另一个 API"，
#:   这次是"另一个目录"）。
NO_WINDOW = 0x08000000 if os.name == "nt" else 0

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
ARCHIFY = ROOT / "tools" / "archify" / "archify" / "bin" / "archify.mjs"

DIAGRAMS = [
    ("architecture", "workbench.architecture.json", "YOLO工作台_架构图.html"),
    ("workflow", "workbench.workflow.json", "YOLO工作台_流程图.html"),
    ("sequence", "workbench.sequence.json", "YOLO工作台_时序图.html"),
    ("lifecycle", "workbench.lifecycle.json", "YOLO工作台_状态图.html"),
]


def log(msg: str):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def run(cmd: list, timeout: int = 600) -> bool:
    log(">> " + " ".join(cmd[:4]) + "...")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           creationflags=NO_WINDOW)
        tail = (r.stdout or "") + (r.stderr or "")
        ok = r.returncode == 0
        if not ok:
            for ln in tail.splitlines()[-25:]:
                print("   ", ln.strip())
        return ok
    except Exception as e:
        log(f"  !! {e}")
        return False


def run_visual_gate() -> int:
    """跑可读性门（`tools/qc/diagram_visual_gate.py`），**只打印、不阻塞本链**。

    返回门的退出码（0 全过 / 1 有未达标 / 2 未运行），供调用方自行决定用处。
    ★ 不把门的失败加进 `problems`：门报红是"图能不能看清"的信息，
      不是"同步链坏了"。混在一起会让同步链天天以非零码退出 ⇒ 训练人忽略它。
    """
    gate = ROOT / "tools" / "qc" / "diagram_visual_gate.py"
    if not gate.exists():
        log("  [跳过] 未找到 tools/qc/diagram_visual_gate.py")
        return 2
    log("  --- 可读性门 (diagram_visual_gate) ---")
    try:
        r = subprocess.run([PY, "-X", "utf8", str(gate)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600,
                           creationflags=NO_WINDOW)
    except Exception as e:  # noqa: BLE001
        log(f"  !! 门没跑起来: {e}")
        return 2
    for ln in (r.stdout or "").splitlines():
        log("  " + ln)
    if r.returncode != 0:
        log(f"  [注意] 可读性门 rc={r.returncode}"
            f"（{'未运行 —— 拿不到读数不等于通过' if r.returncode == 2 else '有图未达标'}）"
            "；**不阻塞本次同步**，详见维修手册 #198")
    return r.returncode


def main():
    try:
        sys.stdout.reconfigure(errors="replace")  # 兼容 GBK 控制台 (h3_setup 输出含 ✔ 等字符)
    except Exception:
        pass
    argv = [a for a in sys.argv[1:] if a != "--visual"]
    want_visual = len(argv) != len(sys.argv[1:])
    message = argv[0] if argv else "常规更新"
    problems = []

    # 0) 解释器预检（维修手册 #203）—— 必须在跑任何子步骤**之前**。
    #    链上每个子脚本都用 `PY = sys.executable` 启动 ⇒ **用什么解释器拉起链，整条链就用什么**。
    #    误用别的解释器（例如 .workbuddy 自带的 managed python，没有 yaml/numpy）时，
    #    症状是**三条互不相干的报错**：
    #      · h3_setup     → ModuleNotFoundError: No module named 'yaml'
    #      · sync_release → 第 1 步「[错误] 配置加载失败: No module named 'yaml'」
    #      · 自检         → 「[PASS] H3 Factory ... No module named 'numpy'」
    #    ⇒ 同一个根因被拆成三条"问题"，**而真实原因一个字都不出现**。
    #    宁可在第 0 步用一句清楚的话停住，也不要让链带着坏解释器跑完再报三个假故障。
    try:
        import yaml  # noqa: F401
    except ImportError:
        log(f"!! 解释器缺少项目依赖 yaml —— 当前解释器: {sys.executable}")
        log("   ⇒ 请用**项目解释器**重跑（桌面快捷方式「YOLO工作台」"
            "指向的那个 python.exe，不是 .workbuddy 自带的）")
        sys.exit(2)

    # 1) 介绍图 (工作台四类 + 工坊两张 + 双系统共享面一张)
    log("=== 1/3 介绍图 7 张 (archify validate + deliver) ===")
    if ARCHIFY.exists() and (ROOT / "archify").exists():
        for dtype, jname, html in DIAGRAMS:
            src = ROOT / "archify" / jname
            out = ROOT / "archify" / html
            if not run(["node", str(ARCHIFY), "validate", dtype, str(src), "--quality", "standard", "--json"]):
                problems.append(f"archify validate {dtype}")
                continue
            if not run(["node", str(ARCHIFY), "deliver", dtype, str(src), str(out), "--quality", "standard", "--json"]):
                problems.append(f"archify deliver {dtype}")
        log("  7 图: " + ("全部交付完成" if not problems else "存在失败"))
        # 1b) 可读性门 (可选, 不阻塞) —— validate 管结构, 这里才管"看不清" (#198)
        if want_visual:
            run_visual_gate()
        else:
            log("  [提示] 未跑可读性门; 改过图之后请跑 python tools/qc/diagram_visual_gate.py"
                " (或本次加 --visual)")
    else:
        log("  [跳过] 未找到 tools/archify 或 archify")


    # 3) 发布同步链 (审计/自检/自修/日志/手册/快捷方式/安装包)
    log("=== 2/2 发布同步链 (sync_release.py) ===")
    if not run([PY, str(ROOT / "scripts" / "sync_release.py"), "-m", message], timeout=900):
        problems.append("sync_release")

    log("=" * 50)
    if problems:
        log(f"同步完成, 存在 {len(problems)} 个问题: {problems}")
        sys.exit(1)
    log("一键同步完成: 介绍图 7 张/环境配置/审计/自检/自修/日志/手册/快捷方式/安装包 全部通过")


if __name__ == "__main__":
    main()
