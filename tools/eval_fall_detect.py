#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""eval_fall_detect.py — 摔倒检测权重的**检测效果评估**。

## 为什么单独写

整仓此前没有任何算 mAP / P / R 的地方 —— 训练跑完只知道"训完了"，不知道
"到底准不准"。这是链路里最后一段空白：`train_*` 目录里的 `results.csv`
只有训练期的 val 曲线，不给你 per-class 指标、不给漏检/误检计数、不给可目检的图。

## 给出什么

1. **标准指标**（`model.val`，conf=0.001）：mAP50 / mAP50-95 / P / R，总体 + 逐类。
2. **部署工作点**（conf=0.25，IoU 0.5 贪心匹配）：TP / FP / FN / 逐类漏检误检计数。
   —— mAP 是排序质量，不回答"上线跑起来会漏几个"；这一节才回答。
3. **可目检的对照图**：真值框（细绿 `(0,220,0)`）叠预测框（粗：person 浅蓝 `(255,128,0)` /
   fall 红 `(0,0,255)`，均为 OpenCV BGR）拼成大图，数字之外还得人眼看。
   ⚠ **画序不能反**：真值必须**后**画（在预测之上），否则粗预测框会盖掉细真值框，
   图会静默失去区分能力 —— 详见 §V.15。图自带 `grid_check` 自检（每格两类框都得看得见）。
4. **负对照**（`--negctl`）：拿**没训过的** yolo11n.pt 在同一个测试集上跑一遍，
   证明"指标不是管线随便给的"。**判据按类别分开，不看总体 mAP50**：
   * 基座词表里**没有**的类（本数据集 `fall`，基座同索引通道叫 `bicycle`）
     ⇒ 绝对值必须 < 0.05。这是"管线没瞎给分"的硬证据。
   * 基座词表里**有同名**的类（`person`，COCO 里本来就有）
     ⇒ 用相对判据：训过的至少是基座的 2 倍。
   **为什么不能要求总体近 0**：COCO 预训练的 yolo11n 词表里本来就有 `person`，
   而本数据集 class 0 恰好同名 ⇒ 它天然拿到非零分（实测 mAP50 = 0.437），
   这是迁移能力，不是管线自欺。用"总体 < 0.05"当判据会**必然误报**。
   负对照不通过时**退出码 6**，本轮指标一律不得采信。

## 用法

    $PY tools/eval_fall_detect.py                     # 读 runs/outputs/fall_synth_train.json 找权重
    $PY tools/eval_fall_detect.py --weights <xxx.pt>
    $PY tools/eval_fall_detect.py --negctl            # 附带负对照

## 退出码

    0 = 正常；6 = 负对照未过（指标不可信，先去查评估管线）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _toolkit import ROOT, Tee, mem_gate, resolve_weights     # noqa: E402

INDEX = ROOT / "runs" / "outputs" / "fall_synth_train.json"

CLASSES = ["person", "fall"]


# ---------------------------------------------------------------- 指标

def iou(a, b) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def load_gt(label_path: Path, w: int, h: int):
    """读 YOLO 标签 → [(cls, (x1,y1,x2,y2))]，与 synth 侧 parse_yolo 同口径。"""
    out = []
    if not label_path.is_file():
        return out
    for line in label_path.read_text(encoding="utf-8").splitlines():
        p = line.split()
        if len(p) < 5:
            continue
        c = int(p[0])
        cx, cy, bw, bh = (float(v) for v in p[1:5])
        out.append((c, ((cx - bw / 2.0) * w + 0.5, (cy - bh / 2.0) * h + 0.5,
                        (cx + bw / 2.0) * w - 0.5, (cy + bh / 2.0) * h - 0.5)))
    return out


def match_image(gt, pred, iou_thr=0.5):
    """贪心匹配：按 IoU 从高到低配对，一支 GT 只配一个预测。返回 (tp, fp, fn, per_class)。"""
    per = {i: {"tp": 0, "fp": 0, "fn": 0} for i in range(len(CLASSES))}
    pairs = []
    for gi, (gc, gb) in enumerate(gt):
        for pi, (pc, pb, pconf) in enumerate(pred):
            if pc != gc:
                continue
            v = iou(gb, pb)
            if v >= iou_thr:
                pairs.append((v, gi, pi))
    pairs.sort(reverse=True)
    used_g, used_p = set(), set()
    tp = 0
    for _v, gi, pi in pairs:
        if gi in used_g or pi in used_p:
            continue
        used_g.add(gi)
        used_p.add(pi)
        tp += 1
        per[gt[gi][0]]["tp"] += 1
    for gi, (gc, _gb) in enumerate(gt):
        if gi not in used_g:
            per[gc]["fn"] += 1
    for pi, (pc, _pb, _c) in enumerate(pred):
        if pi not in used_p:
            per[pc]["fp"] += 1
    return tp, len(pred) - tp, len(gt) - tp, per


