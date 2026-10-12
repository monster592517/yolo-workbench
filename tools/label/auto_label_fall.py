# -*- coding: utf-8 -*-
"""auto_label_fall.py — 摔倒帧自动 YOLO 标注 (bbox + 14 关键点 + 像素级多边形).

标注策略 (v3, 2026-09-08):
- 人物框: yolo11n-pose 人框优先 (conf>=0.25, 1920 主推定 → 1280 降档重试; pose 只有人,
  不会被背景/窗户/杂物抢框) → 分割掩码框 (person 类) → yolo11n 检测框 (类过滤=person, 兜底);
  最终扩展到 [像素级掩码 ∪ 画面内关键点] 的并集+边距 — 贴边人体 (头盔顶/腿脚) 全部罩进框
- 关键点 14 点 (额头/脖子 + 身体 12 点): 只标模型真实检出的点;
  * 低置信 (conf<0.15) → vis=0 不标
  * 越界 (坐标不在画面内) → vis=0 不标  (画面外=导出必错, 宁可少标)
  * 不做镜像脑补、不做解剖外推、不做"✱脑补" (上一版乱标点的主因)
- 多边形: yolo11s-seg 人形掩码 (与人物框一致才采用), 可选 SAM2 框提示精修
- 黑帧/废帧 (均值亮度 <25) → 不标注, person_vis=0 (调用方应标为不可用)
- 输出: labels/<帧名>.txt (bbox) + <帧名>_seg.txt (多边形) + <帧名>_kpt.txt
  (_kpt.txt = yolo-pose 完整行: cls cx cy w h + 14×[x y v]) — 免二次合并可直接喂 pose 训练
"""
from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
MODEL_PATH = ROOT / "yolo11s-seg.pt"
MODEL_FALLBACK = ROOT / "yolo11n.pt"
LABELS_DIR = ROOT / "runs" / "outputs" / "fall_400" / "labels"
_MIN_BOX = 24          # 最小框边长 (px)
_MIN_CONF = 0.25
_BLACK_MEAN = 25.0     # 均值亮度低于此 = 黑帧/废帧 (正常场景帧 ≥80)
_POSE_IMGSZ = int(os.environ.get("FALL_POSE_IMGSZ", "1920"))  # 高清主推定: 贴边人物置信度显著更高
_POSE_IMGSZ_B = int(os.environ.get("FALL_POSE_IMGSZ_B", "1280"))  # 主推定无框时的降档重试
# ComfyUI 占着 GPU 生成: 默认 CPU (1920 也仅 ~0.5s/帧); 可 FALL_LABEL_DEVICE=cuda
_POSE_DEVICE = os.environ.get("FALL_LABEL_DEVICE", "cpu")
_LAST_DBG: dict = {}   # 最近一次 label_frame 的中间量 (审计/自检读, 不参与任何判定)

_model = None
_model_fb = None
_model_pose = None
_sam2 = None

# 头部补圆开关（2026-09-30）：实测它**净负**（背景 改善134/变差298，头盔只 0.454→0.433）
# ⇒ 默认**关**。用 `YOLO_HEAD_DISC=1` 可临时打开做 A/B。根治靠重训分割模型。
_HEAD_DISC_ON = os.environ.get("YOLO_HEAD_DISC", "") == "1"

_KPT_NAMES = ["neck", "forehead", "LShoulder", "RShoulder", "LElbow", "RElbow",
              "LWrist", "RWrist", "LHip", "RHip", "LKnee", "RKnee", "LAnkle", "RAnkle"]
# COCO 17 点索引: 0鼻 1左眼 2右眼 3左耳 4右耳 5-16 肩肘腕髋膝踝 (左右交替)
_BODY = [("LShoulder", 5), ("RShoulder", 6), ("LElbow", 7), ("RElbow", 8),
         ("LWrist", 9), ("RWrist", 10), ("LHip", 11), ("RHip", 12),
         ("LKnee", 13), ("RKnee", 14), ("LAnkle", 15), ("RAnkle", 16)]


def _get_pose():
    global _model_pose
    if _model_pose is None:
        from ultralytics import YOLO
        _model_pose = YOLO(str(ROOT / "yolo11n-pose.pt"))
    return _model_pose


def _get_model():
    global _model
    if _model is None:
        from ultralytics import YOLO
        _model = YOLO(str(MODEL_PATH))
    return _model


def _get_fb():
    global _model_fb
    if _model_fb is None:
        from ultralytics import YOLO
        _model_fb = YOLO(str(MODEL_FALLBACK))
    return _model_fb


def _pose_predict(img_path: Path, imgsz: int | None = None):
    """yolo11n-pose 单次推理 (人框 + 17 点), 失败返回 None.

    FALL_LABEL_DEVICE=dml → onnx + DirectML 走 AMD 核显 (约 29ms, 不占 NVIDIA/CPU);
    其余 device 值走 ultralytics (cuda/cpu)。DML 不可用时自动回退 ultralytics。
    """
    if _POSE_DEVICE == "dml":
        r = _pose_predict_dml(img_path)
        if r is not None:
            return r
    return _pose_predict_ultralytics(img_path, imgsz)


def _pose_predict_ultralytics(img_path: Path, imgsz: int | None = None):
    try:
        return _get_pose().predict(str(img_path), conf=_MIN_CONF, verbose=False,
                                   device="cpu" if _POSE_DEVICE == "dml" else _POSE_DEVICE,
                                   imgsz=imgsz or _POSE_IMGSZ)[0]
    except Exception:
        return None


# ── DirectML (AMD 核显) 推理: yolo11n-pose-1280.onnx / yolo11n.onnx ──
_dml_pose = None
_dml_det = None


def _as_np(x):
    return x.cpu().numpy() if hasattr(x, "cpu") else np.asarray(x)


def _dml_session(model_path: Path):
    try:
        import onnxruntime as ort
        if "DmlExecutionProvider" not in ort.get_available_providers():
            return None
        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        so.intra_op_num_threads = 1  # 主体在核显, 释放 CPU 线程
        return ort.InferenceSession(str(model_path), sess_options=so,
                                    providers=["DmlExecutionProvider", "CPUExecutionProvider"])
    except Exception:
        return None


def _dml_letterbox(img, size=1280):
    import cv2
    import numpy as _np
    ih, iw = img.shape[:2]
    s = min(size / iw, size / ih)
    nw, nh = int(round(iw * s)), int(round(ih * s))
    r = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = _np.full((size, size, 3), 114, _np.uint8)
    y0, x0 = (size - nh) // 2, (size - nw) // 2
    canvas[y0:y0 + nh, x0:x0 + nw] = r
    blob = cv2.dnn.blobFromImage(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB),
                                 1 / 255.0, (size, size), swapRB=False)
    return blob, s, x0, y0


class _DMLBox:
    __slots__ = ("conf", "xyxy", "cls")

    def __init__(self, conf, xyxy, cls=0.0):
        self.conf = float(conf)
        self.xyxy = np.asarray([xyxy], dtype=np.float32)
        self.cls = np.asarray([cls], dtype=np.float32)


class _DMLResult:
    """模拟 ultralytics Results 的最小接口 (boxes/keypoints)."""

    def __init__(self, boxes, kps=None):
        self.boxes = boxes
        if kps is not None:
            class _K:
                pass
            kk = _K()
            kk.data = [np.asarray(kps, dtype=np.float32)]
            self.keypoints = kk
        else:
            self.keypoints = None


def _pose_predict_dml(img_path: Path, size=1280):
    """onnx(DML) pose: 返回 _DMLResult (boxes+17点行), 与原图坐标一致."""
    global _dml_pose
    if _dml_pose is None:
        _dml_pose = _dml_session(ROOT / "yolo11n-pose-1280.onnx")
        if _dml_pose is None:
            return None
    import cv2
    import numpy as _np
    im = cv2.imread(str(img_path))
    if im is None:
        return None
    blob, s, x0, y0 = _dml_letterbox(im, size)
    out = _dml_pose.run(None, {_dml_pose.get_inputs()[0].name: blob})[0]
    o = out[0]
    scores = o[4]
    boxes = []
    rows = []
    for i in _np.nonzero(scores >= _MIN_CONF)[0]:
        cx, cy, bw, bh = (float(o[j, i]) for j in range(4))
        x1 = (cx - bw / 2 - x0) / s
        y1 = (cy - bh / 2 - y0) / s
        x2 = (cx + bw / 2 - x0) / s
        y2 = (cy + bh / 2 - y0) / s
        if (x2 - x1) < _MIN_BOX or (y2 - y1) < _MIN_BOX:
            continue
        boxes.append(_DMLBox(float(scores[i]), [x1, y1, x2, y2]))
        kp = o[5:56, i].reshape(17, 3).copy()
        kp[:, 0] = (kp[:, 0] - x0) / s
        kp[:, 1] = (kp[:, 1] - y0) / s
        rows.append(kp)
    if not boxes:
        return _DMLResult([])
    return _DMLResult(boxes, np.stack(rows))


def _det_person_dml(img_path: Path, size=1280):
    """onnx(DML) detect: 返回 [(conf, xyxy...), ...] 仅 person (cls0), 原图坐标."""
    global _dml_det
    if _dml_det is None:
        _dml_det = _dml_session(ROOT / "yolo11n.onnx")
        if _dml_det is None:
            return None
    import cv2
    import numpy as _np2
    im = cv2.imread(str(img_path))
    if im is None:
        return None
    blob, s, x0, y0 = _dml_letterbox(im, size)
    out = _dml_det.run(None, {_dml_det.get_inputs()[0].name: blob})[0]
    o = out[0]
    cls_scores = o[4:84]
    best = cls_scores.argmax(axis=0)
    confs = cls_scores[best, _np2.arange(cls_scores.shape[1])]
    res = []
    for i in _np2.nonzero((best == 0) & (confs >= _MIN_CONF))[0]:
        cx, cy, bw, bh = (float(o[j, i]) for j in range(4))
        x1 = (cx - bw / 2 - x0) / s
        y1 = (cy - bh / 2 - y0) / s
        x2 = (cx + bw / 2 - x0) / s
        y2 = (cy + bh / 2 - y0) / s
        if (x2 - x1) < _MIN_BOX or (y2 - y1) < _MIN_BOX:
            continue
        res.append((float(confs[i]), [x1, y1, x2, y2]))
    return res


