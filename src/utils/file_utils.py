# -*- coding: utf-8 -*-
"""
平台检测 & 系统工具
支持: Windows, Linux, macOS, Jetson (ARM64)
"""

import platform
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple

# ── 系统识别 ──
IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform == "linux"
IS_MACOS = sys.platform == "darwin"

_system = platform.system()
_arch = platform.machine().lower()
JETSON_MODEL: Optional[str] = None
IS_JETSON = False
IS_YABO = False  # 亚博智能 Jetson Nano B01


def _detect_jetson():
    global IS_JETSON, JETSON_MODEL, IS_YABO
    if not IS_LINUX:
        return
    if _arch not in ("aarch64", "armv7l"):
        return
    model_path = Path("/proc/device-tree/model")
    if model_path.exists():
        try:
            model = model_path.read_text().strip("\x00").strip()
            if "jetson" in model.lower() or "tegra" in model.lower():
                IS_JETSON = True
                JETSON_MODEL = model
                if "nano" in model.lower():
                    IS_YABO = True
        except Exception:
            pass


_detect_jetson()


def get_system_info() -> dict:
    return {
        "system": _system,
        "arch": _arch,
        "python": sys.version.split()[0],
        "is_windows": IS_WINDOWS,
        "is_linux": IS_LINUX,
        "is_macos": IS_MACOS,
        "is_jetson": IS_JETSON,
        "jetson_model": JETSON_MODEL,
        "is_jetson_nano": IS_YABO,
    }


def get_default_device() -> str:
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass
    if IS_JETSON:
        try:
            sp = subprocess.run(
                [sys.executable, "-c", "import torch; print(torch.cuda.is_available())"],
                capture_output=True, text=True, timeout=10
            )
            if "True" in sp.stdout:
                return "cuda"
        except Exception:
            pass
    return "cpu"


def get_camera_backend() -> int:
    try:
        import cv2
    except ImportError:
        return 0
    if IS_WINDOWS:
        return cv2.CAP_DSHOW
    if IS_JETSON:
        return cv2.CAP_V4L2
    return cv2.CAP_ANY


def detect_csi_camera() -> Optional[str]:
    if not IS_LINUX:
        return None
    for dev in ["/dev/video0", "/dev/video1"]:
        if Path(dev).exists():
            return dev
    return None


def get_pip_mirror() -> str:
    # 统一使用 config.yaml 的 mirrors.pip (默认官方源, 绕代理由配置控制)
    try:
        from infrastructure.config import get_config
        return get_config().pip_mirror
    except Exception:
        return "https://pypi.org/simple"


def get_torch_index() -> str:
    if IS_JETSON:
        return "https://download.pytorch.org/whl/cpu"
    return "https://download.pytorch.org/whl/cu121"


def open_file_explorer(path: Path):
    p = str(path)
    if IS_WINDOWS:
        subprocess.Popen(["explorer", p])
    elif IS_MACOS:
        subprocess.Popen(["open", p])
    elif IS_LINUX:
        subprocess.Popen(["xdg-open", p])


def open_browser(url: str):
    import webbrowser
    webbrowser.open(url)


def parse_dnd_paths(data: str) -> list:
    """解析 tkinterdnd2 拖拽事件路径 (Windows: 支持 {带空格路径} 与裸路径)。

    不能用 tk.splitlist(): 它会吃掉 Windows 反斜杠 (C:\\Users -> C:Users)。
    """
    import re
    out = []
    for m in re.finditer(r"\{([^}]*)\}|(\S+)", data or ""):
        part = (m.group(1) or m.group(2) or "").strip()
        if part:
            out.append(part.replace("/", "\\"))
    return out
