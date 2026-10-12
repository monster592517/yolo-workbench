# -*- coding: utf-8 -*-
"""
检测引擎 v2 - 高性能队列化管道
特性: 帧跳帧, 线程安全, 资源自动管理, 反压控制
"""

from __future__ import annotations
import logging

import os, sys
import queue, time, threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

import numpy as np

# 抑制 OpenCV DSHOW/MSMF 后端无关警告
os.environ.setdefault("OPENCV_LOG_LEVEL", "FATAL")
os.environ.setdefault("OPENCV_VIDEOIO_LOG_LEVEL", "FATAL")

try:
    import cv2
    _CV2_OK = True
except ImportError:
    cv2 = None
    _CV2_OK = False

# ── ultralytics 惰性导入 (启动提速, 2026-09-11) ──
# 原为模块顶层 `from ultralytics import YOLO`, 连带 import torch (冷导入 ≈9.9s),
# 把启动画面卡死。改为惰性代理, 首次实例化模型时才真正导入。
from infrastructure.lazy_import import (           # noqa: E402
    ultralytics_available as _ultra_available,
    yolo_class as _yolo_class,
)

HAS_ULTRA = _ultra_available()


class _LazyYOLO:
    """YOLO 惰性代理 — `YOLO(path)` 用法不变。"""

    __slots__ = ()

    def __call__(self, *args, **kwargs):
        cls = _yolo_class()
        if cls is None:
            raise ImportError("ultralytics 不可用 (未安装或导入失败)")
        return cls(*args, **kwargs)

    def __bool__(self):
        return HAS_ULTRA


YOLO = _LazyYOLO()

from infrastructure.config import SourceType, get_config
from infrastructure.diagnostics import (
    InferenceError, SourceError, FrameCaptureError,
    ModelLoadError, CameraNotFoundError, ShutdownSignal, ResourceBusyError,
)
from infrastructure.diagnostics import get_logger
from utils.file_utils import get_camera_backend, IS_WINDOWS
from utils.text_utils import ensure_pt_file

_log = logging.getLogger(__name__)
_cfg = get_config()

QUEUE_DEPTH = 4  # 帧队列深度(反压控制)
DISPLAY_SKIP = 2  # 每N帧投递一帧到UI
CAMERA_RECONNECT_AFTER = 15  # 摄像头连续失败帧数, 达到后强制重开设备


@dataclass
class DetectionFrame:
    """检测结果帧 - 包含原始帧和标注帧"""
    raw: Optional[np.ndarray] = None
    annotated: Optional[np.ndarray] = None
    fps: float = 0.0
    timestamp: float = field(default_factory=time.time)