# ---------------------------------------------------------------- 目检图

def draw_grid(rows, out_png: Path, tile=(480, 288), cols=3):
    """真值框叠预测框拼成大图。**顺序不能反，理由见下。**

    ⚠ 2026-09-21 实测踩到：原先"真值细绿(thickness=1)先画、预测粗框(thickness=2)后画"，
    而模型准到 FN 只有 3/273 ⇒ 粗框几乎完整盖住细绿框，**12 格里 7 格的真值绿像素为 0**。
    于是"GT 与 Pred 完美重合"和"GT 根本没画"在图上**长得一模一样** ——
    这张图的区分能力被静默削弱了。⇒ **真值必须最后画（在预测之上）**。

    返回 dict：`shape` + `tiles_real` / `tiles_gt_visible` / `tiles_pred_visible`，
    供调用方做"图里到底有没有东西"的自检（本项目第二道独立检查的口径）。
    """
    import cv2
    import numpy as np
    tiles, n_real = [], 0
    for img_bgr, gt, pred in rows:
        vis = cv2.resize(img_bgr, tile, interpolation=cv2.INTER_AREA)
        sx, sy = tile[0] / img_bgr.shape[1], tile[1] / img_bgr.shape[0]

        def sc(b):
            return (int(b[0] * sx), int(b[1] * sy), int(b[2] * sx), int(b[3] * sy))

        for c, b, cf in pred:                              # 预测：粗（浅蓝=person / 红=fall）
            x1, y1, x2, y2 = sc(b)
            col = (0, 0, 255) if c == 1 else (255, 128, 0)
            cv2.rectangle(vis, (x1, y1), (x2, y2), col, 2)
            cv2.putText(vis, "%s %.2f" % (CLASSES[c], cf), (x1, max(11, y1 - 3)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1, cv2.LINE_AA)
        for _c, b in gt:                                   # 真值：细绿，**画在预测之上**
            x1, y1, x2, y2 = sc(b)
            cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 220, 0), 1)
        cv2.putText(vis, "GT %d / Pred %d" % (len(gt), len(pred)), (4, tile[1] - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)
        tiles.append((vis, len(gt) > 0, len(pred) > 0))
        n_real += 1
    while len(tiles) % cols:                               # 补空位（补的是纯黑，不参与自检）
        tiles.append((np.zeros((tile[1], tile[0], 3), np.uint8), None, None))

    def _has(t, bgr, tol=45):
        return int((np.abs(t.astype(np.int16) - np.array(bgr, np.int16)).max(axis=2) <= tol).sum()) > 0

    n_gt_vis = sum(1 for t, has_gt, _ in tiles[:n_real]
                   if has_gt and (_has(t, (0, 220, 0)) or _has(t, (0, 255, 0))))
    n_pd_vis = sum(1 for t, _, has_pd in tiles[:n_real]
                   if has_pd and (_has(t, (255, 128, 0)) or _has(t, (0, 0, 255))))

    imgs = [t for t, _, _ in tiles]
    rows_img = [np.hstack(imgs[i:i + cols]) for i in range(0, len(imgs), cols)]
    grid = np.vstack(rows_img)
    cv2.imwrite(str(out_png), grid)
    return {"shape": grid.shape, "tiles_real": n_real,
            "tiles_gt_visible": n_gt_vis, "tiles_pred_visible": n_pd_vis}


# ---------------------------------------------------------------- 主流程

def label_file_of(img_path: Path) -> Path:
    """由图片路径推出它的**标签文件**路径：最后一个 `images` 段换成 `labels`，后缀换 `.txt`。

    两个坑都踩过：
    ① 不要用 `str(p).replace("images","labels")` —— 数据集根目录名里只要出现
       "images" 就会被一起替换掉，静默指向错误目录；
    ② **别忘了换后缀**。只换目录不换后缀，`load_gt` 会因"文件不存在"返回空列表，
       于是所有预测都被判成误检 —— 表面看是一张像模像样的表（TP=0 / FP=N），
       唯一的破绽是 **FN=0**（真值一个都没读进来）。
    """
    parts = list(img_path.parts)
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] == "images":
            parts[i] = "labels"
            break
    return Path(*parts).with_suffix(".txt")


