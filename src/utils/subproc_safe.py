# -*- coding: utf-8 -*-
"""subproc_safe.py — 子进程文本捕获的容错解码（全局一次性修补）。

问题（2026-09-09 定位）：项目统一用 `python -X utf8` 运行，UTF-8 模式会把
`subprocess.run(..., text=True)` 的默认解码器也变成 UTF-8；而中文 Windows 上
`py -0p` / `pip` / `tasklist` / `wmic` 等子进程会输出 GBK 字节，于是 subprocess
的 reader 线程抛 `UnicodeDecodeError`（表现为功能偶发失败、退出码 -1073741819
或结果为空）。

做法：import 本模块即调用 install()，给 `subprocess.run` / `subprocess.Popen`
补上 `errors="replace"`（仅当调用方要求文本模式且没显式指定 errors 时）。
不影响字节模式调用，不改变已显式声明 errors 的调用。
"""
from __future__ import annotations

import subprocess as _sp

_installed = False


def _patch_kw(kw: dict) -> dict:
    if kw.get("text") or kw.get("universal_newlines") or kw.get("encoding"):
        kw.setdefault("errors", "replace")
    return kw


def install() -> None:
    """幂等安装；重复调用无副作用。"""
    global _installed
    if _installed:
        return
    _orig_run = _sp.run
    _orig_popen_init = _sp.Popen.__init__

    def _run(*args, **kw):
        return _orig_run(*args, **_patch_kw(kw))

    def _popen_init(self, *args, **kw):
        return _orig_popen_init(self, *args, **_patch_kw(kw))

    _sp.run = _run          # type: ignore[assignment]
    _sp.Popen.__init__ = _popen_init  # type: ignore[method-assign]
    _installed = True