def _iou_xyxy(a, b) -> float:
    """两个 (x1,y1,x2,y2) 框的 IoU."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / max(1e-6, ua)


def _best_pose_person(r, w: int, h: int):
    """从 pose 结果选 (框, 关键点行): 框置信>=0.25; 同一人物的碎框(IoU>=0.05)合并取外接.

    返回 (bbox[归一化] 或 None, kp_row[17,3] numpy 或 None)。
    贴边/出画裁剪后仍小于 _MIN_BOX 则弃。
    """
    boxes = getattr(r, "boxes", None)
    if boxes is None or len(boxes) == 0:
        return None, None
    kp = getattr(r, "keypoints", None)
    karr = None
    if kp is not None and kp.data is not None and len(kp.data):
        try:
            karr = _as_np(kp.data[0])
            if karr.ndim == 2:
                karr = karr[None, ...]
        except Exception:
            karr = None
    cands = []   # (idx, xyxy)
    for i, b in enumerate(boxes):
        if float(b.conf) < _MIN_CONF:
            continue
        x1, y1, x2, y2 = [float(v) for v in b.xyxy[0].tolist()]
        if (x2 - x1) < _MIN_BOX or (y2 - y1) < _MIN_BOX:
            continue
        cands.append((i, (x1, y1, x2, y2)))
    if not cands:
        return None, None
    # 选主框: 框置信 + 自身骨架强点 (背景幻影/杂物框骨架弱, 即使面积大也不抢框;
    # 小人物时真实人框骨架清晰 → 稳获主框)
    primary = None
    best_score = -1.0
    for idx, xy in cands:
        conf = float(boxes[idx].conf)
        row_r = karr[idx] if (karr is not None and idx < karr.shape[0]) else None
        n_strong = int((row_r[:, 2] > 0.5).sum()) if row_r is not None else 0
        score = conf + 0.12 * n_strong
        if score > best_score:
            best_score = score
            primary = (idx, xy)
    union = list(primary[1])
    while True:
        new_u = list(union)
        for _i, xy in cands:
            if _iou_xyxy(union, xy) >= 0.05:
                new_u = [min(new_u[0], xy[0]), min(new_u[1], xy[1]),
                         max(new_u[2], xy[2]), max(new_u[3], xy[3])]
        if new_u == union:
            break
        union = new_u
    x1 = max(0.0, union[0]); y1 = max(0.0, union[1])
    x2 = min(float(w), union[2]); y2 = min(float(h), union[3])
    if (x2 - x1) < _MIN_BOX or (y2 - y1) < _MIN_BOX:
        return None, None
    bbox = [round((x1 + x2) / 2 / w, 5), round((y1 + y2) / 2 / h, 5),
            round((x2 - x1) / w, 5), round((y2 - y1) / h, 5)]
    row = None
    i0 = primary[0]
    if karr is not None and 0 <= i0 < karr.shape[0]:
        row = karr[i0]
    return bbox, row


def _blank_kpts() -> list[dict]:
    """14 点占位: 全部 vis=0 (未检出/越界视为不标, 坐标仅为占位)."""
    return [{"name": n, "x": 0.5, "y": 0.5, "vis": 0} for n in _KPT_NAMES]


def _pose_person_bbox(r, w: int, h: int):
    """兼容入口: 取 (bbox, 关键点行) — 见 _best_pose_person."""
    return _best_pose_person(r, w, h)


def _kpts_from_pose(r, w: int, h: int, row=None) -> tuple[list, int]:
    """pose 17 点 → 14 点 (neck/forehead + 身体 12 点) + person_vis.

    严格只标真实检出: 低置信/越界 → vis=0 (不标); 不做任何脑补。
    row: 与选中框配对的 [17,3] 关键点行 (None=取 data[0], 单框常态)。
    """
    kp = getattr(r, "keypoints", None)
    if kp is None or kp.data is None or len(kp.data) == 0:
        return _blank_kpts(), 0
    arr = _as_np(kp.data[0])
    d = row if row is not None else (arr if arr.ndim == 2 else arr[0])
    if d is None or getattr(d, "ndim", 2) != 2 or d.shape[0] < 17 or float(d[:, 2].max()) < 0.15:
        return _blank_kpts(), 0

    def X(i):
        return float(d[i][0]) / w

    def Y(i):
        return float(d[i][1]) / h

    def C(i):
        return float(d[i][2])

    out = []
    # ── neck: 鼻(或眼) ↔ 双肩中点 (仅在两侧锚点置信可用时推) ──
    eyes = [i for i in (1, 2, 3, 4) if C(i) > 0.3]
    nose_ok = C(0) > 0.3
    sh = [i for i in (5, 6) if C(i) > 0.3]
    neck = None
    if sh and (nose_ok or eyes):
        sx = sum(X(i) for i in sh) / len(sh)
        sy = sum(Y(i) for i in sh) / len(sh)
        ny = Y(0) if nose_ok else sum(Y(i) for i in eyes) / len(eyes)
        nx = X(0) if nose_ok else sum(X(i) for i in eyes) / len(eyes)
        neck = ((sx + nx) / 2, (ny + sy) / 2)
    elif eyes:
        ex = sum(X(i) for i in eyes) / len(eyes)
        ey = sum(Y(i) for i in eyes) / len(eyes)
        neck = (ex, ey + 0.06)
    if neck:
        out.append({"name": "neck", "x": round(neck[0], 6), "y": round(neck[1], 6), "vis": 2})
    else:
        out.append({"name": "neck", "x": 0.5, "y": 0.5, "vis": 0})
    # ── forehead: 鼻正上 0.07H, 双眼前置 1.9×眼鼻距 (取更靠上) ──
    if nose_ok or eyes:
        nx = X(0) if nose_ok else sum(X(i) for i in eyes) / len(eyes)
        ny = Y(0) if nose_ok else sum(Y(i) for i in eyes) / len(eyes)
        fy = ny - 0.07
        if eyes:
            ey = sum(Y(i) for i in eyes) / len(eyes)
            fy = min(ny - 0.02, ny - 1.9 * (ey - ny))
        out.append({"name": "forehead", "x": round(nx, 6), "y": round(fy, 6), "vis": 2})
    else:
        out.append({"name": "forehead", "x": 0.5, "y": 0.5, "vis": 0})
    # ── 身体 12 点 ──
    for name, i in _BODY:
        vis = 2 if C(i) > 0.5 else (1 if C(i) > 0.15 else 0)
        out.append({"name": name, "x": round(X(i), 6), "y": round(Y(i), 6), "vis": vis})
    # ── 越界 = 不在画面 → 不标 (vis=0); 不脑补、不镜像 ──
    for p in out:
        if not (0.0 <= p["x"] <= 1.0 and 0.0 <= p["y"] <= 1.0):
            p["vis"] = 0
        if p["vis"] == 0:
            p["x"], p["y"] = round(min(max(p["x"], 0.0), 1.0), 6), round(min(max(p["y"], 0.0), 1.0), 6)
    # 独立入口的场景没有 bbox: 按点质量给 pv (label_frame 会用真实 bbox 重算)
    n2 = sum(1 for p in out if p["vis"] == 2)
    n1 = sum(1 for p in out if p["vis"] >= 1)
    return out, (2 if n2 >= 9 else (1 if n1 >= 1 else 0))


def _person_vis(kpts: list, bbox) -> int:
    """2=人物完全可见 (≥9 高置信点) 1=有框(部分可见/待标注) 0=无人/黑帧."""
    if bbox is None:
        return 0
    n2 = sum(1 for k in kpts if k.get("vis") == 2)
    return 2 if n2 >= 9 else 1


def _poly_extent(poly) -> tuple[float, float, float, float]:
    """多边形 (归一化扁平列表) 的 (min_x, min_y, max_x, max_y)."""
    xs = [float(v) for v in poly[0::2]]
    ys = [float(v) for v in poly[1::2]]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else (0.0, 0.0, 1.0, 1.0)


def _ext_of(bbox) -> tuple[float, float, float, float] | None:
    """归一化 [cx,cy,w,h] → (x1,y1,x2,y2); 空框返回 None."""
    if not bbox:
        return None
    cx, cy, w, h = [float(v) for v in bbox[:4]]
    return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)


def _ext_union(a, b):
    """两个范围 (x1,y1,x2,y2) 的并集; 任一为空返回另一个."""
    if a is None:
        return b
    if b is None:
        return a
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _kpts_covered(poly, kpts, tol=0.005) -> bool:
    """poly 范围是否盖住所有画面内可视关键点 (±0.5%) — 判定掩码是否漏了肢体."""
    if poly is None or len(poly) < 6:
        return False
    pts = [k for k in (kpts or [])
           if isinstance(k, dict) and k.get("vis", 0) >= 1
           and 0.0 <= k["x"] <= 1.0 and 0.0 <= k["y"] <= 1.0]
    if not pts:
        return True
    mx1, my1, mx2, my2 = _poly_extent(poly)
    for k in pts:
        if (k["x"] < mx1 - tol or k["y"] < my1 - tol
                or k["x"] > mx2 + tol or k["y"] > my2 + tol):
            return False
    return True


def _kpt_extent(kpts) -> tuple[float, float, float, float] | None:
    """画面内可视关键点的外接范围 (归一化); 少于 6 个可视点返回 None。"""
    pts = [k for k in (kpts or [])
           if isinstance(k, dict) and k.get("vis", 0) >= 1
           and 0.0 <= k["x"] <= 1.0 and 0.0 <= k["y"] <= 1.0]
    if len(pts) < 6:
        return None
    return (min(k["x"] for k in pts), min(k["y"] for k in pts),
            max(k["x"] for k in pts), max(k["y"] for k in pts))


def _clip_extent_to_kpts(ext, kpts, grow: float = 1.25) -> tuple:
    """掩码范围超出关键点范围太多时按关键点范围裁剪 —— 治"掩码带背景把框撑大"。

    只在关键点足够(>=6 可视)时启用; grow 给人体边缘留余量(手/脚常超出骨架外接框)。
    """
    ke = _kpt_extent(kpts)
    if ke is None or ext is None:
        return ext
    kx1, ky1, kx2, ky2 = ke
    gw = (kx2 - kx1) * (grow - 1.0) / 2 + 0.02
    gh = (ky2 - ky1) * (grow - 1.0) / 2 + 0.02
    x1 = max(float(ext[0]), kx1 - gw)
    y1 = max(float(ext[1]), ky1 - gh)
    x2 = min(float(ext[2]), kx2 + gw)
    y2 = min(float(ext[3]), ky2 + gh)
    if x2 - x1 < 0.01 or y2 - y1 < 0.01:
        return ext
    return (x1, y1, x2, y2)


def _ext_leak(inner, outer, grow: float = 0.12) -> float:
    """inner 有多大比例落在 outer (外扩 grow) 之外 —— 判"这份掩码是不是抢了背景"。

    两个独立检测器 (pose 框 / yolo-seg 框) 都指到人身上时, 真人体掩码不该大幅外溢到
    检测框之外; 外溢 = 掩码圈住了墙/窗/地面 (那种掩码再大也不能当夹紧基准)。
    返回 0~1, 越大越可疑。
    """
    if inner is None or outer is None:
        return 1.0
    gw = (outer[2] - outer[0]) * grow + 0.01
    gh = (outer[3] - outer[1]) * grow + 0.01
    ox1, oy1 = outer[0] - gw, outer[1] - gh
    ox2, oy2 = outer[2] + gw, outer[3] + gh
    ix = max(0.0, min(inner[2], ox2) - max(inner[0], ox1))
    iy = max(0.0, min(inner[3], oy2) - max(inner[1], oy1))
    ai = max(1e-9, (inner[2] - inner[0]) * (inner[3] - inner[1]))
    return 1.0 - (ix * iy) / ai


def _clamp_box_to_ext(bbox: list, ext, grow: float = 1.25,
                      floor_margin: float = 0.045) -> list:
    """最终框夹紧到"给定范围 ext + 余量"内 —— 治掩码把墙/窗/背景带进框。

    余量取 max(floor_margin, grow-1 比例的一半), 保证手/脚/头盔这类基准范围之外的
    部位不被切掉。**基准选谁是调用方的事** —— 骨架和像素掩码各有失效场景, 取舍见
    `label_frame` 末尾 (2026-09-12: 基准不可信会把框切成碎片)。
    """
    if ext is None or not bbox:
        return bbox
    kx1, ky1, kx2, ky2 = ext
    mx = max(floor_margin, (kx2 - kx1) * (grow - 1.0) / 2)
    my = max(floor_margin, (ky2 - ky1) * (grow - 1.0) / 2)
    lim = (max(0.0, kx1 - mx), max(0.0, ky1 - my),
           min(1.0, kx2 + mx), min(1.0, ky2 + my))
    x1 = float(bbox[0]) - float(bbox[2]) / 2
    y1 = float(bbox[1]) - float(bbox[3]) / 2
    x2 = float(bbox[0]) + float(bbox[2]) / 2
    y2 = float(bbox[1]) + float(bbox[3]) / 2
    nx1, ny1 = max(x1, lim[0]), max(y1, lim[1])
    nx2, ny2 = min(x2, lim[2]), min(y2, lim[3])
    if nx2 - nx1 < 0.02 or ny2 - ny1 < 0.02:
        return bbox
    return [round((nx1 + nx2) / 2, 5), round((ny1 + ny2) / 2, 5),
            round(nx2 - nx1, 5), round(ny2 - ny1, 5)]


def _clip_poly_to_ext(poly: list, ext, grow: float = 1.25,
                      floor_margin: float = 0.045) -> list | None:
    """多边形顶点夹紧到"给定范围 ext + 余量"内 —— 治"多边形被背景抢"。

    掩码把墙/窗圈进来时, 这些顶点会落在基准范围之外 → 直接夹回人体范围。
    """
    if ext is None or not poly or len(poly) < 6:
        return poly
    kx1, ky1, kx2, ky2 = ext
    mx = max(floor_margin, (kx2 - kx1) * (grow - 1.0) / 2)
    my = max(floor_margin, (ky2 - ky1) * (grow - 1.0) / 2)
    lim = (max(0.0, kx1 - mx), max(0.0, ky1 - my),
           min(1.0, kx2 + mx), min(1.0, ky2 + my))
    out = []
    for i in range(0, len(poly) - 1, 2):
        x = min(max(float(poly[i]), lim[0]), lim[2])
        y = min(max(float(poly[i + 1]), lim[1]), lim[3])
        out += [round(x, 6), round(y, 6)]
    return out if len(out) >= 6 else poly


def _clamp_box_to_kpts(bbox: list, kpts, grow: float = 1.25, floor_margin: float = 0.045) -> list:
    """兼容入口: 以关键点外接框为基准夹紧 (>=6 可视点才生效)。"""
    return _clamp_box_to_ext(bbox, _kpt_extent(kpts), grow, floor_margin)


def _clip_poly_to_kpts(poly: list, kpts, grow: float = 1.25, floor_margin: float = 0.045) -> list | None:
    """兼容入口: 以关键点外接框为基准夹多边形顶点。"""
    return _clip_poly_to_ext(poly, _kpt_extent(kpts), grow, floor_margin)


def _clamp_extent_to_poly(bbox: list, poly, grow: float = 1.15,
                          floor_margin: float = 0.03) -> list:
    """兼容入口: 以掩码范围 (poly extent) 为基准夹框。"""
    if not poly or len(poly) < 6:
        return bbox
    return _clamp_box_to_ext(bbox, _poly_extent(poly), grow, floor_margin)


_COVER_PAD = 0.10      # 「大框框人」的地板: 框每边至少比人物轮廓范围大 10%
                       # 与 _prompt_bbox(pad=0.12) 同源。口径依据见 _cover_box_to_ext。


# ── 口径判据的容差: **单一真源在 `convention.py`**, 这里只做 re-export ─────────────
# 历史教训: 同一个概念(点是否在框外 / 框是否越出 [0,1] / 框边是否贴着画面)曾在 4 个脚本
# 里各写各的, 出现 1e-9 / 1e-6 / 1e-4 三种阈值 ⇒ 同一份数据两个工具报出不同结论。
# 判据一律走下面这些函数, 不要在别处硬编码这类阈值字面量。
from convention import (                        # noqa: E402
    CONV_TOL_N, CONV_TOL_PX, TIGHT_ABS_PX, TIGHT_REL,   # noqa: F401
    conv_edge_flags_n, conv_edge_flags_px,              # noqa: F401
    conv_is_oob_n, conv_is_oob_px,                      # noqa: F401
    conv_oob_n, conv_oob_px,                            # noqa: F401
)


def _cover_box_to_ext(bbox: list, ext, pad: float = _COVER_PAD) -> list:
    """把框**只往外长**到至少含住 ext 且每边留 pad×边长 的余量 —— 绝不缩小。

    口径（2026-09-12 用户说明，本项目标注约定）:
      框 = **大框框人**；多边形 = **把人物边缘完全走一遍**；关键点 = 标关节。
    ⇒ 多边形才是"人物真实范围"的代理, 而**框必须含住它并留出可见余量**。
      「框贴到多边形边缘上」(r≈1.0) 不是"紧凑"，是**违反约定**。

    与 `_clamp_box_to_ext` 是一对反方向的约束（避坑 115）:
      * `_clamp_box_to_ext` 只管"别圈背景" —— 往里夹, 上界
      * 本函数只管"别切人"     —— 往外长, 地板
      只做其中一个, 另一个方向的错误就**结构上看不见**（2026-09-12 实测:
      只有上界时, 846 帧里有 458 帧框到轮廓的边距 <8px、97 帧为 0）。
    """
    if ext is None or not bbox or len(bbox) < 4:
        return bbox
    mx = (ext[2] - ext[0]) * pad
    my = (ext[3] - ext[1]) * pad
    wx1, wy1 = ext[0] - mx, ext[1] - my
    wx2, wy2 = ext[2] + mx, ext[3] + my
    x1 = float(bbox[0]) - float(bbox[2]) / 2
    y1 = float(bbox[1]) - float(bbox[3]) / 2
    x2 = float(bbox[0]) + float(bbox[2]) / 2
    y2 = float(bbox[1]) + float(bbox[3]) / 2
    nx1, ny1 = min(x1, wx1), min(y1, wy1)
    nx2, ny2 = max(x2, wx2), max(y2, wy2)
    if nx2 - nx1 <= 0.02 or ny2 - ny1 <= 0.02:
        return bbox
    return [round((nx1 + nx2) / 2, 5), round((ny1 + ny2) / 2, 5),
            round(nx2 - nx1, 5), round(ny2 - ny1, 5)]


def _pack_box01(bbox) -> list | None:
    """把框打包成归一化 YOLO `[cx,cy,w,h]`，**保证反推边界不越出 [0,1]**。

    为什么需要单独一道: 写盘用的 `cx,cy,w,h` 是**各自** round 到 5 位的，
    两个 ±5e-6 的舍入叠加后，`cx+w/2` 可以比原边界大 —— 实测归档里有 **192 帧**
    框越出画面（YOLO 标签越界非法）。地板只会往外长，贴边人物的框更容易被顶出边界。

    ⚠️ 只夹**真正越界的那一边**。早期写法是「越界多少就把 w 整体收缩 2×越界量」，
       那会**连带把没越界的一边也切进去**（cx=0.9,w=0.3 ⇒ 左边界 0.75 被切到 0.80）；
       离线模拟里该写法让 **27 帧**的关键点掉到框外（本来只有 11 帧）。
       边界问题必须在**边界**上解决，不能转成尺寸问题。

    做法: 边界先朝画面内缩 1e-5，再在 1e-5 网格上朝内取整（左/上 ceil，右/下 floor），
    这样 round 的 5e-6 舍入被完全吸收，反推必然落在 [0,1]。
    1e-5 ≈ 1280px 上的 0.013px，对标注内容无影响。退化框返回 None（调用方保持原框）。
    """
    if not bbox or len(bbox) < 4:
        return bbox
    try:
        cx = float(bbox[0])
        cy = float(bbox[1])
        w = float(bbox[2])
        h = float(bbox[3])
    except (TypeError, ValueError):
        return bbox
    if w <= 0.0 or h <= 0.0:
        return None
    q = 100000.0
    eps = 1.0 / q          # 每条边都留 eps —— 只留一侧会被 clamp 吃掉 (实测右边界顶到 1.000005)
    lo, hi = eps, 1.0 - eps
    x1 = min(max(cx - w / 2.0, lo), hi)
    x2 = min(max(cx + w / 2.0, lo), hi)
    y1 = min(max(cy - h / 2.0, lo), hi)
    y2 = min(max(cy + h / 2.0, lo), hi)
    X1 = math.ceil(x1 * q - 1e-9) / q
    Y1 = math.ceil(y1 * q - 1e-9) / q
    X2 = math.floor(x2 * q + 1e-9) / q
    Y2 = math.floor(y2 * q + 1e-9) / q
    if X2 <= X1 or Y2 <= Y1:
        return None
    return [round((X1 + X2) / 2.0, 5), round((Y1 + Y2) / 2.0, 5),
            round(X2 - X1, 5), round(Y2 - Y1, 5)]


def _ext_center(e) -> tuple | None:
    """(x1,y1,x2,y2) → 中心点; 空返回 None。"""
    if e is None:
        return None
    return ((float(e[0]) + float(e[2])) / 2.0, (float(e[1]) + float(e[3])) / 2.0)


def _ext_center_dist(a, b) -> float | None:
    """两个范围的中心距 (归一化); 任一为空返回 None。"""
    ca, cb = _ext_center(a), _ext_center(b)
    if ca is None or cb is None:
        return None
    return math.hypot(ca[0] - cb[0], ca[1] - cb[1])


# 幻影阈值: 两个独立检测器 (pose 骨架 / yolo-seg 像素) 的中心距超过它 ⇒ 指着不同地方。
# 定标依据 (2026-09-30 全库普查 767 帧, 见 runs/_phantom_census.json):
#   真幻影 (框真的飞到别处) 2 帧: f0032 d=0.695 / f0038 d=0.540;
#   区间 (0.15, 0.5] 全库**无样本** ⇒ 0.15 与 0.5 之间是天然分界, 取 0.5。
#   ⚠️ 旧值 0.15 **太松**: 会把 0.15~0.27 的 43 个"安全帽 vs 头顶"正常误差
#   (两框中心几乎重合) 一并误判为幻影 ⇒ 报了 45 帧, 真幻影只有 2 帧。
_PHANTOM_D = 0.5


def _is_phantom(pose_ext, seg_ext, thresh: float = _PHANTOM_D) -> bool:
    """两个独立检测器是否**指着不同地方** —— 至少一个是幻影。

    为什么需要它 (避坑 118 的补丁): 「大框框人」判据的参照是**标注自身的多边形/关键点**,
    而它们在幻影帧上**跟着幻影一起飞**, 与框自洽 ⇒ 判绿。必须引入第三方一致性判据。
    任一缺失 ⇒ **不报** (宁松勿切: 只有一个检测器时无从判分歧, 不该凭它否定标注)。
    """
    d = _ext_center_dist(pose_ext, seg_ext)
    return d is not None and d > thresh


def _phantom_verdict(pose_ext, seg_ext, witness, thresh: float = _PHANTOM_D) -> str:
    """幻影定向: 返回 'agree' | 'phantom' | 'pose' | 'seg'。

    witness = 独立证人的**归一化中心点** (本产线用蓝头盔颜色质心; None = 无证人)。
      * 两框一致           → 'agree'   (无分歧)
      * 分歧且无证人       → 'phantom' (判幻影, 但不定向)
      * 证人离 pose 更近   → 'pose'    (pose 是真人, seg 误检)
      * 证人离 seg  更近   → 'seg'     (seg 是真人, pose 是幻影)
    """
    if not _is_phantom(pose_ext, seg_ext, thresh):
        return "agree"
    if witness is None:
        return "phantom"
    ca, cb = _ext_center(pose_ext), _ext_center(seg_ext)
    if ca is None or cb is None:
        return "phantom"
    wx, wy = float(witness[0]), float(witness[1])
    dp = math.hypot(wx - ca[0], wy - ca[1])
    ds = math.hypot(wx - cb[0], wy - cb[1])
    return "seg" if ds < dp else "pose"


def _clamp_basis(kpts, poly, pose_ext, seg_ext) -> tuple:
    """选末尾夹紧的基准 —— 返回 (ext 或 None, 来源标签, 骨架覆盖比 span, 掩码是否可信)。

    判据 (2026-09-12 定; 旧版无条件用骨架, 结果把框切成碎片):
      ① 骨架面积 >=0.6×且 <=2.0× pose 框面积 ⇒ 用骨架 (与 `_prompt_bbox` 同一门槛)
      ② 否则掩码外溢检测框 <=25% (不是墙/窗掩码) ⇒ 用掩码范围 (像素级, 更硬)
      ③ 都不成立 ⇒ 返回 None, 不夹 (宁松勿切: 框大是噪声, 框切人是错误标签)
    """
    ke = _kpt_extent(kpts)
    pe = _poly_extent(poly) if (poly and len(poly) >= 6) else None
    span = None
    if ke is not None and pose_ext is not None:
        ka = (ke[2] - ke[0]) * (ke[3] - ke[1])
        pa = max(1e-9, (pose_ext[2] - pose_ext[0]) * (pose_ext[3] - pose_ext[1]))
        span = ka / pa
    mask_ok = (pe is not None and seg_ext is not None
               and _ext_leak(pe, seg_ext) <= 0.25)
    if ke is not None and span is not None and 0.6 <= span <= 2.0:
        return ke, "kpt", span, mask_ok
    if pe is not None and mask_ok:
        return pe, "mask", span, mask_ok
    return None, "none", span, mask_ok


def _expand_bbox_to_person(bbox: list, kpts: list, poly, w: int, h: int, pad=0.04) -> list:
    """完全框选: 人物框扩展到 [掩码像素 ∪ 画面内可视关键点] 的并集 + 边距.

    yolo-pose 的框对贴边/出画人物常只罩住模型认出的部分 (头/躯干), 腿脚、
    头盔顶等画面边缘的人体部分会漏在框外 — 用像素级掩码与关键点并集把框补全。
    结果 clamp 到 [0,1] (人体贴边时框即达画面边缘)。
    """
    x1 = float(bbox[0]) - float(bbox[2]) / 2
    y1 = float(bbox[1]) - float(bbox[3]) / 2
    x2 = float(bbox[0]) + float(bbox[2]) / 2
    y2 = float(bbox[1]) + float(bbox[3]) / 2
    for k in kpts or []:
        if not isinstance(k, dict):
            continue
        if k.get("vis", 0) >= 1 and 0.0 <= k["x"] <= 1.0 and 0.0 <= k["y"] <= 1.0:
            x1 = min(x1, float(k["x"])); y1 = min(y1, float(k["y"]))
            x2 = max(x2, float(k["x"])); y2 = max(y2, float(k["y"]))
    if poly and len(poly) >= 6:
        xs = [float(v) for v in poly[0::2]]
        ys = [float(v) for v in poly[1::2]]
        if xs and ys:
            x1 = min(x1, min(xs)); y1 = min(y1, min(ys))
            x2 = max(x2, max(xs)); y2 = max(y2, max(ys))
    pw = (x2 - x1) * pad + 0.004
    ph = (y2 - y1) * pad + 0.004
    x1 = max(0.0, x1 - pw); y1 = max(0.0, y1 - ph)
    x2 = min(1.0, x2 + pw); y2 = min(1.0, y2 + ph)
    if (x2 - x1) * w < _MIN_BOX or (y2 - y1) * h < _MIN_BOX:
        return bbox
    return [round((x1 + x2) / 2, 5), round((y1 + y2) / 2, 5),
            round(x2 - x1, 5), round(y2 - y1, 5)]


def _prompt_bbox(bbox, kpts, w: int, h: int, pad=0.12):
    """SAM2 提示框: 人体小时收紧到关键点范围+边距 (防背景抢框).

    仅当关键点范围 ≈ 检测框范围 (≥0.6 面积比, 说明骨架覆盖全身) 才收紧 —
    躺地/部分出画的人骨架只覆盖部分身体, 收紧会把框切小, 必须退回检测框。
    返回 (提示框, tightened: bool)。
    """
    pts = [k for k in (kpts or []) if isinstance(k, dict) and k.get("vis", 0) >= 1
           and 0.0 <= k["x"] <= 1.0 and 0.0 <= k["y"] <= 1.0]
    if not bbox:
        return None, False
    box_area = max(1e-6, float(bbox[2]) * float(bbox[3]))
    if len(pts) >= 5:
        xs = [float(k["x"]) for k in pts]
        ys = [float(k["y"]) for k in pts]
        x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
        ka = (x2 - x1) * (y2 - y1)
        if (x2 - x1) * w >= _MIN_BOX and (y2 - y1) * h >= _MIN_BOX \
                and ka >= 0.6 * box_area:
            base = [(x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1]
            return _expand_bbox_to_person(base, kpts, None, w, h, pad=pad), True
    return list(bbox), False


def _poly_area(poly) -> float:
    """多边形范围面积 (归一化); 用于 SAM2 掩码是否大面积抢背景的判据."""
    if not poly or len(poly) < 6:
        return 0.0
    x1, y1, x2, y2 = _poly_extent(poly)
    return (x2 - x1) * (y2 - y1)


def _sam2_gate(poly, kpts) -> bool:
    """是否需要跑 SAM2 (CPU 大头, 能省则省):
    分割掩码已盖住全部关键点、未贴边、面积正常 → 不需要 (保留分割多边形);
    无掩码 / 关键点外露 / 贴边出画 / 掩码过小 → 需要 (SAM2 补全/精修)."""
    if poly is None or len(poly) < 6:
        return True
    if not _kpts_covered(poly, kpts):
        return True
    x1, y1, x2, y2 = _poly_extent(poly)
    if x1 < 0.04 or y1 < 0.04 or x2 > 0.96 or y2 > 0.96:
        return True               # 贴边/出画人物: SAM2 补全可见肢体
    if _poly_area(poly) < 0.02:
        return True               # 掩码太小: SAM2 确认
    return False


def _poly_covers(a: list, b) -> bool:
    """a 多边形范围是否覆盖 b (容差 0.5%) — 防 SAM2 幻影/碎片掩码替换真人体掩码."""
    if b is None or len(b) < 6:
        return True
    ax1, ay1, ax2, ay2 = _poly_extent(a)
    bx1, by1, bx2, by2 = _poly_extent(b)
    t = 0.005
    return (ax1 <= bx1 + t and ay1 <= by1 + t and ax2 >= bx2 - t and ay2 >= by2 - t)


def _boxes_agree(a: list, b: list, w: int, h: int, min_iou=0.4) -> bool:
    """两个归一化框是否指向同一目标 (交并/较小面积), 防止"人物框+窗户分割框"错配."""
    ax1, ay1 = (a[0] - a[2] / 2) * w, (a[1] - a[3] / 2) * h
    ax2, ay2 = (a[0] + a[2] / 2) * w, (a[1] + a[3] / 2) * h
    bx1, by1 = (b[0] - b[2] / 2) * w, (b[1] - b[3] / 2) * h
    bx2, by2 = (b[0] + b[2] / 2) * w, (b[1] + b[3] / 2) * h
    ix = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    iy = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = ix * iy
    aa = max(1e-6, (ax2 - ax1) * (ay2 - ay1))
    ba = max(1e-6, (bx2 - bx1) * (by2 - by1))
    return inter / min(aa, ba) >= min_iou


def _get_sam2():
    """SAM2 图像级 predictor (框提示 → 精确人物像素). lazy, CPU; 缺模型/组件返回 None."""
    global _sam2
    if _sam2 is None:
        ck = ROOT / "models" / "sam2" / "sam2.1_hiera_tiny.pt"
        cf = ROOT / "tools" / "sam2" / "sam2" / "configs" / "sam2.1" / "sam2.1_hiera_t.yaml"
        if not ck.exists() or not cf.exists():
            return None
        try:
            import sys as _sys
            _sys.path.insert(0, str(ROOT / "tools" / "sam2"))
            from sam2.build_sam import build_sam2
            from sam2.sam2_image_predictor import SAM2ImagePredictor
            model = build_sam2(str(cf), str(ck), device="cpu")
            _sam2 = SAM2ImagePredictor(model)
        except Exception:
            _sam2 = None
    return _sam2


def _sam2_person_mask(img_path: Path, box):
    """SAM2 框提示分割: 返回 (mask_bool[H,W]) 或 None. box=[cx,cy,w,h](归一化)."""
    try:
        import cv2
        im = cv2.imread(str(img_path))
        if im is None:
            return None
        h, w = im.shape[:2]
        cx, cy, bw, bh = box
        x1 = max(0, int((cx - bw / 2) * w)); y1 = max(0, int((cy - bh / 2) * h))
        x2 = min(w, int((cx + bw / 2) * w)); y2 = min(h, int((cy + bh / 2) * h))
        if x2 - x1 < 8 or y2 - y1 < 8:
            return None
        pred = _get_sam2()
        if pred is None:
            return None
        pred.set_image(im)
        masks, scores, _ = pred.predict(box=[x1, y1, x2, y2], multimask_output=False)
        if masks is None or len(masks) == 0:
            return None
        m = (masks[0] > 0.5)
        if m.sum() < 200:
            return None
        return m
    except Exception:
        return None


def _kpts_fallback_by_box(img_path: Path):
    """历史入口保留: 旧版"解剖外推脑补"已废弃 — 一律返回 14 点全 0 (不标).

    脑补/镜像点会标在错误位置且导出必错, 改为宁缺毋滥 (vis=0 由训练方忽略).
    """
    return _blank_kpts(), 0


def _detect_kpts(img_path: Path):
    """独立关键点入口 (兼容旧脚本): 返回 (14点, person_vis); 黑帧/失败 → (全0点, 0)."""
    import cv2
    try:
        im = cv2.imread(str(img_path))
        if im is None:
            return None, None
        if float(im.mean()) < _BLACK_MEAN:
            return _blank_kpts(), 0
        r = _pose_predict(img_path)
        if r is None:
            return _blank_kpts(), 0
        w, h = im.shape[1], im.shape[0]
        _b, row = _best_pose_person(r, w, h)
        return _kpts_from_pose(r, w, h, row)
    except Exception:
        return None, None


def _mask_to_bbox(mask, img_w, img_h):
    """分割掩码 → 像素级精确框 归一化 [cx,cy,w,h] (贴边人物不再被丢弃)."""
    import numpy as np
    ys, xs = np.nonzero(mask)
    if xs.size < _MIN_BOX * 4:
        return None
    x1, x2 = float(xs.min()), float(xs.max())
    y1, y2 = float(ys.min()), float(ys.max())
    x1 = max(0.0, x1); y1 = max(0.0, y1)
    x2 = min(float(img_w), x2); y2 = min(float(img_h), y2)
    if (x2 - x1) < _MIN_BOX or (y2 - y1) < _MIN_BOX:
        return None
    cx, cy = (x1 + x2) / 2 / img_w, (y1 + y2) / 2 / img_h
    bw, bh = (x2 - x1) / img_w, (y2 - y1) / img_h
    return [round(cx, 5), round(cy, 5), round(bw, 5), round(bh, 5)], (x1, y1, x2, y2)


def _restrict_to_base(p3, base, w: int, h: int, margin: float = 0.015):
    """把 SAM2 多边形限制在**基线(yolo-seg)多边形向外扩 margin** 的范围内。

    为什么（怪怪「多边形还是取到了背景」+ 2026-09-30 实测）
    ------------------------------------------------------
    对"背景占比最差"的帧分别量**不开 SAM2** 与**开 SAM2**（`runs/_probe_bg_blame.py`）：

        | 帧 | 无SAM2 灰占比 | 用SAM2 灰占比 |
        |---|---|---|
        | `f0000_drag_brace_hands_none_s12` | 36.2% | **76.0%** |
        | `f0018_stagger_brace_hands_curl_s6` | 14.8% | **50.1%** |
        | `f0039_drag_guard_head_curl_s4` | 22.2% | **40.2%** |
        | `f0038_drag_guard_head_curl_s11` | （基线无多边形） | **87.0%** |

    ⇒ **吃背景的是 SAM2**（灰占比翻倍~三倍），基线 yolo-seg 干净得多。
    **SAM2 的职责是"精修边界"，不是"扩张领地"** ⇒ 用基线当**围栏**：
    允许它在边界上小修小补，**不允许它圈进几十上百像素外的新区域**（那片墙/那块地）。

    margin = 画面宽 1.5%（≈19px @1280）：够修正十几像素的边界误差，又拦得住"抢墙"。
    ⚠️ 只在**基线存在**时能当围栏；基线为空时返回原值（那一路另有"没人脸/没人"的守卫）。
    """
    import cv2
    import numpy as np
    if not p3 or len(p3) < 6 or not base or len(base) < 6:
        return p3
    W, H = int(w), int(h)

    def _fill(poly):
        m = np.zeros((H, W), np.uint8)
        pts = np.array([[float(poly[i]) * W, float(poly[i + 1]) * H]
                        for i in range(0, len(poly) - 1, 2)], np.int32)
        cv2.fillPoly(m, [pts], 255)
        return m

    k = max(3, int(round(margin * W))) | 1        # 核必须是奇数
    fence = cv2.dilate(_fill(base),
                       cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    inter = cv2.bitwise_and(_fill(p3), fence)
    if int(inter.sum() // 255) < 50:
        return p3                                  # 交集太小 = 基线本身可疑 ⇒ 不动它
    return _mask_polygon(inter > 0, W, H)


def _bg_fraction(im, poly):
    """多边形内**低饱和(灰白)**像素占比 —— 用来判"这个多边形是不是基本圈的是墙/地"。

    ⚠️ **数据集相关**：本数据集里"人"是**高饱和**的红工装 + 蓝安全帽，"房间"是灰白
      （实测人=高饱和、背景 S<55）。**换数据集**（人穿灰衣、或背景很艳）这个量就失效 ⇒
      所以只用在**没有基线多边形**的兜底路径上（正常路径有围栏，不靠它）。
    """
    import cv2
    import numpy as np
    if im is None or not poly or len(poly) < 6:
        return None
    H, W = im.shape[:2]
    hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
    m = np.zeros((H, W), np.uint8)
    pts = np.array([[float(poly[i]) * W, float(poly[i + 1]) * H]
                    for i in range(0, len(poly) - 1, 2)], np.int32)
    cv2.fillPoly(m, [pts], 255)
    n = int(m.sum() // 255)
    if n < 50:
        return None
    return float(((hsv[:, :, 1] < 55) & (m > 0)).sum()) / n


def _helmet_disc(im, bbox):
    """**兜底**：用安全帽颜色在框顶找头盔 → `(cx, cy, rad)`；找不到返回 None。

    ⚠️ **这是数据集相关的启发式**：本数据集所有工人都戴**高饱和蓝**色安全帽
      （`data/datasets/*/data.yaml` 的采集条件）。所以只在 **pose 头部证据完全缺失**时启用
      （实测 224/767 = 29% 的帧没有任何头部关键点）。
      **换数据集**（不戴盔 / 盔不是蓝色）时它自然返回 None，**不会污染标注** —— 宁可不动。
    半径由**头盔连通块的等效圆**直接算出（`sqrt(area/π)`），比任何"按比例估"都准。
    """
    import cv2
    import numpy as np
    if im is None or not bbox or len(bbox) < 4:
        return None
    H, W = im.shape[:2]
    hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, (95, 90, 70), (130, 255, 255))
    cx0, cy0, bw, bh = [float(v) for v in bbox[:4]]
    x0 = int(max(0, (cx0 - bw / 2) * W))
    x1 = int(min(W, (cx0 + bw / 2) * W))
    y0 = int(max(0, (cy0 - bh / 2) * H))
    y1 = int(min(H, (cy0 - bh / 2 + 0.45 * bh) * H))     # 只在框的上 45% 找（头在框顶）
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    roi = np.zeros_like(m)
    roi[y0:y1, x0:x1] = m[y0:y1, x0:x1]
    n, _lab, stats, cent = cv2.connectedComponentsWithStats(roi, 8)
    if n <= 1:
        return None
    k = 1 + int(np.argmax(stats[1:, 4]))                 # 最大连通块
    area = int(stats[k, 4])
    if area < max(150, 0.0006 * W * H):                  # 太小 = 噪声/反光, 不认
        return None
    # ★ 紧凑度闸门（2026-09-30 实测踩到）：背景里也有蓝色构件（如一条**竖直蓝带**贯穿画面），
    #   它的最大连通块会被当成"头盔"放到**错误位置**。头盔是**圆钝**的 ⇒ 长宽比必须接近 1，
    #   且不能是细长条。不满足就返回 None（宁可不动，也不要加错 —— 那是"改一个赔三个"）。
    bw_px, bh_px = int(stats[k, 2]), int(stats[k, 3])
    if bh_px <= 0 or bw_px <= 0:
        return None
    ar = bw_px / bh_px
    if not (0.55 <= ar <= 1.8):                          # 不像圆钝的头盔
        return None
    if area > 0.05 * W * H:                              # 占画面 5% 以上 = 背景大色块
        return None
    cx, cy = float(cent[k][0]), float(cent[k][1])
    rad = max(0.030, float(np.sqrt(area / np.pi)) / W)
    return (cx / W, cy / H, min(rad, 0.22))


def _head_disc(r, w: int, h: int, row=None, kpts=None, bbox=None, im=None):
    """估一个「头」的圆 `(cx, cy, rad)`（归一化）；证据不足返回 None。

    为什么需要它（怪怪 2026-09-30「有些人头没标上」）
    ------------------------------------------------
    实测（全库 767 有框帧，`runs/_probe_head_bg.py`）：
      * 头盔像素落在**框外** 的：p50 = **0.000**（框基本是准的）
      * 头盔像素落在**多边形外**的：p50 = **0.383**，**21% 的帧 > 90%**
    ⇒ 病灶在 **yolo11s-seg 的 person 掩码不把安全帽算作人体**（只覆盖工装），
      所以多边形缺头，而框因为走"关键点地板"侥幸盖住了头。
    ⇒ 修法：**用 pose 的头部关键点（鼻/眼/耳，COCO 0~4）估一个圆，并入多边形**。

    ★ 尺度**只能用"直接量头"的证据**（两个都失败过一次）：
      · ❌ 肩宽 —— 同日实测趴地帧肩线被透视压扁（肩宽→0.001），当分母必爆；
      · ❌ 框高 —— 框常常只框住**局部**（只露一个头时框高就是头高），0.10×框高 算出来
        半径 30px 而头盔有 100px（实测无效：97.8% → 97.4%）。
      · ✅ **pose 头部关键点(0~4)** 的最大间距（眼↔耳 ≈ 头直径）；
      · ✅ **`neck` ↔ `forehead`** 的距离（≈ 1 个头高 ≈ 2r）。
    **两者都没有 ⇒ 返回 None，不猜**（宁可不动，也不要瞎加一个圆）。
    """
    import numpy as np
    head = []
    kp = getattr(r, "keypoints", None)
    if kp is not None and getattr(kp, "data", None) is not None and len(kp.data):
        arr = _as_np(kp.data[0])
        d = row if row is not None else (arr if arr.ndim == 2 else arr[0])
        if d is not None and getattr(d, "ndim", 2) == 2 and d.shape[0] >= 5:
            head = [(float(d[i][0]) / w, float(d[i][1]) / h)
                    for i in range(5) if float(d[i][2]) > 0.3]
    km = {k.get("name"): k for k in (kpts or []) if k.get("vis", 0) >= 1}

    rad = 0.0
    if len(head) >= 2:                       # 头直径 = 头部点最大间距
        rad = max(rad, 0.5 * max(math.dist(a, b)
                                 for i, a in enumerate(head) for b in head[i + 1:]))
    nk, fh = km.get("neck"), km.get("forehead")
    if nk is not None and fh is not None:    # 颈↔额 ≈ 1 个头高 ≈ 2r
        # ★ 倍数 0.60 → 1.35：实测(490 帧, 全库) 头盔等效半径 / dist(neck,forehead) 的分布是
        #   p10=0.40 **p50=0.84** p90=1.85（max 16.7 是退化值）。用 0.60 只有 **51% 覆盖**，
        #   实测样本 24 帧里只改善 2 帧 ⇒ 取 **p75=1.34 ≈ 1.35** 才覆盖大多数。
        rad = max(rad, 1.35 * math.dist((nk["x"], nk["y"]), (fh["x"], fh["y"])))
    if rad <= 0.0:
        # 没有任何"直接量头"的 pose 证据 ⇒ 退回**头盔颜色**兜底（数据集相关；找不到就返回 None，不动）
        return _helmet_disc(im, bbox)
    rad = min(max(rad, 0.030), 0.22)         # 夹紧: 下限 3%(≈38px) / 上限 22% 防爆

    if len(head) >= 1:                       # 圆心: 头部点质心
        cx = sum(p[0] for p in head) / len(head)
        cy = sum(p[1] for p in head) / len(head)
    elif nk is not None and fh is not None:  # 颈额中点
        cx, cy = (nk["x"] + fh["x"]) / 2, (nk["y"] + fh["y"]) / 2
    else:
        return None
    return (float(cx), float(cy), float(rad))


def _union_disc_into_poly(poly, disc, w: int, h: int):
    """把圆**并入**多边形（栅格域求并集后重新取轮廓）—— 保拓扑、不会自交。"""
    import cv2
    import numpy as np
    if not poly or len(poly) < 6 or disc is None:
        return None
    W, H = int(w), int(h)
    m = np.zeros((H, W), np.uint8)
    pts = np.array([[float(poly[i]) * W, float(poly[i + 1]) * H]
                    for i in range(0, len(poly) - 1, 2)], np.int32)
    cv2.fillPoly(m, [pts], 255)
    cx, cy, rad = disc
    cv2.circle(m, (int(round(cx * W)), int(round(cy * H))),
               max(2, int(round(rad * W))), 255, -1)
    return _mask_polygon(m > 0, W, H)


def _mask_polygon(mask, img_w, img_h):
    """掩码边缘点 → 归一化多边形 (yolo-seg 格式), 简化到 <=100 点."""
    import cv2
    import numpy as np
    m8 = (mask.astype(np.uint8) * 255)
    cont, _ = cv2.findContours(m8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cont:
        return None
    c = max(cont, key=cv2.contourArea)
    if cv2.contourArea(c) < _MIN_BOX * 4:
        return None
    eps = 0.003 * cv2.arcLength(c, True)
    ap = cv2.approxPolyDP(c, eps, True).reshape(-1, 2)
    if len(ap) < 3:
        return None
    if len(ap) > 100:
        ap = ap[::max(1, len(ap) // 100)]
    return [round(v / img_w, 6) if i % 2 == 0 else round(v / img_h, 6)
            for i, v in enumerate(ap.flatten().tolist())]


def _tighten_mask(m, kpts, w: int, h: int, steps=(3, 5, 7)):
    """**逐级腐蚀**掩码，直到"再多腐蚀就要丢关键点"为止 —— 治"多边形不够贴合"。

    为什么（怪怪 2026-09-30「选中人了，但不够贴合，还是选到了很多背景部分」）
    ----------------------------------------------------------------------
    * **不是阈值问题**：实测 `masks.data` 已经是二值，阈值 0.5→0.9 **结果完全一样**
      （`runs/_probe_tighten.py`）。边界偏松是因为掩码来自**低分辨率原型上采样**，
      轮廓量化在原型网格上（160→1280 时每格 ≈8px）。
    * **腐蚀是有效杠杆**（`runs/_probe_erode.py`，12 帧抽样）：
        腐蚀 0px  → 多边形内背景色占比 p50 **0.156**，边界梯度 60.9
        腐蚀 9px  → 同上 **0.115**（−26%），边界梯度 **61.9**（边界更压在真实轮廓上）
      并且**关键点覆盖率 p50 始终 1.000**。
    * ⚠️ 但**少数帧腐蚀到 6~9 会开始丢点**（如 `f0031_..._s13` 覆盖 80%→60%）
      ⇒ **绝不能一刀切**，必须**逐级试、以"不丢点"为上限**（削掉人比多圈点背景更糟）。

    关键点判定带 **3×3 邻域容差**：点正好落在边界上不该算丢。
    """
    import cv2
    import numpy as np
    kp = [(float(k["x"]), float(k["y"])) for k in (kpts or []) if int(k.get("vis", 0)) >= 1]
    cur = (m > 0).astype(np.uint8)
    best = cur
    for k in steps:
        e = cv2.erode(cur, np.ones((k, k), np.uint8))
        if int(e.sum()) < 50:
            break
        if kp:
            hit = 0
            for x, y in kp:
                xi, yi = int(min(max(x, 0.0), 1.0) * w), int(min(max(y, 0.0), 1.0) * h)
                x0, x1 = max(0, xi - 1), min(w, xi + 2)
                y0, y1 = max(0, yi - 1), min(h, yi + 2)
                if e[y0:y1, x0:x1].any():
                    hit += 1
            if hit < len(kp):          # 这一级会丢点 ⇒ 停在上一步
                break
        best = e
    return best > 0


def _detect_seg(img_path: Path, kpts=None):
    """分割检测 (仅 person 类, 最大掩码): 返回 (bbox, polygon) 或 None."""
    r = _get_model().predict(str(img_path), conf=_MIN_CONF, verbose=False, device="cpu")[0]
    boxes = r.boxes
    masks = r.masks
    if masks is None or boxes is None or len(boxes) == 0:
        return None
    import numpy as np
    best = None
    best_area = 0.0
    for i, b in enumerate(boxes):
        if float(b.conf) < _MIN_CONF:
            continue
        if int(b.cls) != 0:          # 只要 person, 防窗户/杂物大掩码抢框
            continue
        m = masks.data[i].cpu().numpy()
        m = (m > 0.5)
        area = float(m.sum())
        if area > best_area:
            best_area = area
            best = (b, m)
    if best is None:
        return None
    b, m = best
    import cv2
    im = cv2.imread(str(img_path))
    if im is None:
        return None
    h, w = im.shape[:2]
    if m.shape[:2] != (h, w):
        import cv2 as _cv2
        m = _cv2.resize(m.astype(np.uint8), (w, h), interpolation=_cv2.INTER_NEAREST) > 0
    # ★ 收紧边界（怪怪「不够贴合，还是选到了很多背景部分」）：
    #   掩码来自低分辨率原型上采样 ⇒ 轮廓量化在 ~8px 网格上、整体偏松。
    #   逐级腐蚀到"再腐蚀就要丢关键点"为止（见 `_tighten_mask` 的实测数据）。
    if kpts:
        try:
            m = _tighten_mask(m, kpts, w, h)
        except Exception:                                        # noqa: BLE001
            pass
    r1 = _mask_to_bbox(m, w, h)
    if r1 is None:
        return None
    bbox, (ax1, ay1, ax2, ay2) = r1
    poly = _mask_polygon(m, w, h)
    return bbox, poly


def auto_bbox(img_path: Path) -> list | None:
    """最后兜底: yolo11n 检测最大人框 (person 过滤); 仅在 pose/分割/关键点全部失败时用.
    FALL_LABEL_DEVICE=dml 时走 onnx + DirectML (AMD 核显), 失败回退 ultralytics CPU."""
    try:
        if _POSE_DEVICE == "dml":
            boxes = _det_person_dml(img_path)
            if boxes is not None and boxes:
                best = max(boxes, key=lambda c: (c[1][2] - c[1][0]) * (c[1][3] - c[1][1]))
                x1, y1, x2, y2 = best[1]
                import cv2
                im = cv2.imread(str(img_path))
                if im is None:
                    return None
                h, w = im.shape[:2]
                x1 = max(0.0, x1); y1 = max(0.0, y1)
                x2 = min(float(w), x2); y2 = min(float(h), y2)
                if (x2 - x1) < _MIN_BOX or (y2 - y1) < _MIN_BOX:
                    return None
                return [round((x1 + x2) / 2 / w, 5), round((y1 + y2) / 2 / h, 5),
                        round((x2 - x1) / w, 5), round((y2 - y1) / h, 5)]
        r = _get_fb().predict(str(img_path), conf=_MIN_CONF, verbose=False,
                              device="cpu", imgsz=1280)[0]
        best = None
        best_area = 0.0
        for b in r.boxes:
            if int(b.cls) != 0:
                continue
            x1, y1, x2, y2 = [float(v) for v in b.xyxy[0].tolist()]
            if (x2 - x1) < _MIN_BOX or (y2 - y1) < _MIN_BOX:
                continue
            area = (x2 - x1) * (y2 - y1)
            if area > best_area:
                best_area = area
                best = (x1, y1, x2, y2)
        if best is None:
            return None
        import cv2
        im = cv2.imread(str(img_path))
        if im is None:
            return None
        h, w = im.shape[:2]
        x1, y1, x2, y2 = best
        x1 = max(0.0, x1); y1 = max(0.0, y1)
        x2 = min(float(w), x2); y2 = min(float(h), y2)
        if (x2 - x1) < _MIN_BOX or (y2 - y1) < _MIN_BOX:
            return None
        return [round((x1 + x2) / 2 / w, 5), round((y1 + y2) / 2 / h, 5),
                round((x2 - x1) / w, 5), round((y2 - y1) / h, 5)]
    except Exception:
        return None


def label_frame(img_path: Path, cls: int = 1, use_sam2: bool = True,
                write: bool = True) -> dict | None:
    """检测并写入:
    - labels/<name>.txt      (bbox, yolo detect)
    - labels/<name>_seg.txt  (贴合人物边缘的多边形, 与人物框一致的掩码)
    - labels/<name>_kpt.txt  (yolo-pose 行: cls cx cy w h + 14×[x y v])
    返回 {"bbox","poly","kpts","person_vis","black"} — 黑帧/废帧 bbox=None, black=True.

    write=False: 只算不落盘（GUI 手动标注「本地重标」按钮用，结果由编辑器保存流程统一回写）。
    """
    import cv2
    try:
        im = cv2.imread(str(img_path))
    except Exception:
        im = None
    if im is None:
        return {"bbox": None, "poly": None, "kpts": None, "person_vis": 0, "black": False}
    h, w = im.shape[:2]
    if float(im.mean()) < _BLACK_MEAN:
        return {"bbox": None, "poly": None, "kpts": None, "person_vis": 0, "black": True}

    r = _pose_predict(img_path)
    row = None
    bbox = None
    if r is not None:
        bbox, row = _best_pose_person(r, w, h)
        if bbox is None:
            # 升降档重试: 部分贴边人物在低一档分辨率才过阈值 (真框/幻影由 conf>=0.25 把关)
            r2 = _pose_predict(img_path, imgsz=_POSE_IMGSZ_B)
            if r2 is not None:
                b2, row2 = _best_pose_person(r2, w, h)
                if b2 is not None:
                    r, bbox, row = r2, b2, row2
    kpts, pv = (_kpts_from_pose(r, w, h, row) if (r is not None and bbox is not None)
                else (_blank_kpts(), 0))
    pose_ext = _ext_of(bbox)     # 骨架所属的原始 pose 框 (末尾判骨架是否可信要用)

    # 像素级多边形: yolo11s-seg 人形掩码 (且与人物框指向同一目标才采用)
    poly = None
    seg = None
    try:
        seg = _detect_seg(img_path, kpts)      # 传 kpts: 掩码收紧要以"不丢关键点"为上限
    except Exception:
        seg = None
    seg_ext = _ext_of(seg[0]) if seg else None   # yolo-seg 自己给的框 (掩码是否可信要用)
    if bbox is None and seg is not None:
        bbox, poly = seg
    elif bbox is not None and seg is not None:
        b2, p2 = seg
        if b2 and p2 and _boxes_agree(bbox, b2, w, h):
            poly = p2
    # 最后兜底: yolo11n 检测 (person)
    if bbox is None:
        try:
            bbox = auto_bbox(img_path)
        except Exception:
            bbox = None
    if bbox is None:
        return {"bbox": None, "poly": None, "kpts": None, "person_vis": 0, "black": False}
    # 幻影救援: pose 选中的框比 yolo11n 明显小得多且不同目标 (管道/角落幻影), 改用人框
    rescued = False
    try:
        ab = auto_bbox(img_path)
        if (ab and not _boxes_agree(bbox, ab, w, h, 0.2)
                and bbox[2] * bbox[3] < 0.6 * ab[2] * ab[3]):
            bbox = ab
            rescued = True
    except Exception:
        pass
    if rescued:
        # 骨架属于刚被否掉的幻影 —— 必须一起丢, 否则它会继续经"并集"与"夹紧"
        # 把框拖回幻影 (2026-09-12: f0016_face_down ..._s14 框最终落在右上角
        # 管道上就是这个原因)。pose_ext 也一并清掉, 免得末尾拿幻影框当参照。
        kpts, pv = _blank_kpts(), 0
        pose_ext = None
    # 低信服兜底: 最终框极小 (检测器全是幻影/碎片, 未检出真人) → 不标注 (宁缺毋滥)
    if bbox[2] * bbox[3] < 0.015:
        return {"bbox": None, "poly": None, "kpts": None, "person_vis": 0, "black": False}

    # SAM2 框提示精修: 提示框以"人体证据"为界 — 分割掩码范围 ∪ 关键点 + 12% 边距
    # (pose 框常偏向一侧且易含背景, 直接外扩会把背景带进掩码 → 多边形抢背景)
    sam2_used = False
    tightened = False
    if use_sam2 and _sam2_gate(poly, kpts):
        try:
            prompt_bbox, tightened = _prompt_bbox(bbox, kpts, w, h)
            if prompt_bbox is None or not tightened:
                if poly and len(poly) >= 6:
                    px1, py1, px2, py2 = _poly_extent(poly)
                    base = [(px1 + px2) / 2, (py1 + py2) / 2, px2 - px1, py2 - py1]
                else:
                    base = list(bbox)
                prompt_bbox = _expand_bbox_to_person(base, kpts, poly, w, h, pad=0.12)
            m = _sam2_person_mask(img_path, prompt_bbox)
            if m is not None:
                p3 = _mask_polygon(m, int(m.shape[1]), int(m.shape[0]))
                # ★★ 围栏（2026-09-30 怪怪「多边形还是取到了背景」）：
                #   SAM2 只许在**基线多边形 ±1.5% 画幅**内精修边界，不许扩张到几十上百像素外的
                #   新区域。实测 SAM2 把灰(背景)占比从 15~36% 抬到 40~87%，基线本身是干净的。
                p3 = _restrict_to_base(p3, poly, w, h)
                # 无基线 ⇒ 没有围栏可依。此时只能用"背景色占比"兜底：
                # 实测 `f0038_drag_guard_head_curl_s11` 走的就是这条路，SAM2 补出来的多边形
                # **87% 是背景**（那片墙）—— 比"没有多边形"更糟。>50% 就拒用。
                if p3 is not None and not (poly and len(poly) >= 6):
                    try:
                        _g = _bg_fraction(im, p3)
                        if _g is not None and _g > 0.5:
                            _LAST_DBG["sam2_rej_bg"] = round(_g, 3)
                            p3 = None
                    except Exception:                            # noqa: BLE001
                        pass
                if p3 and len(p3) >= 6 and _kpts_covered(p3, kpts) \
                        and _poly_covers(p3, poly):
                    # 面积守卫: SAM2 掩码比人体证据大太多 → 大面积背景抢框, 拒用
                    if poly and len(poly) >= 6:
                        a_old = max(_poly_area(poly), 1e-6)
                        # ★ 2026-09-30: 2.5 → 1.5。实测(12 帧抽样, runs/_probe_sam2_blame.py):
                        #   SAM2 的膨胀**通常只有 1.00~1.08×**(7/12 帧根本不用 SAM2);
                        #   但**离群帧能到 1.66×** —— 出图看过, 那种帧的多边形直接啃进墙面/空地
                        #   (`f0000_drag_brace_hands_none_s12`: 人在右下角, 品红铺满左边一片墙)。
                        #   ⇒ 1.5 卡掉离群值, 又**不碰常态精修**(1.08 远低于 1.5)。
                        #   ⚠️ 不要收到 1.35 —— 那已经在离群与常态之间, 会误伤正常帧。
                        if _poly_area(p3) > max(1.5 * a_old, 0.30):
                            p3 = None
                    if p3:
                        poly = p3
                        sam2_used = True
        except Exception:
            pass

    # ── 头部并入多边形 (2026-09-30 怪怪报「有些人头没标上」) ──────────────────
    # 根因: yolo11s-seg 的 person 掩码**不把安全帽当人体**; 实测头盔落在**框外** p50=0.000
    #   (框是好的) 而落在**多边形外** p50=0.383 / 21% 的帧 >90% ⇒ 只有多边形漏头。
    # 修法: 用 pose 头部关键点估一个"头"的圆并入多边形(尺度取框高, **不用肩宽** ——
    #   同日实测趴地帧肩线被透视压扁, 肩宽不可当分母)。
    # ⚠️ 必须放在**框规则之前** —— 框的"地板"要含住多边形, 这一步挪到后面框就跟不上。
    try:
        # ★★ 两个圆心来源**并列**，不是"一个兜底另一个"（2026-09-30 出图钉死）：
        #   pose 头点/neck↔forehead 是**估计** —— 实测 `f0048_step_back_guard_head_curl_s14`
        #     里 `fore`/`neck` 落在**头盔右侧边缘**，以它为中心画的圆盖到躯干上，头盔照样露着；
        #   安全帽色块是**直接测量** —— 盖在哪儿就是哪儿。
        #   ⇒ 谁有就并谁；两个都有就都并（多并一块不会错，只会更贴）。
        _discs = []
        try:
            _hd = _head_disc(r, w, h, row, kpts, bbox, im=im)
            if _hd is not None:
                _discs.append(_hd)
        except Exception:
            pass
        try:
            _hel = _helmet_disc(im, bbox)
            if _hel is not None:
                _discs.append(_hel)
        except Exception:
            pass
        for _d in (_discs if _HEAD_DISC_ON else []):
            _u = _union_disc_into_poly(poly, _d, w, h)
            if _u and len(_u) >= 6:
                poly = _u
                _LAST_DBG["head_disc"] = _d
    except Exception:
        _LAST_DBG["head_disc"] = None

    # 完全框选 / 背景收缩: 小人物(骨架盖全身) → 框底=像素级掩码范围 (背景不抢框);
    # 其余情况: 检测框 ∪ 掩码 ∪ 关键点 并集 (半身骨架只盖住部分人时不能收)
    if tightened and poly and len(poly) >= 6:
        # 收框: 掩码范围按关键点范围裁剪(防掩码带背景), 再与关键点并集+边距
        px1, py1, px2, py2 = _clip_extent_to_kpts(_poly_extent(poly), kpts)
        base = [(px1 + px2) / 2, (py1 + py2) / 2, px2 - px1, py2 - py1]
        try:
            bbox = _expand_bbox_to_person(base, kpts, None, w, h)
        except Exception:
            pass
    else:
        try:
            bbox = _expand_bbox_to_person(bbox, kpts, poly, w, h)
        except Exception:
            pass

    # 最终防抢框 (2026-09-12 重定判据): 夹紧基准必须是**真能代表人物**的那份证据。
    # 旧版无条件用关键点外接框当基准, 但骨架在横躺/贴地/运动模糊帧上常只落在腰胯
    # 一小块 (f0030: pose 框 x578~1276 而骨架只在 x716~904) ⇒ 一把夹紧把框切成碎片,
    # 人头/半身落到框外; f0042 连头盔顶都被切掉。判据:
    #   ① 骨架面积 >= 0.6 × pose 框面积 且 <= 2.0 × (上界防"关键点飞到天花板") ⇒ 用骨架
    #   ② 否则: 掩码外溢检测框 <=25% (说明不是墙/窗掩码) ⇒ 用掩码范围 (像素级, 更硬)
    #   ③ 两者都不成立 ⇒ 不夹 (宁松勿切: 框大是噪声, 框切人是错误标签)
    _ke = _kpt_extent(kpts)
    _trust, _src, _ae, _mask_ok = _clamp_basis(kpts, poly, pose_ext, seg_ext)
    _pe = _poly_extent(poly) if (poly and len(poly) >= 6) else None
    try:
        if _trust is not None:
            bbox = _clamp_box_to_ext(bbox, _trust)
            if poly:
                poly = _clip_poly_to_ext(poly, _trust)
        # 「大框框人」地板 (2026-09-12): 上一步只管"别圈背景"(往里夹), 缺了下界就会被
        # 夹成"贴着人物轮廓"——而本项目约定框是**大框**、多边形才是紧贴边缘的那个。
        # 实测只做上界: 846 帧里 458 帧框到轮廓的边距 <8px、97 帧为 0。
        _cover = []
        if _pe is not None:
            bbox = _cover_box_to_ext(bbox, _pe)          # 必含住人物轮廓
            _cover.append(_pe)
        # 关键点范围也一起含 —— 目检(2026-09-12)发现 **seg 多边形不含头盔**
        # (yolo11s-seg 把头盔当非人体, 只覆盖工装), 而 `forehead` 关键点在头盔里,
        # 所以只含多边形的地板对"切头盔"**结构上看不见**。
        # 放开成无条件含是**有据**的: 实测 `_ke`/`_pe` 面积比 中位 0.33 / P90 0.71 /
        # max 0.74 (40 帧抽样), 即骨架外接框从不比轮廓外接框大 ⇒ 含它等于给
        # 头顶/脚尖补余量, 不会把框撑爆。护栏: 面积比 >3 视为骨架飘(避坑 111), 不含。
        if _ke is not None:
            _kea = max(0.0, _ke[2] - _ke[0]) * max(0.0, _ke[3] - _ke[1])
            _pea = (max(0.0, _pe[2] - _pe[0]) * max(0.0, _pe[3] - _pe[1])
                    if _pe is not None else 0.0)
            if _pea <= 0.0 or _kea <= 3.0 * _pea:
                bbox = _cover_box_to_ext(bbox, _ke, 0.04)   # 关节一起含
                _cover.append(_ke)
        _LAST_DBG["cover"] = _cover
    except Exception:
        pass
    # 收口: 地板只往外长, 贴边人物会被顶出 [0,1]; 而 cx/cy/w/h 各自 round 5 位本身
    # 也能把边界推出去 (v0.28.2 残留 132 帧框底 1.00005)。统一在这里打包。
    try:
        _packed = _pack_box01(bbox)
        if _packed is not None:
            bbox = _packed
    except Exception:
        pass
    # 幻影判据 (2026-09-30 加): pose 与 seg 是否**指着不同地方**。
    # 只记录进 _LAST_DBG 供诊断读, **不改标注行为** —— 改行为会动生产输出, 另行拍板。
    # 事故取证: f0032_trip_guard_head_curl_s10 的 pose_ext 在画面上部(幻影管),
    # seg_ext 在画面下部(真人), 中心距 0.47; 「大框框人」判据因 poly/kpts 跟幻影
    # 一起飞而判绿 ⇒ 必须靠这条独立判据才能看见。
    _LAST_DBG["pose_ext"] = pose_ext
    _LAST_DBG["seg_ext"] = seg_ext
    _LAST_DBG["kpt_ext"] = _ke
    _LAST_DBG["mask_ext"] = _pe
    _LAST_DBG["span"] = _ae
    _LAST_DBG["mask_ok"] = bool(_mask_ok)
    _LAST_DBG["trust"] = _trust
    _LAST_DBG["src"] = _src
    _LAST_DBG["rescued"] = bool(rescued)
    try:
        _LAST_DBG["phantom_d"] = _ext_center_dist(pose_ext, seg_ext)
        _LAST_DBG["phantom"] = _is_phantom(pose_ext, seg_ext)
    except Exception:
        _LAST_DBG["phantom_d"] = None
        _LAST_DBG["phantom"] = False

    pv = _person_vis(kpts, bbox)
    if write:
        try:
            LABELS_DIR.mkdir(parents=True, exist_ok=True)
            (LABELS_DIR / f"{img_path.stem}.txt").write_text(
                f"{cls} {bbox[0]} {bbox[1]} {bbox[2]} {bbox[3]}\n", encoding="utf-8")
            if poly:
                (LABELS_DIR / f"{img_path.stem}_seg.txt").write_text(
                    f"{cls} " + " ".join(str(v) for v in poly) + "\n", encoding="utf-8")
            # yolo-pose 完整行 (bbox + 14 点三态), 可直接喂 pose 训练
            vals = ([f"{cls}"] + [str(v) for v in bbox] +
                    [f"{p['x']} {p['y']} {p['vis']}" for p in (kpts or [])])
            (LABELS_DIR / f"{img_path.stem}_kpt.txt").write_text(
                " ".join(vals) + "\n", encoding="utf-8")
        except Exception:
            pass
    return {"bbox": bbox, "poly": poly, "kpts": kpts, "person_vis": pv,
            "black": False, "sam2": sam2_used}


def label_meta_items(items: list[dict], cls: int = 1) -> int:
    """为 meta 每个帧做检测: 写 label + 回填 bbox; 返回本次新标注帧数."""
    n = 0
    for it in items:
        for f in it.get("frames", []):
            if f.get("bbox"):
                continue
            p = Path(str(f.get("path") or ""))
            if not p.exists():
                continue
            bbox = label_frame(p, cls=cls)
            if bbox is not None:
                f["bbox"] = bbox.get("bbox")
                f["kpts"] = bbox.get("kpts")
                f["person_vis"] = bbox.get("person_vis")
                f["poly"] = bbox.get("poly")
                if bbox.get("bbox"):
                    n += 1
    return n


if __name__ == "__main__":
    import json
    import sys
    meta = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "runs" / "outputs" / "fall_400" / "meta.json")
    items = json.loads(Path(meta).read_text(encoding="utf-8"))
    n = label_meta_items(items)
    Path(meta).write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已自动标注 {n} 帧 → {LABELS_DIR}", flush=True)
