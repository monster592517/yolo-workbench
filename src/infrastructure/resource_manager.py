# -*- coding: utf-8 -*-
"""ResourceManager — singleton with timeout enforcement, camera retry, exception logging"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

from infrastructure.diagnostics import (
    CameraError, SubprocessTimeoutError, VideoWriterError,
)

_log = logging.getLogger(__name__)

CAMERA_RETRY_MAX = 3
CAMERA_STALL_SECONDS = 10.0


class ResourceManager:
    """Singleton — timeout-enforced subprocess, camera retry, full-stack exception handling."""

    _instance: Optional["ResourceManager"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "ResourceManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init()
        return cls._instance

    def _init(self):
        self._cameras: Dict[int, dict] = {}       # index → {"cap", "last_frame", "retries"}
        self._stray_caps: List[tuple] = []        # abandoned VideoCapture handles (timeout open)
        self._writers: Dict[str, cv2.VideoWriter] = {}
        self._subprocesses: List[subprocess.Popen] = []
        self._camera_lock = threading.Lock()
        self._writer_lock = threading.Lock()
        self._proc_lock = threading.Lock()

    # ═══════════════════ Camera with retry ═══════════════════

    def get_camera(self, index: int, backend: int = None) -> cv2.VideoCapture:
        """Get camera handle. Auto-retry on stall (no new frame for 10s, max 3 retries)."""
        with self._camera_lock:
            if index in self._cameras:
                entry = self._cameras[index]
                cap = entry["cap"]
                if cap.isOpened():
                    return cap
                # Stale handle — clean up
                try:
                    cap.release()
                except Exception:
                    pass
                del self._cameras[index]

            # Open new camera (with open timeout to avoid VideoCapture hang)
            for retry in range(CAMERA_RETRY_MAX):
                cap = self._open_camera_with_timeout(index, backend or cv2.CAP_DSHOW)
                if cap is not None and cap.isOpened():
                    self._cameras[index] = {
                        "cap": cap, "last_frame": time.time(), "retries": retry,
                    }
                    _log.info("Camera %d opened (retry=%d)", index, retry)
                    # Start looping stall monitor in background
                    threading.Thread(
                        target=self._monitor_camera_stall,
                        args=(index,), daemon=True, name=f"cam-monitor-{index}"
                    ).start()
                    return cap
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
                _log.error("Camera %d open attempt %d failed (timeout/error)", index, retry + 1)
                time.sleep(0.5)

            raise CameraError(f"摄像头 {index} 打开失败 (已重试 {CAMERA_RETRY_MAX} 次)")

    def _open_camera_with_timeout(self, index: int, backend: int, timeout: float = 5.0) -> Optional[cv2.VideoCapture]:
        """Open camera in a helper thread with a timeout; abandon hung opens (long-run safety)."""
        holder: dict = {}
        def _open():
            try:
                holder["cap"] = cv2.VideoCapture(index, backend)
            except Exception as e:
                holder["err"] = e
        t = threading.Thread(target=_open, daemon=True, name=f"cam-open-{index}")
        t.start()
        t.join(timeout)
        if t.is_alive():
            # Open hung: keep reference; it will be released by GC / release_all
            with self._camera_lock:
                self._stray_caps.append((t, holder))
            _log.warning("Camera %d open timed out after %.1fs (background open abandoned)", index, timeout)
            return None
        return holder.get("cap")

    def _monitor_camera_stall(self, index: int):
        """Looping monitor: reconnect when camera produces no new frame for STALL_SECONDS.

        Long-run design: keeps watching until the camera is released; reconnect retries
        indefinitely (with warning logs) so a dead USB/CSI camera never kills the pipeline.
        """
        interval = max(2.0, CAMERA_STALL_SECONDS / 2)
        while True:
            time.sleep(interval)
            with self._camera_lock:
                entry = self._cameras.get(index)
                if entry is None:
                    return  # camera released
                elapsed = time.time() - entry["last_frame"]
                if elapsed < CAMERA_STALL_SECONDS:
                    continue
                retries = entry["retries"] + 1
                old_cap = entry["cap"]
                self._cameras.pop(index, None)
                _log.warning("Camera %d stalled (%.1fs no frame), reconnecting (attempt %d)",
                             index, elapsed, retries)
            try:
                old_cap.release()
            except Exception:
                pass
            new_cap = None
            for b in self._backend_list(None):
                new_cap = self._open_camera_with_timeout(index, b)
                if new_cap is not None and new_cap.isOpened():
                    break
                if new_cap is not None:
                    try:
                        new_cap.release()
                    except Exception:
                        pass
                    new_cap = None
            if new_cap is not None and new_cap.isOpened():
                with self._camera_lock:
                    self._cameras[index] = {
                        "cap": new_cap,
                        "last_frame": time.time(),
                        "retries": retries,
                    }
                _log.info("Camera %d reconnected after stall", index)
            else:
                if new_cap is not None:
                    try:
                        new_cap.release()
                    except Exception:
                        pass
                _log.error("Camera %d reconnect failed after stall", index)

    def mark_camera_frame(self, index: int):
        """Call this after each successful cap.read() to reset stall timer."""
        with self._camera_lock:
            entry = self._cameras.get(index)
            if entry:
                entry["last_frame"] = time.time()

    def release_camera(self, index: int):
        with self._camera_lock:
            entry = self._cameras.pop(index, None)
            if entry and entry["cap"]:
                try:
                    entry["cap"].release()
                except Exception as e:
                    _log.error("Camera %d release error: %s\n%s", index, e, traceback.format_exc())
                _log.info("Camera %d released", index)

    # ═══════════════════ Video Writer ═══════════════════

    def get_video_writer(self, path: str, fourcc: str, fps: float, size: tuple) -> cv2.VideoWriter:
        with self._writer_lock:
            if path in self._writers:
                return self._writers[path]
            try:
                fourcc_code = cv2.VideoWriter_fourcc(*fourcc)
                writer = cv2.VideoWriter(path, fourcc_code, fps, size)
                if writer.isOpened():
                    self._writers[path] = writer
                    _log.info("VideoWriter opened: %s (%dx%d@%.1ffps)", path, size[0], size[1], fps)
                    return writer
                raise VideoWriterError(f"VideoWriter 打开失败: {path}")
            except Exception as e:
                _log.error("VideoWriter error: %s\n%s", e, traceback.format_exc())
                raise VideoWriterError(f"VideoWriter 创建失败: {e}") from e

    def release_writer(self, path: str):
        with self._writer_lock:
            w = self._writers.pop(path, None)
            if w:
                w.release()
                _log.info("VideoWriter released: %s", path)

    # ═══════════════════ Subprocess with timeout ═══════════════════

    def run_subprocess(self, cmd: list, timeout: int = None) -> subprocess.Popen:
        """Run subprocess with enforced timeout from config. Auto-kills on timeout."""
        if timeout is None:
            from infrastructure.config import get_config
            timeout = get_config().timeout_subprocess

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
        except Exception as e:
            _log.error("Subprocess spawn failed: %s\n%s", cmd[0], traceback.format_exc())
            raise SubprocessTimeoutError(f"子进程启动失败: {cmd[0]}") from e

        with self._proc_lock:
            self._subprocesses.append(proc)

        _log.debug("Subprocess started: %s (pid=%d, timeout=%ds)", cmd[0], proc.pid, timeout)

        # Background timeout watcher
        threading.Thread(
            target=self._watch_subprocess_timeout,
            args=(proc, cmd, timeout), daemon=True, name=f"proc-watch-{proc.pid}"
        ).start()
        return proc

    def _watch_subprocess_timeout(self, proc: subprocess.Popen, cmd: list, timeout: int):
        """Background thread: kill process if it exceeds timeout."""
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _log.error("Subprocess timeout (%ds): %s (pid=%d) — killing", timeout, cmd[0], proc.pid)
            try:
                proc.kill()
                proc.wait(timeout=3)
            except Exception as e:
                _log.error("Kill failed for pid=%d: %s\n%s", proc.pid, e, traceback.format_exc())
            with self._proc_lock:
                if proc in self._subprocesses:
                    self._subprocesses.remove(proc)
            raise SubprocessTimeoutError(
                f"子进程超时 ({timeout}s): {' '.join(cmd[:3])}..."
            )
        except Exception as e:
            _log.error("Subprocess %s error: %s\n%s", cmd[0], e, traceback.format_exc())

    def kill_subprocess(self, proc: subprocess.Popen):
        with self._proc_lock:
            if proc in self._subprocesses:
                self._subprocesses.remove(proc)
        try:
            proc.kill()
            proc.wait(timeout=5)
            _log.info("Subprocess killed: pid=%d", proc.pid)
        except Exception as e:
            _log.error("Kill subprocess pid=%d error: %s\n%s", proc.pid, e, traceback.format_exc())

    # ═══════════════════ Release All ═══════════════════

    def release_all(self):
        """Release all cameras, writers, and subprocesses. Safe to call multiple times."""
        _log.info("Releasing all resources...")
        errors = []

        with self._camera_lock:
            for idx, entry in list(self._cameras.items()):
                try:
                    entry["cap"].release()
                except Exception as e:
                    errors.append(f"camera[{idx}]: {e}")
            self._cameras.clear()

        with self._writer_lock:
            for path, w in list(self._writers.items()):
                try:
                    w.release()
                except Exception as e:
                    errors.append(f"writer[{path}]: {e}")
            self._writers.clear()

        with self._proc_lock:
            for proc in list(self._subprocesses):
                try:
                    proc.kill()
                    proc.wait(timeout=3)
                except Exception as e:
                    errors.append(f"proc[{proc.pid}]: {e}")
            self._subprocesses.clear()

        # Reclaim abandoned camera handles from timed-out opens
        with self._camera_lock:
            alive = []
            for t, holder in self._stray_caps:
                if t.is_alive() or "cap" not in holder:
                    alive.append((t, holder))
                    continue
                try:
                    holder["cap"].release()
                except Exception:
                    pass
            self._stray_caps = alive

        if errors:
            _log.warning("Release had %d errors: %s", len(errors), "; ".join(errors[-5:]))
        else:
            _log.info("All resources released cleanly")


def get_resource_manager() -> ResourceManager:
    return ResourceManager()