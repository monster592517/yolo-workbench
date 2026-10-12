r"""路径卫士 — 项目目录实时监测与迁移自动适配

背景: 项目文件夹可能因磁盘空间等原因随时迁移 (如 C:\Users\...\Desktop\yolo_lab
      -> D:\yolo_lab)。代码内全部路径均基于 __file__ 动态解析, 天然可移植;
      但以下外部引用会残留旧路径, 需要自动适配:
        - data/**/data.yaml 的 path 字段
        - 桌面快捷方式 (.lnk) 的工作目录
        - scripts/*.ps1 等脚本内写死的开发目录

⚠️ **搬迁不止是"别人家的事" —— 本仓 2026-09-18 真的搬过一次**
   (从 `D:\yolo_lab` 迁到 `<项目根>\01_yolo_lab`)。那次搬迁暴露了本模块一个
   静默失效: `locate_project()` 的候选位置**写死成 `X:\yolo_lab`**,
   而新位置既不在盘根、末级名字也变了 ⇒ 根一旦消失, 它再也找不到自己。
   它**不会报错**, 只会返回 None, 然后日志里写一句"项目目录不可访问" ——
   看上去像是"盘真的没了", 实际是"量具的射程不够"。
   ⇒ 现在候选位置由**当前根自己的名字**派生 (`_root_rel_candidates`),
     再搬一次也认得自己。

功能:
  1. 启动适配: 启动时对比"记录根目录"与"当前根目录", 检测到迁移后自动修复
     data.yaml / 快捷方式 / 运行目录, 并写入审计日志与 CHANGELOG
  2. 实时监测: 后台守护线程定期检查根目录存在性与引用完整性, 运行中迁移也能发现并适配
  3. 手动适配: scripts/adapt_paths.py [--scan] 或根目录"YOLO工作台.bat"(菜单 5)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_MARKER_NAME = ".root_marker.json"
_MARKER_PATH = _PROJECT_ROOT / "runs" / _MARKER_NAME
_RUNTIME_DIRS = ("runs/logs", "runs/outputs", "runs/recordings",
                 "runs/deploy_packages", "runs/board_sim", "data")
_SKIP_SCAN_DIRS = {"__pycache__", ".pytest_cache", ".git", "runs", "node_modules",
                   "tools", ".idea", ".vscode", "__MACOSX"}
_DRIVE_RE = re.compile(r"^([A-Za-z]):[\\/].*")


# ═══════════════════ 基础路径 ═══════════════════

def set_project_root(root) -> None:
    """路径中心联动: 切换路径卫士的根目录与标记文件路径 (幂等)"""
    global _PROJECT_ROOT, _MARKER_PATH
    _PROJECT_ROOT = Path(root).resolve()
    _MARKER_PATH = _PROJECT_ROOT / "runs" / _MARKER_NAME


def get_project_root() -> Path:
    """当前项目根目录 (含 config.yaml + YOLO工作台.py + src 的目录; 可动态切换)"""
    return _PROJECT_ROOT


def is_project_root(path: Path) -> bool:
    try:
        return ((path / "config.yaml").is_file()
                and (path / "YOLO工作台.py").is_file()
                and (path / "src").is_dir())
    except Exception:
        return False


def ensure_runtime_dirs(root: Optional[Path] = None) -> List[Path]:
    """补建运行目录, 返回本次新建的目录"""
    root = root or get_project_root()
    created = []
    for rel in _RUNTIME_DIRS:
        d = root / rel
        try:
            if not d.exists():
                d.mkdir(parents=True, exist_ok=True)
                created.append(d)
        except Exception:
            pass
    return created


# ═══════════════════ 根目录标记 ═══════════════════

def read_marker(root: Optional[Path] = None) -> Optional[dict]:
    """读取根目录标记文件, 不存在返回 None"""
    mp = (root or get_project_root()) / "runs" / _MARKER_NAME
    try:
        if mp.exists():
            return json.loads(mp.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def write_marker(root: Optional[Path] = None, old_root: Optional[str] = None,
                 reason: str = "startup") -> Path:
    root = root or get_project_root()
    (root / "runs").mkdir(parents=True, exist_ok=True)
    mp = root / "runs" / _MARKER_NAME
    data = {
        "root": str(root.resolve()),
        "old_root": old_root,
        "reason": reason,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "version": _app_version(),
    }
    try:
        mp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return mp


def _app_version() -> str:
    try:
        import yaml as _y
        cfg = _y.safe_load((get_project_root() / "config.yaml").read_text(encoding="utf-8")) or {}
        return str(cfg.get("app", {}).get("version", "?"))
    except Exception:
        return "?"


# ═══════════════════ 旧路径引用扫描与修复 ═══════════════════

def _abs_root_strings(root: Path) -> List[str]:
    """当前根目录及其常见别名 (正反斜杠、大小写)"""
    s = str(root.resolve())
    alts = {s, s.replace("\\", "/"), s.lower(), s.replace("\\", "/").lower()}
    return sorted(alts, key=len, reverse=True)


def _find_stale_yamls(root: Path) -> List[Path]:
    """data 下所有 data.yaml / dataset.yaml"""
    out = []
    data_dir = root / "data"
    if data_dir.exists():
        out += list(data_dir.rglob("data.yaml"))
        out += list(data_dir.rglob("dataset.yaml"))
    return out


def scan_stale_refs(root: Optional[Path] = None) -> List[dict]:
    """扫描项目中残留的旧根目录引用 (仅报告, 不修改)

    返回 [{file, ref, line}] — ref 为被引用的其他磁盘位置。
    跳过: runs(日志历史), tools(安装包构建产物), 缓存目录。
    """
    root = root or get_project_root()
    marker = read_marker(root)
    known_old = None
    if marker:
        for k in ("old_root", "root"):
            v = marker.get(k)
            if v and _DRIVE_RE.match(str(v)) and str(Path(v).resolve()) != str(root.resolve()):
                known_old = str(v)
                break
    cur = str(root.resolve()).replace("\\", "/").lower()
    # 末级名字候选**派生**，不写死 `yolo_lab` —— 见 _root_name_hints。
    # （原来写死时，这条判据靠"名字恰好以 yolo_lab 结尾"这个巧合在工作。）
    hints = _root_name_hints(root)
    found = []
    scan_files = []
    try:
        scan_files += _find_stale_yamls(root)
    except Exception:
        pass
    for rel in ("scripts", "tests"):
        d = root / rel
        if d.exists():
            scan_files += [f for f in d.rglob("*")
                           if f.is_file() and f.suffix in (".ps1", ".py", ".bat")]
    for f in root.glob("*.bat"):
        scan_files.append(f)
    for f in scan_files:
        try:
            if any(part in _SKIP_SCAN_DIRS for part in f.relative_to(root).parts):
                continue
            for i, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                for m in _DRIVE_RE.finditer(line):
                    ref = m.group(0).rstrip('"\'\\')
                    norm = ref.replace("\\", "/").rstrip("/").lower()
                    if not any(norm.endswith(h) for h in hints):
                        continue
                    if norm == cur:
                        continue
                    # 排除已修复/通用的相对写法
                    if "%~dp0" in line or "$PSScriptRoot" in line:
                        continue
                    found.append({"file": str(f), "line": i, "ref": ref,
                                  "known_old": known_old})
                    break
        except Exception:
            continue
    return found


def adapt_data_yamls(root: Optional[Path] = None, dry_run: bool = False) -> List[dict]:
    """修复 data/**/data.yaml 的 path 字段指向当前根目录"""
    root = root or get_project_root()
    fixed = []
    for yf in _find_stale_yamls(root):
        try:
            lines = yf.read_text(encoding="utf-8").splitlines()
            target = str(yf.parent.resolve())
            changed = False
            for i, ln in enumerate(lines):
                if re.match(r"^\s*path\s*:", ln):
                    cur = ln.split(":", 1)[1].strip().strip("\"'")
                    if str(Path(cur).resolve()) != str(Path(target).resolve()):
                        if not dry_run:
                            lines[i] = f"path: {target}"
                        changed = True
                    break
            if changed and not dry_run:
                yf.write_text("\n".join(lines) + "\n", encoding="utf-8")
            if changed:
                fixed.append({"file": str(yf), "from": cur, "to": target})
        except Exception:
            continue
    return fixed


# ═══════════════════ 桌面快捷方式 ═══════════════════

def _desktop_lnk() -> Path:
    return Path(os.path.expanduser("~")) / "Desktop" / "YOLO工作台.lnk"


def shortcut_points_to(root: Optional[Path] = None) -> bool:
    """检查桌面快捷方式工作目录是否指向当前根目录"""
    root = root or get_project_root()
    lnk = _desktop_lnk()
    if not lnk.exists():
        return False
    try:
        ps = ("$ws=New-Object -ComObject WScript.Shell;"
              "$s=$ws.CreateShortcut('%s');"
              "[Console]::Write($s.WorkingDirectory)" % str(lnk))
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=20,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        wd = r.stdout.decode("utf-8", errors="ignore").strip()
        return bool(wd) and str(Path(wd).resolve()) == str(root.resolve())
    except Exception:
        return False


def refresh_desktop_shortcut(root: Optional[Path] = None) -> bool:
    """调用 scripts/create_shortcut.ps1 刷新桌面快捷方式"""
    root = root or get_project_root()
    ps1 = root / "scripts" / "create_shortcut.ps1"
    if not ps1.exists():
        return False
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)],
            capture_output=True, timeout=90,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode == 0
    except Exception:
        return False


# ═══════════════════ 迁移定位 ═══════════════════

def _root_rel_candidates(root: Optional[Path] = None) -> List[str]:
    """按**当前根自己的名字**推候选相对路径 —— 不写死。

    为什么要派生（2026-09-18 实测，见模块 docstring）：原来这里写死了 `yolo_lab`，
    而项目已搬到一个**形状不同**的位置 ⇒ 根一旦消失，`locate_project()` 在
    每一个候选上都找不到自己，迁移自愈能力**静默失效**（返回 None + 一句日志，
    看起来像"盘真的没了"，实际是"射程不够"）。

    产出顺序即优先级：
      1. 末级名字            (`01_yolo_lab`)          —— 盘根 / 桌面直放
      2. 父级+末级           (`AI_projects\\01_yolo_lab`) —— 现在的布局
      3. 历史名              (`yolo_lab`)             —— 老布局兜底（只兜底，不当主路径）
    """
    root = root or get_project_root()
    names: List[str] = []
    try:
        if root.name:
            names.append(root.name)
    except Exception:
        pass
    try:
        if root.parent and root.parent.name:
            names.append(os.path.join(root.parent.name, root.name))
    except Exception:
        pass
    names.append("yolo_lab")                 # 历史名兜底
    out: List[str] = []
    for n in names:
        if n and n not in out:
            out.append(n)
    return out


def _root_name_hints(root: Optional[Path] = None) -> List[str]:
    """根目录的**末级名字**候选（小写），用于判断某条盘符绝对路径是不是指本项目。

    同样是派生而非写死：`scan_stale_refs` 原来写 `endswith("yolo_lab")`，
    靠"名字恰好以 yolo_lab 结尾"这个巧合工作。项目换个名字就整条判据失效。
    """
    out: List[str] = []
    for n in _root_rel_candidates(root):
        last = os.path.basename(n.rstrip("\\/")).lower()
        if last and last not in out:
            out.append(last)
    return out


def locate_project(depth: int = 3) -> Optional[Path]:
    """在常见位置查找项目 (当前根目录消失时用于迁移定位)

    `depth` = 桌面 / 用户目录下**往下翻几层**（候选直查不受它限制）。
    候选一律由当前根的名字派生，见 `_root_rel_candidates`。
    """
    root = get_project_root()
    rels = _root_rel_candidates(root)
    want = set(_root_name_hints(root))

    cands: List[Path] = [root]
    try:
        user = Path(os.path.expanduser("~"))
        for rel in rels:
            cands.append(user / "Desktop" / rel)
            cands.append(user / rel)
    except Exception:
        pass
    for letter in "CDEFGH":
        for rel in rels:
            cands.append(Path(f"{letter}:\\{rel}"))

    seen = set()
    for c in cands:
        try:
            key = str(Path(c)).lower()
            if key in seen:
                continue
            seen.add(key)
            if c.exists() and is_project_root(c):
                return c
        except Exception:
            continue

    # 浅层递归: 桌面 / 用户目录往下 depth 层（**不去扫盘根** —— 那里目录太多，代价不成比例）
    try:
        bases = [Path(os.path.expanduser("~")) / "Desktop", Path(os.path.expanduser("~"))]
    except Exception:
        bases = []
    for base in bases:
        try:
            if not base.is_dir():
                continue
            frontier = [base]
            for _ in range(max(1, int(depth))):
                nxt: List[Path] = []
                for cur in frontier:
                    try:
                        for child in cur.iterdir():
                            if not child.is_dir():
                                continue
                            if child.name.lower() in want and is_project_root(child):
                                return child
                            nxt.append(child)
                    except Exception:
                        continue
                frontier = nxt
                if not frontier:
                    break
        except Exception:
            continue
    return None


# ═══════════════════ 审计/日志/CHANGELOG 同步 ═══════════════════

def _audit(event: str, msg: str, level: str = "info") -> None:
    try:
        from infrastructure.audit import audit
        audit(event, msg, level=level, module="path_guard")
    except Exception:
        try:
            log = get_project_root() / "runs" / "logs" / "operations.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"event": event, "level": level, "message": msg,
                                    "time": time.strftime("%Y-%m-%d %H:%M:%S")},
                                   ensure_ascii=False) + "\n")
        except Exception:
            pass


def _append_changelog(root: Path, old_root: Optional[str], reason: str) -> None:
    try:
        doc = root / "CHANGELOG.md"
        if not doc.exists():
            return
        src = doc.read_text(encoding="utf-8")
        entry = (f"\n## {time.strftime('%Y-%m-%d %H:%M')} 路径迁移记录\n\n"
                 f"- 检测到项目目录迁移: {old_root or '未知'} -> {root.resolve()}\n"
                 f"- 触发方式: {reason}; 已自动适配 data.yaml / 桌面快捷方式 / 运行目录\n")
        doc.write_text(src + entry, encoding="utf-8")
    except Exception:
        pass


# ═══════════════════ 启动适配 ═══════════════════

def ensure_paths_adapted(start_monitor: bool = True, force: bool = False) -> dict:
    """启动时路径校验与迁移适配 (幂等, 可重复调用)

    返回报告: {root, old_root, migrated, fixed_yamls, shortcut, created_dirs, ...}
    """
    # 路径中心为唯一真相源: 先同步到路径中心, 再以它为准
    try:
        from infrastructure.path_center import (
            get_root as _pc_get_root,
            set_root as _pc_set_root,
            resync_all as _pc_resync,
        )
        root = _pc_get_root()
        _pc_set_root(root)
        _pc_resync_available = True
    except Exception:
        root = get_project_root()
        _pc_resync_available = False
    set_project_root(root)

    marker = read_marker(root)
    old_root = None
    if marker:
        old = marker.get("root")
        if old and str(Path(old).resolve()) != str(root.resolve()):
            old_root = str(old)
    migrated = bool(old_root)
    manual = force and not migrated

    created = ensure_runtime_dirs(root)
    fixed_yamls = adapt_data_yamls(root)
    shortcut_ok = shortcut_points_to(root)
    shortcut_refreshed = False
    if migrated or manual or not shortcut_ok:
        shortcut_refreshed = refresh_desktop_shortcut(root)

    report = {
        "root": str(root.resolve()),
        "old_root": old_root,
        "migrated": migrated,
        "manual": manual,
        "fixed_yamls": fixed_yamls,
        "shortcut_was_ok": shortcut_ok,
        "shortcut_refreshed": shortcut_refreshed,
        "created_dirs": [str(d) for d in created],
    }

    if migrated or manual or fixed_yamls or shortcut_refreshed or created:
        _audit("path_adapt",
               f"路径适配: 根目录={root.resolve()} 迁移={migrated} 手动={manual} "
               f"data.yaml修复={len(fixed_yamls)} 快捷方式刷新={shortcut_refreshed}",
               level="warn" if migrated else "info")
    if migrated:
        _append_changelog(root, old_root, "migration")
    write_marker(root, old_root=old_root,
                 reason="migration" if migrated else ("manual" if manual else "startup"))
    # 全程序路径联动: 迁移或手动适配后, 把新根目录推送到全部已加载模块
    if (migrated or manual) and _pc_resync_available:
        try:
            _pc_resync(root)
        except Exception:
            pass
    if start_monitor:
        start_path_monitor()
    return report


# ═══════════════════ 实时监测守护线程 ═══════════════════

_MONITOR = None
_MONITOR_LOCK = threading.Lock()


def start_path_monitor(interval: float = 30.0) -> threading.Thread:
    """启动路径监测守护线程 (幂等; 检测运行中迁移并自动适配)"""
    global _MONITOR
    with _MONITOR_LOCK:
        if _MONITOR and _MONITOR.is_alive():
            return _MONITOR
        _MONITOR = threading.Thread(target=_monitor_loop, args=(interval,),
                                    daemon=True, name="path-monitor")
        _MONITOR.start()
        return _MONITOR


def _monitor_loop(interval: float) -> None:
    warned = False
    cycle = 0
    while True:
        try:
            root = get_project_root()
            if not root.exists():
                # 运行中目录被迁移: 尝试定位新位置
                new_root = locate_project()
                if new_root and str(new_root.resolve()) != str(root.resolve()):
                    _audit("path_monitor",
                           f"检测到项目目录运行中迁移: {root} -> {new_root}", level="warn")
                    try:
                        from infrastructure.path_center import set_root as _pc_set_root
                        _pc_set_root(new_root)
                    except Exception:
                        set_project_root(new_root)
                    ensure_paths_adapted(start_monitor=False)
                    warned = False
                elif not warned:
                    _audit("path_monitor", f"项目目录不可访问: {root}", level="warn")
                    warned = True
            else:
                warned = False
                # 引用完整性: data.yaml 残留旧路径
                stale = scan_stale_refs(root)
                if stale:
                    fixed = adapt_data_yamls(root)
                    if fixed:
                        _audit("path_monitor",
                               f"自动修复 {len(fixed)} 个 data.yaml 旧路径引用", level="warn")
                # 快捷方式: 每 10 个周期 (~5分钟) 校验一次
                cycle += 1
                if cycle % 10 == 0 and not shortcut_points_to(root):
                    if refresh_desktop_shortcut(root):
                        _audit("path_monitor", "快捷方式已自动刷新指向当前目录")
        except Exception:
            pass
        time.sleep(interval)


# ═══════════════════ 命令行入口 (scripts/adapt_paths.py 复用) ═══════════════════

def cli(argv: Optional[List[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    import argparse
    ap = argparse.ArgumentParser(description="YOLO工作台 路径迁移适配工具")
    ap.add_argument("--scan", action="store_true", help="仅扫描, 不修改")
    args = ap.parse_args(argv)
    root = get_project_root()
    if args.scan:
        stale = scan_stale_refs(root)
        marker = read_marker(root)
        print(f"当前根目录: {root.resolve()}")
        print(f"标记根目录: {(marker or {}).get('root', '无')}")
        print(f"快捷方式指向当前目录: {shortcut_points_to(root)}")
        print(f"残留旧路径引用: {len(stale)}")
        for s in stale:
            print(f"  - {s['file']} 第{s['line']}行: {s['ref']}")
        return 0
    report = ensure_paths_adapted(start_monitor=False, force=True)
    print(f"根目录: {report['root']}")
    print(f"迁移来源: {report['old_root'] or '无'}")
    print(f"修复 data.yaml: {len(report['fixed_yamls'])}")
    for f in report["fixed_yamls"]:
        print(f"  - {f['file']}")
    print(f"快捷方式刷新: {report['shortcut_refreshed']}")
    try:
        from infrastructure.path_center import verify_consistency
        issues = verify_consistency()
        if issues:
            print(f"路径一致性警告: {len(issues)} 个模块不一致")
            for it in issues:
                print(f"  - {it['module']}: 实际 {it['actual']}")
        else:
            print("全程序路径联动: 所有模块一致 [OK]")
    except Exception:
        pass
    print("路径适配完成 [OK]")
    return 0


if __name__ == "__main__":
    sys.exit(cli())
