# -*- coding: utf-8 -*-
"""HealthChecker — enterprise system health diagnostics. Plugin architecture, async, config-driven."""

from __future__ import annotations

import time, threading, json, sys
from pathlib import Path
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from typing import Callable, List, Dict, Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_LOG_DIR = _PROJECT_ROOT / "runs" / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_HEALTH_LOG = _LOG_DIR / "health_check.log"


def get_project_root() -> Path:
    """当前项目根目录 (可被路径中心动态切换)"""
    return _PROJECT_ROOT


def set_project_root(root) -> None:
    """路径中心联动: 切换健康检查的根目录与日志文件 (幂等)"""
    global _PROJECT_ROOT, _LOG_DIR, _HEALTH_LOG
    _PROJECT_ROOT = Path(root).resolve()
    _LOG_DIR = _PROJECT_ROOT / "runs" / "logs"
    _HEALTH_LOG = _LOG_DIR / "health_check.log"
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


@dataclass
class CheckResult:
    name: str = ""; status: str = "PASS"; message: str = ""
    suggestion: str = ""; duration_ms: float = 0.0; group: str = "general"
    def to_dict(self): return vars(self)


class HealthChecker:
    """Plugin-based health checker. Register checks, run concurrently, report results."""

    def __init__(self, timeout: int = 10):
        self._checks: List[tuple] = []
        self._timeout = timeout
        try:
            from infrastructure.config import get_config
            self._timeout = int(get_config().get("health.timeout", 10))
        except: pass

    def register(self, group: str, name: str, fn: Callable):
        self._checks.append((group, name, fn))

    def run_all(self, progress_cb: Callable = None) -> List[CheckResult]:
        results = []; total = len(self._checks); completed = [0]; lock = threading.Lock()
        def _log(msg: str):
            try:
                with open(_HEALTH_LOG, "a", encoding="utf-8") as f:
                    f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
            except: pass
        _log("=== HEALTH CHECK START ===")
        def _run(grp: str, name: str, fn: Callable) -> CheckResult:
            t0 = time.time()
            try: r = fn()
            except Exception as e: r = CheckResult(name=name, status="FAIL", message=str(e), group=grp)
            r.name = name; r.group = grp; r.duration_ms = (time.time() - t0) * 1000
            with lock: completed[0] += 1
            if progress_cb:
                try: progress_cb(name, completed[0], total)
                except: pass
            _log(f"[{r.status:4s}] {name} ({r.duration_ms:.0f}ms) {r.message}")
            return r
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(_run, grp, name, fn) for grp, name, fn in self._checks]
            for fut in futures:
                try: results.append(fut.result(timeout=self._timeout))
                except FutTimeout: results.append(CheckResult(name="timeout", status="FAIL", message="Timed out"))
        _log(f"=== DONE ({len(results)} checks) ===")
        return results

    @staticmethod
    def summary(results: List[CheckResult]) -> dict:
        gs = {}; total = len(results)
        passed = sum(1 for r in results if r.status == "PASS")
        warns = sum(1 for r in results if r.status == "WARN")
        fails = sum(1 for r in results if r.status == "FAIL")
        for r in results:
            g = gs.setdefault(r.group, {"PASS": 0, "WARN": 0, "FAIL": 0})
            if r.status in g: g[r.status] += 1
        return {"total": total, "passed": passed, "warn": warns, "fail": fails, "groups": gs}

    @classmethod
    def standard(cls) -> "HealthChecker":
        hc = cls()
        from infrastructure.environment import check_gpu, check_camera, _pkg_installed
        # Env
        def _cuda(): r = check_gpu(); return CheckResult("CUDA", "PASS" if r[0] else "WARN", r[1], "pip install torch", group="env")
        def _cam(): c = check_camera(); return CheckResult("Camera", "PASS" if c > 0 else "WARN", f"{c} found", "", group="env")
        def _disk():
            try:
                import shutil; gb = shutil.disk_usage(".").free / (1024 ** 3)
                return CheckResult("Disk", "PASS" if gb > 1 else "WARN", f"{gb:.1f}GB free", "Free space" if gb < 1 else "", group="env")
            except: return CheckResult("Disk", "FAIL", "Error", "", group="env")
        def _ram():
            try:
                import psutil; m = psutil.virtual_memory(); pct = m.percent
                return CheckResult("RAM", "PASS" if pct < 90 else "WARN", f"{pct}% used", "Close apps" if pct > 90 else "", group="env")
            except: return CheckResult("RAM", "WARN", "psutil not installed", "pip install psutil", group="env")
        for chk in [("CUDA", _cuda), ("Camera", _cam), ("Disk Space", _disk), ("RAM Usage", _ram)]:
            hc.register("env", chk[0], chk[1])
        # Packages
        for pkg, nm in [("ultralytics","YOLO"),("torch","PyTorch"),("opencv-python","OpenCV"),("numpy","NumPy"),("Pillow","PIL"),("pyyaml","YAML")]:
            def _p(p=pkg, n=nm):
                ok = _pkg_installed(p)
                return CheckResult(n, "PASS" if ok else "FAIL", "Installed" if ok else "Missing", f"pip install {p}" if not ok else "", group="packages")
            hc.register("packages", nm, _p)
        # Training
        def _train_ds():
            dp = _PROJECT_ROOT / "data" / "datasets"
            if dp.exists():
                cnt = sum(1 for _ in dp.rglob("*.jpg"))
                return CheckResult("Dataset", "PASS" if cnt > 0 else "WARN", f"{cnt} images", group="training")
            return CheckResult("Dataset", "FAIL", "Not found", "Place data in data/datasets/", group="training")
        hc.register("training", "Dataset", _train_ds)
        # Detection
        def _detect_model():
            op = _PROJECT_ROOT / "runs" / "outputs"
            pts = list(op.glob("*.pt")) if op.exists() else []
            return CheckResult("Model File", "PASS" if pts else "WARN", f"{len(pts)} .pt files" if pts else "No models", "Train a model first", group="detection")
        hc.register("detection", "Model", _detect_model)
        # Resource
        def _rm():
            from infrastructure.resource_manager import get_resource_manager
            rm = get_resource_manager()
            return CheckResult("ResourceMgr", "PASS", "Singleton OK", group="resource")
        hc.register("resource", "ResourceMgr", _rm)
        return hc
