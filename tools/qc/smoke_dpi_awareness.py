# -*- coding: utf-8 -*-
"""守卫「切换检测源时整个主窗口变小」这个 bug 不得复发。

════════════════════════════════════════════════════════════════════════
被守住的缺陷 (v0.34.1, 2026-10-03)
════════════════════════════════════════════════════════════════════════
症状: 检测页把来源切到「桌面」或「窗口」时, **整个主窗口在屏幕上缩一圈**
      (实测占屏宽 100% -> 73.8%)。切摄像头/视频/图片不犯。

真因: 程序从不声明 DPI 觉察 ⇒ 进程是 `DPI_AWARENESS_UNAWARE`, Windows 按 1.354
      插值放大铺满物理屏。而「桌面/窗口」走 `mss`, **`mss.MSS.__init__` 会调用
      `shcore.SetProcessDpiAwareness(2)`**(源码 `mss/windows/gdi.py:247`)⇒ 进程在
      **运行中途**翻成 `PER_MONITOR`, Windows 随即**停止插值放大**, 窗口像素矩形
      没跟着变大 ⇒ 屏上只剩 73.8%。
      (摄像头/视频/图片不 import mss ⇒ 觉察度不变 ⇒ 不犯。解释了触发范围。)

修法: `YOLO工作台.py` 顶部、`_start_splash()` **之前** 调
      `infrastructure.dpi.enable_per_monitor_dpi_awareness()`。

本守卫测四条 (缺一条这个 bug 都能悄悄回来):
  A. 依赖前提: 装着的 `mss` 确实会翻 DPI 觉察度 (上游行为变了要重新评估)
  B. 修法设施: `dpi.enable_...()` 能真的把 UNAWARE 翻成 PER_MONITOR, 且幂等
  C. ★接线: `YOLO工作台.py` 里那次调用**真的在** `tk.Tk()` / `_start_splash()` 之前
  D. ★端到端: 走真实入口顺序, **切到桌面源前后 DPI 觉察度必须不变**
     (这是唯一直接对应"窗口会不会缩"的判据; A~C 只是它的必要条件)
"""
import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.stdout.reconfigure(encoding="utf-8")
PY = sys.executable

fails = []


# ── 环境前置门（2026-10-10 开源清理新增）─────────────────────────────
# 本守卫需要一个**装了 requirements 的解释器**（要 import mss / tkinter）。
# 用裸 Python 跑会得到「No module named 'mss'」这种 FAIL —— 那是**环境没装依赖**，
# 不是判据坏了、也不是仓库有问题。把两者混成一个红叉，会训练人忽略 FAIL。
# ⇒ 依赖缺失时给 **SKIP（退出码 3）** 并说清怎么装，而不是伪装成"判据失效"。
_MISSING = []
for _m in ("mss", "tkinter"):
    try:
        __import__(_m)
    except Exception:
        _MISSING.append(_m)
if _MISSING:
    print(f"SKIP  smoke_dpi_awareness —— 当前解释器缺依赖: {', '.join(_MISSING)}")
    print(f"      解释器: {sys.executable}")
    print("      本守卫需要装了 requirements 的环境; 请先:")
    print("        python -m pip install -r requirements.txt")
    print("      然后重跑。**这不是判据失效** —— 判据本身没被验证过。")
    sys.exit(3)


def chk(name, cond, extra=""):
    print(("  OK   " if cond else "  FAIL ") + name + (("  " + extra) if extra else ""))
    if not cond:
        fails.append(name)


# ═══════════ A. 依赖前提: mss 仍会翻 DPI 觉察度 ═══════════
print("A. 依赖前提 —— 上游 mss 的行为")
try:
    import mss  # noqa
    gdi = Path(mss.__file__).parent / "windows" / "gdi.py"
    src = gdi.read_text(encoding="utf-8", errors="replace")
    has_call = "SetProcessDpiAwareness" in src or "SetProcessDPIAware" in src
    in_init = bool(re.search(
        r"def __init__\(self\)[\s\S]{0,900}?_set_dpi_awareness\(\)", src))
    chk("mss 源码里仍有 SetProcessDpiAwareness 调用", has_call)
    chk("该调用仍在 MSS.__init__ 路径上 (即第一次抓屏就会触发)", in_init)
    print(f"       -> {gdi}")
