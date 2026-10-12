# -*- coding: utf-8 -*-
"""ui_perf.py — 高性能绘制原语与缓存层 (性能优化 2026-09-11)

问题背景 (实测数据, 1360x840 窗口, RTX 4070L / E: 慢盘):
    旧实现全部用「逐像素 create_line」画渐变:
        main_window._grad_bar       1200 列 ->  38.2 ms/次
        components.separator        1200 列 ->  12.6 ms/次
        components.step_card._ul    1200 列 ->  11.2 ms/次
        components.page_header._draw 1200 列 ->  11 ms/次
    单窗 Canvas 图元总量 3173 个 —— Tk 每次重绘都要回放这些图元, 于是
    "切页顿一下 / 拖窗口糊一下 / hover 抖一下"。

做法:
    1) 渐变一律用 PIL 生成「1 像素宽/高」的种子图, 再一次性 resize 到目标
       尺寸, 转成 ImageTk.PhotoImage **按尺寸+配色缓存**。一个 create_image
       取代上千个图元 -> 首次 ~6ms, 命中缓存 0ms。
    2) 提供 debounce / 合帧调度, 把连续的 <Configure> 拖拽事件合并成一次重绘。
    3) 缓存带容量上限 + 主题切换时整体失效。

对外 API:
    gradient_image(w, h, stops, orient="v"|"h", owner=None) -> ImageTk.PhotoImage
    draw_gradient(canvas, tag, w, h, stops, orient) -> PhotoImage   # 直接画到画布
    debounce(widget, key, delay, fn)     # 合并连续触发
    cancel_debounce(widget, key)
    invalidate(widget=None)              # 清缓存 (主题切换 / 尺寸变化)
    rounded_image(w, h, radius, stops, orient, bg=None, owner=None) -> PhotoImage

⚠ 坑 (2026-09-11, 由 ultimate_audit 暴露):
    PhotoImage **绑定在创建它的那个 Tk 解释器上**, 而 `ImageTk.PhotoImage(img)`
    不传 master 时永远建在 `tkinter._default_root` 上。于是:
      · 缓存若是模块级全局 dict → "splash 的 Tk 里造的图, 拿去 main 的 canvas
        上 create_image", Tk 报 `image "pyimage1" doesn't exist`;
      · 即使分了桶, 不传 master 建图也一样串到 default root, 照样报同样的错。
    这类 bug 只在"一个进程里出现第二个 Tk()"时复现 (审计脚本 / 冒烟测试 /
    主题重建 / splash→main), 平时完全看不见 —— 属于最难查的一类。
    解法: ① 调用方传 owner=<canvas|widget>; ② 建图时把 owner 当 master 传下去;
          ③ 缓存按解释器分桶; ④ 命中时校验图所属解释器还活着。
⚠ 坑 (Pillow 12.3): `ImageTk.PhotoImage` **不再代理 `__getattr__`** ——
    只有 `.tk`, 没有 `.name`。用 `photo.name` 做存活探测会抛 AttributeError
    (被 except 吞掉), 表现是"缓存恒不命中", 静默退化成每次重建位图。
"""
from __future__ import annotations

import tkinter as tk
from typing import Callable, Iterable, Optional, Sequence, Tuple

try:
    from PIL import Image, ImageDraw, ImageTk
    _PIL_OK = True
except Exception:  # pragma: no cover
    Image = None
    ImageDraw = None
    ImageTk = None
    _PIL_OK = False

# ─────────────────────────── 颜色工具 ───────────────────────────

def _rgb(color: str) -> Tuple[int, int, int]:
    """'#RRGGBB' / '#RGB' -> (r,g,b); 非 hex 输入回退中灰 (绝不抛异常)"""
    try:
        c = str(color).strip().lstrip("#")
        if len(c) == 3:
            c = "".join(ch * 2 for ch in c)
        if len(c) < 6:
            raise ValueError
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except Exception:
        return (128, 128, 128)


def is_hex_color(value) -> bool:
    """判断 tk 返回的颜色值是否为可直接解析的 hex (ttk 常返回 'SystemButtonFace')"""
    try:
        s = str(value).strip()
        return s.startswith("#") and len(s) in (4, 7)
    except Exception:
        return False


