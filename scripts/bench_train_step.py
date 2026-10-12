# -*- coding: utf-8 -*-
"""基准: 一个真实训练步 (前向+损失+反向+优化器) 的耗时."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    from ultralytics import YOLO
    m = YOLO(str(ROOT / "models" / "yolo11m-pose.pt"))
    model = m.model.cuda().half().train()
    opt = torch.optim.AdamW(model.parameters(), lr=1e-3)
    n = 60
    batch = {
        "img": torch.randn(32, 3, 512, 512).cuda().half(),
        "cls": torch.randint(0, 2, (n,)).cuda(),
        "bboxes": torch.rand(n, 4).cuda(),
        "kpts": torch.rand(n, 11, 3).cuda(),
        "batch_idx": torch.randint(0, 32, (n,)).cuda(),
    }
    for i in range(6):
        opt.zero_grad()
        t0 = time.time()
        loss, items = model(batch)
        loss.backward()
        opt.step()
        torch.cuda.synchronize()
        print(f"iter {i}: {time.time() - t0:.3f}s loss={float(loss):.3f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
