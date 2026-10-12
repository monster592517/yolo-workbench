# -*- coding: utf-8 -*-
"""环境检测 → 推荐配套方案 → 一键安装/切换环境.

用法:
    python scripts/setup_env.py --check          # 检测并输出报告
    python scripts/setup_env.py --auto           # 检测并推荐
    python scripts/setup_env.py --install gpu8   # 按档位一键安装 pip 依赖
"""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "config" / "projects.json"
REPORT = ROOT / "runs" / "env_report.json"


def run(cmd: list[str], timeout: int = 60) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout,
                           creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        return (p.stdout or "") + (p.stderr or "")
    except Exception:
        return ""


def detect() -> dict:
    info = {
        "os": platform.platform(),
        "python": platform.python_version(),
        "cpu_cores": None,
        "ram_gb": None,
        "gpu": None,
        "gpu_vram_gb": 0.0,
        "disk_free_gb": None,
    }
    try:
        import psutil
        info["cpu_cores"] = psutil.cpu_count(logical=True)
        info["ram_gb"] = round(psutil.virtual_memory().total / 1024 ** 3, 1)
        info["disk_free_gb"] = round(psutil.disk_usage(str(ROOT)).free / 1024 ** 3, 1)
    except Exception:
        pass
    out = run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
               "--format=csv,noheader"])
    if out.strip():
        parts = [p.strip() for p in out.splitlines()[0].split(",")]
        info["gpu"] = parts[0] if parts else None
        try:
            info["gpu_vram_gb"] = round(float(parts[1].split()[0]) / 1024, 1)
        except Exception:
            pass
        if len(parts) > 2:
            info["driver"] = parts[2]
    try:
        import torch
        info["torch"] = torch.__version__
        info["cuda_available"] = bool(torch.cuda.is_available())
        if torch.cuda.is_available():
            info["cuda_device"] = torch.cuda.get_device_name(0)
    except Exception:
        info["torch"] = None
        info["cuda_available"] = False
    return info


def recommend(info: dict) -> str:
    vram = info.get("gpu_vram_gb", 0) or 0
    if vram <= 0:
        return "cpu"
    if vram < 8:
        return "gpu8" if vram >= 4 else "cpu"
    if vram < 12:
        return "gpu8"
    return "gpu12"


def collect_commands(profile: str) -> list[dict]:
    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    cmds = []
    for cat, items in reg.items():
        if cat == "说明":
            continue
        for it in items:
            tier = it.get("tier", "all")
            if profile == "cpu" and tier not in ("all", "cpu"):
                continue
            if profile == "gpu8" and tier not in ("all", "cpu", "gpu8"):
                continue
            if profile == "gpu12" and tier not in ("all", "cpu", "gpu8", "gpu12"):
                continue
            inst = it.get("install", "")
            if inst.startswith("pip "):
                cmds.append({"id": it["id"], "name": it["name"], "cmd": inst,
                             "repo": it["repo"], "purpose": it["purpose"]})
            else:
                cmds.append({"id": it["id"], "name": it["name"], "manual": inst,
                             "repo": it["repo"], "purpose": it["purpose"]})
    return cmds


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--auto", action="store_true")
    ap.add_argument("--install", choices=["cpu", "gpu8", "gpu12"], default=None)
    ap.add_argument("--mirror", default="https://pypi.tuna.tsinghua.edu.cn/simple")
    args = ap.parse_args()
    info = detect()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(info, ensure_ascii=False, indent=1))
    prof = recommend(info)
    print(f"推荐档位: {prof} (显存 {info.get('gpu_vram_gb')}GB)")
    if args.check or args.auto:
        print(f"一键执行: python scripts/setup_env.py --install {prof}")
    if args.install:
        cmds = collect_commands(args.install)
        pip_cmds = [c for c in cmds if "cmd" in c]
        manual = [c for c in cmds if "manual" in c]
        print(f"将安装 {len(pip_cmds)} 个 pip 包 (镜像 {args.mirror})")
        for c in pip_cmds:
            if "&&" in c["cmd"] or ";" in c["cmd"]:
                manual.append(c)
                continue
            cmd = c["cmd"] if "--index-url" in c["cmd"] else \
                c["cmd"].replace("pip install", f"pip install -i {args.mirror}", 1)
            print(f"  [{c['id']}] {cmd}")
            p = subprocess.run(cmd.split(), timeout=1800)
            if p.returncode != 0:
                print(f"    ! 失败: {c['id']} (可重试或换源)")
        if manual:
            print("以下项目需手动安装:")
            for c in manual:
                print(f"  {c['name']}: {c['manual']} | {c['repo']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
