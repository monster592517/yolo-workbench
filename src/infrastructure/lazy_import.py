# -*- coding: utf-8 -*-
"""lazy_import.py — 重型依赖的惰性加载与后台预热 (启动提速, 2026-09-11)

问题 (实测, 本机 E: 慢盘 / RTX 4070 Laptop):
    import torch                8703 ms
    from ultralytics import YOLO  +1198 ms
    合计 ≈ 9.9 秒冷导入。

而 `core/trainer.py` 与 `core/detector.py` 都在**模块顶层**做
`from ultralytics import YOLO`, 于是启动画面一走到"加载训练引擎..."就
硬卡 13+ 秒 —— 这就是"启动慢"的头号原因 (占 splash 总耗时 ~90%)。

做法:
    1) `yolo_class()` —— 惰性拿到 YOLO 类, 首次调用才真正 import。
    2) `ultralytics_available()` —— 用 importlib.util.find_spec 判断, 不触发导入。
    3) `warm_up_async()` —— 界面出来之后在**后台线程**里预热, 用户第一次点
       "训练/检测"时模块已经在内存里, 无需等待; 预热状态可查询。

对外 API:
    yolo_class() -> type | None
    ultralytics_available() -> bool
    warm_up_async(callback=None)        启动后台预热 (幂等)
    warm_up_state() -> "idle"|"running"|"ready"|"failed"
    is_ready() -> bool
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import threading
import time

_log = logging.getLogger(__name__)

_lock = threading.Lock()
_state = {"status": "idle", "t0": None, "elapsed_ms": 0, "error": ""}
_yolo_cls = None


def ultralytics_available() -> bool:
    """是否装了 ultralytics —— 只查 finder, 不解包 (微秒级)。"""
    try:
        return importlib.util.find_spec("ultralytics") is not None
    except Exception:
        return False


def yolo_class():
    """惰性返回 ultralytics.YOLO (首次调用才导入 torch); 不可用时返回 None。"""
    global _yolo_cls
    if _yolo_cls is not None:
        return _yolo_cls
    with _lock:
        if _yolo_cls is not None:
            return _yolo_cls
        t0 = time.perf_counter()
        try:
            mod = importlib.import_module("ultralytics")
            _yolo_cls = getattr(mod, "YOLO", None)
            _log.info("ultralytics 惰性导入完成 (%.0f ms)", (time.perf_counter() - t0) * 1000)
        except Exception as e:  # 未安装 / 依赖缺失
            _log.warning("ultralytics 导入失败: %s", e)
            _state["error"] = str(e)
            _yolo_cls = None
        return _yolo_cls


def is_ready() -> bool:
    return _yolo_cls is not None


def low_priority() -> bool:
    """把**当前线程**降到"低于正常"优先级 (Windows)。

    后台预热 torch 要跑 ~10s 的重 CPU 导入, 若与 UI 抢时间片会让界面发涩。
    降一级优先级后, 主线程/UI 事件始终抢占得到 CPU, 预热只是"慢一点完成"。
    返回是否设置成功; 非 Windows 或失败时静默返回 False。
    """
    try:
        import ctypes
        # THREAD_PRIORITY_BELOW_NORMAL = -1
        handle = ctypes.windll.kernel32.GetCurrentThread()
        return bool(ctypes.windll.kernel32.SetThreadPriority(handle, -1))
    except Exception:
        return False


def warm_up_state() -> str:
    if _yolo_cls is not None:
        return "ready"
    return _state["status"]


def warm_up_async(callback=None) -> None:
    """后台线程预热 torch/ultralytics (幂等, 重复调用只跑一次)。"""
    if _state["status"] in ("running", "ready"):
        return
    if not ultralytics_available():
        _state["status"] = "failed"
        _state["error"] = "ultralytics 未安装"
        return
    _state["status"] = "running"
    _state["t0"] = time.perf_counter()

    def _work():
        global _yolo_cls
        low_priority()          # 别和 UI 抢 CPU
        t0 = time.perf_counter()
        try:
            # yolo_class() 内部就是 importlib.import_module("ultralytics"), 而
            # ultralytics 自己会 import torch —— 所以这里是全应用**唯一**的冷导入点。
            # 别的模块 (如 environment 的 GPU 探测) 一律改为复用本预热, 不再各自
            # `import torch`, 否则两个线程同时导 torch 会互相等导入锁 + 抢 GIL。
            _yolo_cls = yolo_class()
            _state["status"] = "ready" if _yolo_cls is not None else "failed"
        except Exception as e:
            _state["status"] = "failed"
            _state["error"] = str(e)
            _log.warning("预热失败: %s", e)
        _state["elapsed_ms"] = int((time.perf_counter() - t0) * 1000)
        if callback:
            try:
                callback(_state["status"], _state["elapsed_ms"])
            except Exception:
                pass

    threading.Thread(target=_work, daemon=True, name="torch-warmup").start()


def lazy_torch():
    """惰性返回 torch 模块 (设备查询等场景); 不可用返回 None。"""
    try:
        return importlib.import_module("torch")
    except Exception:
        return None