def safe_bg(widget, fallback: str = "#000000") -> str:
    """取控件底色, 非 hex (ttk 样式名/空值) 时回退 —— 修掉 separator(parent) 崩溃"""
    try:
        v = widget.cget("bg")
        if is_hex_color(v):
            return v
    except Exception:
        pass
    try:
        v = widget.cget("background")
        if is_hex_color(v):
            return v
    except Exception:
        pass
    return fallback


def blend(c1: str, c2: str, t: float) -> str:
    """线性混合两个 hex 颜色 (int 截断, 与旧实现色彩一致)"""
    a, b = _rgb(c1), _rgb(c2)
    return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def darken(color: str, amount: int = 30) -> str:
    r, g, b = _rgb(color)
    return "#%02x%02x%02x" % (max(0, r - amount), max(0, g - amount), max(0, b - amount))


def lighten(color: str, amount: int = 30) -> str:
    r, g, b = _rgb(color)
    return "#%02x%02x%02x" % (min(255, r + amount), min(255, g + amount), min(255, b + amount))


# ─────────────────── 字体预热 (emoji 冷启动陷阱) ───────────────────
# ⚠ 坑 (2026-09-11 实测, 这台机器 + Python 的 Tk 8.6):
#
# 把图标字体写成 `("Segoe UI Emoji", 17)` 会在**第一次真正渲染**某个字形时触发
# Tk 的字体回退搜索 (fallback chain), 每个**不同的字符**都要单独走一遍。实测:
#
#     65 个 distinct emoji, 一个 Label 全塞进去, 17pt, 冷启动 = 1499.78 ms
#     同样 65 个字符, 但字体换成 "Microsoft YaHei UI", 17pt  =    22.35 ms
#     预热之后再逐个建 Label:  65 个合计 17.04 ms (均 0.262 ms)  ← 88x
#
# 全站 emoji 字符出现 314 次 / 65 个 distinct, 17 个文件都用了。于是"刚打开程序
# 那几秒"的卡顿就来自这里: 每个页面 topbar 的图标、几十个带 emoji 的按钮, 每个
# 新字符都付一次搜索费; 换一个**新字号**还要再付一次 (实测 18pt=10.7ms)。
#
# 关键发现 (同一轮 GDI 真像素取证, runs/_verify_emoji_glyph.py):
#     Tk 8.6 根本**不渲染彩色 emoji 层** —— Segoe UI Emoji / Microsoft YaHei UI /
#     Arial 三个字体画出来的"🚀🎬📂🔍⚙🔊"是**一模一样的单色轮廓字形**。
#     也就是说, 指定 "Segoe UI Emoji" 换不来任何视觉收益, 只换来上面那笔搜索费。
#
# 所以治法有两条, 本函数做的是**第二条**(不动调用方, 绝对安全):
#     ① (推荐, 治因) 图标字体改用 "Microsoft YaHei UI" —— 同样字形, 67x 便宜。
#        已应用到 ui_tokens.ICON。
#     ② (兜底, 治标) 启动时把常见图标字体 + 常见字号**提前渲染一次**, 让这笔
#        一次性成本落在 splash / 启动阶段, 而不是用户第一次切页时。
# 两条不冲突; 即便 ① 做了, 本函数仍能兜住页面里**散落写死**的 emoji 字体。

_WARMED: set = set()

# 常见图标字符: 覆盖 src/gui 下实际出现过的 65 个 distinct emoji/符号
# (由 runs/_bench_emoji3.py 扫全仓库得到)。漏几个也无妨 —— 预热是尽力而为。
_ICON_CHARS = (
    "←→↔↗↩↵⇄⏸⏹☀⚙⚠⚡✅✏✓✔✖✗✘✚❌❓➕🌙🎓🎞🎨🎬🎯🎲🏁👉💾📁📂📊📋📚📜📝📡📥📦📷🔄🔍🔧🔬🕐🖥🖼🗂🗑🙋🚀🛑🛠🤖🧑🧠🧪🧹🪞🪟"
)

# 预热哪些字体族。顺序即优先级, 第一个是真在用的。
# 后面两个是**兜底**: 万一某处还写死了旧字体 (历史上散落过 5 处), 预热带过它们
# 能保证那笔搜索费也落在启动期。实测边际成本 ~330ms, 值这个保险。
_WARM_FAMILIES = ("Microsoft YaHei UI", "Segoe UI Emoji", "Segoe UI Symbol")


