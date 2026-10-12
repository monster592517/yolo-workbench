# -*- coding: utf-8 -*-
"""数据集共享状态 — 「数据集与标注」页与「模型训练」页之间的唯一真源。

## 为什么需要它

拆分前, 数据集选择和训练挤在同一个 TrainingPage 里, 选中项存在
`self._ds_var` / `self._ds_map`。拆成两页后, 训练页必须知道标注页选的是哪个
数据集, 但**两页不应该互相持有引用**: 页面是惰性构造的 (`main_window._ensure_page`),
谁先被打开不确定, 拿 `controller._pages["dataset"]` 会踩 KeyError。

所以: 把 {数据集清单, 当前选中} 提到模块级单例, 两页都当它是共享黑板。

## 契约

- 任何页面**只通过本模块**读写当前数据集, 不要各存一份副本;
- `ensure_scan()` 是幂等的, 后台只扫一次, 两个页面共用同一份 scan_datasets() 结果;
- 变更经 `infrastructure.event_bus` 广播, 订阅方**必须自己 `after(0, ...)` 回主线程**
  (emit 是在调用方线程里同步跑的, 后台线程发出来的事件就在后台线程回调)。

## 踩坑记录 (2026-09-11)

- **不要把 `scan_datasets()` 放进 `__init__` 里同步跑** —— 大目录会卡住页面构建。
  旧实现靠 `_bg_scan` 线程 + queue 规避, 本模块保持同样的异步姿态。
- **`core.trainer` 必须惰性 import** —— 它拖着 ultralytics/torch, 模块顶层 import 会
  把冷启动拖慢 (本机 14.7GB 内存, torch 初始化还容易触发换页)。只在 `ensure_scan` 里 import。
- **`EventBus.emit` 是同步回调且静默吞异常** —— 订阅方不要在回调里做重活, 也不要指望
  异常会被报出来。
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict, Optional

EV_CHANGED = "dataset:changed"      # 选中数据集变化 -> kwargs: name
EV_SCANNED = "dataset:scanned"      # 数据集清单刷新 -> kwargs: count (-1 = 开始扫描)
EV_MODELS_CHANGED = "models:changed"  # .pt 模型清单变化 -> kwargs: 无

_LOCK = threading.RLock()
_MAP: Dict[str, Any] = {}
_NAME: str = ""
_SCANNED: bool = False
_SCANNING: bool = False
_AUDIT_LAST: Optional[str] = None
_SCAN_AUDIT_LAST: Optional[int] = None   # 上次落审计的命中数 (变化才写, 见 set_map)


def _bus():
    from infrastructure.event_bus import get_event_bus
    return get_event_bus()


def bus():
    """公开的 event bus 句柄 —— 页面用它订阅本模块广播的事件。"""
    return _bus()


def _last_ds_file() -> Optional[Path]:
    try:
        from infrastructure.config import get_config
        return Path(get_config().output_dir).parent / "last_dataset.txt"
    except Exception:
        return None


# ────────────────────────── 读 ──────────────────────────

def get_map() -> Dict[str, Any]:
    """数据集清单 {显示名: DatasetInfo} — 返回浅拷贝, 调用方改不到内部状态。"""
    with _LOCK:
        return dict(_MAP)


def get_name() -> str:
    with _LOCK:
        return _NAME


def get_info(name: Optional[str] = None) -> Any:
    """取数据集信息。name 省略时用当前选中项。"""
    with _LOCK:
        key = _NAME if name is None else name
        if not key:
            return None
        return _MAP.get(key)


def is_ready() -> bool:
    """当前选中项存在且图片数 > 0 —— 训练前的就绪判据。"""
    info = get_info()
    return bool(info) and getattr(info, "image_count", 0) > 0


def summary() -> str:
    """给训练页摘要行用的一句话描述。"""
    info = get_info()
    if not info:
        return "未选择数据集 ⚠  请在「数据集与标注」页选择"
    return (f"{Path(get_name()).name}  (图片 {getattr(info, 'image_count', 0)} 张 · "
            f"标注 {getattr(info, 'label_count', 0)} 个)")


# ────────────────────────── 写 ──────────────────────────

def set_map(ds_map: Dict[str, Any]) -> None:
    """后台扫描完成后回填清单。

    2026-09-11: 扫描结果也落一条审计 —— 排查"训练页说没有数据集"时, 日志要能
    回答"什么时候扫的、扫到几个、扫的是哪个目录"。**扫到 0 个同样要留痕**:
    这正是最需要查的那种情况 (原实现只在 set_selection 里写审计, 而清单为空时
    valid=False, 一条都不会写 —— 数据目录空掉的整个过程在日志里完全消失)。
    命中数没变则不重复写, 避免反复 refresh_map_sync 把日志刷满。
    """
    global _MAP, _SCANNED, _SCAN_AUDIT_LAST
    with _LOCK:
        _MAP = dict(ds_map or {})
        _SCANNED = True
        n = len(_MAP)
    _bus().emit(EV_SCANNED, count=n)
    if n != _SCAN_AUDIT_LAST:
        _SCAN_AUDIT_LAST = n
        try:
            from infrastructure.audit import audit
            audit("ds_scan_done",
                  f"数据集扫描完成: 命中 {n} 个 · 扫描目录 {get_config_data_dir()}",
                  level="warn" if n == 0 else "info", module="dataset_state")
        except Exception:
            pass


def set_selection(name: str, persist: bool = True) -> None:
    """设置当前数据集。两页都可以调 —— 谁改谁负责发广播, 另一页跟着刷新。"""
    global _NAME
    name = (name or "").strip()
    with _LOCK:
        if name == _NAME:
            return
        _NAME = name
        valid = name in _MAP
    if persist and valid:
        fp = _last_ds_file()
        if fp is not None:
            try:
                fp.write_text(name, encoding="utf-8")
            except Exception:
                pass
    global _AUDIT_LAST
    if valid and name != _AUDIT_LAST:
        _AUDIT_LAST = name
        try:
            from infrastructure.audit import audit
            audit("ds_select", f"数据集选择变化 -> {name}", module="dataset_state")
        except Exception:
            pass
    _bus().emit(EV_CHANGED, name=name)


def restore_last() -> str:
    """从 last_dataset.txt 恢复上次选择 (清单为空时是 no-op)。"""
    fp = _last_ds_file()
    if fp is None or not fp.exists():
        return ""
    try:
        name = fp.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    with _LOCK:
        if name and name in _MAP:
            pass
        else:
            return ""
    set_selection(name, persist=False)
    return name


# ────────────────────────── 扫描 ──────────────────────────

def ensure_scan(force: bool = False) -> bool:
    """确保清单已就绪。幂等; 返回是否真的起了新扫描。

    扫描在后台线程做, 完成后 emit(EV_SCANNED); 调用方不要在这里等结果。

    2026-09-11 修: **决定不发新扫描时, 把当前状态重播一次**。
    原实现只在"这一刻起新扫描"的分支里发事件, 于是当清单早已扫过且**为空**时
    它是纯 no-op —— EV_SCANNED 一次都不发。后果: 先构造训练页 (它也会
    ensure_scan) 再构造数据集页时, 数据集页的状态标签**永久停在
    「正在扫描数据集...」**, 因为唯一能把它改掉的广播永远不会来。
    重播对订阅方是幂等的 (按当前清单再铺一次), 代价可忽略。
    """
    global _SCANNING, _SCANNED
    replay = False
    with _LOCK:
        if _SCANNING:
            return False          # 已有扫描在飞, 它完成时会发事件
        if _SCANNED and not force:
            replay = True
        else:
            _SCANNING = True
            if force:
                _SCANNED = False

    if replay:
        _bus().emit(EV_SCANNED, count=len(_MAP))
        return False

    def _work():
        global _SCANNING
        try:
            from core.trainer import scan_datasets
            _bus().emit(EV_SCANNED, count=-1)      # -1 = 正在扫
            set_map(scan_datasets())
            restore_last()
        except Exception as ex:
            try:
                from infrastructure.audit import audit
                audit("ds_scan_failed", str(ex), level="error", module="dataset_state")
            except Exception:
                pass
        finally:
            with _LOCK:
                _SCANNING = False

    threading.Thread(target=_work, daemon=True, name="ds-state-scan").start()
    return True


def refresh_map_sync() -> Dict[str, Any]:
    """同步重扫清单并写入共享状态。

    常规刷新一律走 `ensure_scan()` 后台线程 —— 同步扫描大目录会卡 UI。
    本函数只为「刚生成就立刻要用」的链路开口子 (H3 一键训练: 导出数据集后
    马上要选中它开训, 等不了后台那一趟)。
    """
    from core.trainer import scan_datasets
    m = scan_datasets()
    set_map(m)
    return m


def scan_models() -> list:
    """列出 output_dir 下的 .pt 模型 (按 mtime 倒序)。空时返回占位项。"""
    try:
        out = Path(get_config_output_dir())
        pts = [f.name for f in sorted(out.glob("*.pt"),
                                      key=lambda p: p.stat().st_mtime, reverse=True)] \
            if out.exists() else []
    except Exception:
        pts = []
    return pts or ["(请先训练/导入 .pt 模型)"]


def get_config_output_dir() -> str:
    from infrastructure.config import get_config
    return get_config().output_dir


def get_config_data_dir() -> str:
    """被扫描的数据集根目录 —— 空状态提示与审计都靠它说清"到底看了哪里"。"""
    try:
        from infrastructure.config import get_config
        return str(get_config().data_dir)
    except Exception:
        return "?"


def notify_models_changed() -> None:
    """训练页导出新模型后调用 —— 标注页据此刷新 .pt 下拉。"""
    _bus().emit(EV_MODELS_CHANGED)
