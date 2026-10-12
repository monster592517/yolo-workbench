# -*- coding: utf-8 -*-
"""ultimate_audit.py — YOLO Workbench 全量自检. No GUI dependency.

当前 15 项 (_chk1.._chk15), 末尾打印 `Summary: n PASS, n WARN, n FAIL`。
⚠ 别在这里写死项数以外的细节 —— 项数变了要同步 CHANGELOG.md 与 README.md
  (2026-09-11 的教训: 手册里出现了不存在的 scripts/page_timing.py, 文档与实现脱节却没人发现)。
  2026-10-01 四线剥离后 16 -> 15 项: H3 摔倒数据生成检查(_chk16)随 H3 线搬出本仓。
"""

import sys, os, time, json, re, logging, traceback, threading
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

_LOG_DIR = ROOT / "runs" / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
AUDIT_LOG = _LOG_DIR / "audit.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler(AUDIT_LOG, encoding="utf-8")]
)
_log = logging.getLogger("ultimate_audit")

results = []
TIMEOUT = 10
# 健康检查含摄像头探测 (最多 6 路 × 2s), GPU 满载时可能超 10s, 单独放宽
HEALTH_TIMEOUT = 30

FAIL_MARKS = ("fail:", "errors:", "error:", "missing:", "missing keys",
              "mismatch", "not created", "not fired", "not singleton",
              "timeout after", "traceback", "exception", "不存在", "失败", "错误")


def _classify(detail) -> str:
    """把检查函数返回的文本映射为 PASS / WARN / FAIL。

    坑 (2026-09-11): 原实现只有"抛异常"或"超时"才判 FAIL, 正常 return 一律 PASS ——
    于是 "FAIL: [...]" / "Missing: [...]" / "MD5 mismatch" / "Logger file not created"
    / "EventBus callback not fired" 全被记成 [PASS]。审计因此变成永远全绿的装饰品:
    实测 GUI Preload 明明报 `Missing: {5 个页面}`, 却标 [PASS]。
    约定: 返回 "WARN: ..." 开头 → WARN; 命中 FAIL_MARKS → FAIL; 其余 PASS。
    """
    s = str(detail or "").strip()
    low = s.lower()
    if low.startswith("warn") or "warn:" in low or "警告" in s:
        return "WARN"
    for mk in FAIL_MARKS:
        if mk in low or mk in s:
            return "FAIL"
    return "PASS"


def _run_with_timeout(fn, name, timeout: int | None = None):
    t0 = time.time()
    timeout = timeout or TIMEOUT
    result = {"name": name, "status": "PASS", "detail": "", "duration_ms": 0}
    done = threading.Event()
    def _do():
        try:
            result["detail"] = fn()
            result["status"] = _classify(result["detail"])
        except Exception as e:
            result["status"] = "FAIL"; result["detail"] = str(e)
        done.set()
    t = threading.Thread(target=_do, daemon=True, name=f"audit-{name}")
    t.start()
    if done.wait(timeout):
        result["duration_ms"] = (time.time() - t0) * 1000
    else:
        result["status"] = "FAIL"; result["detail"] = f"Timeout after {timeout}s"
        result["duration_ms"] = timeout * 1000
    results.append(result)
    icon = {"PASS": "[PASS]", "WARN": "[WARN]", "FAIL": "[FAIL]"}.get(result["status"], "[????]")
    _log.info(f"  {icon} {name} ({result['duration_ms']:.0f}ms) {result['detail']}")
    return result

