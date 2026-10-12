# -*- coding: utf-8 -*-
"""YOLO工作台统一维护入口。

将旧版分散的诊断、校验和性能工具收敛到一个稳定命令入口；旧脚本继续保留，
作为兼容层和专业场景工具，不改变现有模块调用关系。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


COMMANDS = {
    "audit": ("scripts/ultimate_audit.py", []),
    "paths": ("scripts/verify_paths.py", []),
    "models": ("scripts/verify_model.py", []),
    "environment": ("scripts/setup_env.py", ["--check"]),
    "ui": ("scripts/ui_visual_check.py", []),
    "restructure": ("scripts/restructure.py", ["--check"]),
}


def _run_script(script: str, extra: list[str]) -> int:
    target = ROOT / script
    if not target.exists():
        print(f"[ERROR] 维护脚本不存在: {target}")
        return 2
    command = [sys.executable, str(target), *extra]
    try:
        return subprocess.run(command, cwd=str(ROOT)).returncode
    except OSError as exc:
        print(f"[ERROR] 无法执行 {target}: {exc}")
        return 2


def inventory() -> int:
    """输出可迁移的项目清单，忽略外部工具、运行产物和虚拟环境。"""
    excluded = {".venv_3dgs", "runs", "data", "outputs", "tools", "dev"}
    rows = []
    for path in sorted(ROOT.iterdir()):
        if path.name in excluded:
            continue
        if path.is_dir():
            count = sum(1 for item in path.rglob("*") if item.is_file())
            rows.append((path.name + "/", count))
        else:
            rows.append((path.name, 1))
    print("YOLO工作台可迁移核心清单")
    for name, count in rows:
        print(f"  {name:<34} {count:>5} 文件")
    print("提示: tools/.venv_3dgs/runs/data/outputs 为可选重资产，不纳入核心迁移包")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="YOLO工作台统一维护入口（旧脚本仍可单独运行）")
    sub = parser.add_subparsers(dest="command")
    for name in COMMANDS:
        sub.add_parser(name)
    sub.add_parser("inventory")
    sub.add_parser("help")
    args, unknown = parser.parse_known_args()
    if args.command == "inventory":
        return inventory()
    if args.command in COMMANDS:
        script, defaults = COMMANDS[args.command]
        return _run_script(script, [*defaults, *unknown])
    parser.print_help()
    return 0 if args.command == "help" else 1


if __name__ == "__main__":
    raise SystemExit(main())
