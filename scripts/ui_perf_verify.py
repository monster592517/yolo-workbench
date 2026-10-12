# -*- coding: utf-8 -*-
"""ui_perf_verify.py — UI 性能改造回归自检 (2026-09-11)

Purpose
    把这一轮"界面整洁 + 动画流畅 + 速度提升"改造的**可回归断言**全部固化成
    一个可重复执行的脚本。改动 ui_perf / motion_kit / poll_kit / lazy_import
    或任意页面之后, 跑一遍就知道有没有把优化改回去。

Covers
    1. ui_perf     — 渐变位图缓存命中(同一对象)/ 非 hex 底色不崩 / 去抖合并
    2. motion_kit  — 单时钟多动画 / cancel_all 归零 / pulse 可停
    3. poll_kit    — 三档退避 / 控件销毁时 after 定时器被回收
    4. lazy_import — 探测微秒级 / 后台预热
    5. App         — 全部页面全建 + 全切 + 干净退出
    6. _fit_px     — 截断结果与旧实现逐字符一致 + measure 调用量下降
    7. thumb_async — 缩略图缺缓存不再走主线程 (端到端: 丢后台→取回→贴图→命中)
    8. font_lag    — 图标字体不再用昂贵的 Segoe UI Emoji + warm_fonts 真的生效
                     (2026-09-11: 这笔字体回退搜索是"刚打开程序那几秒卡"的主因)
    9. bmp_owner   — 位图必须建在**目标解释器**上 (漏传 owner= 会炸 pyimage;
                     只在"一个进程里出现第二个 Tk"时复现, 平时看不见)
   10. text_clip   — 文字裁切/遮挡: 画布文本 bbox 溢出边界、被后画的实心形状
                     盖住、Label 需要宽 > 分配宽。t_text_clip_selftest 是它的
                     正对照(造三种已知缺陷喂进去); 真实扫描在 App 套件里做。
                     (2026-09-11: 这是"部分文字被遮挡"的另一条成因 —— 与
                      font_family 的"字写错"互相掩盖, 改完字体必须重新量)
   11. dup_def    — 类内同名方法重复定义: 后定义的**静默覆盖**前面的。
                     (2026-09-12: 抓到 2 处; 其中 `_render_task_zh` 两版逻辑不同
                      ⇒ 旧版的 `🎲 当前组合` 从来没被渲染过, 不报错不告警)
                     纯 AST, 不需要 Tk/显示器 ⇒ `--no-app` 也跑。

Usage
    python scripts/ui_perf_verify.py            # 全部
    python scripts/ui_perf_verify.py --no-app   # 跳过 GUI 应用级(无需显示器)
Exit code: 0 = 全部通过, 1 = 有失败项
"""
from __future__ import annotations

import argparse
import re
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

_PASS = 0
_FAIL = 0
_RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
    else:
        _FAIL += 1
    _RESULTS.append((name, bool(cond), detail))
    print("  [%s] %-46s %s" % ("PASS" if cond else "FAIL", name, detail))
    return bool(cond)


def section(title: str) -> None:
    print("\n=== %s ===" % title)


# ── 1. ui_perf ────────────────────────────────────────────────────────
def t_ui_perf() -> None:
    section("ui_perf — 绘制原语与缓存")
    import tkinter as tk
    from gui import ui_perf as up

    root = tk.Tk()
    root.withdraw()

    # 1.1 渐变位图缓存: 同参数必须命中同一对象 (逐像素重画已被彻底取代)
    a = up.gradient_image(64, 16, ("#101820", "#3A6EA5"), "h")
    b = up.gradient_image(64, 16, ("#101820", "#3A6EA5"), "h")
    check("gradient_image 同参数命中缓存", a is b, "id=%s" % (id(a) == id(b)))

    # 1.2 不同尺寸/方向不可串味
    c = up.gradient_image(64, 32, ("#101820", "#3A6EA5"), "h")
    d = up.gradient_image(64, 16, ("#101820", "#3A6EA5"), "v")
    check("不同尺寸/方向分槽缓存", (c is not a) and (d is not a))

    # 1.3 圆角位图
    r1 = up.rounded_image(80, 24, 8, ("#1B2430", "#2C3E50"), "h")
    r2 = up.rounded_image(80, 24, 8, ("#1B2430", "#2C3E50"), "h")
    check("rounded_image 同参数命中缓存", r1 is r2)

    # 1.4 非 hex 底色回退 (ttk 控件 cget('bg') 会返回样式名 → 原实现直接崩)
    check("is_hex_color 拒绝样式名", not up.is_hex_color("SystemButtonFace"))
    check("safe_bg 回退不抛异常", up.safe_bg(root, "#123456") == "#123456",
          "root(bg 可能是样式名) -> %s" % up.safe_bg(root, "#123456"))
    check("_rgb 对非 hex 回退中灰", up._rgb("SystemButtonFace") == (128, 128, 128))

    # 1.5 去抖: 连打 N 次只执行 1 次
    hits = []
    for _ in range(3):
        up.debounce(root, "k", 60, lambda: hits.append(1))
    for _ in range(20):
        root.update()
        time.sleep(0.02)
    check("debounce 3 连击只执行 1 次", len(hits) == 1, "hits=%d" % len(hits))

    # 1.6 cancel_all_debounce
    hits2 = []
    up.debounce(root, "k2", 80, lambda: hits2.append(1))
    up.cancel_all_debounce()
    for _ in range(12):
        root.update()
        time.sleep(0.02)
    check("cancel_all_debounce 生效", len(hits2) == 0)

    # 1.7 invalidate 后缓存重建 (换主题必须失效)
    before = up.gradient_image(50, 10, ("#000000", "#FFFFFF"), "h")
    up.invalidate()
    after = up.gradient_image(50, 10, ("#000000", "#FFFFFF"), "h")
    check("invalidate 后缓存重建", after is not before)
    check("缓存有上限 (不无限膨胀)", up._CACHE_MAX > 0, "_CACHE_MAX=%d" % up._CACHE_MAX)

    # 1.8 位图缓存必须按 Tk 解释器隔离 —— 回归 "image pyimage1 doesn't exist"
    #     复现场景: 同进程出现第二个 Tk() (审计脚本 / 冒烟测试 / splash→main),
    #     旧实现是模块级全局缓存, 会把 A 解释器的 PhotoImage 拿到 B 上 create_image。
    _errs: list[str] = []

    def _draw(canvas, img) -> bool:
        try:
            canvas.create_image(0, 0, image=img, anchor="nw")
            return True
        except Exception as e:
            _errs.append(str(e)[:70])
            return False

    cv_a = tk.Canvas(root, width=60, height=20, highlightthickness=0)
    stops_r = ("#123456", "#654321")
    ia = up.rounded_image(60, 20, 6, stops_r, owner=cv_a)
    check("解释器 A 取到圆角位图", ia is not None)
    check("A 的图能画在 A 的画布上", _draw(cv_a, ia) if ia else False)

    b_root = tk.Tk()
    b_root.withdraw()
    cv_b = tk.Canvas(b_root, width=60, height=20, highlightthickness=0)
    ib = up.rounded_image(60, 20, 6, stops_r, owner=cv_b)
    check("解释器 B 不复用 A 的位图 (旧实现在此串味)", ib is not ia and ib is not None)
    check("B 的图能画在 B 的画布上", _draw(cv_b, ib) if ib else False)
    check("缓存按解释器分桶", up.stats()["interps"] >= 2,
          "interps=%d" % up.stats()["interps"])

    # B 销毁后再取 (owner 指向 A): 绝不能把 B 的尸体图递出去
    b_root.destroy()
    ic = up.rounded_image(60, 20, 6, stops_r, owner=cv_a)
    check("B 销毁后不递出尸体图", ic is not ib and ic is not None)
    check("B 销毁后回落到 A 的可用图", _draw(cv_a, ic) if ic else False,
          "; ".join(_errs))

    root.destroy()