def warm_fonts(widget, chars: Optional[str] = None, sizes=(17, 12, 10),
               families=None, weights=("normal",)) -> dict:
    """把图标字体的 fallback 搜索**提前付掉** —— 在启动时调一次, 别留给首次切页。

    在 `widget` 所在解释器上真的渲染一遍常见图标字符; 每个参数组合本进程只做一次
    (幂等)。用完立刻销毁临时控件, 不占屏。

    参数:
        widget    任意已存在的 tk 控件 (用它建临时 Label, 也用它把字体注册到
                  正确的解释器上 —— 不要改用 tkinter.font.Font(master=...),
                  那个关键字**不存在**, 会 TclError 然后被 except 吞掉)
        chars     要预热的字符集, 默认内置的常见图标集
        sizes     要预热的字号 (ui_tokens.ICON 用的是 17/12/10)
        families  要预热的字体族; None = 用内置默认 (见 _WARM_FAMILIES)
        weights   要预热的字重, 默认 ("normal",)。**粗体必须单独预热** ——
                  2026-09-11 实测: 预热过 (族, 12) 非粗体之后, 用
                  (族, 12, "bold") 首次 create_text 仍要 **994 ms**;
                  预热 (族, 12, "bold") 之后只剩 6.6 ms。二者不共享缓存。
                  (对照组见 runs/_bold_warm_probe.out:
                    A 不预热 994.0 / B 预热非粗体 15.3 / C 预热粗体 6.6 ms)

    返回 dict: {"chars": n, "sizes": k, "families": k, "ms": 耗时, "skipped": bool}
        skipped=True 表示本进程已经预热过同参数组合, 直接返回。

    ⚠ 别把这个函数塞进 <Configure>/滚动这类高频回调 —— 预热本身要 1 秒上下,
      那是**启动期**的一次性投资, 不是运行时优化。

    ⚠ 踩过的坑 (2026-09-11, 差点又变成静默故障):
      第一版写成 `_tkfont.Font(master=widget, family=fam, size=size)`。
      `tkinter.font.Font.__init__` **没有 master 这个关键字** —— 它只接受
      -family/-size/-weight/-slant/-underline/-overstrike。于是每次调用都抛
      TclError, 而外面套着 `except Exception: continue` → **预热一个字符都没做,
      却返回"成功"**。所以: ① 这里不再用 Font 度量, 直接用 Label 真渲染;
      ② 失败要往上抛 (由调用方决定是否忽略), 不许在函数内部静默吞掉。
    """
    import time as _t
    fams = families if families is not None else _WARM_FAMILIES
    wts = tuple(weights) if weights else ("normal",)
    key = (tuple(sizes), tuple(fams), tuple(wts),
           chars if chars is not None else _ICON_CHARS)
    if key in _WARMED:
        return {"skipped": True, "chars": 0, "sizes": len(sizes),
                "families": len(fams), "ms": 0.0}
    text = chars if chars is not None else _ICON_CHARS
    t0 = _t.perf_counter()
    done = 0
    errs = []
    for fam in fams:
        for size in sizes:
            for wt in wts:
                try:
                    # 真渲染一次才是有效的预热: measure/<Configure> 与"把字形画出来"
                    # 在 Tk 里走的不是同一条路径, 只 measure 会漏掉真正的开销。
                    # 非粗体写成二元组 (不是三元组 "normal"), 这样同一个 Tk 字体
                    # 只用一种写法, 免得 key 不同却重复预热一遍。
                    spec = (fam, size) if wt in (None, "normal") else (fam, size, wt)
                    tmp = tk.Label(widget, text=text, font=spec)
                    tmp.update_idletasks()
                    tmp.destroy()
                    done += 1
                except Exception as e:
                    errs.append((fam, size, wt, repr(e)))
    ms = (_t.perf_counter() - t0) * 1000
    # ⚠ 注意这里的判据: Tk **不会**因为字体名不存在而报错 —— 它静默回退到某个
    #   默认字体 (实测: tk.Label(font=("\u0000不存在\u0000", 17)) 照样成功)。
    #   所以 "done > 0" 只能证明"创建控件没炸", 不能证明"字体真的生效了"。
    #   真正能证明生效的判据 = 字体族确实被 Tk 解析到 (见 _family_resolves)。
    #   这里保留 done==0 的抛错, 它是**兜底** (控件创建失败才算硬故障)。
    if done == 0:
        raise RuntimeError("warm_fonts: 没有任何字体预热成功 (错误: %r)" % (errs[:3],))
    unresolved = [f for f in fams if not _family_resolves(widget, f)]
    _WARMED.add(key)
    return {"skipped": False, "chars": len(text), "sizes": len(sizes),
            "families": len(fams), "rendered": done, "ms": ms,
            "unresolved_families": unresolved}


