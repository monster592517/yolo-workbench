# -*- coding: utf-8 -*-
"""
输入验证 & 数据清洗工具
"""

from pathlib import Path
from typing import Any, Optional

from infrastructure.diagnostics import ValidationError


def ensure_file(path: str, desc: str = "文件") -> Path:
    """断言文件存在, 否则抛出 ValidationError"""
    p = Path(path)
    if not p.exists():
        raise ValidationError(f"{desc}不存在: {path}")
    if not p.is_file():
        raise ValidationError(f"{desc}不是有效文件: {path}")
    return p


def ensure_dir(path: str, desc: str = "目录") -> Path:
    p = Path(path)
    if not p.exists():
        raise ValidationError(f"{desc}不存在: {path}")
    if not p.is_dir():
        raise ValidationError(f"{desc}不是有效目录: {path}")
    return p


def ensure_pt_file(path: str) -> Path:
    p = ensure_file(path, "模型文件")
    if p.suffix.lower() not in (".pt", ".pth"):
        raise ValidationError(f"不支持的文件格式: {p.suffix}")
    return p


def parse_int(val: Any, default: int = 0, min_val: Optional[int] = None, max_val: Optional[int] = None) -> int:
    try:
        v = int(val)
    except (ValueError, TypeError):
        v = default
    if min_val is not None and v < min_val:
        v = min_val
    if max_val is not None and v > max_val:
        v = max_val
    return v


def parse_float(val: Any, default: float = 0.0, min_val: Optional[float] = None, max_val: Optional[float] = None) -> float:
    try:
        v = float(val)
    except (ValueError, TypeError):
        v = default
    if min_val is not None and v < min_val:
        v = min_val
    if max_val is not None and v > max_val:
        v = max_val
    return v


def parse_bool(val: Any, default: bool = False) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        return val.strip().lower() in ("true", "1", "yes", "y")
    return default


def sanitize_filename(name: str) -> str:
    import re
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip()


def generate_unique_name(base: str, existing_names: list) -> str:
    """防重名 - 自动添加序号: file -> file(1)"""
    if base not in existing_names:
        return base
    stem = Path(base).stem
    suffix = Path(base).suffix
    counter = 1
    while f"{stem}({counter}){suffix}" in existing_names:
        counter += 1
    return f"{stem}({counter}){suffix}"