# ── 2. motion_kit ─────────────────────────────────────────────────────
def t_motion_kit() -> None:
    section("motion_kit — 全局单时钟动画引擎")
    import tkinter as tk
    from gui import motion_kit as mk

    root = tk.Tk()
    root.withdraw()

    mk.cancel_all()
    check("初始活动动画为 0", mk.active_count() == 0)

    # 30 个控件各起一条动画 —— 旧实现是 30 条 after 链, 新实现共用一个心跳
    # (必须给显式 hex 底色: 起点取不到 hex 时 animate 会直接返回, 不做无意义补间)
    boxes = [tk.Frame(root, width=40, height=20, bg="#111111") for _ in range(30)]
    for b in boxes:
        b.pack()
        mk.animate(b, "bg", "#EEEEEE", ms=400)
    check("30 条动画同时活动", mk.active_count() == 30, "active=%d" % mk.active_count())
    n_jobs = sum(1 for _ in [mk._CLOCK.get("job")] if _)
    check("只占 1 个全局定时器", n_jobs == 1, "clock job=%s" % (mk._CLOCK.get("job") is not None))

    mk.cancel_all()
    check("cancel_all 后归零", mk.active_count() == 0)
    check("cancel_all 同时停表", mk._CLOCK.get("job") is None)

    # cancel(widget) 必须真的能取消 (v2 把 job 存在 _TICKS 里、state 里没有 → 取消失效)
    mk.animate(boxes[0], "bg", "#FFFFFF", ms=800)
    mk.animate(boxes[1], "bg", "#FFFFFF", ms=800)
    before = mk.active_count()
    mk.cancel(boxes[0])
    check("cancel(widget) 只取消自己", mk.active_count() == before - 1,
          "%d -> %d" % (before, mk.active_count()))
    mk.cancel_all()

    # pulse 呼吸
    mk.pulse(boxes[2], "bg", from_c="#111111", to_c="#EEEEEE", ms=600)
    check("pulse 启动", mk.active_count() == 1)
    mk.stop_pulse(boxes[2], "bg")
    check("stop_pulse 停止", mk.active_count() == 0)
    mk.pulse(boxes[3], "bg", from_c="red", to_c="#EEEEEE")
    check("pulse 拒绝非 hex 配色", mk.active_count() == 0)

    # animate 的入参防护: 目标色非 hex 直接忽略, 不能把控件配成样式名
    mk.animate(boxes[4], "bg", "SystemButtonFace", ms=100)
    check("animate 拒绝非 hex 目标色", mk.active_count() == 0)

    check("lerp_color 端点收敛",
          mk.lerp_color("#000000", "#FFFFFF", 0.0).upper() == "#000000"
          and mk.lerp_color("#000000", "#FFFFFF", 1.0).upper() == "#FFFFFF")
    check("缓动函数端点正确",
          mk.EASE_OUT_QUART(1.0) == 1.0 and mk.EASE_OUT_QUART(0.0) == 0.0
          and mk.EASE_OUT_CUBIC(1.0) == 1.0)
    check("帧间隔锁 60fps", mk.FRAME_MS == 16, "FRAME_MS=%d" % mk.FRAME_MS)

    mk.cancel_all()
    root.destroy()


# ── 3. poll_kit ───────────────────────────────────────────────────────
def t_poll_kit() -> None:
    section("poll_kit — 轮询退避 + after 定时器回收")
    import tkinter as tk
    from gui import poll_kit as pk

    check("reaper 已安装", bool(getattr(pk, "_REAPER_INSTALLED", False)))

    root = tk.Tk()
    root.withdraw()

    class Page(tk.Frame):
        def __init__(self, master, name):
            super().__init__(master)
            self.name = name
            self.pack()
            self.fired = 0

        def _poll_q(self):
            self.fired += 1
            if self.winfo_exists():
                self.after(pk.poll_delay(self, False), self._poll_q)

    front, back = Page(root, "front"), Page(root, "back")
    pk.mark_active(front, True)
    pk.mark_active(back, False)

    # 3.1 三档退避
    check("busy -> fast", pk.poll_delay(front, True) == pk.FAST_MS)
    check("前台空闲 -> idle", pk.poll_delay(front, False) == pk.IDLE_MS)
    check("后台空闲 -> hidden", pk.poll_delay(back, False) == pk.HIDDEN_MS)

    # 3.2 可见性沿 master 链解析 (堆叠页面 winfo_ismapped 不可靠)
    check("active_page 解析前台", pk.active_page(front) is True)
    check("active_page 解析后台", pk.active_page(back) is False)

    # 3.3 销毁页面的 pending after 必须被取消, 且不能误杀别的页面
    #     front 前台 → 600ms; back 后台 → 1000ms。泵 1.6s 事件:
    #     没有 reaper 时 front 的定时器到点会再跑一次 (fired=2) 并抛 invalid command name;
    #     有 reaper 时 front 停在 1, back 照常继续轮询 (证明没误杀)。
    errors: list = []
    root.report_callback_exception = lambda *a: errors.append(a)
    pk.mark_active(front, True)
    front._poll_q()
    back._poll_q()                 # back 的轮询循环先跑起来 (后台 1000ms 档)
    before_back = back.fired
    front.destroy()
    deadline = time.perf_counter() + 1.6
    while time.perf_counter() < deadline:
        root.update()
        time.sleep(0.02)
    check("销毁页的 pending 定时器被取消", front.fired == 1,
          "fired=%d (无 reaper 会是 2)" % front.fired)
    check("未误杀其它页面的轮询", back.fired > before_back,
          "back fired %d -> %d" % (before_back, back.fired))
    check("无 invalid command name 报错", not errors, "errors=%d" % len(errors))

    # 3.4 整页批量销毁 (apply_theme / _rebuild_pages 路径)
    pages = [Page(root, "bulk%d" % i) for i in range(6)]
    for p in pages:
        p._poll_q()
    n_before = len(root.tk.splitlist(root.tk.call("after", "info")))
    for p in pages:
        p.destroy()
    n_after = len(root.tk.splitlist(root.tk.call("after", "info")))
    check("批量销毁后定时器数回落", n_after < n_before,
          "%d -> %d" % (n_before, n_after))

    root.destroy()


# ── 4. lazy_import ────────────────────────────────────────────────────
def t_lazy_import() -> None:
    section("lazy_import — 惰性导入与后台预热")
    # 冷启动的关键: 探测 ultralytics 可用性**不能把 torch 拉进来**。
    #
    # ⚠ 判据是「这次探测有没有把重型模块拉进 `sys.modules`」, **不是「导入耗时」**
    #   (2026-09-11 修): 原来判 `probe_ms < 200`, 而首次 import 的耗时**取决于磁盘** ——
    #   本项目根挂在 **USB 外接 SSD** 上, 同一个 import 实测一次 47.8ms、一次 **760.8ms**,
    #   直接判出假 FAIL。耗时只是「有没有拉重活」的**代理指标**, 且与磁盘/内存强相关
    #   ⇒ 不是判据该管的东西。**意图就用意图本身断言**
    #   (同第 94 条: 别让判据去管它管不到的变量)。
    #
    #   用「基线之外**新增**」的语义, 免得被前面套件导入过的东西污染 ——
    #   `sys.modules` 是**进程级共享**的, 前面的 `ui_perf`/`thumb_async` 早把 PIL 拉进来了,
    #   所以只查 `torch`/`ultralytics` 这两个**没人会提前拉**的重型模块。
    base_mods = set(sys.modules)
    t0 = time.perf_counter()
    from infrastructure import lazy_import as li
    probe_ms = (time.perf_counter() - t0) * 1000
    li.ultralytics_available()
    added = [m for m in ("torch", "ultralytics")
             if m in sys.modules and m not in base_mods]
    check("★ 探测不把 torch/ultralytics 拉进 sys.modules", not added,
          "新增重型模块 %s (导入耗时 %.1f ms, 仅供观察)" % (added or "无", probe_ms))
    check("ultralytics_available() 可用", isinstance(li.ultralytics_available(), bool))

    t0 = time.perf_counter()
    li.ultralytics_available()
    again_ms = (time.perf_counter() - t0) * 1000
    check("find_spec 探测微秒级", again_ms < 20, "%.2f ms" % again_ms)

    # 后台预热
    check("warm_up_state 可读", isinstance(li.warm_up_state(), str),
          "state=%s" % li.warm_up_state())


