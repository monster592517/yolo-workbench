# -*- coding: utf-8 -*-
"""纯函数（零 I/O、零依赖）：判「运行中的 ComfyUI 用的 torch」与「磁盘 venv 里的 torch」是不是同一份。

为什么有它（2026-09-13 晚 3 实测故障，`AI动画产线_自检维修手册.md` §8.13）：
  1) ComfyUI 进程 **07:54** 启动，内存里是 `.venv_3dgs` 的 torch **2.13.0+cu130**；
  2) 当天 **14:40** 该 venv 的 torch 被换成 **2.4.1+cu124**（为 `gsplat pt24cu124`）；
  3) torch 2.13 的 `serialization.py` 第 149 行是**函数内惰性 import**
     （`from torch.utils.serialization import config`），只在**调用时**才去磁盘解析；
     而 2.4.1 **没有**该子包 ⇒ 16:26 一调 `torch.load` 就 `ModuleNotFoundError`。
     **只有 zip 格式 `.pth`（ControlNet）会走到**，safetensors 全免疫 —— 所以症状极像"只有它坏了"。
  4) 更糟：重启**不能**修 —— 2.4.1 上 `comfy_kitchen 0.2.31` 的 PEP585 注解会让
     `import comfy.utils` 直接 `ValueError`，ComfyUI 起不来。

⇒ 本模块只做两件**能纯函数化**的判定，真正的 I/O（读文件、问 8188）留在 `studio_doctor`：
  * `check_versions`：运行版本 vs 磁盘版本 —— 不一致就是"库在进程脚下被换过"；
  * `check_lazy_submodule`：**磁盘**那份 `serialization.py` 里若引用了某个**不存在**的子包 ⇒
    一旦用它加载 zip `.pth` 必炸（这条对"当前磁盘这份"做**自洽**检查，不需要进程信息）。

⚠️ 不写死任何版本号/行号 —— 判据全部来自传进来的文本与版本串，免得数字一升级就开始烂。
"""
from __future__ import annotations

import re

# 惰性（函数内）import 的形式：缩进空白的 `from X import ...`
_LAZY_IMPORT_RE = re.compile(r"^\s+from\s+([A-Za-z_][\w.]*)\s+import\s+", re.M)


def norm_version(v: str | None) -> str | None:
    """`2.4.1+cu124` → `2.4.1`；空/None → None。"""
    if not v:
        return None
    return v.split("+", 1)[0].strip() or None


def _v(*a):
    """把 (major, minor, patch) 里非数字的当 0，便于比较（不抛异常）。"""
    out = []
    for x in a:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            out.append(0)
    return tuple(out)


def parse_version(v: str | None) -> tuple:
    n = norm_version(v)
    if not n:
        return (0, 0, 0)
    parts = re.split(r"[.\-]", n)
    return _v(*(parts + ["0", "0", "0"])[:3])


def check_versions(running: str | None, disk: str | None) -> list[dict]:
    """运行中的 torch 版本 vs 磁盘 venv 的 torch 版本。

    - 两边都有且**不同** ⇒ `bad`：库在进程脚下被换过（进程仍用内存里那份）。
    - 任一边拿不到 ⇒ `warn`（拿不到就**不能**下结论，别当通过）。
    - 相同 ⇒ `ok`。
    """
    r, d = norm_version(running), norm_version(disk)
    out: list[dict] = []
    if r is None:
        out.append({"level": "warn", "code": "running_unknown",
                    "msg": "拿不到运行中 ComfyUI 的 torch 版本（离线？）",
                    "detail": "拿不到就不能判'一致'——先去 check_comfy 确认 8188 在线"})
    if d is None:
        out.append({"level": "warn", "code": "disk_unknown",
                    "msg": "拿不到磁盘 venv 的 torch 版本",
                    "detail": "确认 .venv_3dgs 里有 torch（dist-info / version.py）"})
    if r and d and r != d:
        out.append({
            "level": "bad", "code": "mismatch",
            "msg": f"运行中的 torch {r} ≠ 磁盘 venv 的 torch {d} ⇒ 不是同一份",
            "detail": "进程把库载入内存后，磁盘怎么改都不影响它；反之**重启**就会换成磁盘那份。"
                      "改共享 venv 前先确认没有别人的进程在用（见维修手册 §8.13）",
        })
    elif r and d:
        out.append({"level": "ok", "code": "match",
                    "msg": f"运行与磁盘的 torch 都是 {r}"})
    return out


def lazy_torch_imports(src: str) -> list[str]:
    """列出源码里**函数内**（有缩进的）`from torch.xxx import ...` 的模块名，去重排序。

    只看 `torch.*`：相对 import（`from . import config`）与第三方 import 不是我们要防的对象。
    """
    mods = {m for m in _LAZY_IMPORT_RE.findall(src or "") if m.startswith("torch.")}
    return sorted(mods)


def check_lazy_submodule(serialization_src: str | None,
                        present: dict | None = None) -> list[dict]:
    """**磁盘**那份 `torch/serialization.py` 引用的惰性子包，是否真的存在。

    `present`：`{子模块名: bool}`（调用方 os.path.exists 得到）。
    只报「文本里引用了、但目录不存在」这一种 ⇒ **对没写这种 import 的版本天然不误报**。
    """
    out: list[dict] = []
    if serialization_src is None:
        out.append({"level": "warn", "code": "no_source",
                    "msg": "读不到磁盘的 torch/serialization.py（跳过该检查）"})
        return out
    present = present or {}
    lazy = lazy_torch_imports(serialization_src)
    broken = [m for m in lazy if present.get(m) is False]
    if broken:
        out.append({
            "level": "bad", "code": "lazy_submodule_missing",
            "msg": "磁盘的 torch 引用了不存在的子包: " + ", ".join(broken),
            "detail": "这是**函数内** import，只在调用时才炸（例如 zip 格式 .pth 的 torch.load）"
                      "⇒ 用这份 torch 时 ControlNet 之类会 ModuleNotFoundError",
        })
    else:
        out.append({"level": "ok", "code": "lazy_submodule_ok",
                    "msg": f"惰性子包自洽（扫描 {len(lazy)} 条函数内 torch.* import）"})
    return out
