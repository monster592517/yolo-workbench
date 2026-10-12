# -*- coding: utf-8 -*-
"""摄像头探测 — 黑帧检测与最优摄像头查找

背景: 部分摄像头在 物理挡片关闭 / 硬件开关关闭 / 驱动异常 时会"打开成功"但输出
      全黑帧 (平均亮度≈0)。本模块统一处理:
        - 枚举 索引 x 后端(DSHOW/MSMF/ANY) x MJPG 格式
        - 用平均亮度识别黑帧, 找到第一个真正输出画面的摄像头
        - 输出明确中文诊断, 供 检测页预览 / 检测启动 / 环境自检 / 环境页 使用
"""
from __future__ import annotations

import time
from typing import Optional, Tuple

BLACK_THRESHOLD = 3.0       # 平均亮度低于该值判定为黑帧
_BACKENDS = None
_BLACK_AUDITED = set()      # 已审计黑帧的索引 (每进程一次)

# 自拍摄像头名称特征 (内置/前置/自拍)
SELFIE_HINTS = ("integrated", "front", "selfie", "user facing", "userfacing",
                "内置", "前置", "自拍")


def _backends():
    """[(cv2后端, 名称)] — DSHOW 优先, MSMF/ANY 兜底"""
    global _BACKENDS
    if _BACKENDS is None:
        try:
            import cv2
            _BACKENDS = ((cv2.CAP_DSHOW, "DSHOW"), (cv2.CAP_MSMF, "MSMF"),
                         (cv2.CAP_ANY, "ANY"))
        except Exception:
            _BACKENDS = ((0, "ANY"),)
    return _BACKENDS


def is_black_frame(frame, threshold: float = BLACK_THRESHOLD) -> bool:
    """帧是否全黑 (平均亮度低于阈值)"""
    try:
        return float(frame.mean()) < threshold
    except Exception:
        return True


def camera_device_names() -> list:
    """Windows PnP 摄像头/图像设备名称清单 (内置/外接摄像头识别用)"""
    import subprocess
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-PnpDevice -Class Camera,Image -ErrorAction SilentlyContinue | "
             "Where-Object { $_.Status -eq 'OK' } | "
             "ForEach-Object { $_.FriendlyName }"],
            capture_output=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return [ln.strip() for ln in
                r.stdout.decode("utf-8", "ignore").splitlines() if ln.strip()]
    except Exception:
        return []


def match_selfie_names(names) -> bool:
    """名称清单中是否含自拍摄像头特征 (内置/前置/自拍)"""
    try:
        low = " ".join(str(n) for n in (names or [])).lower()
        return any(h in low for h in SELFIE_HINTS)
    except Exception:
        return False


def is_selfie_camera(index: int = 0) -> bool:
    """判断指定摄像头是否为自拍摄像头 (笔记本内置/前置)。

    优先用 Windows PnP 设备名匹配 (如 "Integrated Camera"); 无法获取名称时
    回退: 笔记本内置摄像头通常位于索引 0。
    """
    try:
        names = camera_device_names()
        if names:
            return match_selfie_names(names)
    except Exception:
        pass
    return index == 0


def _audit_black(index: int) -> None:
    """黑帧审计 (每索引每进程一次)"""
    if index in _BLACK_AUDITED:
        return
    _BLACK_AUDITED.add(index)
    try:
        from infrastructure.audit import audit
        audit("camera_black_stream",
              f"摄像头 {index} 打开成功但输出全黑帧: 检查物理挡片/开关/驱动",
              level="warn", module="camera_probe")
    except Exception:
        pass


def probe_camera(index: int = 0, frames: int = 3, max_time: float = 8.0) -> dict:
    """探测单个摄像头 (自动尝试后端与 MJPG), 返回报告 dict"""
    import cv2
    import numpy as np
    report = {"index": index, "opened": False, "backend": None, "mjpg": False,
              "w": 0, "h": 0, "frames": 0, "black_frames": 0, "mean": None,
              "error": None}
    t0 = time.time()
    for backend, bname in _backends():
        if time.time() - t0 > max_time:
            break
        for use_mjpg in (False, True):
            cap = None
            try:
                cap = cv2.VideoCapture(index, backend)
                if not cap.isOpened():
                    continue
                if use_mjpg:
                    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
                means = []
                for _ in range(frames):
                    if time.time() - t0 > max_time:
                        break
                    ret, f = cap.read()
                    if not ret:
                        break
                    means.append(float(f.mean()))
                report.update(opened=True, backend=bname, mjpg=use_mjpg,
                              w=w, h=h, frames=len(means),
                              black_frames=sum(1 for m in means if m < BLACK_THRESHOLD),
                              mean=float(np.mean(means)) if means else None)
                if report["frames"] and report["black_frames"] == 0:
                    return report    # 找到非黑帧即返回
            except Exception as e:
                report["error"] = f"{type(e).__name__}: {e}"
                break
            finally:
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
    if report["opened"] and report["frames"] and report["black_frames"] == report["frames"]:
        _audit_black(index)
    return report