def count_gt(data_yaml: str, split: str, log=print):
    """直接数测试/验证集标签，返回 `{images, objects, per_class{name: n}}`。

    为什么不从 `model.val()` 的结果里拿：**ultralytics 8.4.87 的 `Metric` 没有 `nt`
    字段**（实测 `hasattr(box,'nt') == False`）。与其猜某个版本里的私有字段，
    不如自己数标签 —— 它是版本无关的，而且顺带构成一条**独立第二路径**：
    数和 `_qa.json` 对不上就说明标注本身有问题。
    """
    import yaml
    cfg = yaml.safe_load(Path(data_yaml).read_text(encoding="utf-8"))
    root = Path(str(cfg.get("path") or Path(data_yaml).parent))
    rel = cfg.get("val" if split == "valid" else split)
    idir = Path(str(rel)) if Path(str(rel)).is_absolute() else (root / str(rel))
    if not idir.is_dir():
        raise FileNotFoundError("图片目录不存在: %s" % idir)
    files = sorted(f for f in os.listdir(idir)
                   if os.path.splitext(f)[1].lower() in (".jpg", ".png"))
    per = {c: 0 for c in CLASSES}
    n_obj = 0
    missing = 0
    for fn in files:
        lp = label_file_of(idir / fn)
        if not lp.is_file():
            missing += 1
            continue
        for line in lp.read_text(encoding="utf-8").splitlines():
            p = line.split()
            if len(p) < 5:
                continue
            c = int(p[0])
            n_obj += 1
            if c < len(CLASSES):
                per[CLASSES[c]] += 1
    if missing:
        log("  [WARN] %d 张图没有对应标签文件 —— 这些图会被当成「无目标」，"
            "所有预测都算误检" % missing)
    return {"images": len(files), "objects": n_obj, "per_class": per, "missing_labels": missing}


def run_val(yolo_cls, weights: str, data_yaml: str, split: str, tag: str, out_root: Path,
            n_gt=None, expect_mismatch=False):
    """标准 mAP 评估。`yolo_cls` 是 **ultralytics 的 YOLO 类本身**（不是模块）。
    `n_gt` 是标签里数出来的实例数 —— 不取模型内部字段（8.4.87 没有 `nt`）。
    `expect_mismatch=True` 用于**负对照**：基座词表本来就与本数据集不同名，
    此时类别名不一致是**设计如此**，不是隐患 —— 记为说明项，不报 WARN。
    （必然误报的 WARN 会训练人忽略告警，见 docs/避坑清单 §V.7。）"""
    m = yolo_cls(weights)
    r = m.val(data=data_yaml, split=split, conf=0.001, iou=0.7, plots=True,
              project=str(out_root), name=tag, exist_ok=True, verbose=False,
              device=_DEVICE, workers=0)
    box = r.box
    res = {
        "weights": weights,
        "split": split,
        "names": list(getattr(r, "names", {}).values()) if isinstance(getattr(r, "names", None), dict) else None,
        "overall": {
            "P": round(float(box.mp), 4),
            "R": round(float(box.mr), 4),
            "mAP50": round(float(box.map50), 4),
            "mAP50-95": round(float(box.map), 4),
            "n_instances": n_gt,
        },
        "per_class": {},
        "save_dir": str(getattr(r, "save_dir", "")),
    }
    idx = list(box.ap_class_index) if box.ap_class_index is not None else []
    for j, ci in enumerate(idx):
        name = (res["names"] or CLASSES)[int(ci)] if res["names"] else CLASSES[int(ci)]
        res["per_class"][name] = {
            "P": round(float(box.p[j]), 4),
            "R": round(float(box.r[j]), 4),
            "mAP50": round(float(box.ap50[j]), 4),
            "mAP50-95": round(float(box.ap[j]), 4),
        }
    # 类别顺序闸门：names 对不上说明数据集/权重不同源，指标再好看也没意义
    if res["names"] and [str(x) for x in res["names"]] != CLASSES:
        msg = "权重里的类别名 %s != 脚本预期的 %s" % (res["names"], CLASSES)
        if expect_mismatch:
            res["names_mismatch_expected"] = msg
        else:
            res["WARN_names_mismatch"] = msg
    return res, m


