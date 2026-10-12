# -*- coding: utf-8 -*-
"""thumb_pool.py — 图片扫描 + 缩略图生成的后台池 (主线程绝不做这两件事)

为什么要有它 (2026-09-11)
    `dataset_page._ds_show_preview` 原来在**主线程**干三件重活:
      ① `sorted(ds_path.rglob("*"))` —— 全树枚举, 而且对**每个条目**调 `is_file()`
         (每项一次 stat)。实测比 `os.walk` 慢 **8.3~11.5 倍**
         (runs: 245.7ms vs 29.6ms; tools: 681.2ms vs 59.2ms)。
      ② 明明只要最后 16 张, 却把整棵树都排了序。
      ③ `Image.open().convert("RGB")` + `thumbnail` **同步**解 16 张
         (实测单次 **10087ms** —— 大图 + E: USB 盘)。
    在 12 秒的观察窗口里它占 **22/28 次卡顿、累计 126 秒**, 是用户报的
    "使用到一半自己会卡"的**唯一主因**。

    h3_factory_page 里已有一套等价且带回归测试的实现
    (`ui_perf_verify.py::thumb_async`)。本模块把它抽成共享件, 免得同一份逻辑
    再写第三遍; dataset 页与 h3 页共用同一套约定。

分工 (照 h3 的既有约定, 别混)
    · 后台线程: 纯文件系统 + 纯 PIL —— 扫描、解码、落盘。**绝不碰 Tk**。
    · 主线程: 只把现成的小 jpg 读成 `PhotoImage` (`photo()`), 再 create_image。

⚠ 常驻线程, 不是每批新建
    "为每批任务新建线程" 本身会被内存压力放大成百毫秒级卡顿 (h3 侧已实测),
    所以这里是一条常驻 worker, 死了会自愈重建。
"""
from __future__ import annotations

import hashlib
import os
import queue
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

# 默认图片扩展名
IMG_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")

_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "runs" / ".thumb_cache"

# ── 扫描上限: 防止在超大数据集上把后台线程也拖成分钟级 ──
MAX_DIRS = 4000          # 最多进入多少个目录
MAX_FILES = 40000        # 最多考察多少个文件项
MAX_CAND = 4000          # 最多收集多少个候选 (之后只保留最新 N)


def scan_images(root, exts: Sequence[str] = IMG_EXTS, want: int = 16,
                max_dirs: int = MAX_DIRS, max_files: int = MAX_FILES,
                max_cand: int = MAX_CAND) -> List[Tuple[float, str]]:
    """**纯函数, 可在后台线程调用**: 找出 root 下最新的 `want` 张图片。

    用 `os.scandir` 递归而不是 `Path.rglob`:
      · `DirEntry.is_file()` / `DirEntry.stat()` 在 Windows 上直接来自目录枚举,
        基本免费; 而 `Path.is_file()` 每项都要单独 stat (实测 0.010~0.014 ms/项,
        整棵树就是几百 ms)。
      · 扩展名过滤**不花钱**, 所以先过滤再取 mtime, stat 次数降到只剩真图片。

    返回 [(mtime, path), ...] 按 mtime 倒序 (最新在前); 失败返回 []。
    有界: 超过 max_dirs / max_files 就停 —— 宁可少给几张, 不许把主线程饿死。
    """
    exts_l = tuple(e.lower() for e in exts)
    out: List[Tuple[float, str]] = []
    n_dirs = n_files = 0
    stack = [str(root)]
    try:
        while stack:
            d = stack.pop()
            n_dirs += 1
            if n_dirs > max_dirs:
                break
            try:
                with os.scandir(d) as it:
                    for e in it:
                        n_files += 1
                        if n_files > max_files:
                            return sorted(out, reverse=True)[:want]
                        try:
                            if e.is_dir(follow_symlinks=False):
                                stack.append(e.path)
                                continue
                            if not e.is_file(follow_symlinks=False):
                                continue
                        except OSError:
                            continue
                        if os.path.splitext(e.name)[1].lower() not in exts_l:
                            continue
                        try:
                            mt = e.stat(follow_symlinks=False).st_mtime
                        except OSError:
                            mt = 0.0
                        out.append((mt, e.path))
                        if len(out) >= max_cand * 2:
                            # 保留最新的一半, 控制内存
                            out = sorted(out, reverse=True)[:max_cand]
            except OSError:
                continue
    except Exception:
        pass
    return sorted(out, reverse=True)[:want]


# ── 缩略图磁盘缓存 ──────────────────────────────────────────────

def build_thumb_file(src, size) -> Optional[str]:
    """**纯 PIL, 可在后台线程调用**: 生成磁盘缩略图缓存, 返回缓存文件路径。

    绝不碰 Tk (`PhotoImage` 只能在主线程造)。
    写入用 `.part` + `os.replace` 原子落地 —— 直接写目标文件时, 另一个线程/进程
    可能读到写了一半的 jpg (`Image.open` 抛 "image file is truncated")。
    """
    try:
        st = os.stat(src)
        key = hashlib.md5(
            ("%s|%s|%s|%sx%s" % (src, st.st_mtime, st.st_size, size[0], size[1])).encode()
        ).hexdigest()
        cache_f = _CACHE_DIR / ("%s.jpg" % key)
        if cache_f.exists():
            return str(cache_f)
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        from gui.image_cache import open_scaled
        im = open_scaled(src, size[0], size[1])
        if im is None:
            return None
        if im.mode != "RGB":
            im = im.convert("RGB")
        tmp = cache_f.with_suffix(".part")
        im.save(tmp, "JPEG", quality=82)
        os.replace(tmp, cache_f)
        return str(cache_f)
    except Exception:
        return None


