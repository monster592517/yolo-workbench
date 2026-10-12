# -*- coding: utf-8 -*-
"""Environment management - detect & install dependencies"""

from __future__ import annotations

import json, subprocess, sys, threading, os
from typing import Callable, Optional

import logging
import os as _os
_os.environ.setdefault("OPENCV_LOG_LEVEL", "OFF")

_log = logging.getLogger(__name__)

from infrastructure.config import get_config, get_project_root
_cfg = get_config()
MIRROR = _cfg.pip_mirror

# ── 项目统一 Python 版本要求 ──
# 自 v0.10.0 起项目统一要求 Python 3.11+ (X-AnyLabeling 官方约束 >=3.11)
# 最低版本集中配置于 config.yaml -> python.min_version
def _load_python_min():
    try:
        raw = _cfg.get("python.min_version", "3.11")
        parts = str(raw).split(".")
        return (int(parts[0]), int(parts[1])), ".".join(parts[:2])
    except Exception:
        return (3, 11), "3.11"

PYTHON_MIN, PYTHON_MIN_STR = _load_python_min()

REQUIRED = ["ultralytics", "opencv-python", "numpy", "torch", "mss", "Pillow", "pyyaml"]
OPTIONAL = ["torchvision", "tkinterdnd2", "psutil", "x-anylabeling-cvhub"]
DESC_MAP = {
    "ultralytics": "YOLO核心", "opencv-python": "OpenCV", "numpy": "NumPy",
    "torch": "PyTorch", "mss": "屏幕捕获", "Pillow": "图像处理", "pyyaml": "YAML解析",
    "torchvision": "视觉扩展",
    "tkinterdnd2": "拖放支持", "psutil": "系统监控",
    "x-anylabeling-cvhub": "X-AnyLabeling标注",
}
# 仅展示、不参与一键安装的包。x-anylabeling-cvhub 仅在 Python<3.11 时排除
# (3.11+ 已支持直接安装, 见训练页右下角安装控制台)
INSTALL_EXCLUDE = set() if sys.version_info >= PYTHON_MIN else {"x-anylabeling-cvhub"}


def _import_name(pkg: str) -> str:
    """Map pip package name → Python import name"""
    mapping = {
        "opencv-python": "cv2",
        "Pillow": "PIL",
        "pyyaml": "yaml",
        "scikit-learn": "sklearn",
        "x-anylabeling-cvhub": "anylabeling",
    }
    name = mapping.get(pkg, pkg.replace("-", "_"))
    return name


def _pip_list_map():
    """单次 pip list 快查 (比逐包子进程 import 快 10 倍以上)"""
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "list", "--format=json"],
            capture_output=True, text=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        if r.returncode == 0:
            return {e.get("name", "").lower().replace("_", "-"): e.get("version", "")
                    for e in json.loads(r.stdout)}
    except Exception:
        pass
    return None


def _pkg_version(pkg: str) -> Optional[str]:
    """包版本 —— **只读元数据, 绝不 import**。

    坑 (2026-09-11, 由审计"飘绿/飘红"暴露):
        原实现起子进程 `python -c "import <name>; print(__version__)"`,
        timeout = config.timeouts.subprocess (默认 8s)。
        而 torch 冷导入在本机实测 **8.7s** —— 于是
        `_pkg_installed("torch")` / `("ultralytics")` 的结果完全取决于
        **磁盘缓存冷热**: 冷 → 子进程超时 → TimeoutExpired 被 except 吞掉 →
        返回 None → 健康检查报 "YOLO: Missing"。
        实测同一份代码同一台机器, 健康检查在 11P/4W/0F 与 11P/3W/1F 之间随机跳。
        另外 6 个包检查 = 6 个重型子进程, 在 14.7GB 机器上就是内存炸弹。
    现在: importlib.metadata 读 dist-info —— 微秒级、不加载任何包、结果确定。
    """
    try:
        import importlib.metadata as _md
        for dist in (pkg, pkg.replace("_", "-")):
            try:
                return _md.version(dist)
            except Exception:
                continue
    except Exception:
        pass
    # 兜底: 有模块但无 dist-info 的包 → 只探存在性, 不导入
    try:
        import importlib.util as _iu
        if _iu.find_spec(_import_name(pkg)) is not None:
            return "ok"
    except Exception:
        pass
    return None

def _pkg_installed(pkg: str) -> bool: return _pkg_version(pkg) is not None