class FrameSource:
    """帧源抽象 - 统一摄像头/桌面/窗口/视频/图片访问"""

    def __init__(self, source_type: SourceType, source_value: Any,
                 mirror: bool = False):
        self._type = source_type
        self._value = source_value
        self._mirror = mirror
        self._cap: Optional[cv2.VideoCapture] = None
        self._sct = None  # mss instance
        self._monitor: Optional[dict] = None
        self._sct_lib = None
        self._camera_index: Optional[int] = None
        self._consecutive_fail = 0

    def open(self) -> "FrameSource":
        backend = get_camera_backend()
        if self._type == SourceType.CAMERA:
            idx = int(self._value) if str(self._value).isdigit() else 0
            self._camera_index = idx
            from infrastructure.resource_manager import get_resource_manager
            self._cap = get_resource_manager().get_camera(idx, backend)
            if not self._cap.isOpened():
                raise CameraNotFoundError(f"摄像头 {idx} 不可用")
            # 黑帧验证: 打开成功但画面全黑 (物理挡片/开关/驱动) 时给出明确报错
            try:
                from core.camera_probe import is_black_frame
                _black_n = 0
                for _ in range(2):
                    _ok, _f = self._cap.read()
                    if not _ok or _f is None:
                        _black_n += 1
                        continue
                    if is_black_frame(_f):
                        _black_n += 1
                if _black_n >= 2:
                    raise CameraNotFoundError(
                        f"摄像头 {idx} 画面全黑: 请检查物理隐私挡片/摄像头开关(F键)/"
                        f"设备管理器驱动, 或用系统相机App验证")
            except CameraNotFoundError:
                raise
            except Exception:
                pass

        elif self._type == SourceType.VIDEO:
            import os
            if not isinstance(self._value, str):
                raise SourceError("视频路径无效")
            if not os.path.exists(self._value):
                raise SourceError(f"视频文件不存在: {self._value}")
            self._cap = cv2.VideoCapture(self._value)
            if not self._cap.isOpened():
                raise SourceError(f"无法打开视频: {self._value}")

        elif self._type == SourceType.DESKTOP:
            import mss
            self._sct = mss.mss()
            self._monitor = self._sct.monitors[1]
            self._sct_lib = mss

        elif self._type == SourceType.WINDOW:
            self._setup_window()
        return self

    def _setup_window(self):
        if not IS_WINDOWS:
            raise SourceError("窗口捕获仅支持Windows")
        import mss
        self._sct_lib = mss
        self._sct = mss.mss()
        self._monitor = self._value if isinstance(self._value, dict) else None

    def read(self) -> Optional[np.ndarray]:
        """读取一帧, 失败返回 None"""
        if self._cap is not None:
            if self._camera_index is not None:
                return self._read_camera()
            ret, frame = self._cap.read()
            return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB) if ret else None

        if self._sct is not None:
            try:
                if self._type == SourceType.WINDOW and IS_WINDOWS:
                    self._update_window_rect()
                frame = np.array(self._sct.grab(self._monitor))
                return cv2.cvtColor(frame, cv2.COLOR_BGRA2RGB)
            except Exception:
                return None
        return None

    def _read_camera(self) -> Optional[np.ndarray]:
        """摄像头读取 (长稳): 成功帧刷新断流计时; 连续失败强制重开设备。"""
        from infrastructure.resource_manager import get_resource_manager
        rm = get_resource_manager()
        try:
            ret, frame = self._cap.read()
            if ret and frame is not None:
                try:
                    from core.camera_probe import is_black_frame
                    if is_black_frame(frame):
                        self._consecutive_fail += 1
                        if not getattr(self, "_black_warned", False):
                            self._black_warned = True
                            _log.warning("FrameSource camera %d: 画面全黑(挡片/开关/驱动?)",
                                         self._camera_index)
                            try:
                                from infrastructure.audit import audit
                                audit("camera_black_stream", f"摄像头 {self._camera_index} 输出全黑帧",
                                      level="warn", module="detector")
                            except Exception:
                                pass
                        return None
                except Exception:
                    pass
                self._consecutive_fail = 0
                self._black_warned = False
                rm.mark_camera_frame(self._camera_index)
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                # 自拍摄像头默认镜像 (内置/前置摄像头画面左右翻转)
                if self._mirror:
                    try:
                        rgb = cv2.flip(rgb, 1)
                    except Exception:
                        pass
                return rgb
            self._consecutive_fail += 1
            if self._consecutive_fail >= CAMERA_RECONNECT_AFTER:
                _log.warning("FrameSource camera %d: %d consecutive failures, forcing reopen",
                             self._camera_index, self._consecutive_fail)
                rm.release_camera(self._camera_index)
                try:
                    self._cap = rm.get_camera(self._camera_index, get_camera_backend())
                except Exception as e:
                    _log.error("FrameSource camera reopen failed: %s", e)
                    self._cap = None
                self._consecutive_fail = 0
            return None
        except Exception as e:
            _log.error("FrameSource camera read error: %s", e)
            return None

    def _update_window_rect(self):
        if not IS_WINDOWS or self._type != SourceType.WINDOW:
            return
        try:
            import ctypes
            from ctypes import wintypes
            hwnd = self._value[0] if isinstance(self._value, tuple) else None
            if hwnd is None:
                return
            rect = wintypes.RECT()
            if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                w, h = rect.right - rect.left, rect.bottom - rect.top
                if w > 10 and h > 10:
                    self._monitor = {"top": rect.top, "left": rect.left, "width": w, "height": h}
        except Exception:
            pass

    def reset(self):
        """视频/摄像头复位"""
        if self._cap is not None:
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    def close(self):
        if self._camera_index is not None:
            from infrastructure.resource_manager import get_resource_manager
            get_resource_manager().release_camera(self._camera_index)
            self._cap = None
        elif self._cap is not None:
            self._cap.release()
            self._cap = None
        self._sct = None

    @property
    def is_live(self) -> bool:
        return self._type in (SourceType.CAMERA, SourceType.DESKTOP, SourceType.WINDOW)

    @property
    def source_type(self) -> SourceType:
        return self._type