def open_best_camera(max_check: int = 4, frames: int = 2,
                     max_time: float = 12.0) -> Tuple[Optional[object], dict]:
    """打开第一个输出非黑帧的摄像头

    返回 (cap, report):
      - cap 为已打开的 VideoCapture (调用方负责 release), 失败为 None
      - report.opened=False -> 全部摄像头打开失败
      - report.opened=True 且 black_frames==frames -> 有摄像头但画面全黑
    """
    import cv2
    t0 = time.time()
    last_report = None
    found_cap = None
    found_report = None
    for idx in range(max_check):
        if time.time() - t0 > max_time:
            break
        for backend, bname in _backends():
            if time.time() - t0 > max_time:
                break
            for use_mjpg in (False, True):
                cap = None
                try:
                    cap = cv2.VideoCapture(idx, backend)
                    if not cap.isOpened():
                        continue
                    if use_mjpg:
                        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
                    means = []
                    for _ in range(frames):
                        if time.time() - t0 > max_time:
                            break
                        ret, f = cap.read()
                        if not ret:
                            break
                        means.append(float(f.mean()))
                    report = {"index": idx, "opened": True, "backend": bname,
                              "mjpg": use_mjpg, "w": w, "h": h,
                              "frames": len(means),
                              "black_frames": sum(1 for m in means
                                                  if m < BLACK_THRESHOLD),
                              "mean": (sum(means) / len(means)) if means else None,
                              "error": None}
                    if report["frames"] and report["black_frames"] == 0:
                        # 找到可用摄像头: 保持打开 (不经过 finally 释放)
                        found_cap, found_report = cap, report
                        break
                    last_report = report
                    if report["frames"] and report["black_frames"] == report["frames"]:
                        _audit_black(idx)
                except Exception as e:
                    last_report = {"index": idx, "opened": False, "error":
                                   f"{type(e).__name__}: {e}"}
                    break
                finally:
                    if cap is not None and found_cap is None:
                        try:
                            cap.release()
                        except Exception:
                            pass
            if found_cap is not None:
                break
        if found_cap is not None:
            break
    if found_cap is not None:
        return found_cap, found_report
    return None, (last_report or {"index": 0, "opened": False, "error": None,
                                  "frames": 0, "black_frames": 0})


def format_diagnosis(report: dict) -> str:
    """把探测报告转为中文诊断提示"""
    if not report:
        return "摄像头不可用"
    if not report.get("opened"):
        return ("摄像头打开失败: 请检查设备/驱动/是否被其他程序占用"
                " (也可切换到 桌面/窗口 来源)")
    if not report.get("frames"):
        return ("摄像头已打开但无法读取画面: 驱动响应超时或被其他程序占用"
                " (也可切换到 桌面/窗口 来源)")
    if report.get("frames") and report.get("black_frames") == report.get("frames"):
        return ("摄像头已打开但画面全黑: 请检查 "
                "①物理隐私挡片/快门 ②摄像头硬件开关或F键 ③设备管理器驱动 "
                "④用系统相机App验证 (也可切换到 桌面/窗口 来源)")
    return "摄像头正常"


