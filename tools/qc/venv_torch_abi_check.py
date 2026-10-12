# -*- coding: utf-8 -*-
"""venv_torch_abi_check.py — 检查 venv 内「编译型扩展的 +ptXXcuXX 标签」是否与 torch 实际版本匹配。

为什么需要（2026-09-04 事故）
--------------------------------
`.venv_3dgs` 里同时装上了：
    gsplat==1.5.3+pt24cu124        (为 torch 2.4 + CUDA 12.4 编译)
    torch_cluster==1.6.3+pt24cu124
    torch_scatter==2.1.2+pt24cu124
    torch==2.13.0+cu130            (完全不同代)
pip **不报任何错**（包名与版本号都「合法」），但三个扩展全部**静默失效**：
    - gsplat: `_C is None`，只留一句误导性的 "No CUDA toolkit found"（真因不是缺 CUDA Toolkit）
    - 真因: csrc.pyd 依赖 cudart64_12.dll + torch 2.4 时代的符号，在 torch 2.13+cu130 下
      WinError 126（找不到模块）→ 补 DLL 后变 WinError 127（找不到符号）⇒ 确系 ABI 不匹配。

判据（约定）
------------
- `+ptXYcuZ` 里 pt 段 = PyTorch 大版本.小版本，cu 段 = CUDA 大版本.小版本。
- **torch 的 major.minor 必须与标签完全一致**（PyTorch 每个 minor 都可能 ABI break）；
  补丁号（2.4.0 vs 2.4.1）无关。
- **CUDA 只比 major**：同一 major 内 minor 兼容（cu121 编的能在 cu124 上跑），
  跨 major（12 → 13）不算。

⚠️ pip 改名残留（`~xxx`）
--------------------------
pip 在 Windows 上卸载/覆盖被占用的文件时，会把旧目录**改名**成 `~` 前缀而不是删除
（本项目实测：`torch-2.13.0+cu130.dist-info` → `~orch-2.13.0+cu130.dist-info`）。
这些残留带着**旧版本的 METADATA**，且 `sorted()` 下 `~` 排在字母之后 ⇒ 会**覆盖真值**，
让本工具把 torch 版本读错。故 `scan_site_packages` 一律跳过 `~` 开头，
并由 `find_pip_residue` 单独报出来（残留本身就该清）。

作者: Fairy  日期: 2026-09-13
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple

# +pt24cu124 / +pt211cu128 这类 local version tag
#   pt 段: 首位 = major，其余 = minor   (pt24 -> 2.4 ; pt211 -> 2.11)
#   cu 段: 末位 = minor，其余 = major   (cu124 -> 12.4 ; cu130 -> 13.0 ; cu118 -> 11.8)
_TAG_RE = re.compile(r"^\+pt(\d)(\d+)cu(\d+)(\d)$")

RESIDUE_PREFIX = "~"


def parse_local_tag(version: str) -> Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
    """把 '1.5.3+pt24cu124' 解析成 ((2, 4), (12, 4))；无 tag 或格式不符返回 None。"""
    if not version or "+" not in version:
        return None
    local = "+" + version.split("+", 1)[1]
    m = _TAG_RE.match(local)
    if not m:
        return None
    pt_major, pt_minor, cu_major, cu_minor = m.groups()
    return (int(pt_major), int(pt_minor)), (int(cu_major), int(cu_minor))


def parse_torch_version(torch_version: str) -> Tuple[Tuple[int, int], Optional[int]]:
    """'2.4.1+cu124' -> ((2, 4), 12)；'2.13.0+cu130' -> ((2, 13), 13)。"""
    base = torch_version.split("+", 1)[0]
    parts = base.split(".")
    mm = (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    cuda_major: Optional[int] = None
    if "+cu" in torch_version:
        local = torch_version.split("+", 1)[1]
        m = re.match(r"^cu(\d+)(\d)$", local)
        if m:
            cuda_major = int(m.group(1))
    return mm, cuda_major


def check_abi(torch_version: str, packages: Dict[str, str]) -> List[dict]:
    """核心判据（纯函数）。

    torch_version: 例如 '2.4.1+cu124'
    packages:      {包名: 版本}，例如 {'gsplat': '1.5.3+pt24cu124', ...}

    返回不匹配清单；每项含 package / version / tag_torch / tag_cuda / reason / expected。
    """
    t_mm, t_cuda = parse_torch_version(torch_version)
    problems: List[dict] = []

    for name, ver in sorted(packages.items()):
        tag = parse_local_tag(ver)
        if tag is None:
            continue                       # 纯 Python 包，不吃 ABI
        tag_torch, tag_cuda = tag
        if name.lower().replace("_", "-") == "torch":
            continue                       # torch 自己不算扩展

        if tag_torch != t_mm:
            problems.append({
                "package": name,
                "version": ver,
                "tag_torch": ".".join(map(str, tag_torch)),
                "actual_torch": ".".join(map(str, t_mm)),
                "reason": f"为 torch {'.'.join(map(str, tag_torch))} 编译，实际 torch 是 {'.'.join(map(str, t_mm))}",
                "expected": f"torch 必须为 {'.'.join(map(str, tag_torch))}.x（或重装该扩展）",
            })
        elif t_cuda is not None and tag_cuda[0] != t_cuda:
            problems.append({
                "package": name,
                "version": ver,
                "tag_cuda": f"{tag_cuda[0]}.{tag_cuda[1]}",
                "actual_cuda": f"{t_cuda}.x",
                "reason": f"为 CUDA {tag_cuda[0]}.x 编译，实际 CUDA 是 {t_cuda}.x（跨大版本）",
                "expected": f"CUDA major 必须一致（{tag_cuda[0]}.x）",
            })

    return problems


# ---------------------------------------------------------------- 采集

def _read_metadata_name_version(meta_path: str) -> Tuple[Optional[str], Optional[str]]:
    name = version = None
    try:
        with open(meta_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("Name: ") and name is None:
                    name = line[6:].strip()
                elif line.startswith("Version: ") and version is None:
                    version = line[9:].strip()
                if name and version:
                    break
    except OSError:
        return None, None
    return name, version


def find_pip_residue(site_packages: str) -> List[str]:
    """列出 pip 改名残留（`~` 开头的顶层条目）。它们带旧版本信息，会让版本判断出错。"""
    if not os.path.isdir(site_packages):
        return []
    return sorted(d for d in os.listdir(site_packages) if d.startswith(RESIDUE_PREFIX))


def scan_site_packages(site_packages: str, *, skip_residue: bool = True) -> Dict[str, str]:
    """读 *.dist-info/METADATA，返回 {Name: Version}。纯文件读，不 import、不跑 pip。

    skip_residue=True（默认）时跳过 `~xxx`，避免旧版本 METADATA 覆盖真值。
    """
    out: Dict[str, str] = {}
    if not os.path.isdir(site_packages):
        return out
    for d in sorted(os.listdir(site_packages)):
        if skip_residue and d.startswith(RESIDUE_PREFIX):
            continue
        if not d.endswith(".dist-info"):
            continue
        name, version = _read_metadata_name_version(os.path.join(site_packages, d, "METADATA"))
        if name and version:
            out[name] = version
    return out


def find_site_packages(venv_root: str) -> Optional[str]:
    """兼容 Windows/Linux 的 venv site-packages 定位。"""
    for rel in ("Lib/site-packages", "lib/site-packages"):
        p = os.path.join(venv_root, rel)
        if os.path.isdir(p):
            return p
    lib = os.path.join(venv_root, "lib")
    if os.path.isdir(lib):
        for d in sorted(os.listdir(lib)):
            p = os.path.join(lib, d, "site-packages")
            if os.path.isdir(p):
                return p
    return None


def check_venv(venv_root: str) -> dict:
    """对一个 venv 做完整检查。

    返回 {venv, site_packages, torch, tagged, problems, pip_residue}。
    `pip_residue` 非空 ⇒ 需清理（它不代表 ABI 错误，但会让版本判断失真）。
    """
    sp = find_site_packages(venv_root)
    if sp is None:
        return {"venv": venv_root, "error": "site-packages not found"}
    pkgs = scan_site_packages(sp)
    residue = find_pip_residue(sp)
    torch_ver = pkgs.get("torch")
    tagged = {k: v for k, v in pkgs.items() if parse_local_tag(v) is not None}
    if torch_ver is None:
        return {"venv": venv_root, "site_packages": sp, "torch": None,
                "error": "torch not installed", "tagged": tagged,
                "problems": [], "pip_residue": residue}
    return {"venv": venv_root, "site_packages": sp, "torch": torch_ver,
            "tagged": tagged, "problems": check_abi(torch_ver, pkgs),
            "pip_residue": residue}


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="检查 venv 内 torch 与 +ptXXcuXX 扩展的 ABI 匹配")
    ap.add_argument("--venv", default=r"<项目根>\01_yolo_lab\.venv_3dgs", help="venv 根目录")
    args = ap.parse_args()

    r = check_venv(args.venv)
    print(json.dumps({k: v for k, v in r.items() if k != "tagged"}, ensure_ascii=False, indent=2))
    print("tagged pkgs:", json.dumps(r.get("tagged", {}), ensure_ascii=False))
    if r.get("pip_residue"):
        print("⚠️ pip 残留（建议清理）:", r["pip_residue"])
    print("RESULT:", "OK" if not r.get("problems") else f"{len(r['problems'])} PROBLEM(S)")
