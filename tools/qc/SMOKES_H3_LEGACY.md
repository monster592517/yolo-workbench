# 隔离记录：H3 线残留守卫（32 个）

> **2026-10-10** · 开源清理第 2 轮 · 关联：`tools/qc/SMOKES.md` §三

## 为什么要这份文件（而不是只留一个 JSON）

隔离区的实体在 **`.trash/2026-10-10_smokes_h3_legacy/`**，而 `.trash/` 是
**被 `.gitignore` 忽略**的目录 —— 也就是说，**那些文件在版本历史里是隐形的**。

> 纪律（本项目已确立）：**被忽略的产物，必须在被跟踪的元数据里落内容哈希当溯源锚。**
> 否则将来无法回答「当时隔离的到底是哪一版」。

所以这份 `.md` 是**被跟踪**的，它是隔离区的**唯一可信索引**。

## 前置：为什么隔离而不是修

2026-10-01 **四线剥离**把「H3 AI 视频产线 / 机械仿真 / 板卡部署」整体搬去
`18_legacy_h3_sim/from_01_yolo_lab/`（在 `AI_projects` 根下）。
下列守卫的**被判对象不在本仓了** ⇒ 它们恒 FAIL，且**失败信息指向错误的方向**
（报 `No module named 'auto_label_fall'`，看着像"判据坏了"，实际是"资产搬走了"）。

**判据本身很可能是对的**，只是它要守的东西不在这儿 —— 所以是**隔离**，不是删除。
产线若要用，从 `18_legacy_h3_sim/` 那侧跑；**不要往回搬**。

## 复现/唤醒方式

```bash
# 文件在磁盘上（未被跟踪），可直接拷回后跑：
ls .trash/2026-10-10_smokes_h3_legacy/
```

## 32 个文件的 sha256 前 16 位（配对即证明"隔离的就是这一版"）

| 文件 | sha256_16 |
|---|---|
| `smoke_anchor_scale.py` | `53532d161e5972eb` |
| `smoke_bare_cmd.py` | `89916ee4b59e9ef9` |
| `smoke_bgm.py` | `dc8dfa6cde20ee2b` |
| `smoke_clamp_basis.py` | `6ce4208ce4ded4ef` |
| `smoke_cls_zero.py` | `a10af8dd07f9b7a3` |
| `smoke_control_engine.py` | `b4aedf96f4cd9c1a` |
| `smoke_dml.py` | `7055a5419a67d2db` |
| `smoke_doc_coverage.py` | `52c55a1162280ac9` |
| `smoke_expand.py` | `b3b699669ce6a2c4` |
| `smoke_frame_paths.py` | `fe43e60a1b751a2a` |
| `smoke_gate.py` | `1767e412c292bd32` |
| `smoke_gen_mem.py` | `245e7ccd81c91f35` |
| `smoke_instrument_selfselect.py` | `fe8b8e105ce1b484` |
| `smoke_interlock.py` | `9e4c43f1d839d1c9` |
| `smoke_kpt_anatomy.py` | `d948c07466c867f0` |
| `smoke_label.py` | `b95565016c5dbd81` |
| `smoke_label2.py` | `63b1ed550a65b78a` |
| `smoke_log_reading.py` | `a286537f27731fd3` |
| `smoke_lowconf.py` | `d0cb752e63567ca8` |
| `smoke_master_defaults.py` | `8b050081e6e2d166` |
| `smoke_path_guard.py` | `ab722f5fe2b041f3` |
| `smoke_phantom.py` | `66c78d69fd1eacdb` |
| `smoke_polyfixed.py` | `e5d1cb36dd8bd1c9` |
| `smoke_popup_wmi.py` | `722070f7e589dff1` |
| `smoke_postlog.py` | `441149853d018fe5` |
| `smoke_postloop.py` | `476028b492a26eee` |
| `smoke_prompt_drift.py` | `128e823a68b5f152` |
| `smoke_sam2.py` | `71f5890180eae4ca` |
| `smoke_scale_medium.py` | `f3ddc9b6e724db0c` |
| `smoke_scale_risk.py` | `339bd8f19f89907c` |
| `smoke_shot_diversity.py` | `741db3c075d6d10c` |
| `smoke_small.py` | `7b043fc39b9ab596` |

> 隔离区目录内另有 `_TRIAGE.json`（逐条分类与原因）与 `_MOVED_MANIFEST.json`
> （同一份哈希清单的机器可读版），供程序化核对。

## 核验（可复跑）

```bash
# 配对判据：磁盘上的 sha256 前 16 位必须与本表逐行一致
python - <<'EOF'
import hashlib, pathlib
d = pathlib.Path(".trash/2026-10-10_smokes_h3_legacy")
for p in sorted(d.glob("smoke_*.py")):
    print(p.stem, hashlib.sha256(p.read_bytes()).hexdigest()[:16])
EOF
```

> ⚠️ 用 **sha256 而不是文件大小/条数** —— "同大小不同内容"看不出来。
