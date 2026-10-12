# -*- coding: utf-8 -*-
"""YOLO 标注内容级质检 —— 检查 label/*.txt 里**内容**对不对。

为什么单独一层（与 `dataset_profiler` 三分工，别混）：

  ① 目录结构层  `check_dataset_format`       train/valid/test + data.yaml 在不在
  ② 图片文件层  `verify_dataset_images`      图片能不能打开（PIL verify）
  ③ 标注内容层  `audit_labels`（本模块）      txt 里的数字对不对   ← 社区最高频的坑

前两层只证明「文件在那儿、也没坏」，**证明不了「标注内容正确」**。
公开的踩坑记录与一批专用小工具的检查项都指向第 ③ 层：除了显存不够，
「训练跑不起来 / mAP 一直是 0 / loss 变 NaN」的头号成因就是标注内容坏掉 ——
空标签、字段数不对、坐标没归一化、类别 ID 越界、图像与标签张冠李戴。

⚠️ 一条最容易做错的判据：**空标签文件在 YOLO 里是合法的**（表示"本图无目标"，
常作背景图用；ultralytics 见到空 txt 不会报错）。把它一律当错误 = 满屏误报，
会训练人忽略自检。⇒ 本模块只在「整个 split 全空」时才提级报警，
那才是真正的信号：**labels 目录配错、或标签压根没生成**。

⚠️ 本模块只读不写。它不修数据 —— 修复动作由 `trainer.normalize_dataset_in_place`
等既有链路负责，别在这里再造一套。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}

#: 只审这三种 split 目录；raw / compare 本来就可能没标签，不参与
_AUDIT_KINDS = ("train", "valid", "test", "root")

# ── 级别归属 ────────────────────────────────────────────────────────────────
# 这三张表是判据的一部分（改前先读 tools/qc/SMOKES.md 的四条纪律）：
#   fatal = 会直接导致训练失败 / 静默零标签，训练前必须拦
#   warn  = 会拖低 mAP 或有歧义，报出来让人复核
#   info  = 合法但值得知道（空标签 = 背景图）
FATAL_KINDS = ("field_count", "non_numeric", "cls_negative", "cls_oorrange")
WARN_KINDS = ("coord_range", "zero_area", "unpaired_img", "unpaired_lbl")
INFO_KINDS = ("empty_lbl",)

#: 「有图无标」占比超过此值 ⇒ 提级为致命（多半是 labels 目录配错/没生成）
UNPAIRED_FATAL_RATIO = 0.5

KIND_ZH: Dict[str, str] = {
    "field_count":   "字段数不是合法形态",
    "non_numeric":   "含非数字",
    "coord_range":   "坐标不在 [0,1]",
    "zero_area":     "框宽或高 <= 0",
    "cls_negative":  "类别 ID 为负",
    "cls_oorrange":  "类别 ID 越界",
    "unpaired_img":  "有图无标",
    "unpaired_lbl":  "有标无图",
    "empty_lbl":     "空标签(背景图, 合法)",
}


@dataclass
class Issue:
    """一条问题。`line=0` 表示文件级（不是某一行出错）。"""
    kind: str
    path: str
    line: int
    detail: str

    def as_row(self) -> List[str]:
        return [self.kind, KIND_ZH.get(self.kind, self.kind), self.path,
                str(self.line), self.detail]


@dataclass
class SplitAudit:
    name: str
    images_dir: str
    labels_dir: str
    n_images: int = 0
    n_labels: int = 0
    n_scanned: int = 0
    counts: Dict[str, int] = field(default_factory=dict)
    issues: List[Issue] = field(default_factory=list)
    truncated: bool = False

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def fatal_count(self) -> int:
        n = sum(v for k, v in self.counts.items() if k in FATAL_KINDS)
        # 提级 ①：整个 split 的标签全空 ⇒ 路径配错 / 标签没生成
        if self.n_labels and self.counts.get("empty_lbl", 0) == self.n_labels:
            n += 1
        # 提级 ②：有图无标过半 ⇒ 标签目录与图片目录对不上
        if self.n_images and self.counts.get("unpaired_img", 0) > self.n_images * UNPAIRED_FATAL_RATIO:
            n += 1
        return n

    @property
    def warn_count(self) -> int:
        return sum(v for k, v in self.counts.items() if k in WARN_KINDS)

    @property
    def ok(self) -> bool:
        return self.fatal_count == 0


@dataclass
class DatasetAudit:
    name: str
    path: str
    nc: Optional[int] = None
    splits: List[SplitAudit] = field(default_factory=list)
    note: str = ""

    @property
    def counts(self) -> Dict[str, int]:
        agg: Dict[str, int] = {}
        for s in self.splits:
            for k, v in s.counts.items():
                agg[k] = agg.get(k, 0) + v
        return agg

    @property
    def fatal_count(self) -> int:
        return sum(s.fatal_count for s in self.splits)

    @property
    def ok(self) -> bool:
        return self.fatal_count == 0


# ── 解析 ────────────────────────────────────────────────────────────────────

def classify_shape(n: int) -> Optional[str]:
    """按字段数判定标签形态；非法返回 None。

    YOLO / ultralytics 的三种合法行形态：
      det : cls cx cy w h                -> 5
      seg : cls x1 y1 x2 y2 ...          -> 1 + 2k   (k >= 3)
      pose: cls cx cy w h px py v ...    -> 5 + 3K   (K >= 1)

    n=5 优先判 det（seg 最少要 3 个点 = 7 个字段，不会撞上）。
    n=11 这种两可的情况按 pose 判（因为 pose 有 v，坐标校验时要跳过它）。
    """
    if n == 5:
        return "det"
    if n >= 8 and (n - 5) % 3 == 0:
        return "pose"
    if n >= 7 and (n - 1) % 2 == 0:
        return "seg"
    return None


def _coords_of(shape: str, vals: Sequence[float]) -> List[float]:
    """取出一条标签里**应当落在 [0,1] 的坐标值**（按形态跳过非坐标位）。"""
    if shape == "det":
        return list(vals[1:5])
    if shape == "seg":
        return list(vals[1:])
    if shape == "pose":
        out = list(vals[1:5])          # 框参数
        rest = list(vals[5:])          # K 组 (px, py, v)
        for i in range(0, len(rest) - 2, 3):
            out.extend(rest[i:i + 2])  # 跳过 v（它允许 0/1/2，不在 [0,1] 判据内）
        return out
    return []


def _read_lines(p: Path) -> List[str]:
    try:
        return p.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []


def audit_label_file(txt: Path, nc: Optional[int] = None) -> Tuple[List[Issue], bool]:
    """审一个 label 文件 → (问题列表, 是否空文件)。

    空文件的判定返回给调用方 —— 因为"空"的级别取决于上下文
    （单张空 = 背景图，合法；整 split 全空 = 标签没生成）。
    """
    rel = txt.name
    rows = [(i, ln.strip()) for i, ln in enumerate(_read_lines(txt), 1) if ln.strip()]
    if not rows:
        return [], True

    issues: List[Issue] = []
    for lineno, ln in rows:
        parts = ln.split()
        n = len(parts)
        try:
            vals = [float(x) for x in parts]
        except ValueError:
            issues.append(Issue("non_numeric", rel, lineno, ln[:60]))
            continue

        shape = classify_shape(n)
        if shape is None:
            issues.append(Issue("field_count", rel, lineno, f"{n} 个字段: {ln[:50]}"))
            continue

        cid = vals[0]
        if cid != int(cid):
            issues.append(Issue("non_numeric", rel, lineno, f"类别 ID 非整数: {parts[0]}"))
            continue
        cid = int(cid)
        if cid < 0:
            issues.append(Issue("cls_negative", rel, lineno, str(cid)))
        elif nc is not None and cid >= nc:
            issues.append(Issue("cls_oorrange", rel, lineno, f"类别 {cid} >= nc={nc}"))

        for v in _coords_of(shape, vals):
            if not (0.0 <= v <= 1.0):
                issues.append(Issue("coord_range", rel, lineno, f"{v:g} 不在 [0,1]"))
                break

        if shape == "det" and (vals[3] <= 0 or vals[4] <= 0):
            issues.append(Issue("zero_area", rel, lineno, f"w={vals[3]:g} h={vals[4]:g}"))

    return issues, False


# ── 单 split ────────────────────────────────────────────────────────────────

def audit_split(name: str, images_dir: Path, labels_dir: Optional[Path],
                nc: Optional[int] = None, limit: Optional[int] = None) -> SplitAudit:
    """审一个 split。`limit` 限制**扫描的标签文件数**（内存/耗时友好）。"""
    sa = SplitAudit(name=name, images_dir=str(images_dir),
                    labels_dir=str(labels_dir) if labels_dir else "")
    counts: Dict[str, int] = {}
    issues: List[Issue] = []

    imgs: Dict[str, Path] = {}
    if images_dir.is_dir():
        for p in images_dir.rglob("*"):
            if p.is_file() and p.suffix.lower() in IMG_EXTS:
                imgs[p.stem] = p

    lbls: Dict[str, Path] = {}
    if labels_dir and labels_dir.is_dir():
        for p in labels_dir.rglob("*.txt"):
            if p.is_file():
                lbls[p.stem] = p

    sa.n_images = len(imgs)
    sa.n_labels = len(lbls)

    # 配对：有图无标 / 有标无图
    for stem in imgs:
        if stem not in lbls:
            counts["unpaired_img"] = counts.get("unpaired_img", 0) + 1
            issues.append(Issue("unpaired_img", imgs[stem].name, 0, "无同名 .txt"))
    for stem in lbls:
        if stem not in imgs:
            counts["unpaired_lbl"] = counts.get("unpaired_lbl", 0) + 1
            issues.append(Issue("unpaired_lbl", lbls[stem].name, 0, "无同名图片"))

    # 内容：逐文件
    targets = sorted(lbls.values())
    if limit is not None and len(targets) > limit:
        sa.truncated = True
        targets = targets[:limit]
    for txt in targets:
        file_issues, is_empty = audit_label_file(txt, nc=nc)
        if is_empty:
            counts["empty_lbl"] = counts.get("empty_lbl", 0) + 1
        for it in file_issues:
            counts[it.kind] = counts.get(it.kind, 0) + 1
            issues.append(it)

    sa.n_scanned = len(targets)
    sa.counts = counts
    sa.issues = issues
    return sa


# ── 整个数据集 ──────────────────────────────────────────────────────────────

def _standard_splits(ds_path: Path) -> List[Tuple[str, Path, Optional[Path]]]:
    """回退路径：不依赖 dataset_profiler，直接按 YOLO 标准结构找。"""
    out: List[Tuple[str, Path, Optional[Path]]] = []
    for key, aliases in (("train", ("train",)), ("valid", ("valid", "val")),
                         ("test", ("test",))):
        for a in aliases:
            d = ds_path / a
            if d.is_dir():
                img = d / "images" if (d / "images").is_dir() else d
                lab = d / "labels" if (d / "labels").is_dir() else None
                out.append((key, img, lab))
                break
    if not out and (ds_path / "images").is_dir():
        out.append(("root", ds_path / "images", ds_path / "labels"
                    if (ds_path / "labels").is_dir() else None))
    return out


def _minimal_yaml_get(text: str) -> Dict:
    """不依赖 PyYAML 的极简提取：只认本模块要的 `names` / `nc` 两个键。

    为什么要有这条兜底：PyYAML 是**可选依赖**（本机实测有装不上的解释器），
    而 `nc` 一旦拿不到，`cls_oorrange` 这条判据就会**静默失效** ——
    那是最坏的一种失败：**看着在跑、还报"未发现问题"，其实根本没检查**。
    ⇒ 宁可退化到无依赖的行解析，也不接受"读不到就不查、还不说"。
    """
    import json
    import re
    try:
        d = json.loads(text)
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    out: Dict = {}
    m = re.search(r"(?m)^nc\s*:\s*(\d+)\s*$", text)
    if m:
        out["nc"] = int(m.group(1))
    m = re.search(r"(?m)^names\s*:\s*\[([^\]]*)\]", text)          # 行内列表
    if m:
        out["names"] = [x.strip().strip("'\"") for x in m.group(1).split(",") if x.strip()]
    else:
        m = re.search(r"(?ms)^names\s*:\s*\n((?:[ \t]*-[ \t]*.+\n?)+)", text)  # 块列表
        if m:
            out["names"] = [ln.split("-", 1)[1].strip().strip("'\"")
                            for ln in m.group(1).splitlines() if "-" in ln]
    return out


def read_nc(ds_path: Path) -> Optional[int]:
    """读类别数（`names` 长度优先于 `nc`；data.yaml → classes.txt）。

    三级回退：PyYAML → 无依赖行解析 → `classes.txt` 行数。
    三个都拿不到才返回 None（调用方**必须**把"没查到"如实说出去）。
    """
    for fn in ("data.yaml", "dataset.yaml"):
        y = ds_path / fn
        if not y.is_file():
            continue
        text = y.read_text(encoding="utf-8", errors="replace")
        cfg = None
        try:
            import yaml
            cfg = yaml.safe_load(text)
        except Exception:
            cfg = None
        if not isinstance(cfg, dict):
            cfg = _minimal_yaml_get(text)
        if not isinstance(cfg, dict):
            continue
        names = cfg.get("names")
        if isinstance(names, (list, tuple)) and names:
            return len(names)
        if isinstance(names, dict) and names:
            return len(names)
        if isinstance(cfg.get("nc"), int):
            return int(cfg["nc"])
    ct = ds_path / "classes.txt"
    if ct.is_file():
        n = len([ln for ln in ct.read_text(encoding="utf-8", errors="replace").splitlines()
                 if ln.strip()])
        if n:
            return n
    return None


def audit_dataset(ds_path: Path, nc: Optional[int] = None,
                  limit: Optional[int] = None) -> DatasetAudit:
    """审整个数据集。`nc` 不给就从 data.yaml 推。"""
    ds_path = Path(ds_path)
    res = DatasetAudit(name=ds_path.name, path=str(ds_path))
    if nc is None:
        nc = read_nc(ds_path)
    res.nc = nc

    if not ds_path.is_dir():
        res.note = "路径不存在"
        return res

    # 尽量复用 dataset_profiler 的 split 识别；它不在也照样能跑
    splits: List[Tuple[str, Path, Optional[Path]]] = []
    try:
        from core.dataset_profiler import profile_dataset  # type: ignore
        prof = profile_dataset(ds_path)
        if prof:
            for s in prof.splits:
                if s.kind in _AUDIT_KINDS:
                    splits.append((s.key, Path(s.images_dir),
                                   Path(s.labels_dir) if s.labels_dir else None))
    except Exception:
        pass
    if not splits:
        splits = _standard_splits(ds_path)

    for key, img, lab in splits:
        res.splits.append(audit_split(key, img, lab, nc=nc, limit=limit))
    if not res.splits:
        res.note = "未找到可审的 split（train/valid/test 或 images/）"
    return res


# ── 报告 ────────────────────────────────────────────────────────────────────

def render_text(res: DatasetAudit, max_detail: int = 20) -> str:
    """人类可读报告（GUI 与 CLI 共用同一份口径）。"""
    lines: List[str] = []
    lines.append(f"数据集：{res.name}    类别数 nc = {res.nc if res.nc is not None else '未知'}")
    if res.nc is None:
        # 判据"没执行"必须说出来 —— 否则「✅ 未发现问题」会被读成「全查过了」
        lines.append("  ⚠️ 读不到 nc ⇒ 类别 ID 越界检查**未执行**（其余检查照常，别当成全查过了）")
    if res.note:
        lines.append(f"⚠️ {res.note}")
    for s in res.splits:
        head = (f"  [{s.name}] 图片 {s.n_images} / 标签 {s.n_labels}"
                f"{' (抽样 ' + str(s.n_scanned) + ')' if s.truncated else ''}")
        lines.append(head)
        if not s.counts:
            lines.append("      ✅ 未发现问题")
            continue
        for k in list(FATAL_KINDS) + list(WARN_KINDS) + list(INFO_KINDS):
            v = s.counts.get(k)
            if v:
                lv = "❌" if k in FATAL_KINDS else ("⚠️" if k in WARN_KINDS else "·")
                lines.append(f"      {lv} {KIND_ZH.get(k, k)}: {v} 处")
        if s.fatal_count:
            lines.append("      ⇒ 致命问题：训练前应先修复，否则可能训练失败或静默零标签")
    shown = 0
    for s in res.splits:
        for it in s.issues:
            if it.kind not in FATAL_KINDS:
                continue
            if shown >= max_detail:
                lines.append(f"  … 更多致命明细已省略（见导出）")
                break
            lines.append(f"    - [{s.name}] {it.path}:{it.line} {KIND_ZH.get(it.kind)} — {it.detail}")
            shown += 1
        if shown >= max_detail:
            break
    agg = res.counts
    lines.append("  —— 汇总：" + ("；".join(f"{KIND_ZH.get(k, k)} {v}" for k, v in agg.items())
                                  if agg else "无问题"))
    lines.append(f"  结论：{'✅ 通过（无致命问题）' if res.ok else '❌ 存在致命问题，共 ' + str(res.fatal_count) + ' 类'}")
    return "\n".join(lines)


def write_csv(res: DatasetAudit, out: Path) -> int:
    """导出明细为 CSV（UTF-8-SIG，Excel 直接可读）。返回写出的行数。"""
    import csv
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["split", "kind", "说明", "文件", "行", "详情"])
        for s in res.splits:
            for it in s.issues:
                w.writerow([s.name] + it.as_row())
                n += 1
    return n


# ── CLI ─────────────────────────────────────────────────────────────────────

def _main(argv: Sequence[str]) -> int:
    import sys
    if len(argv) < 2:
        print("用法: python src/core/dataset_qc.py <数据集目录> [--limit N] [--csv 输出.csv]")
        return 2
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    ds = Path(argv[1])
    limit = None
    csv_out = None
    if "--limit" in argv:
        limit = int(argv[argv.index("--limit") + 1])
    if "--csv" in argv:
        csv_out = Path(argv[argv.index("--csv") + 1])
    res = audit_dataset(ds, limit=limit)
    print(render_text(res))
    if csv_out:
        print(f"\n明细已导出：{csv_out}（{write_csv(res, csv_out)} 行）")
    return 0 if res.ok else 1


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv))
