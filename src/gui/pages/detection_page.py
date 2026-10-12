# -*- coding: utf-8 -*-
"""Detection Page - 后台抓帧 + 主线程定时渲染 (15fps), 摄像头惰性开启, 不阻塞UI."""
from __future__ import annotations
import tkinter as tk, threading, cv2, numpy as np, time as _time
from tkinter import ttk
from pathlib import Path
from PIL import Image, ImageTk
from infrastructure.config import get_config, get_theme
_tc = get_theme().colors
try: from tkinterdnd2 import DND_FILES
except: DND_FILES = None

_PREVIEW_INTERVAL = 66          # 主线程绘制间隔 ms (~15fps), 防止 UI 事件堆积
_CAMERA_BACKENDS = None

def _camera_backends():
    global _CAMERA_BACKENDS
    if _CAMERA_BACKENDS is None:
        try:
            _CAMERA_BACKENDS = (cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY)
        except Exception:
            _CAMERA_BACKENDS = (0,)
    return _CAMERA_BACKENDS

class DetectionPage(tk.Frame):
    def __init__(self, parent, controller=None):
        super().__init__(parent, bg=_tc.bg)
        self.controller = controller
        self.P=_tc.primary; BG=_tc.bg; T=_tc.text; S=_tc.text_secondary
        self._src_val = "camera"; self._cap = None; self._preview_running = False
        self._preview_thread = None; self._cam_report = None
        self._detector = None; self._detecting = False
        self._dropped_file = None  # path from drag-drop
        self._win_hwnd = None  # 当前选中窗口句柄
        self._wins = {}  # 标题 -> (hwnd, title)
        # 帧缓冲: 工作线程只写最新帧, 主线程 15fps 定时器统一绘制
        self._latest_preview = None   # RGB ndarray (已按画布缩放)
        self._latest_detect = None
        self._latest_fps = 0.0
        self._preview_err = None
        self._canvas_size = (1, 1)
        self._draw_job = None
        self._shown = False
        self._mirror_var = tk.BooleanVar(master=self, value=False)  # 自拍摄像头镜像
        # ★ 2026-10-03: 镜像状态的**线程可读副本**。
        #   背景: `tk.Variable` 在子线程里 `.get()` / `.set()` 都会抛
        #     `RuntimeError: main thread is not in main loop`
        #   (实测确认; 普通属性读写则完全正常)。而预览线程**每帧**都要问一次
        #   "要不要镜像", 拿 BooleanVar 去问 = 每帧抛一次、被外层 `except` 吞掉
        #   ⇒ **镜像功能实际从未生效**(而且报出的错还会污染 `_preview_err`)。
        #   做法: BooleanVar 仍是唯一真源(绑着 UI 的 Checkbutton),
        #         本属性是它的**主线程同步镜像**, 供子线程只读。
        #         `trace_add` 在**主线程**触发 —— 用户点勾选框时才会变。
        self._mirror_flag = False
        self._mirror_var.trace_add(
            "write", lambda *_: setattr(self, "_mirror_flag", bool(self._mirror_var.get())))

        from gui.components import topbar, sub_card, btn, step_card
        topbar(self, "🔍", "PT 检测", "选择来源即预览 | 拖放文件 | 点击开始检测",
               actions=[("📂 录制文件夹", self._open_record_dir, "border")])
        tk.Frame(self, height=1, bg=_tc.border).pack(fill="x", padx=24, pady=(0, 8))

        # ══════════════════════════════════════════════════════════════════
        # ★★ 2026-10-03 按功能重排: 原布局是**上下两张卡 + Tab 切换**
        #   （① 检测配置 / ② 实时画面，看一个就藏另一个）。实测后果:
        #   配置卡只有 177px, 而可用高度 874px ⇒ **下方 561px（64%）纯空白**,
        #   且"看画面时必须切走配置" —— 调参和看效果不能同时。
        #   重排为**常显双栏**: 左栏 320px 放「配置」（模型/置信度/来源/窗口）,
        #   右栏吃满剩余放「实时画面」。宽屏空间用起来, 且不再需要切 Tab。
        #   ⚠ 底部操作条保持 side="bottom" 常驻（原实现就是这么定的, 不动）。
        # ══════════════════════════════════════════════════════════════════
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=24, pady=(0, 8))
        body.columnconfigure(0, weight=0, minsize=320)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        # ── 左栏: 检测配置 ──
        cfg_card, cfg_body = step_card(body, 1, "检测控制", "模型/置信度/画面来源")
        cfg_card.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self._det_cfg_card = cfg_card
        row1 = tk.Frame(cfg_body, bg=BG); row1.pack(fill="x", padx=12, pady=(8, 2))
        tk.Label(row1, text="模型", font=("Microsoft YaHei UI", 8), bg=BG, fg=S).pack(anchor="w")
        pts = list(Path(get_config().output_dir).glob("*.pt"))
        self._mdl_var = tk.StringVar(master=self, value=pts[0].name if pts else "无模型")
        self._mdl_cb = None
        if pts:
            self._mdl_cb = ttk.Combobox(row1, textvariable=self._mdl_var, values=[p.name for p in pts],
                                        state="readonly", font=("Microsoft YaHei UI", 9))
            self._mdl_cb.pack(fill="x", pady=(2, 0))

        # 置信度: 竖排 (左栏窄, 横排会被挤)
        cf_row = tk.Frame(cfg_body, bg=BG); cf_row.pack(fill="x", padx=12, pady=(8, 2))
        tk.Label(cf_row, text="置信度", font=("Microsoft YaHei UI", 8), bg=BG, fg=S).pack(anchor="w")
        self._conf = tk.DoubleVar(master=self, value=0.5)
        tk.Scale(cf_row, from_=0.1, to=1.0, resolution=0.05, variable=self._conf, orient="horizontal",
                 bg=BG, fg=T, highlightbackground=BG, troughcolor=_tc.border).pack(fill="x", pady=(2, 0))
        # ★ FPS/状态位: 原实现 width=30 固定宽是为防撑宽行; 现在它独占一行, 改为贴左
        self._fps_lbl = tk.Label(cfg_body, text="FPS: --", font=("Microsoft YaHei UI", 9),
                                 bg=BG, fg=S, anchor="w")
        self._fps_lbl.pack(fill="x", padx=12, pady=(4, 0))
        self._fps_lbl.bind("<Button-1>", self._open_camera_guide)
        self._fps_lbl.bind("<Enter>", lambda e: self._fps_lbl.config(cursor="hand2"))

        # 画面来源: 由横排一行改为**两列网格**(左栏 320px 放不下 5 个横排按钮)
        src_row = tk.Frame(cfg_body, bg=BG); src_row.pack(fill="x", padx=12, pady=(10, 8))
        tk.Label(src_row, text="画面来源", font=("Microsoft YaHei UI", 8), bg=BG, fg=S).pack(anchor="w")
        src_grid = tk.Frame(cfg_body, bg=BG); src_grid.pack(fill="x", padx=12, pady=(2, 8))
        self._src_var = tk.StringVar(master=self, value="camera"); self._src_btns = {}
        for _i, (txt, val) in enumerate([("📷 摄像头", "camera"), ("🎬 视频", "video"),
                                         ("🖼 图片", "image"), ("🖥 桌面", "desktop"),
                                         ("🪟 窗口", "window")]):
            f = tk.Frame(src_grid, bg=_tc.sidebar_hover, highlightbackground=_tc.border,
                         highlightthickness=1)
            f.grid(row=_i // 2, column=_i % 2, sticky="ew", padx=2, pady=2)
            src_grid.columnconfigure(_i % 2, weight=1)
            self._src_btns[val] = f
            lbl = tk.Label(f, text=txt, font=("Microsoft YaHei UI", 9), bg=_tc.sidebar_hover,
                           fg=T, cursor="hand2")
            lbl.pack(padx=6, pady=4)
            f.bind("<Button-1>", lambda e, v=val: self._on_src(v))
            lbl.bind("<Button-1>", lambda e, v=val: self._on_src(v))

        # 窗口来源选择 (仅 window 模式显示) —— 改为挂在左栏内, 不横跨整页
        self._win_frame = tk.Frame(cfg_body, bg=BG)
        tk.Label(self._win_frame, text="窗口:", font=("Microsoft YaHei UI", 8), bg=BG, fg=S).pack(anchor="w")
        self._win_var = tk.StringVar(master=self, value="点击刷新")
        self._win_cb = ttk.Combobox(self._win_frame, textvariable=self._win_var, values=["点击刷新"],
                                    state="readonly", font=("Microsoft YaHei UI", 9))
        self._win_cb.pack(fill="x", pady=(2, 4))
        self._win_cb.bind("<<ComboboxSelected>>", self._on_win_selected)
        self._win_refresh = tk.Label(self._win_frame, text=" 刷新 ", font=("Microsoft YaHei UI", 9),
                                     bg=_tc.sidebar_hover, fg=T, padx=8, pady=2, cursor="hand2")
        self._win_refresh.pack(anchor="w")
        self._win_refresh.bind("<Button-1>", lambda e: self._refresh_windows())

        # ── 右栏: 实时画面 (常显, 吃掉剩余全宽) ──
        cv_card, cv_body = step_card(body, 2, "实时画面", "拖放文件或选择来源")
        cv_card.grid(row=0, column=1, sticky="nsew")
        self._det_cv_card = cv_card
        self._canvas = tk.Canvas(cv_body, bg=_tc.sidebar_bg, highlightthickness=0)
        self._canvas.pack(fill="both", expand=True, padx=8, pady=8)
        self._img_id = None
        if DND_FILES:
            self._canvas.drop_target_register(DND_FILES)
            self._canvas.dnd_bind("<<Drop>>", self._on_drop)

        # ── 底部操作条 ──
        # ── 底部操作条 (side=bottom: 窗口再矮按钮也固定在底部) ──
        ctrl = tk.Frame(self, bg=BG); ctrl.pack(side="bottom", fill="x", padx=24, pady=(0, 12))
        self._det_ctrl = ctrl
        self._det_ctrl_btns(ctrl)
        # 初始选中摄像头样式 (不自动开启设备, 由 on_show 惰性启动)
        f = self._src_btns["camera"]
        f.config(highlightbackground=self.P, highlightthickness=2, bg=_tc.sidebar_hover)
        self._start_draw_timer()
        self.after(200, lambda: self._det_show(0))

    def _det_show(self, i):
        """兼容旧调用: 重排后**两栏常显, 不再切 Tab**。

        保留本方法是因为 `__init__` 尾部、`on_show` 等处可能仍在调用它,
        且 `_det_mode` 被别处读取 (录制/绘制分支)。
        ⚠ 不再做 pack/pack_forget —— 那正是"看一个藏一个"的旧行为。
        """
        try:
            # 两栏始终在位; 只记录"用户想看哪块"(保留 _det_mode 语义, 不切布局)
            for w in (self._det_cfg_card, self._det_cv_card):
                if w is not None and not w.winfo_manager():
                    w.grid()
            # ⚠ Tab 条已随重排移除 (两栏常显), 这里不再调 _seg.set
            self._det_mode = i
        except Exception:
            pass

    def _det_ctrl_btns(self, ctrl):
        """底部操作条按钮 (常驻)"""
        from gui.components import btn
        self._btn_start = btn(ctrl, "  ▶ 开始检测 ", self._start, "primary", big=True)
        self._btn_start.pack(side="left", padx=(0, 8))
        self._btn_stop = btn(ctrl, "  ■ 停止 ", self._stop, "danger")
        self._btn_stop.pack(side="left", padx=(0, 8))
        self._btn_stop.config(state="disabled")
        self._btn_rec = btn(ctrl, "  ● 录制检测画面 ", self._toggle_record, "ghost")
        self._btn_rec.pack(side="left", padx=(0, 8))
        self._btn_rec.config(state="disabled")
        self._btn_open = btn(ctrl, "  📂 录制文件夹 ", self._open_record_dir, "ghost")
        self._btn_open.pack(side="left")
        self._mirror_chk = tk.Checkbutton(ctrl, text="🪞 自拍摄像头镜像",
                                          variable=self._mirror_var,
                                          bg=_tc.bg, fg=_tc.text_secondary,
                                          activebackground=_tc.bg,
                                          activeforeground=_tc.text,
                                          selectcolor=_tc.bg, font=("Microsoft YaHei UI", 9))
        self._mirror_chk.pack(side="left", padx=(12, 0))

    # ═══════════ 页面生命周期: 显示时启动预览, 隐藏时释放摄像头 ═══════════

    # ═══════════ 页面生命周期: 显示时启动预览, 隐藏时释放摄像头 ═══════════
    def on_show(self):
        self._shown = True
        self._refresh_models()
        if not self._detecting and not self._preview_running:
            self._start_preview()

    def on_hide(self):
        self._shown = False
        if not self._detecting:
            self._stop_preview()

    # ═══════════ 主线程绘制定时器 (唯一 after 循环, 自动合并, 15fps) ═══════════
    def _start_draw_timer(self):
        if self._draw_job is not None:
            return
        try:
            self._draw_job = self.after(_PREVIEW_INTERVAL, self._draw_tick)
        except Exception:
            pass

    def _draw_tick(self):
        self._draw_job = None
        try:
            cw, ch = self._canvas.winfo_width(), self._canvas.winfo_height()
            if cw > 2 and ch > 2:
                self._canvas_size = (cw, ch)
        except Exception:
            pass
        if self._detecting:
            frame, fps = self._latest_detect, self._latest_fps
        else:
            frame, fps = self._latest_preview, 0.0
        if frame is not None:
            try:
                import numpy as _np
                # 黑帧检测: 纯黑画面不直显, 给遮挡/占用提示 (避免"打开一片黑")
                if _np.asarray(frame).mean() < 3.0:
                    self._canvas.delete("img")
                    self._canvas.delete("cam_err")
                    cw, ch = self._canvas.winfo_width(), self._canvas.winfo_height()
                    self._canvas.create_text(
                        max(20, cw // 2), max(20, ch // 2),
                        text="📷 摄像头画面全黑\n可能原因: 物理挡片未开 / 被其他程序占用 / 驱动异常\n👉 点击右下 FPS 处查看诊断；或切换到 视频/图片/桌面 来源",
                        fill=_tc.warning, font=("Microsoft YaHei UI", 10, "bold"),
                        justify="center", width=max(80, cw - 80), tags="cam_err")
                else:
                    img = ImageTk.PhotoImage(Image.fromarray(frame))
                    self._draw_safe(img)
            except Exception:
                pass
        if fps > 0:
            try:
                self._fps_lbl.config(text=f"FPS: {fps:.1f}", fg=_tc.text_secondary)
            except Exception:
                pass
        if self._preview_err and not self._detecting:
            try:
                self._fps_lbl.config(text=self._preview_err, fg=_tc.danger)
            except Exception:
                pass
            # 画布居中显示诊断 (持续显示, 直到下次启动预览)
            try:
                self._canvas.delete("cam_err")
                cw, ch = self._canvas.winfo_width(), self._canvas.winfo_height()
                self._canvas.create_text(
                    max(20, cw // 2), max(20, ch // 2),
                    text=self._preview_err, fill=_tc.warning,
                    font=("Microsoft YaHei UI", 11, "bold"), justify="center",
                    width=max(60, cw - 60), tags="cam_err")
            except Exception:
                pass
        if self._preview_running or self._detecting:
            self._start_draw_timer()

    # ═══════════ 预览: 后台线程抓帧, 不阻塞 UI ═══════════
    def _start_preview(self):
        if self._preview_running or self._detecting:
            return
        z = getattr(self, "_preview_zombie", None)
        if z is not None and z.is_alive():
            # 旧摄像头线程未退净: 延迟重试, 避免双线程抢摄像头
            self.after(400, self._start_preview)
            return
        self._preview_zombie = None
        self._preview_running = True
        self._preview_err = None
        try:
            self._canvas.delete("cam_err")
        except Exception:
            pass
        self._latest_preview = None
        self._preview_thread = threading.Thread(target=self._preview_loop, daemon=True, name="preview")
        self._preview_thread.start()
        self._start_draw_timer()

    def _stop_preview(self):
        self._preview_running = False
        t = self._preview_thread
        self._preview_thread = None
        if t is not None and t.is_alive():
            # 短等待: 不阻塞页面切换 (摄像头释放慢时记录为 zombie, 由下次启动兜底)
            t.join(timeout=0.15)
            if t.is_alive():
                self._preview_zombie = t
        self._latest_preview = None

    def _open_camera_best(self):
        """打开第一个输出非黑帧的摄像头 (黑帧检测, 防止无声黑屏)。

        ⚠ 2026-10-03 修「假报错」—— 本函数**在预览子线程里被调用**
          (`_preview_loop` → `_open_camera_best`), 而它原先直接碰 `tk.Variable`:

              self._mirror_var.set(True)     # ← 子线程里必然抛

          实测(Tk 8.6, 本机): 子线程里**任何** `tk.Variable` 读写都会抛
              RuntimeError: main thread is not in main loop
          而**普通属性写** (`self._x = 1`) 完全正常 —— 所以这不是"线程不安全"的
          笼统问题, 是**规则明确**的: Tcl 变量对象只能主线程碰。

          后果(实测): 整个函数被 `except Exception as e` 兜住 ⇒
          `_preview_err = "摄像头打开异常: main thread is not in main loop"`,
          **但此时摄像头其实已经打开成功了**(返回值是正常的 `cv2.VideoCapture`)。
          ⇒ 用户/排障者看到的是**假的"摄像头打不开"**, 方向被彻底带偏。

          修法: 碰 Tk 的部分一律 `self.after(0, ...)` 回主线程执行。
          `_preview_err` 是**普通属性**, 子线程写它是安全的, 保持原样。
        """
        try:
            from core.camera_probe import open_best_camera, format_diagnosis
            cap, report = open_best_camera(frames=2, max_time=12.0)
            self._cam_report = report
            if cap is not None and report:
                try:
                    from core.camera_probe import is_selfie_camera
                    idx = int(report.get("index", 0) or 0)
                    if is_selfie_camera(idx):
                        # ★ 回主线程设 Tk 变量 (子线程直接 .set() 会抛, 见 docstring)
                        self.after(0, lambda: self._mirror_var.set(True))
                        from infrastructure.audit import audit
                        audit("camera_mirror_auto",
                              f"检测到自拍摄像头 index={idx}, 已启用默认镜像",
                              module="detection_page")
                except Exception:
                    pass
            if cap is None:
                self._preview_err = format_diagnosis(report)
                try:
                    from infrastructure.audit import audit
                    audit("camera_preview_fail", str(report), level="warn",
                          module="detection_page")
                except Exception:
                    pass
            return cap
        except Exception as e:
            self._preview_err = f"摄像头打开异常: {e}"
            return None

    def _open_camera_guide(self, e=None):
        """点击错误提示 -> 打开摄像头排查向导 (后台诊断, 不阻塞 UI)"""
        try:
            self._fps_lbl.config(text="正在生成摄像头诊断报告...", fg=_tc.text_secondary)
        except Exception:
            pass

        def _do():
            try:
                from core.camera_probe import diagnose, format_diagnose_report
                report = format_diagnose_report(diagnose())
            except Exception as ex:
                report = f"诊断失败: {ex}"
            try:
                self.after(0, lambda: self._show_guide_window(report))
            except Exception:
                pass
        threading.Thread(target=_do, daemon=True, name="cam-guide").start()

    def _show_guide_window(self, report):
        """展示诊断报告的独立窗口 (玻璃样式)"""
        win = tk.Toplevel(self)
        win.title("摄像头排查向导")
        win.geometry("760x520")
        win.configure(bg=_tc.card_bg)
        C = _tc
        head = tk.Frame(win, bg=C.card_bg)
        head.pack(fill="x", padx=16, pady=(16, 4))
        chip = tk.Canvas(head, width=38, height=38, bg=C.card_bg, highlightthickness=0)
        chip.pack(side="left")
        chip.create_oval(1, 1, 37, 37, fill=C.nav_active_bg, outline=C.border)
        chip.create_text(19, 18, text="🔍", font=("Microsoft YaHei UI", 13))
        tk.Label(head, text="摄像头排查报告", font=("Microsoft YaHei UI", 14, "bold"),
                 bg=C.card_bg, fg=C.text).pack(side="left", padx=(12, 0))
        tk.Label(head, text="  (打开失败原因与建议)", font=("Microsoft YaHei UI", 8),
                 bg=C.card_bg, fg=C.text_muted).pack(side="left", pady=(8, 0))
        wrap = tk.Frame(win, bg=C.card_bg, highlightbackground=C.border,
                        highlightthickness=1)
        wrap.pack(fill="both", expand=True, padx=16, pady=(8, 8))
        txt = tk.Text(wrap, font=("Consolas", 10), bg=C.log_bg, fg=C.log_text,
                      relief="flat", bd=0, padx=12, pady=8)
        txt.pack(fill="both", expand=True, padx=2, pady=2)
        try:
            # ⚠ 2026-09-12: 本来是 `tk.Scrollbar` + `dark_scrollbar()` —— 本机
            # `tk.Scrollbar` 由 Windows 原生绘制, bg/troughcolor 全部失效, 那条
            # 滚动条一直是近白色。改用 themed_scrollbar() 走 ttk+clam。
            from gui.components import themed_scrollbar
            sb = themed_scrollbar(wrap, command=txt.yview)
            sb.pack(side="right", fill="y")
            txt.configure(yscrollcommand=sb.set)
        except Exception:
            pass
        txt.insert("1.0", report)
        txt.configure(state="disabled")
        btn = tk.Label(win, text="  关闭  ", font=("Microsoft YaHei UI", 10, "bold"),
                       bg=_tc.primary, fg=_tc.bg2, padx=16, pady=8, cursor="hand2")
        btn.pack(pady=(0, 16))
        btn.bind("<Button-1>", lambda ev: win.destroy())
        win.bind("<Escape>", lambda ev: win.destroy())
        try:
            from infrastructure.audit import audit
            audit("camera_guide_open", "打开摄像头排查向导", module="detection_page")
        except Exception:
            pass

    def _preview_loop(self):
        cap = None; sct = None; mon = None; img_src = None
        val = self._src_val; file_path = self._dropped_file
        try:
            if val == "camera":
                cap = self._open_camera_best()
                if cap is None and not self._preview_err:
                    self._preview_err = "摄像头打开失败: 请检查设备/驱动/是否被其他程序占用"
            elif val == "video" and file_path:
                cap = cv2.VideoCapture(file_path)
            elif val == "image" and file_path:
                img_src = cv2.imread(file_path)
                if img_src is not None:
                    img_src = cv2.cvtColor(img_src, cv2.COLOR_BGR2RGB)
            elif val == "desktop":
                import mss; sct = mss.mss(); mon = sct.monitors[1]
            elif val == "window":
                import mss; sct = mss.mss(); mon = self._window_monitor()

            while self._preview_running:
                frame = None
                if cap is None and sct is None and img_src is None:
                    break  # 无可用帧源 (如摄像头打开失败), 结束线程
                if cap is not None and cap.isOpened():
                    ret, f = cap.read()
                    if not ret:
                        if val == "video":
                            break
                        _time.sleep(0.03)
                        continue
                    frame = cv2.cvtColor(f, cv2.COLOR_BGR2RGB)
                    if val == "camera" and self._mirror_flag:
                        try:
                            frame = cv2.flip(frame, 1)
                        except Exception:
                            pass
                elif sct is not None:
                    if val == "window":
                        mon = self._window_monitor()
                        if mon is None:
                            _time.sleep(0.05)
                            continue
                    try:
                        f = np.array(sct.grab(mon))
                        frame = cv2.cvtColor(f, cv2.COLOR_BGRA2RGB)
                    except Exception:
                        _time.sleep(0.05)
                        continue
                elif img_src is not None:
                    frame = img_src.copy()
                    if val == "image":
                        self._preview_running = False  # single frame

                if frame is not None:
                    cw, ch = self._canvas_size
                    if cw > 2 and ch > 2:
                        h, w = frame.shape[:2]
                        s = min(cw / w, ch / h)
                        nw, nh = max(1, int(w * s)), max(1, int(h * s))
                        try:
                            frame = cv2.resize(frame, (nw, nh))
                        except Exception:
                            pass
                    self._latest_preview = frame
                if val == "image":
                    break
                _time.sleep(0.03)
        except Exception as e:
            self._preview_err = f"预览异常: {e}"
        finally:
            self._preview_running = False
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
            if sct is not None:
                try:
                    sct.close()
                except Exception:
                    pass

    def _draw_safe(self, img):
        try:
            if self._img_id:
                self._canvas.delete(self._img_id)
            cw, ch = self._canvas.winfo_width() // 2, self._canvas.winfo_height() // 2
            self._img_id = self._canvas.create_image(cw, ch, anchor="center", image=img)
            self._canvas.image = img
        except Exception:
            pass

    def _on_src(self, val):
        if self._detecting and self._detector:
            self._stop()
        self._src_val = val
        if val in ("camera", "desktop", "window"):
            self._dropped_file = None
        self._stop_preview()
        for v, f in self._src_btns.items():
            a = v == val
            f.config(highlightbackground=self.P if a else _tc.border,
                     highlightthickness=2 if a else 1,
                     bg=_tc.sidebar_hover if a else _tc.card_bg)
        if val == "window":
            # 重排后 _win_frame 挂在**左栏 cfg_body 内部** —— padx 用列内间距(12),
            # 不再是整页留白(24); 否则它会比同栏其他控件窄一截、看着没对齐。
            self._win_frame.pack(fill="x", padx=12, pady=(0, 8))
            if not self._wins:
                self._refresh_windows()
        else:
            self._win_frame.pack_forget()
        if val in ("video", "image") and not self._dropped_file:
            from tkinter import filedialog
            ft = [("Video", "*.mp4;*.avi;*.mov")] if val == "video" else [("Image", "*.jpg;*.png;*.bmp")]
            fp = filedialog.askopenfilename(title="选择文件", filetypes=ft)
            if fp:
                self._dropped_file = fp
        if self._shown and not self._detecting:
            self._start_preview()

    # ═══════════ 检测: 加载模型/打开源/停止 全部后台执行, UI 不冻结 ═══════════
    def _start(self):
        mdl = self._mdl_var.get()
        if not mdl or mdl == "无模型":
            from tkinter import messagebox
            messagebox.showwarning("提示", "请先选择模型")
            return
        pt = Path(get_config().output_dir) / mdl
        if not pt.exists():
            from tkinter import messagebox
            messagebox.showwarning("提示", f"模型不存在: {pt}")
            return
        if self._src_val == "image":
            fp = self._dropped_file
            if not fp or not Path(fp).exists():
                self._fps_lbl.config(text="请先选择图片", fg=_tc.danger)
                return
            self._stop_preview()
            self._detecting = True
            self._btn_start.config(state="disabled"); self._btn_stop.config(state="normal")
            threading.Thread(target=self._start_worker, args=(str(pt),), daemon=True,
                             name="detect-start").start()
            return
        if self._src_val == "window" and self._win_hwnd is None:
            self._fps_lbl.config(text="请先刷新并选择窗口", fg=_tc.danger)
            return
        if self._src_val == "camera":
            try:
                from core.camera_probe import is_selfie_camera
                idx = 0
                if self._cam_report:
                    idx = int(self._cam_report.get("index", 0) or 0)
                if is_selfie_camera(idx):
                    self._mirror_var.set(True)
            except Exception:
                pass
        self._stop_preview()
        self._detecting = True
        self._btn_start.config(state="disabled"); self._btn_stop.config(state="normal")
        self._fps_lbl.config(text="正在加载模型...")
        self._start_draw_timer()
        threading.Thread(target=self._start_worker, args=(str(pt),), daemon=True,
                         name="detect-start").start()

    def _start_worker(self, pt):
        try:
            from core.detector import DetectionPipeline, SourceType
            dp = DetectionPipeline(pt, self._conf.get(),
                                   mirror=bool(self._mirror_flag))
            if self._src_val == "image":
                dp.load_model()
                result = dp.run_image(self._dropped_file)
                self.after(0, lambda: self._image_done(result))
                return
            src_val = self._dropped_file if self._src_val == "video" and self._dropped_file else "0"
            if self._src_val == "window":
                src_val = (self._win_hwnd, self._win_var.get())
            src_val = str(src_val).replace("\\\\", "\\") if isinstance(src_val, str) else src_val
            sm = {"camera": SourceType.CAMERA, "video": SourceType.VIDEO,
                  "desktop": SourceType.DESKTOP, "window": SourceType.WINDOW}
            self.after(0, lambda: self._fps_lbl.config(
                text="正在加载模型 (CPU 约需 3-10 秒)..."))
            dp.load_model()
            self.after(0, lambda: self._fps_lbl.config(
                text="模型就绪, 正在打开画面源/验证画面..."))
            dp.set_source(sm.get(self._src_val, SourceType.CAMERA), src_val)
            dp.set_callbacks(on_frame=self._on_result)
            dp.start()
            self.after(0, lambda: self._start_done(dp))
        except Exception as e:
            self.after(0, lambda: self._start_failed(str(e)))

    def _start_done(self, dp):
        self._detector = dp
        self._btn_rec.config(state="normal", text="  ● 录制检测画面  ", bg=_tc.purple)
        self._fps_lbl.config(text="检测运行中", fg=_tc.text_secondary)
        self._audit("detect_start", f"{self._src_val} 源 模型 {self._mdl_var.get()}")

    def _start_failed(self, err):
        self._fps_lbl.config(text=f"错误: {err}", fg=_tc.danger)
        self._btn_start.config(state="normal"); self._btn_stop.config(state="disabled")
        self._btn_rec.config(state="disabled", text="  ● 录制检测画面  ", bg=_tc.purple)
        self._detecting = False
        self._audit("detect_start_failed", err, "error")
        if self._shown:
            self._start_preview()

    def _image_done(self, result):
        if result is not None:
            cw, ch = self._canvas_size
            if cw > 2 and ch > 2:
                h, w = result.shape[:2]
                s = min(cw / w, ch / h)
                nw, nh = max(1, int(w * s)), max(1, int(h * s))
                try:
                    result = cv2.resize(result, (nw, nh))
                except Exception:
                    pass
            self._latest_preview = result
        self._fps_lbl.config(text="图片检测完成")
        self._btn_start.config(state="normal"); self._btn_stop.config(state="disabled")
        self._detecting = False

    def _stop(self):
        dp = self._detector
        self._detector = None
        self._detecting = False
        self._btn_start.config(state="normal"); self._btn_stop.config(state="disabled")
        self._btn_rec.config(state="disabled", text="  ● 录制检测画面  ", bg=_tc.purple)
        self._fps_lbl.config(text="已停止")
        self._latest_detect = None
        if dp is not None:
            threading.Thread(target=self._stop_worker, args=(dp,), daemon=True,
                             name="detect-stop").start()
        elif self._shown:
            self._start_preview()

    def _stop_worker(self, dp):
        try:
            if dp.is_recording():
                try:
                    dp.stop_recording()
                except Exception:
                    pass
            dp.stop()
        except Exception as e:
            self._audit("detect_stop_failed", str(e), "error")
        self.after(0, self._stop_done)

    def _stop_done(self):
        self._audit("detect_stop", f"{self._src_val} 源")
        if self._shown and not self._detecting:
            self._start_preview()

    def _on_result(self, frame, fps=0):
        if fps > 0:
            self._latest_fps = fps
        if frame is None:
            return
        cw, ch = self._canvas_size
        if cw > 2 and ch > 2:
            h, w = frame.shape[:2]
            s = min(cw / w, ch / h)
            nw, nh = max(1, int(w * s)), max(1, int(h * s))
            try:
                frame = cv2.resize(frame, (nw, nh))
            except Exception:
                pass
        self._latest_detect = frame

    def _on_drop(self, event):
        try:
            from utils.file_utils import parse_dnd_paths
            paths = parse_dnd_paths(event.data)
        except Exception:
            paths = [event.data.strip().strip("{}")]
        if not paths:
            return
        fp = paths[0]
        p = Path(fp)
        if p.suffix.lower() in {".mp4", ".avi", ".mov", ".mkv", ".webm"}:
            self._dropped_file = str(p)
            self._src_val = "video"
            self._on_src("video")
            self._fps_lbl.config(text=f"视频: {p.name}")
        elif p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}:
            self._dropped_file = str(p)
            self._src_val = "image"
            self._on_src("image")
            self._fps_lbl.config(text=f"图片: {p.name}")

    def _refresh_models(self):
        """页面显示时刷新模型列表 (pt 文件可能被训练/管理功能增删); 目录指纹缓存避免重复扫描"""
        try:
            out = Path(get_config().output_dir)
            fp = (out.stat().st_mtime_ns, out.stat().st_size) if out.exists() else None
            if getattr(self, "_pt_fp", None) == fp:
                return
            self._pt_fp = fp
            pts = sorted(out.glob("*.pt"), key=lambda p: p.stat().st_mtime, reverse=True) if fp else []
            names = [p.name for p in pts]
            if names:
                if self._mdl_cb is not None:
                    self._mdl_cb["values"] = names
                if self._mdl_var.get() not in names:
                    self._mdl_var.set(names[0])
            else:
                if self._mdl_cb is not None:
                    self._mdl_cb["values"] = []
                self._mdl_var.set("无模型")
        except Exception:
            pass

    def _refresh_windows(self):
        """枚举当前可见窗口并填入下拉框"""
        try:
            from core.detector import enum_windows
            wins = enum_windows()
        except Exception as e:
            self._fps_lbl.config(text=f"窗口枚举失败: {e}", fg=_tc.danger)
            self._audit("window_refresh_failed", str(e), "error")
            return
        self._wins = {}
        titles = []
        for hwnd, title, _l, _t, w, h in wins:
            key = f"{title}  [{w}x{h}]"
            if key not in self._wins:
                self._wins[key] = (hwnd, title)
                titles.append(key)
        if not titles:
            titles = ["未发现可见窗口"]
        self._win_cb["values"] = titles
        if self._win_var.get() not in titles:
            self._win_var.set(titles[0])
        self._on_win_selected()
        self._fps_lbl.config(text=f"已刷新窗口: {len(self._wins)} 个")
        self._audit("window_refresh", f"枚举 {len(self._wins)} 个窗口")

    def _on_win_selected(self, event=None):
        item = self._wins.get(self._win_var.get())
        self._win_hwnd = item[0] if item else None
        if item:
            self._audit("window_selected", item[1])

    def _window_monitor(self):
        """获取当前选中窗口的屏幕矩形 (mss 可抓取格式)"""
        hwnd = self._win_hwnd
        if not hwnd:
            return None
        try:
            import ctypes
            from ctypes import wintypes
            rect = wintypes.RECT()
            if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                w, h = rect.right - rect.left, rect.bottom - rect.top
                if w > 10 and h > 10:
                    return {"top": rect.top, "left": rect.left, "width": w, "height": h}
        except Exception:
            pass
        return None

    # ═══════════ 录制检测画面 ═══════════
    def _toggle_record(self):
        if not self._detector:
            return
        try:
            if self._detector.is_recording():
                n = self._detector.stop_recording()
                self._btn_rec.config(text="  ● 录制检测画面  ", bg=_tc.purple)
                self._fps_lbl.config(text=f"录制已保存 ({n} 帧)")
                self._audit("detect_recording_stop", f"保存 {n} 帧")
            else:
                rec_dir = Path(get_config().record_dir)
                rec_dir.mkdir(parents=True, exist_ok=True)
                from datetime import datetime
                path = rec_dir / f"detect_{datetime.now():%Y%m%d_%H%M%S}.mp4"
                self._detector.start_recording(str(path))
                self._btn_rec.config(text="  ■ 停止录制  ", bg=_tc.danger)
                self._fps_lbl.config(text=f"录制中: {path.name}")
                self._audit("detect_recording_start", path.name)
        except Exception as e:
            self._fps_lbl.config(text=f"录制异常: {e}", fg=_tc.danger)
            self._audit("detect_recording_failed", str(e), "error")

    def _open_record_dir(self):
        rec_dir = Path(get_config().record_dir)
        try:
            rec_dir.mkdir(parents=True, exist_ok=True)
            import os
            os.startfile(str(rec_dir))
            self._audit("open_record_dir", str(rec_dir))
        except Exception as e:
            self._fps_lbl.config(text=f"打开失败: {e}", fg=_tc.danger)
            self._audit("open_record_dir_failed", str(e), "error")

    def _audit(self, action, detail, level="info"):
        try:
            from infrastructure.audit import audit as _a
            _a(action, detail, level=level, module="detection")
        except Exception:
            pass