def _pkg_location(pkg: str) -> str:
    """Get install location of a pip package"""
    try:
        name = _import_name(pkg)
        r = subprocess.run(
            [sys.executable, "-c", f"import {name}; print({name}.__file__)"],
            capture_output=True, text=True, timeout=_cfg.timeout_subprocess, creationflags=0x08000000)
        return r.stdout.strip() if r.returncode == 0 else "unknown"
    except Exception:
        return "not found"


def _pkg_size_mb(pkg: str) -> float:
    """Get approximate install size in MB"""
    import shutil
    loc = _pkg_location(pkg)
    if loc == "not found" or loc == "unknown":
        return 0
    try:
        pkg_dir = Path(loc).parent
        if pkg_dir.exists():
            total = sum(f.stat().st_size for f in pkg_dir.rglob("*") if f.is_file())
            return total / (1024 * 1024)
    except Exception:
        pass
    return 0


def _get_memory_usage() -> dict:
    """Get system memory stats"""
    try:
        import psutil
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage(str(get_project_root()))
        return {
            "ram_used_gb": mem.used / (1024**3),
            "ram_total_gb": mem.total / (1024**3),
            "ram_pct": mem.percent,
            "disk_free_gb": disk.free / (1024**3),
            "disk_total_gb": disk.total / (1024**3),
            "disk_pct": disk.percent,
        }
    except ImportError:
        import shutil
        stat = shutil.disk_usage(".")
        return {
            "ram_used_gb": 0, "ram_total_gb": 0, "ram_pct": 0,
            "disk_free_gb": stat.free / (1024**3),
            "disk_total_gb": stat.total / (1024**3),
            "disk_pct": 0,
        }


def _uninstall_pkg(pkg: str) -> tuple:
    """Uninstall a pip package, returns (ok: bool, detail: str)"""
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "uninstall", pkg, "-y"],
            capture_output=True, text=True, timeout=60,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        if r.returncode == 0:
            return True, f"{pkg} 已卸载"
        return False, r.stderr.strip()[-150:] if r.stderr else "失败"
    except Exception as e:
        return False, str(e)[:150]

