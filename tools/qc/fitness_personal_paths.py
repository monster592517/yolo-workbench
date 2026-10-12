# -*- coding: utf-8 -*-
r"""fitness_personal_paths —— 会被公开的文件里，**不许出现作者本机的家目录路径**。

为什么不并进 `fitness_hardcoded_root`（两者是不同的病）
----------------------------------------------------
`fitness_hardcoded_root` 守的是**项目根路径**（`<项目根>\\01_yolo_lab` 之类），
问的是「搬了家会不会断」。它**不管** `C:\\Users\\<某个账户>` —— 那种路径是另一个病：

    ① 暴露作者的本机账户名；
    ② 在别人机器上**根本不存在**，照抄的人第一步就失败。

射程缺口是实测撞出来的（2026-10-11）：全仓 **34 处** `C:\\Users\\<name>\\...`，
**28 处在活文档里、1 处在个人脚本里** —— 而**没有任何判据看着这一块**。
（当时是因为要开源自查才偶然发现，不是判据报出来的。）

判据口径
--------
- **全集合扫描**：拿的是 `git ls-files`，也就是"**会被公开的全部文件**"，
  不点名清单 ⇒ 新加的文件天然在射程内（纪律：准入门用全集合，豁免才点名）。
- **占位符不算命中**：`%USERPROFILE%` / `yourname` / `someone` / `<you>` / `$HOME` 等
  —— 文档里拿它们做示范是**正确写法**，报它们就是满屏噪音。
- **射程刻意比"本机"更宽**：只要不是占位，**任何**账户名都报 —— 因为
  `C:\Users\<别人的名字>\...` 在**读者机器上同样不存在**，一样是"照抄必错"。
  （实测就撞到过：`C:\Users\1\...` 是从别处抄来的，本机根本没有 `1` 这个账户。）
- **豁免点名 + 写明理由**：史实文件（改了就是篡改历史）与判据本体自指。
- 退出码：`0` 干净 / `1` 有发现（**架构漂移，判据本身是好的**）/ `3` 不在 git 仓库（前置不满足）。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]

# 文本类才扫 —— 二进制里"命中"没有意义，只会制造噪音
TEXT_EXT = {".py", ".md", ".html", ".htm", ".json", ".yaml", ".yml", ".txt",
            ".bat", ".cmd", ".ps1", ".sh", ".ini", ".cfg", ".toml"}

# 家目录形态。刻意收窄到"后面还跟着一层子目录"（`Users\\x\\`），
# 免得把"讲这个坑"的正文里那种裸 `C:\\Users\\x` 也扫成噪音。
PATTERNS = [
    re.compile(r"[A-Za-z]:[\\/]{1,2}Users[\\/]+([^\\/\s\"'<>|,;)]+)"),
    # `(?<![A-Za-z]:)` 把盘符版让给上一条 —— 否则 `C:/Users/alice` 会被两个模式
    # 各报一次，**同一处报两遍 = 报告虚高**（实测踩到过）。
    re.compile(r"(?<![A-Za-z]:)/(?:home|Users)/([^/\s\"'<>|,;)]+)"),
]

#: 占位符 / 变量 —— 这些是**正确写法**，不许报
PLACEHOLDERS = {
    "yourname", "you", "username", "user", "someone", "somebody", "xxx", "xx",
    "%userprofile%", "$home", "$user", "${user}", "~",
}

#: 豁免**点名**（每条都要能说出理由；不做"按目录整片放过"）
EXEMPT: dict[str, str] = {
    "CHANGELOG.md": "史实：变更历史不改（项目纪律），里面带本机路径是历史事实",
    "避坑清单_20260814.md": "史实：坑雷记录带日期与证据，改了就是篡改",
    "tools/qc/fitness_personal_paths.py": "判据本体：它**必须**包含这些模式才叫判据（自指）",
    "tools/qc/smoke_personal_paths.py": "上面的正负对照，同理必须包含模式（自指）",
}


def tracked_files() -> list[str] | None:
    """`git ls-files -z` → 相对路径列表；不在仓库里返回 None。"""
    r = subprocess.run(["git", "ls-files", "-z"], cwd=str(ROOT),
                       capture_output=True)
    if r.returncode != 0:
        return None
    out = r.stdout.decode("utf-8", "replace")
    return [x for x in out.split("\0") if x]


def scan(text: str) -> list[tuple[int, str, str]]:
    """扫一段文本 → [(行号, 命中的账户名, 该行原文)]。"""
    hits: list[tuple[int, str, str]] = []
    for ln, line in enumerate(text.splitlines(), 1):
        for pat in PATTERNS:
            for m in pat.finditer(line):
                name = m.group(1)
                if name.lower() in PLACEHOLDERS:
                    continue
                if any(c in name for c in "%${}*&"):
                    continue
                # 账户名形态：1~32 位、不以点开头、不能是纯符号串。
                # （`C:\Users\...` / `C:/Users/&lt;你&gt;` 这类**占位写法**必须放过 ——
                #   它们是文档里正确的示范，报它们就是"误报训练人忽略自检"。）
                if not re.fullmatch(r"[A-Za-z0-9_\-][A-Za-z0-9_.\-]{0,31}", name):
                    continue
                if set(name) <= set(".-_"):
                    continue
                hits.append((ln, name, line.strip()[:120]))
    return hits


def main() -> int:
    files = tracked_files()
    if files is None:
        print("SKIP  当前目录不是 git 仓库，无法列举'会被公开的文件'")
        return 3

    bad: list[tuple[str, int, str, str]] = []
    scanned = skipped = 0
    for rel in files:
        if Path(rel).suffix.lower() not in TEXT_EXT:
            skipped += 1
            continue
        if rel in EXEMPT:
            skipped += 1
            continue
        p = ROOT / rel
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            skipped += 1
            continue
        scanned += 1
        for ln, name, line in scan(text):
            bad.append((rel, ln, name, line))

    print("== fitness_personal_paths ==")
    print(f"  扫描 {scanned} 个文本文件（跳过 {skipped}：非文本/豁免）")
    if bad:
        for rel, ln, name, line in bad[:30]:
            print(f"  BAD  {rel}:{ln}  命中账户名 {name!r}  |  {line}")
        if len(bad) > 30:
            print(f"  … 其余 {len(bad) - 30} 处省略")
        print(f"\nFAIL  {len(bad)} 处本机家目录路径。要么改成 `%USERPROFILE%` 之类的占位写法，"
              f"要么把它加进 EXEMPT 并写明理由。")
        return 1
    print("  OK    没有本机家目录路径")
    print("\nPASS  fitness_personal_paths")
    return 0


if __name__ == "__main__":
    sys.exit(main())