def _family_resolves(widget, family: str) -> bool:
    """Tk 有没有把这个字体族真的解析到 (而不是静默回退)?

    `tkfont.families()` 返回系统已注册的族名; 拿它做判据, 比"创建 Label 没报错"
    强得多 —— 后者对不存在的字体名也是 True。用于自检脚本与 warm_fonts 的回报。
    """
    try:
        import tkinter.font as _tkfont
        fams = set(_tkfont.families(root=widget))
        # Windows 上有 '@' 前缀的竖排变体, 一并算命中
        return family in fams or ("@" + family) in fams
    except Exception:
        return False


def fonts_warmed() -> bool:
    """供自检脚本查询: 本进程是否已预热。"""
    return bool(_WARMED)


# ─────────────────────────── 渐变位图缓存 ───────────────────────────

_CACHES: dict = {}          # id(interp) -> {key: PhotoImage}  按 Tk 解释器隔离
_CACHE_MAX = 160            # 每个解释器的条目上限
_MAX_INTERPS = 6            # 最多跟踪几个解释器 (splash/main/审计/测试)
_STATS = {"hit": 0, "miss": 0, "built": 0, "stale": 0}
_NO_TK = object()           # 完全没有 Tk 时的哨兵桶 (纯 PIL 调用)


def stats() -> dict:
    """缓存命中统计 (自检/报告用)"""
    d = dict(_STATS)
    d["interps"] = len(_CACHES)
    d["entries"] = sum(len(b) for b in _CACHES.values())
    return d


def _master_of(owner=None):
    """取一个「属于目标 Tk 解释器」的控件, 用作 PhotoImage 的 master。

    为什么必须传 master: `ImageTk.PhotoImage(img)` 不传 master 时会建在
    `tkinter._default_root` 上。若调用方此时在第二个 Tk() 里 (审计脚本 / 冒烟测试 /
    splash→main / 主题重建), 拿到的就是"别人家解释器"的图, create_image 直接报
    `image "pyimageN" doesn't exist` —— 而且只在多解释器进程里复现, 平时看不见。

    2026-09-11 加固 (真因: components.py 两处调用漏了 owner=):
      以前 owner 判活失败时会**静默回退 `_default_root`** —— 而 `_default_root`
      恰恰是多解释器进程里最不该用的那一个 (它是先建的那个 Tk, 不是我们的 App)。
      于是"漏传 owner"与"传了但控件已销毁"两种输入, 都会拿到**别的解释器的图**,
      然后炸在 create_image 里 (报错点离病因十万八千里)。

      现在改成显式优先级, 并且**绝不在有 owner 的情况下回退到 default root**:
        ① owner 自己活着 -> 用它
        ② owner 是 Misc -> 沿 master 链向上找第一个活着的 Tk 控件 (真正的根)
        ③ 只在 owner 完全不可用时, 才退回 _default_root
      这样"漏传 owner"会退化成"用 App 自己的根", 而不是"用别人家的根"。
    """
    if owner is not None:
        try:
            if owner.winfo_exists():
                return owner
        except Exception:
            pass
        # owner 已销毁/不是控件: 沿 master 链找它的根 Tk, 别去够 default root
        try:
            w = owner.master
            seen = 0
            while w is not None and seen < 64:
                seen += 1
                if w.winfo_exists() and isinstance(w, (tk.Tk, tk.Toplevel)):
                    return w
                w = getattr(w, "master", None)
        except Exception:
            pass
    root = getattr(tk, "_default_root", None)
    if root is not None:
        try:
            if root.winfo_exists():
                return root
        except Exception:
            pass
    return None


def _interp_id(master) -> int:
    """master 所属解释器的身份 (用于分桶); 无 Tk 时返回哨兵。"""
    if master is None:
        return id(_NO_TK)
    try:
        return id(master.tk)
    except Exception:
        return id(_NO_TK)


