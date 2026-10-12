# -*- coding: utf-8 -*-
"""
训练服务 v2 - 企业级训练管道
特性: 配置驱动, 多重标注, OBB/Keypoint 原生支持, 训练回调
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from infrastructure.config import ModelTask, get_config, get_project_root
from infrastructure.diagnostics import TrainingError, DatasetError, AnnotationError
from utils.text_utils import ensure_dir, sanitize_filename
import yaml as _yaml_mod

_log = logging.getLogger(__name__)
_cfg = get_config()

# ── pythonw/无控制台兜底: 必须在 ultralytics 导入前执行 ──
# 否则 ultralytics 的 LOGGER/TQDM 会捕获 None 流, 训练结束时
# TQDM.close() 里 self.file.write("\n") 崩溃 ('NoneType' write)
# 注意: 这里必须先声明默认值。原实现在兜底块之后又写了一次
# `_CONSOLE_GUARDED = False`, 把刚置上的 True 覆盖掉, 导致
# ensure_console_streams() 每次都重复打开 console.log (文件句柄泄漏)。
_CONSOLE_GUARDED = False
if sys.stdout is None or sys.stderr is None:
    try:
        _cfg.log_dir.mkdir(parents=True, exist_ok=True)
        _console_stream = open(_cfg.log_dir / "console.log", "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = _console_stream
        if sys.stderr is None:
            sys.stderr = _console_stream
        _CONSOLE_GUARDED = True
    except Exception:
        pass

# ── ultralytics 惰性导入 (启动提速, 2026-09-11) ──
# 原本这里 `from ultralytics import YOLO` 会连带 import torch, 本机实测冷导入
# ≈9.9s (torch 8.7s + YOLO 类 1.2s)。而启动画面正好在"加载训练引擎"这一步,
# 于是整个启动被卡住。现改为惰性: 首次真正用到 YOLO 时才导入, 界面先出来,
# 之后由 yolo_warmup 在后台线程预热。
from infrastructure.lazy_import import (           # noqa: E402
    ultralytics_available as _ultra_available,
    yolo_class as _yolo_class,
)

HAS_ULTRA = _ultra_available()


class _LazyYOLO:
    """YOLO 的惰性代理: `model = YOLO(path)` 用法完全不变, 首次实例化才导入。"""

    __slots__ = ()

    def __call__(self, *args, **kwargs):
        cls = _yolo_class()
        if cls is None:
            raise ImportError("ultralytics 不可用 (未安装或导入失败)")
        return cls(*args, **kwargs)

    def __bool__(self):
        return HAS_ULTRA


YOLO = _LazyYOLO()

_TQDM_PATCHED = False


# ═══════════════════ 无控制台启动兜底 (pythonw 修复) ═══════════════════

def ensure_console_streams() -> bool:
    """pythonw/无控制台启动时 sys.stdout/stderr 为 None, 会导致
    ultralytics TQDM.close() 执行 self.file.write() 崩溃
    ('NoneType' object has no attribute 'write')。

    本函数把缺失的控制台流重定向到 runs/logs/console.log (追加模式),
    返回 True 表示发生过兜底。
    """
    global _CONSOLE_GUARDED
    if _CONSOLE_GUARDED:
        return sys.stdout is None or sys.stderr is None
    if sys.stdout is not None and sys.stderr is not None:
        _CONSOLE_GUARDED = True
        return False
    try:
        log_dir = _cfg.log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        stream = open(log_dir / "console.log", "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = stream
        if sys.stderr is None:
            sys.stderr = stream
    except Exception:
        # 最后的兜底: devnull 至少保证 .write() 不崩溃
        try:
            import io
            if sys.stdout is None:
                sys.stdout = io.StringIO()
            if sys.stderr is None:
                sys.stderr = io.StringIO()
        except Exception:
            pass
    _CONSOLE_GUARDED = True
    return True


def _patch_ultralytics_tqdm() -> bool:
    """给 ultralytics 8.4.x 的 TQDM.close() 加 None-file 防护 (幂等)。

    即使控制台流兜底失败/其他调用方显式传 file=None, close() 也不会再因
    self.file.write() 抛 'NoneType' object has no attribute 'write'。
    """
    global _TQDM_PATCHED
    if _TQDM_PATCHED:
        return True
    try:
        from ultralytics.utils import tqdm as _utqdm
        _orig_close = _utqdm.TQDM.close

        def _safe_close(self):
            if getattr(self, "file", None) is None:
                self.file = sys.stdout or sys.stderr or open(os.devnull, "w")
            return _orig_close(self)

        _utqdm.TQDM.close = _safe_close
        _TQDM_PATCHED = True
    except Exception:
        _TQDM_PATCHED = False
    return _TQDM_PATCHED


# ═══════════════════ 数据集管理 ═══════════════════

_IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tiff", ".tif"}


# ═══════════════════ 拖拽生成训练集 ═══════════════════

def read_classes_from_folder(folder: Path) -> list:
    """从文件夹中读取类别: classes.txt > data.yaml names > 空列表"""
    try:
        for cand in [folder / "classes.txt", folder / "labels" / "classes.txt",
                     folder / "images" / "classes.txt"]:
            if cand.exists():
                names = [ln.strip() for ln in cand.read_text(encoding="utf-8").splitlines() if ln.strip()]
                if names:
                    return names
    except Exception:
        pass
    try:
        for cand in [folder / "data.yaml", folder / "dataset.yaml"]:
            if cand.exists():
                with cand.open("r", encoding="utf-8") as f:
                    cfg = _yaml_mod.safe_load(f) or {}
                names = cfg.get("names")
                if isinstance(names, dict):
                    return [str(n) for n in names.values()]
                if isinstance(names, list):
                    return [str(n) for n in names]
    except Exception:
        pass
    return []


def build_dataset_from_images(src_folder: Path, dest_name: str = None,
                              classes: list = None, data_dir: Path = None,
                              log_cb: Optional[Callable] = None,
                              progress_cb: Optional[Callable] = None,
                              strip_annotations: bool = True) -> Path:
    """将含图片的文件夹转换为标准 YOLO 训练集 (格式同 跌倒数据集):
    <dest>/data.yaml + classes.txt +
    train|valid|test 各含 images/ + labels/ (labels 目录保留, 由标注工具/AI预标注生成)
    划分比例: 训练 80% / 验证 10% / 测试 10% (按文件名排序确定性分配)
    strip_annotations=True (默认): 生成副本不携带任何标注文件
    (labelme json / voc xml / yolo txt), 只保留纯图片三分结构。
    返回生成的数据集路径。"""
    import shutil
    src_folder = Path(src_folder)
    if not src_folder.exists():
        raise DatasetError(f"源文件夹不存在: {src_folder}")
    dest_name = (dest_name or src_folder.name).strip()
    dest = (data_dir or _cfg.data_dir) / dest_name
    if dest.exists():
        raise DatasetError(f"数据集已存在: {dest_name}")

    imgs = sorted([p for p in src_folder.rglob("*")
                   if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}])
    if not imgs:
        raise DatasetError(f"文件夹中没有图片: {src_folder}")

    if classes is None or not classes:
        classes = read_classes_from_folder(src_folder) or ["person", "fall"]

    dest.mkdir(parents=True, exist_ok=True)
    splits = {"train": dest / "train", "valid": dest / "valid", "test": dest / "test"}
    for sp in splits.values():
        (sp / "images").mkdir(parents=True, exist_ok=True)
        (sp / "labels").mkdir(parents=True, exist_ok=True)

    def _log(msg, tag="info"):
        if log_cb:
            log_cb(msg, tag)

    _log(f"生成训练集: {dest_name} (图片 {len(imgs)} 张, 类别 {classes}, 训练/验证/测试=80/10/10"
         + (", 不携带标注" if strip_annotations else ", 保留标注"))
    used_names = set()
    copied_json = copied_txt = 0
    stripped_json = stripped_xml = stripped_txt = 0
    total = len(imgs)
    for idx, img in enumerate(imgs):
        # 确定性划分: 每 10 张中 1 张测试、1 张验证、其余训练
        if idx % 10 == 0:
            split = splits["test"]
        elif idx % 10 == 1:
            split = splits["valid"]
        else:
            split = splits["train"]
        img_dir = split / "images"
        label_dir = split / "labels"
        # 防重名
        name = img.name
        stem, suffix = img.stem, img.suffix
        while name.lower() in used_names:
            stem += "_1"
            name = stem + suffix
        used_names.add(name.lower())
        try:
            shutil.copy2(str(img), str(img_dir / name))
        except Exception as e:
            if log_cb:
                log_cb(f"  复制失败 {img.name}: {e}", "warn")
            continue
        # 标注文件: 默认全部剔除; 仅 strip_annotations=False 时才随图片复制
        json_src = img.with_suffix(".json")
        xml_src = img.with_suffix(".xml")
        txt_src = img.with_suffix(".txt")
        if not txt_src.exists():
            cand = src_folder / "labels" / (img.stem + ".txt")
            if cand.exists():
                txt_src = cand
        if strip_annotations:
            if json_src.exists():
                stripped_json += 1
            if xml_src.exists():
                stripped_xml += 1
            if txt_src.exists():
                stripped_txt += 1
        else:
            if json_src.exists():
                try:
                    shutil.copy2(str(json_src), str(img_dir / (Path(name).stem + ".json")))
                    copied_json += 1
                except Exception:
                    pass
            if txt_src.exists():
                try:
                    shutil.copy2(str(txt_src), str(label_dir / (Path(name).stem + ".txt")))
                    copied_txt += 1
                except Exception:
                    pass
        if progress_cb:
            progress_cb(int((idx + 1) / total * 100), f"复制图片 {idx + 1}/{total}")

    # classes.txt + images/classes.txt + data.yaml (格式对齐 跌倒数据集: train/valid/test)
    _write_dataset_meta(dest, classes)
    try:
        from infrastructure.audit import audit
        audit("dataset_build", f"生成训练集 {dest_name}: {len(imgs)} 张, "
                               f"类别 {classes}, 三分结构 train/valid/test -> {dest}"
                               + (f" (剔除标注 json {stripped_json}/xml {stripped_xml}/txt {stripped_txt})"
                                  if strip_annotations else ""),
              module="trainer")
    except Exception:
        pass

    _log(f"完成: {len(imgs)} 张 -> train {sum(1 for _ in (splits['train']/'images').glob('*') if _.is_file())} "
         f"/ valid {sum(1 for _ in (splits['valid']/'images').glob('*') if _.is_file())} "
         f"/ test {sum(1 for _ in (splits['test']/'images').glob('*') if _.is_file())} "
         + (f" | 已剔除标注 json {stripped_json} / xml {stripped_xml} / txt {stripped_txt}"
            if strip_annotations else f" | labels {copied_txt} 个 / labelme {copied_json} 个")
         + f" -> {dest}")
    return dest


def _write_dataset_meta(dest: Path, classes: list) -> None:
    """写 classes.txt / images/classes.txt / data.yaml。
    path 优先使用相对主体文件夹的路径, 整个项目迁移后无需修改仍可训练。"""
    (dest / "classes.txt").write_text("\n".join(classes) + "\n", encoding="utf-8")
    (dest / "images").mkdir(parents=True, exist_ok=True)
    (dest / "images" / "classes.txt").write_text("\n".join(classes) + "\n", encoding="utf-8")
    try:
        _path_val = dest.resolve().relative_to(get_project_root().resolve()).as_posix()
    except Exception:
        _path_val = str(dest.resolve())
    yaml_cfg = {
        "path": _path_val,
        "train": "train",
        "val": "valid",
        "test": "test",
        "nc": len(classes),
        "names": classes,
    }
    (dest / "data.yaml").write_text(
        _yaml_mod.dump(yaml_cfg, default_flow_style=False, allow_unicode=True), encoding="utf-8")


def find_annotation_files(ds_path: Path) -> List[Path]:
    """递归查找数据集内的标注文件 (labelme json / voc xml / yolo txt)。

    排除 classes.txt / predefined_classes.txt 等类名文件;
    txt 仅当位于 labels|annotations 目录或与某张图片同主名时视为标注。
    """
    ds = Path(ds_path).resolve()
    if not ds.is_dir():
        return []
    image_stems = set()
    try:
        for p in ds.rglob("*"):
            if p.is_file() and p.suffix.lower() in _IMG_EXTS:
                image_stems.add(p.stem.lower())
    except Exception:
        pass
    found: List[Path] = []
    try:
        for p in sorted(ds.rglob("*")):
            if not p.is_file():
                continue
            if p.name in {"classes.txt", "predefined_classes.txt"}:
                continue
            low = p.suffix.lower()
            parts = [x.lower() for x in p.relative_to(ds).parts]
            if low in {".json", ".xml"}:
                found.append(p)
            elif low == ".txt":
                in_label_dir = any(x in {"labels", "annotations"} for x in parts[:-1])
                if in_label_dir or p.stem.lower() in image_stems:
                    found.append(p)
    except Exception:
        pass
    return found


def strip_dataset_annotations(ds_path: Path, backup_root: Path = None,
                              log_cb: Optional[Callable] = None) -> dict:
    """备份并删除数据集内全部标注文件 (labelme json / voc xml / yolo txt)。

    备份目录: runs/_dataset_backup/<数据集名>_annotations_<时间戳>/ (保留相对路径)
    返回 {"removed": n, "files": [...], "backup": "..."}。
    """
    import shutil
    from datetime import datetime
    ds = Path(ds_path).resolve()
    if not ds.is_dir():
        raise DatasetError(f"数据集路径不存在: {ds}")
    if backup_root is None:
        backup_root = _cfg.output_dir.parent / "_dataset_backup"
    backup_root = Path(backup_root).resolve()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = backup_root / f"{ds.name}_annotations_{stamp}"
    files = find_annotation_files(ds)
    removed = []
    for f in files:
        try:
            if ds not in f.resolve().parents:
                continue  # 防御: 只处理数据集目录内的文件
            rel = f.relative_to(ds)
            dst = backup_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(f), str(dst))
            f.unlink()
            removed.append(str(rel))
        except Exception as e:
            if log_cb:
                log_cb(f"  清理标注失败 {f}: {e}", "warn")
    detail = (f"清理标注文件 {len(removed)} 个 (备份: {backup_dir})" if removed
              else f"未发现标注文件 ({ds.name})")
    if log_cb:
        log_cb(detail, "ok" if removed else "info")
    try:
        from infrastructure.audit import audit
        audit("dataset_strip_annotations", detail, module="trainer")
    except Exception:
        pass
    return {"removed": len(removed), "files": removed, "backup": str(backup_dir)}


def is_dataset_folder(folder: Path) -> bool:
    """判断文件夹是否已经是数据集根目录 (data.yaml 或 train/valid/test/images 等结构)。"""
    folder = Path(folder)
    if not folder.is_dir():
        return False
    try:
        if (folder / "data.yaml").exists() or (folder / "dataset.yaml").exists():
            return True
        names = {d.name.lower() for d in folder.iterdir() if d.is_dir()}
        return bool(names & {"train", "valid", "val", "test", "images", "labels", "annotations"})
    except Exception:
        return False


def normalize_dataset_in_place(folder: Path, classes: list = None,
                               log_cb: Optional[Callable] = None,
                               progress_cb: Optional[Callable] = None) -> Path:
    """把图片文件夹原位整理为 跌倒数据集 格式:
    1) 备份并删除全部标注文件;
    2) 若已有 train/valid/test 则保留, 否则按 80/10/10 将图片移入三分结构;
    3) 生成 classes.txt / images/classes.txt / data.yaml (相对路径)。
    返回规范化后的数据集路径。"""
    import shutil
    folder = Path(folder).resolve()
    if not folder.is_dir():
        raise DatasetError(f"文件夹不存在: {folder}")
    _log = (lambda m, tag="info": log_cb(m, tag)) if log_cb else lambda *a: None
    # 1) 先清理标注 (带备份)
    strip_dataset_annotations(folder, log_cb=log_cb)
    # 2) 三分结构
    split_names = ("train", "valid", "test")
    existing = {s: (folder / s) for s in split_names}
    has_all = all((d / "images").is_dir() for d in existing.values())
    if not has_all:
        protected = {d for d in existing.values() if d.is_dir()}
        imgs = sorted([p for p in folder.rglob("*")
                       if p.is_file() and p.suffix.lower() in _IMG_EXTS
                       and not any(p.is_relative_to(d) for d in protected)])
        total = len(imgs)
        for idx, img in enumerate(imgs):
            if idx % 10 == 0:
                sp = "test"
            elif idx % 10 == 1:
                sp = "valid"
            else:
                sp = "train"
            d = folder / sp
            (d / "images").mkdir(parents=True, exist_ok=True)
            (d / "labels").mkdir(parents=True, exist_ok=True)
            target = d / "images" / img.name
            n = 1
            while target.exists():
                target = d / "images" / f"{img.stem}_{n}{img.suffix}"
                n += 1
            try:
                shutil.move(str(img), str(target))
            except Exception as e:
                _log(f"  移动失败 {img.name}: {e}", "warn")
            if progress_cb:
                progress_cb(int((idx + 1) / max(total, 1) * 100), f"整理图片 {idx + 1}/{total}")
    else:
        for d in existing.values():
            (d / "images").mkdir(parents=True, exist_ok=True)
            (d / "labels").mkdir(parents=True, exist_ok=True)
    # 3) 元数据
    classes = classes or read_classes_from_folder(folder) or ["person", "fall"]
    _write_dataset_meta(folder, classes)
    try:
        from infrastructure.audit import audit
        audit("dataset_normalize", f"原位规范化 {folder.name}: 三分结构 train/valid/test + data.yaml -> {folder}",
              module="trainer")
    except Exception:
        pass
    _log(f"规范化完成: {folder} (classes={classes})", "ok")
    return folder


def import_dataset_copy(src_folder: Path, dest_name: str = None, classes: list = None,
                        data_dir: Path = None, log_cb: Optional[Callable] = None,
                        progress_cb: Optional[Callable] = None) -> Path:
    """把已是数据集的目录复制为规范副本:
    保留原有 train/valid/test 划分 (无划分的图片按 80/10/10 补分),
    一律剔除全部标注文件, 补齐 data.yaml/classes.txt。
    返回副本路径 (源文件夹不会被修改)。"""
    import shutil
    src = Path(src_folder).resolve()
    if not src.is_dir():
        raise DatasetError(f"源文件夹不存在: {src}")
    dest_name = (dest_name or src.name).strip()
    dest = (data_dir or _cfg.data_dir) / dest_name
    if dest.exists():
        raise DatasetError(f"数据集已存在: {dest_name}")
    imgs = sorted([p for p in src.rglob("*")
                   if p.is_file() and p.suffix.lower() in _IMG_EXTS])
    if not imgs:
        raise DatasetError(f"文件夹中没有图片: {src}")
    classes = classes or read_classes_from_folder(src) or ["person", "fall"]
    dest.mkdir(parents=True, exist_ok=True)
    splits = {"train": dest / "train", "valid": dest / "valid", "test": dest / "test"}
    for sp in splits.values():
        (sp / "images").mkdir(parents=True, exist_ok=True)
        (sp / "labels").mkdir(parents=True, exist_ok=True)

    def _pick_split(p: Path):
        rel = p.relative_to(src).as_posix().lower()
        for key, names in (("valid", ("valid", "val")), ("test", ("test", "tests"))):
            if any(rel.startswith(n + "/") or f"/{n}/" in "/" + rel for n in names):
                return key
        if any(rel.startswith(n + "/") or f"/{n}/" in "/" + rel for n in ("train", "training")):
            return "train"
        return None

    used = set()
    idx = 0
    total_copy = len(imgs)
    for copy_idx, img in enumerate(imgs):
        sp = _pick_split(img)
        if sp is None:
            sp = "test" if idx % 10 == 0 else ("valid" if idx % 10 == 1 else "train")
            idx += 1
        name = img.name
        stem, suffix = img.stem, img.suffix
        while name.lower() in used:
            stem += "_1"
            name = stem + suffix
        used.add(name.lower())
        try:
            shutil.copy2(str(img), str(splits[sp] / "images" / name))
        except Exception as e:
            if log_cb:
                log_cb(f"  复制失败 {img.name}: {e}", "warn")
        if progress_cb:
            progress_cb(int((copy_idx + 1) / total_copy * 100), f"复制图片 {copy_idx + 1}/{total_copy}")
    _write_dataset_meta(dest, classes)
    try:
        from infrastructure.audit import audit
        audit("dataset_import_copy", f"数据集副本 {dest_name} (剔除标注, 保留划分) -> {dest}", module="trainer")
    except Exception:
        pass
    if log_cb:
        log_cb(f"副本完成: {dest_name} (图片 {len(imgs)} 张, 无标注, 三分结构)", "ok")
    return dest


def _find_image_dir(dataset_path: Path) -> Path:
    """智能查找—返回图片最多的目录:
       train/images/ → valid/images/ → images/ → <同名>/images/"""
    candidates = [
        dataset_path / "train" / "images",
        dataset_path / "valid" / "images",
        dataset_path / "images",
        dataset_path / dataset_path.name / "train" / "images",
        dataset_path / dataset_path.name / "valid" / "images",
        dataset_path / dataset_path.name / "images",
    ]
    best_dir = None
    best_count = 0
    for cand in candidates:
        if cand.exists():
            try:
                cnt = sum(1 for p in cand.iterdir() if p.suffix.lower() in _IMG_EXTS)
                if cnt > best_count:
                    best_count = cnt
                    best_dir = cand
            except Exception:
                pass
    if best_dir:
        return best_dir
    # 降级
    (dataset_path / "images").mkdir(parents=True, exist_ok=True)
    return dataset_path / "images"


@dataclass
class DatasetInfo:
    path: Path
    name: str
    yaml_path: Optional[Path] = None
    image_count: int = 0
    label_count: int = 0
    classes: List[str] = field(default_factory=list)


def scan_datasets(data_dir: Optional[Path] = None) -> Dict[str, DatasetInfo]:
    """递归扫描数据集目录, 返回有效数据集列表 (支持嵌套子数据集)

    检测规则 (参考 YOLO 训练目录约定):
      - 目录含 data.yaml / dataset.yaml
      - 目录直接含图片文件
      - 目录含 images / labels / train / annotations 等子目录
    名称使用相对 data_dir 的路径 (如 "fall", "fall_dataset/fall_dataset"),
    保证下拉框选择与标注工具打开目录完全一致。
    """
    data_dir = data_dir or _cfg.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    result: Dict[str, DatasetInfo] = {}
    if not data_dir.exists():
        return result

    _SKIP = {"__pycache__", ".git", ".pytest_cache", "runs", "logs",
             ".idea", ".vscode", "node_modules", "__MACOSX"}
    _IMG_EXT_LOW = {e.lower() for e in _IMG_EXTS}
    _STRUCT_DIRS = {"images", "img", "labels", "annotations", "train",
                    "valid", "val", "test"}

    def _is_dataset_root(d: Path) -> bool:
        """判定目录是否为一个独立数据集根目录"""
        try:
            if (d / "data.yaml").exists() or (d / "dataset.yaml").exists():
                return True
            direct_imgs = 0
            for child in d.iterdir():
                if child.is_dir():
                    if child.name.lower() in _STRUCT_DIRS:
                        return True
                elif child.suffix.lower() in _IMG_EXT_LOW:
                    direct_imgs += 1
                    if direct_imgs >= 5:
                        return True
            return direct_imgs > 0
        except Exception:
            return False

    def _build_info(d: Path) -> DatasetInfo:
        yaml = d / "data.yaml"
        if not yaml.exists():
            yaml = d / "dataset.yaml"
        info = DatasetInfo(path=d, name=str(d.relative_to(data_dir)).replace("\\", "/"),
                           yaml_path=yaml if yaml.exists() else None)
        try:
            info.image_count = sum(1 for p in d.rglob("*")
                                   if p.is_file() and p.suffix.lower() in _IMG_EXTS)
        except Exception:
            info.image_count = 0
        try:
            info.label_count = sum(1 for p in d.rglob("*")
                                   if p.is_file() and p.suffix.lower() in {".txt", ".json", ".xml"}
                                   and p.name != "classes.txt")
        except Exception:
            info.label_count = 0
        if yaml.exists():
            try:
                with yaml.open("r", encoding="utf-8") as f:
                    cfg = _yaml_mod.safe_load(f)
                names = (cfg or {}).get("names", [])
                if isinstance(names, dict):
                    info.classes = list(names.values())
                elif isinstance(names, list):
                    info.classes = [str(n) for n in names]
            except Exception:
                pass
        return info

    def _walk(d: Path, max_depth: int = 4):
        """深度优先扫描; 发现数据集根后记录并停止向下 (避免重复列出)"""
        try:
            children = [c for c in sorted(d.iterdir())
                        if c.is_dir() and c.name not in _SKIP and not c.name.startswith(".")]
        except Exception:
            return
        for child in children:
            if len(child.relative_to(data_dir).parts) > max_depth:
                continue
            if _is_dataset_root(child):
                info = _build_info(child)
                if info.image_count > 0 or info.yaml_path:
                    result[info.name] = info
                continue  # 不再深入, 子目录属于该数据集
            _walk(child, max_depth)

    _walk(data_dir)
    return result


def prepare_training_yaml(dataset: DatasetInfo) -> str:
    """为训练准备 data.yaml, 自动处理中文路径和路径规范化"""
    yaml_path = dataset.yaml_path or dataset.path / "data.yaml"
    if not yaml_path.exists():
        # 自动生成
        cfg = {
            "path": str(dataset.path.resolve()),
            "train": "images",
            "val": "images",
            "names": dataset.classes or ["fall", "person"],
        }
    else:
        with yaml_path.open("r", encoding="utf-8") as f:
            cfg = _yaml_mod.safe_load(f) or {}
        cfg["path"] = str(dataset.path.resolve())
        for key in ["train", "val", "test"]:
            if cfg.get(key) and not (dataset.path / cfg[key]).exists():
                img_dir = _find_image_dir(dataset.path)
                cfg[key] = str(img_dir.relative_to(dataset.path))
        # 兼容旧格式: ultralytics 要求 train+val 键必须存在
        img_dir = _find_image_dir(dataset.path)
        rel = str(img_dir.relative_to(dataset.path))
        if not cfg.get("train"):
            cfg["train"] = rel
        if not cfg.get("val"):
            cfg["val"] = cfg.get("train", rel)
        for key in ("train", "val"):
            cand = dataset.path / str(cfg[key])
            if not cand.exists():
                cfg[key] = rel

    temp = _cfg.output_dir / "data_temp.yaml"
    temp.write_text(_yaml_mod.dump(cfg, default_flow_style=False, allow_unicode=True), encoding="utf-8")
    return str(temp)


def auto_annotate(model_path: str, dataset: DatasetInfo, conf: float = 0.5, device: str = "cpu",
                  use_keypoints: bool = True, log_cb: Optional[Callable] = None,
                  progress_cb: Optional[Callable] = None) -> dict:
    """AI预标注 - 边界框 + 关键点"""
    if not HAS_ULTRA:
        raise TrainingError("ultralytics 未安装")

    model = YOLO(model_path)
    img_dir = _find_image_dir(dataset.path)
    if not img_dir.exists():
        raise DatasetError(f"图片目录不存在: {img_dir}")

    imgs = sorted([p for p in img_dir.iterdir()
                    if p.suffix.lower() in {'.jpg', '.jpeg', '.png', '.bmp', '.webp', '.tiff', '.tif'}])
    result = {"total": len(imgs), "annotated": 0, "boxes": 0, "keypoints": 0}

    for idx, img_path in enumerate(imgs):
        try:
            results = model.predict(str(img_path), conf=conf, verbose=False, device=device)
            rez = results[0]

            label_dir = dataset.path / "labels"
            label_dir.mkdir(parents=True, exist_ok=True)
            label_path = label_dir / f"{img_path.stem}.txt"

            lines = []
            if rez.boxes is not None:
                for i in range(len(rez.boxes)):
                    cls_id = int(rez.boxes.cls[i].item())
                    xywh = rez.boxes.xywhn[i]
                    lines.append(f"{cls_id} {xywh[0]:.6f} {xywh[1]:.6f} {xywh[2]:.6f} {xywh[3]:.6f}")
                    result["boxes"] += 1

            # 关键点标注
            if use_keypoints and hasattr(rez, 'keypoints') and rez.keypoints is not None:
                try:
                    kp_data = rez.keypoints.data
                    if kp_data is not None:
                        for kp_set in kp_data:
                            kp_list = kp_set.flatten().tolist()
                            result["keypoints"] += len(kp_list) // 3
                            # 写入关键点到独立文件: filename_kp.txt
                            kp_path = label_dir / f"{img_path.stem}_kp.txt"
                            kp_path.write_text(" ".join(f"{v:.6f}" for v in kp_list), encoding="utf-8")
                except Exception:
                    pass

            if lines:
                label_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                result["annotated"] += 1

            if progress_cb and (idx + 1) % 10 == 0:
                progress_cb(int((idx + 1) / len(imgs) * 100), f"标注: {idx + 1}/{len(imgs)}")

        except Exception as e:
            if log_cb:
                log_cb(f"  ⚠ {img_path.name}: {e}", "warn")

    return result


# ═══════════════════ 模型训练 ═══════════════════

def build_model_list() -> List[str]:
    standard = [
        "yolo11n.pt", "yolo11s.pt", "yolo11m.pt", "yolo11l.pt", "yolo11x.pt",
        "yolov10n.pt", "yolov10s.pt", "yolov10m.pt", "yolov10l.pt", "yolov10x.pt",
        "yolov8n.pt", "yolov8s.pt", "yolov8m.pt", "yolov8l.pt", "yolov8x.pt",
        "yolov8n-obb.pt", "yolov8s-obb.pt", "yolov8m-obb.pt",
        "yolo11n-pose.pt", "yolo11s-pose.pt", "yolo11m-pose.pt",
        "yolo11n-seg.pt", "yolo11s-seg.pt", "yolo11m-seg.pt",
    ]
    custom = []
    for d in [_cfg.output_dir, Path(__file__).resolve().parent.parent / "models"]:
        if d.exists():
            for pt in sorted(d.glob("*.pt")):
                if pt.name not in standard and pt.name not in custom:
                    custom.append(pt.name)
    return standard + custom


def list_local_models() -> List[str]:
    models = []
    search = [_cfg.output_dir, Path(__file__).resolve().parent.parent / "models",
              Path(__file__).resolve().parent.parent.parent]
    for d in search:
        if not d.exists():
            continue
        for p in sorted(d.rglob("*.pt")):
            if p.name == "data_temp.yaml":
                continue
            size_mb = p.stat().st_size / (1024 ** 2)
            models.append(f"{p.name} ({size_mb:.1f}MB) [{p}]")
    return models or ["无本地模型"]


def run_training(model_name: str, data_yaml: str, params: Dict[str, Any],
                 device: str, log_cb: Optional[Callable] = None,
                 progress_cb: Optional[Callable] = None,
                 cancel_event: Optional[threading.Event] = None) -> bool:
    """执行训练 - 返回是否成功"""
    if not HAS_ULTRA:
        raise TrainingError("ultralytics 未安装")

    # ── pythonw/无控制台兜底: 修复 ultralytics TQDM close 崩溃 ──
    # 根因: 桌面快捷方式用 pythonw 启动, sys.stdout=None -> TQDM.file=None
    #       -> close() 时 self.file.write("\n") 抛 NoneType write 错误
    try:
        _console_fallback = ensure_console_streams()
        _patch_ultralytics_tqdm()
        if _console_fallback:
            from infrastructure.audit import audit
            audit("training_console_fallback",
                  "无控制台启动(pythonw): 输出已重定向到 runs/logs/console.log 并给 TQDM 打补丁",
                  level="warn", module="trainer")
            if log_cb:
                log_cb("检测到无控制台启动: 训练输出已自动写入 runs/logs/console.log", "warn")
    except Exception:
        pass

    task = ModelTask.infer(model_name)
    try:
        from infrastructure.device import resolve_best_device
        device = resolve_best_device(device)
    except Exception:
        pass
    if progress_cb:
        progress_cb(2, "正在加载模型...")
    model = YOLO(_resolve_model_path(model_name))
    if progress_cb:
        progress_cb(4, "模型加载完成, 准备数据集...")

    kwargs = {
        "data": data_yaml,
        "epochs": params.get("epochs", 100),
        "batch": params.get("batch", 16),
        "imgsz": params.get("imgsz", 640),
        "device": device,
        "lr0": params.get("lr0", 0.01),
        "lrf": params.get("lrf", 0.01),
        "momentum": params.get("momentum", 0.937),
        "weight_decay": params.get("weight_decay", 0.0005),
        "warmup_epochs": params.get("warmup_epochs", 3),
        "optimizer": params.get("optimizer", "auto"),
        "patience": params.get("patience", 50),
        "augment": params.get("augment", False),
        "project": str(_cfg.output_dir),
        "name": f"train_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "verbose": True,
        "exist_ok": True,
        "workers": 0,
    }

    if task != ModelTask.DETECT:
        kwargs["task"] = task.value

    for aug_key in ["hsv_h", "hsv_s", "hsv_v", "degrees", "translate", "scale",
                    "shear", "perspective", "flipud", "fliplr", "mosaic", "mixup"]:
        if aug_key in params:
            kwargs[aug_key] = float(params[aug_key])

    class ProgressCallback:
        def __init__(s):
            s.total = kwargs["epochs"]

        def __call__(s, trainer):
            if cancel_event is not None and cancel_event.is_set():
                try:
                    trainer.stop_training = True
                except Exception:
                    pass
                if log_cb:
                    log_cb("收到停止请求, 完成当前轮次后停止...", "warn")
                return
            ep = getattr(trainer, 'epoch', 0)
            if s.total > 0:
                pct = min(95, 10 + int(85 * (ep + 1) / s.total))
                if progress_cb:
                    progress_cb(pct, f"Epoch {ep + 1}/{s.total}")
            if log_cb:
                met = getattr(trainer, 'metrics', {})
                parts = [f"Epoch {ep + 1}"]
                for k, v in met.items():
                    if isinstance(v, (int, float)):
                        parts.append(f"{k}={v:.4f}")
                log_cb(" | ".join(parts), "metric")

    class BatchProgressCallback:
        """批次级进度回调 — 训练过程实时可见 (避免看起来像卡死)

        Ultralytics 8.4.x 的 on_train_batch_end 回调中训练器未暴露当前批次号,
        因此按回调触发次数自计数, 并在轮次切换时重置。
        """

        def __init__(s):
            s.total = kwargs["epochs"]
            s.cur_epoch = -1
            s.batch_in_epoch = 0
            s.slow_warned = False

        def __call__(s, trainer):
            if cancel_event is not None and cancel_event.is_set():
                return
            ep = getattr(trainer, "epoch", 0)
            if ep != s.cur_epoch:
                s.cur_epoch = ep
                s.batch_in_epoch = 0
            s.batch_in_epoch += 1
            try:
                nb = max(1, len(trainer.train_loader))
            except Exception:
                nb = 1
            bi = min(s.batch_in_epoch, nb)
            if s.total > 0 and progress_cb:
                frac = (ep + bi / nb) / s.total
                pct = min(95, 5 + int(90 * frac))
                progress_cb(pct, f"Epoch {ep + 1}/{s.total} 批次 {bi}/{nb}")
            if not s.slow_warned and log_cb and s.batch_in_epoch >= 3:
                s.slow_warned = True
                if "cpu" in str(kwargs.get("device", "")).lower():
                    log_cb("提示: CPU 训练速度较慢, 请耐心等待; 或改用 GPU / 减小批次与轮数", "warn")

    try:
        model.add_callback("on_train_epoch_end", ProgressCallback())
        model.add_callback("on_train_batch_end", BatchProgressCallback())
        model.train(**kwargs)

        # 导出模型
        if progress_cb:
            progress_cb(95, "导出模型...")
        train_dir = Path(kwargs["project"]) / kwargs["name"] / "weights"
        best_pt = train_dir / "best.pt"
        if best_pt.exists():
            dest = _cfg.output_dir / f"{Path(model_name).stem}_best_{datetime.now().strftime('%m%d_%H%M')}.pt"
            shutil.copy2(str(best_pt), str(dest))
            # 自动生成 .md5 校验文件 (模型入库即登记, 启动校验不弹窗)
            try:
                from scripts.verify_model import generate_md5_file
                generate_md5_file(dest)
            except Exception:
                try:
                    import hashlib
                    md5_path = dest.with_suffix(dest.suffix + ".md5")
                    h = hashlib.md5()
                    with open(dest, "rb") as f:
                        while chunk := f.read(8192):
                            h.update(chunk)
                    md5_path.write_text(h.hexdigest(), encoding="utf-8")
                except Exception:
                    pass
            if log_cb:
                log_cb(f"模型已导出: {dest.name}", "ok")
        return True
    except Exception as e:
        if log_cb:
            log_cb(f"训练失败: {e}", "err")
            if "NoneType" in str(e) and "write" in str(e):
                log_cb("根因已定位: 无控制台启动(pythonw)时 ultralytics 进度条写入失败; "
                       "本版本已自动兜底, 请直接重试训练", "warn")
            try:
                import traceback
                log_cb("完整堆栈:\n" + traceback.format_exc(), "err")
            except Exception:
                pass
        try:
            from infrastructure.audit import audit
            audit("training_error", f"{e}", level="error", module="trainer")
        except Exception:
            pass
        raise TrainingError(str(e), cause=e)


# 占位 JPG 数据


def _resolve_model_path(name: str) -> str:
    """解析模型路径: 绝对路径 -> 本地 models/ -> 根目录 -> runs/outputs -> 原样(ultralytics下载)"""
    if Path(name).exists():
        return str(Path(name).resolve())
    base = Path(__file__).resolve().parent.parent.parent
    for cand in [base / "models" / name, base / name, base / "runs" / "outputs" / name]:
        if cand.exists():
            return str(cand.resolve())
    return name
