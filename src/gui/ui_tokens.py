# -*- coding: utf-8 -*-
"""ui_tokens.py — 全站设计 token (视觉重设计 2026-09-11)

问题背景:
    界面之所以"又空又挤、白对白、排版乱", 根因不是缺功能, 而是**没有统一的设计
    标尺**: 字号在 7~16 之间随手取, 间距 2/6/10/14 混用, 卡片/按钮/输入框各自
    定色, 于是同一屏里出现 6 种字号、4 种圆角、8 种灰。

本模块把"标尺"集中成一份可复用的常量表:

    SPACE    8px 栅格间距 (2 的倍数, 保证任意组合都能对齐)
    RADIUS   圆角三档 + 胶囊
    TYPE     字阶 (含字重与用途说明)
    ICON     图标字号
    SURF     语义"表面层级" —— 用一个名字表达"这是第几层底", 由主题映射成实际色

使用约定 (给后来改界面的人):
    · 横向/纵向间距一律从 SPACE 取, 不要再写字面量;
    · 字号一律从 TYPE 取; 7pt 以下禁止出现 (1920 屏上等于糊);
    · "卡片放在画布上"用 SURF["card"] + SURF["canvas"] 组合, 保证对比度;
    · 一个区块只允许一个 primary 按钮, 其余走 secondary/ghost。
"""
from __future__ import annotations

from typing import Dict, Tuple

# ─────────────────────────── 间距 (8px 栅格) ───────────────────────────
SPACE: Dict[str, int] = {
    "xxs": 2,
    "xs": 4,
    "sm": 8,
    "md": 12,
    "lg": 16,
    "xl": 24,
    "xxl": 32,
}

# 兼容旧代码里的 SP4 命名
SP4 = {"sm": SPACE["sm"], "md": SPACE["md"], "lg": SPACE["lg"], "xl": SPACE["xl"]}

# ─────────────────────────── 圆角 ───────────────────────────
RADIUS: Dict[str, int] = {
    "sm": 6,      # 小徽标 / 输入框
    "md": 10,     # 卡片 / 按钮
    "lg": 14,     # 大面板 / 导航胶囊
    "pill": 999,  # 胶囊
}

# ─────────────────────────── 字阶 ───────────────────────────
_FAMILY = "Microsoft YaHei UI"
_MONO = "Consolas"

TYPE: Dict[str, Tuple] = {
    # 页标题: 一屏只出现一次, 用来锚定视觉起点
    "h1": (_FAMILY, 17, "bold"),
    # 卡片标题
    "h2": (_FAMILY, 13, "bold"),
    # 卡片内小节标题
    "h3": (_FAMILY, 11, "bold"),
    # 正文 / 表单值
    "body": (_FAMILY, 10),
    "body_bold": (_FAMILY, 10, "bold"),
    # 标签 / 辅助说明
    "label": (_FAMILY, 9),
    "label_bold": (_FAMILY, 9, "bold"),
    # 元信息 (时间戳、计数) — 最小可用字号, 不再往下降到 7
    "meta": (_FAMILY, 9),
    "tiny": (_FAMILY, 8),
    # 代码 / 日志 / 数值
    "mono": (_MONO, 10),
    "mono_sm": (_MONO, 9),
}

# 图标字号。
# ⚠ 2026-09-11 由 "Segoe UI Emoji" 改成 _FAMILY —— 两个原因, 都实测过:
#   ① **视觉收益是零**: Tk 8.6 不渲染彩色 emoji 层, 拿 Segoe UI Emoji /
#      Microsoft YaHei UI / Arial 画同一串 "🚀🎬📂🔍⚙🔊" 得到的是**一模一样的
#      单色轮廓字形** (GDI 真像素取证: runs/_verify_emoji_glyph.py + _emoji_probe.png)。
#   ② **代价是 67 倍**: 65 个 distinct 图标字符一次性渲染, 17pt 下
#      Segoe UI Emoji = 1499.78 ms, Microsoft YaHei UI = 22.35 ms。
#      这个钱是**每个不同字符**单独付的, 且换新字号还要再付 (18pt 追加 ~10.7ms)。
#      全站 emoji 出现 314 次 ⇒ 就是"刚打开程序那几秒卡"的主因。
# 配套: ui_perf.warm_fonts() 负责兜住页面里**写死旧字体**的散落调用点。
ICON = {
    "lg": (_FAMILY, 17),
    "md": (_FAMILY, 12),
    "sm": (_FAMILY, 10),
}

# ─────────────────────────── 尺寸 ───────────────────────────
CONTROL_H = 30          # 输入框 / 下拉 / 小按钮统一高度
BUTTON_H = {"sm": 26, "md": 32, "lg": 38}
CARD_PAD = (SPACE["lg"], SPACE["md"])        # 标准卡片内边距 (x, y)
CARD_PAD_TIGHT = (SPACE["md"], SPACE["sm"])
PAGE_PAD = SPACE["xl"]                        # 页面左右留白
GRID_GAP = SPACE["md"]                        # 卡片之间

# ─────────────────────────── 语义表面层级 ───────────────────────────
# 0 = 最底 (应用画布), 1 = 次级面 (内嵌面板), 2 = 卡片面, nav = 导航面
SURF_LEVELS = ("canvas", "sunken", "card", "nav")

# 主题色字段映射: 语义名 -> ThemeColors 属性名
SURF_MAP = {
    "canvas": "bg",
    "sunken": "bg2",
    "card": "card_bg",
    "nav": "sidebar_bg",
    "input": "input_bg",
    "raised": "card_bg",
}

# 文本层级: 语义名 -> ThemeColors 属性名
TEXT_MAP = {
    "primary": "text",
    "secondary": "text_secondary",
    "muted": "text_muted",
}


def surf(name: str, colors=None):
    """取"第几层底"的实际颜色 (语义化调用, 避免各页面各写各的 hex)。"""
    if colors is None:
        from infrastructure.config import get_theme
        colors = get_theme().colors
    return getattr(colors, SURF_MAP.get(name, "bg"), colors.bg)


def text_color(name: str = "primary", colors=None):
    if colors is None:
        from infrastructure.config import get_theme
        colors = get_theme().colors
    return getattr(colors, TEXT_MAP.get(name, "text"), colors.text)


def contrast_ok(fg: str, bg: str, minimum: float = 4.5) -> bool:
    """粗略校验前景/背景对比度是否达 WCAG AA (供自检脚本调用)。"""
    def _lum(c):
        try:
            c = str(c).strip().lstrip("#")
            if len(c) == 3:
                c = "".join(ch * 2 for ch in c)
            r, g, b = (int(c[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
        except Exception:
            return 0.0

        def f(v):
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)

    l1, l2 = _lum(fg), _lum(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05) >= minimum
