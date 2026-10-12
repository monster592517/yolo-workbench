# -*- coding: utf-8 -*-
"""poll_kit.py — 后台队列轮询的统一节流 (性能优化 2026-09-11)

问题:
    9 个页面各自注册了 `self.after(120~200, self._poll_q)` 的**永久轮询循环**,
    即使页面不可见、队列里一个字都没有, 也照样每秒醒 5~8 次。9 页堆在一起就是
    每秒约 60 次无意义唤醒 —— 抢 GIL、抢 Tk 事件循环, 笔记本上直接体现为
    风扇一转、电池掉得快、动画发涩。

做法:
    `poll_delay()` 给出这一次该用多长的间隔:
        · 本次有消息 → fast_ms        (尽快把积压排干)
        · 空闲 + 当前页可见 → idle_ms (慢速待命)
        · 空闲 + 当前页不可见 → hidden_ms (更慢, 但仍要消费队列,
          否则训练/生成的进度会堆在队列里不刷新)

    页面可见性由 main_window.show_page 维护的 `_is_active` 标记提供
    (堆叠式页面用 tkraise/lower 切换, winfo_ismapped 分辨不出谁在前台)。

用法 (改造一个已有轮询循环只需两行):
    busy = False
    try:
        while True:
            msg = q.get_nowait(); busy = True; handle(msg)
    except queue.Empty:
        pass
    self.after(poll_delay(self, busy), self._poll_q)
"""
from __future__ import annotations

import tkinter as tk

FAST_MS = 120      # 有活干
IDLE_MS = 600      # 空闲 · 前台
HIDDEN_MS = 1000   # 空闲 · 后台


def active_page(widget: tk.Misc) -> bool:
    """向上找到页面的 _is_active 标记 (main_window 维护); 找不到视为可见。"""
    w = widget
    for _ in range(12):
        if w is None:
            break
        flag = getattr(w, "_is_active", None)
        if flag is not None:
            return bool(flag)
        try:
            w = w.master
        except Exception:
            break
    return True


def poll_delay(host: tk.Misc, busy: bool,
               fast_ms: int = FAST_MS,
               idle_ms: int = IDLE_MS,
               hidden_ms: int = HIDDEN_MS) -> int:
    """返回本轮轮询应使用的间隔 (毫秒)。"""
    if busy:
        return fast_ms
    try:
        if active_page(host):
            return idle_ms
    except Exception:
        return idle_ms
    return hidden_ms


def mark_active(page: tk.Misc, active: bool) -> None:
    """标记页面是否在前台 (由 main_window.show_page 调用)。"""
    try:
        page._is_active = bool(active)
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────
#  after 定时器收尾 (2026-09-11)
#
#  问题: tkinter 的 Misc.destroy() 会把自己注册过的 Tcl 命令 deletecommand 掉,
#  但**不会取消已经排进 after 队列的定时器**。定时器到点后 Tcl 查不到那个命令,
#  于是往 stderr 吐一行 `invalid command name "<id>_poll_q"`, 并且那个回调原本
#  该干的活(把队列排干、把 busy 置回 False、收尾线程)全都静默丢失。
#
#  页面重建 (apply_theme / _rebuild_pages) 和退出流程最容易踩: 旧页面的轮询
#  定时器还挂在 Tcl 事件循环里, 新页面已经建好了 —— 轻则刷屏报错, 重则旧回调
#  在新页面上乱动控件。
#
#  做法: 给 Misc.destroy 挂一层前置收尾 —— 拿本控件的 `_tclCommands` 名单去和
#  待触发的 after id 逐一比对(**只认自己名下的**), 命中就 after cancel。
#  按控件名册精确匹配, 不会误杀别的页面挂在同一个方法名上的定时器。
# ──────────────────────────────────────────────────────────────────────

_REAPER_INSTALLED = False


def _reap_after_timers(widget: tk.Misc) -> int:
    """取消 widget 名下所有待触发的 after 定时器, 返回取消条数。"""
    cmds = getattr(widget, "_tclCommands", None)
    if not cmds:
        return 0          # 没注册过回调 → 快路径, 绝大多数控件走这里
    try:
        tkapp = widget.tk
        raw = tkapp.call("after", "info")
        if not raw:
            return 0
        pending = tkapp.splitlist(raw)
    except Exception:
        return 0
    owned = set(cmds)
    killed = 0
    for aid in pending:
        try:
            info = tkapp.splitlist(tkapp.call("after", "info", aid))
        except Exception:
            continue
        name = info[0] if info else ""
        if isinstance(name, str) and name in owned:
            try:
                tkapp.call("after", "cancel", aid)
                killed += 1
            except Exception:
                pass
    return killed


def install_after_reaper() -> bool:
    """幂等安装 after 收尾钩子 (poll_kit 导入时自动调用)。"""
    global _REAPER_INSTALLED
    if _REAPER_INSTALLED:
        return True
    try:
        orig_destroy = tk.Misc.destroy

        def destroy(self):
            _reap_after_timers(self)
            try:
                orig_destroy(self)
            except tk.TclError:
                pass          # 已经销毁过: 幂等, 不往上抛

        tk.Misc.destroy = destroy
        _REAPER_INSTALLED = True
    except Exception:
        return False
    return True


install_after_reaper()