def run_operating_point(model, data_yaml: str, split: str, conf: float, n_vis: int,
                        out_png: Path, log=print):
    """部署工作点：conf 阈值固定，逐图贪心匹配，统计 TP/FP/FN 并存目检图。"""
    import cv2
    import yaml
    cfg = yaml.safe_load(Path(data_yaml).read_text(encoding="utf-8"))
    root = Path(str(cfg.get("path") or Path(data_yaml).parent))
    idir = Path(str(cfg["val" if split == "valid" else split]))
    if not idir.is_absolute():
        idir = root / idir
    if not idir.is_dir():
        raise FileNotFoundError("图片目录不存在: %s" % idir)

    files = sorted([f for f in os.listdir(idir)
                    if os.path.splitext(f)[1].lower() in (".jpg", ".png")])
    if not files:
        raise FileNotFoundError("图片目录里没有图: %s" % idir)
    first_label = label_file_of(idir / files[0])
    if not first_label.is_file():
        raise FileNotFoundError("推出来的标签文件不存在: %s" % first_label)

    tot = {i: {"tp": 0, "fp": 0, "fn": 0} for i in range(len(CLASSES))}
    rows = []
    for fn in files:
        img = cv2.imread(str(idir / fn))
        if img is None:
            log("  [WARN] 读不了: %s" % (idir / fn))
            continue
        h, w = img.shape[:2]
        gt = load_gt(label_file_of(idir / fn), w, h)
        rr = model.predict(str(idir / fn), conf=conf, iou=0.7, verbose=False,
                           device=_DEVICE)[0]
        pred = []
        if rr.boxes is not None and len(rr.boxes):
            xyxy = rr.boxes.xyxy.cpu().numpy()
            cls = rr.boxes.cls.cpu().numpy().astype(int)
            cf = rr.boxes.conf.cpu().numpy()
            for b, c, k in zip(xyxy, cls, cf):
                pred.append((int(c), (float(b[0]), float(b[1]), float(b[2]), float(b[3])), float(k)))
        pred.sort(key=lambda t: -t[2])
        _tp, _fp, _fn, per = match_image(gt, pred)
        for i in per:
            for k in per[i]:
                tot[i][k] += per[i][k]
        if len(rows) < n_vis:
            rows.append((img, gt, pred))

    agg = {"conf": conf, "iou_match": 0.5, "n_images": len(files), "per_class": {}}
    TP = FP = FN = 0
    for i, name in enumerate(CLASSES):
        v = tot[i]
        TP += v["tp"]
        FP += v["fp"]
        FN += v["fn"]
        recall = v["tp"] / float(v["tp"] + v["fn"]) if (v["tp"] + v["fn"]) else None
        prec = v["tp"] / float(v["tp"] + v["fp"]) if (v["tp"] + v["fp"]) else None
        agg["per_class"][name] = {
            "tp": v["tp"], "fp": v["fp"], "fn": v["fn"],
            "precision": None if prec is None else round(prec, 4),
            "recall": None if recall is None else round(recall, 4),
        }
    agg["overall"] = {
        "tp": TP, "fp": FP, "fn": FN,
        "precision": round(TP / float(TP + FP), 4) if (TP + FP) else None,
        "recall": round(TP / float(TP + FN), 4) if (TP + FN) else None,
    }
    if rows:
        agg["grid"] = str(out_png)
        g = draw_grid(rows, out_png)
        agg["grid_shape"] = list(g["shape"])
        # 图的第二道自检：真值/预测**各自**都必须在每一格里看得见。
        # 少了这条，"GT 被粗预测框盖住"会伪装成"GT 与 Pred 完美重合"。
        agg["grid_check"] = {"tiles_real": g["tiles_real"],
                             "tiles_gt_visible": g["tiles_gt_visible"],
                             "tiles_pred_visible": g["tiles_pred_visible"]}
        if g["tiles_gt_visible"] != g["tiles_real"]:
            agg["WARN_grid_gt_hidden"] = (
                "目检图里有 %d/%d 格看不到真值框像素 —— 图失去区分能力，检查绘制顺序"
                % (g["tiles_real"] - g["tiles_gt_visible"], g["tiles_real"]))
    return agg


_DEVICE = "0"

