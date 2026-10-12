# -*- coding: utf-8 -*-
"""企业级审计日志 — 结构化记录程序全部运行情况与报错。

- operations.log: 结构化 JSON 审计轨迹 (操作/结果/耗时/来源)
- 自动捕获: 未处理异常 (sys/threading excepthook) + ERROR 级日志
- 与标准 logging (app.log) 并存, 互不干扰
"""
from __future__ import annotations

import json
import logging
import sys
import threading
import time
import traceback
from collections import deque
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_AUDIT_DIR = _PROJECT_ROOT / "runs" / "logs"
_AUDIT_FILE = _AUDIT_DIR / "operations.log"
_MAX_BYTES = 20 * 1024 * 1024
_BACKUPS = 3

_log = logging.getLogger(__name__)
_lock = threading.Lock()

# ── 目录 / 句柄缓存 (2026-09-11 性能修复) ──
# 原实现: audit() 里对**每一条**日志执行
#     _AUDIT_DIR.mkdir(parents=True, exist_ok=True)
#     with open(_AUDIT_FILE, "a") as f: f.write(...)
# 即便目录已存在, mkdir 仍是一次系统调用 (Windows 还要逐级拼父路径); 再加上每条
# 一次 open/write/close —— 而 audit() 是全项目最热的调用之一 (页面切换、启动、
# 训练、标注全在写)。看门狗实测单次 audit 326ms 卡在主线程上。
# 现在: 目录只确保一次; 文件句柄常驻, 用**行缓冲** (buffering=1) 保持"每条立即落盘"
# 的语义, 但省掉 open/close 的系统调用。
_DIR_READY = False
_FH = None


def _ensure_dir() -> None:
    """确保审计目录存在 —— 只做一次 (换根目录时会重置)。"""
    global _DIR_READY
    if _DIR_READY:
        return
    try:
        _AUDIT_DIR.mkdir(parents=True, exist_ok=True)
        _DIR_READY = True
    except Exception:
        pass


def _fh():
    """常驻追加句柄 (行缓冲: 每条立即落盘, 但不再 open/close)。"""
    global _FH
    if _FH is None or getattr(_FH, "closed", True):
        _ensure_dir()
        _FH = open(_AUDIT_FILE, "a", encoding="utf-8", buffering=1)
    return _FH


def _close_fh() -> None:
    global _FH
    try:
        if _FH is not None and not _FH.closed:
            _FH.close()
    except Exception:
        pass
    _FH = None


# ── 内存环形缓冲 (性能修复 2026-09-11) ──
# 原 recent_operations() 每次都 read_text() 整个 operations.log (上限 20MB) 再
# splitlines() 取最后 n 行。实测单次 **108ms**, 而分享页每次 on_show 都会调它,
# 于是"切到分享页"肉眼可见一顿。
# 现改为写入时就顺手塞进环形缓冲, 读取变成 O(1) 内存操作 (0ms)。
_RECENT_MAX = 800
_RECENT: deque = deque(maxlen=_RECENT_MAX)
_RECENT_LOADED = False


def _seed_recent_from_disk() -> None:
    """首次使用时只读文件尾部若干 KB 灌入缓冲 (绝不整文件读)。"""
    global _RECENT_LOADED
    if _RECENT_LOADED:
        return
    _RECENT_LOADED = True
    try:
        if not _AUDIT_FILE.exists():
            return
        size = _AUDIT_FILE.stat().st_size
        chunk = min(size, 512 * 1024)
        with open(_AUDIT_FILE, "rb") as f:
            if size > chunk:
                f.seek(size - chunk)
                f.readline()          # 丢弃可能被截断的首行
            raw = f.read()
        for line in raw.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                _RECENT.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        pass


def get_project_root() -> Path:
    """当前项目根目录 (可被路径中心动态切换)"""
    return _PROJECT_ROOT


def set_project_root(root) -> None:
    """路径中心联动: 切换审计日志目录与文件 (幂等)"""
    global _PROJECT_ROOT, _AUDIT_DIR, _AUDIT_FILE, _RECENT_LOADED, _DIR_READY
    _close_fh()                 # 句柄指向旧文件 → 必须先关 (Windows 上不关就删不掉)
    _PROJECT_ROOT = Path(root).resolve()
    _AUDIT_DIR = _PROJECT_ROOT / "runs" / "logs"
    _AUDIT_FILE = _AUDIT_DIR / "operations.log"
    _RECENT_LOADED = False      # 换了根目录 → 环形缓冲需重新回填
    _DIR_READY = False          # 换了目录 → 需要重新 mkdir 一次
    _RECENT.clear()
    _ensure_dir()


