# -*- coding: utf-8 -*-
"""路径中心验证工具 (v0.10.15+)

用法:
    python scripts/verify_paths.py            # 检查当前全程序路径一致性
    python scripts/verify_paths.py --simulate # 模拟迁移到临时目录并回滚

退出码: 0=全部通过, 1=存在问题
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))


def check_consistency() -> list:
    """检查已加载模块根目录与路径中心一致"""
    from infrastructure import path_center, path_guard
    issues = list(path_center.verify_consistency())
    marker = path_guard.read_marker(path_center.get_root())
    if marker and str(Path(marker.get("root", "")).resolve()) != str(path_center.get_root().resolve()):
        issues.append({"module": "marker", "expected": str(path_center.get_root()),
                       "actual": str(marker.get("root"))})
    return issues


def simulate_migration() -> list:
    """模拟: 切到临时假项目目录 -> 验证日志/审计/配置跟随 -> 恢复"""
    from infrastructure import audit, config, logger, path_center
    problems = []
    fake = Path(tempfile.mkdtemp(prefix="yolo_pc_sim_"))
    try:
        (fake / "src").mkdir(parents=True, exist_ok=True)
        (fake / "config.yaml").write_text(
            "app:\n  name: fake\n  version: 0.0.0\npaths:\n  log_dir: runs/logs\n",
            encoding="utf-8")
        (fake / "YOLO工作台.py").write_text("", encoding="utf-8")

        path_center.set_root(fake)
        report = path_center.resync_all()

        roots = {
            "logger": logger.get_project_root(),
            "audit": audit.get_project_root(),
            "config": config.get_project_root(),
            "path_center": path_center.get_root(),
        }
        for name, root in roots.items():
            if str(root.resolve()).lower() != str(fake.resolve()).lower():
                problems.append(f"{name} -> {root}")

        import logging
        logging.getLogger("verify_paths").info("simulated migration log")
        audit.audit("path_center_sim", "simulated migration", module="verify_paths")
        app_log = fake / "runs" / "logs" / "app.log"
        ops_log = fake / "runs" / "logs" / "operations.log"
        if not (app_log.exists() and app_log.stat().st_size > 0):
            problems.append("app.log not written to new root")
        if not (ops_log.exists() and ops_log.stat().st_size > 0):
            problems.append("operations.log not written to new root")
        if config.get_config()._root.resolve() != fake.resolve():
            problems.append("config root not switched")

        issues = path_center.verify_consistency()
        problems += [f"{it['module']} inconsistent" for it in issues]
    finally:
        # 无论成败都恢复真实目录
        try:
            path_center.set_root(ROOT)
            path_center.resync_all()
        except Exception:
            pass
        shutil.rmtree(fake, ignore_errors=True)
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description="YOLO工作台 路径中心验证工具")
    ap.add_argument("--simulate", action="store_true", help="模拟迁移到临时目录并回滚")
    args = ap.parse_args()

    from infrastructure import path_center
    from infrastructure.audit import audit

    problems = []
    sim_problems = []
    if args.simulate:
        print("== 模拟迁移验证 ==")
        sim_problems = simulate_migration()
        print("模拟迁移:", "PASS" if not sim_problems else "; ".join(sim_problems))
        problems += sim_problems

    print("== 当前路径一致性 ==")
    print("根目录:", path_center.get_root())
    print("默认目录:", ROOT)
    issues = check_consistency()
    if issues:
        print("一致性: FAIL")
        for it in issues:
            print(f"  - {it['module']}: expected={it.get('expected')} actual={it.get('actual')}")
    else:
        print("一致性: PASS (所有已加载模块指向同一根目录)")
    problems += issues

    audit("path_center_verify",
          f"验证完成: 一致={not issues} 模拟迁移={'PASS' if args.simulate and not sim_problems else 'N/A'}",
          level="info" if not issues else "warn", module="verify_paths")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