class VideoRecorder:
    """录制器 - 独立线程写入, 避免阻塞推理"""

    def __init__(self, output_path: str, fps: float = 20.0, codec: str = "mp4v"):
        self._path = output_path
        self._fps = fps
        self._codec = codec
        self._writer: Optional[cv2.VideoWriter] = None
        self._lock = threading.Lock()
        self._frame_count = 0

    def start(self, frame_shape: Tuple[int, int]):
        h, w = frame_shape[:2]
        from infrastructure.resource_manager import get_resource_manager
        self._writer = get_resource_manager().get_video_writer(self._path, self._codec, self._fps, (w, h))

    def write(self, frame: np.ndarray):
        with self._lock:
            if self._writer is not None and frame is not None:
                if frame.shape[2] == 3 and isinstance(frame, np.ndarray):
                    bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                    self._writer.write(bgr)
                    self._frame_count += 1

    def stop(self) -> int:
        with self._lock:
            if self._writer is not None:
                self._writer.release()
                self._writer = None
            return self._frame_count

    @property
    def is_recording(self) -> bool:
        return self._writer is not None


class DetectionPipeline:
    """
    高性能检测管道
    ┌─────────┐   ┌──────────┐   ┌────────────┐   ┌──────┐
    │FrameSrc │ → │ 模型推理  │ → │ 帧跳帧队列│ → │UI渲染 │
    └─────────┘   └──────────┘   └────────────┘   └──────┘
    """

    def __init__(self, model_path: str, conf: float = None, device: str = None,
                 mirror: bool = False):
        if conf is None: conf = _cfg.detection_conf
        if device is None: device = _cfg.detection_device
        try:
            from infrastructure.device import resolve_best_device
            device = resolve_best_device(device)
        except Exception:
            pass
        self._model_path = ensure_pt_file(model_path)
        self._conf = conf
        self._device = device
        self._model: Any = None
        self._source: Optional[FrameSource] = None
        self._mirror = mirror
        self._recorder: Optional[VideoRecorder] = None
        self._running = False
        self._paused = False
        self._display_queue: queue.Queue = queue.Queue(maxsize=QUEUE_DEPTH)
        self._frame_count = 0
        self._on_frame: Optional[Callable] = None
        self._on_status: Optional[Callable] = None

    def load_model(self):
        if not HAS_ULTRA:
            raise ModelLoadError("ultralytics 未安装")
        self._model = YOLO(str(self._model_path))
        _log.info(f"Model loaded: {self._model_path.name}")

    def set_source(self, source_type: SourceType, source_value: Any):
        self._source = FrameSource(source_type, source_value,
                                   mirror=self._mirror).open()
        _log.info(f"Source opened: {source_type.value}")

    def set_callbacks(self, on_frame: Optional[Callable] = None,
                      on_status: Optional[Callable] = None):
        self._on_frame = on_frame
        self._on_status = on_status

    def start(self):
        if self._running:
            raise ResourceBusyError("检测已在运行")
        if self._model is None:
            self.load_model()
        if self._source is None:
            raise SourceError("未设置检测源")
        self._running = True
        threading.Thread(target=self._capture_loop, daemon=True, name="capture").start()
        threading.Thread(target=self._render_loop, daemon=True, name="render").start()
        _log.info("Detection pipeline started")

    def _capture_loop(self):
        """帧捕获线程 (长稳加固)

        - 单帧推理异常隔离, 不会中断整个管道
        - 摄像头断流由 FrameSource 自动重连
        - 自适应节流 + 周期 GC, 长时间高强度运行不膨胀
        """
        import gc
        skip_counter = 0
        fps_time = time.time()
        fps_count = 0
        current_fps = 0.0
        inf_errors = 0
        empty_reads = 0
        frames_since_gc = 0

        try:
            while self._running:
                if self._paused:
                    time.sleep(0.1)
                    continue

                t0 = time.time()
                frame = self._source.read()
                if frame is None:
                    if not self._source.is_live:
                        break
                    empty_reads += 1
                    if empty_reads == 300:
                        self._emit_status("检测源持续无帧 (自动重连中)...", "error")
                        empty_reads = 0
                    time.sleep(0.05)
                    continue
                empty_reads = 0

                # 推理 (异常隔离: 单帧失败不中断管道)
                try:
                    if self._model is not None:
                        results = self._model(frame, conf=self._conf, verbose=False)
                        annotated = results[0].plot()
                    else:
                        annotated = frame
                    inf_errors = 0
                except Exception as e:
                    inf_errors += 1
                    _log.warning("Inference error (%d): %s", inf_errors, e)
                    if inf_errors == 1:
                        self._emit_status(f"推理异常: {e}", "error")
                    if inf_errors > 20:
                        _log.error("Inference failing repeatedly, stopping pipeline")
                        self._emit_status(f"推理连续失败, 检测停止: {e}", "error")
                        break
                    time.sleep(0.2)
                    continue

                # 录制 (录制器失败不影响检测)
                try:
                    if self._recorder and self._recorder.is_recording:
                        self._recorder.write(annotated)
                except Exception as e:
                    _log.warning("Recorder write error: %s", e)

                # 帧跳帧: 每 DISPLAY_SKIP 帧投递一次到渲染队列
                skip_counter += 1
                fps_count += 1
                if skip_counter >= DISPLAY_SKIP:
                    skip_counter = 0
                    try:
                        self._display_queue.put_nowait(
                            DetectionFrame(raw=frame, annotated=annotated, fps=current_fps)
                        )
                    except queue.Full:
                        pass  # 丢弃帧, UI跟不上

                # FPS 计算
                if time.time() - fps_time >= 1.0:
                    current_fps = fps_count
                    fps_count = 0
                    fps_time = time.time()

                # 周期 GC: 每 300 帧回收一次, 防内存膨胀
                frames_since_gc += 1
                if frames_since_gc >= 300:
                    frames_since_gc = 0
                    gc.collect()

                # 自适应节流: 实时源约 50fps 上限, 避免满载过热
                if self._source.is_live:
                    dt = time.time() - t0
                    if dt < 0.02:
                        time.sleep(0.02 - dt)

        except ShutdownSignal:
            pass
        except Exception as e:
            _log.exception(f"Capture error: {e}")
            self._emit_status(f"捕获异常: {e}", "error")
        finally:
            try:
                self._display_queue.put(ShutdownSignal())
            except Exception:
                pass

    def _render_loop(self):
        """UI 渲染线程"""
        while self._running:
            try:
                item = self._display_queue.get(timeout=0.05)
                if isinstance(item, ShutdownSignal):
                    break
                if self._on_frame and item.annotated is not None:
                    self._on_frame(item.annotated, item.fps)
            except queue.Empty:
                continue
            except ShutdownSignal:
                break
            except Exception:
                pass

    def stop(self):
        if not self._running and self._source is None:
            return
        self._running = False
        if self._source:
            try:
                self._source.close()
            except Exception as e:
                _log.warning("Source close error: %s", e)
        if self._recorder:
            try:
                self._recorder.stop()
            except Exception as e:
                _log.warning("Recorder stop error: %s", e)
        self._emit_status("检测已停止", "info")
        _log.info("Detection pipeline stopped")

    def pause(self, paused: bool):
        self._paused = paused

    def toggle_pause(self) -> bool:
        self._paused = not self._paused
        return self._paused

    def start_recording(self, output_path: str):
        self._recorder = VideoRecorder(output_path, _cfg.recording_fps)
        self._emit_status("录制中...", "recording")

    def stop_recording(self) -> int:
        if self._recorder:
            count = self._recorder.stop()
            self._recorder = None
            self._emit_status(f"已保存 ({count}帧)", "info")
            return count
        return 0

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def is_recording(self) -> bool:
        return self._recorder is not None and self._recorder.is_recording

    def _emit_status(self, msg: str, tag: str = "info"):
        if self._on_status:
            self._on_status(msg, tag)

    def run_image(self, image_path: str) -> Optional[np.ndarray]:
        path = ensure_pt_file(image_path) if image_path.endswith(".pt") else Path(image_path)
        frame = cv2.imread(str(path))
        if frame is None:
            raise SourceError(f"无法读取图片: {image_path}")
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        if self._model is None:
            self.load_model()
        results = self._model(frame, conf=self._conf, verbose=False)
        return results[0].plot()