def detect_py311_exe():
    """通过 py 启动器探测本机可用的 Python 3.11+ 解释器路径 (无则返回 None)"""
    try:
        r = subprocess.run(["py", "-0p"], capture_output=True, text=True, timeout=10,
                           creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        if r.returncode != 0:
            return None
        import re
        for line in r.stdout.splitlines():
            m = re.search(r"-V:(\d+)\.(\d+).*?(\S+python\.exe)", line)
            if m:
                major, minor = int(m.group(1)), int(m.group(2))
                if (major, minor) >= PYTHON_MIN:
                    return m.group(3)
    except Exception:
        pass
    return None


def python_version_report():
    """返回当前 Python 与项目要求对比: (ok, current_str, required_str, suggestion)"""
    py_ver = sys.version_info
    ok = (py_ver.major, py_ver.minor) >= PYTHON_MIN
    current = f"{py_ver.major}.{py_ver.minor}.{py_ver.micro}"
    suggestion = ""
    if not ok:
        exe = detect_py311_exe()
        if exe:
            suggestion = f"检测到本机 Python {PYTHON_MIN_STR}+: {exe}\n请使用 YOLO工作台.bat 或该解释器启动程序"
        else:
            suggestion = "未检测到 Python 3.11+, 请安装 Python 3.11+ 后重试"
    return ok, current, PYTHON_MIN_STR, suggestion


def _run_with_timeout(fn, timeout, default):
    """看门狗: 在独立线程运行 fn, 超时直接返回默认值 (避免摄像头/GPU 探测挂死)

    探测线程一律降为"低于正常"优先级: 它可能在导 torch / 开摄像头 / 枚举设备,
    走的是重活; 降一级后 UI 主线程始终抢占得到 CPU。
    """
    box = [default]

    def _w():
        try:
            from infrastructure.lazy_import import low_priority
            low_priority()
        except Exception:
            pass
        try:
            box[0] = fn()
        except Exception:
            pass
    t = threading.Thread(target=_w, daemon=True)
    t.start()
    t.join(timeout=timeout)
    return box[0]


def _gpu_from_torch():
    """只在 torch **已经躺在内存里**时用它查 GPU —— 绝不触发冷导入。

    返回 (ok, desc) 或 None (无法判定 / torch 尚未加载)。
    """
    t = sys.modules.get("torch")
    if t is None:
        return None
    try:
        if not t.cuda.is_available():
            return False, ""
        props = t.cuda.get_device_properties(0)
        mem = getattr(props, "total_memory", getattr(props, "total_mem", 0))
        n = t.cuda.get_device_name(0)
        return True, f"{n} ({mem / (1024**3):.1f}GB)"
    except Exception:
        return None


def _gpu_from_smi():
    """用 nvidia-smi 拿一个"有没有 GPU"的快速答案 (子进程, 不占 GIL)。"""
    try:
        r = subprocess.run(["nvidia-smi"], capture_output=True, timeout=_cfg.timeout_gpu, creationflags=0x08000000)
        if r.returncode == 0:
            return True, "NVIDIA GPU"
    except Exception:
        pass
    return None


def _check_gpu_impl():
    """GPU 探测 —— 分层取数, 从最便宜的方案开始。

    SPEED (2026-09-11): 原实现第一句就是 `import torch`。torch 冷导入在本机实测
    约 8.7 秒, 而且**导入过程一直攥着 GIL**, 于是本线程虽然只是"后台探测", 却把
    主线程画界面的时间片全抢走 —— 表现为启动后界面僵住十几秒, 页面首建从 ~0.3s
    被拖到 1.5~1.9s。实测: 关掉这个导入后 h3factory 冷建 1920ms -> 273ms。

    现在改成:
        1) torch 已在内存 -> 直接用它 (拿到型号 + 显存, 最好的结果)
        2) 否则 -> nvidia-smi 快速问答 (没有冷导入, 没有 GIL 占用)
        3) 冷导入 torch 的事交给 lazy_import 的**唯一**低优先级后台预热去做,
           等它 ready 之后下一次探测自然升级成详细型号。
    """
    r = _gpu_from_torch()
    if r is not None:
        return r
    r = _gpu_from_smi()
    if r is not None:
        return r
    return False, ""


def check_gpu():
    info = _run_with_timeout(_check_gpu_impl, _cfg.timeout_gpu + 2, (False, ""))
    if not info[0]:
        # 还没拿到 GPU 信息: 让统一的预热线程把 torch 导进来, 下次调用即可升级为
        # 详细型号。这里只"下单", 不等结果 —— 调用方永远不会因此阻塞。
        try:
            from infrastructure.lazy_import import warm_up_async
            warm_up_async()
        except Exception:
            pass
    return info

_CAMERA_NOTE = ""


def _check_camera_impl() -> int:
    """SPEED_OPT: scan first 3 indices only, use CAP_ANY for fast probing.
    附加黑帧检测: 打开成功但画面全黑时记录备注 (物理挡片/开关/驱动)。"""
    global _CAMERA_NOTE
    try:
        import cv2
        count = 0
        for i in range(3):  # SPEED_OPT: 6->3 reduces time by 50%
            cap = cv2.VideoCapture(i, cv2.CAP_ANY)  # SPEED_OPT: CAP_ANY faster than CAP_DSHOW
            if cap.isOpened():
                count += 1
                try:
                    from core.camera_probe import is_black_frame
                    _n = 0
                    for _ in range(2):
                        _ok, _f = cap.read()
                        if _ok and _f is not None and is_black_frame(_f):
                            _n += 1
                    if _n >= 2:
                        _CAMERA_NOTE = "画面全黑(挡片/开关/驱动)"
                    else:
                        _CAMERA_NOTE = ""
                except Exception:
                    _CAMERA_NOTE = ""
                cap.release()
                break  # SPEED_OPT: stop after first found
            cap.release()
        return count
    except Exception:
        _CAMERA_NOTE = ""
        return 0


def camera_note() -> str:
    """最近一次摄像头探测的黑帧备注 (与 check_camera 同源)"""
    return _CAMERA_NOTE


def check_camera() -> int:
    """摄像头探测 — 看门狗超时保护, 防止设备被占用时挂死"""
    return _run_with_timeout(_check_camera_impl, _cfg.timeout_gpu, 0)


class Environment:
    def __init__(self):
        self._cancel = False

    def cancel(self): self._cancel = True

    def scan(self, on_check=None) -> dict:
        """Scan environment with real-time per-package callback.
        on_check(package_name, installed: bool, version: str, progress_pct: float)
        """
        py_ver = sys.version_info
        r = {"python": sys.version.split()[0],
             "python_ok": (py_ver.major, py_ver.minor) >= PYTHON_MIN,
             "python_required": PYTHON_MIN_STR,
             "gpu": check_gpu(), "cameras": check_camera(), "camera_note": camera_note(),
             "memory": _get_memory_usage(), "packages": {}}
        pip_map = _pip_list_map()
        all_pkgs = list(REQUIRED) + list(OPTIONAL)
        total = len(all_pkgs)
        for i, p in enumerate(all_pkgs):
            norm = p.lower().replace("_", "-")
            if pip_map is not None and norm in pip_map:
                v = pip_map[norm]
                installed = True
            else:
                # 兜底: pip list 查不到时再用 import 验证
                v = _pkg_version(p)
                installed = v is not None
            r["packages"][p] = {"installed": installed, "version": v or "N/A", "desc": DESC_MAP.get(p, p)}
            if on_check:
                on_check(p, installed, v or "N/A", (i + 1) / total * 100)
        return r
    def install_all(self, on_item=None, on_status=None):
        """Install missing packages with per-package progress.
        Returns (all_ok: bool, installed_count: int)
        on_item(pkg_name, status, pct, msg='') — status: 'installing'|'done'|'failed'|'skip'
        """
        self._cancel = False
        all_pkgs = list(REQUIRED) + list(OPTIONAL)
        missing = [p for p in all_pkgs if p not in INSTALL_EXCLUDE and not _pkg_installed(p)]
        if not missing:
            if on_status: on_status(100, "全部已安装!")
            if on_item:
                for pkg in all_pkgs:
                    on_item(pkg, "skip", 100, "")
            return True, 0

        has_gpu, _ = check_gpu()
        torch_idx = "https://download.pytorch.org/whl/cu121" if has_gpu else "https://download.pytorch.org/whl/cpu"
        total = len(missing)
        installed_count = 0
        i = 0
        while i < len(missing):
            pkg = missing[i]
            if self._cancel: break
            desc = DESC_MAP.get(pkg, pkg)
            pct = (i + 0.5) / total * 100
            if on_item: on_item(pkg, "installing", pct, "")

            if pkg == "torch":
                rc, err = self._stream([sys.executable, "-m", "pip", "install", "torch", "torchvision",
                                        "--index-url", torch_idx])
                if "torchvision" in missing:
                    missing.remove("torchvision"); total = len(missing)
            else:
                rc, err = self._stream([sys.executable, "-m", "pip", "install", pkg, "-i", MIRROR])

            ok = _pkg_installed(pkg)
            done_pct = (i + 1) / total * 100
            msg = ""
            if ok:
                installed_count += 1
            else:
                msg = err or f"安装失败 (exit={rc})"
            if on_item: on_item(pkg, "done" if ok else "failed", done_pct, msg)
            i += 1

        if on_status: on_status(100, "安装完成!")
        return installed_count > 0, installed_count

    def _stream(self, cmd, timeout=None):
        """Run subprocess, return (returncode, stderr_tail). Logs errors to runs/logs/install_errors.log"""
        if timeout is None: timeout = _cfg.timeout_pip
        try:
            env = None
            if _cfg.pip_no_proxy:
                env = dict(os.environ)
                env["NO_PROXY"] = "*"; env["no_proxy"] = "*"
                for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
                    env.pop(_k, None)
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, env=env,
                                     creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
            _, stderr = proc.communicate(timeout=timeout)
            err = ""
            if proc.returncode != 0 and stderr:
                lines = [l.strip() for l in stderr.splitlines() if l.strip()]
                err = lines[-1] if lines else ""
                # Write full error to log file
                try:
                    log_dir = _cfg.log_dir
                    log_dir.mkdir(parents=True, exist_ok=True)
                    with open(log_dir / "install_errors.log", "a", encoding="utf-8") as f:
                        f.write(f"[{__import__('datetime').datetime.now():%Y-%m-%d %H:%M:%S}] cmd: {' '.join(cmd)}\n")
                        f.write(stderr)
                        f.write("\n---\n")
                except: pass
            return proc.returncode, err[-200:] if err else ""
        except subprocess.TimeoutExpired:
            proc.kill(); return -1, "install timeout"
        except Exception as e:
            return -1, str(e)[:200]


_env_instance: Optional[Environment] = None
def get_environment() -> Environment:
    global _env_instance
    if _env_instance is None: _env_instance = Environment()
    return _env_instance