def t_lazy_import_warmup() -> None:
    from infrastructure import lazy_import as li
    done = {"n": 0}
    li.warm_up_async(lambda: done.__setitem__("n", 1))
    t0 = time.perf_counter()
    li.warm_up_async(lambda: None)   # 幂等: 不重复起线程
    check("warm_up_async 幂等", (time.perf_counter() - t0) * 1000 < 50)
    for _ in range(400):
        if done["n"]:
            break
        time.sleep(0.05)
    check("后台预热最终 ready", li.is_ready(), "state=%s" % li.warm_up_state())


# ── 5. App ────────────────────────────────────────────────────────────
# ── 芯片行溢出守卫 (2026-09-12) ────────────────────────────────────────
# 坑: h3factory 顶栏那行 5 个 chip 是 `pack(side="left")` 的 Label, **没有自动省略**。
#     GPU 型号一长 (``RTX 3050 Ti Laptop``) 整行就往右顶穿页面边缘被硬切, 看着像界面坏了。
#     旧代码用 `[:20]` **字符截断**兜底, 结果是 ``GPU 4GB · RTX 3050 Ti Laptop G``
#     这种半截词 —— 而且中英混排下"字符数"代表不了像素宽 (见 _fit_px 的 docstring)。
# 修法: 语义压缩 `_short_gpu_name()` + 像素预算 `_gpu_chip_budget()` + `_fit_px()`。
#
# ⚠ 两条判据缺一不可, 且**必须带注入用例**:
#   · 只喂"真机型号"这种装得下的名字 ⇒ `_fit_px` 一次都不截断
#     ⇒ 这个守卫只会证明"没溢出", 证明不了"截得对" (避坑清单 #82)。
#   · 而第一版 `_gpu_chip_budget()` 漏算了 chip 自身的 2×padx(=18px),
#     **只有病态长名字才把它暴露出来** (实测仍溢出 9px)。
_INJECT_GPU = ("NVIDIA GeForce RTX 5090 Ti Super Laptop GPU Max-Q Ultra Advanced Edition "
               "24GB GDDR7 Xtreme Overclocked Founders Edition Titanium Ultra "
               "Workstation Compute Accelerator Professional Series")


def _walk_all(w):
    """递归整个控件树 (含 Toplevel), 只产出控件。"""
    for k in w.winfo_children():
        yield k
        yield from _walk_all(k)


def _bg_rgb(w):
    """取控件"面"的颜色 —— 用 `winfo_rgb` 解析, 因此 `SystemButtonFace` 这类
    系统颜色名也能拿到真值 (这正是本次事故里浅边的来源, 只认 hex 会漏掉它)。"""
    for opt in ("bg", "background", "troughcolor", "fieldbackground"):
        try:
            v = w.cget(opt)
        except Exception:
            continue
        if not v:
            continue
        try:
            r, g, b = w.winfo_rgb(str(v))
        except Exception:
            continue
        return (r >> 8, g >> 8, b >> 8), opt
    return None, None


def _lum(c):
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


_LUM_MAX = 196      # 深色主题下"亮面"上限。夹在 C.primary(#7CB0FF,169) 之上、
                    # 原生/clam 默认滚动条底色(#F0F0F0 240 / #dcdad5 218 / #c8c8c8 200) 之下。


def _hex_rgb(v):
    v = str(v or "").strip()
    if v.startswith("#") and len(v) == 7:
        try:
            return tuple(int(v[i:i + 2], 16) for i in (1, 3, 5))
        except Exception:
            return None
    return None


def _sb_classify(root):
    """扫一棵子树: (tk滚动条清单, ttk浅色清单, 亮面清单, 滚动条总数)。"""
    from tkinter import ttk

    tk_sb, ttk_light, lights, n_sb = [], [], [], 0
    for w in _walk_all(root):
        try:
            cls = w.winfo_class()
        except Exception:
            continue
        if cls == "Scrollbar":
            n_sb += 1
            tk_sb.append(str(w))
            continue
        if cls == "TScrollbar":
            n_sb += 1
            try:
                orient = str(w.cget("orient")) or "vertical"
                name = str(w.cget("style")) or (
                    "Vertical.TScrollbar" if orient == "vertical"
                    else "Horizontal.TScrollbar")
                st = ttk.Style(w)
                for opt in ("background", "troughcolor"):
                    v = st.lookup(name, opt)
                    rgb = _hex_rgb(v)
                    if rgb and _lum(rgb) >= _LUM_MAX:
                        ttk_light.append("%s %s=%s" % (str(w), opt, v))
                        break
            except Exception:
                pass
            continue
        try:
            if not w.winfo_manager() or not w.winfo_ismapped():
                continue
            if w.winfo_width() < 8 or w.winfo_height() < 8:
                continue
            if w.winfo_width() * w.winfo_height() < 200:
                continue
        except Exception:
            continue
        rgb, opt = _bg_rgb(w)
        if rgb and _lum(rgb) >= _LUM_MAX:
            lights.append("%s %s=%s" % (str(w), opt, rgb))
    return tk_sb, ttk_light, lights, n_sb


def _chk_dark_scrollbar_selftest(app) -> None:
    """正对照: 先证明"这个判据看得见问题", 否则"0 个问题"毫无意义。

    探针窗口必须 `deiconify` —— withdraw 状态下 `winfo_ismapped()` 恒为 0,
    判据会整片跳过, 变成"什么都看不见" (避坑清单第 93/94 条)。
    位置放屏外 + 全透明, 不打扰用户。
    """
    import tkinter as tk
    from tkinter import ttk

    probe = tk.Toplevel(app.root)
    try:
        probe.geometry("+12000+12000")
        tk.Scrollbar(probe, orient="vertical", bg="#28384E", troughcolor="#1B2636",
                     relief="flat", borderwidth=0).pack(side="left", fill="y")
        tk.Frame(probe, bg="#F0F0F0", width=40, height=40).pack(side="left")
        # clam 基类 style 没被换肤, 就是浅色 —— 用来证明 style 查找这条路是通的
        ttk.Scrollbar(probe, orient="vertical",
                      style="TScrollbar").pack(side="left", fill="y")
        probe.attributes("-alpha", 0.0)
        probe.deiconify()
        probe.update_idletasks()
        probe.update()
        p_tk, p_ttk, p_light, _ = _sb_classify(probe)
        check("★ 正对照: 判据能看见 tk.Scrollbar", len(p_tk) == 1,
              "探针里检出 %d 个 (期望 1)" % len(p_tk))
        check("★ 正对照: 判据能看见 #F0F0F0 亮面", len(p_light) >= 1,
              "探针里检出 %d 个 (期望 >=1)" % len(p_light))
        check("★ 正对照: 判据能看见未换肤的 ttk 滚动条", len(p_ttk) >= 1,
              "探针里检出 %d 个 (期望 >=1)" % len(p_ttk))
    finally:
        try:
            probe.destroy()
            app.root.update()
        except Exception:
            pass


