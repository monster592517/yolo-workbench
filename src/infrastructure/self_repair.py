# -*- coding: utf-8 -*-
"""自我修复系统 — 根据自检结果自动修复程序常见损坏 (v0.10.31+)

自检系统 (infrastructure.diagnostics) 输出结构化结果后, 调用 auto_repair(result)
即可自动修复:
  - 运行目录缺失       -> 重建 runs/* 目录
  - 日志不可写         -> 重建 runs/logs 并重建日志句柄
  - config.yaml 损坏/缺失 -> 用内置默认配置重建
  - 路径标记不一致     -> 触发路径卫士强制适配
  - 模型 md5 缺失      -> 自动登记生成
  - CUDA 缓存损坏      -> 清除重建
  - 数据集目录缺失     -> 重建数据目录
  - 数据集格式异常     -> 补全 data.yaml 的 path/train/val/test/nc
  - 标注工具依赖损坏   -> attrs 版本过高缺少 attr.s 时自动降级 attrs==23.2.0

每个修复动作都会写入审计日志 (self_repair), 设置页自检完成后自动执行,
也可点击"自动修复"按钮手动触发。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

DEFAULT_CONFIG = """# YOLO工作台 默认配置 (自我修复重建, v0.10.31)
app:
  name: "YOLO 工作台"
  version: "0.11.7"
paths:
  data_dir: data/datasets
  output_dir: runs/outputs
  record_dir: runs/recordings
  deploy_dir: runs/deploy_packages
  log_dir: runs/logs
training:
  default_model: yolo11n.pt
  epochs: 100
  batch: 16
  imgsz: 640
detection:
  conf: 0.5
  device: auto
logging:
  level: INFO
python:
  min_version: "3.11"
ui:
  theme_mode: auto