# ── 常驻后台池 ─────────────────────────────────────────────────

_JOBS: "queue.Queue" = queue.Queue()
_DONE: "queue.Queue" = queue.Queue()
_TH: list = []
_INFLIGHT = [0]          # worker 已取走但还没做完的任务数


def _worker() -> None:
    while True:
        try:
            job = _JOBS.get()
        except Exception:
            return
        if job is None:
            return
        src, size, token = job
        _INFLIGHT[0] += 1
        try:
            _DONE.put((src, size, token, build_thumb_file(src, size)))
        except Exception:
            try:
                _DONE.put((src, size, token, None))
            except Exception:
                pass
        finally:
            _INFLIGHT[0] -= 1


def _ensure() -> None:
    """懒启动常驻线程; 线程死了会自愈重建 (否则缩略图永久静默不出)。"""
    th = _TH[0] if _TH else None
    if th is not None and th.is_alive():
        return
    th = threading.Thread(target=_worker, daemon=True, name="thumb-pool")
    _TH[:] = [th]
    th.start()


def submit(src, size, token=None) -> bool:
    """把"生成缩略图缓存"交给后台线程; 结果稍后由 take_all() 取回。

    token: 调用方用来丢弃过期结果 (切换数据集后旧任务的结果不该再贴上)。
    """
    _ensure()
    try:
        _JOBS.put_nowait((str(src), tuple(size), token))
        return True
    except Exception:
        return False


def take_all(limit: int = 256) -> list:
    """主线程取回已完成的任务 -> [(src, size, token, cache_file_or_None)]。"""
    out = []
    for _ in range(limit):
        try:
            out.append(_DONE.get_nowait())
        except Exception:
            break
    return out


def pending() -> int:
    """还有多少任务没完成 = 排队中 + **在途**(worker 已取走) + 已完成待取。

    ⚠ 只算 `_JOBS.qsize() + _DONE.qsize()` 是不够的: worker 一旦把任务从队列里
    取走, 两个队列都会"看起来空了", 而它其实还在解码 —— 调用方会据此误判
    "全干完了" 并停掉轮询, 于是最后几张缩略图**永远不出现**。
    """
    try:
        return _JOBS.qsize() + _DONE.qsize() + _INFLIGHT[0]
    except Exception:
        return 0


# ── 主线程: 读成 PhotoImage ────────────────────────────────────

_MEM: "OrderedDict" = OrderedDict()
_MEM_MAX = 400


def photo(cache_f, key=None, master=None):
    """**主线程**: 把已生成的缩略图 jpg 读成 PhotoImage 并进内存 LRU。

    ⚠ 必须传 master (2026-09-11 踩到, 当场复现):
        `ImageTk.PhotoImage(im)` **不传 master 时永远建在 `tkinter._default_root`**。
        一个进程里只要出现过第二个 `Tk()` (启动 splash / 审计脚本 / 冒烟测试),
        `_default_root` 就是**别人家的解释器**, 于是:
            create_image -> TclError: image "pyimage47" doesn't exist
        本模块第一版就是这么写的, 结果数据集预览**只贴出 1/16 张就静默停住**
        (异常被 drain 的 except 吞掉)。这与 `ui_perf._master_of` 那条规矩同源:
        **凡 PhotoImage 一律显式指定所属解释器**, 别指望默认值。
    """
    from PIL import Image, ImageTk
    with open(cache_f, "rb") as f:
        im = Image.open(f).convert("RGB")
    if master is not None:
        try:
            ph = ImageTk.PhotoImage(im, master=master)
        except Exception:
            ph = ImageTk.PhotoImage(im)
    else:
        ph = ImageTk.PhotoImage(im)
    # master 可能跨解释器, 用缓存文件路径做 key (与调用方的 key 分开存)
    k = key or Path(cache_f).stem
    _MEM[k] = ph
    _MEM.move_to_end(k)
    while len(_MEM) > _MEM_MAX:
        _MEM.popitem(last=False)
    return ph


# ── 后台扫描器 (与缩略图池分开, 因为扫描是"一次性任务"而非池化) ──

_SCAN_DONE: "queue.Queue" = queue.Queue()
_SCAN_BUSY = [False]


def scan_async(root, token, want: int = 16, exts: Iterable[str] = IMG_EXTS) -> bool:
    """后台跑一次 scan_images; 结果由 take_scan() 取回。同一时刻只跑一个。"""
    if _SCAN_BUSY[0]:
        return False
    _SCAN_BUSY[0] = True

    def _run():
        try:
            res = scan_images(root, tuple(exts), want)
        except Exception:
            res = []
        finally:
            _SCAN_BUSY[0] = False
        try:
            _SCAN_DONE.put((token, res))
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True, name="thumb-scan").start()
    return True


def take_scan():
    """取回一次扫描结果 -> (token, [(mtime, path)]) 或 None。"""
    try:
        return _SCAN_DONE.get_nowait()
    except Exception:
        return None


def scan_busy() -> bool:
    return bool(_SCAN_BUSY[0])
