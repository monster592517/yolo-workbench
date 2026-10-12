# -*- coding: utf-8 -*-
"""数据集清理与规范化工具 (摔倒/跌倒等图片文件夹通用)。

用法:
    python scripts/clean_dataset.py "D:\\摔倒" [--classes person,fall]

作用:
    1) 备份并删除文件夹内全部标注文件 (labelme json / voc xml / yolo txt);
    2) 若已有 train/valid/test 则保留, 否则按 80/10/10 移入三分结构;
    3) 生成 classes.txt / images/classes.txt / data.yaml (相对路径, 项目迁移不坏)。

备份目录: runs/_dataset_backup/<数据集名>_annotations_<时间戳>/
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="清理标注并规范化为 YOLO 三分数据集 (同跌倒数据集)")
    ap.add_argument("path", help="图片/数据集文件夹路径")
    ap.add_argument("--classes", default="", help="类别列表, 逗号分隔 (默认自动读取或 person,fall)")
    args = ap.parse_args()
    folder = Path(args.path)
    if not folder.is_dir():
        print(f"[错误] 文件夹不存在: {folder}")
        return 1
    classes = [c.strip() for c in args.classes.split(",") if c.strip()] if args.classes else None

    def _log(msg, tag="info"):
        prefix = "  " if tag == "info" else ("✔ " if tag == "ok" else "⚠ ")
        print(prefix + str(msg))

    from core.trainer import normalize_dataset_in_place
    dest = normalize_dataset_in_place(folder, classes=classes, log_cb=_log,
                                      progress_cb=lambda pct, txt: print(f"  [{pct:3d}%] {txt}"))
    print(f"完成: {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
