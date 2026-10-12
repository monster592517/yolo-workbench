# -*- coding: utf-8 -*-
"""smoke_venv_torch_abi.py — venv_torch_abi_check 的正/负对照自检。

每项都必须「按预期」：正例不许报、负例必须报。任一不符即 FAIL。
含一组「pip 改名残留」对照 —— 这是真事故里让工具读错版本的那个坑。
作者: Fairy  日期: 2026-09-13
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from venv_torch_abi_check import (  # noqa: E402
    check_abi, check_venv, find_pip_residue, parse_local_tag, parse_torch_version,
    scan_site_packages,
)

CASES = []


def case(name, got, want):
    CASES.append((name, got == want, got, want))


# ---- 解析器 ----
case("parse_tag pt24cu124", parse_local_tag("1.5.3+pt24cu124"), ((2, 4), (12, 4)))
case("parse_tag pt211cu128", parse_local_tag("9.9+pt211cu128"), ((2, 11), (12, 8)))
case("parse_tag 无tag -> None", parse_local_tag("1.2.3"), None)
case("parse_torch 2.13.0+cu130", parse_torch_version("2.13.0+cu130"), ((2, 13), 13))
case("parse_torch 2.4.1+cu124", parse_torch_version("2.4.1+cu124"), ((2, 4), 12))

# ---- 判据：正例（应无告警）----
case("正例 匹配组合",
     len(check_abi("2.4.1+cu124", {"gsplat": "1.5.3+pt24cu124",
                                   "torch_cluster": "1.6.3+pt24cu124",
                                   "torch_scatter": "2.1.2+pt24cu124"})), 0)
case("正例 同major不同minor的CUDA(cu121 vs cu124)",
     len(check_abi("2.4.1+cu124", {"foo": "1.0+pt24cu121"})), 0)
case("正例 纯Python包不吃ABI",
     len(check_abi("2.13.0+cu130", {"numpy": "2.2.6", "pillow": "12.3.0"})), 0)

# ---- 判据：负例（必须报）----
case("负例 真事故 torch2.13+cu130 vs pt24cu124",
     len(check_abi("2.13.0+cu130", {"gsplat": "1.5.3+pt24cu124"})), 1)
case("负例 CUDA跨大版本 2.4.1+cu130 vs pt24cu124",
     len(check_abi("2.4.1+cu130", {"gsplat": "1.5.3+pt24cu124"})), 1)
case("负例 torch minor 差一档 2.5.1 vs pt24",
     len(check_abi("2.5.1+cu124", {"gsplat": "1.5.3+pt24cu124"})), 1)
case("负例 多个扩展同时错",
     len(check_abi("2.13.0+cu130", {"a": "1.0+pt24cu124", "b": "2.0+pt25cu121"})), 2)

# ---- pip 改名残留（真事故形态：~orch-<旧版>.dist-info 会覆盖真值）----
tmp = tempfile.mkdtemp(prefix="vtabi_")
try:
    site = os.path.join(tmp, "Lib", "site-packages")
    os.makedirs(site)
    for dirname, name, ver in (
        ("torch-2.4.1+cu124.dist-info", "torch", "2.4.1+cu124"),
        ("torch_cluster-1.6.3+pt24cu124.dist-info", "torch_cluster", "1.6.3+pt24cu124"),
        ("~orch-2.13.0+cu130.dist-info", "torch", "2.13.0+cu130"),   # ← 残留
    ):
        d = os.path.join(site, dirname)
        os.makedirs(d)
        with open(os.path.join(d, "METADATA"), "w", encoding="utf-8") as f:
            f.write(f"Metadata-Version: 2.1\nName: {name}\nVersion: {ver}\n")

    case("残留 默认跳过~ -> torch 读到真值",
         scan_site_packages(site).get("torch"), "2.4.1+cu124")
    case("残留 负对照 不跳过时会被旧值覆盖(证明坑真实存在)",
         scan_site_packages(site, skip_residue=False).get("torch"), "2.13.0+cu130")
    case("残留 find_pip_residue 报出",
         find_pip_residue(site), ["~orch-2.13.0+cu130.dist-info"])
    case("残留 check_venv 的 torch 与 problems 均正确",
         (lambda r: (r["torch"], len(r["problems"])))(check_venv(tmp)),
         ("2.4.1+cu124", 0))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ---- 汇总 ----
passed = sum(1 for _, ok, _, _ in CASES if ok)
total = len(CASES)
for name, ok, got, want in CASES:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if not ok:
        print(f"         got={got!r}  want={want!r}")
print(f"\n{passed}/{total} PASS")
sys.exit(0 if passed == total else 1)