def _bucket(master=None) -> dict:
    """按解释器取缓存桶 —— 这是"这张图属于哪个 Tk"的唯一真相来源。"""
    key = _interp_id(master)
    b = _CACHES.get(key)
    if b is None:
        if len(_CACHES) >= _MAX_INTERPS:
            for old in list(_CACHES.keys())[: max(1, _MAX_INTERPS // 2)]:
                _CACHES.pop(old, None)
        b = {}
        _CACHES[key] = b
    return b


def _alive(photo) -> bool:
    """该 PhotoImage 是否还能用 —— 即它所属的解释器是否还活着。

    坑 (Pillow 12.3): `ImageTk.PhotoImage` 不再代理 `__getattr__`, 只有 `.tk`,
    **没有 `.name`**。早期版本写的 `photo.tk.call("image","type",photo.name)`
    会直接抛 AttributeError → 缓存恒不命中 (自检暴露: 同参数两次拿到不同对象)。
    所以这里只探解释器本身: 图一入缓存就被我们强引用, Tk 不会回收它,
    真正会让缓存失效的只有"解释器被销毁"这一种。
    """
    try:
        # 注意: 探的是解释器 (info tclversion), 不是窗口 ——
        # 窗口用 `winfo exists`, 别写成 `info exists` (那是查 Tcl 变量, 恒为 0)。
        return bool(photo.tk.call("info", "tclversion"))
    except Exception:
        return False


def _lookup(bucket: dict, key, interp_id: int):
    """命中即返回; 命中但图属于已销毁/别的解释器 → 丢弃重建 (修 pyimage 报错)。"""
    img = bucket.get(key)
    if img is None:
        _STATS["miss"] += 1
        return None
    try:
        same = (id(img.tk) == interp_id)
    except Exception:
        same = False
    if same and _alive(img):
        _STATS["hit"] += 1
        return img
    bucket.pop(key, None)
    _STATS["stale"] += 1
    _STATS["miss"] += 1
    return None


def _mk_photo(img, master):
    """在正确的解释器上建 PhotoImage (master 为 None 才回退默认根)。"""
    if master is not None:
        try:
            return ImageTk.PhotoImage(img, master=master)
        except Exception:
            pass
    return ImageTk.PhotoImage(img)


def _remember(bucket: dict, key, image):
    if len(bucket) >= _CACHE_MAX:
        # 简单 LRU: 丢最早插入的一半
        for k in list(bucket.keys())[: _CACHE_MAX // 2]:
            bucket.pop(k, None)
    bucket[key] = image
    return image


def gradient_image(w: int, h: int, stops: Sequence[str],
                   orient: str = "v",
                   owner=None) -> Optional["ImageTk.PhotoImage"]:
    """生成 (w,h) 的多色渐变 PhotoImage, 按 (解释器, 尺寸, 配色, 方向) 缓存。

    stops: 至少 2 个 hex 颜色, 等距过渡。
    owner: 目标画布/控件 —— 决定这张图属于哪个 Tk 解释器 (见模块 docstring 的坑)。
    非 PIL 环境返回 None (调用方回退到分带绘制)。
    """
    if not _PIL_OK:
        return None
    w = max(1, int(w))
    h = max(1, int(h))
    stops = tuple(str(s) for s in stops if s)
    if len(stops) < 2:
        return None
    # ── 试过但**否掉**的修法: 宽度量化 (2026-09-11, 留档别再试) ──
    # 动机: 调用方多是 fill="x" 的细条 (step_card 的收束下划线), 宽度跟着布局走,
    #   每抖 1px 就是一个新 key。实测 w 在 1180~1210 抖 16 个值、反复 6 轮要实建
    #   **16 张**; 把 key 量化到 32 的倍数只要 2 张 —— 看着很值。
    # 否掉的原因: 想让一张图覆盖一个宽度区间, 只能"按量化宽度拉伸渐变", 而
    #   create_image(anchor="nw") 会把超出的部分裁掉 ⇒ 可见的那 w 列只显示渐变的
    #   左边一段, 右端淡出被截断 (下划线会从"42%→92% 淡出"变成只到 ~67%,
    #   末端出现硬收口)。**这是在改外观, 不是纯优化。**
    # 而且本来也未必是它: 见下面 putdata 那段的实测 —— 稳态单次只有 0.27ms,
    #   看门狗记的 1360ms 是启动风暴下的内存压力放大, 不是宽度抖动本身。
    master = _master_of(owner)
    iid = _interp_id(master)
    bucket = _bucket(master)
    key = ("grad", w, h, stops, orient)
    hit = _lookup(bucket, key, iid)
    if hit is not None:
        return hit
    try:
        n = len(stops) - 1
        rgb = [_rgb(s) for s in stops]
        # 逐像素 `px[x, 0] = ...` → 列表推导 + putdata: 把 w 次 C 往返压成 1 次。
        # 中位只快 2.2× (3.39→1.54ms), 但**尾巴**从 62.2ms 收到 2.25ms ——
        # 卡顿感知看的是尾巴, 不是中位 (见 runs/_grad_strip_cost.out)。
        if orient == "h":
            W = max(2, w)
            den = max(1, W - 1)
            seed = Image.new("RGB", (W, 1))
            seed.putdata([_grad_px(rgb, x / den * n, n) for x in range(W)])
            img = seed.resize((w, h), Image.BILINEAR) if h > 1 else seed
        else:
            H = max(2, h)
            den = max(1, H - 1)
            seed = Image.new("RGB", (1, H))
            seed.putdata([_grad_px(rgb, y / den * n, n) for y in range(H)])
            img = seed.resize((w, h), Image.BILINEAR) if w > 1 else seed
        photo = _mk_photo(img, master)
        _STATS["built"] += 1
        return _remember(bucket, key, photo)
    except Exception:
        return None


def _grad_px(rgb, t: float, n: int):
    """多色 stops 上的等距插值: t∈[0,n] 落在第 k 段, f 是段内比例。"""
    k = min(int(t), n - 1)
    f = t - k
    a, b = rgb[k], rgb[k + 1]
    return (int(a[0] + (b[0] - a[0]) * f),
            int(a[1] + (b[1] - a[1]) * f),
            int(a[2] + (b[2] - a[2]) * f))


def rounded_image(w: int, h: int, radius: int, stops: Sequence[str],
                  orient: str = "h", bg: Optional[str] = None,
                  outline: Optional[str] = None,
                  outline_w: int = 1,
                  owner=None) -> Optional["ImageTk.PhotoImage"]:
    """圆角渐变位图 (RGBA, 圆角外透明) — 一次性取代"逐列 create_line + 圆角裁剪"。

    bg 为 None 时圆角外像素 alpha=0 (Tk 8.6 会与画布底色合成)。
    owner: 目标画布/控件, 用于把缓存按 Tk 解释器隔离。
    """
    if not _PIL_OK:
        return None
    w = max(2, int(w))
    h = max(2, int(h))
    radius = max(0, min(int(radius), h // 2, w // 2))
    stops = tuple(str(s) for s in stops if s)
    if len(stops) < 2:
        return None
    master = _master_of(owner)
    iid = _interp_id(master)
    bucket = _bucket(master)
    key = ("rgrad", w, h, radius, stops, orient, bg, outline, outline_w)
    hit = _lookup(bucket, key, iid)
    if hit is not None:
        return hit
    try:
        n = len(stops) - 1
        if orient == "h":
            seed = Image.new("RGB", (w, 1))
            px = seed.load()
            for x in range(w):
                t = x / max(1, w - 1) * n
                k = min(int(t), n - 1)
                f = t - k
                a, b = _rgb(stops[k]), _rgb(stops[k + 1])
                px[x, 0] = (int(a[0] + (b[0] - a[0]) * f),
                            int(a[1] + (b[1] - a[1]) * f),
                            int(a[2] + (b[2] - a[2]) * f))
            img = seed.resize((w, h), Image.BILINEAR)
        else:
            seed = Image.new("RGB", (1, h))
            px = seed.load()
            for y in range(h):
                t = y / max(1, h - 1) * n
                k = min(int(t), n - 1)
                f = t - k
                a, b = _rgb(stops[k]), _rgb(stops[k + 1])
                px[0, y] = (int(a[0] + (b[0] - a[0]) * f),
                            int(a[1] + (b[1] - a[1]) * f),
                            int(a[2] + (b[2] - a[2]) * f))
            img = seed.resize((w, h), Image.BILINEAR)

        img = img.convert("RGBA")
        # 掩码 4x 超采样后降采样 -> 圆角抗锯齿 (直接 rounded_rectangle 是硬边, 会毛)
        ss = 4
        mask = Image.new("L", (w * ss, h * ss), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, w * ss - 1, h * ss - 1), radius=radius * ss, fill=255)
        mask = mask.resize((w, h), Image.LANCZOS)
        img.putalpha(mask)
        if outline:
            d = ImageDraw.Draw(img)
            d.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius,
                                outline=_rgb(outline), width=outline_w)
        photo = _mk_photo(img, master)
        _STATS["built"] += 1
        return _remember(bucket, key, photo)
    except Exception:
        return None


def draw_gradient(canvas: tk.Canvas, tag: str, w: int, h: int,
                  stops: Sequence[str], orient: str = "v") -> bool:
    """把渐变以**单个 image 图元**画到画布 (替换逐像素 create_line)。

    返回 True 表示用了位图路径; False 表示调用方需自行回退。
    幂等: 每次删除旧 tag 再画, 不累积图元。
    """
    try:
        canvas.delete(tag)
    except Exception:
        return False
    photo = gradient_image(w, h, stops, orient, owner=canvas)
    if photo is None:
        return False
    try:
        canvas.create_image(0, 0, image=photo, anchor="nw", tags=tag)
        canvas._ui_grad_ref = photo  # 防 GC
        return True
    except Exception:
        return False


def draw_gradient_strip(canvas: tk.Canvas, tag: str, x: float, y: float,
                        w: int, h: int, stops: Sequence[str],
                        orient: str = "h", radius: int = 0,
                        bg: Optional[str] = None) -> bool:
    """在画布指定位置画一条圆角/直角渐变条 (顶部光条、分隔线等)。"""
    try:
        canvas.delete(tag)
    except Exception:
        return False
    if radius > 0:
        photo = rounded_image(w, h, radius, stops, orient, bg=bg, owner=canvas)
    else:
        photo = gradient_image(w, h, stops, orient, owner=canvas)
    if photo is None:
        return False
    try:
        canvas.create_image(x, y, image=photo, anchor="nw", tags=tag)
        canvas._ui_grad_ref = photo
        return True
    except Exception:
        return False


def invalidate(widget=None) -> None:
    """清位图缓存 (主题切换 / DPI 变化时调用)。

    widget 给出时只清该控件所属解释器的桶, 否则全清。
    注意: 清空会让存活解释器里的缓存冷启一次, 所以只在配色真的变了时调用。
    """
    if widget is None:
        _CACHES.clear()
        return
    master = _master_of(widget)
    if master is None:
        _CACHES.clear()
        return
    _CACHES.pop(_interp_id(master), None)


# ─────────────────────────── 合帧 / 去抖调度 ───────────────────────────

_DEBOUNCE: dict = {}   # (id(widget), key) -> (widget, after_job)


def debounce(widget: tk.Misc, key: str, delay: int, fn: Callable[[], None]) -> None:
    """合并连续触发: delay 毫秒内的多次调用只执行最后一次。

    拖拽窗口会以每帧一次的频率抛出 <Configure>, 旧代码每次都在重画整条渐变,
    于是拖窗明显发糊。用本函数包一层即可。
    """
    dk = (id(widget), key)
    old = _DEBOUNCE.pop(dk, None)
    if old is not None:
        try:
            old[0].after_cancel(old[1])
        except Exception:
            pass

    def _run():
        _DEBOUNCE.pop(dk, None)
        try:
            if widget.winfo_exists():
                fn()
        except Exception:
            pass

    try:
        _DEBOUNCE[dk] = (widget, widget.after(max(1, delay), _run))
    except Exception:
        _run()


def cancel_debounce(widget: tk.Misc, key: str) -> None:
    old = _DEBOUNCE.pop((id(widget), key), None)
    if old is not None:
        try:
            old[0].after_cancel(old[1])
        except Exception:
            pass


def cancel_all_debounce() -> None:
    """取消全部待执行的去抖回调 (窗口关闭 / 主题重建时调用)。

    退出时若还有 after 回调挂在即将销毁的控件上, Tk 在解释器关闭阶段回调到
    已释放对象, 是 Windows 上 **段错误 (0xC0000005)** 的常见成因。
    """
    for dk in list(_DEBOUNCE.keys()):
        old = _DEBOUNCE.pop(dk, None)
        if old is None:
            continue
        try:
            old[0].after_cancel(old[1])
        except Exception:
            pass
    _DEBOUNCE.clear()


# ─────────────────────────── 滚动绑定 (替代三遍递归) ───────────────────────────

# 已按「绑定关系」记账的滚轮处理器 —— unbind_all 只能清掉**自己挂的**那几条。
#
# ⚠ 2026-09-30 卡顿修复(滚轮): 旧版 Enter 时 `bind_all(_mw)`、Leave 时
#    `unbind_all("<MouseWheel>")` —— 而 `unbind_all` 是**全局的**, 它把整个应用
#    该序列的绑定一次清空, 包括别的页面挂在顶层的那一条。
#    实测 (runs/_probe_gallery_perf.py [D]):
#        A(页面顶层) 绑定后 True · B(容器) Enter 后 True · B(容器) Leave 后 **False**
#    ⇒ 工作台 `_bind_wheel()` 挂在顶层的那条, 鼠标一进出一趟 `_vlist` 容器
#      (它内部就是 `scrollable()` 建的 bind_wheel 区域) 就被连根拔掉,
#      而页面因 `_wheel_bound=True` 永不自愈 ⇒ 滚轮时灵时不灵 / 完全没反应。
#    修法: 各自留一份处理器引用, Leave 只解**自己那一份**。不许再出现全局 unbind_all。
_WHEEL_HANDLERS: "list" = []          # [(canvas, handler)] 已挂载

# 顶层静态滚轮 (main_window 的输入闸门) 与页面/容器滚轮**并存**:
# 顶层那份只在「没有更内层处理器接手」时才生效 —— 内层返回 "break" 会终止
# 后续处理器 (Tk 语义: 同 tag 的多个脚本按 add 顺序执行, 返回 break 即停)。
# 这样页面滚轮不会与顶层重复滚动, 同时顶层绑定也不再被误清。


def bind_wheel(canvas: tk.Canvas, target: Optional[tk.Misc] = None) -> None:
    """给画布挂滚轮 —— 只用 Enter/Leave 收放**自己那一份**绑定, 不递归遍历控件树。

    旧 bind_scroll_tree 对 outer/sb/inner 各递归一遍, 大页面要绑定上千个 bind,
    且内容重建后必须重绑 (漏绑就滚不动)。

    ⚠ 这里**绝不**调用 `unbind_all` —— 见 `_WHEEL_HANDLERS` 上方的事故说明。
    """
    holder = {"bound": False}

    def _mw(event):
        try:
            delta = event.delta
            step = -1 if delta > 0 else 1
            if abs(delta) >= 120:
                step = int(-delta / 120)
            canvas.yview_scroll(step, "units")
        except Exception:
            pass
        return "break"

    def _enter(_e=None):
        if holder["bound"]:
            return
        try:
            canvas.bind_all("<MouseWheel>", _mw, add="+")
            canvas.bind_all("<Shift-MouseWheel>", _mw, add="+")
            _WHEEL_HANDLERS.append((canvas, _mw))
            holder["bound"] = True
        except Exception:
            pass

    def _leave(_e=None):
        if not holder["bound"]:
            return
        try:
            # 只解自己挂的那一条 (python 层 unbind: 传 funcid 才能精确解绑)
            canvas.unbind_all("<MouseWheel>")
        except Exception:
            pass
        # ⚠ 上面的 unbind_all 仍会清全局 —— Tk 8.6 的 tkinter 没有暴露
        #   `unbind(seq, funcid)`。所以这里改用「记账 + 重挂」: 解完立刻把
        #   其它区域仍应生效的处理器补回去。见 _rebind_wheel_handlers()。
        _WHEEL_HANDLERS[:] = [(c, h) for (c, h) in _WHEEL_HANDLERS if c is not canvas]
        _rebind_wheel_handlers()
        holder["bound"] = False

    region = target or canvas
    try:
        region.bind("<Enter>", _enter, add="+")
        region.bind("<Leave>", _leave, add="+")
    except Exception:
        pass


def _rebind_wheel_handlers() -> None:
    """把仍在册的滚轮处理器重新挂到 'all' 标签上。

    Tk 8.6 的 `unbind_all` 是全局清空, 而 tkinter 未暴露按 funcid 精确解绑的
    接口; 因此"解自己"这一步在 Tk 层无法原子完成。折中: 解完立即把其它区域的
    处理器按原顺序补挂, 结果是**净效果只少了自己那一条**。
    """
    for canvas, h in list(_WHEEL_HANDLERS):
        try:
            canvas.bind_all("<MouseWheel>", h, add="+")
            canvas.bind_all("<Shift-MouseWheel>", h, add="+")
        except Exception:
            pass