def audit_all():
    _log.info("=" * 50)
    _log.info("YOLO Workbench v0.10.4 Ultimate Audit")
    _log.info(f"Time: {datetime.now():%Y-%m-%d %H:%M:%S}")
    _log.info("=" * 50)

    # [1] Import integrity
    def _chk1():
        modules = [
            "infrastructure.config", "infrastructure.logger", "infrastructure.diagnostics",
            "infrastructure.environment", "infrastructure.resource_manager", "infrastructure.event_bus",
            "infrastructure.path_center", "infrastructure.path_guard",
            "core.trainer", "core.detector", "core.annotator", "core.history_viewer",
            "core.relabel", "core.dataset_profiler", "core.yolo_convert",
            "gui.components", "gui.main_window",
        ]
        failed = []
        for m in modules:
            try:
                __import__(m)
            except Exception as e:
                failed.append(f"{m}({e})")
        if failed:
            return f"FAIL: {failed}"
        return f"All {len(modules)} core modules OK"
    # 冷启动 import 链含 ultralytics/torch + gui（慢盘 E: 上实测 12-20s），单独放宽到 90s
    _run_with_timeout(_chk1, "Import Integrity", 90)

    # [2] Config file
    def _chk2():
        try:
            from infrastructure.config import get_config
            cfg = get_config()
            # 必须与 config.yaml 的实际顶层键一致。
            # 坑: 旧列表里有 "window", 而 config.yaml 从来没有这个顶层键 (GUI 配置在 "ui"),
            #     于是这条检查一直在"要一个不存在的键"; 配合旧的全绿判定 = 双重失效。
            required = ["app", "paths", "mirrors", "camera", "timeouts",
                        "training", "detection", "recording", "logging", "ui"]
            missing = [k for k in required if cfg.get(k) is None]
            if missing: return f"Missing keys: {missing}"
            return f"config.yaml OK ({len(required)} required keys)"
        except Exception as e:
            return str(e)
    _run_with_timeout(_chk2, "Config Loading")

    # [3] ResourceManager singleton
    def _chk3():
        try:
            from infrastructure.resource_manager import ResourceManager
            rm = ResourceManager()
            if not isinstance(rm, ResourceManager): return "Not singleton"
            rm.release_all()
            return "Singleton OK"
        except Exception as e:
            return str(e)
    _run_with_timeout(_chk3, "ResourceManager")

    # [4] Logger system
    def _chk4():
        try:
            log_file = ROOT / "runs" / "logs" / "app.log"
            with open(log_file, "a", encoding="utf-8") as f:
                f.write("[AUDIT] test log entry\n")
            if log_file.exists():
                return f"Logger OK ({log_file.stat().st_size} bytes)"
            return "Logger file not created"
        except Exception as e:
            return str(e)
    _run_with_timeout(_chk4, "Logger System")

    # [5] EventBus
    def _chk5():
        try:
            from infrastructure.event_bus import EventBus
            bus = EventBus()
            data = {}
            bus.subscribe("test_audit", lambda **kw: data.update(kw))
            bus.emit("test_audit", fired=True)
            if data.get("fired"): return "EventBus OK"
            return "EventBus callback not fired"
        except Exception as e:
            return str(e)
    _run_with_timeout(_chk5, "EventBus")

    # [6] Health check quick
    def _chk6():
        """如实上报: 健康检查自己的 FAIL/WARN 必须传导到审计结论。

        坑 (2026-09-11): 原实现无条件返回 f"Health OK (...)"，哪怕里面写着 1F
        (1 项失败) 也照样 [PASS]。现在把失败的检查名逐条列出来。
        """
        try:
            from infrastructure.health_check import HealthChecker
            hc = HealthChecker.standard()
            results_hc = hc.run_all()
            summary = HealthChecker.summary(results_hc)
            bad = [r for r in results_hc if r.status not in ("PASS",)]
            detail = "; ".join(
                f"{r.name}:{r.message or r.status}" for r in bad[:5]) or "全部通过"
            head = (f"{summary['total']} checks: {summary['passed']}P/"
                    f"{summary['warn']}W/{summary['fail']}F")
            if summary["fail"] > 0:
                return f"FAIL: {head} -> {detail}"
            if summary["warn"] > 0:
                return f"WARN: {head} -> {detail}"
            return f"Health OK ({head})"
        except Exception as e:
            return str(e)
    _run_with_timeout(_chk6, "Health Check", HEALTH_TIMEOUT)

    # [7] Dependencies
    def _chk7():
        import importlib.metadata as _md
        pkgs = ["ultralytics", "opencv-python", "numpy", "torch", "mss", "Pillow", "pyyaml"]
        missing = []
        for p in pkgs:
            try:
                v = _md.version(p)
            except _md.PackageNotFoundError:
                missing.append(p)
        if missing: return f"Missing: {missing}"
        return "All dependencies OK"
    _run_with_timeout(_chk7, "Dependencies")

    # [8] MD5 verification (权重完整性)
    def _chk8():
        """校验权重文件的 .md5 是否真的对得上。

        坑 (2026-09-11): 原实现只在 runs/outputs/*.md5 里找, 而真实的 .md5
        全在项目根 (yolo11n.pt.md5 等 5 个), 于是这条检查永远 "No .md5 files — skip",
        等于从未校验过任何权重。现在按根目录 + models/ 实测哈希。
        """
        import hashlib
        pairs, mismatched, unpaired = [], [], []
        for base in (ROOT, ROOT / "models"):
            if not base.exists():
                continue
            for mf in sorted(base.glob("*.md5")):
                pt = mf.with_suffix("")
                if pt.exists():
                    pairs.append((pt, mf))
                else:
                    unpaired.append(mf.name)
        if not pairs:
            return f"WARN: 未找到可校验的 .md5/.pt 配对 (unpaired={len(unpaired)})"
        for pt, mf in pairs:
            h = hashlib.md5(pt.read_bytes()).hexdigest()
            stored = mf.read_text(encoding="utf-8", errors="ignore").strip().split()[0]
            if h != stored:
                mismatched.append(f"{pt.name}(存{stored[:8]}≠实{h[:8]})")
        if mismatched:
            return f"FAIL: MD5 mismatch: {'; '.join(mismatched)}"
        return (f"MD5 OK ({len(pairs)} 个权重校验通过"
                + (f", {len(unpaired)} 个 .md5 无对应权重" if unpaired else "") + ")")
    # 权重合计数十 MB, 慢盘上留足时间
    _run_with_timeout(_chk8, "MD5 Verification", 60)

    # [9] Jetson scripts syntax (Windows: skip)
    def _chk9():
        if sys.platform == "win32":
            return "Windows: bash -n not available — skip"
        import subprocess as _sp
        deploy_scripts = ROOT / "deploy" / "scripts"
        errors = []
        for sh in sorted(deploy_scripts.glob("*.sh")):
            r = _sp.run(["bash", "-n", str(sh)], capture_output=True, text=True, timeout=5)
            if r.returncode != 0:
                errors.append(f"{sh.name}: {r.stderr.strip()[:100]}")
        if errors: return f"Errors: {errors}"
        return "All deploy scripts OK"
    _run_with_timeout(_chk9, "Jetson Scripts")

    # [10] GUI preload (headless)
    def _chk10():
        """真正把 9 个页面全建一遍, 而不是只数 App() 顺手建了几个。

        坑 (2026-09-11): App() 改成分批惰性预建后 (首屏只建重页面), 这里原来的
        `app._pages.keys()` 只能看到 2-4 页, 报 "Missing: {5 个页面}" —— 配合旧判定
        还是 [PASS]。检查必须显式驱动, 否则测的是"运气"不是"能力"。
        """
        try:
            import tkinter as tk
            root = tk.Tk(); root.withdraw()
            from gui.main_window import App
            app = App()
            spec = list(getattr(App, "_PAGE_SPEC", {}) or {})
            built, failed = [], []
            for key in spec:
                try:
                    page = app._ensure_page(key)
                except Exception as e:
                    failed.append(f"{key}({str(e)[:60]})")
                    continue
                if page is None:
                    failed.append(f"{key}(None)")
                else:
                    built.append(key)
            try:
                root.update_idletasks()
            except Exception:
                pass
            root.destroy()
            if failed:
                return f"FAIL: {len(built)}/{len(spec)} built, failed={failed}"
            if len(built) != len(spec):
                return f"Missing: built {sorted(built)} of {len(spec)}"
            return f"All {len(built)} pages OK: {sorted(built)}"
        except Exception as e:
            return str(e)
    # 9 页冷建在慢盘 E: 上实测 3-6s, 10s 会误判超时
    _run_with_timeout(_chk10, "GUI Preload", 60)

    # [11] Print + hardcoded paths
    def _chk11():
        prints = 0; abs_paths = 0
        for f in SRC.rglob("*.py"):
            try:
                c = f.read_text(encoding="utf-8", errors="ignore")
                for m in re.finditer(r'^\s*print\(', c, re.MULTILINE):
                    ctx = c[max(0, m.start()-200):m.start()]
                    if "__name__" not in ctx: prints += 1
                if re.search(r"/(?:[A-Z]:\\|/home/)", c): abs_paths += 1
            except: pass
        warnings = []
        if prints > 0: warnings.append(f"{prints} print() calls")
        if abs_paths > 0: warnings.append(f"{abs_paths} absolute paths")
        if warnings: return "WARN: " + ", ".join(warnings)
        return "Code clean"
    _run_with_timeout(_chk11, "Print/Hardcode Scan")

    # [12] Resource compliance (cv2 outside ResourceManager)
    def _chk12():
        violations = 0
        rm_file = SRC / "infrastructure" / "resource_manager.py"
        for f in SRC.rglob("*.py"):
            if f == rm_file or "deployer.py" in str(f): continue
            try:
                c = f.read_text(encoding="utf-8", errors="ignore")
                if re.search(r'cv2\.VideoCapture\(', c) and "get_resource_manager" not in c:
                    violations += 1
            except: pass
        if violations: return f"WARN: {violations} direct cv2 calls outside ResourceManager"
        return "Compliant"
    _run_with_timeout(_chk12, "Resource Compliance")

    # [13] Release sync integrity (路径/手册/快捷方式/更新记录)
    def _chk13():
        issues = []
        try:
            from infrastructure.config import get_config
            cfg = get_config()
            for name, d in [("data", cfg.data_dir), ("output", cfg.output_dir),
                            ("record", cfg.record_dir), ("deploy", cfg.deploy_dir),
                            ("log", cfg.log_dir)]:
                if not d.exists():
                    issues.append(f"missing dir: {name}={d}")
        except Exception as e:
            issues.append(f"config: {e}")
        for f in [ROOT / "README.md", ROOT / "CHANGELOG.md"]:
            if not f.exists():
                issues.append(f"missing doc: {f.name}")
        try:
            import subprocess as _sp
            r = _sp.run(
                ["powershell", "-NoProfile", "-Command",
                 "$s=New-Object -ComObject WScript.Shell;"
                 "$l=$s.CreateShortcut([Environment]::GetFolderPath('Desktop')+'\\YOLO工作台.lnk');"
                 "$l.WorkingDirectory"],
                capture_output=True, timeout=10)
            wd = (r.stdout or b"").decode("utf-8", errors="replace").strip()
            if wd and Path(wd).resolve() != ROOT.resolve():
                issues.append(f"shortcut points to {wd}")
        except Exception as e:
            issues.append(f"shortcut check: {e}")
        if issues:
            return "WARN: " + "; ".join(issues)
        return "Release sync OK (paths/manual/shortcut)"
    _run_with_timeout(_chk13, "Release Sync Integrity")

    # [14] Path center consistency (主体文件夹统一管理)
    def _chk14():
        try:
            from infrastructure import path_center, path_guard
            root = path_center.get_root()
            issues = path_center.verify_consistency()
            marker = path_guard.read_marker(root)
            if marker and str(Path(marker.get("root", "")).resolve()) != str(root.resolve()):
                issues.append({"module": "marker", "expected": str(root.resolve()),
                               "actual": str(marker.get("root"))})
            if issues:
                return f"WARN: {len(issues)} 个模块不一致: " + "; ".join(
                    f"{it['module']}->{it['actual']}" for it in issues[:3])
            return f"Path center OK ({root})"
        except Exception as e:
            return str(e)
    _run_with_timeout(_chk14, "Path Center Consistency")

    # [15] Self repair system (自我修复系统就绪)
    def _chk15():
        try:
            from infrastructure.self_repair import auto_repair
            rep = auto_repair({})
            if rep["fixed"] == 0:
                return f"Self repair OK (dry-run, 0 actions)"
            return f"WARN: self repair ran {rep['fixed']} actions (系统曾被检出问题并自动修复)"
        except Exception as e:
            return str(e)
    _run_with_timeout(_chk15, "Self Repair")


    # ── Summary ──
    passed = sum(1 for r in results if r["status"] == "PASS")
    warned = sum(1 for r in results if r["status"] == "WARN")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    _log.info("=" * 50)
    _log.info(f"Summary: {passed} PASS, {warned} WARN, {failed} FAIL")
    _log.info(f"Full log: {AUDIT_LOG}")
    _log.info("=" * 50)

    return results

if __name__ == "__main__":
    audit_all()
