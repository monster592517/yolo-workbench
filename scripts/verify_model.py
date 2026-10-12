#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模型完整性校验 — MD5 哈希比对

用法:
    python scripts/verify_model.py [model_path]       # 校验指定模型
    python scripts/verify_model.py --generate <path>  # 生成 .md5 文件

在程序启动时调用 verify_model()，若校验失败则弹出提示并退出。
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from tkinter import messagebox


def compute_md5(file_path: str | Path) -> str:
    """计算文件的 MD5 哈希值"""
    file_path = Path(file_path)
    if not file_path.exists():
        raise FileNotFoundError(f"模型文件不存在: {file_path}")

    md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            md5.update(chunk)
    return md5.hexdigest()


def generate_md5_file(model_path: str | Path) -> Path:
    """生成 .md5 校验文件，返回其路径"""
    model_path = Path(model_path)
    hash_value = compute_md5(model_path)
    md5_path = model_path.with_suffix(model_path.suffix + ".md5")
    md5_path.write_text(hash_value, encoding="utf-8")
    print(f"√ MD5 已生成: {md5_path}")
    print(f"  {hash_value}")
    return md5_path


def verify_model(model_path: str | Path = None, *, show_errors: bool = True) -> bool:
    """
    校验模型文件 MD5。

    Args:
        model_path: 模型文件路径。为 None 时自动查找 runs/outputs/ 下最新 .pt
        show_errors: True 时弹出 tkinter 错误提示

    Returns:
        True — 通过; False — 未找到/不匹配 (并可能弹窗)
    """
    project_root = Path(__file__).resolve().parent.parent

    # 自动查找模型
    if model_path is None:
        candidates = []
        for search_dir in [
            project_root / "runs" / "outputs",
            project_root,
        ]:
            if search_dir.exists():
                candidates.extend(sorted(search_dir.glob("*.pt"), key=lambda p: p.stat().st_mtime, reverse=True))
        if not candidates:
            candidates.extend(sorted(project_root.rglob("*.pt"), key=lambda p: p.stat().st_mtime, reverse=True))
        if candidates:
            model_path = candidates[0]
        else:
            if show_errors:
                messagebox.showerror("模型校验", "未找到任何 .pt 模型文件")
            return False

    model_path = Path(model_path)
    md5_path = model_path.with_suffix(model_path.suffix + ".md5")

    # 检查 MD5 文件是否存在
    if not md5_path.exists():
        # 首次登记: 自动生成 .md5 (模型由本程序训练/导入产生, 入库即登记),
        # 不再弹窗阻塞启动; 仅 md5 不匹配 (被篡改/损坏) 才视为校验失败
        try:
            generate_md5_file(model_path)
            print(f"√ 模型首次登记 (自动生成 MD5): {model_path.name}")
            try:
                from infrastructure.audit import audit
                audit("model_md5_register", f"首次登记 {model_path.name}", module="verify_model")
            except Exception:
                pass
        except Exception as e:
            if show_errors:
                messagebox.showerror("模型校验", f"生成校验文件失败:\n{e}")
            return False
        return True

    # 计算并比对
    try:
        current_hash = compute_md5(model_path)
        stored_hash = md5_path.read_text(encoding="utf-8").strip()
    except Exception as e:
        if show_errors:
            messagebox.showerror("模型校验", f"计算哈希失败:\n{e}")
        return False

    if current_hash == stored_hash:
        print(f"√ 模型校验通过: {model_path.name}")
        return True

    if show_errors:
        messagebox.showerror(
            "模型校验失败",
            f"模型文件已被修改或损坏!\n\n"
            f"文件: {model_path.name}\n"
            f"期望: {stored_hash[:16]}...\n"
            f"实际: {current_hash[:16]}...\n\n"
            f"请重新获取正确的模型文件。"
        )
    print(f"✗ 模型校验失败: {model_path.name}")
    return False


# ═══════════════════ CLI ═══════════════════

if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--generate":
        target = sys.argv[2] if len(sys.argv) > 2 else None
        if target is None:
            # Find all .pt files recursively
            for pt in sorted(Path.cwd().glob("*.pt")):
                generate_md5_file(pt)
            for pt in sorted((Path.cwd() / "runs" / "outputs").glob("*.pt")):
                generate_md5_file(pt)
        else:
            generate_md5_file(target)
    elif len(sys.argv) >= 2:
        ok = verify_model(sys.argv[1], show_errors=False)
        print(f"结果: {'通过' if ok else '失败'}")
        sys.exit(0 if ok else 1)
    else:
        # Auto-find model
        ok = verify_model(show_errors=False)
        print(f"结果: {'通过' if ok else '失败'}")
        sys.exit(0 if ok else 1)
