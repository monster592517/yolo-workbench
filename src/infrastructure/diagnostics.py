# -*- coding: utf-8 -*-
"""Diagnostics: LAYER_1_FAST pip list + LAYER_2_DEEP subprocess verification"""

from __future__ import annotations

import json, os, sys, threading, time, traceback
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
# 程序自检加固: 任何入口调用本模块时自动补齐 src 路径, 避免模块探测误报
_SRC_DIR = _PROJECT_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))
_LOG_DIR = _PROJECT_ROOT / "runs" / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_MAX_BUFFER, _MAX_FILE_SIZE, _MAX_BACKUPS = 3000, 10*1024*1024, 5


def get_project_root() -> Path:
    """当前项目根目录 (可被路径中心动态切换)"""
    return _PROJECT_ROOT


def set_project_root(root) -> None:
    """路径中心联动: 切换自检系统的根目录/日志目录/CUDA 缓存路径 (幂等)"""
    global _PROJECT_ROOT, _SRC_DIR, _LOG_DIR, _CUDA_CACHE_PATH
    _PROJECT_ROOT = Path(root).resolve()
    _SRC_DIR = _PROJECT_ROOT / "src"
    _LOG_DIR = _PROJECT_ROOT / "runs" / "logs"
    _CUDA_CACHE_PATH = _PROJECT_ROOT / "runs" / ".cuda_cache.json"
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


# ============== Logger (compact) ==============

class Level(IntEnum):
    TRACE=0;DEBUG=1;INFO=2;WARN=3;ERROR=4;FATAL=5
    @property
    def tag(self): return {0:"TRACE",1:"DEBUG",2:"INFO",3:"WARN",4:"ERROR",5:"FATAL"}[self]

@dataclass
class LogRecord:
    timestamp:datetime=field(default_factory=datetime.now); level:Level=Level.INFO
    module:str=""; message:str=""; exc_info:Optional[str]=None; extra:Dict[str,Any]=field(default_factory=dict)
    def fmt(self)->str: return f"{self.timestamp:%Y-%m-%d %H:%M:%S.%f}"[:-3]+f" [{self.level.tag:5s}] [{self.module:>12s}] {self.message}"

class LogRouter:
    def __init__(s):
        s._l=threading.Lock(); s._b=deque(maxlen=_MAX_BUFFER); s._ls=[]; s._fl=threading.Lock()
    @property
    def _fp(s): return _LOG_DIR/f"yolo_{datetime.now():%Y%m%d}.log"
    def _rot(s):
        p=s._fp
        if p.exists() and p.stat().st_size>_MAX_FILE_SIZE:
            for i in range(_MAX_BACKUPS-1,0,-1):
                o=p.parent/f"{p.stem}.{i}{p.suffix}"; n=p.parent/f"{p.stem}.{i+1}{p.suffix}"
                if o.exists(): o.replace(n)
            p.rename(p.parent/f"{p.stem}.1{p.suffix}")
    def emit(s,r):
        with s._l: s._b.append(r)
        s._wf(r)
        for cb in s._ls:
            try: cb(r)
            except: pass
    def _wf(s,r):
        try:
            s._rot()
            with s._fl:
                with open(s._fp,"a",encoding="utf-8") as f: f.write(r.fmt()+"\n")
                if r.exc_info: f.write(r.exc_info+"\n")
        except: pass
    def sub(s,cb): s._ls.append(cb)
    def unsub(s,cb):
        try: s._ls.remove(cb)
        except ValueError: pass
    def recent(s,n=200):
        with s._l: return list(s._b)[-n:]
    def export(s):
        p=s._fp; return p if p.exists() else None

_rt=LogRouter()

class Logger:
    def __init__(s,m=""): s._m=m
    def _log(s,lv,msg,exc=None):
        es=None
        if exc: es="".join(traceback.format_exception(type(exc),exc,exc.__traceback__))
        elif lv>=Level.ERROR and sys.exc_info()[0]: es=traceback.format_exc()
        _rt.emit(LogRecord(level=lv,module=s._m,message=msg,exc_info=es))
    def trace(s,msg): s._log(Level.TRACE,msg)
    def debug(s,msg): s._log(Level.DEBUG,msg)
    def info(s,msg): s._log(Level.INFO,msg)
    def warn(s,msg): s._log(Level.WARN,msg)
    def error(s,msg,exc=None): s._log(Level.ERROR,msg,exc)
    def fatal(s,msg,exc=None): s._log(Level.FATAL,msg,exc)

