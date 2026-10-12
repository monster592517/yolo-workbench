# -*- coding: utf-8 -*-
"""check_annotations.py — Validate Labelme JSON annotation files."""
import json, os, sys, cv2
from pathlib import Path

def check_annotations(dataset_dir):
    """Check all Labelme JSON files in a directory. Returns report dict."""
    dp = Path(dataset_dir)
    images = list(dp.glob("*.jpg")) + list(dp.glob("*.png"))
    if not images:
        # 自动下探常见图片子目录 (摔倒数据集格式: images/)
        for sub in ("images", "train/images", "valid/images", "val/images"):
            cand = dp / sub
            if cand.exists():
                images = list(cand.glob("*.jpg")) + list(cand.glob("*.png"))
                if images:
                    break
    report = {"total_images": len(images), "missing_json": [], "format_errors": [],
              "empty_annotations": [], "coordinate_errors": [], "imagePath_errors": []}

    for img in images:
        jp = img.with_suffix(".json")
        if not jp.exists():
            report["missing_json"].append(img.name)
            continue

        try:
            data = json.loads(jp.read_text(encoding="utf-8"))
        except Exception as e:
            report["format_errors"].append(f"{img.name}: JSON parse error: {e}")
            continue

        # Required fields
        for key in ["version", "shapes", "imagePath", "imageWidth", "imageHeight"]:
            if key not in data:
                report["format_errors"].append(f"{img.name}: missing field '{key}'")
                break

        # Empty
        shapes = data.get("shapes", [])
        if len(shapes) == 0:
            report["empty_annotations"].append(img.name)

        # Shape-type / coordinate check
        w, h = data.get("imageWidth", 0), data.get("imageHeight", 0)
        for i, s in enumerate(shapes):
            pts = s.get("points", [])
            st = s.get("shape_type", "rectangle")
            if st == "rotation" and len(pts) != 4:
                report["format_errors"].append(
                    f"{img.name}: rotation shape[{i}] requires 4 points, got {len(pts)}")
            for j, pt in enumerate(pts):
                if len(pt) < 2: continue
                x, y = pt[0], pt[1]
                if w > 0 and (x < 0 or x > w or y < 0 or y > h):
                    report["coordinate_errors"].append(
                        f"{img.name}: shape[{i}] point[{j}] ({x},{y}) outside {w}x{h}")

        # imagePath check
        imp = data.get("imagePath", "")
        if os.path.isabs(imp) or "\\" in imp or ":" in imp:
            report["imagePath_errors"].append(
                f"{img.name}: imagePath is absolute: {imp} (should be filename only)")

    return report

def print_report(report):
    print("=" * 60)
    print("Labelme Annotation Check — " + str(report["total_images"]) + " images")
    print("=" * 60)
    issues = 0
    for key, label, level in [
        ("missing_json", "Missing JSON files", "WARN"),
        ("format_errors", "JSON format errors", "FAIL"),
        ("empty_annotations", "Empty annotations (no shapes)", "WARN"),
        ("coordinate_errors", "Coordinate out of bounds", "FAIL"),
        ("imagePath_errors", "imagePath is absolute path", "FAIL"),
    ]:
        items = report[key]
        if items:
            issues += len(items)
            print()
            print("[" + level + "] " + label + ": " + str(len(items)))
            for item in items[:5]:
                print("  - " + str(item))
            if len(items) > 5:
                print("  ... and " + str(len(items)-5) + " more")
    print()
    print("=" * 60)
    if issues == 0:
        print("All good! 0 issues found.")
    else:
        print("Total: " + str(issues) + " issues.")
        print()
        print("Fix suggestions:")
        if report["missing_json"]: print("  -> Run AI auto-annotation to generate missing JSON files")
        if report["format_errors"]: print("  -> Re-run AI annotation with latest annotator.py")
        if report["imagePath_errors"]: print("  -> Re-run AI annotation (latest annotator.py fixes this)")
        if report["coordinate_errors"]: print("  -> Check model confidence threshold (too many false positives?)")
    print("=" * 60)
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/check_annotations.py <dataset_images_dir>")
        sys.exit(1)
    r = check_annotations(sys.argv[1])
    print_report(r)
