# -*- coding: utf-8 -*-
"""smoke_doctor_probe.py — `doctor_probe_files` 的正/负对照自检。

**为什么必须有它**：这个探针已经**踩过两次**（截断整份自检 / 留垃圾+常驻假告警），
两次都不是"没写对"，而是**没有对照** —— 没人试过"删除抛 SystemExit"会发生什么。
所以这里把两次事故都固化成对照用例。

用法：``py -3.13 -X utf8 tools\\qc\\smoke_doctor_probe.py``（退出码 0 = 全过）
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import doctor_probe_files as D   # noqa: E402

PASS = FAIL = 0
RESULTS: list[tuple[bool, str]] = []


def check(cond: bool, name: str):
    global PASS, FAIL
    if cond:
        PASS += 1
        RESULTS.append((True, name))
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        RESULTS.append((False, name))
        print(f"[FAIL] {name}")


class Boom(BaseException):
    """模拟非 SystemExit 的 BaseException（如 KeyboardInterrupt 族）。"""


def raiser(exc):
    def _f(*a, **k):
        raise exc
    return _f


def ok_write(*a, **k):
    Path(a[0]).write_text(a[1], encoding="utf-8")


def ok_unlink(p):
    Path(p).unlink(missing_ok=True)


# ── ① 正常路径 ────────────────────────────────────────────────
with tempfile.TemporaryDirectory() as td:
    r = D.probe_writable(td)
    check(r["level"] == "ok" and not r["leftover"], "写成功+删成功 ⇒ ok（正对照）")
    check(not D.probe_path(td).exists(), "正常路径下**不留**探针文件（正对照：别留垃圾）")

# ── ② ★ 事故一：unlink 抛 SystemExit ──────────────────────────
#    期望：不抛出去 + **不是 bad**（闸门不是目录问题）+ 明说残留会自愈
with tempfile.TemporaryDirectory() as td:
    escaped = None
    try:
        r = D.probe_writable(td, write=ok_write, unlink=raiser(SystemExit("SAFE_DELETE_BULK_CONFIRM_REQUIRED")))
    except BaseException as e:                            # noqa: BLE001
        escaped = e
        r = {}
    check(escaped is None, "★ 删除抛 SystemExit ⇒ **不抛给调用方**（正对照：这就是截断整份自检的那条路）")
    check(r.get("level") == "ok", "★ 删除抛 SystemExit ⇒ level 仍是 ok（负对照：别把闸门当目录不可写）")
    check(r.get("leftover") is True, "★ 删除抛 SystemExit ⇒ leftover=True 被记下来（正对照：不许静默）")
    check("自动清掉" in r.get("detail", ""), "★ detail 说清「下次自检会自动清」（正对照：给出退路）")
    check(r.get("msg", "").startswith("日志目录可写"), "★ msg 仍然是「可写」（正对照：判据=写得进去）")

# ── ③ 删除抛 OSError：同样不该报 bad ──────────────────────────
with tempfile.TemporaryDirectory() as td:
    r = D.probe_writable(td, write=ok_write, unlink=raiser(PermissionError("locked")))
    check(r["level"] == "ok" and r["leftover"], "删失败(OSError) ⇒ 仍 ok + 记 leftover（负对照）")

# ── ④ 写失败分级 ──────────────────────────────────────────────
with tempfile.TemporaryDirectory() as td:
    r = D.probe_writable(td, write=raiser(PermissionError("denied")))
    check(r["level"] == "bad" and "不可写" in r["msg"], "写抛 PermissionError ⇒ bad（正对照：真不可写必须红）")

with tempfile.TemporaryDirectory() as td:
    escaped = None
    try:
        r = D.probe_writable(td, write=raiser(SystemExit("gate")))
    except BaseException as e:                            # noqa: BLE001
        escaped = e
        r = {}
    check(escaped is None, "写抛 SystemExit ⇒ 不抛给调用方（正对照）")
    check(r.get("level") == "warn", "写抛 SystemExit ⇒ warn 而不是 bad（负对照：闸门≠权限问题）")

with tempfile.TemporaryDirectory() as td:
    escaped = None
    try:
        r = D.probe_writable(td, write=raiser(Boom("?")), unlink=raiser(Boom("?")))
    except BaseException as e:                            # noqa: BLE001
        escaped = e
        r = {}
    check(escaped is None and r.get("level") == "bad",
          "写抛**任意** BaseException（非 SystemExit）⇒ 兜住且判 bad（正对照：BaseException 也得兜）")

# ── ⑤ sweep：残留自愈 ─────────────────────────────────────────
with tempfile.TemporaryDirectory() as td:
    D.probe_path(td).write_text("stale", encoding="utf-8")
    s = D.sweep_probe(td)
    check(s["swept"] and not D.probe_path(td).exists(), "sweep 清掉残留（正对照：自愈成立）")

with tempfile.TemporaryDirectory() as td:
    s = D.sweep_probe(td)
    check(s["swept"] is False and s["err"] is None, "无残留 ⇒ swept False 且 err=None（负对照：别把「没东西可清」当错）")

with tempfile.TemporaryDirectory() as td:
    D.probe_path(td).write_text("stale", encoding="utf-8")
    escaped = None
    try:
        s = D.sweep_probe(td, unlink=raiser(SystemExit("gate")))
    except BaseException as e:                            # noqa: BLE001
        escaped = e
        s = {}
    check(escaped is None and s.get("swept") is False and s.get("err"),
          "sweep 清不掉 ⇒ 不抛 + 如实记 err（正对照）")

# ── ⑥ sweep 的信息要并进 probe 的 detail ──────────────────────
with tempfile.TemporaryDirectory() as td:
    D.probe_path(td).write_text("stale", encoding="utf-8")
    s = D.sweep_probe(td)
    r = D.probe_writable(td, sweep=s)
    check("已清掉上次残留" in r["detail"], "sweep 结果并入 detail（正对照：别让清残留这件事静默）")

# ── ⑦ 名字固定 ⇒ 反复跑不会累积文件 ───────────────────────────
with tempfile.TemporaryDirectory() as td:
    def blind_write(p, t):                                # 写成功但永远删不掉
        Path(p).write_text(t, encoding="utf-8")
    for _ in range(5):
        D.probe_writable(td, write=blind_write, unlink=raiser(SystemExit("gate")))
    n = len(list(Path(td).iterdir()))
    check(n == 1, f"删不掉时反复跑只留 **1 个**文件（实测 {n} 个）（正对照：名字必须是固定的）")

    # ★ 用 AST 判「名字是字面量常量 + 只有一处真源」。
    #   为什么不用字符串扫描：本模块的 docstring 里**就写着** uuid / mkstemp 这两个词
    #   （是"别这么改"的警告），扫源码会扫到自己的注释 ⇒ 自指假红。
    #   这正是用户手册里那条「扫描类脚本必须显式排除自己的输出」的同族陷阱。
    import ast
    tree = ast.parse(Path(D.__file__).read_text(encoding="utf-8"))
    assign = [n for n in ast.walk(tree)
              if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == "PROBE_NAME" for t in n.targets)]
    is_literal = bool(assign) and isinstance(assign[0].value, ast.Constant) \
        and isinstance(assign[0].value.value, str)
    check(is_literal, "PROBE_NAME 是**字面量常量**（AST 判：不是 f-string/调用/变量）（正对照）")
    pdef = [n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "probe_path"]
    uses = bool(pdef) and "PROBE_NAME" in ast.unparse(pdef[0])
    check(uses, "probe_path() 用 PROBE_NAME 拼路径（AST 判：探针名只有一处真源）（正对照）")

# ── ⑧ 端到端：真目录 + 真 sweep ───────────────────────────────
with tempfile.TemporaryDirectory() as td:
    r = D.probe_writable(td, sweep=D.sweep_probe(td))
    check(r["level"] == "ok" and r["detail"] == "" and not D.probe_path(td).exists(),
          "端到端无异常 ⇒ ok 且 detail 为空（负对照：别输出无效噪声）")

print(f"\n{PASS}/{PASS + FAIL} PASS")
if FAIL:
    print("失败项：")
    for ok, name in RESULTS:
        if not ok:
            print("  -", name)
sys.exit(1 if FAIL else 0)
