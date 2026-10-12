# -*- coding: utf-8 -*-
"""AutoAnnotator — Labelme JSON generation via trained PT models, with pose support."""
from __future__ import annotations
import json, logging, threading, time, cv2, numpy as np
from pathlib import Path
from typing import Callable, Dict, Optional

_YOLO = None


def _get_yolo():
    """ultralytics 惰性导入: 冷导入 (~5s) 推迟到首次实际标注, 不再拖慢 UI 页面构建."""
    global _YOLO
    if _YOLO is None:
        try:
            from ultralytics import YOLO as _Y
        except ImportError:
            _Y = None
        _YOLO = _Y
    return _YOLO

_log = logging.getLogger(__name__)
IMG_EXTS = {".jpg",".jpeg",".png",".bmp",".webp"}


class AutoAnnotator:
    """Auto-annotate a dataset using a trained PT model. Generates Labelme JSON."""

    def __init__(self, model_path: str, conf_threshold: float = 0.5,
                 use_pose: bool = False, device: str = "cpu"):
        try:
            from infrastructure.device import resolve_best_device
            device = resolve_best_device(device)
        except Exception:
            pass
        self._conf = conf_threshold
        self._device = device
        self._stop_flag = threading.Event()
        YOLO = _get_yolo()
        if YOLO is None:
            raise RuntimeError("ultralytics not installed")
        self._model = YOLO(str(model_path))
        self._names = dict(getattr(self._model, "names", None) or {})
        self._pose_model = YOLO("yolov8n-pose.pt") if use_pose else None
        _log.info("AutoAnnotator init: model=%s conf=%.2f pose=%s device=%s",
                  Path(model_path).name, conf_threshold, use_pose, device)

    def stop(self):
        self._stop_flag.set()

    def annotate_dataset(self, image_dir: str, output_dir: str = None,
                         overwrite: bool = False,
                         progress_callback: Callable = None) -> Dict:
        """
        Annotate all images in a directory. Generates Labelme JSON per image.
        Returns {"total": N, "success": N, "failed": N, "skipped": N}
        """
        img_dir = Path(image_dir)
        output_dir = Path(output_dir) if output_dir else img_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        images = sorted([p for p in img_dir.iterdir() if p.suffix.lower() in IMG_EXTS])
        result = {"total": len(images), "success": 0, "failed": 0, "skipped": 0}

        for idx, img_path in enumerate(images):
            if self._stop_flag.is_set():
                _log.info("AutoAnnotator stopped by user at %d/%d", idx, len(images))
                break

            json_path = output_dir / f"{img_path.stem}.json"
            if json_path.exists() and not overwrite:
                json_path = output_dir / f"{img_path.stem}_auto.json"
                if json_path.exists():
                    result["skipped"] += 1
                    continue

            try:
                # Read image dimensions
                img = cv2.imread(str(img_path))
                if img is None:
                    result["failed"] += 1; continue
                h, w = img.shape[:2]

                # Run models
                det_result = self._model(img_path, conf=self._conf, verbose=False, device=self._device)[0]
                if self._pose_model is not None:
                    pose_result = self._pose_model(img_path, conf=self._conf, verbose=False, device=self._device)[0]

                # Write Labelme JSON (spec-compliant)
                # Filter tiny boxes, clamp coordinates, use pixel ints
                clean_shapes = []
                if getattr(det_result, "obb", None) is not None:
                    for i in range(len(det_result.obb)):
                        cls_id = int(det_result.obb.cls[i].item())
                        pts = det_result.obb.xyxyxyxy[i].tolist()
                        pts = [[max(0.0, min(w, float(px))), max(0.0, min(h, float(py)))] for px, py in pts]
                        if len(pts) != 4:
                            continue
                        clean_shapes.append({
                            "label": str(self._names.get(cls_id, cls_id)),
                            "points": pts,
                            "group_id": None,
                            "shape_type": "rotation",
                            "flags": {}
                        })
                elif det_result.boxes is not None:
                    for i in range(len(det_result.boxes)):
                        cls_id = int(det_result.boxes.cls[i].item())
                        xyxy = det_result.boxes.xyxy[i].tolist()
                        x1 = max(0, int(xyxy[0])); y1 = max(0, int(xyxy[1]))
                        x2 = min(w, int(xyxy[2])); y2 = min(h, int(xyxy[3]))
                        if x2 - x1 < 2 or y2 - y1 < 2:
                            continue  # skip tiny boxes
                        clean_shapes.append({
                            "label": str(self._names.get(cls_id, cls_id)),
                            "points": [[x1, y1], [x2, y2]],
                            "group_id": None,
                            "shape_type": "rectangle",
                            "flags": {}
                        })
                if self._pose_model is not None and 'pose_result' in dir():
                    if pose_result.keypoints is not None and pose_result.keypoints.data is not None:
                        for kp_set in pose_result.keypoints.data:
                            for kidx, (px, py, pv) in enumerate(kp_set.tolist()):
                                if float(pv) < 0.5: continue
                                x, y = int(float(px)), int(float(py))
                                if 0 <= x < w and 0 <= y < h:
                                    clean_shapes.append({
                                        "label": f"keypoint_{kidx}",
                                        "points": [[x, y]],
                                        "group_id": None,
                                        "shape_type": "point",
                                        "flags": {}
                                    })
                labelme = {
                    "version": "4.5.6",
                    "flags": {},
                    "shapes": clean_shapes,
                    "imagePath": img_path.name,  # filename only (Labelme spec)
                    "imageData": None,
                    "imageHeight": h,
                    "imageWidth": w,
                }
                json_path.write_text(json.dumps(labelme, ensure_ascii=False, indent=2), encoding="utf-8")
                # Also write YOLO format labels so LabelImg can read them
                label_dir = output_dir.parent / "labels"
                label_dir.mkdir(parents=True, exist_ok=True)
                yolo_lines = []
                if getattr(det_result, "obb", None) is not None:
                    # YOLO OBB format: cls x1 y1 x2 y2 x3 y3 x4 y4 (normalized)
                    for i in range(len(det_result.obb)):
                        cid = int(det_result.obb.cls[i].item())
                        pts = det_result.obb.xyxyxyxy[i].tolist()
                        norm = []
                        for px, py in pts:
                            norm.append(max(0.0, min(1.0, float(px) / w)))
                            norm.append(max(0.0, min(1.0, float(py) / h)))
                        yolo_lines.append(f"{cid} " + " ".join(f"{v:.6f}" for v in norm))
                elif det_result.boxes is not None:
                    for i in range(len(det_result.boxes)):
                        cid = int(det_result.boxes.cls[i].item())
                        xywh = det_result.boxes.xywhn[i].tolist()
                        yolo_lines.append(f"{cid} {xywh[0]:.6f} {xywh[1]:.6f} {xywh[2]:.6f} {xywh[3]:.6f}")
                if yolo_lines:
                    (label_dir / f"{img_path.stem}.txt").write_text("\n".join(yolo_lines) + "\n", encoding="utf-8")
                result["success"] += 1

            except Exception as e:
                _log.error("Failed %s: %s", img_path.name, e)
                result["failed"] += 1

            if progress_callback:
                progress_callback(idx + 1, len(images), img_path.name)

        return result



# ── Convenience: call auto_annotate from GUI ──

def run_auto_annotate(model_path: str, dataset_dir: str, conf: float = 0.5,
                      use_pose: bool = False, device: str = "cpu",
                      progress_cb=None, cancel_flag=None, overwrite=False):
    """Launch annotation in background thread, returns (thread, result_dict_ref)."""
    result_ref = {"total": 0, "success": 0, "failed": 0, "done": False}

    def _worker():
        try:
            aa = AutoAnnotator(model_path, conf, use_pose, device)
            r = aa.annotate_dataset(dataset_dir, overwrite=overwrite, progress_callback=progress_cb)
            result_ref.update(r)
            result_ref["done"] = True
        except Exception as e:
            _log.error("AutoAnnotator crashed: %s", e)
            result_ref["failed"] = -1
            result_ref["done"] = True

    t = threading.Thread(target=_worker, daemon=True, name="auto-annotate")
    t.start()
    return t, result_ref
