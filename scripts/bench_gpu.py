# -*- coding: utf-8 -*-
"""GPU 训练吞吐基准: 用真实跌倒检测数据集快速跑 1-2 epoch.

用法:
    python scripts/bench_gpu.py [--epochs 2] [--batch 64] [--imgsz 640]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--model", default=str(ROOT / "models" / "yolo11n.pt"))
    ap.add_argument("--name", default="gpu_bench")
    args = ap.parse_args()

    import torch
    print("[GPU]", torch.cuda.get_device_name(0),
          torch.cuda.get_device_properties(0).total_memory / 1024 ** 3, "GB", flush=True)
    from ultralytics import YOLO
    m = YOLO(args.model)
    m.train(
        data=str(ROOT / "data" / "datasets" / "fall_dataset" / "fall_dataset" / "data.yaml"),
        epochs=args.epochs, imgsz=args.imgsz, batch=args.batch, device=0,
        cache="ram", workers=8, project=str(ROOT / "runs" / "train_bench"),
        name=args.name, plots=False, verbose=True, deterministic=False,
        close_mosaic=1,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
