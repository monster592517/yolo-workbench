# -*- coding: utf-8 -*-
"""Infrastructure layer"""

# 全局修补: 子进程文本捕获容错解码 (中文 Windows 的 GBK 输出不再崩 reader 线程)
try:
    from utils import subproc_safe as _subproc_safe

    _subproc_safe.install()
except Exception:  # 极端情况下不影响主流程
    pass

