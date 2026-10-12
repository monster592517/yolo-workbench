# -*- coding: utf-8 -*-
"""动效引擎 (v3 — 全局单时钟, 2026-09-11 性能重写)

v2 的问题 (实测/代码审查):
    每个动画各自 `widget.after(16, step)` 形成独立定时器链。一次 hover 扫过
    导航栏 + 卡片 + 按钮会同时拉起几十条 after 链, Tk 事件循环被切成碎片;
    并且 v2 的 animate() 把 after id 写进 _TICKS 却从不在 state 里存 job,
    导致 cancel()/重入时**无法真正取消**, 旧链会继续空跑一轮。

v3 的做法:
    · 全局**单一时钟**: 只要有任意动画在跑, 就只有一个 after(16) 在排程;
      全部结束时立即停表 —— 空闲时 0 定时器, 不占 CPU、不抢主循环。
    · 按时间插值 (perf_counter), 与帧率无关, UI 忙时不会"压缩"动画时长。
    · 颜色插值预计算 from/to 的 RGB 整数, 每帧只做一次整数线性插值 + 格式化。
    · animate / pulse / fade 共用同一张表与同一时钟, 可被真正取消。

对外 API (与 v2 完全兼容):
    animate(widget, prop, to, ms=200, ease=..., on_done=None)
    fade_in(widget, to_bg, from_bg=None, ms=160)
    hover_bind(widget, prop="bg", base="", hover="", ms=160)
    pulse(widget, prop="bg", from_c="", to_c="", ms=800, times=None)
    stop_pulse(widget, prop="bg")
    cancel(widget)            # 取消该控件全部动画
    lerp_color(a, b, t) / to_hex(rgb)
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from tkinter import Widget

# ── 缓动 ──────────────────────────────────────────────────────────────
EASE_OUT_CUBIC = lambda t: 1 - (1 - t) ** 3            # noqa: E731
EASE_IN_OUT = lambda t: t * t * (3 - 2 * t)            # noqa: E731
EASE_OUT_QUART = lambda t: 1 - (1 - t) ** 4            # noqa: E731
FRAME_MS = 16                                          # ~60fps

# 活动动画表: (widget_id, prop) -> state
_ANIMS: dict = {}
_CLOCK = {"host": None, "job": None}


# ── 颜色工具 ─────────────────────────────────────────────────────────

def _parse_hex(color) -> tuple:
    c = str(color).strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    if len(c) < 6:
        raise ValueError(f"not a hex color: {color!r}")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def to_hex(rgb) -> str:
    return "#%02x%02x%02x" % (int(rgb[0]), int(rgb[1]), int(rgb[2]))


def lerp_color(a: str, b: str, t: float) -> str:
    pa, pb = _parse_hex(a), _parse_hex(b)
    return to_hex((pa[0] + (pb[0] - pa[0]) * t,
                   pa[1] + (pb[1] - pa[1]) * t,
                   pa[2] + (pb[2] - pa[2]) * t))


def _is_hex(value) -> bool:
    try:
        s = str(value).strip()
        return s.startswith("#") and len(s) in (4, 7)
    except Exception:
        return False


# ── 时钟 ─────────────────────────────────────────────────────────────

def _host_for(widget) -> Widget:
    """选一个存活控件挂全局时钟 (优先根窗口, 最稳)。"""
    try:
        top = widget.winfo_toplevel()
        if top is not None and top.winfo_exists():
            return top
    except Exception:
        pass
    return widget


def _ensure_clock(widget) -> bool:
    """确保全局时钟在跑; 返回是否成功排程。"""
    if _CLOCK["job"] is not None:
        return True
    try:
        host = _CLOCK["host"]
        if host is None or not host.winfo_exists():
            host = _host_for(widget)
            _CLOCK["host"] = host
        _CLOCK["job"] = host.after(FRAME_MS, _tick)
        return True
    except Exception:
        _CLOCK["job"] = None
        _CLOCK["host"] = None
        return False


def _tick() -> None:
    """全局唯一心跳: 推进全部动画, 表空即停表。"""
    _CLOCK["job"] = None
    now = time.perf_counter()
    dirty_keys = []
    for key, st in list(_ANIMS.items()):
        w = st["widget"]
        try:
            if not w.winfo_exists():
                _ANIMS.pop(key, None)
                continue
        except Exception:
            _ANIMS.pop(key, None)
            continue
        try:
            if st["kind"] == "tween":
                _step_tween(key, st, now)
            else:
                _step_pulse(key, st, now)
        except Exception:
            _ANIMS.pop(key, None)
        else:
            dirty_keys.append(key)
    if _ANIMS:
        host = _CLOCK["host"]
        try:
            if host is None or not host.winfo_exists():
                _CLOCK["host"] = next(
                    (s["widget"] for s in _ANIMS.values()), None)
                host = _CLOCK["host"]
            _CLOCK["job"] = host.after(FRAME_MS, _tick)
        except Exception:
            _CLOCK["job"] = None
            _CLOCK["host"] = None


# ── 线性补间 (animate / fade_in / hover_bind) ────────────────────────

def animate(widget: Widget, prop: str, to: str, ms: int = 200,
            ease: Callable[[float], float] = EASE_OUT_CUBIC,
            on_done: Optional[Callable[[], None]] = None) -> None:
    """平滑过渡 widget 的某个可配色属性 (bg/fg/highlightbackground...)。"""
    try:
        if not widget.winfo_exists():
            return
    except Exception:
        return

    if not _is_hex(to):
        # 目标不是颜色 (ttk 样式名等): 直接赋值, 不做补间
        try:
            widget.configure(**{prop: to})
        except Exception:
            pass
        if on_done:
            try:
                on_done()
            except Exception:
                pass
        return

    key = (id(widget), prop)
    try:
        cur = widget.cget(prop)
    except Exception:
        cur = to
    if not _is_hex(cur):
        cur = to
    if cur == to:
        # 已是目标值: 取消旧动画 (避免旧链把颜色又拽回去), 立即收尾
        _ANIMS.pop(key, None)
        if on_done:
            try:
                on_done()
            except Exception:
                pass
        return

    try:
        f_rgb, t_rgb = _parse_hex(cur), _parse_hex(to)
    except Exception:
        return

    st = _ANIMS.get(key)
    if st is not None and st["kind"] == "tween" and st["to"] == to:
        return  # 已在向同一目标过渡, 不重置进度 (避免 hover 抖动被无限拉长)
    _ANIMS[key] = {
        "kind": "tween", "widget": widget, "prop": prop,
        "from_rgb": f_rgb, "to_rgb": t_rgb, "to": to,
        "t0": time.perf_counter(), "ms": max(40, int(ms)),
        "ease": ease, "on_done": on_done,
    }
    _ensure_clock(widget)


def _step_tween(key: str, st: dict, now: float) -> None:
    t = (now - st["t0"]) / st["ms"]
    if t >= 1.0:
        try:
            st["widget"].configure(**{st["prop"]: st["to"]})
        except Exception:
            pass
        _ANIMS.pop(key, None)
        cb = st.get("on_done")
        if cb:
            try:
                cb()
            except Exception:
                pass
        return
    e = st["ease"](t)
    fr, tr = st["from_rgb"], st["to_rgb"]
    st["widget"].configure(**{st["prop"]: "#%02x%02x%02x" % (
        int(fr[0] + (tr[0] - fr[0]) * e),
        int(fr[1] + (tr[1] - fr[1]) * e),
        int(fr[2] + (tr[2] - fr[2]) * e))})


def fade_in(widget: Widget, to_bg: str, from_bg: Optional[str] = None, ms: int = 160) -> None:
    """入场: 背景自 from_bg 淡入到 to_bg。"""
    animate(widget, "bg", to_bg, ms=ms, ease=EASE_OUT_CUBIC)


def hover_bind(widget: Widget, prop: str = "bg", base: str = "", hover: str = "",
               ms: int = 160) -> None:
    """绑定 hover 动效 (Enter/Leave); 未指定 base 时取控件当前值。"""
    try:
        if not base:
            cur = widget.cget(prop)
            base = cur if _is_hex(cur) else ""
        if not hover or not base:
            return
        widget.bind("<Enter>", lambda e: animate(widget, prop, hover, ms=ms), add="+")
        widget.bind("<Leave>", lambda e: animate(widget, prop, base, ms=ms), add="+")
    except Exception:
        pass


# ── 呼吸脉冲 (仅"运行中"状态点) ──────────────────────────────────────

def pulse(widget: Widget, prop: str = "bg", from_c: str = "", to_c: str = "",
          ms: int = 800, times: Optional[int] = None) -> None:
    """呼吸脉冲: from_c <-> to_c 往复; stop_pulse 结束。"""
    stop_pulse(widget, prop)
    if not (_is_hex(from_c) and _is_hex(to_c)):
        return
    key = (id(widget), prop, "pulse")
    _ANIMS[key] = {
        "kind": "pulse", "widget": widget, "prop": prop,
        "a_rgb": _parse_hex(from_c), "b_rgb": _parse_hex(to_c),
        "t0": time.perf_counter(), "half": max(100.0, ms / 2.0),
        "a": from_c, "b": to_c, "times": times, "done": 0, "forward": True,
    }
    _ensure_clock(widget)


def _step_pulse(key: str, st: dict, now: float) -> None:
    elapsed = now - st["t0"]
    if elapsed >= st["half"]:
        # 半周期结束: 取反方向, 累计完成次数
        st["t0"] = now
        st["forward"] = not st["forward"]
        if not st["forward"]:
            st["done"] += 1
            if st["times"] is not None and st["done"] >= st["times"]:
                try:
                    st["widget"].configure(**{st["prop"]: st["a"]})
                except Exception:
                    pass
                _ANIMS.pop(key, None)
                return
    f = min(1.0, max(0.0, (now - st["t0"]) / st["half"]))
    e = EASE_IN_OUT(f)
    if not st["forward"]:
        e = 1.0 - e
    a, b = st["a_rgb"], st["b_rgb"]
    st["widget"].configure(**{st["prop"]: "#%02x%02x%02x" % (
        int(a[0] + (b[0] - a[0]) * e),
        int(a[1] + (b[1] - a[1]) * e),
        int(a[2] + (b[2] - a[2]) * e))})


def stop_pulse(widget: Widget, prop: str = "bg") -> None:
    _ANIMS.pop((id(widget), prop, "pulse"), None)


def cancel(widget: Widget) -> None:
    """取消该控件的全部动画 (页面切换/销毁时调用)。"""
    wid = id(widget)
    for key in [k for k in _ANIMS if k[0] == wid]:
        _ANIMS.pop(key, None)


def cancel_all() -> None:
    """清空全部动画并停表 (窗口关闭/主题重建时调用)。"""
    _ANIMS.clear()
    job = _CLOCK.get("job")
    if job is not None:
        try:
            _CLOCK["host"].after_cancel(job)
        except Exception:
            pass
    _CLOCK["job"] = None


def active_count() -> int:
    """当前活动动画数 (自检/性能面板用)。"""
    return len(_ANIMS)
