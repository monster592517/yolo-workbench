#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""merge_fall_datasets.py — 把多份「摔倒检测」数据集合并成一份。

## 为什么需要它（不是"顺手加戏"，是"不合并就必然半个瞎模型"）

工作台里同时存在多条数据来源，它们的**类覆盖面不同**：

  - `fall_synth_v1`      —— `tools/synth_fall_dataset.py` 程序合成。实测 person 935 框 / fall 897 框。
  - `fall_h3_batch_main` —— H3 工厂 GUI 导出，labelme 标注的真实域素材。实测 **fall 738 框 / person 0 框**。

两者的类定义（`["person", "fall"]`）一致，但 h3 那份**只有 fall 类**。
单独用它训练会踩一个看不见的坑：`nc=2` 而 `person` 样本数为 0 ⇒ 该类的分类头
永远拿不到梯度 ⇒ 评估时 person 的 mAP 恒为 0。**看上去像"训练坏了"，其实是"数据里没有"**
—— 这正是本项目最忌讳的「静默故障」形态。

合并后同时保留**两类**与**两个域**（合成 / 真实）。

## 用法

    $PY tools/merge_fall_datasets.py --out fall_all_v1 \
        --src synth=data/datasets/fall_synth_v1 \
        --src h3=data/datasets/fall_h3_batch_main

## 产出

    data/datasets/<out>/{train,valid,test}/{images,labels}
    data/datasets/<out>/data.yaml          # train/val/test 一律带 /images 后缀
    data/datasets/<out>/merge_report.json  # 各来源贡献数、撞名、跳过项
    runs/logs/merge_fall_datasets_<ts>.log

## 退出码

    0  合并成功且自检全过
    2  输入不合法（data.yaml 缺失 / 类定义不一致 / 目录结构不符）
    3  撞名（不静默覆盖 —— 撞名即故障，必须改前缀重跑）
    4  输出自检未过（图数对不上 / 配对不完整 / 有坏图）
    5  输出目录已存在（本工具不删东西；先自行处理再重跑）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _toolkit import ROOT, Tee          # noqa: E402

SPLITS = ("train", "valid", "test")
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


# ──────────────────────────── 读取与校验 ────────────────────────────

def read_yaml(p: Path) -> dict:
    import yaml
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def collect(src_name: str, dsroot: Path) -> tuple[dict, list[str], dict]:
    """收集一份数据集的配对。

    返回 ({(split, stem): (img, lbl)}, 问题清单, 统计)。
    结构约定：`<dsroot>/<split>/{images,labels}` —— 与 data.yaml 里
    `train:` 字段怎么写无关（那个字段是给 ultralytics 看的，本工具自己按目录走）。
    """
    problems: list[str] = []
    stat = {"prefix": src_name, "root": str(dsroot), "splits": {}, "skipped_nonimage": 0,
            "labels_missing": 0, "images_orphan": 0}

    yml = dsroot / "data.yaml"
    if not yml.is_file():
        problems.append("%s: 缺 data.yaml" % dsroot)
        return {}, problems, stat
    cfg = read_yaml(yml)
    names = cfg.get("names")
    if isinstance(names, list):
        stat["names"] = names

    pairs: dict[tuple[str, str], tuple[Path, Path]] = {}
    for sp in SPLITS:
        img_dir = dsroot / sp / "images"
        lbl_dir = dsroot / sp / "labels"
        if not img_dir.is_dir():
            problems.append("%s: 缺目录 %s" % (src_name, img_dir))
            continue
        n_img = n_lbl = 0
        img_stems: dict[str, Path] = {}
        for f in sorted(os.listdir(img_dir)):
            p = img_dir / f
            if not p.is_file():
                continue
            if p.suffix.lower() in IMG_EXT:
                img_stems[p.stem] = p
            else:
                stat["skipped_nonimage"] += 1
        lbl_stems = set()
        if lbl_dir.is_dir():
            for f in sorted(os.listdir(lbl_dir)):
                p = lbl_dir / f
                if p.is_file() and p.suffix.lower() == ".txt":
                    lbl_stems.add(p.stem)
        for stem, img in img_stems.items():
            if stem in lbl_stems:
                pairs[(sp, stem)] = (img, lbl_dir / (stem + ".txt"))
                n_img += 1
                n_lbl += 1
            else:
                stat["labels_missing"] += 1
        orphan = lbl_stems - set(img_stems)
        if orphan:
            stat["images_orphan"] += len(orphan)
            problems.append("%s/%s: %d 个 label 没有对应图片" % (src_name, sp, len(orphan)))
        stat["splits"][sp] = n_img
    return pairs, problems, stat


