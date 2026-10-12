# -*- coding: utf-8 -*-
"""relabel.py — 单帧本地重标注（原先住在 `core/h3_factory.py` 里）。

## 为什么单独成文件

它原本是 `core/h3_factory.py`（H3 视频生成模块）的一个函数，但它**跟 H3 无关**——
它是 YOLO 工作台「标注器」的功能（`gui/annotation_editor.py` 的「重标注」按钮）。

H3 模块整体剥离出本仓之后，这个函数必须留下，所以在这里落地。

## 语义（照抄原实现，不改行为）

用户约定：**标注只能走本地路线，零 API 调用。**

引擎链 = yolo11n-pose（人框 + 14 关键点）→ yolo11s-seg（像素掩码）
          → SAM2.1 框提示精修 → 关键点夹紧防抢框

**只计算、不落盘**（由调用方保存流程统一回写）。
首次调用会加载模型（约 10–30s），建议在后台线程里调。
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path

# 自动标注脚本的落点。原在 `tools/h3/`，2026-10-01 随 H3 模块剥离迁到 `tools/label/`。
# 层级与原来一致（tools/<目录>/x.py），所以脚本内部的 `ROOT = parent.parent.parent` 不受影响。
_LABEL_DIR = Path(__file__).resolve().parent.parent.parent / "tools" / "label"


def relabel_frame(path, cls: int = 1) -> dict:
    """本地重标注单帧（用户约定：标注只能走本地路线，零 API 调用）。

    引擎 = yolo11n-pose（人框+14 关键点）→ yolo11s-seg（像素掩码）→ SAM2.1 框提示精修
    → 关键点夹紧防抢框；只计算不落盘（由调用方保存流程统一回写）。
    首次调用会加载模型（约 10-30s），建议在后台线程里调。
    """
    try:
        d = str(_LABEL_DIR)
        if d not in _sys.path:
            _sys.path.insert(0, d)
        import auto_label_fall as alf
        res = alf.label_frame(Path(str(path)), cls=int(cls), write=False) or {}
        return {"ok": bool(res.get("bbox")), "bbox": res.get("bbox"), "poly": res.get("poly"),
                "kpts": res.get("kpts"), "person_vis": res.get("person_vis"),
                "engine": "local(pose+seg+SAM2)"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