def _chk_dark_scrollbars_scan(app, page_keys) -> None:
    """深色主题下不允许出现 ① `tk.Scrollbar` ② 未换肤的 ttk 滚动条 ③ 亮面控件。

    根因 (2026-09-12, 证据: `runs/_tk_sb_repro.py`, `runs/_ttk_sb_repro.py`):
      本机 `tk.Scrollbar` 由 **Windows 原生滚动条**绘制, `bg`/`troughcolor`/
      `relief`/`borderwidth` **全部无效**, 永远渲染 #F0F0F0; 而 `cget` 会老老实实
      回读你设的深色 ⇒ 旧 `dark_scrollbar()` 是**空操作却看着成功**, 于是每一页
      都挂着一条近白竖带而代码自认已处理。配合 `ScrolledText` 自带 `tk.Frame`
      (底色 SystemButtonFace) 的浅边, 就是"深色界面上一堆白条"的全部来源。
      ⇒ 唯一生效路径: `components.themed_scrollbar()` (ttk + clam style)。

    ⚠ **必须逐页扫**: 非当前页 `winfo_manager()` 为空、其控件 `winfo_ismapped()`
      恒为 0, 只扫一次会把后面 9 页整片跳过 —— 那样报出来的"0 个问题"是假绿。
    """
    from gui.theme_manager import resolved as _resolved

    if _resolved() != "dark":
        check("★ 深色滚动条/亮面守卫 (本次为 %s 主题, 跳过)" % _resolved(), True,
              "浅色主题下亮面是正常的")
        return

    _chk_dark_scrollbar_selftest(app)

    tk_sb, ttk_light, lights, n_sb = [], [], [], 0
    per_page_sb = {}
    for k in page_keys:
        try:
            app.show_page(k, animate=False)
            _clip_settle(app.root)
        except Exception as e:
            check("★ 滚动条扫描: 切到 %s" % k, False, repr(e))
            continue
        pg = app._pages.get(k)
        if pg is None:
            continue
        a, b, c, n = _sb_classify(pg)
        tk_sb += ["%s %s" % (k, x) for x in a]
        ttk_light += ["%s %s" % (k, x) for x in b]
        lights += ["%s %s" % (k, x) for x in c]
        per_page_sb[k] = n
        n_sb += n

    # 顶层 chrome (顶栏/侧栏/内容容器) 单独一遍 —— 它不属于任何页面
    a, b, c, n = _sb_classify(app.root)
    tk_sb += ["chrome %s" % x for x in a]
    ttk_light += ["chrome %s" % x for x in b]
    lights += ["chrome %s" % x for x in c]

    check("★ 滚动条扫描覆盖了全部页面 (少一页就是假绿)",
          len(per_page_sb) == len(page_keys),
          "%d/%d 页 · 逐页命中 %s" % (len(per_page_sb), len(page_keys), per_page_sb))
    check("★ 控件树里确实扫到了滚动条 (证明判据不是空跑)", n_sb >= 1,
          "共 %d 个" % n_sb)
    check("★ 深色主题下不允许存在 tk.Scrollbar", not tk_sb,
          ("%d 个: %s" % (len(tk_sb), tk_sb[:3])) if tk_sb
          else "0 个 (全部已走 ttk)")
    check("★ ttk 滚动条 style 必须已换肤", not ttk_light,
          ("%d 个仍是浅色: %s" % (len(ttk_light), ttk_light[:3])) if ttk_light
          else "全部深色")
    check("★ 深色主题下不允许存在亮面控件", not lights,
          ("%d 个: %s" % (len(lights), lights[:4])) if lights else "0 个")


