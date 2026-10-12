# -*- coding: utf-8 -*-
"""守卫对照: `core/dataset_qc.py` 的判据必须**既灵敏又不误报**。

为什么要带对照（项目纪律：**新守卫必须带正负对照**）：
  dataset_qc 的全部价值在于「坏标注混进来时它会把训练拦下来」。
  如果它对任何输入都返回空（解析写错、形态判据过严/过松），
  它就是**恒绿的假检查** —— 比没有更坏：训练照跑、mAP 恒 0，
  而界面上写着「✅ 未发现问题」。
  反过来，如果它把**空标签当成错误**，就会满屏误报 ——
  空标签在 YOLO 里是合法的（= 背景图），报警报多了会训练人忽略自检。
  ⇒ 必须同时证明三件事：
     ① 真坏的东西**必报**（灵敏度）；② 合法的东西**不报**（特异度）；
     ③ 判据**分级正确**（致命 vs 告警，别全塞成一档）。

对照清单（全部必须通过）:
  正  : 干净数据集                                        -> ok = True
  正  : 三种形态 det/seg/pose 都认；畸形字段数拒            -> 形态判定
  正  : pose 的 v 允许 0/1/2 ⇒ 不能被当坐标越界            -> 0 处
  负  : field_count / non_numeric / cls_oorrange / cls_negative -> 各 1 处且属致命
  负  : coord_range / zero_area                           -> 各 1 处且**不**属致命
  负  : **空标签不许报成问题**（特异度，防满屏误报）        -> 0 处问题
  负  : 少量空标签/少量无标 ⇒ 不致命（边界）               -> ok = True
  负  : 整个 split 全空 ⇒ 提级致命                         -> ok = False
  负  : 有图无标过半 ⇒ 提级致命                            -> ok = False
  负  : **读不到 nc 时报告必须明说"检查未执行"**            -> 文本含"未执行"
  负  : nc 三级回退（块 YAML / 行内 / JSON / classes.txt）  -> 都读得出
  畸形: 路径不存在 / 只有图片没有标签                      -> 不抛

⚠️ 夹具**自造**（tempfile 现建），不依赖仓库里"恰好有某个坏数据集"——
   那种外部状态会漂移，今天绿明天红，等于没测。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from core.dataset_qc import (FATAL_KINDS, audit_dataset, audit_label_file,  # noqa: E402
                             classify_shape, read_nc, render_text)

checks = []


def chk(label: str, got, want) -> bool:
    ok = got == want
    checks.append((label, ok))
    print(f"[{'OK  ' if ok else 'FAIL'}] {label} — 实得 {got!r}（应为 {want!r}）")
    return ok


def _put(img: Path, lab: Path, name: str, body: str = None):
    (img / f"{name}.jpg").write_bytes(b"\xff\xd8\xff\xe0")
    if body is not None:
        (lab / f"{name}.txt").write_text(body, encoding="utf-8")


def _mkds(base: Path, yaml_text: str = None):
    ds = base / "ds"
    img = ds / "train" / "images"
    lab = ds / "train" / "labels"
    img.mkdir(parents=True)
    lab.mkdir(parents=True)
    (ds / "data.yaml").write_text(
        yaml_text if yaml_text is not None
        else "names: [person, fall]\nnc: 2\ntrain: train/images\n", encoding="utf-8")
    return ds, img, lab


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="smoke_qc_"))
    try:
        # ── 形态判定 ──
        chk("正 det=5 字段", classify_shape(5), "det")
        chk("正 seg=7 字段(3 点)", classify_shape(7), "seg")
        chk("正 pose=8 字段(1 关键点)", classify_shape(8), "pose")
        chk("正 pose=47 字段(14 关键点)", classify_shape(47), "pose")
        chk("负 3 字段是畸形", classify_shape(3), None)
        chk("负 6 字段是畸形", classify_shape(6), None)

        # ── pose 的 v 不能被当坐标 ──
        t = tmp / "v.txt"
        t.write_text("0 0.5 0.5 0.2 0.2 0.5 0.5 2", encoding="utf-8")
        chk("正 pose v=2 不报坐标越界", len(audit_label_file(t, nc=2)[0]), 0)

        # ── 每类问题都能报，且分级正确 ──
        kinds = {
            "short":    ("0 0.5 0.5",           "field_count",  True),
            "nonnum":   ("0 a b c d",           "non_numeric",  True),
            "cls_oor":  ("5 0.5 0.5 0.2 0.2",   "cls_oorrange", True),
            "cls_neg":  ("-1 0.5 0.5 0.2 0.2",  "cls_negative", True),
            "oor":      ("0 1.5 0.5 0.2 0.2",   "coord_range",  False),
            "zeroarea": ("0 0.5 0.5 0 0.2",     "zero_area",    False),
        }
        for nm, (body, kind, is_fatal) in kinds.items():
            f = tmp / f"{nm}.txt"
            f.write_text(body, encoding="utf-8")
            got = [i.kind for i in audit_label_file(f, nc=2)[0]]
            chk(f"负 {nm} 必报 {kind}", kind in got, True)
            chk(f"分级 {kind} fatal={is_fatal}", kind in FATAL_KINDS, is_fatal)

        # ── 特异度：空标签**不许**报成问题 ──
        e = tmp / "empty.txt"
        e.write_text("", encoding="utf-8")
        issues, is_empty = audit_label_file(e, nc=2)
        chk("正 空标签认定为空", is_empty, True)
        chk("正 空标签 0 处问题（防误报）", len(issues), 0)

        # ── 干净数据集 -> 通过 ──
        ds1, i1, l1 = _mkds(tmp / "clean")
        for n in ("a", "b", "c"):
            _put(i1, l1, n, "0 0.5 0.5 0.2 0.2\n")
        r1 = audit_dataset(ds1)
        chk("正 干净数据集通过", r1.ok, True)
        chk("正 干净数据集 nc=2", r1.nc, 2)

        # ── 少量空标签 / 少量无标 -> 仍通过（边界） ──
        ds2, i2, l2 = _mkds(tmp / "minor")
        _put(i2, l2, "obj", "0 0.5 0.5 0.2 0.2\n")
        _put(i2, l2, "bg", "")
        r2 = audit_dataset(ds2)
        chk("负 少量空标签不致命（边界）", r2.ok, True)
        chk("负 少量空标签计入 info", r2.counts.get("empty_lbl"), 1)

        # ── 整个 split 全空 -> 提级致命 ──
        ds3, i3, l3 = _mkds(tmp / "allempty")
        for n in ("a", "b"):
            _put(i3, l3, n, "")
        r3 = audit_dataset(ds3)
        chk("负 全空 split 提级致命", r3.ok, False)

        # ── 有图无标过半 -> 提级致命 ──
        ds4, i4, l4 = _mkds(tmp / "unpaired")
        _put(i4, l4, "one", "0 0.5 0.5 0.2 0.2\n")
        for n in ("u1", "u2", "u3"):
            _put(i4, l4, n, None)
        r4 = audit_dataset(ds4)
        chk("负 无标过半提级致命", r4.ok, False)

        # ── 读不到 nc 时必须**说出"检查未执行"**（防静默失效） ──
        ds5 = tmp / "nonc"
        (ds5 / "train" / "images").mkdir(parents=True)
        (ds5 / "train" / "labels").mkdir(parents=True)
        (ds5 / "train" / "images" / "x.jpg").write_bytes(b"\xff\xd8")
        (ds5 / "train" / "labels" / "x.txt").write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")
        r5 = audit_dataset(ds5)
        chk("负 nc 未知", r5.nc, None)
        chk("负 nc 未知时报告明说未执行", "未执行" in render_text(r5), True)

        # ── nc 三级回退 ──
        for nm, body, want in (
            ("块状 YAML", "names:\n- person\n- fall\nnc: 2\n", 2),
            ("行内 YAML", "names: [a, b, c]\nnc: 3\n", 3),
            ("单行 JSON", '{"path":".","nc":2,"names":["p","f"]}', 2),
        ):
            d = tmp / ("nc_" + str(abs(hash(nm)) % 10000))
            d.mkdir(parents=True, exist_ok=True)
            (d / "data.yaml").write_text(body, encoding="utf-8")
            chk(f"正 nc 回退: {nm}", read_nc(d), want)
        d = tmp / "nc_classes"
        d.mkdir()
        (d / "classes.txt").write_text("p\nf\n", encoding="utf-8")
        chk("正 nc 回退: classes.txt", read_nc(d), 2)

        # ── 畸形输入不许抛 ──
        try:
            r = audit_dataset(tmp / "nope")
            chk("畸形 路径不存在不抛", isinstance(r.ok, bool), True)
        except Exception as ex:
            chk("畸形 路径不存在不抛", f"抛了 {ex}", True)
        try:
            d = tmp / "noimg"
            (d / "train" / "images").mkdir(parents=True)
            r = audit_dataset(d)
            chk("畸形 只有图片无标签不抛", isinstance(r.ok, bool), True)
        except Exception as ex:
            chk("畸形 只有图片无标签不抛", f"抛了 {ex}", True)

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    bad = [c for c in checks if not c[1]]
    print("-" * 70)
    print(f"合计 {len(checks)} 条：如期 {len(checks) - len(bad)} / 未如期 {len(bad)}")
    tag = "PASS " if not bad else "FAIL "
    print(f"{tag} smoke_dataset_qc: {len(checks) - len(bad)}/{len(checks)} 通过")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
