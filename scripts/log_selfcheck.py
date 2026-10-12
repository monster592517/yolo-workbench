# -*- coding: utf-8 -*-
"""log_selfcheck.py — 审计日志子系统自检 (2026-09-11)

为什么单独有一个:
    audit() 是全项目最热的调用之一 (启动/切页/训练/标注/报错都在写), 而它的写入
    路径在 2026-09-11 做了一次性能改造:
        before: 每条日志 → mkdir(parents=True) + open/write/close
        after : 目录只建一次 + 常驻句柄 (行缓冲, 仍然每条落盘)
    这类"改动最热的写路径"的改动, 最怕两件事:
      (1) 为了性能把落盘语义改没了 (崩溃后丢日志) —— 用户明确要求日志可查询;
      (2) Windows 上"开着句柄去 rename"导致**轮转静默失效** (异常被 except 吞掉,
          表现是文件顶到 20MB 就不再轮转, 而且一声不吭)。
    所以本脚本逐条验: 落盘及时性 / 轮转真的发生 / 句柄坏了能自愈 / 目录只建一次。

用法: python scripts/log_selfcheck.py            (退出码 0 = 全过)
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

_PASS = 0
_FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> bool:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
    else:
        _FAIL += 1
    print("  [%s] %-48s %s" % ("PASS" if cond else "FAIL", name, detail))
    return bool(cond)


def main() -> int:
    import infrastructure.audit as A

    sandbox = ROOT / "runs" / "_log_selfcheck"
    try:
        if sandbox.exists():
            shutil.rmtree(sandbox, ignore_errors=True)
    except Exception:
        pass
    sandbox.mkdir(parents=True, exist_ok=True)

    orig_root = A.get_project_root()
    log_file = sandbox / "runs" / "logs" / "operations.log"

    print("=" * 70)
    print("审计日志子系统自检")
    print("=" * 70)
    print("  沙箱:", sandbox)

    # 计数 mkdir 调用
    real_mkdir = Path.mkdir
    calls = {"n": 0}

    def counting_mkdir(self, *a, **k):
        calls["n"] += 1
        return real_mkdir(self, *a, **k)

    Path.mkdir = counting_mkdir
    try:
        A.set_project_root(sandbox)
        calls["n"] = 0

        # ① 目录只建一次
        for i in range(50):
            A.audit("selfcheck", "rec %d" % i, module="log_selfcheck")
        check("50 条日志最多只触发 1 次 mkdir (原实现 50 次)", calls["n"] <= 1,
              "mkdir 调用 %d 次" % calls["n"])

        # ② 落盘及时性: 写完立刻能在文件里读到 (行缓冲不能变成"攒着不写")
        A.audit("selfcheck", "落盘探针-UNIQUE-20260911", module="log_selfcheck")
        on_disk = False
        try:
            on_disk = "落盘探针-UNIQUE-20260911" in log_file.read_text(
                encoding="utf-8", errors="replace")
        except Exception as e:
            check("日志立即落盘 (无缓冲丢失)", False, repr(e))
        else:
            check("日志立即落盘 (无缓冲丢失)", on_disk)

        # ③ 写入吞吐 (明确只做量级判断, 不做精确基准 —— 本机墙钟噪声很大)
        t0 = time.perf_counter()
        for i in range(500):
            A.audit("selfcheck", "perf %d" % i, module="log_selfcheck")
        dt = (time.perf_counter() - t0) * 1000
        check("500 条写入耗时 < 2000ms", dt < 2000, "%.0f ms (%.3f ms/条)" % (dt, dt / 500))

        # ③b 此刻文件里有 550+ 条, 趁文件够大校验"没有半行/坏行" —— 放到删文件测试
        #     之后再查就只剩 1 行了, 那种检查等于没查。
        bad = 0
        total = 0
        try:
            for line in log_file.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                total += 1
                try:
                    json.loads(line)
                except Exception:
                    bad += 1
        except Exception as e:
            check("全部日志行都是合法 JSON", False, repr(e))
        else:
            check("全部日志行都是合法 JSON (大文件下)", bad == 0 and total >= 500,
                  "%d 行, 坏行 %d" % (total, bad))

        # ④ 环形缓冲与写入一致 (不断言条数: recent_operations 可能还会回填磁盘尾部,
        #    这里只验"最新一条能读到", 不假设它的内部实现)
        recent = A.recent_operations(5)
        last_ok = bool(recent) and recent[-1].get("action") == "selfcheck"
        check("recent_operations 能读到最新记录", last_ok,
              "len=%d last_action=%s" % (len(recent),
                                         recent[-1].get("action") if recent else None))

        # ⑤ 轮转必须真的发生 (常量调小后灌够量)
        try:
            A._close_fh()
        except Exception:
            pass
        old_max = A._MAX_BYTES
        A._MAX_BYTES = 2000            # 极小阈值, 逼出轮转
        A._rotate_counter = 0
        try:
            for i in range(400):
                A.audit("selfcheck", "rotate-%04d-%s" % (i, "x" * 40), module="log_selfcheck")
            rotated = (log_file.parent / "operations.1.log").exists()
            check("日志轮转真的发生 (开着常驻句柄也不失效)", rotated,
                  "operations.1.log exists=%s" % rotated)
            # 轮转后必须还能继续写 (句柄要在新文件上重建)
            A.audit("selfcheck", "轮转后继续写-UNIQUE-AFTER-ROTATE", module="log_selfcheck")
            again = "轮转后继续写-UNIQUE-AFTER-ROTATE" in log_file.read_text(
                encoding="utf-8", errors="replace")
            check("轮转后写入仍然落盘 (句柄已重建)", again)
        finally:
            A._MAX_BYTES = old_max
            A._rotate_counter = 0

        # ⑥ 句柄坏掉能自愈: 直接删掉底层文件
        try:
            A._close_fh()
            if log_file.exists():
                log_file.unlink()
            A.audit("selfcheck", "删文件后继续写-UNIQUE-RECOVER", module="log_selfcheck")
            recovered = log_file.exists() and (
                "删文件后继续写-UNIQUE-RECOVER" in log_file.read_text(
                    encoding="utf-8", errors="replace"))
            check("底层文件被删后仍能自愈重建", recovered)
        except Exception as e:
            check("底层文件被删后仍能自愈重建", False, repr(e))
    finally:
        Path.mkdir = real_mkdir
        try:
            A._close_fh()
        except Exception:
            pass
        try:
            A.set_project_root(orig_root)
        except Exception:
            pass

    print("\n" + "=" * 70)
    print("RESULT: %d passed, %d failed" % (_PASS, _FAIL))
    print("=" * 70)
    if _FAIL == 0:
        try:
            shutil.rmtree(sandbox, ignore_errors=True)     # 全过 → 清掉测试痕迹
        except Exception:
            pass
    else:
        print("  失败 → 保留沙箱供排查: %s" % sandbox)
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