"""


def _audit(detail: str, level: str = "info") -> None:
    try:
        from infrastructure.audit import audit
        audit("self_repair", detail, level=level, module="self_repair")
    except Exception:
        pass


def _status(result: dict, key: str) -> str:
    try:
        return str((result or {}).get(key, {}).get("status", "ok"))
    except Exception:
        return "ok"


def repair_runtime_dirs(root: Path) -> int:
    """重建运行目录, 返回新建数量"""
    try:
        from infrastructure.path_center import ensure_runtime_dirs
        return len(ensure_runtime_dirs(root))
    except Exception:
        return 0


def repair_logs(root: Path) -> bool:
    """重建日志目录并验证可写, 返回是否确实发生修复 (目录原本缺失)"""
    log_dir = root / "runs" / "logs"
    try:
        existed = log_dir.exists()
        log_dir.mkdir(parents=True, exist_ok=True)
        probe = log_dir / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return not existed
    except Exception:
        return False


def repair_config(root: Path) -> bool:
    """config.yaml 缺失或损坏时用默认配置重建, 返回是否重建"""
    cfg = root / "config.yaml"
    try:
        if cfg.exists():
            try:
                import yaml
                yaml.safe_load(cfg.read_text(encoding="utf-8"))
                return False
            except Exception:
                pass
        cfg.write_text(DEFAULT_CONFIG, encoding="utf-8")
        return True
    except Exception:
        return False


def repair_paths(root: Path) -> bool:
    """路径标记不一致时触发路径卫士强制适配, 返回是否确实发生变化 (仅真实项目根)"""
    if root.resolve() != _PROJECT_ROOT.resolve():
        return False
    try:
        from infrastructure.path_guard import ensure_paths_adapted
        report = ensure_paths_adapted(start_monitor=False, force=True)
        return bool(report.get("migrated") or report.get("fixed_yamls")
                    or report.get("shortcut_refreshed") or report.get("created_dirs"))
    except Exception:
        return False


def repair_model_md5(root: Path) -> int:
    """为缺失 md5 的模型自动登记生成, 返回补登数量"""
    fixed = 0
    try:
        out = root / "runs" / "outputs"
        for pt in sorted(out.glob("*.pt")):
            md5 = pt.with_suffix(pt.suffix + ".md5")
            if md5.exists():
                continue
            try:
                from scripts.verify_model import generate_md5_file
                generate_md5_file(pt)
            except Exception:
                import hashlib
                h = hashlib.md5()
                with open(pt, "rb") as f:
                    while chunk := f.read(8192):
                        h.update(chunk)
                md5.write_text(h.hexdigest(), encoding="utf-8")
            fixed += 1
    except Exception:
        pass
    return fixed


def repair_cuda_cache(root: Path) -> bool:
    """CUDA 缓存损坏时清除, 返回是否清除"""
    cache = root / "runs" / ".cuda_cache.json"
    try:
        if cache.exists():
            json.loads(cache.read_text(encoding="utf-8"))
            return False
        return False
    except Exception:
        try:
            cache.unlink(missing_ok=True)
            return True
        except Exception:
            return False


def repair_dataset_yamls(root: Path) -> int:
    """修复数据集 data.yaml: 补全 path/train/val/test/nc/names, 返回修复数量。

    仅做非破坏性补全: 保留已有键值, 目录不存在时不会强行写入。
    """
    fixed = 0
    try:
        import yaml
        data_dir = root / "data"
        yamls = list(data_dir.rglob("data.yaml")) + list(data_dir.rglob("dataset.yaml"))
    except Exception:
        return 0
    for yf in yamls:
        try:
            cfg = yaml.safe_load(yf.read_text(encoding="utf-8")) or {}
            changed = False
            base = yf.parent
            # path: 缺失时写入当前绝对路径 (路径卫士迁移时再统一适配)
            if not str(cfg.get("path", "")).strip():
                cfg["path"] = str(base.resolve())
                changed = True
            # train/val/test: 缺失或引用目录不存在时, 用存在的标准目录补全
            for key, fallback in (("train", "train"), ("val", "valid"), ("test", "test")):
                val = cfg.get(key)
                target = base / str(val) if val else None
                if not target or not target.exists():
                    if (base / fallback).exists():
                        cfg[key] = fallback
                        changed = True
                    elif (base / fallback / "images").exists():
                        cfg[key] = f"{fallback}/images"
                        changed = True
            # names/nc: 有 classes.txt 时补全
            if not cfg.get("names"):
                cls_file = base / "classes.txt"
                if cls_file.exists():
                    names = [ln.strip() for ln in cls_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
                    if names:
                        cfg["names"] = names
                        changed = True
            names = cfg.get("names")
            if isinstance(names, list) and names and cfg.get("nc") != len(names):
                cfg["nc"] = len(names)
                changed = True
            if changed:
                yf.write_text(yaml.dump(cfg, default_flow_style=False, allow_unicode=True),
                              encoding="utf-8")
                fixed += 1
        except Exception:
            continue
    return fixed


def annotation_deps_ok() -> bool:
    """检查标注工具依赖是否可用:
    jsonlines 使用旧 API attr.s, attrs>=24 已移除, 会导致 X-AnyLabeling
    无法加载标注文件 (打开工具无标注痕迹)。"""
    try:
        import attr
        if not hasattr(attr, "s"):
            return False
        import importlib
        importlib.import_module("anylabeling.views.labeling.label_file")
        return True
    except Exception:
        return False


def repair_annotation_deps() -> bool:
    """标注工具依赖损坏时自动修复 (pip 降级 attrs==23.2.0), 返回是否修复成功"""
    if annotation_deps_ok():
        return False
    try:
        import subprocess
        import sys
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "attrs==23.2.0"],
            capture_output=True, text=True, timeout=300,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode == 0 and annotation_deps_ok()
    except Exception:
        return False




def auto_repair(result: Optional[dict] = None,
                root: Optional[Path] = None,
                progress_cb: Optional[Callable[[int, int, str], None]] = None) -> dict:
    """根据自检结果自动修复, 返回 {root, actions, fixed}

    progress_cb(step, total, label): 修复进度回调 (共 9 个检查步骤)
    """
    result = result or {}
    root = Path(root) if root else _PROJECT_ROOT
    actions = []
    TOTAL = 9

    def _p(step: int, label: str) -> None:
        if progress_cb:
            try:
                progress_cb(step, TOTAL, label)
            except Exception:
                pass

    # 1) 运行目录 / 关键文件
    _p(1, "运行目录/关键文件")
    if _status(result, "path") != "ok" or _status(result, "files") != "ok":
        n = repair_runtime_dirs(root)
        if n:
            actions.append(f"重建运行目录 {n} 个")

    # 2) 日志可写
    _p(2, "日志系统")
    if _status(result, "logs") != "ok":
        if repair_logs(root):
            actions.append("重建日志目录并验证可写")
            try:
                from infrastructure.logger import set_project_root
                set_project_root(root)
            except Exception:
                pass

    # 3) 配置文件
    _p(3, "配置文件")
    if _status(result, "config") != "ok":
        if repair_config(root):
            actions.append("重建 config.yaml (默认配置)")
            try:
                from infrastructure.config import set_project_root
                set_project_root(root)
            except Exception:
                pass

    # 4) 路径标记
    _p(4, "路径适配")
    if _status(result, "path") != "ok":
        if repair_paths(root):
            actions.append("路径卫士强制适配")

    # 5) 模型 MD5
    _p(5, "模型 MD5")
    if _status(result, "model") != "ok":
        n = repair_model_md5(root)
        if n:
            actions.append(f"补登模型 MD5 {n} 个")

    # 6) CUDA 缓存
    _p(6, "CUDA 缓存")
    if _status(result, "cuda") in ("fail", "warn"):
        if repair_cuda_cache(root):
            actions.append("清除损坏的 CUDA 缓存")

    # 7) 数据集目录 + 数据集格式
    _p(7, "数据集目录/格式")
    if _status(result, "datasets") == "fail":
        try:
            from infrastructure.config import get_config
            existed = get_config().data_dir.exists()
            get_config().data_dir.mkdir(parents=True, exist_ok=True)
            if not existed:
                actions.append("重建数据集目录")
        except Exception:
            pass
    if _status(result, "dataset_format") != "ok":
        n = repair_dataset_yamls(root)
        if n:
            actions.append(f"修复数据集 data.yaml {n} 个 (补全三分结构)")
    # 数据集标注清理 (fall/摔倒): 自检发现残留标注时备份并删除
    if _status(result, "dataset_cleanup") != "ok":
        try:
            from core.trainer import strip_dataset_annotations
            _fall = root / "data" / "datasets" / "fall"
            if _fall.is_dir():
                _r = strip_dataset_annotations(_fall)
                if _r.get("removed"):
                    actions.append(f"清理 fall 残留标注 {_r['removed']} 个 (已备份)")
        except Exception:
            pass


    # 9) 标注工具依赖 (attrs/jsonlines 兼容性)
    _p(9, "标注工具依赖")
    if _status(result, "annotation_deps") != "ok":
        if repair_annotation_deps():
            actions.append("修复标注工具依赖 (attrs 降级到 23.2.0)")

    _p(TOTAL, "完成")
    for a in actions:
        _audit(a)
    return {"root": str(root), "actions": actions, "fixed": len(actions)}
