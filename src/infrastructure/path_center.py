# -*- coding: utf-8 -*-
"""路径中心 — 全程序路径的唯一真相源 (v0.10.15+)

背景:
    历史实现里每个模块各自用 Path(__file__) 在 import 时缓存项目根目录,
    主体文件夹一旦迁移, 已加载模块里缓存的旧根目录不会自动更新, 日志/审计/
    配置/自检等仍会指向旧位置, 造成"目录搬走了程序还在找老路径"的问题。

    本模块统一持有"主体文件夹"路径, 并允许在任何时刻:
      1. set_root(新路径)  —— 立即切换主体文件夹
      2. resync_all()      —— 把新根目录一次性推送到全部已加载模块
                               (logger / audit / config / diagnostics /
                                health_check / path_guard / theme_manager /
                                main_window), 重建日志句柄与配置缓存,
                                并同步 sys.path 中的 src/scripts 入口
      3. verify_consistency() —— 自检所有模块根目录是否与路径中心一致

用法:
    from infrastructure import path_center
    path_center.set_root(r"D:\\new\\yolo_lab")
    report = path_center.resync_all()
    path_center.get_root()
    path_center.get_dir("runs", "logs")
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import List, Optional, Union

# 初始根目录: 本文件位于 <根>/src/infrastructure/ 下
_DEFAULT_ROOT = Path(__file__).resolve().parent.parent.parent
_root = _DEFAULT_ROOT
_prev_root = _DEFAULT_ROOT
_lock = threading.RLock()

# 必须推送的基础层模块 (未导入也会尝试导入, 保证 CLI 场景同样生效)
_CORE_MODULES = (
    "infrastructure.logger",
    "infrastructure.audit",
    "infrastructure.config",
    "infrastructure.diagnostics",
    "infrastructure.health_check",
    "infrastructure.path_guard",
)

# 表现层模块: 仅在已加载时推送 (避免命令行场景拉起 tkinter)
_UI_MODULES = (
    "gui.theme_manager",
    "gui.main_window",
)

# 运行期目录 (相对主体文件夹)
RUNTIME_DIRS = (
    "runs",
    "runs/logs",
    "runs/outputs",
    "runs/recordings",
    "runs/person_db",
    "runs/preview",
    "data",
    "data/datasets",
)


# ═══════════════════ 根目录管理 ═══════════════════

def get_root() -> Path:
    """当前主体文件夹 (唯一真相源)"""
    return _root


def get_prev_root() -> Path:
    """上一次的主体文件夹 (迁移前后对比用)"""
    return _prev_root


def is_default_root() -> bool:
    """当前根目录是否仍为程序初始位置 (未发生迁移)"""
    return _root == _DEFAULT_ROOT


def set_root(root: Union[str, Path]) -> Path:
    """切换主体文件夹并同步 sys.path (幂等; 不推送模块, 需配合 resync_all)"""
    global _root, _prev_root
    with _lock:
        new_root = Path(root).resolve()
        old_root = _root
        if new_root == old_root:
            return new_root
        _prev_root = old_root
        _root = new_root
        _sync_sys_path(old_root, new_root)
    return _root


def _sync_sys_path(old_root: Path, new_root: Path) -> int:
    """把 sys.path 中指向旧主体文件夹 src/scripts 的项替换为新位置"""
    n = 0
    old_s = str(old_root).lower()
    for i, entry in enumerate(list(sys.path)):
        try:
            p = Path(entry)
            if not p.is_absolute():
                continue
            abs_s = str(p.resolve()).lower()
            if abs_s.startswith(old_s):
                rel = p.resolve().relative_to(old_root)
                sys.path[i] = str(new_root / rel)
                n += 1
        except Exception:
            continue
    return n


# ═══════════════════ 常用路径快捷方式 ═══════════════════

def get_dir(*parts: str) -> Path:
    """相对主体文件夹取路径: get_dir("runs", "logs")"""
    return _root.joinpath(*parts)


def config_path() -> Path:
    return _root / "config.yaml"


def log_dir() -> Path:
    return _root / "runs" / "logs"


def app_log_path() -> Path:
    return log_dir() / "app.log"


def audit_log_path() -> Path:
    return log_dir() / "operations.log"


def marker_path() -> Path:
    return _root / "runs" / ".root_marker.json"


def cuda_cache_path() -> Path:
    return _root / "runs" / ".cuda_cache.json"


def last_page_path() -> Path:
    return _root / "runs" / "last_page.json"




def ensure_runtime_dirs(root: Optional[Union[str, Path]] = None) -> List[Path]:
    """补建运行期目录, 返回本次新建的目录列表"""
    base = Path(root).resolve() if root is not None else _root
    created = []
    for rel in RUNTIME_DIRS:
        d = base / rel
        try:
            if not d.exists():
                d.mkdir(parents=True, exist_ok=True)
                created.append(d)
        except Exception:
            pass
    return created


# ═══════════════════ 全程序路径联动 ═══════════════════

def resync_all(new_root: Optional[Union[str, Path]] = None) -> dict:
    """把主体文件夹路径推送到所有模块并重建运行目录/日志/配置。

    返回报告: {root, prev_root, pushed, sys_path_updated, dirs_created}
    """
    if new_root is not None:
        set_root(new_root)
    root = get_root()
    report = {
        "root": str(root),
        "prev_root": str(_prev_root),
        "pushed": [],
        "sys_path_updated": 0,
        "dirs_created": [str(d) for d in ensure_runtime_dirs(root)],
    }

    modules = list(_CORE_MODULES) + list(_UI_MODULES)
    for name in modules:
        try:
            mod = sys.modules.get(name)
            if mod is None:
                if name.startswith("gui."):
                    continue  # 表现层未加载时不强制拉起
                mod = __import__(name, fromlist=["*"])
            setter = getattr(mod, "set_project_root", None)
            if callable(setter):
                setter(root)
                report["pushed"].append(name)
        except Exception:
            continue

    report["sys_path_updated"] = _sync_sys_path(_prev_root, root)
    return report


# ═══════════════════ 一致性自检 (设置页/自检系统使用) ═══════════════════

def verify_consistency() -> List[dict]:
    """检查已加载模块的根目录是否与路径中心一致。

    返回不一致项列表: [{module, expected, actual}]; 空列表 = 全部一致。
    """
    expected = str(get_root().resolve()).lower()
    issues = []
    for name in _CORE_MODULES + _UI_MODULES:
        mod = sys.modules.get(name)
        if mod is None:
            continue
        getter = getattr(mod, "get_project_root", None)
        if not callable(getter):
            continue
        try:
            actual = str(Path(getter()).resolve()).lower()
        except Exception:
            actual = "?"
        if actual != expected:
            issues.append({"module": name, "expected": expected, "actual": actual})
    return issues


def path_report() -> dict:
    """汇总报告: 当前根目录/默认根目录/是否迁移/一致性 (供日志与 UI 展示)"""
    issues = verify_consistency()
    return {
        "root": str(get_root()),
        "default_root": str(_DEFAULT_ROOT),
        "prev_root": str(_prev_root),
        "migrated": not is_default_root(),
        "consistent": not issues,
        "issues": issues,
    }
