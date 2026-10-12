# -*- coding: utf-8 -*-
"""make_logo.py — 艺术字 Logo (YOLO工作台)

设计语言: 深蓝圆角底 + 白色艺术字 "YOLO" — 字母逐字倾倒 (0°→55°)呼应"摔倒",
第二个 O 以检测框(四角括号)呈现, 呼应目标检测; 下方细字"目标检测"。
输出: assets/app_icon.png (512), assets/app_icon.ico (多尺寸)
"""
from __future__ import annotations
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT_PNG = ROOT / "assets" / "app_icon.png"
OUT_ICO = ROOT / "assets" / "app_icon.ico"
SIZE = 512

TOP = np.array([30, 64, 175], dtype=np.float64)
BOT = np.array([26, 50, 140], dtype=np.float64)


def font(paths, size):
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def draw_letter(d, ch, cx, cy, tilt, f, fill, shadow=None):
    """以 (cx,cy) 为中心绘制单字符 (含倾斜与阴影), 返回近似宽高"""
    bbox = d.textbbox((0, 0), ch, font=f)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    img = Image.new("RGBA", (int(w + 40), int(h + 40)), (0, 0, 0, 0))
    dd = ImageDraw.Draw(img)
    dd.text((20 - bbox[0], 20 - bbox[1]), ch, font=f, fill=shadow or (0, 0, 0, 0))
    img = img.rotate(tilt, expand=True, resample=Image.BICUBIC)
    d._canvas.paste(img, (int(cx - img.width / 2), int(cy - img.height / 2)), img)


class _D:
    """薄封装: 让 draw_letter 能往画布贴图"""
    def __init__(self, canvas):
        self._canvas = canvas


def main():
    img = np.zeros((SIZE, SIZE, 3), dtype=np.uint8)
    t = np.linspace(0, 1, SIZE)[:, None]
    c = TOP[None, :] * (1 - t) + BOT[None, :] * t
    img[:, :, :] = np.repeat(c[:, None, :].astype(np.uint8), SIZE, axis=1)
    im = Image.fromarray(img, "RGB")
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, SIZE - 1, SIZE - 1],
                                           radius=int(SIZE * 0.21), fill=255)
    out = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    d = ImageDraw.Draw(out)
    d._canvas = out  # 供 draw_letter 使用

    W = (255, 255, 255)
    W2 = (225, 235, 255)
    f_big = font([r"C:\Windows\Fonts\impact.ttf", r"C:\Windows\Fonts\arialbd.ttf",
                  r"C:\Windows\Fonts\msyhbd.ttc"], 210)
    f_small = font([r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc"], 44)

    # "YOLO" 艺术字: 逐字倾倒 (0°, 12°, 26°, 52°) — 摔倒感
    letters = [("Y", 0), ("O", 12), ("L", 26), ("O", 52)]
    xs = [SIZE * 0.20, SIZE * 0.40, SIZE * 0.60, SIZE * 0.80]
    cy = SIZE * 0.46
    for (ch, tilt), x in zip(letters, xs):
        # 阴影 (深色下移) 增加艺术字厚度
        bbox = d.textbbox((0, 0), ch, font=f_big)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        letter_img = Image.new("RGBA", (int(w + 60), int(h + 60)), (0, 0, 0, 0))
        ld = ImageDraw.Draw(letter_img)
        ld.text((30 - bbox[0], 30 - bbox[1]), ch, font=f_big, fill=(20, 40, 100))
        letter_img = letter_img.rotate(tilt, expand=True, resample=Image.BICUBIC)
        out.paste(letter_img, (int(x - letter_img.width / 2),
                               int(cy + 8 - letter_img.height / 2)), letter_img)

    # 白色字层 (阴影上偏移 6px)
    for (ch, tilt), x in zip(letters, xs):
        bbox = d.textbbox((0, 0), ch, font=f_big)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        letter_img = Image.new("RGBA", (int(w + 60), int(h + 60)), (0, 0, 0, 0))
        ld = ImageDraw.Draw(letter_img)
        ld.text((30 - bbox[0], 30 - bbox[1]), ch, font=f_big, fill=W)
        letter_img = letter_img.rotate(tilt, expand=True, resample=Image.BICUBIC)
        out.paste(letter_img, (int(x - letter_img.width / 2),
                               int(cy - letter_img.height / 2)), letter_img)

    # 第二个 O 上叠加检测框四角 (白色细框)
    ox, oy = xs[3], cy + 16
    r = 58
    bw = 4
    cl = 40
    for x0, y0, dx, dy in ((ox - r, oy - r, 1, 1), (ox + r, oy - r, -1, 1),
                           (ox - r, oy + r, 1, -1), (ox + r, oy + r, -1, -1)):
        d.line([x0, y0, x0 + dx * cl, y0], fill=W, width=bw)
        d.line([x0, y0, x0, y0 + dy * cl], fill=W, width=bw)

    # 底部细字: 目标检测
    d.text((SIZE * 0.5, SIZE * 0.84), "目标检测", font=f_small, fill=W2, anchor="mm")

    OUT_PNG.parent.mkdir(exist_ok=True)
    out.save(OUT_PNG)
    out.save(OUT_ICO, format="ICO",
             sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    print(f"艺术字 Logo 已生成: {OUT_PNG} / {OUT_ICO}")


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    main()
