# -*- coding: utf-8 -*-
"""ui_launch_probe.py — 真实入口冒烟 (2026-09-11)

跑真正的启动脚本 `YOLO工作台.py`, 等 N 秒, 判断进程是否活着 (说明主循环已经跑起来、
没被启动阶段的异常打断), 然后把它结束掉。用来确认启动链路改动没有把程序改挂。

    python scripts/ui_launch_probe.py [--seconds 30]
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENTRY = ROOT / "YOLO工作台.py"


def main() -> int:
    secs = 30
    if "--seconds" in sys.argv:
        try:
            secs = int(sys.argv[sys.argv.index("--seconds") + 1])
        except Exception:
            pass

    env = dict(os.environ)
    env["YOLO_SKIP_VERIFY"] = "1"
    env["OPENCV_LOG_LEVEL"] = "OFF"
    env["PYTHONUNBUFFERED"] = "1"

    # 直接把子进程输出写到文件, 不走管道 —— 管道 + 长时间不通话会让 GUI 进程
    # 在某些 Windows 会话下收到控制台事件而关闭, 那是测量方式的问题, 不是程序的问题。
    log = ROOT / "runs" / "launch_probe.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    print("launching %s ... (log: %s)" % (ENTRY.name, log))
    t0 = time.perf_counter()
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        p = subprocess.Popen([sys.executable, str(ENTRY)], cwd=str(ROOT), env=env,
                             stdout=fh, stderr=subprocess.STDOUT)
        # 分片轮询, 能区分"自己退了"和"一直在跑"
        while time.perf_counter() - t0 < secs:
            if p.poll() is not None:
                break
            time.sleep(0.5)

    alive = p.poll() is None
    ran = time.perf_counter() - t0
    if alive:
        print("进程存活 %.1f 秒 -> 主循环在跑, 启动链路 OK" % ran)
        try:
            p.terminate()
            try:
                p.wait(timeout=10)
            except Exception:
                p.kill()
        except Exception:
            pass
    else:
        print("!! 进程在 %.1f 秒内自行退出, returncode=%s" % (ran, p.returncode))

    text = ""
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except Exception:
        pass
    bad = [ln for ln in text.splitlines()
           if "Traceback" in ln or "invalid command name" in ln
           or "TclError" in ln or "AttributeError" in ln or "TypeError" in ln]
    if bad:
        print("日志里的报错行 (%d):" % len(bad))
        for ln in bad[:30]:
            print("   ", ln)
    print("LAUNCH_PROBE: %s" % ("PASS" if alive else "FAIL"))
    return 0 if alive else 1


if __name__ == "__main__":
    sys.exit(main())
