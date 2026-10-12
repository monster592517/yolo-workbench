# -*- coding: utf-8 -*-
"""convert_obb.py — Convert Labelme JSON (rotation/rectangle) to YOLO OBB txt.

Usage:
    python scripts/convert_obb.py --data <images_dir> [--classes <classes.txt>] [--out <labels_dir>]

Output: <labels_dir>/*.txt, each line: cls x1 y1 x2 y2 x3 y3 x4 y4 (normalized)
Rectangles are emitted as clockwise 4-point boxes (DOTA convention).
"""
import argparse
import json
import sys
from pathlib import Path

IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
SHAPE_TYPES = {"rotation", "rectangle"}


def load_class_map(classes_path: Path, shapes_labels):
    """Return {label_name: class_id} using classes.txt (one name per line)."""
    if classes_path and classes_path.exists():
        names = [ln.strip() for ln in classes_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        return {name: i for i, name in enumerate(names)}
    # Fallback: order of first appearance (warned)
    ordered = []
    for lbl in shapes_labels:
        if lbl not in ordered:
            ordered.append(lbl)
    return {name: i for i, name in enumerate(ordered)}


def rect_to_quad(x1, y1, x2, y2):
    return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]


def convert_dir(img_dir: Path, classes_path: Path, out_dir: Path) -> dict:
    jsons = sorted(img_dir.glob("*.json"))
    stats = {"json": len(jsons), "shapes": 0, "skipped": 0, "files": 0}
    shapes_labels = []
    for jp in jsons:
        try:
            data = json.loads(jp.read_text(encoding="utf-8"))
            shapes_labels.extend(s.get("label", "") for s in data.get("shapes", []))
        except Exception:
            continue
    class_map = load_class_map(classes_path, shapes_labels)

    out_dir.mkdir(parents=True, exist_ok=True)
    for jp in jsons:
        try:
            data = json.loads(jp.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[FAIL] {jp.name}: {e}", file=sys.stderr)
            stats["skipped"] += 1
            continue
        w = data.get("imageWidth") or 0
        h = data.get("imageHeight") or 0
        if not w or not h:
            print(f"[FAIL] {jp.name}: missing imageWidth/imageHeight", file=sys.stderr)
            stats["skipped"] += 1
            continue

        lines = []
        for s in data.get("shapes", []):
            st = s.get("shape_type", "rectangle")
            label = s.get("label", "")
            pts = s.get("points", [])
            if st not in SHAPE_TYPES:
                continue
            if label not in class_map:
                class_map[label] = len(class_map)  # unknown label -> append id
            cid = class_map[label]
            quad = None
            if st == "rotation":
                if len(pts) != 4:
                    stats["skipped"] += 1
                    continue
                quad = [(float(px), float(py)) for px, py in pts]
            else:
                if len(pts) < 2:
                    stats["skipped"] += 1
                    continue
                x1, y1 = pts[0]; x2, y2 = pts[1]
                quad = rect_to_quad(x1, y1, x2, y2)
            norm = []
            for px, py in quad:
                norm.append(max(0.0, min(1.0, px / w)))
                norm.append(max(0.0, min(1.0, py / h)))
            lines.append(f"{cid} " + " ".join(f"{v:.6f}" for v in norm))
            stats["shapes"] += 1

        (out_dir / f"{jp.stem}.txt").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        stats["files"] += 1
    return stats


def main():
    ap = argparse.ArgumentParser(description="Labelme rotation/rectangle JSON -> YOLO OBB txt")
    ap.add_argument("--data", required=True, help="images directory containing *.json labels")
    ap.add_argument("--classes", default=None, help="classes.txt path (one class name per line)")
    ap.add_argument("--out", default=None, help="output labels directory (default: <parent>/labels)")
    args = ap.parse_args()

    img_dir = Path(args.data)
    if not img_dir.is_dir():
        print(f"[FAIL] data dir not found: {img_dir}", file=sys.stderr)
        sys.exit(1)
    classes_path = Path(args.classes) if args.classes else None
    out_dir = Path(args.out) if args.out else img_dir.parent / "labels"
    stats = convert_dir(img_dir, classes_path, out_dir)
    print(f"json={stats['json']} files={stats['files']} shapes={stats['shapes']} skipped={stats['skipped']} -> {out_dir}")


if __name__ == "__main__":
    main()
