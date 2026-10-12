# -*- coding: utf-8 -*-
"""YOLO 数据集结构检测器 — 自动识别数据集内部目录构成

识别内容 (参考 YOLO 训练目录约定):
  - 图片目录   : images / img / 含图片的目录
  - 标注目录   : labels (默认 YOLO txt; 也识别 labelme json / VOC xml)
  - 训练原始数据: train
  - 对照/测试   : valid / val / test 及 原始/对照 等中文目录
输出结构化 DatasetProfile, 供训练页/标注工具/部署链路使用。
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
_SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache", "runs", "logs"}

# 目录名关键字 -> (kind, 显示名)
_KIND_RULES: List[tuple] = [
    (("train", "training", "训练", "训练集", "训练原始数据"), "train", "训练集"),
    (("valid", "val", "validation", "验证", "验证集"), "valid", "验证集"),
    (("test", "testing", "测试", "测试集", "评估"), "test", "测试集"),
    (("raw", "original", "source", "origin", "原始", "原图", "未标注", "原始数据"), "raw", "原始数据"),
    (("compare", "comparison", "ref", "reference", "对照", "参考", "对照测试"), "compare", "对照测试"),
]


@dataclass
class DatasetSplit:
    key: str                 # train / valid / test / raw / compare / root
    display: str             # 显示名
    kind: str
    images_dir: Path
    labels_dir: Optional[Path] = None
    image_count: int = 0
    label_count: int = 0
    label_format: str = "none"   # yolo | labelme | voc | none | mixed


@dataclass
class DatasetProfile:
    name: str
    path: Path
    splits: List[DatasetSplit] = field(default_factory=list)
    label_format: str = "yolo"
    classes: List[str] = field(default_factory=list)

    def default_split(self) -> Optional[DatasetSplit]:
        """默认标注/查看目录: 优先训练集, 否则整个数据集"""
        for s in self.splits:
            if s.kind == "train":
                return s
        for s in self.splits:
            if s.kind == "root":
                return s
        return self.splits[0] if self.splits else None


def _classify(rel_parts: List[str]) -> tuple:
    """根据相对路径目录名判定 (kind, display)"""
    low = [p.lower() for p in rel_parts]
    for keywords, kind, display in _KIND_RULES:
        if any(any(k in lp for k in keywords) for lp in low):
            return kind, display
    return "root", "整个数据集"


def _find_image_dirs(root: Path, max_depth: int = 4) -> List[tuple]:
    """递归查找含图片文件的目录, 返回 [(dir, 图片数)]"""
    found = []
    for d in root.rglob("*"):
        if not d.is_dir():
            continue
        if any(p in _SKIP_DIRS or p.startswith(".") for p in d.relative_to(root).parts):
            continue
        rel = d.relative_to(root)
        if len(rel.parts) > max_depth:
            continue
        try:
            n = sum(1 for f in d.iterdir()
                    if f.is_file() and f.suffix.lower() in IMG_EXTS)
        except Exception:
            n = 0
        if n > 0:
            found.append((d, n))
    return found


def _find_labels_dir(images_dir: Path) -> Optional[Path]:
    """为图片目录配对标注目录: train/images -> train/labels; images -> 上级 labels; images/labels"""
    cands = [
        images_dir.parent / "labels",        # train/images -> train/labels
        images_dir.parent / "label",
        images_dir / "labels",               # 扁平: images/labels
        images_dir.parent.parent / "labels", # root/images -> root/labels
        images_dir.parent / "annotations",
    ]
    for c in cands:
        try:
            if c.is_dir() and any(f.is_file() for f in c.iterdir()):
                return c
        except Exception:
            continue
    return None


def _detect_format(labels_dir: Optional[Path]) -> str:
    if not labels_dir or not labels_dir.exists():
        return "none"
    counts = Counter()
    try:
        for f in labels_dir.iterdir():
            if f.is_file() and f.name != "classes.txt":
                counts[f.suffix.lower()] += 1
    except Exception:
        return "none"
    if counts[".txt"] > 0 and counts[".txt"] >= counts[".json"]:
        return "yolo"
    if counts[".json"] > 0:
        return "labelme"
    if counts[".xml"] > 0:
        return "voc"
    return "none"



def find_labels_for_image_dir(images_dir: Path) -> Optional[Path]:
    """为任意图片目录配对标注目录 (支持 YOLO 子目录结构)

    候选顺序 (参考 YOLO 目录约定):
      images/scene        -> train/images/scene -> train/labels/scene
      train/images        -> train/labels
      dataset/images      -> dataset/labels
    """
    base = Path(images_dir)
    cands = [
        base / "labels",
        base / "annotations",
        base / "label",
        base.parent / "labels",                    # train/images -> train/labels
        base.parent / "annotations",               # dataset/images -> dataset/annotations
        base.parent / "label",
        base.parent.parent / "labels" / base.name, # train/images/scene -> train/labels/scene
        base.parent.parent / "labels",             # 兜底: 上级 labels
        base.parent.parent / "annotations" / base.name,
        base.parent.parent / "annotations",
    ]
    for c in cands:
        try:
            if c.is_dir() and any(f.is_file() for f in c.iterdir()):
                return c
        except Exception:
            continue
    return None


def list_image_subdirs(images_dir: Path, max_depth: int = 2, max_items: int = 60):
    """列出图片目录下含图片的子目录 (深度受限, 供标注工具子目录选择)

    自动排除 labels/annotations 等标注目录与隐藏/缓存目录。
    返回 [(Path, 图片数)], 按路径排序。
    """
    import os
    res = []
    skip_names = {"labels", "annotations", "label", "__pycache__", ".git",
                  ".pytest_cache", "runs", "logs"}
    try:
        for dirpath, dirnames, filenames in os.walk(str(images_dir)):
            d = Path(dirpath)
            if d == Path(images_dir):
                dirnames[:] = [x for x in dirnames
                               if x.lower() not in skip_names and not x.startswith(".")]
                continue
            rel = d.relative_to(images_dir)
            if len(rel.parts) > max_depth:
                dirnames[:] = []
                continue
            if any(x.lower() in skip_names or x.startswith(".") for x in rel.parts):
                dirnames[:] = []
                continue
            dirnames[:] = [x for x in dirnames
                           if x.lower() not in skip_names and not x.startswith(".")]
            n = sum(1 for f in filenames if f.lower().endswith(tuple(IMG_EXTS)))
            if n > 0:
                res.append((d, n))
                if len(res) >= max_items:
                    break
    except Exception:
        pass
    res.sort(key=lambda x: str(x[0]).lower())
    return res


def profile_dataset(ds_path: Path) -> Optional[DatasetProfile]:
    """分析单个数据集目录结构"""
    if not ds_path.is_dir():
        return None
    profile = DatasetProfile(name=ds_path.name, path=ds_path)

    # 1) 发现图片目录
    img_dirs = _find_image_dirs(ds_path)
    if not img_dirs:
        return profile

    # 2) 配对标注 + 分类
    by_kind: Dict[str, dict] = {}
    for img_dir, n in img_dirs:
        rel = list(img_dir.relative_to(ds_path).parts)
        kind, display = _classify(rel)
        labels_dir = _find_labels_dir(img_dir)
        fmt = _detect_format(labels_dir)
        entry = by_kind.setdefault(kind, {
            "display": display, "images_dirs": [], "labels_dir": None,
            "image_count": 0, "label_count": 0, "formats": [],
        })
        entry["images_dirs"].append(img_dir)
        entry["image_count"] += n
        if labels_dir and entry["labels_dir"] is None:
            entry["labels_dir"] = labels_dir
        if labels_dir:
            try:
                entry["label_count"] += sum(1 for f in labels_dir.iterdir()
                                            if f.is_file() and f.name != "classes.txt")
            except Exception:
                pass
        entry["formats"].append(fmt)

    # 3) 汇总 splits (root 与 train/valid 并存时保留两者; 同类多目录合并显示)
    for kind, entry in by_kind.items():
        images_dir = entry["images_dirs"][0]
        labels_dir = entry["labels_dir"]
        formats = entry["formats"]
        fmt = "mixed" if len(set(formats)) > 1 else (formats[0] if formats else "none")
        label_count = entry["label_count"]
        # 中文名目录(原始/对照)优先显示其目录名
        display = entry["display"]
        if kind in ("raw", "compare"):
            display = f"{display} ({entry['images_dirs'][0].name})"
        elif len(entry["images_dirs"]) > 1:
            display = f"{display} ({len(entry['images_dirs'])}个目录)"
        profile.splits.append(DatasetSplit(
            key=kind, display=display, kind=kind,
            images_dir=images_dir, labels_dir=labels_dir,
            image_count=entry["image_count"], label_count=label_count,
            label_format=fmt,
        ))

    # 4) 排序: train > valid > test > root > raw > compare
    order = {"train": 0, "valid": 1, "test": 2, "root": 3, "raw": 4, "compare": 5}
    profile.splits.sort(key=lambda s: order.get(s.kind, 9))

    # 5) 数据集整体标注格式 (默认 YOLO)
    fmts = {s.label_format for s in profile.splits}
    if "yolo" in fmts:
        profile.label_format = "yolo"
    elif "labelme" in fmts:
        profile.label_format = "labelme"
    elif "voc" in fmts:
        profile.label_format = "voc"
    else:
        profile.label_format = "none"

    # 6) 类别清单
    try:
        from core.yolo_convert import load_classes
        profile.classes = load_classes(ds_path)
    except Exception:
        profile.classes = []
    return profile


def profile_all(data_dir: Path) -> Dict[str, DatasetProfile]:
    """扫描数据目录下全部数据集"""
    result = {}
    if not data_dir.exists():
        return result
    for d in sorted(data_dir.iterdir()):
        if d.is_dir() and not d.name.startswith("."):
            p = profile_dataset(d)
            if p and p.splits:
                result[d.name] = p
    return result


def verify_dataset_images(ds_path: Path, sample: int = 120) -> dict:
    """抽样校验数据集图片完整性 (PIL verify, 支持中文路径)

    注意: 不能用 cv2.imread 直接读 — Windows 下 OpenCV 无法处理非 ASCII 路径,
    会全部误报为损坏。返回 {"total", "checked", "bad"}。
    """
    import random
    try:
        from PIL import Image
    except Exception:
        return {"total": 0, "checked": 0, "bad": 0}
    img_files = [d for d in ds_path.rglob("*")
                 if d.is_file() and d.suffix.lower() in IMG_EXTS]
    total = len(img_files)
    if total == 0:
        return {"total": 0, "checked": 0, "bad": 0}
    random.seed(1)
    checked = random.sample(img_files, min(sample, total))
    bad = 0
    for f in checked:
        try:
            with Image.open(str(f)) as im:
                im.verify()
        except Exception:
            bad += 1
    return {"total": total, "checked": len(checked), "bad": bad}


def check_dataset_format(ds_path: Path) -> dict:
    """校验数据集是否为 YOLO 标准三分结构 (train/valid/test + data.yaml)。

    与 跌倒数据集 同构判定: data.yaml 含 train/val/test 三键且引用目录存在,
    同时有 names 或 classes.txt。返回 {"ok", "detail"}。
    """
    if not ds_path.is_dir():
        return {"ok": False, "detail": "路径不存在"}
    yaml_path = ds_path / "data.yaml"
    if not yaml_path.exists():
        yaml_path = ds_path / "dataset.yaml"
    if not yaml_path.exists():
        return {"ok": False, "detail": "缺少 data.yaml"}
    try:
        import yaml as _ym
        cfg = _ym.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        return {"ok": False, "detail": f"data.yaml 解析失败: {e}"}
    missing = []
    for key, fallback in (("train", "train"), ("val", "valid"), ("test", "test")):
        val = cfg.get(key)
        target = ds_path / str(val) if val else None
        if not target or not target.exists():
            if not (ds_path / fallback).exists():
                missing.append(key + (f"->{val}" if val else ""))
    if not cfg.get("names") and not (ds_path / "classes.txt").exists():
        missing.append("names/classes.txt")
    if missing:
        return {"ok": False, "detail": "结构缺失: " + ",".join(missing)}
    return {"ok": True, "detail": "train/valid/test 三分结构完整"}
