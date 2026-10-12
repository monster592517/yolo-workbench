# -*- coding: utf-8 -*-
"""摄像头排查向导 (命令行版) — 运行: python scripts/camera_diagnose.py

输出完整诊断报告 + 分步排查清单, 并写入 runs/logs/operations.log 审计记录。
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def main() -> int:
    from core.camera_probe import diagnose, format_diagnose_report
    print("正在诊断摄像头, 请稍候 (约 10-20 秒)...")
    diag = diagnose()
    report = format_diagnose_report(diag)
    print("\n" + report)
    try:
        from infrastructure.audit import audit
        audit("camera_diagnose",
              f"设备={len(diag['devices'])} 隐私={diag['privacy']} "
              f"探测={diag.get('probe', {}).get('mean')}",
              level="warn" if not (diag.get("probe") or {}).get("frames")
              or (diag.get("probe") or {}).get("black_frames", 0) > 0 else "info",
              module="camera_diagnose")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