def t_app() -> None:
    section("App — 主窗口 / 全部页面构建与切换 / 干净退出")
    from gui.main_window import App

    t0 = time.perf_counter()
    app = App(visible=False)
    app.root.update_idletasks()
    check("主窗口构建", True, "%.0f ms" % ((time.perf_counter() - t0) * 1000))

    keys = list(app._PAGE_SPEC)
    built = 0
    for k in keys:
        try:
            app.show_page(k, animate=False)
            app.root.update_idletasks()
            built += 1
        except Exception as e:
            check("page %s 构建" % k, False, repr(e))
    check("全部页面构建成功", built == len(keys), "%d/%d" % (built, len(keys)))

    # 切换: 只有当前页 _is_active
    app.show_page(keys[0], animate=False)
    app.root.update()
    actives = [k for k, p in app._pages.items() if getattr(p, "_is_active", False)]
    check("同时只有一页处于 active", len(actives) <= 1, "actives=%s" % actives)

    # 切页耗时 (稳态)
    #
    # ⚠ 量法坑 (2026-09-11): 旧写法是「连续 9 次 show_page 后只调一次 update()」——
    # 那样量到的是**被推迟的重绘总账** (末尾那一次 update 要把 9 次切页攒下的
    # 脏区一次画完), 报出 597ms/page 的假数字; 而真实交互里每帧都会让出。
    # 正确量法: 每次 show_page 后立刻 update_idletasks, 让成本落在它自己头上。
    # A/B 实测 (同机同会话): 旧写法 597.53 ms/page vs 新写法 ~4-17 ms/page。
    #
    # 另外 `app.root.update()` 会跑 after 队列 —— 只在稳态循环之前冲一次,
    # 循环内不再 update 全量 (否则把动画/轮询的活儿也算进"切页")。
    for k in keys:
        app.show_page(k, animate=False)
        app.root.update()
    per = []
    for k in keys:
        t0 = time.perf_counter()
        app.show_page(k, animate=False)
        app.root.update_idletasks()
        per.append(((time.perf_counter() - t0) * 1000, k))
    vals = [v for v, _ in per]
    ms = sum(vals) / max(1, len(vals))
    vals_sorted = sorted(vals)
    # 用中位数做判据: 本机内存紧张时单个页面会被换页顶到上百 ms,
    # 均值会被一个离群点带跑 (实测 worst=153ms 而中位数个位数)。
    med = vals_sorted[len(vals_sorted) // 2] if vals_sorted else 0.0
    slow = [(k, v) for v, k in per if v > 15]
    check("稳态切页中位数 < 15ms", med < 15,
          "median=%.2f ms  avg=%.2f ms (worst %.1f @%s, %d 页)"
          % (med, ms, max(vals) if vals else 0, per[vals.index(max(vals))][1] if vals else "-",
             len(per)))
    if slow:
        # 不判失败: 报出来供人判断是不是环境噪声 (内存换页会偶发)
        print("       [note] >15ms 的页面: %s"
              % ", ".join("%s=%.1f" % (k, v) for k, v in slow))

    # ── 文字裁切 / 遮挡扫描 (见 _clip_scan_canvas 上面的说明) ──────────
    # 复用已经建好的 App 逐页走一遍, 不再单独建一个 —— 本机内存常态只有
    # 1-2GB, 一次 GUI 建树已经很贵了。整页扫描比"逐控件计时"省得多。
    #
    # ⚠ **必须先让窗口真显示**再扫 (2026-09-11 实测): App 是 visible=False 建的,
    #   withdraw 状态下 Tk 不给一部分控件分配真实尺寸 —— 同一套判据, 有效样本只有
    #   175 (可见时 404), 而且会**凭空报出 9 处 Label 切字**。读数随仪器状态变,
    #   这种结论一律不能信。所以临时"显示 + 全透明 + 挪屏外", 扫完原样藏回去,
    #   既不打扰正在用机器的人, 又拿到真实几何。
    for _k in _CLIP_STAT:
        _CLIP_STAT[_k] = 0

    clip_finds = []
    _was_view = False
    try:
        _was_view = bool(app.root.winfo_viewable())
    except Exception:
        pass
    try:
        if not _was_view:
            try:
                app.root.geometry("+12000+12000")       # 挪到屏幕外
                app.root.attributes("-alpha", 0.0)      # 全透明 (双保险, 别闪到人)
            except Exception:
                pass
            app.root.deiconify()
            app.root.update()

        for k in keys:
            try:
                app.show_page(k, animate=False)
                _clip_settle(app.root)      # 几何收敛后再判 (见函数注释)
            except Exception as e:
                clip_finds.append((k, "切页失败", "", repr(e)))
                continue
            pg = app._pages.get(k)
            if pg is not None:
                _clip_walk(pg, k, clip_finds)


        # ── 深色滚动条 / 亮面 (逐页扫; 非当前页 ismapped 恒为 0, 只扫一次是假绿) ──
        try:
            _chk_dark_scrollbars_scan(app, keys)
        except Exception as e:
            check("★ 深色主题下不允许存在 tk.Scrollbar", False, "守卫异常 %r" % e)
    finally:
        if not _was_view:
            try:
                app.root.attributes("-alpha", 1.0)
                app.root.withdraw()
                app.root.update()
            except Exception:
                pass

    cv_m, cv_t = _CLIP_STAT["cv_measured"], _CLIP_STAT["cv_total"]
    lb_m, lb_t = _CLIP_STAT["lbl_measured"], _CLIP_STAT["lbl_total"]
    lb_w = _CLIP_STAT["lbl_wrapped"]
    lb_f = _CLIP_STAT["lbl_fitpx"]
    lb_judged = lb_m - lb_w          # 真正参与"单行宽度"判据的样本
    # ⚠ 有效样本数: 为 0 说明控件根本没映射, 判据一次都没跑。这时必须报红 ——
    #   否则"0 处缺陷"与"探针瞎了"是同一个读数 (本项目已栽三次)。
    check("★ 裁切扫描有有效样本", cv_m > 0 and lb_judged > 0,
          "Canvas %d/%d · 文本已量 %d/%d (换行跳过 %d, 实判 %d; 另 %d 个 _fit_px 托管跳过)"
          % (cv_m, cv_t, lb_m, lb_t, lb_w, lb_judged, lb_f))
    check("★ 文本控件采样量达标", lb_judged > 150, "实判 %d 个" % lb_judged)
    if clip_finds:
        by_page = {}
        for pg_, kind, txt, why in clip_finds:
            by_page.setdefault(pg_, []).append((kind, txt, why))
        for pg_, rows in sorted(by_page.items()):
            for kind, txt, why in rows:
                print("       [clip] %-12s [%s] %-40s %s" % (pg_, kind, txt, why))
    check("★ 无文字裁切/遮挡", not clip_finds,
          "合计 %d 处" % len(clip_finds) if clip_finds else "0 处")

    # 干净退出: 原实现缺 WM_DELETE_WINDOW, 退出时 0xC0000005
    try:
        app._on_close()
    except Exception as e:
        check("_on_close 无异常", False, repr(e))
    else:
        check("_on_close 干净退出", True)


# ── 6. _fit_px 截断等价性 ─────────────────────────────────────────────
class _FakeFont:
    """假字体: 按字符给宽度, 并统计 measure() 的调用次数与测量字符总量。

    用它把 _fit_px 从 Tk 里摘出来做**差分测试** —— 2026-09-11 为了让轮询刷新
    不再卡 123ms, 改写了二分区间 (见 h3_factory_page._fit_px 的注释)。这种
    "性能改写"最危险的地方是**悄悄改了输出**: 用户看到的是排版错位, 不是报错。
    所以必须逐字符对比旧实现。
    """

    def __init__(self, cjk: int = 12, ascii_: int = 6, space: int = 3, nl: int = 0):
        self.w = (cjk, ascii_, space, nl)
        self.calls = 0
        self.chars = 0
        self.ell = 0

    def measure(self, s: str) -> int:
        self.calls += 1
        self.chars += len(s)
        cjk, ascii_, space, nl = self.w
        tot = 0
        for ch in s:
            if ch == " ":
                tot += space
            elif ch == "\n":
                tot += nl
            elif ord(ch) > 127:
                tot += cjk
            else:
                tot += ascii_
        return tot


class _Stub:
    """_fit_px 只用到 widget.cget('font') 和 widget._fit_cache 两处。"""

    def __init__(self, font: str = "stub"):
        self._font = font

    def cget(self, key):
        return self._font


def t_font_lag() -> None:
    """图标字体不能再用 "Segoe UI Emoji"; warm_fonts 必须真的生效。

    背景 (2026-09-11 实测, 本机 Python 3.13 + Tk 8.6):
        · 65 个 distinct 图标字符一次渲染: Segoe UI Emoji 17pt = **1499.78 ms**,
          换成 Microsoft YaHei UI 同字号 = **22.35 ms** (67x)。
        · 这笔钱按**每个不同字符**收, 换新字号还要再收一次。
        · 全站 emoji 出现 314 次 / 17 个文件 ⇒ 就是"刚打开程序那几秒卡"。
        · GDI 真像素取证 (runs/_verify_emoji_glyph.py) 还证明: Tk **不渲染彩色
          emoji 层**, 三个字体画出来是**一模一样的单色轮廓** ⇒ 这钱白花。
    """
    section("font_lag — 图标字体成本 + warm_fonts 有效性")

    # 1) ICON token 不能回退到 Segoe UI Emoji
    from gui import ui_tokens as TK
    icon_fams = {v[0] for v in TK.ICON.values()}
    check("ICON 不用 Segoe UI Emoji", "Segoe UI Emoji" not in icon_fams,
          "families=%s" % sorted(icon_fams))
    check("ICON 三档 family 统一", len(icon_fams) == 1, "families=%s" % sorted(icon_fams))

    # 2) 全仓库不许把 Segoe UI Emoji 当**渲染字体**写死。
    #
    #    ⚠ 这条判据自己踩过两次坑, 都记在这里:
    #      ① 第一版"行里出现就算"把 main_window._warm_fonts_idle 里那个预热
    #         兜底字体列表也算成残留 —— 那是**故意**留的 (万一哪里还有旧调用点,
    #         预热带过它能保证搜索费也落在启动期)。
    #      ② 第二版用 `st.startswith("#")` 排注释, 漏掉了**文档字符串** ——
    #         结果 main_window 的 docstring 里那句"实测总共 3 片 (Microsoft YaHei
    #         UI / Segoe UI Emoji / Segoe UI Symbol)"又被当成代码。
    #      所以最终用 AST 取"真正的字符串字面量/调用实参", 注释与 docstring 天生
    #      不在 AST 里, 一次解决两类误报。
    import ast as _ast
    resid = []
    for fp in (ROOT / "src").rglob("*.py"):
        if fp.name in ("ui_perf.py", "ui_tokens.py"):
            continue          # 这两个文件里的 "Segoe UI Emoji" 只在注释/兜底列表里
        try:
            src = fp.read_text(encoding="utf-8", errors="replace")
            tree = _ast.parse(src)
        except Exception:
            continue
        # AST 不自带 parent 指针, 自己挂一遍 (用来分辨 docstring / 元组第一项)
        for _p in _ast.walk(tree):
            for _c in _ast.iter_child_nodes(_p):
                _c._parent = _p
        lines = src.split("\n")
        for node in _ast.walk(tree):
            if not isinstance(node, _ast.Constant):
                continue
            if not isinstance(node.value, str) or "Segoe UI Emoji" not in node.value:
                continue
            ln = node.lineno
            # docstring: 直接挂在模块/类/函数体首位的 Expr
            if isinstance(getattr(node, "_parent", None), _ast.Expr):
                continue
            # 兜底名单里的并列项: 该字符串不是所在元组/列表的**第一项**
            # (第一项才是"真在用"的字体, 后面的都是兜底)
            parent = getattr(node, "_parent", None)
            if isinstance(parent, (_ast.Tuple, _ast.List)) and parent.elts:
                if parent.elts[0] is not node:
                    continue
            resid.append("%s:%d" % (fp.relative_to(ROOT), ln))
    check("无写死的 Segoe UI Emoji 渲染字体", not resid, "残留=%s" % resid)

    # 3) warm_fonts 真的能被调用且幂等 (需要 Tk; --no-app 时跳过)
    from gui import ui_perf as up
    check("warm_fonts 可调用", callable(getattr(up, "warm_fonts", None)))
    check("_family_resolves 可调用", callable(getattr(up, "_family_resolves", None)))

    try:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
    except Exception as e:
        check("Tk 可用 (跳过预热行为验证)", False, repr(e))
        return

    try:
        # 仪器自检: 判据必须能分辨真假字体
        #   ⚠ 反直觉事实: Tk **不会**因字体名不存在而报错, 它静默回退。
        #     所以"创建控件没抛错"不能当判据, 必须查 Tk 已注册族名。
        check("★ _family_resolves 认得真字体",
              up._family_resolves(root, "Microsoft YaHei UI") is True)
        check("★ _family_resolves 识破假字体",
              up._family_resolves(root, "\u0000不存在\u0000") is False)

        r1 = up.warm_fonts(root)
        check("首次预热真干活", r1.get("skipped") is False, repr(r1))
        check("★ 预热真的渲染了 (rendered>0)", r1.get("rendered", 0) > 0,
              "rendered=%s" % r1.get("rendered"))
        check("★ 字体族全部被 Tk 解析 (无静默回退)",
              not (r1.get("unresolved_families") or []),
              "unresolved=%s" % (r1.get("unresolved_families"),))
        t0 = time.perf_counter()
        r2 = up.warm_fonts(root)
        d2 = (time.perf_counter() - t0) * 1000
        check("二次预热幂等且近零耗时", r2.get("skipped") is True and d2 < 5,
              "%.3f ms" % d2)
        check("fonts_warmed() 反映状态", up.fonts_warmed() is True)
    finally:
        try:
            root.destroy()
        except Exception:
            pass


# ── 9. bmp_owner ──────────────────────────────────────────────────────
def t_bmp_owner() -> None:
    """位图必须建在**目标解释器**上 —— 漏传 owner= 会炸 `image "pyimageN" doesn't exist`。

    为什么单独立一条 (2026-09-11):
        `ImageTk.PhotoImage(img)` 不传 master 时**永远建在 `tkinter._default_root`**。
        一个进程里只要出现过第二个 `Tk()` (splash→main / 审计脚本 / 冒烟测试 / 主题重建),
        `_default_root` 就是**别人家的解释器**。此时:
          · 图缓存进"别人家"的桶;
          · `create_image` 画到自己的画布上 → TclError。
        症状离病因极远 (报错在 create_image, 真因是两行调用漏了个参数),
        且**只在多解释器进程里复现** —— 平时跑得好好的。
        components.gradient_pill / page_header 正是漏了这个, 2026-09-11 已修。

    判据 (必须能分辨"漏传 owner"):
        在双解释器进程里建组件 -> 位图必须落进 canvas 所属解释器的桶。
    """
    section("bmp_owner — 位图建在正确解释器上 (双 Tk 场景)")
    try:
        import tkinter as tk
        import tkinterdnd2
        from gui import components as comp
        from gui import ui_perf as up
    except Exception as e:
        check("Tk / 依赖可用 (跳过位图归属验证)", False, repr(e))
        return

    r1 = None
    r2 = None
    try:
        # 先建一个"陌生" root, 让它成为 _default_root (模拟 splash / 审计脚本残留)
        r1 = tkinterdnd2.TkinterDnD.Tk()
        r1.withdraw()
        r2 = tkinterdnd2.TkinterDnD.Tk()
        r2.geometry("900x600")          # 必须真实布局, 否则 canvas 宽度 <120 会被跳过

        id_r1, id_r2 = id(r1.tk), id(r2.tk)
        # 注意: 不要求 _default_root 一定是 r1 —— 前面的套件可能留了更早的 Tk。
        # 真正要确认的是「_default_root 不是 r2」, 即"有个别人家的解释器占着默认位"。
        # 这正是会引发 pyimage 的那个条件。
        dflt = id(tk._default_root) if tk._default_root is not None else None
        check("★ 进程内存在『别人家的』默认解释器 (pyimage 复现条件)",
              dflt is not None and dflt != id_r2,
              "default=%s id_r1=%d id_r2=%d" % (dflt, id_r1, id_r2))

        up._CACHES.clear()

        f = tk.Frame(r2, bg="#101010")
        f.pack()
        r2.update()
        comp.gradient_pill(f, "自检胶囊", lambda: None)
        r2.update()
        time.sleep(0.12)
        r2.update()
        comp.page_header(f, icon="🔍", title="自检标题", subtitle="副标题")
        r2.update()
        time.sleep(0.12)
        r2.update()

        buckets = dict((k, len(v)) for k, v in up._CACHES.items())
        check("★ 位图缓存桶只有目标解释器一个", set(buckets.keys()) <= {id_r2},
              "buckets=%s (期望 only %d)" % (buckets, id_r2))
        check("★ 位图真的建了 (两个组件都跑了)", sum(buckets.values()) >= 2,
              "entries=%d" % sum(buckets.values()))

        # 直接验证: 带 owner 的图能画到目标画布上
        cv = tk.Canvas(r2, width=100, height=30, highlightthickness=0)
        cv.pack()
        img = up.rounded_image(100, 30, 8, ("#3355ff", "#aa33ff"),
                               orient="h", owner=cv)
        drew = False
        if img is not None:
            try:
                cv.create_image(0, 0, image=img, anchor="nw")
                drew = True
            except Exception as e:
                check("create_image 不抛 TclError", False, repr(e))
        check("★ owner=cv 的位图能画到目标画布", drew)

        # _master_of 的加固: 传了活控件就必须用它, 不许偷偷回退 default root
        m = up._master_of(f)
        check("★ _master_of(活控件) 不返回陌生的 default root", m is not r1,
              "owner 解析 -> %s" % ("r2" if m is r2 else ("r1(陌生!)" if m is r1 else m)))
    finally:
        for r in (r2, r1):
            if r is not None:
                try:
                    r.destroy()
                except Exception:
                    pass


# ── 10. 文字裁切 / 遮挡扫描 (由 t_app 调用, 见那里) ──────────────────────
#
# 为什么单独立一条 (2026-09-11):
#     用户报「部分文字被遮挡没有全部放出来」。查下去是**两条完全不同**的成因,
#     而只有一条是"字写错了":
#       (a) 字体族名写错 -> Tk 静默换宋体 -> 字宽与测量值不符 -> 被容器裁掉
#           (源码级, 由 t_font_family 守)
#       (b) 真·几何溢出 -> 画布文本 bbox 超出画布边界, 或被后画的实心形状盖住
#           (布局级, 由本条守)
#     两条会**互相掩盖**: 改完字体名字宽就变了, 原本勉强不溢出的地方可能才开始
#     溢出。所以 (a) 修完必须重新量一次 (b)。
#
# 仪器可信度 (v2 的教训):
#     第一版探针在页面未映射时 (画布 width==1) 静默跳过所有画布并报 0/0 ——
#     与"真的干净"**无法区分**。所以这里强制统计"有效样本数", 样本为 0 直接判
#     FAIL: 结论作废, 先修仪器。同族的坑本项目已踩过三次。
#
# 正对照: `t_text_clip_selftest()` 造出三种**已知**缺陷喂给同一套判据, 抓不到
#     就判 FAIL —— 否则"0 处"完全可能只是探针瞎了。它单独立成套件 (不并进 App),
#     因为"探针有没有用"和"界面上有没有缺陷"是两件事: 前者失败时, 后者的结论
#     一律作废, 必须能一眼分辨是哪一种。

_CLIP_STAT = {"cv_total": 0, "cv_measured": 0, "lbl_total": 0, "lbl_measured": 0,
              "lbl_wrapped": 0, "lbl_fitpx": 0}


def _clip_scan_canvas(cv, page, findings) -> None:
    """画布: 文本 bbox 溢出边界 = 裁切; 被**后画**的实心形状盖 >25% = 遮挡。"""
    _CLIP_STAT["cv_total"] += 1
    try:
        cw, ch = cv.winfo_width(), cv.winfo_height()
    except Exception:
        return
    if cw <= 1 or ch <= 1:          # 未映射 —— 不计入有效样本 (防假绿)
        return
    _CLIP_STAT["cv_measured"] += 1
    try:
        items = list(cv.find_all())
    except Exception:
        return
    texts, shapes = [], []
    for i in items:
        try:
            t = cv.type(i)
            bb = cv.bbox(i)
        except Exception:
            continue
        if not bb:
            continue
        if t == "text":
            try:
                texts.append((i, bb, cv.itemcget(i, "text")))
            except Exception:
                pass
        elif t in ("rectangle", "oval", "polygon", "image"):
            try:
                fill = cv.itemcget(i, "fill") if t != "image" else "#img"
            except Exception:
                fill = ""
            shapes.append((i, bb, fill))

    for _i, bb, txt in texts:
        x1, y1, x2, y2 = bb
        if x1 < -1 or y1 < -1 or x2 > cw + 1 or y2 > ch + 1:
            over = []
            if x1 < -1:
                over.append("左溢%.0f" % (-x1))
            if x2 > cw + 1:
                over.append("右溢%.0f" % (x2 - cw))
            if y1 < -1:
                over.append("上溢%.0f" % (-y1))
            if y2 > ch + 1:
                over.append("下溢%.0f" % (y2 - ch))
            findings.append((page, "裁切", str(txt)[:44],
                             "%s / 画布%dx%d" % (",".join(over), cw, ch)))

    order = {it: n for n, it in enumerate(items)}
    for _i, bb, txt in texts:
        x1, y1, x2, y2 = bb
        area = max(1, (x2 - x1) * (y2 - y1))
        for j, sb, fill in shapes:
            # 只算**画在文本之后**的实心形状: 画在前面的是底板, 不构成遮挡。
            # image 跳过 —— 图标底本来就压在字后面, 报了全是噪音。
            if order.get(j, -1) <= order.get(_i, -1) or not fill or fill == "#img":
                continue
            sx1, sy1, sx2, sy2 = sb
            ix1, iy1 = max(x1, sx1), max(y1, sy1)
            ix2, iy2 = min(x2, sx2), min(y2, sy2)
            if ix2 > ix1 and iy2 > iy1:
                cov = (ix2 - ix1) * (iy2 - iy1) / area
                if cov > 0.25:
                    findings.append((page, "遮挡", str(txt)[:44],
                                     "被 %s 盖 %.0f%%" % (fill, cov * 100)))
                    break


def _clip_scan_text(w, page, findings, require_mapped: bool = True) -> None:
    """Label/Button: 文本实测需要宽 > 实际分配宽 => 右边被切。"""
    try:
        cls = w.winfo_class()
    except Exception:
        return
    if cls not in ("Label", "Button", "TLabel", "TButton", "Checkbutton", "Radiobutton"):
        return
    _CLIP_STAT["lbl_total"] += 1
    try:
        txt = w.cget("text")
    except Exception:
        return
    if not txt:
        return
    # ⚠ 文本由 `_fit_px` 管理的控件, **不适用几何判据** (2026-09-11, 同族第 5 次)。
    #   `_fit_px(widget, text, budget_px)` 是 h3_factory_page 里**按像素预算主动截断**
    #   文本的函数, 它会给控件挂上 `_fit_cache`。这类控件的文本长度是「算出来适应宽度」
    #   的, 而其中 GPU 状态那条**每几秒被轮询刷新一次** ⇒ 扫描必然撞上
    #   「文本已换新 + 几何还没跟上」的中间态。实测连扫 6 轮, 有 3 轮报出同一处假阳性:
    #       `GPU 0% · 3.7/4.0GB · 9.0W · 71℃ · 0 采样 · 0 排` 需要252px 只有211px
    #   而稳定态下它是正常的。它的正确性**已经**由 `t_fit_px` 的 **144 组差分对比**
    #   (逐字符等价 + measure 调用量)守着 —— 几何判据对它只是纯假阳性。
    #   跳过但要计数, 让人看得见判据的覆盖范围 (见下面"实判 N 个")。
    if hasattr(w, "_fit_cache"):
        _CLIP_STAT["lbl_fitpx"] += 1
        return
    try:
        wl = int(w.cget("wraplength") or 0)
    except Exception:
        wl = 0
    try:
        if require_mapped and not w.winfo_ismapped():
            return
        aw = w.winfo_width()
        if aw <= 1:
            return
    except Exception:
        return
    _CLIP_STAT["lbl_measured"] += 1
    if wl:                       # 会换行, 不能按单行判
        _CLIP_STAT["lbl_wrapped"] += 1
        return
    try:
        req = w.winfo_reqwidth()
    except Exception:
        return
    if req > aw + 2:             # 留 2px 容差, 差得明显才算
        findings.append((page, "Label切字", str(txt)[:44],
                         "需要%dpx 只有%dpx (差%d)" % (req, aw, req - aw)))


def _clip_walk(w, page, findings) -> None:
    for k in w.winfo_children():
        try:
            cls = k.winfo_class()
        except Exception:
            cls = ""
        if cls == "Canvas":
            _clip_scan_canvas(k, page, findings)
        _clip_scan_text(k, page, findings)
        _clip_walk(k, page, findings)


def _clip_settle(root, rounds: int = 3) -> None:
    """让页面进入**稳态**再判 —— 否则判据会撞上中间态, 凭空报缺陷。

    ⚠ 为什么必须有 (2026-09-11, 同族第 5 次): 有的 Label 文本由事件**异步**刷新,
      而它的 `wraplength` 由 `components.autowrap` 在 `<Configure>` 里跟随宽度设上。
      于是存在这样一个中间态: **文本已换新 + `wraplength` 还是 0 + 几何还没重算** ——
      此时 `wl == 0` 不会被"会换行就跳过"拦下, 而 `req`(新文本) 又对不上 `aw`(旧几何),
      `req > aw + 2` 就地成立。

      实测同一张 training 页的数据集核对卡:
        · 中间态 -> 报「需要326px 只有316px」
        · 稳态    -> `req=692 / aw=699`(**还余 7px**, 且有 `wraplength=699` 本就该跳过)
      连续 update 后读数在第 2 轮收敛 (`stable_at=2`), 所以 3 轮够它用。

      ⚠ settle **不是万能的**: 对"每几秒被轮询刷新"的控件它压不住 —— 实测单进程
        连扫 6 轮仍有 3 轮报同一处假阳性 (`runs/_clip_repeat_probe.py`, 报的是
        h3factory 的 GPU 状态条)。那类控件改用 `_fit_cache` 标记**整体跳过几何判据**
        (理由见 `_clip_scan_text` 里那段)。settle 只负责压掉"切页后首次布局"这一种中间态。

      这不是"多 update 几次让它变绿" —— 不稳的读数本来就不可用, 先用探针确认过
      稳态是干净的才这么改 (`runs/_train_label_probe.py`)。
    """
    for _ in range(rounds):
        try:
            root.update_idletasks()
            root.update()
        except Exception:
            return
        time.sleep(0.02)


def t_text_clip_selftest() -> None:
    """**正对照** —— 造三种已知缺陷, 看同一套判据抓不抓得到。

    没有这一步, "合计 0 处" 就无法与"探针瞎了"区分 (这正是本项目反
    复栽的那类静默故障: 判据看着通过, 其实什么都没测)。
    三种缺陷对应三条判据分支:
      A 画布文本左溢  -> _clip_scan_canvas 的溢出分支
      B 画布文本被盖  -> _clip_scan_canvas 的遮挡分支
      C Label 需要宽 > 分配宽 -> _clip_scan_text
    这里用**独立的 Tk** deiconify 后搭, 因为判据要求控件真映射 (winfo_ismapped),
    而 App 是 withdraw 的。
    """
    section("text_clip/selftest — 正对照: 探针抓得到已知缺陷吗")
    import tkinter as _tk

    root = None
    try:
        root = _tk.Tk()
        root.geometry("320x220+3000+3000")     # 挪到屏外, 不打扰用户
        root.update()
    except Exception as e:
        check("正对照环境可建", False, repr(e))
        return

    found = []
    try:
        cv = _tk.Canvas(root, width=160, height=90, highlightthickness=0)
        cv.place(x=4, y=4)
        # A: 左溢 —— 文本中心放到画布外
        cv.create_text(-40, 30, text="左溢样本", fill="#ffffff")
        # B: 遮挡 —— 文本先画, 实心矩形后画并盖住它
        cv.create_text(100, 70, text="被盖样本", fill="#ffffff")
        cv.create_rectangle(60, 55, 160, 85, fill="#ff0000", outline="")
        # C: 强制一个 40px 宽的 Label 装长文本
        lb = _tk.Label(root, text="这是一段一定装不下的长中文文本用于正对照")
        lb.place(x=4, y=110, width=40, height=22)
        root.update()
        # 让 place 的几何真正落定 (只在 deiconify 后才算数)
        root.update_idletasks()

        check("正对照控件已映射 (判据的前置条件)", bool(lb.winfo_ismapped()) and cv.winfo_width() > 1,
              "label mapped=%s canvas w=%d" % (lb.winfo_ismapped(), cv.winfo_width()))

        _clip_scan_canvas(cv, "selftest", found)
        _clip_scan_text(lb, "selftest", found)

        kinds = sorted(set(k for _p, k, _t, _w in found))
        check("★ 探针抓到画布文本裁切", any(k == "裁切" for _p, k, _t, _w in found),
              "命中: %s" % [t for _p, k, t, _w in found if k == "裁切"])
        check("★ 探针抓到画布文本遮挡", any(k == "遮挡" for _p, k, _t, _w in found),
              "命中: %s" % [t for _p, k, t, _w in found if k == "遮挡"])
        check("★ 探针抓到 Label 切字", any(k == "Label切字" for _p, k, _t, _w in found),
              "命中: %s" % [t for _p, k, t, _w in found if k == "Label切字"])
        if not kinds:
            print("       [note] 一条都没抓到 —— 后面 App 扫描的『0 处』不可信")
    finally:
        # 正对照用了独立 Tk, 必须把这三个计数器回滚, 免得污染 App 扫描的有效样本数
        _CLIP_STAT["cv_total"] -= 1
        _CLIP_STAT["cv_measured"] -= 1
        _CLIP_STAT["lbl_total"] -= 1
        _CLIP_STAT["lbl_measured"] -= 1
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass


_FAMILY_RX = re.compile(
    r"""font\s*=\s*\(\s*["']([^"']{3,40})["']\s*,\s*\d+""")


def _scan_families():
    """从 src/gui 源码里扫出**真实写下的**字体族名。

    只认出现在 `font=("族名", 数字...` 这种形态里的字面量 —— 也就是真的当字体用的。
    特意**不**扫注释/docstring 里的例子: 早前用宽松正则扫, 把 ui_perf.py docstring 里
    那句反例 (`tk.Label(font=("\\u0000不存在\\u0000", 17))`) 也算成了一个"被替换的族",
    处数就不可信了。写法里带 NUL / 带"不存在"的一律跳过。
    """
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "src" / "gui"
    fams = {}
    for fp in root.rglob("*.py"):
        try:
            txt = fp.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for m in _FAMILY_RX.finditer(txt):
            fam = m.group(1)
            if "\\u0000" in fam or "不存在" in fam or fam.startswith("\\"):
                continue
            fams.setdefault(fam, []).append(fp.name)
    return fams


def t_font_family() -> None:
    """字体族必须被 Tk **原样解析** —— 名字写错时 Tk 不报错, 会静默换个字体。

    真事 (2026-09-11): 8 个页面文件里写着 "MS YaHei UI" (真名是
    "Microsoft YaHei UI"), 共 **114 处**。Tk 把这名字静默换成**宋体** ——
    衬线体混在雅黑 UI 里发虚发糊, 而且字宽不同 (9pt bold: 宋体 100.0px vs
    雅黑 96.0px), 于是"按宋体量出来的宽度"去排"雅黑渲染的字", 该收的没收 ⇒
    **文字被容器裁掉**。这条正是"部分文字被遮挡"的一条真因。

    判据: 读 `Font.actual()["family"]` —— 也就是 **Tk 真正选中了哪个族**,
    而不是"源码里写了什么"。源码写什么不算数, Tk 选什么才算数。
    """
    section("font_family — 字体族不被静默替换")
    try:
        import tkinter as _tk
        import tkinter.font as _tkfont
    except Exception as e:
        check("tkinter 可导入", False, repr(e))
        return
    root = None
    try:
        root = _tk.Tk()
        root.withdraw()
    except Exception as e:
        print("  [skip] 没有可用的显示环境: %r" % (e,))
        return
    try:
        # ① **正对照** —— 先证明这个探针不是瞎的: 拿一个一定不存在的族名, 如果
        #    它也报 "原样解析", 说明 actual() 这条路根本测不出替换, 后面的绿就是假绿。
        bogus_name = "\u0000zzz-no-such-family\u0000"
        bogus = _tkfont.Font(font=(bogus_name, 9))
        b_real = str(bogus.actual().get("family", ""))
        check("探针有效 (不存在的族名会被识破)", b_real != bogus_name,
              "假名 -> Tk 实选 %r" % (b_real,))

        # ② 扫源码里真实写下的族名, 逐个回读 Tk 的实选
        fams = _scan_families()
        check("扫到字体族", bool(fams),
              "共 %d 个: %s" % (len(fams), sorted(fams)))
        bad = []
        for fam in sorted(fams):
            try:
                f = _tkfont.Font(font=(fam, 9))
                real = str(f.actual().get("family", ""))
            except Exception as e:
                bad.append((fam, "EXC %r" % (e,)))
                continue
            if real.lower() != fam.lower():
                bad.append((fam, real))
        check("所有字体族都被 Tk 原样解析", not bad,
              ("被静默替换: %r (出现在 %s)" % (
                  bad, {f: fams.get(f, [])[:3] for f, _ in bad})) if bad
              else "%d 个族全部原样解析" % len(fams))
    finally:
        try:
            root.destroy()
        except Exception:
            pass


# ── main ──────────────────────────────────────────────────────────────
def _scan_dup_defs(source: str):
    """扫出一段源码里"同一个类定义了两个同名方法"的地方。

    返回 [(类名, 方法名, [行号, ...]), ...]。
    纯 AST, 不需要 Tk、不需要显示器、不需要 import 被测模块。
    """
    import ast
    import collections
    out = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ClassDef):
            continue
        seen = collections.defaultdict(list)
        for b in node.body:
            if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)):
                seen[b.name].append(b.lineno)
        for name, lns in seen.items():
            if len(lns) > 1:
                out.append((node.name, name, lns))
    return out


