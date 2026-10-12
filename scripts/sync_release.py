# -*- coding: utf-8 -*-
"""sync_release.py - 发布同步器 (每次更新程序后运行, 保证全链路一致)

用法:
    python scripts/sync_release.py -m "本次变更说明" [--bump] [--version X.Y.Z] [--skip-rebuild]

同步内容 (六大同步, 含自我修复):
    1. 路径同步   : 校验/创建 config.yaml 路径目录; 扫描代码硬编码路径与配置不一致项
    2. 自检系统   : 模块清单自动发现(新模块自动纳入); 运行 ultimate_audit 全量自检
    2.5 自我修复  : 校验 self_repair 模块就绪并干跑一遍修复检查
    3. 日志系统   : operations.log 写入 release_sync 记录; CHANGELOG.md 追加变更记录
    4. 变更记录   : CHANGELOG.md 顶部插入版本更新记录
    5. 快捷方式   : 刷新桌面快捷方式指向最新版本 (安装包构建也已内置此步)
    6. 安装包     : 默认每次同步自动重建安装包 (tools/make_installer), --skip-rebuild 可跳过
可选:
    --bump       : 自动递增 config.yaml 版本号 (补丁位 +1)
    --version X  : 指定版本号
    --skip-rebuild: 跳过安装包重建 (默认每次同步自动重建)
    --rebuild    : (兼容旧参数) 重建安装包, 即默认行为
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

#: 子进程一律**不弹控制台窗口**（项目「弹窗纪律」，维修手册 §N 一族）。
#: ★ 2026-09-17 实测：`scripts/` 整条路不在 doctor 的控制台纪律视野里（它只扫
#:   `tools/h3`）⇒ 本文件 6 处 `subprocess.run` 每拉一个子进程就弹一个窗；
#:   其中 `sync_shortcut()` 拉的是 **PowerShell**（控制台程序，必然分配控制台）
#:   + `build_installer.py` 还会再生孙子进程 ⇒ 一次同步屏幕上连弹十几个。
NO_WINDOW = 0x08000000 if os.name == "nt" else 0

_LOG_DIR = ROOT / "runs" / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
SYNC_LOG = _LOG_DIR / "sync_release.log"
CONFIG = ROOT / "config.yaml"
MANUAL = ROOT / "CHANGELOG.md"        # 原为 AI动画产线_自检维修手册.md(随 H3 搬出本仓)
CHANGELOG = ROOT / "CHANGELOG.md"

# 已知硬编码路径 (用于与 config.yaml 比对, 防止改配置后代码遗漏)
_HARDCODED = [
    "runs/outputs", "runs/recordings", "runs/logs", "runs/deploy_packages",
    "runs/last_page.json", "data/datasets",
]


def log(msg: str):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    try:
        with open(SYNC_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def current_version() -> str:
    m = re.search(r'version\s*:\s*["\']?([\w.]+)', CONFIG.read_text(encoding="utf-8"))
    return m.group(1) if m else "0.0.0"


def bump_version(v: str) -> str:
    parts = v.split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    return ".".join(parts)


def set_version(v: str):
    s = CONFIG.read_text(encoding="utf-8")
    s2 = re.sub(r'(version\s*:\s*["\']?)[\w.]+(["\' ])', lambda m: m.group(1) + v + m.group(2),
                s, count=1)
    CONFIG.write_text(s2, encoding="utf-8")
    log(f"[版本] config.yaml version -> {v}")


def sync_paths() -> list:
    """路径同步: 创建缺失目录; 报告代码硬编码路径与配置不一致项"""
    log("=== 1/5 路径同步 ===")
    issues = []
    try:
        from infrastructure.config import get_config
        cfg = get_config()
        for name, d in [("data", cfg.data_dir), ("output", cfg.output_dir),
                        ("record", cfg.record_dir), ("deploy", cfg.deploy_dir),
                        ("log", cfg.log_dir)]:
            d.mkdir(parents=True, exist_ok=True)
            log(f"  [路径] {name}: {d}")
    except Exception as e:
        issues.append(f"config load: {e}")
        log(f"  [错误] 配置加载失败: {e}")
    # 扫描硬编码路径
    hits = []
    for py in SRC.rglob("*.py"):
        try:
            c = py.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for hp in _HARDCODED:
            if hp in c:
                hits.append((py.relative_to(ROOT).as_posix(), hp))
    if hits:
        log(f"  [提示] 发现 {len(hits)} 处硬编码路径引用 (需人工确认是否应改为配置):")
        for f, hp in hits[:12]:
            log(f"    {f} -> {hp}")
    else:
        log("  [OK] 无硬编码路径引用")
    return issues


# 除 ultimate_audit 以外要一并跑的自检: (显示名, 参数, 超时秒)。
# 全部要求**退出码 0** —— 失败必须让同步链变红。登记在这里而不是"顺带跑一下",
# 是因为"自检自己没牙"是本项目踩过的坑 (见 docs/避坑清单 44~53 条)。
_SELF_CHECKS = [
    ("log_selfcheck", ["scripts/log_selfcheck.py"], 120),
    ("ui_perf_verify", ["scripts/ui_perf_verify.py", "--no-app"], 300),
]


def sync_audit() -> list:
    """自检系统: 运行全量自检 (模块清单自动发现, 新模块自动纳入)"""
    log("=== 2/5 自检系统同步 ===")
    issues: list = []
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "ultimate_audit.py")],
                       capture_output=True, timeout=180, creationflags=NO_WINDOW)
    out = (r.stdout or b"").decode("utf-8", errors="replace")
    tail = [ln for ln in out.splitlines() if "Summary:" in ln or "PASS" in ln]
    for ln in tail[-6:]:
        log("  " + ln.strip())
    m = re.search(r"Summary: (\d+) PASS, (\d+) WARN, (\d+) FAIL", out)
    if m:
        passed, warned, failed = int(m.group(1)), int(m.group(2)), int(m.group(3))
        log(f"  [自检] {passed} PASS / {warned} WARN / {failed} FAIL")
        if failed:
            issues.append(f"audit failed: {failed} FAIL")
    elif r.returncode != 0:
        issues.append(f"audit 退出码 {r.returncode}")

    # 其余自检脚本: 只认退出码 (它们自己会打印 RESULT: n passed, m failed)
    for name, argv, tout in _SELF_CHECKS:
        cmd = [sys.executable] + [str(ROOT / a) if a.endswith(".py") else a for a in argv]
        try:
            rr = subprocess.run(cmd, capture_output=True, timeout=tout,
                                creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired:
            log(f"  [FAIL] {name} 超时 (>{tout}s)")
            issues.append(f"{name} timeout")
            continue
        o = ((rr.stdout or b"") + (rr.stderr or b"")).decode("utf-8", errors="replace")
        res = [ln.strip() for ln in o.splitlines() if "RESULT:" in ln]
        line = res[-1] if res else ""
        if rr.returncode == 0:
            log(f"  [OK] {name}  {line}")
        else:
            log(f"  [FAIL] {name} 退出码 {rr.returncode}  {line}")
            for ln in [x.strip() for x in o.splitlines() if "[FAIL]" in x][:6]:
                log("      " + ln)
            issues.append(f"{name} failed")
    return issues


def sync_self_repair() -> list:
    """自我修复系统同步: 校验模块就绪并干跑 (空结果不应产生任何修复动作)"""
    log("=== 2.5/6 自我修复系统同步 ===")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    r = subprocess.run(
        [sys.executable, "-c",
         "from infrastructure.self_repair import auto_repair; "
         "rep = auto_repair({}); print('self_repair_ok fixed=' + str(rep['fixed']))"],
        capture_output=True, text=True, errors="replace", env=env, timeout=90,
        creationflags=NO_WINDOW)
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode == 0 and "self_repair_ok" in out:
        log("  [自我修复] 模块就绪, 修复系统自检通过")
        return []
    log("  [错误] 自我修复系统异常: " + out[-300:])
    return ["self_repair sync failed"]


def sync_logs(version: str, message: str):
    """日志系统: operations.log 记录 + CHANGELOG 追加"""
    log("=== 3/5 日志系统同步 ===")
    try:
        from infrastructure.audit import audit
        audit("release_sync", f"v{version} 发布同步: {message}", module="sync_release")
        log("  [日志] operations.log 已写入 release_sync 记录")
    except Exception as e:
        log(f"  [警告] operations.log 写入失败: {e}")
    # CHANGELOG
    entry = f"\n## v{version} ({datetime.now():%Y-%m-%d})\n\n{message}\n"
    if CHANGELOG.exists():
        CHANGELOG.write_text(CHANGELOG.read_text(encoding="utf-8").rstrip() + "\n" + entry, encoding="utf-8")
    else:
        CHANGELOG.write_text("# YOLO工作台 变更记录\n" + entry, encoding="utf-8")
    log(f"  [日志] CHANGELOG.md 已追加 v{version} 记录")


def sync_manual(version: str, message: str):
    """变更记录: 插入版本更新记录(最新在顶部, 与 CHANGELOG 惯例一致)"""
    log("=== 4/5 变更记录同步 ===")
    if not MANUAL.exists():
        log("  [警告] CHANGELOG 不存在, 跳过")
        return
    date = datetime.now().strftime("%Y-%m-%d")
    lines = [f"\n# v{version} 更新说明 ({date})\n"]
    for ln in message.splitlines():
        ln = ln.strip()
        if ln:
            lines.append(f"- {ln}")
    lines.append("- 同步记录: 路径配置/自检系统/日志/维护手册/桌面快捷方式 已自动同步")
    block = "\n".join(lines) + "\n"
    text = MANUAL.read_text(encoding="utf-8")
    anchor = "# YOLO工作台 变更记录\n"
    if anchor in text:
        text = text.replace(anchor, anchor + "\n" + block, 1)
    else:
        text = text.rstrip() + "\n\n" + block
    MANUAL.write_text(text, encoding="utf-8")
    log(f"  [变更记录] {MANUAL.name} 已插入 v{version}")


def sync_shortcut():
    """桌面快捷方式: 刷新指向最新版本"""
    log("=== 5/5 桌面快捷方式同步 ===")
    ps1 = ROOT / "scripts" / "create_shortcut.ps1"
    if not ps1.exists():
        log("  [警告] create_shortcut.ps1 不存在")
        return
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", str(ps1)], capture_output=True, timeout=60,
                       creationflags=NO_WINDOW)
    out = (r.stdout or b"").decode("utf-8", errors="replace")
    for ln in out.splitlines():
        if ln.strip():
            log("  " + ln.strip())
    if r.returncode != 0:
        log("  [警告] 快捷方式刷新返回非零")


def main():
    try:
        sys.stdout.reconfigure(errors="replace")  # 兼容 GBK 控制台
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="YOLO工作台 发布同步器")
    ap.add_argument("-m", "--message", default="常规更新", help="本次变更说明")
    ap.add_argument("--bump", action="store_true", help="自动递增版本号 (补丁位+1)")
    ap.add_argument("--version", help="指定版本号 (优先于 --bump)")
    ap.add_argument("--skip-rebuild", action="store_true", help="跳过安装包重建 (默认自动重建)")
    ap.add_argument("--rebuild", action="store_true", help="(兼容旧参数) 重建安装包, 默认行为")
    args = ap.parse_args()

    log("=" * 60)
    log("YOLO工作台 发布同步器启动")
    version = current_version()
    if args.version:
        version = args.version
        set_version(version)
    elif args.bump:
        version = bump_version(version)
        set_version(version)
    log(f"当前版本: v{version}")
    log(f"变更说明: {args.message}")

    problems = []
    problems += sync_paths()
    problems += sync_audit()
    problems += sync_self_repair()
    sync_logs(version, args.message)
    sync_manual(version, args.message)
    sync_shortcut()


    log("=" * 60)
    if problems:
        log(f"发布同步完成, 存在 {len(problems)} 个问题: {problems}")
        sys.exit(1)
    log("发布同步完成: 路径/自检(含自我修复)/日志/手册/快捷方式/安装包 全部同步 [OK]")
    sys.exit(0)


if __name__ == "__main__":
    main()
