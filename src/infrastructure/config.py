# -*- coding: utf-8 -*-
"""Configuration — reads config.yaml, provides typed access"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import yaml as _yaml

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CONFIG_PATH = _PROJECT_ROOT / "config.yaml"


def get_project_root() -> Path:
    """当前项目根目录 (可被路径中心动态切换)"""
    return _PROJECT_ROOT


def set_project_root(root) -> None:
    """路径中心联动: 切换根目录, 重载 config.yaml 并补建运行目录 (幂等)"""
    global _PROJECT_ROOT, _CONFIG_PATH, _cfg
    _PROJECT_ROOT = Path(root).resolve()
    _CONFIG_PATH = _PROJECT_ROOT / "config.yaml"
    if Config._instance is not None:
        Config._instance._root = _PROJECT_ROOT
        Config._instance._raw = Config._load_yaml()
    _cfg = load_config()
    for _d in [_cfg.data_dir, _cfg.output_dir, _cfg.record_dir, _cfg.deploy_dir, _cfg.log_dir]:
        try:
            _d.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass


class ModelTask(str, Enum):
    DETECT = "detect"; OBB = "obb"; POSE = "pose"; SEGMENT = "segment"
    @classmethod
    def infer(cls, name: str) -> "ModelTask":
        n = name.lower()
        if "pose" in n: return cls.POSE
        if "obb" in n: return cls.OBB
        if "seg" in n: return cls.SEGMENT
        return cls.DETECT

class SourceType(str, Enum):
    CAMERA = "camera"; VIDEO = "video"; IMAGE = "image"; DESKTOP = "desktop"; WINDOW = "window"

class DeviceType(str, Enum):
    CPU = "cpu"; CUDA = "cuda"; MPS = "mps"


# ═══════════════════ Theme ═══════════════════

@dataclass
class ThemeColors:
    # 深黑主题默认值（与 theme_manager.DARK_COLORS 一致，集中管理；启动时由 apply_theme 覆盖）
    primary: str = "#3B82F6"; primary_hover: str = "#2563EB"
    accent: str = "#38BDF8"; bg: str = "#0A0A0A"
    bg2: str = "#141414"
    card_bg: str = "#171717"; text: str = "#EAEAEA"
    text_secondary: str = "#9CA3AF"; text_muted: str = "#6B7280"
    border: str = "#2A2A2A"; input_bg: str = "#0F0F0F"
    log_bg: str = "#080808"; log_text: str = "#E5E7EB"
    success: str = "#34D399"; success_hover: str = "#10B981"
    danger: str = "#F87171"; danger_hover: str = "#EF4444"
    purple: str = "#A78BFA"; purple_hover: str = "#8B5CF6"
    warning: str = "#FBBF24"
    success_bg: str = "#0F2A1E"
    danger_bg: str = "#2E1518"
    warning_bg: str = "#2E2410"
    # ★ 2026-09-23：**「压在实色底上的字」单独给一个语义 token**。
    #   之前 4 处在页里写死 `"#FFFFFF"`（按钮/小标签压在 purple / success / warning 实底上）
    #   ⇒ 换主题不跟着走，而且**浅色主题下就是白字压浅黄底**（本来就不可读）。
    #   这类"实色底上的字"必须有独立口径 —— 与 `nav_active_fg`（导航选中态专用）不是一回事。
    on_solid: str = "#FFFFFF"
    glass_highlight: str = "#4F7FFF"
    glow: str = "#60A5FA"
    sidebar_bg: str = "#0D0D0D"
    sidebar_hover: str = "#1F1F1F"
    nav_active_bg: str = "#1E2A45"
    nav_active_fg: str = "#FFFFFF"
    nav_hover_bg: str = "#161616"
    @property
    def primary_h(self): return self.primary_hover
    @property
    def text_sec(self): return self.text_secondary

@dataclass
class ThemeFonts:
    family: str = "Microsoft YaHei UI"; mono: str = "Consolas"
    size_sm: int = 9; size_base: int = 11; size_h3: int = 14
    size_h2: int = 22; size_h1: int = 28
    @property
    def sm(self): return (self.family, self.size_sm)
    @property
    def normal(self): return (self.family, self.size_base)
    @property
    def bold(self): return (self.family, self.size_base, "bold")
    @property
    def h3(self): return (self.family, self.size_h3, "bold")
    @property
    def h2(self): return (self.family, self.size_h2, "bold")
    @property
    def h1(self): return (self.family, self.size_h1, "bold")

@dataclass
class Theme:
    colors: ThemeColors = field(default_factory=ThemeColors)
    fonts: ThemeFonts = field(default_factory=ThemeFonts)


# ═══════════════════ Config Object ═══════════════════

class Config:
    """Loaded from config.yaml — all paths resolved to absolute"""

    _instance: Optional["Config"] = None

    def __init__(self):
        self._raw = self._load_yaml()
        self._root = _PROJECT_ROOT

    @staticmethod
    def _load_yaml() -> dict:
        if _CONFIG_PATH.exists():
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                return _yaml.safe_load(f) or {}
        return {}

    def get(self, key: str, default: Any = None) -> Any:
        """Dotted key access: config.get('training.epochs')"""
        keys = key.split(".")
        node = self._raw
        for k in keys:
            if isinstance(node, dict):
                node = node.get(k)
                if node is None:
                    return default
            else:
                return default
        return node

    def get_path(self, key: str, default: str = "") -> Path:
        """Resolve a path relative to project root"""
        val = self.get(key, default)
        return self._root / val if val else self._root

    def set(self, key: str, value: Any) -> None:
        """Set a dotted key in memory (e.g. 'share.pin'); persist manually."""
        keys = key.split(".")
        node = self._raw
        for k in keys[:-1]:
            nxt = node.get(k) if isinstance(node, dict) else None
            if not isinstance(nxt, dict):
                nxt = {}
                if isinstance(node, dict):
                    node[k] = nxt
            node = nxt
        if isinstance(node, dict):
            node[keys[-1]] = value

    # ── Convenience properties ──

    @property
    def name(self) -> str: return self.get("app.name", "YOLO 工作台")
    @property
    def version(self) -> str: return self.get("app.version", "5.5.0")

    @property
    def data_dir(self) -> Path: return self.get_path("paths.data_dir", "data")
    @property
    def output_dir(self) -> Path: return self.get_path("paths.output_dir", "runs/outputs")
    @property
    def record_dir(self) -> Path: return self.get_path("paths.record_dir", "runs/recordings")
    @property
    def deploy_dir(self) -> Path: return self.get_path("paths.deploy_dir", "runs/deploy_packages")
    @property
    def log_dir(self) -> Path: return self.get_path("paths.log_dir", "runs/logs")

    @property
    def pip_mirror(self) -> str: return self.get("mirrors.pip", "https://pypi.org/simple")
    @property
    def pip_no_proxy(self) -> bool: return bool(self.get("mirrors.no_proxy", False))
    @property
    def theme_mode(self) -> str: return self.get("ui.theme_mode", "auto")
    @property
    def torch_index(self) -> str: return self.get("mirrors.torch_gpu", "https://download.pytorch.org/whl/cu121")
    @property
    def torch_index_cpu(self) -> str: return self.get("mirrors.torch_cpu", "https://download.pytorch.org/whl/cpu")
    @property
    def torch_index_jetson(self) -> str: return self.get("mirrors.torch_jetson", "https://download.pytorch.org/whl/cpu")

    @property
    def camera_backend_name(self) -> str: return self.get("camera.backend", "DSHOW")
    @property
    def camera_max(self) -> int: return self.get("camera.max_check", 6)
    @property
    def camera_default(self) -> int: return self.get("camera.default_index", 0)

    @property
    def timeout_pip(self) -> int: return self.get("timeouts.pip_install", 900)
    @property
    def timeout_pip_quick(self) -> int: return self.get("timeouts.pip_quick", 600)
    @property
    def timeout_subprocess(self) -> int: return self.get("timeouts.subprocess", 8)
    @property
    def timeout_gpu(self) -> int: return self.get("timeouts.gpu_check", 5)
    @property
    def timeout_network_accept(self) -> int: return self.get("timeouts.network_accept", 1)
    @property
    def timeout_network_transfer(self) -> int: return self.get("timeouts.network_transfer", 30)

    # Training defaults
    def training(self, key: str, default: Any = None) -> Any:
        return self.get(f"training.{key}", default)

    # Detection defaults
    @property
    def detection_conf(self) -> float: return self.get("detection.conf", 0.5)
    @property
    def detection_device(self) -> str: return self.get("detection.device", "cpu")
    @property
    def max_display_fps(self) -> int: return self.get("detection.max_display_fps", 60)


    # Recording
    @property
    def recording_fps(self) -> float: return self.get("recording.fps", 20.0)


# ═══════════════════ Singleton access ═══════════════════

def load_config() -> Config:
    """Load or return the cached Config instance"""
    if Config._instance is None:
        Config._instance = Config()
    return Config._instance


def get_config() -> Config:
    return load_config()


_theme: Optional[Theme] = None

def get_theme() -> Theme:
    global _theme
    if _theme is None:
        _theme = Theme()
    return _theme


# Ensure output directories exist
_cfg = load_config()
for _d in [_cfg.data_dir, _cfg.output_dir, _cfg.record_dir, _cfg.deploy_dir, _cfg.log_dir]:
    _d.mkdir(parents=True, exist_ok=True)