def t_dup_def() -> None:
    """同一个类里**同名方法定义两次** ⇒ 后定义的静默覆盖前面的。

    真事 (2026-09-12, 全包 AST 扫描抓到 2 处):
      · `pages/h3_factory_page.py` `H3FactoryPage._render_task_zh` ——
        两版**逻辑不同**, 后定义的那版赢 ⇒ 旧版的 `🎲 当前组合` / `📦 最新输出`
        **从来没被渲染过**。不报错、不告警、不进任何自检。
        直接后果: 组合串只剩顶栏状态行那一处, 还要被 `[:52]` 按字符数砍一刀,
        丢掉「疼痛 / 出画」两个字段。
      · `pages/developer_page.py` `DeveloperPage.on_show` —— 两份逐字相同 (纯冗余)。

    这条为什么单列一个套件: 它是**纯 AST** 判据 (无 Tk / 无显示器 / 不改任何状态),
    而它抓到的两类后果分别是"静默丢功能"和"死代码" —— 都属于"跑起来一切正常"
    的那种病。正对照跑的是**同一个** `_scan_dup_defs`, 不是另写一份等价逻辑。
    """
    section("dup_def — 类内同名方法不得重复定义 (后定义会静默覆盖前面的)")
    # ① 正对照: 现场造一个重复定义的类, 走的必须是同一个扫描函数
    probe = ("class Z:\n"
             "    def f(self):\n"
             "        return 1\n"
             "    def f(self):\n"
             "        return 2\n"
             "class Y:\n"
             "    def g(self):\n"
             "        return 3\n")
    try:
        hit = _scan_dup_defs(probe)
    except Exception as e:
        hit = [("EXC", repr(e), [])]
    check("★ 正对照: 扫描器能认出重复定义",
          len(hit) == 1 and hit[0][0] == "Z" and hit[0][1] == "f",
          "现场造的重复类 -> %r" % (hit,))

    # ② 真扫 src/ 全包
    root = ROOT / "src"
    bad, files = [], 0
    for fp in sorted(root.rglob("*.py")):
        try:
            src = fp.read_text(encoding="utf-8", errors="ignore")
            files += 1
            for cls, name, lns in _scan_dup_defs(src):
                bad.append(("%s::%s.%s" % (fp.relative_to(ROOT), cls, name), lns))
        except Exception as e:
            bad.append(("%s" % fp.relative_to(ROOT), "PARSE %r" % (e,)))
    check("扫到源码文件", files > 0, "共 %d 个 .py" % files)
    check("★ 类内没有同名方法重复定义", not bad,
          ("发现 %d 处: %s" % (len(bad), bad[:6])) if bad
          else "%d 个 .py 中 0 处重复" % files)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-app", action="store_true", help="跳过 GUI 应用级检查")
    args = ap.parse_args()

    suites = [("ui_perf", t_ui_perf), ("motion_kit", t_motion_kit),
              ("poll_kit", t_poll_kit), ("lazy_import", t_lazy_import),
              ("lazy_import/warmup", t_lazy_import_warmup), ("font_lag", t_font_lag),
              ("bmp_owner", t_bmp_owner), ("dup_def", t_dup_def),
              ("font_family", t_font_family),
              ("text_clip/st", t_text_clip_selftest)]
    if not args.no_app:
        suites.append(("App", t_app))

    for name, fn in suites:
        try:
            fn()
        except Exception:
            global _FAIL
            _FAIL += 1
            _RESULTS.append((name, False, "SUITE CRASHED"))
            print("  [FAIL] %s 套件崩溃:" % name)
            traceback.print_exc()

    print("\n" + "=" * 62)
    print("RESULT: %d passed, %d failed" % (_PASS, _FAIL))
    bad = [n for n, ok, _ in _RESULTS if not ok]
    if bad:
        print("failed: " + ", ".join(bad))
    return 0 if _FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
