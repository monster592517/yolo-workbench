# -*- coding: utf-8 -*-
"""路径迁移适配工具 — 手动运行: python scripts/adapt_paths.py [--scan]

用途: 项目文件夹迁移到新位置后, 自动修复:
  - data/**/data.yaml 的 path 字段
  - 桌面快捷方式 (指向当前目录)
  - 运行目录补建 + 根目录标记 + 审计/CHANGELOG 记录
也支持 --scan 仅预览不改动。
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

if __name__ == "__main__":
    from infrastructure.path_guard import cli
    sys.exit(cli())
