# -*- coding: utf-8 -*-
"""拆分后静态核对: 每页引用的 self.X 是否都在本类里定义/赋值过。

拆分最容易出的故障是"搬走了一个方法, 但它引用的属性/方法留在原页" ——
这种错编译不报, 只在用户点到那个按钮时才 AttributeError。
这里用 AST 把每个类里的 self.X 读与写都收齐, 报告只读不写的名字。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path


def check(path: str) -> int:
    src = Path(path).read_text(encoding="utf-8")
    tree = ast.parse(src)
    # 目录通配符会扫到 __init__.py 之类没有类的模块 —— 直接 next() 会
    # StopIteration 崩掉, 而且把前面已核对页面的结果一起丢掉。跳过并报告。
    cls = next((n for n in tree.body if isinstance(n, ast.ClassDef)), None)
    if cls is None:
        print(f"=== {path}\n    [skip] 文件内没有类定义")
        return 0

    defined = set()
    for fn in cls.body:
        if isinstance(fn, ast.FunctionDef):
            defined.add(fn.name)
        elif isinstance(fn, ast.Assign):
            for t in fn.targets:
                if isinstance(t, ast.Name):
                    defined.add(t.id)

    writes, reads = set(), set()
    for node in ast.walk(cls):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                and node.value.id == "self":
            is_write = isinstance(node.ctx, (ast.Store, ast.Del))
            (writes if is_write else reads).add(node.attr)
        # self._x: tk.StringVar(...) 也走 Assign -> Attribute(Store)
        if isinstance(node, ast.AugAssign):
            t = node.target
            if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) \
                    and t.value.id == "self":
                writes.add(t.attr)

    missing = sorted(a for a in (reads - writes - defined) if a.startswith("_"))
    print(f"=== {path}")
    print(f"    方法 {len(defined)} 个 | 写入属性 {len(writes)} | 读取属性 {len(reads)}")
    if missing:
        print(f"    [!!] 只读未写 ({len(missing)}): {' '.join(missing)}")
    else:
        print("    [OK] 无只读未写的 self._x")
    return len(missing)


if __name__ == "__main__":
    targets = []
    for a in sys.argv[1:]:
        pa = Path(a)
        if pa.is_dir():
            targets += sorted(pa.glob("*.py"))
        else:
            targets.append(pa)
    bad = 0
    for p in targets:
        try:
            bad += check(str(p))
        except Exception as ex:
            print(f"=== {p}\n    [ERR] {type(ex).__name__}: {ex}")
            bad += 1
    print("TOTAL MISSING:", bad)
    sys.exit(1 if bad else 0)
