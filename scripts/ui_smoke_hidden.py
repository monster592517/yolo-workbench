# -*- coding: utf-8 -*-
"""隐藏模式全量页面冒烟 (v2 重设计验收用): 构建主窗口 + 逐页 show_page, 全部成功后退出.

python scripts/ui_smoke_hidden.py
"""
from __future__ import annotations
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    from gui.main_window import App
    app = App(visible=False)
    app.root.update_idletasks()
    built = []
    try:
        # 强制构建并切换全部页面 (预建队列分片之外)
        for key in list(app._PAGE_SPEC):
            try:
                app.show_page(key, animate=False)
                app.root.update_idletasks()
                app.root.update()
                built.append(key)
            except Exception as e:
                print(f"PAGE FAIL {key}: {e}")
                traceback.print_exc()
                return 1
    finally:
        try:
            app.root.after(200, app.root.destroy)
            app.root.mainloop()
        except Exception:
            pass
    # ★★ 2026-10-03 修 (假绿), 第二版 —— 第一版判据也是假接线, 记录在此:
    #
    #   第一版写的是 `app._pages.get(k) is None`, 反向注入后**照样报 SMOKE OK**。
    #   原因: `App.show_page` 在 `_ensure_page` 抛异常时, 会用
    #   `self._pages[key] = self._page_fallback(key, msg)` 塞一个**⚠️ 占位 Frame**
    #   进字典 ⇒ `_pages[key]` 永远非 None, 判据恒真。
    #
    #   真信号 = **这页的实例类型对不对**。占位页是 `tk.Frame`;
    #   真页面是 `_PAGE_SPEC[key][1]` 指的类。类型不符 ⇒ 构建失败。
    #   (不依赖日志文案, 也不依赖异常是否被吞 —— 只看最终产物。)
    bad = []
    for k, (_mod, cls_name) in app._PAGE_SPEC.items():
        obj = app._pages.get(k)
        if obj is None:
            bad.append(f"{k}(未建)")
            continue
        if type(obj).__name__ != cls_name:
            bad.append(f"{k}(实际 {type(obj).__name__}, 期望 {cls_name})")
    if bad:
        print(f"SMOKE FAIL: {len(bad)} 页未正确建成 -> {', '.join(bad)}")
        print("  ⚠ 上面若有 'page build <key> failed' 那一行, 就是被 show_page 吞掉的真实异常")
        return 1
    print(f"SMOKE OK: {len(built)} pages built -> {', '.join(built)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