def _rotate():
    try:
        if _AUDIT_FILE.exists() and _AUDIT_FILE.stat().st_size > _MAX_BYTES:
            # ⚠ 必须先关常驻句柄: Windows 上不允许 rename 一个仍被打开的文件,
            #   会抛 PermissionError —— 而下面整个 try 把异常吞掉了, 于是
            #   "日志轮转"会**静默地永久失效** (文件顶到 20MB 就不再轮转)。
            _close_fh()
            for i in range(_BACKUPS - 1, 0, -1):
                old = _AUDIT_FILE.with_suffix(f".{i}.log")
                new = _AUDIT_FILE.with_suffix(f".{i + 1}.log")
                if old.exists():
                    old.replace(new)
            _AUDIT_FILE.replace(_AUDIT_FILE.with_suffix(".1.log"))
            # 换文件后环形缓冲的"已落盘"假设仍成立 (只是历史, 无需清空)
    except Exception:
        pass


_ROTATE_EVERY = 200          # 每 200 条才 stat 一次, 避免每条都碰盘
_rotate_counter = 0


def _rotate_throttled():
    global _rotate_counter
    _rotate_counter += 1
    if _rotate_counter >= _ROTATE_EVERY:
        _rotate_counter = 0
        _rotate()


def audit(action: str, detail: str = "", level: str = "info", module: str = "",
          elapsed: float = None, extra: dict = None) -> None:
    """记录一条结构化审计日志。

    action: 操作名 (app_start / page_switch / training_start / ai_annotate / ...)
    level:  info | warn | error
    """
    try:
        _ensure_dir()
        _rotate_throttled()
        record = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            "level": level,
            "action": action,
            "module": module or __name__,
            "detail": detail,
        }
        if elapsed is not None:
            record["elapsed_s"] = round(elapsed, 3)
        if extra:
            record["extra"] = extra
        with _lock:
            try:
                fh = _fh()              # 常驻句柄 + 行缓冲 → 写入即落盘
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            except Exception:
                # 句柄可能已坏 (被删/盘满/编码问题) → 丢掉句柄, 下次重建
                _close_fh()
            _RECENT.append(record)          # 同步进环形缓冲, recent_operations 零 IO
    except Exception:
        pass
    if level == "error":
        _log.error("[audit] %s: %s", action, detail)
    elif level == "warn":
        _log.warning("[audit] %s: %s", action, detail)
    else:
        _log.info("[audit] %s: %s", action, detail)


class _ErrorForwardHandler(logging.Handler):
    """把 ERROR+ 日志自动转发为审计记录 (保证所有报错都进 operations.log)

    递归防护: 审计模块自身的日志不再转发 (否则 audit -> _log.error ->
    emit -> audit 无限递归, 单个报错可写数百条嵌套日志)。
    """

    _in_emit = False

    def emit(self, record):
        if self._in_emit:
            return
        if record.name == __name__ or record.name == "infrastructure.audit":
            return
        if record.levelno < logging.ERROR:
            return
        self._in_emit = True
        try:
            audit(action="log_error", detail=record.getMessage(),
                  level="error", module=record.name,
                  extra={"line": getattr(record, "lineno", None),
                         "path": getattr(record, "pathname", "")})
        except Exception:
            pass
        finally:
            self._in_emit = False


def _sys_excepthook(exc_type, exc_value, exc_tb):
    audit(action="uncaught_exception", detail="".join(
        traceback.format_exception(exc_type, exc_value, exc_tb)),
        level="error", module="sys.excepthook")
    # 保留默认行为
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def _thread_excepthook(args):
    audit(action="thread_exception", detail="".join(
        traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)),
        level="error", module=f"thread:{args.thread and args.thread.name}")
    # 保留默认行为
    try:
        threading.__excepthook__(args)
    except Exception:
        pass


def install_audit_hooks() -> None:
    """安装全局审计钩子 (幂等): 异常钩子 + ERROR 日志转发"""
    try:
        sys.excepthook = _sys_excepthook
        threading.excepthook = _thread_excepthook
        root = logging.getLogger()
        for h in root.handlers:
            if isinstance(h, _ErrorForwardHandler):
                return
        root.addHandler(_ErrorForwardHandler())
    except Exception:
        pass


def recent_operations(n: int = 200) -> list:
    """最近 n 条审计记录 (设置页/分享页展示用)。

    性能修复 (2026-09-11): 原实现每次 read_text() 整个 operations.log
    (上限 20MB) + splitlines + 逐行 json.loads, 实测 **108ms/次**;
    分享页每次 on_show 都调用, 于是切页明显卡顿。
    现直接从写入时维护的内存环形缓冲取, **0 IO**; 仅进程启动后首次调用
    会从文件尾部最多读 512KB 做一次回填。
    """
    if not _RECENT_LOADED:
        with _lock:
            _seed_recent_from_disk()
    try:
        if n <= 0:
            return []
        if len(_RECENT) > n:
            return list(_RECENT)[-n:]
        return list(_RECENT)
    except Exception:
        return []
