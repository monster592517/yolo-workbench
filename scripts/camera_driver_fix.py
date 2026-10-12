# -*- coding: utf-8 -*-
"""摄像头驱动修复工具 — 一键把摄像头切换到微软通用 UVC 驱动

背景: 2026-08-02 实测, Lenovo System Driver Update (26.6.0.5) 更新 SunplusIT
专用驱动后, 摄像头打开成功但输出全黑帧 (亮度≈0)。切换到微软通用 UVC 驱动
(usbvideo.inf) 后画面恢复正常。

用法: python scripts/camera_driver_fix.py [--check]   (--check 仅检查不修改)
"""
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass


def _ps(script: str) -> str:
    r = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                       capture_output=True, timeout=60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.stdout.decode("utf-8", "ignore").strip()


def camera_driver_state() -> dict:
    """返回摄像头当前驱动信息"""
    out = _ps(
        "Get-PnpDevice -Class Camera -Status OK | Where-Object { "
        "$_.FriendlyName -eq 'Integrated Camera' -or "
        "$_.FriendlyName -notmatch 'IR' -and $_.FriendlyName -match 'Camera' } | "
        "Select-Object -First 1 | "
        "ForEach-Object { "
        "$p = Get-PnpDeviceProperty -InstanceId $_.InstanceId -KeyName "
        "DEVPKEY_Device_DriverInfPath,DEVPKEY_Device_DriverProvider; "
        "Write-Host ($_.FriendlyName + '|' + ($p | Where-Object KeyName -eq "
        "'DEVPKEY_Device_DriverInfPath').Data + '|' + "
        "($p | Where-Object KeyName -eq 'DEVPKEY_Device_DriverProvider').Data) }")
    parts = out.split("|") if out else []
    return {"name": parts[0] if parts else "?", "inf": parts[1] if len(parts) > 1 else "?",
            "provider": parts[2] if len(parts) > 2 else "?"}


def switch_to_uvc(check_only: bool = False) -> bool:
    """切换到微软通用 UVC 驱动; 返回是否成功"""
    state = camera_driver_state()
    print(f"当前驱动: {state['name']} | INF={state['inf']} | 供应商={state['provider']}")
    if "usbvideo" in state["inf"].lower() and "Microsoft" in state["provider"]:
        print("已是微软通用 UVC 驱动, 无需修改 ✓")
        return True
    if check_only:
        print("(--check) 当前为专用驱动, 需切换")
        return False
    # 卸载专用驱动包 (oemNN.inf), 删除后扫描让 Windows 用通用驱动
    inf = state["inf"]
    r = subprocess.run(["pnputil", "/delete-driver", inf, "/uninstall", "/force"],
                       capture_output=True, timeout=120)
    print("卸载专用驱动:", r.stdout.decode("utf-8", "ignore").strip()[-120:])
    subprocess.run(["pnputil", "/scan-devices"], capture_output=True, timeout=120)
    import time
    time.sleep(6)
    new = camera_driver_state()
    print(f"切换后: INF={new['inf']} | 供应商={new['provider']}")
    return "usbvideo" in new["inf"].lower() and "Microsoft" in new["provider"]


def main() -> int:
    check_only = "--check" in sys.argv
    ok = switch_to_uvc(check_only=check_only)
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
        from infrastructure.audit import audit
        audit("camera_driver_fix",
              f"切换UVC驱动 check={check_only} ok={ok}",
              level="info" if ok else "warn", module="camera_driver_fix")
    except Exception:
        pass
    print("修复完成 ✓" if ok else "未完成, 请查看上方输出")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