def get_logger(m=""):
    if not m:
        import inspect; f=inspect.currentframe()
        if f and f.f_back: m=f.f_back.f_globals.get("__name__","")
    return Logger(m)
def subscribe(cb): _rt.sub(cb)
def unsubscribe(cb): _rt.unsub(cb)
def get_recent(n=200): return _rt.recent(n)
def export_log(): return _rt.export()


# ============== Exceptions ==============

class YoloWorkbenchError(Exception):
    code="E000"
    def __init__(s,d="",*,cause=None):
        s.detail=d or getattr(s.__class__,'message',"Unknown"); s.cause=cause
        super().__init__(f"[{s.code}] {s.detail}")

class ConfigError(YoloWorkbenchError): code="E001"
class EnvironmentError(YoloWorkbenchError): code="E010"
class GPUNotFoundError(EnvironmentError): code="E011"
class CameraNotFoundError(EnvironmentError): code="E012"
class ModelLoadError(YoloWorkbenchError): code="E020"
class TrainingError(YoloWorkbenchError): code="E030"
class DatasetError(TrainingError): code="E031"
class InferenceError(YoloWorkbenchError): code="E040"
class SourceError(InferenceError): code="E041"
class FrameCaptureError(InferenceError): code="E042"
class DeployError(YoloWorkbenchError): code="E050"
class PlatformNotSupportedError(DeployError): code="E051"
class NetworkError(YoloWorkbenchError): code="E060"
class TransferError(NetworkError): code="E061"
class AnnotationError(YoloWorkbenchError): code="E070"
class ValidationError(YoloWorkbenchError): code="E080"
class ResourceBusyError(YoloWorkbenchError): code="E090"
class ShutdownSignal(Exception): pass
class CameraError(YoloWorkbenchError): code="E100"
class SubprocessTimeoutError(YoloWorkbenchError): code="E101"
class VideoWriterError(YoloWorkbenchError): code="E102"


# ============== LAYER_1_FAST: pip list (subprocess, <1s) ==============

TARGET_PKGS = {
    "ultralytics": "YOLO", "opencv-python": "OpenCV", "numpy": "NumPy",
    "torch": "PyTorch", "mss": "Screen", "Pillow": "PIL",
    "pyyaml": "YAML", "torchvision": "Vision",
    "tkinterdnd2": "DnD", "psutil": "psutil",
    "x-anylabeling-cvhub": "标注工具",
}

DEEP_VERIFY_PKGS = ["torch", "opencv-python"]  # LAYER_2_DEEP: verify via import


def _pip_list_json():
    """LAYER_1_FAST: run pip list --format=json in subprocess, parse results."""
    try:
        r = __import__('subprocess').run(
            [sys.executable, "-m", "pip", "list", "--format=json"],
            capture_output=True, text=True, timeout=5,
            creationflags=__import__('subprocess').CREATE_NO_WINDOW if sys.platform=="win32" else 0)
        if r.returncode == 0:
            return json.loads(r.stdout)
    except: pass
    return None


def _fallback_dir_scan():
    """LAYER_1_FALLBACK: if pip list fails, scan site-packages folders."""
    import site
    DIR_ALIAS = {"pyyaml": "yaml"}  # PyYAML 的导入目录是 yaml/
    results = {}
    for sp in site.getsitepackages():
        sp = Path(sp)
        for pkg in TARGET_PKGS:
            if pkg in results:
                continue
            names = {pkg, pkg.replace("-", "_"), DIR_ALIAS.get(pkg.lower(), pkg)}
            if any((sp / n).exists() for n in names):
                results[pkg] = "installed"
    return results



# ============== CUDA Cache (24h TTL, no subprocess on re-check) ==============

_CUDA_CACHE_PATH = _PROJECT_ROOT / "runs" / ".cuda_cache.json"
_CUDA_CACHE_TTL = 24 * 3600  # 24 hours


