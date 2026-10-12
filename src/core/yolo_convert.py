# -*- coding: utf-8 -*-
"""YOLO 标注转换器 — YOLO txt -> Labelme JSON (X-AnyLabeling 可直接显示)

支持: HBB(rectangle) / OBB(rotation) / 分割(polygon) / Pose(主体框)
转换产物与图片同目录 (labelme 约定), 标注工具打开后痕迹可见。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Dict, List, Optional

IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
_IMG_SIZE_CACHE: Dict[str, tuple] = {}
_DISK_CACHE: Optional[Path] = None
_DISK_CACHE_LOADED = False

# YOLO pose 默认 17 关键点 (COCO 命名, X-AnyLabeling point shape 兼容)
_COCO_KP_NAMES = ["nose", "left_eye", "right_eye", "left_ear", "right_ear",
                  "left_shoulder", "right_shoulder", "left_elbow", "right_elbow",
                  "left_wrist", "right_wrist", "left_hip", "right_hip",
                  "left_knee", "right_knee", "left_ankle", "right_ankle"]


def _load_disk_cache():
    """加载磁盘图片尺寸缓存 (runs/cache/image_sizes.json), 避免大数据集重复读图头"""
    global _IMG_SIZE_CACHE, _DISK_CACHE_LOADED, _DISK_CACHE
    if _DISK_CACHE_LOADED:
        return
    _DISK_CACHE_LOADED = True
    try:
        from infrastructure.config import get_config
        cache_dir = get_config().log_dir.parent / "cache"
    except Exception:
        cache_dir = Path(__file__).resolve().parent.parent.parent / "runs" / "cache"
    _DISK_CACHE = cache_dir / "image_sizes.json"
    try:
        if _DISK_CACHE.exists():
            data = json.loads(_DISK_CACHE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, list) and len(v) == 2:
                        _IMG_SIZE_CACHE[k] = (int(v[0]), int(v[1]))
    except Exception:
        pass


def _save_disk_cache():
    try:
        if _DISK_CACHE is not None and _IMG_SIZE_CACHE:
            _DISK_CACHE.parent.mkdir(parents=True, exist_ok=True)
            _DISK_CACHE.write_text(
                json.dumps({k: list(v) for k, v in _IMG_SIZE_CACHE.items()},
                           ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def get_image_size(img_path: Path) -> Optional[tuple]:
    """返回 (w, h), 带内存+磁盘缓存; 失败返回 None"""
    _load_disk_cache()
    key = str(img_path)
    if key in _IMG_SIZE_CACHE:
        return _IMG_SIZE_CACHE[key]
    size = None
    try:
        from PIL import Image
        with Image.open(key) as im:
            size = im.size
    except Exception:
        try:
            import cv2
            im = cv2.imread(key)
            if im is not None:
                h, w = im.shape[:2]
                size = (w, h)
        except Exception:
            size = None
    if size:
        _IMG_SIZE_CACHE[key] = size
        _save_disk_cache()
    return size


def load_classes(ds_path: Path) -> List[str]:
    """类别清单: classes.txt > predefined_classes.txt > data.yaml names > 自动生成"""
    for cand in [ds_path / "classes.txt",
                 ds_path / "labels" / "classes.txt",
                 ds_path / "predefined_classes.txt"]:
        try:
            if cand.exists():
                names = [ln.strip() for ln in cand.read_text(encoding="utf-8").splitlines() if ln.strip()]
                if names:
                    return names
        except Exception:
            pass
    for cand in [ds_path / "data.yaml", ds_path / "dataset.yaml"]:
        try:
            if cand.exists():
                import yaml as _y
                with cand.open("r", encoding="utf-8") as f:
                    cfg = _y.safe_load(f) or {}
                names = cfg.get("names")
                if isinstance(names, dict):
                    return [str(n) for n in names.values()]
                if isinstance(names, list):
                    return [str(n) for n in names]
        except Exception:
            pass
    return []


def parse_yolo_line(parts: List[str], img_w: float, img_h: float, label: str) -> Optional[dict]:
    """解析一行 YOLO 标注 -> labelme shape"""
    try:
        nums = [float(x) for x in parts[1:]]
    except Exception:
        return None
    if len(nums) == 4:
        # HBB: cls cx cy w h (归一化)
        cx, cy, w, h = nums
        x1 = (cx - w / 2) * img_w
        y1 = (cy - h / 2) * img_h
        x2 = (cx + w / 2) * img_w
        y2 = (cy + h / 2) * img_h
        return {"label": label, "points": [[x1, y1], [x2, y2]],
                "group_id": None, "shape_type": "rectangle", "flags": {}}
    if len(nums) == 8:
        # OBB: cls x1 y1 x2 y2 x3 y3 x4 y4 (归一化, YOLO11 约定)
        pts = [[nums[i] * img_w, nums[i + 1] * img_h] for i in range(0, 8, 2)]
        return {"label": label, "points": pts,
                "group_id": None, "shape_type": "rotation", "flags": {}}
    if len(nums) >= 6 and len(nums) % 2 == 0:
        # 分割多边形: cls x1 y1 x2 y2 ...
        pts = [[nums[i] * img_w, nums[i + 1] * img_h] for i in range(0, len(nums), 2)]
        return {"label": label, "points": pts,
                "group_id": None, "shape_type": "polygon", "flags": {}}
    if len(nums) > 4:
        # Pose: cls cx cy w h k1x k1y k1v ... -> 主体框 (关键点不在 X-AnyLabeling 原生范围)
        cx, cy, w, h = nums[:4]
        x1 = (cx - w / 2) * img_w
        y1 = (cy - h / 2) * img_h
        x2 = (cx + w / 2) * img_w
        y2 = (cy + h / 2) * img_h
        return {"label": label, "points": [[x1, y1], [x2, y2]],
                "group_id": None, "shape_type": "rectangle", "flags": {}}
    return None


def convert_txt_to_labelme(txt_path: Path, img_path: Path, classes: List[str]) -> Optional[dict]:
    """转换单个 YOLO txt -> labelme JSON dict"""
    size = get_image_size(img_path)
    if size is None:
        return None
    img_w, img_h = size
    shapes = []
    group_counter = 0
    try:
        lines = txt_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return None
    for line in lines:
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            cid = int(float(parts[0]))
        except Exception:
            continue
        label = classes[cid] if cid < len(classes) else f"class_{cid}"
        shape = parse_yolo_line(parts, img_w, img_h, label)
        if shape:
            shape["group_id"] = group_counter
            shapes.append(shape)
            # Pose 行: 附加关键点 (shape_type=point, X-AnyLabeling 原生显示)
            try:
                nums = [float(x) for x in parts[1:]]
                if len(nums) > 4 and (len(nums) - 4) % 3 == 0 and len(nums) >= 7:
                    kp = nums[4:]
                    for k in range(0, len(kp), 3):
                        kv = int(kp[k + 2]) if k + 2 < len(kp) else 0
                        if kv == 0:
                            continue
                        ki = k // 3
                        kname = (_COCO_KP_NAMES[ki] if ki < len(_COCO_KP_NAMES)
                                 else f"keypoint_{ki}")
                        shapes.append({
                            "label": kname, "shape_type": "point", "flags": {},
                            "points": [[kp[k] * img_w, kp[k + 1] * img_h]],
                            "group_id": group_counter, "description": None,
                        })
            except Exception:
                pass
            group_counter += 1
    if not shapes:
        return None
    return {
        "version": "5.2.0.post4",
        "flags": {},
        "shapes": shapes,
        "imagePath": img_path.name,
        "imageData": None,
        "imageHeight": img_h,
        "imageWidth": img_w,
    }


def sync_yolo_to_labelme(images_dir: Path, labels_dir: Optional[Path], classes: List[str],
                         progress_cb: Optional[Callable[[int, int], None]] = None,
                         force: bool = False) -> dict:
    """把 labels_dir 的 YOLO txt 增量转换为 images_dir 同名 json (标注痕迹可见)

    仅转换: json 不存在 或 txt 比 json 新 的图片。返回统计。
    """
    stats = {"total": 0, "converted": 0, "skipped": 0, "errors": 0}
    if not labels_dir or not labels_dir.exists():
        return stats
    txts = [t for t in labels_dir.glob("*.txt") if t.name != "classes.txt"]
    total = len(txts)
    for idx, txt in enumerate(txts):
        stats["total"] = total
        img = images_dir / (txt.stem + ".jpg")
        if not img.exists():
            for ext in (".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"):
                alt = images_dir / (txt.stem + ext)
                if alt.exists():
                    img = alt
                    break
        if not img.exists():
            stats["skipped"] += 1
            continue
        out = images_dir / (txt.stem + ".json")
        if out.exists() and not force:
            try:
                _existing = json.loads(out.read_text(encoding="utf-8"))
                if _existing.get("shapes"):
                    # 已有标注痕迹且标注未更新 -> 跳过
                    if out.stat().st_mtime >= txt.stat().st_mtime:
                        stats["skipped"] += 1
                        continue
                # 空壳 JSON (shapes=0, 旧会话生成) -> 重新生成, 保证标注痕迹可见
            except Exception:
                pass
            if out.stat().st_mtime >= txt.stat().st_mtime:
                stats["skipped"] += 1
                continue
        try:
            data = convert_txt_to_labelme(txt, img, classes)
            if data is not None:
                out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                stats["converted"] += 1
            else:
                stats["skipped"] += 1
        except Exception:
            stats["errors"] += 1
        if progress_cb and (idx + 1) % 50 == 0:
            progress_cb(idx + 1, total)
        if progress_cb:
            progress_cb(total, total)
    return stats


# ═══════════════════════════════════════════════════════════════════════
#  反向：labelme shapes -> YOLO txt（编辑器保存时用）
#
#  正向（上面）是「训练前把 YOLO 标签摊开给人看」；反向是「人改完了写回去」。
#  两条必须**对称**：正向把 pose 行拆成「主体框 + 关键点点集」，反向就得能合回去，
#  否则每存一次盘，关键点就会被降级成一个普通框。
# ═══════════════════════════════════════════════════════════════════════

def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)


def _fmt(v: float) -> str:
    """YOLO 行里的数字统一 6 位小数（与编辑器内部精度一致，避免来回漂移）。"""
    return ("%.6f" % v).rstrip("0").rstrip(".") or "0"


def shape_to_yolo_line(shape: dict, img_w: float, img_h: float,
                       classes: List[str]) -> Optional[str]:
    """单个 labelme shape（**像素坐标**）-> 一行 YOLO txt。

    支持 rectangle / rotation / polygon / circle；`point` 与 `line` 不是 YOLO 原生形态，
    分别交给 pose 合成与忽略（返回 None）。
    """
    if not isinstance(shape, dict) or img_w <= 0 or img_h <= 0:
        return None
    label = str(shape.get("label") or "")
    # 类别号：命中就用，没命中按 0（保持与正向 classes[cid] 对称）
    try:
        cid = classes.index(label)
    except ValueError:
        cid = 0
    st = str(shape.get("shape_type") or "polygon")
    pts = [p for p in (shape.get("points") or []) if isinstance(p, (list, tuple)) and len(p) >= 2]

    if st == "rectangle" and len(pts) >= 2:
        x1, y1 = float(pts[0][0]), float(pts[0][1])
        x2, y2 = float(pts[1][0]), float(pts[1][1])
        cx = _clamp01((x1 + x2) / 2 / img_w)
        cy = _clamp01((y1 + y2) / 2 / img_h)
        bw = _clamp01(abs(x2 - x1) / img_w)
        bh = _clamp01(abs(y2 - y1) / img_h)
        if bw <= 0 or bh <= 0:
            return None
        return "%d %s %s %s %s" % (cid, _fmt(cx), _fmt(cy), _fmt(bw), _fmt(bh))

    if st == "rotation" and len(pts) >= 4:
        nums = []
        for p in pts[:4]:
            nums += [_fmt(_clamp01(float(p[0]) / img_w)), _fmt(_clamp01(float(p[1]) / img_h))]
        return "%d %s" % (cid, " ".join(nums))

    if st in ("polygon", "circle") and len(pts) >= 3:
        nums = []
        for p in pts:
            nums += [_fmt(_clamp01(float(p[0]) / img_w)), _fmt(_clamp01(float(p[1]) / img_h))]
        return "%d %s" % (cid, " ".join(nums))

    return None


def shapes_to_yolo_lines(shapes: List[dict], img_w: float, img_h: float,
                         classes: List[str],
                         kp_names: Optional[List[str]] = None,
                         pose_class: Optional[str] = None) -> List[str]:
    """一组 shapes -> YOLO 行列表（**含 pose 合成**）。

    pose 合成规则（与正向对称）：
      同一 `group_id` 下若**同时**有 rectangle 和落在 `kp_names` 里的 point，
      就合成一行 `cls cx cy w h k1x k1y k1v k2x k2y k2v ...`；
      关键点顺序按 `kp_names` 排（正向也是这样摊开的）。
    `pose_class`: 哪个类别的框算「人框」。默认取 `kpts` 里第一个点的类别，
    再不行取第一个 rectangle 的类别。
    """
    kp_names = list(kp_names or _COCO_KP_NAMES)
    out: List[str] = []
    kp_index = {n: i for i, n in enumerate(kp_names)}

    # ── 按 group_id 分组（None 视为各自独立）──
    groups: Dict[object, List[dict]] = {}
    for i, s in enumerate(shapes):
        if not isinstance(s, dict):
            continue
        gid = s.get("group_id")
        groups.setdefault(("solo", i) if gid is None else ("g", gid), []).append(s)

    for _key, items in groups.items():
        box = next((s for s in items if str(s.get("shape_type")) == "rectangle"), None)
        kps = [s for s in items
               if str(s.get("shape_type")) == "point" and str(s.get("label") or "") in kp_index]
        others = [s for s in items if s is not box and s not in kps]

        if box is not None and kps:
            cls_name = str(pose_class or box.get("label") or "")
            try:
                cid = classes.index(cls_name)
            except ValueError:
                cid = 0
            pts = box.get("points") or []
            if len(pts) < 2:
                continue
            x1, y1 = float(pts[0][0]), float(pts[0][1])
            x2, y2 = float(pts[1][0]), float(pts[1][1])
            row = [str(cid), _fmt(_clamp01((x1 + x2) / 2 / img_w)),
                   _fmt(_clamp01((y1 + y2) / 2 / img_h)),
                   _fmt(_clamp01(abs(x2 - x1) / img_w)), _fmt(_clamp01(abs(y2 - y1) / img_h))]
            slots: List[Optional[str]] = [None] * len(kp_names)
            for k in kps:
                idx = kp_index.get(str(k.get("label") or ""))
                kp = (k.get("points") or [[None, None]])[0]
                if idx is None or len(kp) < 2:
                    continue
                x, y = float(kp[0]), float(kp[1])
                slots[idx] = "%s %s 2" % (_fmt(_clamp01(x / img_w)), _fmt(_clamp01(y / img_h)))
            # YOLO 要求关键点段完整：没标的补 `0 0 0`
            row += [(s if s else "0 0 0") for s in slots]
            out.append(" ".join(row))
        else:
            if box is not None:
                others = [box] + others
            for s in others:
                line = shape_to_yolo_line(s, img_w, img_h, classes)
                if line:
                    out.append(line)
    return out


def write_yolo_labels(txt_path: Path, shapes: List[dict], img_w: float, img_h: float,
                      classes: List[str], kp_names: Optional[List[str]] = None) -> int:
    """把 shapes 写回 `txt_path`（YOLO 格式）。返回写了几行。

    **shapes 为空 -> 写空文件**（而不是删文件）：空文件在 YOLO 里就是「这张图没有目标」，
    与「文件不存在」语义不同 —— 后者会让训练器以为这张图还没标过。
    """
    lines = shapes_to_yolo_lines(shapes, img_w, img_h, classes, kp_names)
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.write_text(("\n".join(lines) + "\n") if lines else "", encoding="utf-8")
    return len(lines)