def class_hist(pairs: dict) -> dict:
    """按类数框 —— 合并前后各报一次，用于证明"person 不再是 0"。"""
    hist: dict[str, dict[str, int]] = {}
    for (sp, _), (_img, lbl) in pairs.items():
        if not lbl.is_file():
            continue
        for line in lbl.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 5:
                hist.setdefault(sp, {})
                k = parts[0]
                hist[sp][k] = hist[sp].get(k, 0) + 1
    return hist


# ──────────────────────────── 写入 ────────────────────────────

def place(src: Path, dst: Path) -> str:
    """硬链接优先（同盘瞬时、零额外占用），失败退回复制。

    为什么可以硬链接：本工具**只读**源数据，从不改写；两个目录项指向同一 inode
    不会造成隐性耦合。万一源数据集被搬走，目标仍完好（硬链接是独立的目录项）。
    """
    try:
        os.link(src, dst)
        return "link"
    except OSError:
        shutil.copy2(src, dst)
        return "copy"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="合并多份摔倒检测数据集")
    ap.add_argument("--out", required=True, help="输出数据集名（落在 data/datasets/ 下）")
    ap.add_argument("--src", action="append", default=[], required=True,
                    metavar="NAME=REL_PATH", help="来源，可多次；例 h3=data/datasets/fall_h3_batch_main")
    ap.add_argument("--no-link", action="store_true", help="强制复制，不用硬链接")
    a = ap.parse_args(argv)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = ROOT / "runs" / "logs" / ("merge_fall_datasets_%s.log" % ts)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    tee = Tee(log_path)
    sys.stdout = tee
    sys.stderr = tee
    try:
        return _run(a, ts, log_path)
    finally:
        sys.stdout = tee._out
        sys.stderr = tee._out
        tee.close()