def read_cuda_cache():
    """Read CUDA status from cache file. Returns dict or None if expired/missing."""
    try:
        if _CUDA_CACHE_PATH.exists():
            data = json.loads(_CUDA_CACHE_PATH.read_text(encoding="utf-8"))
            age = time.time() - data.get("_ts", 0)
            if age < _CUDA_CACHE_TTL:
                if data.get("available"):
                    return {"status": "ok",
                            "detail": f"CUDA {data.get('version','?')} (cached)",
                            "name": "CUDA", "cached": True}
                return {"status": "warn",
                        "detail": "CUDA 不可用 (cached, CPU模式)",
                        "name": "CUDA", "cached": True}
            return {"status": "stale", "detail": f"CUDA cached (expired)",
                    "name": "CUDA", "cached": True, "_stale": True}
    except: pass
    return None


def refresh_cuda_cache_async():
    """Low-priority background thread: runs subprocess to update .cuda_cache.json.
    Returns immediately. UI updates happen via queue if provided."""
    import subprocess as _sp
    def _do():
        try:
            r = _sp.run(
                [sys.executable, "-c",
                 "import torch; ok=torch.cuda.is_available(); "
                 "name=torch.cuda.get_device_name(0) if ok else 'CPU'; "
                 "cu=torch.version.cuda or '?'"],
                capture_output=True, text=True, timeout=10,
                creationflags=_sp.CREATE_NO_WINDOW if sys.platform=="win32" else 0)
            if r.returncode != 0 or not r.stdout.strip():
                get_logger().warn(f"CUDA probe failed (rc={r.returncode}), keep old cache")
                return
            stdout = r.stdout.strip()
            if "True" in stdout:
                parts = stdout.splitlines()
                data = {"_ts": time.time(), "available": True,
                        "version": parts[-1].strip() if parts else "?"}
                _CUDA_CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                return
            # 确实无 GPU: 保留上次状态, 仅当旧缓存不存在或已过期才写
            old = {}
            if _CUDA_CACHE_PATH.exists():
                try:
                    old = json.loads(_CUDA_CACHE_PATH.read_text(encoding="utf-8"))
                except Exception:
                    old = {}
            if not old.get("available"):
                _CUDA_CACHE_PATH.write_text(
                    json.dumps({"_ts": time.time(), "available": False, "version": "?"},
                               ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    t = threading.Thread(target=_do, daemon=True, name="cuda-cache-refresh")
    t.start()
    return t


def deep_refresh_cuda(q=None):
    """Forced CUDA refresh (accepts 10s wait). Updates cache and returns result."""
    import subprocess as _sp
    try:
        r = _sp.run(
            [sys.executable, "-c",
             "import torch; ok=torch.cuda.is_available(); "
             "name=torch.cuda.get_device_name(0) if ok else 'CPU'; "
             "cu=torch.version.cuda or '?'; print(f'{ok}|{name}|{cu}')"],
            capture_output=True, text=True, timeout=12,
            creationflags=_sp.CREATE_NO_WINDOW if sys.platform=="win32" else 0)
        parts = r.stdout.strip().split("|")
        available = parts[0] == "True"
        ver = parts[2] if len(parts) > 2 else "?"
        # Write cache
        _CUDA_CACHE_PATH.write_text(
            json.dumps({"_ts": time.time(), "available": available, "version": ver}, ensure_ascii=False),
            encoding="utf-8")
        result = {"status": "ok" if available else "warn",
                  "detail": f"CUDA {ver}, {parts[1] if len(parts)>1 else 'GPU'}",
                  "name": "CUDA"}
        if q: q.put(("item", "cuda", result))
        return result
    except Exception as e:
        result = {"status": "fail", "detail": f"CUDA: {e}", "name": "CUDA"}
        if q: q.put(("item", "cuda", result))
        return result



def run_diagnostics_polling():
    """NON_BLOCKING: returns (done_event, queue, thread).
    LAYER_1: pip list JSON -> queue items instantly.
    LAYER_2: background deep verify for torch + opencv."""
    import queue as _q
    done = threading.Event()
    q = _q.Queue()
    t = threading.Thread(target=_diag_two_layer, args=(q, done), daemon=True, name="diag-2layer")
    t.start()
    return done, q, t


def _diag_two_layer(q, done):
    """LAYER_1_FAST: pip list. LAYER_2_DEEP: subprocess verify torch + opencv."""
    start = time.time()
    result = {}
    try:
        # ── LAYER_1_FAST: pip list (<1s) ──
        pkgs = _pip_list_json()
        if pkgs is None:
            # LAYER_1_FALLBACK: directory scan
            fallback = _fallback_dir_scan()
            for pkg, nm in TARGET_PKGS.items():
                ok = fallback.get(pkg) == "installed"
                result[pkg] = {"status":"ok" if ok else "fail","detail":f"{nm} installed" if ok else f"{nm} N/A","name":nm,"deep":"pending"}
            q.put(("status", "pip list failed, using directory scan"))
        else:
            # PEP 503 规范化匹配 (pillow/PyYAML/opencv_python 等大小写与连字符差异)
            pip_map = {e["name"].lower().replace("-", "_"): e["version"] for e in pkgs}
            for pkg, nm in TARGET_PKGS.items():
                v = pip_map.get(pkg.lower().replace("-", "_"))
                if v:
                    result[pkg] = {"status":"ok","detail":f"{nm} {v}","name":nm,"deep":"pending"}
                else:
                    result[pkg] = {"status":"fail","detail":f"{nm} N/A","name":nm,"deep":"pending"}

        # Disk
        try:
            import shutil; gb = shutil.disk_usage(".").free/(1024**3)
            result["disk"] = {"status":"ok","detail":f"Disk {gb:.1f}GB","name":"Disk"}
        except: result["disk"] = {"status":"warn","detail":"N/A","name":"Disk"}
        # Config
        try:
            cp = _PROJECT_ROOT / "config.yaml"
            if cp.exists():
                import yaml
                with open(cp,encoding="utf-8") as f: data = yaml.safe_load(f) or {}
                missing = [k for k in ["app","paths","training","detection"] if k not in data]
                result["config"] = {"status":"ok","detail":"Config OK","name":"Config"} if not missing else {"status":"warn","detail":f"Missing:{missing}","name":"Config"}
            else: result["config"] = {"status":"fail","detail":"Not found","name":"Config"}
        except: result["config"] = {"status":"warn","detail":"N/A","name":"Config"}


        # 环境概览 (Python 版本 + 要求)
        try:
            from infrastructure.environment import python_version_report
            ok, cur, req, _tip = python_version_report()
            result["python_env"] = {
                "status": "ok" if ok else "warn",
                "detail": f"Python {cur}" + (" ✓ (≥" + req + ")" if ok else " ✗ (需 " + req + "+)"),
                "name": "Python环境"}
        except Exception:
            result["python_env"] = {"status": "warn", "detail": "N/A", "name": "Python环境"}

        # ─── 程序自检: 关键文件完整性 ───
        try:
            needed = ["config.yaml", "requirements.txt", "YOLO工作台.py", "YOLO工作台.bat",
                      "src", "src/core", "src/gui", "src/infrastructure", "scripts"]
            missing = [n for n in needed if not (_PROJECT_ROOT / n).exists()]
            result["files"] = {"status": "ok" if not missing else "warn",
                               "detail": "9 项关键文件完整" if not missing else "缺少: " + ",".join(missing),
                               "name": "关键文件"}
        except Exception:
            result["files"] = {"status": "warn", "detail": "N/A", "name": "关键文件"}

        # ─── 程序自检: 路径适配完整性 (目录迁移监测) ───
        try:
            from infrastructure import path_guard
            _root = path_guard.get_project_root()
            _marker = path_guard.read_marker(_root)
            _stale = path_guard.scan_stale_refs(_root)
            _lnk_ok = path_guard.shortcut_points_to(_root)
            _issues = []
            if _marker and str(Path(_marker.get("root", "")).resolve()) != str(_root.resolve()):
                _issues.append("根目录标记不一致")
            if _stale:
                _issues.append(f"{len(_stale)}处旧路径引用")
            if not _lnk_ok:
                _issues.append("快捷方式未指向当前目录")
            result["path"] = {"status": "ok" if not _issues else "warn",
                              "detail": "路径适配正常" if not _issues else "; ".join(_issues),
                              "name": "路径适配"}
        except Exception:
            result["path"] = {"status": "warn", "detail": "N/A", "name": "路径适配"}

        # ─── 程序自检: 路径中心一致性 (主体文件夹统一管理) ───
        try:
            from infrastructure import path_center
            _pc_root = path_center.get_root()
            _pc_issues = path_center.verify_consistency()
            _pc_problems = [f"{it['module']} 指向旧路径" for it in _pc_issues]
            try:
                from infrastructure import path_guard as _pg2
                _pc_marker = _pg2.read_marker(_pc_root)
                if _pc_marker and str(Path(_pc_marker.get("root", "")).resolve()) != str(_pc_root.resolve()):
                    _pc_problems.append("根目录标记不一致")
            except Exception:
                pass
            result["path_center"] = {
                "status": "ok" if not _pc_problems else "warn",
                "detail": f"路径中心统一: {_pc_root}" if not _pc_problems else "; ".join(_pc_problems[:3]),
                "name": "路径中心"}
        except Exception:
            result["path_center"] = {"status": "warn", "detail": "N/A", "name": "路径中心"}

        # ─── 程序自检: 日志系统 + 审计异常统计 ───
        try:
            log_dir = _PROJECT_ROOT / "runs" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            probe = log_dir / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            op = log_dir / "operations.log"
            err_count = warn_count = 0
            if op.exists():
                try:
                    for ln in op.read_text(encoding="utf-8").splitlines()[-300:]:
                        if '"level": "error"' in ln or '"level": "fatal"' in ln:
                            err_count += 1
                        elif '"level": "warn"' in ln:
                            warn_count += 1
                except Exception:
                    pass
            detail = "日志可写"
            if err_count:
                detail += f", 近期报错 {err_count}条 (历史统计)"
            if warn_count:
                detail += f", 近期警告 {warn_count}条 (历史统计)"
            # 历史报错/警告属统计提示, 不判定为当前故障 (可写即 ok)
            result["logs"] = {"status": "ok", "detail": detail, "name": "日志系统"}
        except Exception:
            result["logs"] = {"status": "fail", "detail": "日志不可写", "name": "日志系统"}

        # ─── 程序自检: 自我修复系统就绪 (根据自检结果自动修复) ───
        try:
            from infrastructure import self_repair
            _repair_ok = callable(getattr(self_repair, "auto_repair", None))
            result["self_repair"] = {
                "status": "ok" if _repair_ok else "fail",
                "detail": "自我修复系统就绪 (可自动修复 7 类常见损坏)" if _repair_ok
                          else "self_repair 模块不可用",
                "name": "自我修复"}
        except Exception:
            result["self_repair"] = {"status": "warn", "detail": "N/A",
                                     "name": "自我修复"}

        # ─── 程序自检: 标注工具依赖 (attrs/jsonlines 兼容性) ───
        try:
            from infrastructure.self_repair import annotation_deps_ok
            _ok = annotation_deps_ok()
            result["annotation_deps"] = {
                "status": "ok" if _ok else "fail",
                "detail": "标注工具依赖正常" if _ok else
                          "标注工具依赖损坏 (attrs 版本过高缺少 attr.s), 自动修复可降级 attrs==23.2.0",
                "name": "标注工具依赖"}
        except Exception:
            result["annotation_deps"] = {"status": "warn", "detail": "N/A",
                                         "name": "标注工具依赖"}

        # ─── 程序自检: 核心模块可导入 ───
        try:
            bad = []
            for mod in ["core.trainer", "core.annotator", "core.detector",
                        "infrastructure.audit", "infrastructure.event_bus",
                        "gui.theme_manager"]:
                try:
                    __import__(mod)
                except Exception:
                    bad.append(mod)
            result["modules"] = {"status": "ok" if not bad else "fail",
                                 "detail": "6 个核心模块可导入" if not bad else "导入失败: " + ",".join(bad),
                                 "name": "核心模块"}
        except Exception:
            result["modules"] = {"status": "warn", "detail": "N/A", "name": "核心模块"}


        # ─── 程序自检: 数据集可扫描 + 图片完整性抽样 ───
        try:
            from core.trainer import scan_datasets
            dss = scan_datasets()
            if dss:
                names = list(dss.keys())
                detail = str(len(dss)) + " 个数据集: " + ", ".join(names[:4])
                try:
                    from core.dataset_profiler import verify_dataset_images
                    _bad = 0
                    for _info in dss.values():
                        _bad += verify_dataset_images(_info.path).get("bad", 0)
                    detail += " | 图片完好" if _bad == 0 else f" | 抽样发现 {_bad} 张损坏图片"
                except Exception:
                    pass
                # 数据集格式校验 (与 跌倒数据集 同构: train/valid/test + data.yaml)
                _fmt_bad = []
                try:
                    from core.dataset_profiler import check_dataset_format
                    for _info in dss.values():
                        _fmt = check_dataset_format(_info.path)
                        if not _fmt.get("ok"):
                            _fmt_bad.append(_info.name)
                except Exception:
                    pass
                if _fmt_bad:
                    detail += " | 结构异常: " + ", ".join(_fmt_bad[:3])
                result["dataset_format"] = {
                    "status": "ok" if not _fmt_bad else "warn",
                    "detail": "数据集三分结构标准" if not _fmt_bad
                              else "格式异常: " + ", ".join(_fmt_bad[:3]),
                    "name": "数据集格式"}
                # 数据集标注清理 (摔倒/fall: 拖拽生成已默认剔除标注, 不应有残留)
                try:
                    from core.trainer import find_annotation_files
                    _fall_ds = _PROJECT_ROOT / "data" / "datasets" / "fall"
                    _res = find_annotation_files(_fall_ds) if _fall_ds.is_dir() else []
                    result["dataset_cleanup"] = {
                        "status": "ok" if not _res else "warn",
                        "detail": (f"fall 无标注残留 ({len(_res)} 个)" if not _res
                                   else f"fall 仍有 {len(_res)} 个标注文件, 可自我修复清理"),
                        "name": "数据集标注清理"}
                except Exception:
                    result["dataset_cleanup"] = {"status": "warn", "detail": "N/A",
                                                 "name": "数据集标注清理"}
                result["datasets"] = {"status": "ok" if "损坏" not in detail else "warn",
                                      "detail": detail, "name": "数据集"}
            else:
                result["datasets"] = {"status": "warn", "detail": "暂无数据集", "name": "数据集"}
                result["dataset_format"] = {"status": "warn", "detail": "暂无数据集",
                                            "name": "数据集格式"}
                result["dataset_cleanup"] = {"status": "warn", "detail": "暂无数据集",
                                             "name": "数据集标注清理"}
        except Exception as e:
            result["datasets"] = {"status": "fail", "detail": "scan 异常: " + str(e), "name": "数据集"}
            result["dataset_format"] = {"status": "warn", "detail": "scan 异常: " + str(e),
                                        "name": "数据集格式"}

        # ─── 程序自检: 模型文件 + MD5 ───
        try:
            out_dir = _PROJECT_ROOT / "runs" / "outputs"
            pts = sorted(out_dir.glob("*.pt")) if out_dir.exists() else []
            md5_ok = True
            if pts:
                try:
                    import hashlib
                    pt = pts[0]
                    md5_file = pt.with_suffix(pt.suffix + ".md5")
                    if md5_file.exists():
                        expect = md5_file.read_text(encoding="utf-8").strip().split()[0]
                        actual = hashlib.md5(pt.read_bytes()).hexdigest()
                        md5_ok = (actual == expect)
                except Exception:
                    pass
            detail = str(len(pts)) + " 个模型" + (" MD5 匹配" if md5_ok else " MD5 不匹配")
            result["model"] = {"status": "ok" if pts and md5_ok else "warn",
                               "detail": detail, "name": "模型文件"}
        except Exception:
            result["model"] = {"status": "warn", "detail": "N/A", "name": "模型文件"}


        # ── Flush LAYER_1 results immediately ──
        for k, r in result.items():
            q.put(("item", k, r))
        q.put(("layer1_done", {**result, "_elapsed": time.time()-start}))

        # ── LAYER_2_DEEP: background verify torch + opencv via subprocess ──
        import concurrent.futures as _cf
        def _deep_check(pkg):
            import_name = {"torch":"torch","opencv-python":"cv2"}[pkg]
            try:
                r = __import__('subprocess').run(
                    [sys.executable, "-c",
                     f"import {import_name}; print(getattr({import_name},'__version__','ok'))"],
                    capture_output=True, text=True, timeout=3,
                    creationflags=__import__('subprocess').CREATE_NO_WINDOW if sys.platform=="win32" else 0)
                if r.returncode == 0: return pkg, "ok", r.stdout.strip()
                return pkg, "fail", "import failed"
            except: return pkg, "fail", "timeout"

        with _cf.ThreadPoolExecutor(max_workers=2) as pool:
            futs = [pool.submit(_deep_check, p) for p in DEEP_VERIFY_PKGS]
            for fut in _cf.as_completed(futs):
                pkg, status, detail = fut.result()
                nm = TARGET_PKGS.get(pkg, pkg)
                if status == "ok":
                    result[pkg]["deep"] = "ok"
                    result[pkg]["detail"] = f"{nm} {detail}"
                else:
                    result[pkg]["deep"] = "fail"

        # ── CUDA: read from cache (NO subprocess, <1ms) ──
        cached = read_cuda_cache()
        if cached:
            _cuda_stale = bool(cached.get("_stale"))
            if cached.get("status") == "stale":
                # 缓存过期不是故障: 显示"检测中"并后台刷新, 避免虚假报错
                cached = {"status": "ok",
                          "detail": "CUDA 检测中 (缓存已过期, 后台自动刷新)",
                          "name": "CUDA"}
            result["cuda"] = cached
            q.put(("item", "cuda", cached))
            # If cache is stale, refresh in background (non-blocking)
            if _cuda_stale:
                refresh_cuda_cache_async()
        else:
            # No cache at all — show pending, refresh async
            result["cuda"] = {"status":"pending","detail":"CUDA (checking...)","name":"CUDA"}
            q.put(("item","cuda", result["cuda"]))
            refresh_cuda_cache_async()

        result["_elapsed"] = time.time()-start
        q.put(("done", result))

    except Exception as e:
        try: q.put(("status",f"Crash: {e}")); q.put(("done",{"_error":str(e)}))
        except: pass
    finally:
        done.set()


# ============== Backward compat ==============

def run_diagnostics_async(callback, status_callback=None, timeout=15):
    """Backward-compatible wrapper. Uses done_event polling in a single thread."""
    done, q, t = run_diagnostics_polling()
    def _poll_loop():
        while not done.is_set():
            try:
                while True:
                    msg = q.get_nowait()
                    if msg[0] == "done":
                        callback(msg[1])
                        return
                    elif msg[0] == "status" and status_callback:
                        status_callback(msg[1])
            except:
                pass
            time.sleep(0.1)
        # Final drain
        try:
            while True:
                msg = q.get_nowait()
                if msg[0] == "done": callback(msg[1]); return
        except: pass
    threading.Thread(target=_poll_loop, daemon=True).start()
    return t

def _check_cuda():
    return read_cuda_cache() or {"status":"pending","detail":"CUDA N/A","name":"CUDA"}
def _check_pytorch(): return {"status":"ok","detail":"PyTorch","name":"PyTorch"}
def _check_opencv(): return {"status":"ok","detail":"OpenCV","name":"OpenCV"}
def _check_camera():
    """摄像头真实探测: 打开/黑帧/分辨率 (限时, 防止占用挂死)"""
    try:
        from core.camera_probe import probe_camera
        r = probe_camera(0, frames=2, max_time=6.0)
        if not r.get("opened"):
            return {"status": "fail", "detail": "摄像头未检测到", "name": "Camera"}
        if r.get("frames") and r.get("black_frames") == r.get("frames"):
            return {"status": "warn",
                    "detail": f"Camera{r.get('index')} {r.get('w')}x{r.get('h')} 画面全黑(挡片/开关/驱动)",
                    "name": "Camera"}
        return {"status": "ok",
                "detail": f"Camera{r.get('index')} {r.get('w')}x{r.get('h')} 正常",
                "name": "Camera"}
    except Exception:
        return {"status": "warn", "detail": "N/A", "name": "Camera"}
def _check_disk(): return {"status":"ok","detail":"Disk","name":"Disk"}
def _check_config(): return {"status":"ok","detail":"Config","name":"Config"}
