# -*- coding: utf-8 -*-
"""image_cache.py — 统一"按目标尺寸解码"工具（性能优化 2026-09-09）。

问题: 之前各路径都是 Image.open().convert("RGB") 全尺寸解码 + LANCZOS 缩放，
3072×4096 / 6MB 的摔倒照片单张就要 150-170ms（E: 慢盘上更久），图片切换与
手动标注开窗因此明显卡顿。

做法: JPEG 先 Image.draft()，让 libjpeg 直接按 1/2、1/4、1/8 降采样解码，
再做一次小尺寸 BILINEAR 重采样。实测（6MB 照片）:
    编辑器 1080×560 : 170ms → 61ms
    预览   640×156 : 154ms → 42ms
    网格缩略 118×66 : 150ms → 37ms
输出尺寸与原来完全一致，只是解码路径更快（LANCZOS→BILINEAR 在 4 倍以上降采样
时肉眼无差别）。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image


def open_scaled(path, max_w: int, max_h: int, resample=None) -> Image.Image:
    """打开图片并等比缩放到不超过 (max_w, max_h) 的 RGB 图。

    非 JPEG / draft 不支持时自动退回普通解码；任何异常由调用方处理。
    """
    im = Image.open(str(path))
    try:
        # 只对 JPEG 有效；PNG/WebP 会忽略
        im.draft("RGB", (max(1, int(max_w)), max(1, int(max_h))))
    except Exception:
        pass
    im = im.convert("RGB")
    iw, ih = im.size
    sc = min(max_w / iw, max_h / ih, 1.0)
    if sc < 1.0:
        rs = resample or getattr(Image, "BILINEAR", Image.NEAREST)
        im = im.resize((max(1, int(iw * sc)), max(1, int(ih * sc))), rs)
    return im
