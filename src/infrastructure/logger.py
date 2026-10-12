# -*- coding: utf-8 -*-
"""
Standard Python logging configuration for YOLO工作台.

Configures:
  - RotatingFileHandler: runs/logs/app.log, 10MB, 5 backups
  - StreamHandler: stdout (console)
  - Format: "YYYY-MM-DD HH:MM:SS.mmm LEVEL [module] message"

Usage in any module:
    import logging
    _log = logging.getLogger(__name__)
    _log.info("message")
    _log.error("message", exc_info=True)
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_LOG_DIR = _PROJECT_ROOT / "runs" / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_LOG_FILE = _LOG_DIR / "app.log"

_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-5s [%(name)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_BACKUP_COUNT = 5

_initialized = False


def get_project_root() -> Path:
    """当前项目根目录 (可被路径中心动态切换)"""
    return _PROJECT_ROOT


def set_project_root(root) -> None:
    """路径中心联动: 切换项目根目录并重建日志文件句柄 (幂等)"""
    global _PROJECT_ROOT, _LOG_DIR, _LOG_FILE
    _PROJECT_ROOT = Path(root).resolve()
    _LOG_DIR = _PROJECT_ROOT / "runs" / "logs"
    _LOG_FILE = _LOG_DIR / "app.log"
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    if not _initialized:
        return
    # 重建文件句柄: 关闭旧日志文件, 指向新根目录下的 app.log
    try:
        root_logger = logging.getLogger()
        for h in list(root_logger.handlers):
            if isinstance(h, logging.handlers.RotatingFileHandler):
                try:
                    h.close()
                except Exception:
                    pass
                root_logger.removeHandler(h)
        fh = logging.handlers.RotatingFileHandler(
            str(_LOG_FILE),
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        fh.setLevel(root_logger.level)
        fh.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT))
        root_logger.addHandler(fh)
    except Exception:
        pass


def init_logging(
    level: int = logging.INFO,
    log_file: str | None = None,
) -> None:
    """Initialize logging handlers. Idempotent: safe to call multiple times."""
    global _initialized
    if _initialized:
        return
    _initialized = True

    file_path = log_file or str(_LOG_FILE)

    # Root logger
    root = logging.getLogger()
    root.setLevel(level)

    # Suppress noisy OpenCV and PIL debug logs
    logging.getLogger("PIL").setLevel(logging.WARNING)

    # Formatter
    fmt = logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT)

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(fmt)
    root.addHandler(console)

    # Rotating file handler (10 MB, 5 backups)
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            file_path,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except Exception:
        pass


# Auto-initialize on import
init_logging()


def get_logger(name: str = "") -> logging.Logger:
    """Convenience: returns a logger for the given module name."""
    return logging.getLogger(name)
