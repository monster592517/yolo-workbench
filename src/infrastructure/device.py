# -*- coding: utf-8 -*-
"""设备解析 — GPU 加速检测与默认选择

策略: 显式指定设备 > auto/cpu 逻辑 > NVIDIA CUDA > CPU
供 训练 / AI标注 / 检测推理 / 设备下拉框 统一使用。
"""
from __future__ import annotations

from typing import Optional


def cuda_device_names() -> list:
    """返回可用 CUDA 设备显示名列表 (空列表 = 无 NVIDIA GPU 可用)"""
    try:
        import torch
        if torch.cuda.is_available():
            return [f"GPU {i}: {torch.cuda.get_device_name(i)[:48]}"
                    for i in range(torch.cuda.device_count())]
    except Exception:
        pass
    return []


def gpu_available_fast() -> list:
    """nvidia-smi 快速探测 GPU (约 0.5-1s, 不初始化 torch/CUDA 上下文)

    用于设备下拉框快速显示 GPU 选项, 避免首次 torch CUDA 初始化
    (5-10s) 期间用户只看到 cpu。
    """
    import subprocess
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode == 0:
            names = [ln.strip() for ln in r.stdout.decode("utf-8", "ignore").splitlines()
                     if ln.strip()]
            return [f"GPU {i}: {n[:48]}" for i, n in enumerate(names)]
    except Exception:
        pass
    return []


def detect_devices() -> tuple:
    """综合设备检测: 快速(nvidia-smi) + 确认(torch)

    返回 (显示名列表, torch_ok):
      - torch_ok=True  : torch CUDA 已确认可用 (调用方可默认选中 GPU)
      - torch_ok=False : nvidia-smi 检测到 GPU 但 torch 未确认 (初始化中/失败,
                         调用方应安排重试); 或完全无 GPU
    """
    fast = gpu_available_fast()
    try:
        torch_names = cuda_device_names()
        if torch_names:
            return torch_names + ["cpu"], True
    except Exception:
        pass
    if fast:
        return fast + ["cpu"], False
    return ["cpu"], False


def resolve_best_device(preferred: Optional[str] = None) -> str:
    """解析最优设备字符串

    - preferred 为具体设备 (如 "0" / "cpu" / "dml") -> 原样返回 (显式选择优先)
    - preferred 为 "auto" / None / "" -> CUDA 可用返回 "0", 否则 "cpu"
    """
    if preferred and str(preferred).strip().lower() not in ("auto", ""):
        return str(preferred).strip()
    try:
        import torch
        if torch.cuda.is_available():
            return "0"
    except Exception:
        pass
    return "cpu"