# ── 便捷函数 ──

def scan_cameras(max_check: int = 6) -> list:
    if not _CV2_OK:
        return []
    cams = []
    backend = get_camera_backend()
    for idx in range(max_check):
        try:
            cap = cv2.VideoCapture(idx, backend)
            if cap.isOpened():
                name = f"Camera {idx}"
                try:
                    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    if w > 0 and h > 0:
                        name = f"Camera {idx} ({w}x{h})"
                except Exception:
                    pass
                # 黑帧状态检测 (最多2帧)
                try:
                    from core.camera_probe import is_black_frame
                    _n = 0
                    for _ in range(2):
                        _ok, _f = cap.read()
                        if _ok and _f is not None and is_black_frame(_f):
                            _n += 1
                    if _n >= 2:
                        name += " (黑帧)"
                except Exception:
                    pass
                cams.append((str(idx), name))
            cap.release()
        except Exception:
            continue
    return cams


def enum_windows() -> list:
    if not IS_WINDOWS:
        return []
    import ctypes
    from ctypes import wintypes
    wins = []
    user32 = ctypes.windll.user32
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            t = buf.value.strip()
            if t and t not in ("", "Program Manager"):
                rect = wintypes.RECT()
                if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                    w, h = rect.right - rect.left, rect.bottom - rect.top
                    if w > 100 and h > 100:
                        wins.append((hwnd, t, rect.left, rect.top, w, h))
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    return wins