except Exception as e:
    chk("能读取 mss 源码", False, f"{e}")

# ═══════════ B. 修法设施: dpi 模块真的有效 + 幂等 ═══════════
print("\nB. 修法设施 —— infrastructure/dpi.py")
sys.path.insert(0, str(ROOT / "src"))
code_b = r"""
import sys, ctypes
sys.path.insert(0, r"%s")
from infrastructure.dpi import enable_per_monitor_dpi_awareness as en, dpi_awareness_name as nm
shcore = ctypes.windll.shcore
def raw():
    v = ctypes.c_int(0); shcore.GetProcessDpiAwareness(None, ctypes.byref(v)); return v.value
before = raw()
r1 = en()
after = raw()
r2 = en()          # 幂等
after2 = raw()
print(f"RES before={before} r1={r1} after={after} r2={r2} after2={after2}")
""" % (str(ROOT / "src"))
try:
    out = subprocess.run([PY, "-c", code_b], capture_output=True, text=True,
                         timeout=60,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                         if sys.platform == "win32" else 0).stdout
    m = re.search(r"RES before=(\d+) r1=(\S+) after=(\d+) r2=(\S+) after2=(\d+)", out)
    if not m:
        chk("dpi 模块可导入并可调用", False, out.strip()[:200])
    else:
        b, r1, a1, r2, a2 = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
        chk("调用后觉察度 = PER_MONITOR(2)", a1 == "2", f"before={b} -> after={a1}")
        chk("返回值为 'set'(本次真的设置了)", b == "0" and r1.strip("'") == "set",
            f"before={b} r1={r1}")
        chk("幂等: 第二次调用不再改变觉察度", a1 == a2, f"{a1} -> {a2}")
        chk("幂等: 第二次返回 'already'", r2.strip("'") == "already", f"r2={r2}")
except Exception as e:
    chk("dpi 模块子进程执行", False, str(e))

# ═══════════ C. ★接线: 调用必须早于任何 tk.Tk() ═══════════
print("\nC. 接线 —— YOLO工作台.py 里的调用位置")
entry = ROOT / "YOLO工作台.py"
try:
    text = entry.read_text(encoding="utf-8")
    tree = ast.parse(text)

    def line_of(node):
        return getattr(node, "lineno", 10 ** 9)

    # 找 enable_per_monitor_dpi_awareness 的调用行
    decl = None
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            fn = n.func
            nm = getattr(fn, "attr", None) or getattr(fn, "id", None)
            if nm == "enable_per_monitor_dpi_awareness" or (
                    nm and "_dpi_declare" in str(nm)):
                decl = line_of(n)
                break
    # 找最早的 tk.Tk() / TkinterDnD.Tk() 调用行 (含 `import tkinter as tk` 后的 Tk())
    tk_line = None
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            nm = getattr(n.func, "attr", None) or getattr(n.func, "id", None)
            if nm == "Tk":
                tk_line = line_of(n) if tk_line is None else min(tk_line, line_of(n))
    # _start_splash() 的定义行 + 它在 main() 里被调用的行
    splash_call = None
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            nm = getattr(n.func, "attr", None) or getattr(n.func, "id", None)
            if nm == "_start_splash":
                splash_call = line_of(n) if splash_call is None else min(splash_call, line_of(n))

    chk("入口文件里存在 DPI 声明调用", decl is not None,
        f"line={decl}" if decl else "没找到 enable_per_monitor_dpi_awareness()")
    chk("入口文件里存在 tk.Tk()", tk_line is not None, f"line={tk_line}")

    if decl and tk_line:
        chk("★ 声明早于 tk.Tk()", decl < tk_line, f"声明@{decl} vs Tk()@{tk_line}")
    if decl and splash_call:
        chk("★ 声明早于 _start_splash()", decl < splash_call,
            f"声明@{decl} vs _start_splash()@{splash_call}")
