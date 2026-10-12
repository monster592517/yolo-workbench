# -*- coding: utf-8 -*-
"""doctor_probe_files.py — 自检「日志目录可写」探针的**纯函数**实现（零依赖）。

## 为什么要单独抽出来（2026-09-13 晚实测，踩了两次）

原实现是一句话：**写一个探针文件 → 立刻删掉**。但本项目批量删除有安全闸门
（单回合删除计数 > 50 ⇒ shim 抛 **`SystemExit`**，它是 `BaseException` 子类）：

1. **第一次踩**：`except Exception` 抓不住 `SystemExit` ⇒ 异常一路逃到 `main()`，
   **整份自检在第 12 项静默截断**（后面 60 多项一个没跑），却看起来"跑过了"。
2. **第二次踩**：给 `unlink` 单独兜 `BaseException` 之后，不再截断了 —— 但变成
   **每次超闸门就留一个探针文件 + 报一条常驻 🟡**。等于"自检自己留垃圾 + 慢性假告警"，
   而慢性假告警会训练人忽略自检（本项目明确禁止，见避坑清单 §O.30）。

## 现在的口径（三条，都有理由）

* **判据只看「写得进去」**：写得进去就证明目录可写。**删不掉不算缺陷** ——
  那是闸门的行为，不是产线的行为。把闸门的行为报成目录问题就是误报。
* **探针文件名固定**（`PROBE_NAME`，不是随机/时间戳）⇒ 最多只留 **1 个**文件，
  且 `sweep_probe()` 会在**下次自检开头**先扫掉它 ⇒ 残留是自愈的。
* **任何一步都不许把 `BaseException` 抛给调用方**：分级由返回值决定，调用方只负责 `add()`。
  这样"某个探针把整份自检干掉"这件事在结构上就不可能再发生。

## 分级口径（写失败时怎么分）

| 写失败的原因 | 级别 | 理由 |
|---|---|---|
| `SystemExit`（安全闸门） | `warn` | 本回合操作太多，**不是权限问题**，重跑一次就好 |
| 其它 `BaseException` / `OSError` | `bad`  | 真的写不进去（权限 / 路径 / 磁盘满） |

`write` / `unlink` 可注入，便于 `tools/qc/smoke_doctor_probe.py` 造 `SystemExit` 做正对照；
默认实现就是 `Path.write_text` / `Path.unlink`。
"""
from __future__ import annotations

from pathlib import Path

# ★ 固定字面量：不要改成 uuid / 时间戳 / tempfile.mkstemp。
#   固定 ⇒ 残留最多 1 个、可被 sweep_probe 认出来清掉。
PROBE_NAME = "_doctor_probe.txt"
PROBE_TEXT = "ok"


def probe_path(logdir) -> Path:
    """探针文件的**唯一**路径真源（sweep 与 probe 必须用同一个名字）。"""
    return Path(logdir) / PROBE_NAME


def sweep_probe(logdir, unlink=None) -> dict:
    """清掉上一次留下的探针文件。**绝不抛异常**。

    返回 ``{"swept": bool, "err": str|None}``：
      * 没有残留 ⇒ ``swept=False, err=None``（**不是错误**，别报红）
      * 清掉了   ⇒ ``swept=True,  err=None``
      * 清不掉   ⇒ ``swept=False, err="{类型}: {消息}"``（通常又是闸门，无害）
    """
    f = probe_path(logdir)
    rm = unlink or (lambda p: Path(p).unlink(missing_ok=True))
    try:
        existed = f.exists()
    except BaseException as e:                            # noqa: BLE001
        return {"swept": False, "err": f"{type(e).__name__}: {e}"}
    if not existed:
        return {"swept": False, "err": None}
    try:
        rm(f)
        return {"swept": True, "err": None}
    except BaseException as e:                            # noqa: BLE001
        return {"swept": False, "err": f"{type(e).__name__}: {e}"}


def probe_writable(logdir, write=None, unlink=None, sweep=None) -> dict:
    """测日志目录可写性。**绝不抛异常**。

    返回 ``{"level": "ok"|"warn"|"bad", "msg": str, "detail": str, "leftover": bool}``，
    调用方直接 ``add(**{k: r[k] for k in ("level","msg","detail")})``。

    ``sweep`` 传 `sweep_probe()` 的结果时，会把"上次残留被清掉 / 清不掉"的信息并进 detail。
    """
    d = Path(logdir)
    f = probe_path(d)
    wr = write or (lambda p, t: Path(p).write_text(t, encoding="utf-8"))
    rm = unlink or (lambda p: Path(p).unlink(missing_ok=True))

    # ① 写
    try:
        wr(f, PROBE_TEXT)
    except SystemExit as e:                               # ★ 闸门 ≠ 权限问题
        return {"level": "warn", "msg": "日志目录可写性**未测到**（被安全闸门拦下：SystemExit）",
                "detail": f"本回合操作数超闸门，跟目录权限无关 ⇒ 重跑一次自检即可｜{e}",
                "leftover": False}
    except BaseException as e:                            # noqa: BLE001
        return {"level": "bad", "msg": f"日志目录不可写: {type(e).__name__}: {e}",
                "detail": f"判据=「写不进去就是有问题」｜目标 {d}｜查权限/磁盘满/路径失效",
                "leftover": False}

    # ② 收尾（删不掉不算缺陷 —— 见模块 docstring）
    detail = []
    if isinstance(sweep, dict):
        if sweep.get("swept"):
            detail.append("已清掉上次残留的探针文件")
        elif sweep.get("err"):
            detail.append(f"上次残留仍清不掉（{sweep['err']}，通常是闸门）")
    leftover = False
    try:
        rm(f)
    except BaseException as e:                            # noqa: BLE001
        leftover = True
        detail.append(f"本次探针未能删除（{type(e).__name__}，通常是本回合删除计数超闸门）"
                      f"⇒ 残留 {f}，**下次自检开头会自动清掉**，无需手删")
    return {"level": "ok", "msg": f"日志目录可写 {d}", "detail": "；".join(detail),
            "leftover": leftover}
