# -*- coding: utf-8 -*-
"""DPI 觉察度 —— **进程级、必须在建任何 Tk 窗口之前调用一次**。

════════════════════════════════════════════════════════════════════════
为什么需要这个模块 (2026-10-03 定位到的真因, 现象: 切检测源窗口突然变小)
════════════════════════════════════════════════════════════════════════

**症状**: 检测页把来源从「摄像头」切到「桌面」/「窗口」时, **整个主窗口在屏幕上
缩了一圈** (实测占屏宽 100% -> 73.8%); 切到摄像头/视频/图片不会。

**真因链** (每一条都有实测证据, 见 `runs/_ab_*.png` 与 `scripts/_probe_dpi_ab.py`):

  ① 本程序**从不声明 DPI 觉察** ⇒ 进程启动时是 `DPI_AWARENESS_UNAWARE`。
     此时 Windows 会把整个程序**按屏幕缩放比 (本机 1.354) 插值放大**铺满物理屏,
     所以 Tk 报的 1890x1182「逻辑像素」在 2560x1600 上看着正好铺满 (100%)。

  ② 「桌面」/「窗口」两个源走 `mss` 抓屏, 而 `mss` 的 **`MSS.__init__` 会调用**
     `ctypes.windll.shcore.SetProcessDpiAwareness(2)`
     (源码: `mss/windows/gdi.py:247 _set_dpi_awareness()`, 注释明写
      "Set DPI awareness to capture full screen on Hi-DPI monitors")。
     ⇒ **进程在运行中途**从 UNAWARE 翻成 `PER_MONITOR_DPI_AWARE`。

  ③ Windows 一旦发现进程变觉察, **立刻停止插值放大**。而窗口的**像素矩形没跟着
     变大** (1890x1111 还是 1890x1111) ⇒ 它在 2560 宽的屏上只剩 73.8%。
     **这就是用户看到的「整个主窗口变小」。**

  ④ 摄像头/视频/图片三个源**不 import mss** ⇒ 进程觉察度不变 ⇒ 不受影响。
     这正是"只有切到桌面/窗口才犯"的原因。

**修法**: 在**建任何 Tk 窗口之前**主动声明一次 `PER_MONITOR_DPI_AWARE`。
这样 mss 那次调用变成 no-op (觉察度不会再变), 而 Tk 从启动起就在"物理像素"坐标
系里工作 ⇒ 三步都不再发生。A/B 对照实测: 切源后占屏宽 100% 保持不变。

**顺带的好处**: 声明觉察后不做插值放大 ⇒ 文字/图标是原生渲染, 比原来的
"放大 1.354 倍的插值"更锐利 (见 `runs/ab_*_1_before.png` 与 `ab_B_*` 对比)。

**必须付的代价 (已在 `App.__init__` 里一并处理)**: 声明觉察后
`winfo_screenwidth()/winfo_screenheight()` 从"逻辑像素"变成"物理像素"
(本机 1890x1182 -> 2560x1600)。任何**按屏幕尺寸推算窗口尺寸**的代码
(如 `App.__init__` 的自适应尺寸) 会因此拿到 1.354 倍大的数, 必须一并校正,
否则会得到与修复前观感不同的窗口 (详见 `gui.main_window` 的尺寸推算注释)。
"""

from __future__ import annotations
import ctypes
import sys

# 只做一次 + 幂等: 多处调用无害
_DONE = False
_RESULT = "not-called"


def _read_awareness(shcore) -> str:
    try:
        v = ctypes.c_int(0)
        shcore.GetProcessDpiAwareness(None, ctypes.byref(v))
        return {0: "UNAWARE", 1: "SYSTEM_AWARE",
                2: "PER_MONITOR"}.get(v.value, str(v.value))
    except Exception as e:                       # pragma: no cover
        return f"unknown({e})"


def enable_per_monitor_dpi_awareness() -> str:
    """声明本进程为 **每显示器 DPI 觉察** (幂等)。

    返回一个短字符串描述结果, 供日志/自检使用:
      `"already"`    —— 已经是 PER_MONITOR, 没做任何事
      `"set"`        —— 本次设置成功
      `"failed:<e>"` —— 设置失败 (不抛异常; 主程序照常启动)

    ⚠ 重复调用**一律返回 `"already"`** —— 不可以把第一次的 `"set"` 缓存着反复返回:
      那会让调用方以为"又设置了一次", 与 `SetProcessDpiAwareness` 只能生效一次的
      事实矛盾 (本仓 smoke_dpi_awareness 的幂等断言正是抓这个)。
    """
    global _DONE, _RESULT
    if _DONE:
        # 已经跑过: 只有"首次就失败"才继续报失败, 其余一律是 already
        return _RESULT if _RESULT.startswith("failed") else "already"
    _DONE = True

    if sys.platform != "win32":
        _RESULT = "skipped:non-windows"
        return _RESULT

    try:
        shcore = ctypes.windll.shcore
    except Exception as e:
        _RESULT = f"failed:no-shcore({e})"
        return _RESULT

    if _read_awareness(shcore) == "PER_MONITOR":
        _RESULT = "already"
        return _RESULT

    # SetProcessDpiAwareness 只能成功一次; 之后调用返回 E_ACCESSDENIED
    # (0x80070005) —— 那是"已经被别处设过了", 不是错误。
    try:
        hr = shcore.SetProcessDpiAwareness(2)     # 2 = PROCESS_PER_MONITOR_DPI_AWARE
    except Exception as e:
        _RESULT = f"failed:{e}"
        return _RESULT

    now = _read_awareness(shcore)
    if hr == 0:
        _RESULT = "set" if now == "PER_MONITOR" else f"set-not-effective({now})"
    else:
        # 典型: 被别处设过 (E_ACCESSDENIED) -> 以实测觉察度为准
        _RESULT = "already" if now == "PER_MONITOR" else f"failed:hr={hr:#x}({now})"
    return _RESULT


def dpi_awareness_name() -> str:
    """当前进程 DPI 觉察度名 (排障用)"""
    if sys.platform != "win32":
        return "non-windows"
    try:
        return _read_awareness(ctypes.windll.shcore)
    except Exception as e:                       # pragma: no cover
        return f"unknown({e})"
