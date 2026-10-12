# -*- coding: utf-8 -*-
"""守卫对照: `fitness_personal_paths` 必须**既灵敏又不误报**。

两端都会出人命，所以两头都要钉：

  漏报（假绿）：`C:\\Users\\<某人>\\...` 混进仓库 ⇒ 开源后暴露账户名、
                而且别人照抄一个**不存在**的路径。而"没报"和"它没长眼睛"长得一模一样。
  误报（假红）：把**正确的占位写法**（`%USERPROFILE%` / `C:\\Users\\...` / `<你>`）
                也报出来 ⇒ 满屏噪音 ⇒ **训练人忽略自检**（本项目为此吃过多次亏）。

对照清单（全部必须通过）:
  负 : `C:\\Users\\somebody\\…`（反斜杠）        -> 报
  负 : `C:/Users/somebody/…`（正斜杠）           -> 报
  负 : `/home/somebody/…`                        -> 报
  负 : `/Users/somebody/…`（macOS 形态）          -> 报
  正 : `%USERPROFILE%\\…`                        -> 不报
  正 : `C:\\Users\\yourname\\…`                  -> 不报
  正 : `C:/Users/&lt;你&gt;/…`（HTML 转义占位）    -> 不报
  正 : `C:\\Users\\...\\Desktop\\…`（文档举例）    -> 不报
  正 : `/home/$USER/…`（shell 变量）              -> 不报
  正 : 普通文本                                    -> 不报
  未改动: EXEMPT 里必须有那两份**史实**文件       -> 防止有人把豁免表删空
  未改动: 判据本体必须在 EXEMPT 里（自指防护）     -> 否则它会被自己报出来
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "qc"))

from fitness_personal_paths import EXEMPT, PLACEHOLDERS, scan  # noqa: E402

checks: list[tuple[str, bool]] = []


def chk(label: str, got, want) -> bool:
    ok = got == want
    checks.append((label, ok))
    print(f"[{'OK  ' if ok else 'FAIL'}] {label} — 实得 {got!r}（应为 {want!r}）")
    return ok


# ── 负：四种家目录形态都要报出来，且账户名要取对 ──
for text, who in (
    (r'x = r"C:\Users\alice\Desktop\a.txt"', "alice"),
    ('x = "C:/Users/alice/a.txt"', "alice"),
    ("p = /home/alice/proj", "alice"),
    ("p = /Users/alice/proj", "alice"),
):
    hits = scan(text)
    chk(f"负 {text[:36]}", [h[1] for h in hits], [who])

# ── 正：占位写法一个都不许报（误报会训练人忽略自检）──
for text in (
    r'x = r"%USERPROFILE%\Desktop\a.txt"',
    r'x = r"C:\Users\yourname\Desktop\a.txt"',
    'uv venv "C:/Users/&lt;你&gt;/.venv"',
    r"背景: 项目可能迁移 (如 C:\Users\...\Desktop\yolo_lab -> D:\yolo_lab)",
    "cd /home/$USER/proj",
    "这是一段与路径无关的普通说明文字。",
):
    chk(f"正 {text[:36]}", scan(text), [])

# ── 台账完整性（防"把豁免表删空"这种最省事的假修复）──
chk("未改动: 两份史实文件在 EXEMPT 里",
    {"CHANGELOG.md", "避坑清单_20260814.md"} <= set(EXEMPT), True)
chk("未改动: 判据本体在 EXEMPT 里（自指防护）",
    "tools/qc/fitness_personal_paths.py" in EXEMPT, True)
chk("未改动: 判据本体在 EXEMPT 里且理由写明",
    bool(EXEMPT.get("tools/qc/fitness_personal_paths.py")), True)
chk("未改动: 占位符表非空（清空它 = 判据变噪音机）",
    len(PLACEHOLDERS) >= 5, True)

bad = [c for c in checks if not c[1]]
print("-" * 70)
print(f"合计 {len(checks)} 条：如期 {len(checks) - len(bad)} / 未如期 {len(bad)}")
tag = "PASS " if not bad else "FAIL "
print(f"{tag} smoke_personal_paths: {len(checks) - len(bad)}/{len(checks)} 通过")
sys.exit(1 if bad else 0)
