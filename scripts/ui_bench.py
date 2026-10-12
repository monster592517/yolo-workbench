# -*- coding: utf-8 -*-
"""切页性能基准: 首次构建耗时 + 缓存后切换耗时 (3 轮)."""
from __future__ import annotations
import sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def main():
    from gui.main_window import App
    app = App(visible=False)
    keys = list(app._PAGE_SPEC)
    built = {}
    for k in keys:
        t0 = time.perf_counter()
        try:
            app.show_page(k, animate=False)
            app.root.update_idletasks()
        except Exception as e:
            print(f"BUILD FAIL {k}: {e}")
            continue
        built[k] = (time.perf_counter() - t0) * 1000
    print("--- first build (ms) ---")
    for k, ms in built.items():
        print(f"  {k:<12} {ms:7.1f}")
    print("--- cached switch x3 (ms) ---")
    for r in range(3):
        row = []
        for k in keys:
            t0 = time.perf_counter()
            app.show_page(k, animate=False)
            row.append((time.perf_counter() - t0) * 1000)
        print(f"  round{r + 1}: " + " ".join(f"{k[:5]}={m:5.1f}" for k, m in zip(keys, row)))
    app.root.destroy()
    print("BENCH DONE")


if __name__ == "__main__":
    main()