# —— 负对照判据的两个阈值（都不是拍脑袋，见下方 docstring）——
NEG_ABS_MAX = 0.05     # 基座词表里**不存在**的类：绝对值必须近 0
NEG_RATIO_MIN = 2.0    # 基座词表里**有同名**的类：训过的至少是基座的 2 倍


def negctl_check(val: dict, nv: dict, log=print) -> tuple:
    """负对照判定 —— **按类别分别定判据**，不看总体 mAP50。

    为什么总体 mAP50 不能当判据（2026-09-21 实测）：
        COCO 预训练的 yolo11n **词表里本来就有 `person`**，而本数据集 class 0 恰好同名。
        于是它在本数据集上天然拿到非零分（实测 mAP50 = 0.437）—— 这是**迁移能力**，
        不是"管线在自欺"。用"总体 mAP50 < 0.05"当判据 ⇒ **必然误报**，
        而必然误报的判据等于没有判据。

    判据拆两条（都能抓住真故障）：
        A. 基座词表里**没有**的类（本数据集 `fall`，基座第 1 号通道叫 `bicycle`）
           ⇒ 绝对值必须 < NEG_ABS_MAX。
           **这一条才是"管线没在瞎给分"的硬证据**：若 GT 被读错、或报的其实是训练集，
           这一类会跟着高 —— 假阳性无处可藏。实测 0.015 ⇒ 通过。
        B. 基座词表里**有同名**的类（`person`）⇒ 改用**相对判据**：
           `训过的 mAP50 >= NEG_RATIO_MIN × 基座 mAP50`。
           实测 0.995 / 0.437 = 2.28 倍 ⇒ 通过。

    返回 `(ok, rows, notes)`；`rows` 每项为 `(类名, 基座名, 训过mAP50, 基座mAP50, 结论, 说明)`。
    """
    base_names = [str(x) for x in (nv.get("names") or [])]
    rows, notes, ok_all = [], [], True

    for i, cls in enumerate(CLASSES):
        ours = (val.get("per_class") or {}).get(cls, {}).get("mAP50")
        base_name = base_names[i] if i < len(base_names) else None
        b = (nv.get("per_class") or {}).get(base_name, {}).get("mAP50") if base_name else None

        if ours is None or b is None:
            ok_all = False
            rows.append((cls, base_name, ours, b, "FAIL",
                         "取不到指标（本类 %s / 基座 %s）—— 不能当通过"
                         % ("-" if ours is None else "%.3f" % ours,
                            "-" if b is None else "%.3f" % b)))
            continue

        if base_name == cls:
            ratio = (ours / b) if b > 1e-9 else float("inf")
            good = ratio >= NEG_RATIO_MIN
            rows.append((cls, base_name, ours, b, "PASS" if good else "FAIL",
                         "同名类 ⇒ 相对判据：%.3f/%.3f = %.2f 倍（要求 ≥ %.1f 倍）"
                         % (ours, b, ratio, NEG_RATIO_MIN)))
        else:
            good = b < NEG_ABS_MAX
            rows.append((cls, base_name, ours, b, "PASS" if good else "FAIL",
                         "基座无此类 ⇒ 绝对判据：基座 %.3f（要求 < %.2f）"
                         % (b, NEG_ABS_MAX)))

        if not good:
            ok_all = False

    notes.append("注：基座权重第 N 号通道与本数据集第 N 号类**按索引对齐**比较"
                 "（GT 标签用的是本数据集索引）。")
    if nv.get("names_mismatch_expected"):
        notes.append("类别名不一致为负对照设计使然，已按预期处理，不计 WARN。")
    return ok_all, rows, notes


def main(argv=None) -> int:
    global _DEVICE
    ap = argparse.ArgumentParser(description="摔倒检测权重效果评估")
    ap.add_argument("--weights", default=None, help="默认读 runs/outputs/fall_synth_train.json")
    ap.add_argument("--data", default=None)
    ap.add_argument("--split", default="test", choices=["train", "valid", "test"])
    ap.add_argument("--conf", type=float, default=0.25, help="部署工作点的置信度阈值")
    ap.add_argument("--n-vis", type=int, default=12, help="目检图张数")
    ap.add_argument("--device", default="0")
    ap.add_argument("--negctl", action="store_true",
                    help="附带负对照：未训练基座按类别分开判（词表外的类须近 0，同名类改相对判据）")
    ap.add_argument("--force-low-mem", action="store_true", help="跳过内存闸门")
    a = ap.parse_args(argv)
    _DEVICE = a.device

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = ROOT / "runs" / "logs" / ("eval_fall_detect_%s.log" % ts)
    ok, detail = mem_gate(what="摔倒检测效果评估")
    if not ok and not a.force_low_mem:
        print("[拒绝启动] %s" % detail)
        return 2
    tee = Tee(log_path)
    sys.stdout = tee
    sys.stderr = tee
    try:
        print("[内存] %s" % detail)
        if not ok:
            print("[内存] ⚠ 闸门未过但已用 --force-low-mem 强制继续，风险自负")
        return _run(a, ts, log_path)
    finally:
        sys.stdout = tee._out
        sys.stderr = tee._out
        tee.close()


