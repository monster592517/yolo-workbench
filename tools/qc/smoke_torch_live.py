# -*- coding: utf-8 -*-
"""`tools/qc/torch_live_check.py` 的正/负对照（本机纪律：守卫必须带正对照，≥2 条）。

**负对照是关键**：如果只测"该报的报了"，一个"永远返回 bad"的守卫也能过。
所以这里必须证明它对**正常情形不误报** —— 尤其
「没有函数内 import 的 torch 版本 + 子包缺失」这一组（2.4.1 就长这样，它其实是好的）。

每个用例的 `fn` 返回一个 bool = **"判定为有问题吗"**，与 `expect_bad` 比对。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import torch_live_check as T  # noqa: E402

CASES: list[tuple[str, bool, object]] = []


def C(name: str, expect_bad: bool, fn):
    CASES.append((name, expect_bad, fn))


def has_bad(rows) -> bool:
    return any(r["level"] == "bad" for r in rows)


# ---------------- check_versions ----------------
C("版本不同 ⇒ bad（正对照）", True,
  lambda: has_bad(T.check_versions("2.13.0+cu130", "2.4.1+cu124")))
C("版本相同 ⇒ 无 bad（负对照）", False,
  lambda: has_bad(T.check_versions("2.4.1+cu124", "2.4.1+cu124")))
C("只差 build tag（2.4.1+cu130 vs 2.4.1+cu124）⇒ 无 bad（负对照：别把本地标签当版本差异）", False,
  lambda: has_bad(T.check_versions("2.4.1+cu130", "2.4.1+cu124")))
C("运行侧拿不到 ⇒ 只有 warn，不下 bad（负对照）", False,
  lambda: has_bad(T.check_versions(None, "2.4.1+cu124")))
C("磁盘侧拿不到 ⇒ 只有 warn，不下 bad（负对照）", False,
  lambda: has_bad(T.check_versions("2.13.0+cu130", None)))
C("两边都拿不到 ⇒ 不能算通过（负对照：必须留 warn 痕迹）", False,
  lambda: has_bad(T.check_versions(None, None)) or
          not any(r["level"] == "warn" for r in T.check_versions(None, None)))

# ---------------- check_lazy_submodule ----------------
LAZY = ("def get_default_load_endianness():\n"
        "    from torch.utils.serialization import config\n"
        "    return config.load.endianness\n")

FLAT = ("def get_default_load_endianness():\n"
        "    return _default_load_endianness\n")

LOCAL = ("def f():\n"
         "    from . import config\n"
         "    import os\n"
         "    return config, os\n")

TOPLEVEL = "from torch.utils.serialization import config\n"

C("写了惰性子包 import 但它不存在 ⇒ bad（正对照）", True,
  lambda: has_bad(T.check_lazy_submodule(LAZY, {"torch.utils.serialization": False})))
C("同一文本但子包**存在** ⇒ 无 bad（负对照）", False,
  lambda: has_bad(T.check_lazy_submodule(LAZY, {"torch.utils.serialization": True})))
C("**没有**函数内 import 的版本 + 子包缺失 ⇒ 无 bad（关键负对照：2.4.1 就长这样，它是好的）", False,
  lambda: has_bad(T.check_lazy_submodule(FLAT, {"torch.utils.serialization": False})))
C("相对 import（`from . import x`）不算缺件 ⇒ 无 bad（负对照）", False,
  lambda: has_bad(T.check_lazy_submodule(LOCAL, {})))
C("读不到源码 ⇒ 只 warn、不算通过（负对照）", False,
  lambda: has_bad(T.check_lazy_submodule(None, {})) or
          not any(r["level"] == "warn" for r in T.check_lazy_submodule(None, {})))

# ---------------- lazy_torch_imports ----------------
C("提取函数内 torch.* import（正对照）", True,
  lambda: T.lazy_torch_imports(LAZY) == ["torch.utils.serialization"])
C("**顶层** import 不算「函数内」（负对照：无缩进的应被忽略）", False,
  lambda: bool(T.lazy_torch_imports(TOPLEVEL)))
C("第三方 / 相对 import 不被当成 torch 子包（负对照）", False,
  lambda: bool(T.lazy_torch_imports(LOCAL)))

# ---------------- 跑 ----------------
ok = 0
for name, expect_bad, fn in CASES:
    try:
        got = bool(fn())
        good = (got == expect_bad)
        if good:
            ok += 1
        mark = "PASS" if good else "FAIL"
        extra = "" if good else f"   <-- 期望 bad={expect_bad} 实际={got}"
        print(f"[{mark}] {name}{extra}")
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] {name}   <-- 抛异常 {type(e).__name__}: {e}")

print(f"\n{ok}/{len(CASES)} PASS")
sys.exit(0 if ok == len(CASES) else 1)
