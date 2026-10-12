# -*- coding: utf-8 -*-
"""convention.py — 标注口径的**判据容差单一真源**（零依赖，只用标准库）。

为什么单独成文件 (2026-09-12 踩出来的):
  同一个概念（点是否在框外 / 框是否越出 [0,1] / 框边是否贴着画面）曾在 **4 个脚本**
  里各写各的，实测出现 **1e-9 / 1e-6 / 1e-4** 三种阈值。于是 `_pack_box01` 那 1e-5 的
  内缩(=1280px 上的 **0.013px**)在有的脚本里被当成"越界"、在另一些里被忽略 ——
  同一份数据、两个工具报出不同结论，而**两边看起来都对**（典型静默故障）。

  ⇒ 判据一律走本模块的函数，不要在别处硬编码这类阈值字面量。
  ⇒ 零依赖是刻意的：自检工具（`studio_doctor`）只该为拿一个容差常量付出极小的导入代价，
    不该被迫引入 numpy/cv2。

口径 (2026-09-12 用户说明，本项目标注约定):
  框 `bbox` = **大框框人**（含住人物且留余量）；多边形 `poly` = **把人物边缘完全走一遍**；
  关键点 `kpts` = 标**关节**。
"""
from __future__ import annotations

CONV_TOL_PX = 0.5      # 有图像尺寸的调用方: 像素容差 (低于它一律视为数值尘埃, 非缺陷)
CONV_TOL_N = 4e-4      # 无图像尺寸的调用方: 归一化等价 (≈0.51px @1280, 本产线帧宽;
                       #   帧更窄时它更紧, 偏保守 —— 宁可多报也不漏报)

# ── 「框太紧」有**两个口径**, 都能量, 但含义不同 ─────────────────────────────────
# 名称一样、定义不同 = 同一帧在一个工具里"违规"、在另一个里"正常"（2026-09-12 实测：
# studio_doctor 报 1 帧紧, 数据集级工具报 0 帧, 两边都没错）。所以这里**显式并列**,
# 谁用哪个一目了然; 不要再在脚本里写裸 `8` 或 `0.05`。
TIGHT_ABS_PX = 8.0     # 绝对口径: 余量 < 8px。数据集级工具用
                       #   (`fall_label_qa` / `_floor_sim` / `_verify_written` / `_changelog_*`)
                       #   —— 面向"肉眼看得出的贴轮廓", 与人眼尺度一致。
TIGHT_REL = 0.05       # 相对口径: 余量 < 该向多边形尺寸的 5%。自检 `studio_doctor` 用
                       #   —— 与地板常数 `auto_label_fall._COVER_PAD = 0.10` 同族:
                       #   地板保证每侧 ≥10%, 5% 是"明显没生效"的告警线, 尺度无关。

# 说明: 容差的物理来源是 `auto_label_fall._pack_box01` —— 它把边界内缩 1e-5
# (=1280px 上的 0.013px) 来吸收 round 的 5e-6 舍入, 这会让"恰好贴在框边上"的点
# 落到框外 0.01px 级。那不是切人, 是尘埃。CONV_TOL_PX 比它大 1.5 个数量级, 留足余量
# 以吸收 JSON float32 往返与不同分辨率的换算误差。


def conv_edge_flags_n(e) -> tuple:
    """无尺寸版: 框的 (左,右,上,下) 四侧是否**贴着画面边**(留白 < CONV_TOL_N)。

    用途: 「框太紧」判据必须豁免贴画面那一侧 —— 人出画时该侧余量为 0 是物理必然。
    """
    return (e[0] <= CONV_TOL_N, 1.0 - e[2] <= CONV_TOL_N,
            e[1] <= CONV_TOL_N, 1.0 - e[3] <= CONV_TOL_N)


def conv_edge_flags_px(e, w: float, h: float, pad_px: float = CONV_TOL_PX) -> tuple:
    """有尺寸版: 同上, 按**像素**判; 顺序与 `conv_edge_flags_n` 一致 (左,右,上,下)。"""
    return (e[0] * w <= pad_px, (1.0 - e[2]) * w <= pad_px,
            e[1] * h <= pad_px, (1.0 - e[3]) * h <= pad_px)


def conv_oob_px(e, w: float, h: float) -> float:
    """框越出 [0,1] 的**溢出像素量**(四侧取最大); <= 0 表示未越界(合法)。"""
    return max(-e[0] * w, -e[1] * h, (e[2] - 1.0) * w, (e[3] - 1.0) * h)


def conv_oob_n(e) -> float:
    """无尺寸版: 越界的**归一化**溢出量; <= 0 表示未越界(合法)。"""
    return max(-e[0], -e[1], e[2] - 1.0, e[3] - 1.0)


def conv_is_oob_px(e, w: float, h: float, pad_px: float = CONV_TOL_PX) -> bool:
    """有尺寸版: 框是否**真的**越出 [0,1] —— 溢出超过容差才算, 尘埃不算。"""
    return conv_oob_px(e, w, h) > pad_px


def conv_is_oob_n(e, pad_n: float = CONV_TOL_N) -> bool:
    """无尺寸版: 同上。"""
    return conv_oob_n(e) > pad_n
