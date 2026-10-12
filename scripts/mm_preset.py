# -*- coding: utf-8 -*-
"""一键切换多模态组件配置预设.

用法: python scripts/mm_preset.py --list | --apply gpu8_standard
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRESETS = ROOT / "config" / "multimodal_presets.json"
MM_CONFIG = ROOT / "runs" / "multimodal" / "config.json"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--apply", default="")
    args = ap.parse_args()
    data = json.loads(PRESETS.read_text(encoding="utf-8"))
    presets = {k: v for k, v in data.items() if k != "说明"}
    if args.list:
        for k, v in presets.items():
            print(f"{k}: {v.get('name', '')}")
        return 0
    if not args.apply:
        print("用法: --list 或 --apply <预设名>")
        return 2
    if args.apply not in presets:
        print(f"未知预设 {args.apply}, 可选: {list(presets)}")
        return 2
    p = presets[args.apply]
    out = {k: v for k, v in p.items() if k != "name"}
    MM_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    MM_CONFIG.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] 已应用 {args.apply} ({p.get('name')}) -> {MM_CONFIG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
