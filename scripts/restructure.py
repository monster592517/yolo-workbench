# -*- coding: utf-8 -*-
"""目录规范化: 检查/补齐企业级目录结构 (纯安全操作, 不做破坏性移动).

用法:
    python scripts/restructure.py --check   # 检查目录
    python scripts/restructure.py --apply   # 补齐缺失目录
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EXPECTED = [
    ("assets", "图标/静态资源"),
    ("config", "配置/开源注册表/预设"),
    ("data", "数据集"),
    # ("docs", "说明书/规划"),   ← 2026-09-26 移除：文档已提到项目**第一层**，
    #                              留着这一条 `--apply` 会把空的 docs/ 重新建出来，
    #                              而空目录会让下一个人以为文档还在那儿。
    ("inputs", "输入素材"),
    ("outputs", "导出结果"),
    ("runs", "运行产物/日志"),
    ("scripts", "运维/生成/训练/审计脚本"),
    ("src", "源码"),
    ("tests", "测试"),
    ("tools", "安装包/Blender 等工具"),
    ("ckpts", "模型检查点"),
]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    missing = []
    for name, desc in EXPECTED:
        p = ROOT / name
        if not p.exists():
            missing.append((name, desc))
        else:
            print(f"[OK] {name}/ ({desc})")
    if missing:
        print("缺失目录:")
        for name, desc in missing:
            print(f"  - {name}/ ({desc})")
        if args.apply:
            for name, desc in missing:
                (ROOT / name).mkdir(parents=True, exist_ok=True)
                print(f"[创建] {name}/")
            print("目录补齐完成; 请运行 adapt_paths.py 同步路径")
        else:
            print("提示: 使用 --apply 补齐")
        return 1 if not args.apply else 0
    print("目录结构 OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