except Exception as e:
    chk("解析 YOLO工作台.py", False, str(e))

# ═══════════ D. ★★ 端到端: 切源前后 DPI 觉察度不得变 ═══════════
print("\nD. ★★ 端到端 —— 走真实入口顺序, 切「桌面」源前后 DPI 必须不变")
code_d = r'''
import sys, os, time, ctypes
from pathlib import Path
ROOT = Path(r"%s")
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("OPENCV_LOG_LEVEL", "OFF")
shcore = ctypes.windll.shcore
def raw():
    v = ctypes.c_int(0); shcore.GetProcessDpiAwareness(None, ctypes.byref(v)); return v.value

# ── 逐句复刻入口顺序 ──
from infrastructure.dpi import enable_per_monitor_dpi_awareness
decl = enable_per_monitor_dpi_awareness()
after_decl = raw()
import tkinter as tk
splash = tk.Tk(); splash.overrideredirect(True)
sw, sh = splash.winfo_screenwidth(), splash.winfo_screenheight()
splash.geometry("500x340+%%d+%%d" %% ((sw-500)//2, (sh-340)//2)); splash.update()
sg = splash.geometry(); splash.destroy()
from gui.main_window import App
app = App(visible=False)
try: tk._default_root = app.root
except Exception: pass
app.show(anchor_geom=sg)
for _ in range(30): app.root.update(); time.sleep(0.03)
app.show_page("detection")
for _ in range(90): app.root.update(); time.sleep(0.03)
time.sleep(5)
for _ in range(30): app.root.update(); time.sleep(0.03)
pg = app._pages.get("detection")
before = raw()
pg._on_src("desktop")
for _ in range(150): app.root.update(); time.sleep(0.02)
after = raw()
fin = raw()
# 也测「窗口」源
pg._on_src("window")
for _ in range(100): app.root.update(); time.sleep(0.02)
after_win = raw()
print(f"RES decl=%%s after_decl=%%d before=%%d after=%%d fin=%%d after_win=%%d src=%%s err=%%s"
      %% (decl, after_decl, before, after, fin, after_win, pg._src_val, pg._preview_err))
app.root.destroy()
''' % str(ROOT)
try:
    r = subprocess.run([PY, "-c", code_d], capture_output=True, text=True, timeout=240,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                       if sys.platform == "win32" else 0)
    m = re.search(
        r"RES decl=(\S+) after_decl=(\d+) before=(\d+) after=(\d+) fin=(\d+) after_win=(\d+)",
        r.stdout)
    if not m:
        chk("端到端可执行", False, (r.stdout[-300:] + r.stderr[-300:]).strip())
    else:
        decl, ad, bf, af, fin, aw = m.groups()
        chk("进程内声明生效 (after_decl=2)", ad == "2", f"decl={decl} after_decl={ad}")
        chk("★ 切「桌面」前后 DPI 不变 (这就是窗口会不会缩的判据)", bf == af == "2",
            f"切前={bf} -> 切后={af}")
        chk("★ 切「窗口」后 DPI 仍不变", aw == "2", f"after_win={aw}")
        chk("切源后仍在 PER_MONITOR (终态)", fin == "2", f"fin={fin}")
except Exception as e:
    chk("端到端子进程执行", False, str(e))

print()
if fails:
    print(f"FAIL  smoke 失败 {len(fails)} 项: {fails}")
    sys.exit(2)
print("PASS  smoke_dpi_awareness  (A 前提 / B 设施 / C 接线 / D 端到端 全部通过)")
sys.exit(0)
