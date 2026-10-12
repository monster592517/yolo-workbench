# -*- coding: utf-8 -*-
"""临时验收: 重标后 batch_main + runs meta 统计."""
import json
from pathlib import Path
from collections import Counter

B = Path(r"<项目根>\01_yolo_lab\data\h3_factory\batches\batch_main\meta.json")
S = Path(r"<项目根>\01_yolo_lab\runs\outputs\fall_400\meta.json")
b = json.loads(B.read_text(encoding="utf-8"))

tot = no_bbox = no_kpts = usable_n = 0
kpt_len = Counter()
vis_c = Counter()
offscreen = []
bad_bbox = []
blacks = []
for vi, v in enumerate(b["videos"]):
    for f in v.get("frames", []):
        tot += 1
        bb = f.get("bbox")
        if not bb:
            no_bbox += 1
            if f.get("usable"):
                bad_bbox.append((v["video"], f["idx"], "usable but no box"))
        else:
            if not (0 <= bb[0] <= 1 and 0 <= bb[1] <= 1 and 0 < bb[2] <= 1 and 0 < bb[3] <= 1):
                bad_bbox.append((v["video"], f["idx"], "bad bbox"))
        if f.get("usable"):
            usable_n += 1
        k = f.get("kpts")
        if not k:
            no_kpts += 1
        else:
            kpt_len[len(k)] += 1
            names = set()
            for kp in k:
                if not isinstance(kp, dict):
                    bad_bbox.append((v["video"], f["idx"], f"kpt not dict {type(kp).__name__}"))
                    continue
                x, y, vv = float(kp["x"]), float(kp["y"]), int(kp.get("vis", 0))
                vis_c[vv] += 1
                names.add(kp.get("name"))
                if vv != 0 and not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                    offscreen.append((v["video"], f["idx"], kp.get("name"), x, y))
            if len(names) != 14:
                bad_bbox.append((v["video"], f["idx"], f"kpt names {len(names)}"))
        if not f.get("usable") and not f.get("bbox"):
            if f.get("person_vis", 1) != 0:
                bad_bbox.append((v["video"], f["idx"], "unusable but pv!=0"))
print(f"tot={tot} usable={usable_n} no_bbox={no_bbox} no_kpts={no_kpts}")
print("kpt_len:", dict(kpt_len))
print("vis:", dict(vis_c))
print("offscreen vis>0:", len(offscreen), offscreen[:8])
print("problems:", bad_bbox[:10], "... total", len(bad_bbox))

# runs meta 一致性
items = json.loads(S.read_text(encoding="utf-8"))
n17 = 0
for it in items:
    for f in it.get("frames", []):
        k = f.get("kpts")
        if k and not isinstance(k[0], dict):
            n17 += 1
print("runs meta 非dict kpts 帧数:", n17)