def _run(a, ts, log_path) -> int:
    print("=" * 78)
    print("合并摔倒检测数据集")
    print("=" * 78)
    print("  输出      : data/datasets/%s" % a.out)
    print("  日志      : %s" % log_path)
    print("=" * 78)

    out_root = ROOT / "data" / "datasets" / a.out
    if out_root.exists():
        print("[FAIL] 输出目录已存在: %s" % out_root)
        print("       本工具不删东西。请先把它移入隔离区，再重跑。")
        return 5

    # ── ① 收集所有来源 ──
    all_pairs: dict[tuple[str, str], tuple[Path, Path]] = {}
    stats: list[dict] = []
    problems: list[str] = []
    names_ref: list | None = None

    for spec in a.src:
        if "=" not in spec:
            print("[FAIL] --src 格式应为 NAME=REL_PATH，收到: %r" % spec)
            return 2
        name, rel = spec.split("=", 1)
        dsroot = Path(rel) if Path(rel).is_absolute() else (ROOT / rel)
        if not dsroot.is_dir():
            print("[FAIL] 来源目录不存在: %s" % dsroot)
            return 2
        pairs, probs, st = collect(name, dsroot)
        problems.extend(probs)

        # 类定义一致性 —— 不一致就意味着标签语义对不上，必须拦
        nm = st.get("names")
        if nm is not None:
            if names_ref is None:
                names_ref = nm
            elif list(nm) != list(names_ref):
                print("[FAIL] 类定义不一致: %s 是 %r，而基准是 %r" % (name, nm, names_ref))
                return 2

        n_before = len(all_pairs)
        for (sp, stem), (img, lbl) in pairs.items():
            key = (sp, "%s_%s" % (name, stem))
            if key in all_pairs:
                print("[FAIL] 撞名: %s —— 请给来源换一个 --src 名（它同时是文件名前缀）" % (key,))
                return 3
            all_pairs[key] = (img, lbl)
        st["contributed"] = len(all_pairs) - n_before
        stats.append(st)

        print("  [%s] %s" % (name, dsroot))
        for sp in SPLITS:
            print("      %-6s %5d 张" % (sp, st["splits"].get(sp, 0)))
        if st["skipped_nonimage"]:
            print("      （跳过非图片后缀文件 %d 个 —— labelme .json 之类的标注源）"
                  % st["skipped_nonimage"])
        if st["labels_missing"]:
            print("      ⚠ 有图无签 %d 个（已丢弃，不参与训练）" % st["labels_missing"])

    if problems:
        for p in problems:
            print("  ⚠ %s" % p)
    if not all_pairs:
        print("[FAIL] 一个可用样本都没有")
        return 2

    # ── ② 落盘 ──
    print("-" * 78)
    print("  写入 %s" % out_root.relative_to(ROOT))
    n_link = n_copy = 0
    for sp in SPLITS:
        (out_root / sp / "images").mkdir(parents=True, exist_ok=True)
        (out_root / sp / "labels").mkdir(parents=True, exist_ok=True)
    for (sp, stem), (img, lbl) in sorted(all_pairs.items()):
        d_img = out_root / sp / "images" / (stem + img.suffix.lower())
        d_lbl = out_root / sp / "labels" / (stem + ".txt")
        if d_img.exists() or d_lbl.exists():
            print("[FAIL] 目标已存在（不该发生，说明键没唯一化）: %s" % d_img.name)
            return 3
        for s, d in ((img, d_img), (lbl, d_lbl)):
            if a.no_link:
                shutil.copy2(s, d)
                n_copy += 1
            elif place(s, d) == "link":
                n_link += 1
            else:
                n_copy += 1
    print("  硬链接 %d 个 / 复制 %d 个" % (n_link, n_copy))

    # ── ③ 写 data.yaml（口径铁律：一律带 /images）──
    yml_out = out_root / "data.yaml"
    names = names_ref or ["person", "fall"]
    yml_out.write_text(
        "path: %s\n" % str(out_root).replace("\\", "/")
        + "train: train/images\n"
        + "val: valid/images\n"
        + "test: test/images\n"
        + "nc: %d\n" % len(names)
        + "names:\n" + "".join("- %s\n" % x for x in names),
        encoding="utf-8", newline="\n")
    print("  写出 %s" % yml_out.relative_to(ROOT))

    # ── ④ 自检：回读磁盘，不信内存里的账 ──
    print("-" * 78)
    print("自检（回读磁盘）")
    rc = 0
    verify_pairs, vprobs, vstat = collect("merged", out_root)
    for sp in SPLITS:
        exp = sum(1 for (s, _) in all_pairs if s == sp)
        got = vstat["splits"].get(sp, 0)
        flag = "OK " if exp == got else "FAIL"
        if exp != got:
            rc = 4
        print("  [%s] %-6s 期望 %5d / 实际 %5d" % (flag, sp, exp, got))
    if vstat["labels_missing"]:
        print("  [FAIL] 合并后有图无签 %d 个" % vstat["labels_missing"])
        rc = 4
    if vstat["images_orphan"]:
        print("  [FAIL] 合并后有签无图 %d 个" % vstat["images_orphan"])
        rc = 4
    for p in vprobs:
        print("  ⚠ %s" % p)

    # 类直方图：合并的**意义**就体现在这里（person 从 0 变成非 0）
    hist = class_hist(verify_pairs)
    print("  类分布（合并后）:")
    for sp in SPLITS:
        h = hist.get(sp, {})
        pretty = ", ".join("%s:%d" % (names[int(k)] if k.isdigit() and int(k) < len(names) else k, v)
                           for k, v in sorted(h.items()))
        print("      %-6s %s" % (sp, pretty or "(空)"))

    # 抽样读图，确认不是坏图（截断的 JPEG 会让训练在中途才炸）
    try:
        from PIL import Image
        bad = []
        sample = sorted(verify_pairs.items())[:: max(1, len(verify_pairs) // 40)][:40]
        for _k, (img, _l) in sample:
            try:
                with Image.open(img) as im:
                    im.verify()
            except Exception as e:                                  # noqa: BLE001
                bad.append("%s: %r" % (img.name, e))
        if bad:
            print("  [FAIL] 抽样 %d 张里发现 %d 张坏图: %s" % (len(sample), len(bad), bad[:3]))
            rc = 4
        else:
            print("  [OK ] 抽样 %d 张图全部可读" % len(sample))
    except ImportError:
        print("  [WARN] 没有 PIL，跳过坏图抽样")

    report = {
        "generated_at": ts, "out_dataset": str(out_root), "names": names,
        "sources": stats, "problems": problems,
        "totals": {sp: vstat["splits"].get(sp, 0) for sp in SPLITS},
        "class_hist": hist,
        "exit_code": rc, "log": str(log_path),
    }
    (out_root / "merge_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")

    print("=" * 78)
    print("  总计: %s" % ", ".join("%s %d" % (sp, vstat["splits"].get(sp, 0)) for sp in SPLITS))
    print("  报告: %s" % (out_root / "merge_report.json").relative_to(ROOT))
    print("  结果: %s" % ("PASS" if rc == 0 else "FAIL(rc=%d)" % rc))
    print("=" * 78)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