def diagnose() -> dict:
    """完整摄像头诊断 (供 排查向导 / 命令行工具 使用)

    返回结构化结果: 设备清单 / 隐私权限 / 探测报告 / 占用检测 / 排查清单。
    """
    import subprocess
    result = {
        "devices": [],
        "privacy": "未知",
        "probe": None,
        "in_use": [],
        "checklist": [],
    }
    # 1) 设备清单 (PnP)
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-PnpDevice -Class Camera,Image -ErrorAction SilentlyContinue | "
             "Where-Object { $_.Status -eq 'OK' } | "
             "ForEach-Object { $_.FriendlyName }"],
            capture_output=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        result["devices"] = [ln.strip() for ln in
                             r.stdout.decode("utf-8", "ignore").splitlines() if ln.strip()]
    except Exception:
        pass
    # 2) 隐私权限
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-ItemProperty 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\"
             "CapabilityAccessManager\\ConsentStore\\webcam' -ErrorAction SilentlyContinue).Value"],
            capture_output=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        result["privacy"] = r.stdout.decode("utf-8", "ignore").strip() or "未知"
    except Exception:
        pass
    # 3) 探测: 依次检查多个索引, 保留首个打开结果
    for idx in range(4):
        rep = probe_camera(idx, frames=2, max_time=6.0)
        if rep.get("opened"):
            result["probe"] = rep
            break
    # 4) 占用检测: 其他可能占用摄像头的进程
    try:
        import subprocess as _sp
        r = _sp.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process | Where-Object { $_.ProcessName -match "
             "'wechat|wxwork|QQ|Tencent|Teams|zoom|dingtalk|feishu|lark|Meeting|Camera' } | "
             "Select-Object -ExpandProperty ProcessName -Unique"],
            capture_output=True, timeout=20,
            creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
        result["in_use"] = [ln.strip() for ln in
                            r.stdout.decode("utf-8", "ignore").splitlines() if ln.strip()]
    except Exception:
        pass
    # 5) 排查清单
    rep = result["probe"] or {}
    if not rep.get("opened"):
        result["checklist"] = [
            "1. 设备管理器中摄像头是否存在且无感叹号 (Win+X -> 设备管理器 -> 摄像头)",
            "2. 是否被其他程序占用: 关闭 微信/腾讯会议/浏览器 等后重试",
            "3. 隐私权限: 设置 -> 隐私 -> 相机 -> 允许桌面应用访问",
            "4. 重启电脑后重试 (驱动状态恢复)",
        ]
    elif rep.get("frames") and rep.get("black_frames") == rep.get("frames"):
        result["checklist"] = [
            "1. 检查摄像头旁物理隐私挡片/快门是否关闭 (ThinkShutter 滑盖)",
            "2. 按键盘带摄像头图标的 F 键 (如 F8/F9) 确认未误关摄像头",
            "3. 打开系统\"相机\"App 测试: 同样黑屏 -> 硬件/驱动问题; 正常 -> 重启后再试",
            "4. 设备管理器 -> 摄像头 -> 卸载设备(勾选删除驱动) -> 重启自动重装",
            "5. 仍无效: 检查 BIOS 中摄像头是否启用 (开机按 F2/Del 进入)",
            "6. 以上均无效: 摄像头硬件故障, 建议送修或使用外接 USB 摄像头",
            "临时方案: 检测页切换到 桌面/窗口 来源可继续使用检测功能",
        ]
    else:
        result["checklist"] = ["摄像头工作正常"]
    return result


def format_diagnose_report(diag: dict) -> str:
    """把诊断结果格式化为可读文本"""
    lines = ["════════ 摄像头排查报告 ════════", ""]
    lines.append(f"检测到设备: {', '.join(diag['devices']) if diag['devices'] else '无'}")
    lines.append(f"系统隐私权限: {diag['privacy']}")
    rep = diag.get("probe") or {}
    if rep.get("opened"):
        lines.append(f"OpenCV 探测: Camera{rep.get('index')} {rep.get('w')}x{rep.get('h')} "
                     f"后端={rep.get('backend')} 读帧={rep.get('frames')} "
                     f"黑帧={rep.get('black_frames')} 平均亮度={rep.get('mean')}")
        if rep.get("frames") and rep.get("black_frames") == rep.get("frames"):
            lines.append("结论: 摄像头已打开但输出全黑帧 (硬件层问题)")
    else:
        lines.append("OpenCV 探测: 全部摄像头打开失败")
        lines.append("结论: 摄像头不可用 (驱动/占用/设备故障)")
    if diag["in_use"]:
        lines.append(f"疑似占用程序: {', '.join(diag['in_use'])}")
    lines.append("")
    lines.append("排查步骤:")
    lines.extend("  " + c for c in diag["checklist"])
    return "\n".join(lines)
