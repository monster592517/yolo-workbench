# -*- coding: utf-8 -*-
"""主题管理 — 浅色/深色/跟随系统时间自动切换。

- 模式持久化到 config.yaml 的 ui.theme_mode (light | dark | auto)
- auto: 6:00-18:00 浅色, 其余时间深色
- apply_theme() 原地改写全局 ThemeColors 单例, 保证各页面模块级引用即时生效
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from infrastructure.config import ThemeColors, get_theme

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CONFIG_PATH = _PROJECT_ROOT / "config.yaml"


def get_project_root() -> Path:
    """当前项目根目录 (可被路径中心动态切换)"""
    return _PROJECT_ROOT


def set_project_root(root) -> None:
    """路径中心联动: 切换主题持久化所用的 config.yaml 路径 (幂等)"""
    global _PROJECT_ROOT, _CONFIG_PATH
    _PROJECT_ROOT = Path(root).resolve()
    _CONFIG_PATH = _PROJECT_ROOT / "config.yaml"


# ═══════════════ 浅色主题 v7「纸感分层」(2026-09-11 视觉重设计) ═══════════════
# 重设计动机: 旧版 sidebar_bg=#FFFFFF 与 card_bg=#FFFFFF 相同, 画布 bg 又几乎是
# 白 → 侧边栏/画布/卡片三者糊成一整片白, 用户反馈"又空又挤"。
# 现在明确 4 层表面, 层间用"色阶 + 1px 描边"拉开:
#     canvas #E9EEF6   — 应用画布 (内容面板外侧的 12px 留白)
#     nav    #F4F8FD   — 侧边导航 (冷一档的白)
#     card   #FFFFFF   — 卡片 / 内容面板
#     sunken #F5F8FC   — 卡片内嵌面板 (日志/控制台)
# 文本三档对比度全部 ≥ 4.5:1 (WCAG AA) 且在最不利表面 (canvas #E9EEF6) 上仍成立,
# 见 ui_tokens.contrast_ok 校验结果:
#     text 15.2:1 · text_secondary 7.3:1 · text_muted 4.7:1
LIGHT_COLORS = ThemeColors(
    primary="#2C63D6", primary_hover="#2454BC",
    accent="#2C63D6",
    bg="#E9EEF6", bg2="#F5F8FC",
    card_bg="#FFFFFF", text="#141920",
    text_secondary="#414B58", text_muted="#5F6B79",
    border="#D3DCE8", input_bg="#FFFFFF",
    log_bg="#F4F7FB", log_text="#1B2028",
    success="#0B8F52", success_hover="#0A7D47",
    danger="#C93A3A", danger_hover="#B33333",
    purple="#5B4BD6", purple_hover="#4F40C4",
    warning="#8F6414",
    success_bg="#E6F6EE", danger_bg="#FBECEC", warning_bg="#FBF3E0", on_solid="#FFFFFF",
    glass_highlight="#BFD6F2", glow="#6E9CEC",
    sidebar_bg="#F4F8FD", sidebar_hover="#E7EFF9",
    nav_active_bg="#DFEAFC", nav_active_fg="#1B4FC0", nav_hover_bg="#EDF3FA",
)

# ═══════════════ 深色主题 v8「深色玻璃」(2026-09-11 视觉重设计) ═══════════════
# 沿用用户选定的"深色玻璃"方向, 但把层间色差拉开 (旧版 bg #0B1018 与
# card_bg #161F2D 差得不够, 卡片边界靠 border 硬撑), 边框更冷更薄。
# 文本对比度以最亮的输入框面 (#1B2636) 为下限校验: 13.5 / 7.2 / 5.0 :1。
DARK_COLORS = ThemeColors(
    primary="#7CB0FF", primary_hover="#5F9BF5",
    accent="#7CB0FF",
    bg="#090E16", bg2="#111A26",
    card_bg="#16202E", text="#EEF3FA",
    text_secondary="#A9BAD0", text_muted="#8296AE",
    border="#28384E", input_bg="#1B2636",
    log_bg="#080C12", log_text="#DCE6F2",
    success="#3ED598", success_hover="#31BE85",
    danger="#F2706B", danger_hover="#E25E59",
    purple="#A78BFA", purple_hover="#9678F0",
    warning="#E8B75A",
    success_bg="#132B24", danger_bg="#372025", warning_bg="#302818", on_solid="#FFFFFF",
    glass_highlight="#3B5477", glow="#7CB0FF",
    sidebar_bg="#0C1320", sidebar_hover="#18222F",
    nav_active_bg="#1C2A40", nav_active_fg="#A8C8FF", nav_hover_bg="#151E2B",
)

_resolved: str = "dark"

# 主题模式缓存 (性能, 2026-09-11)
# 原 get_mode() 每次调用都 read_text + 正则解析 config.yaml。它被
# apply_theme / _check_auto_theme / 各页面的 _tk5() 间接调用, 属于热路径。
# 现按 (mtime, size) 指纹缓存 —— 外部手工改了 config.yaml 也能立刻感知,
# 未改动时零 IO。
_MODE_CACHE = {"key": None, "value": "auto"}


def _config_fingerprint():
    try:
        st = _CONFIG_PATH.stat()
        return (st.st_mtime_ns, st.st_size)
    except Exception:
        return None


def get_mode() -> str:
    """读取配置中的主题模式: light | dark | auto (带指纹缓存)"""
    fp = _config_fingerprint()
    if fp is not None and _MODE_CACHE["key"] == fp:
        return _MODE_CACHE["value"]
    value = "auto"
    try:
        if _CONFIG_PATH.exists():
            text = _CONFIG_PATH.read_text(encoding="utf-8")
            m = re.search(r"^\s*theme_mode\s*:\s*[\"']?([\w-]+)[\"']?", text, re.M)
            if m and m.group(1) in ("light", "dark", "auto"):
                value = m.group(1)
    except Exception:
        pass
    _MODE_CACHE["key"] = fp
    _MODE_CACHE["value"] = value
    return value


def set_mode(mode: str) -> None:
    """将主题模式写入 config.yaml (保留原有注释与格式)"""
    if mode not in ("light", "dark", "auto"):
        return
    try:
        text = _CONFIG_PATH.read_text(encoding="utf-8")
        if re.search(r"^\s*theme_mode\s*:", text, re.M):
            text = re.sub(r"^(\s*)theme_mode\s*:.*$",
                          lambda m: f"{m.group(1)}theme_mode: {mode!r}   # light | dark | auto(跟随系统时间)",
                          text, count=1, flags=re.M)
        else:
            if re.search(r"^ui\s*:", text, re.M):
                text = re.sub(r"^(ui\s*:.*)$",
                              lambda m: m.group(1) + f"\n  theme_mode: {mode!r}   # light | dark | auto(跟随系统时间)",
                              text, count=1, flags=re.M)
            else:
                text += f"\nui:\n  theme_mode: {mode!r}   # light | dark | auto(跟随系统时间)\n"
        _CONFIG_PATH.write_text(text, encoding="utf-8")
        _MODE_CACHE["key"] = None   # 写盘后指纹失效, 下次 get_mode 重新读
    except Exception:
        pass


def resolve_mode(mode: str | None = None) -> str:
    """解析最终生效主题: auto → 按当前系统时间"""
    mode = mode or get_mode()
    if mode in ("light", "dark"):
        return mode
    hour = datetime.now().hour
    return "light" if 6 <= hour < 18 else "dark"


def apply_theme(mode: str | None = None) -> str:
    """应用主题: 原地改写全局 ThemeColors, 返回最终生效模式 (light/dark)"""
    global _resolved
    mode = mode or get_mode()
    resolved_mode = resolve_mode(mode)
    src = LIGHT_COLORS if resolved_mode == "light" else DARK_COLORS
    theme = get_theme()
    for key, value in src.__dict__.items():
        setattr(theme.colors, key, value)
    _resolved = resolved_mode
    if mode != get_mode():
        set_mode(mode)
    return resolved_mode


def resolved() -> str:
    """当前生效的主题 (light/dark)"""
    return _resolved


def mode_label(mode: str | None = None) -> str:
    mode = mode or get_mode()
    return {"light": "浅色", "dark": "深色", "auto": "跟随系统时间"}.get(mode, mode)


class _ThemeManagerAPI:
    """面向 UI 的轻量 API 包装"""

    @staticmethod
    def get_mode() -> str: return get_mode()
    @staticmethod
    def set_mode(mode: str) -> None: set_mode(mode)
    @staticmethod
    def resolve_mode(mode: str | None = None) -> str: return resolve_mode(mode)
    @staticmethod
    def apply(mode: str | None = None) -> str: return apply_theme(mode)
    @staticmethod
    def resolved() -> str: return resolved()
    @staticmethod
    def label(mode: str | None = None) -> str: return mode_label(mode)


theme_manager = _ThemeManagerAPI()