def _run(a, ts, log_path) -> int:
    out_root = ROOT / "runs" / "outputs" / "fall_eval"
    out_root.mkdir(parents=True, exist_ok=True)

    weights, data_yaml = a.weights, a.data
    note = "来自 --weights"
    if not weights:
        weights, note = resolve_weights(None, [INDEX], log=print)
    if not data_yaml:
        for idx in (INDEX,):
            if Path(idx).is_file():
                try:
                    data_yaml = json.loads(Path(idx).read_text(encoding="utf-8")).get("data_yaml")
                except Exception:                                # noqa: BLE001
                    data_yaml = None
                if data_yaml and Path(data_yaml).is_file():
                    break
                data_yaml = None
    data_yaml = data_yaml or str(ROOT / "data" / "datasets" / "fall_synth_v1" / "data.yaml")
    if not weights or not Path(weights).is_file():
        print("[FAIL] 找不到可用权重（%s）" % note)
        return 2
    if not Path(data_yaml).is_file():
        print("[FAIL] data.yaml 不存在: %s" % data_yaml)
        return 2

    print("=" * 78)
    print("摔倒检测效果评估")
    print("=" * 78)
    print("  权重   : %s  (%.1f MB)" % (weights, os.path.getsize(weights) / 1048576))
    print("  来源   : %s" % note)
    print("  数据   : %s" % data_yaml)
    print("  切分   : %s   conf(工作点) %.2f   IoU 匹配 0.5" % (a.split, a.conf))
    print("  日志   : %s" % log_path)
    print("=" * 78)

    try:
        from ultralytics import YOLO
    except Exception as e:                                   # noqa: BLE001
        print("[FAIL] ultralytics 不可用: %r" % e)
        return 3

    print("\n[1/3] 标准指标 (model.val, conf=0.001)")
    # 先自己数一遍标签：既是实例数的来源，也是对标注的独立复核
    gt = count_gt(data_yaml, a.split, log=print)
    print("  GT 复核 : %d 图 / %d 框（person %d / fall %d），缺标签 %d 张"
          % (gt["images"], gt["objects"], gt["per_class"]["person"],
             gt["per_class"]["fall"], gt["missing_labels"]))
    qa = Path(data_yaml).parent / "_qa.json"
    if qa.is_file():
        try:
            q = json.loads(qa.read_text(encoding="utf-8"))
            key = "valid" if a.split == "valid" else a.split
            exp = (q.get("splits") or {}).get(key, {}).get("objects")
            if exp is not None and exp != gt["objects"]:
                print("  ⚠ GT 复核与 _qa.json 对不上：数出来 %d，_qa 记 %d —— 标注可能被改过"
                      % (gt["objects"], exp))
            elif exp is not None:
                print("  对账    : 与 _qa.json 一致（%d 框）✓" % exp)
        except Exception as e:                                   # noqa: BLE001
            print("  [WARN] _qa.json 读不了: %r" % e)

    val, model = run_val(YOLO, weights, data_yaml, a.split, "val_%s_%s" % (a.split, ts),
                         out_root, n_gt=gt["objects"])
    o = val["overall"]
    print("  %-8s %6s %6s %8s %10s" % ("class", "P", "R", "mAP50", "mAP50-95"))
    print("  %-8s %6.3f %6.3f %8.3f %10.3f" % ("all", o["P"], o["R"], o["mAP50"], o["mAP50-95"]))
    for name, v in val["per_class"].items():
        print("  %-8s %6.3f %6.3f %8.3f %10.3f" % (name, v["P"], v["R"], v["mAP50"], v["mAP50-95"]))
    print("  实例数 %d / 类别名 %s" % (o["n_instances"], val["names"]))
    if val.get("WARN_names_mismatch"):
        print("  ⚠ %s" % val["WARN_names_mismatch"])

    print("\n[2/3] 部署工作点 (conf=%.2f, IoU 0.5 贪心匹配)" % a.conf)
    grid_png = out_root / ("pred_vs_gt_%s_%s.png" % (a.split, ts))
    op = run_operating_point(model, data_yaml, a.split, a.conf, a.n_vis, grid_png)
    print("  %-8s %5s %5s %5s %9s %8s" % ("class", "TP", "FP", "FN", "精确率", "召回率"))
    for name, v in op["per_class"].items():
        print("  %-8s %5d %5d %5d %9s %8s"
              % (name, v["tp"], v["fp"], v["fn"],
                 "%.3f" % v["precision"] if v["precision"] is not None else "-",
                 "%.3f" % v["recall"] if v["recall"] is not None else "-"))
    ov = op["overall"]
    print("  %-8s %5d %5d %5d %9s %8s"
          % ("all", ov["tp"], ov["fp"], ov["fn"],
             "%.3f" % ov["precision"] if ov["precision"] is not None else "-",
             "%.3f" % ov["recall"] if ov["recall"] is not None else "-"))
    print("  目检图: %s  %s" % (grid_png, op.get("grid_shape")))
    gc = op.get("grid_check") or {}
    if gc:
        print("  图自检: 真值可见 %s/%s 格，预测可见 %s/%s 格"
              % (gc.get("tiles_gt_visible"), gc.get("tiles_real"),
                 gc.get("tiles_pred_visible"), gc.get("tiles_real")))
    if op.get("WARN_grid_gt_hidden"):
        print("  ⚠ %s" % op["WARN_grid_gt_hidden"])

    out = {"generated_at": ts, "weights": weights, "data_yaml": data_yaml,
           "split": a.split, "val": val, "operating_point": op, "log": str(log_path)}

    rc = 0
    if a.negctl:
        print("\n[3/3] 负对照：未训练基座 yolo11n.pt（判据按类别分开，不看总体 mAP50）")
        base = str(ROOT / "yolo11n.pt")
        if not Path(base).is_file():
            print("  [SKIP] 本地没有 %s —— 未验证 ≠ 通过" % base)
        else:
            nv, _ = run_val(YOLO, base, data_yaml, a.split, "negctl_%s" % ts, out_root,
                            n_gt=gt["objects"], expect_mismatch=True)
            out["negctl"] = nv
            print("  基座总体: P %.3f  R %.3f  mAP50 %.3f  mAP50-95 %.3f   (仅供参考，不作判据)"
                  % (nv["overall"]["P"], nv["overall"]["R"],
                     nv["overall"]["mAP50"], nv["overall"]["mAP50-95"]))
            nx_ok, rows, notes = negctl_check(val, nv)
            out["negctl"]["check"] = {"passed": nx_ok,
                                      "rows": [{"cls": r[0], "base_cls": r[1],
                                                "trained_mAP50": r[2], "base_mAP50": r[3],
                                                "verdict": r[4], "rule": r[5]} for r in rows],
                                      "notes": notes}
            print("  %-8s %-10s %10s %10s  %-5s %s"
                  % ("本数据集类", "基座通道名", "训过mAP50", "基座mAP50", "结论", "判据"))
            for cls, bn, o, b, vd, why in rows:
                print("  %-8s %-10s %10s %10s  %-5s %s"
                      % (cls, bn or "-", "-" if o is None else "%.3f" % o,
                         "-" if b is None else "%.3f" % b, vd, why))
            for n in notes:
                print("  " + n)
            if nx_ok:
                print("  判定: PASS —— 基座词表外的类近 0（管线未瞎给分），"
                      "同名类上训过的显著更优")
            else:
                print("  判定: ⚠ FAIL —— 负对照未过 ⇒ 本轮指标不可信，先查评估管线再采信结果")
                rc = 6

    out_json = out_root / ("eval_%s_%s.json" % (a.split, ts))
    out_json.write_text(json.dumps(out, ensure_ascii=False, indent=1),
                        encoding="utf-8", newline="\n")
    (ROOT / "runs" / "outputs" / "fall_synth_eval.json").write_text(
        json.dumps({"latest": str(out_json), "generated_at": ts,
                    "mAP50": val["overall"]["mAP50"], "mAP50_95": val["overall"]["mAP50-95"],
                    "weights": weights, "split": a.split},
                   ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print("\n  指标 JSON: %s" % out_json)
    print("=" * 78)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
