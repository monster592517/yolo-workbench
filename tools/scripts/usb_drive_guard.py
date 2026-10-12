# -*- coding: utf-8 -*-
"""usb_drive_guard.py — U盘(E盘) 掉线监测 + 自愈辅助.

E: 是一块 JMicron USB SSD (256GB), 事件日志里反复出现 disk 153 "IO retried"
(桥接芯片重置/掉线)。本守护:
  1. 每 15s 探测 E: 是否可访问 (项目根 <项目根>\\01_yolo_lab);
  2. 追踪 "磁盘 I/O 重试" (disk 153) 事件次数, 掉线/重试异常时打印提醒;
  3. E: 掉线后自动等待重新枚举; 若分区回来了但没有盘符 → 尝试自动恢复 E:
     (需要管理员; 普通权限下仅提示手工执行命令);
  4. 全程记录到 runs/logs/usb_drive_guard.log。

用法:  python tools/scripts/usb_drive_guard.py [--interval 15]
       (可加开机自启: 注册表 Run 或 计划任务)
退出: Ctrl+C
"""
import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
LOG = ROOT / "runs" / "logs" / "usb_drive_guard.log"
ROOT_DIR = ROOT
DRIVE = ROOT_DIR.drive or "E:"


def log(msg):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def drive_ok() -> bool:
    try:
        return ROOT_DIR.is_dir() and os.stat(str(ROOT_DIR)).st_ino is not None
    except Exception:
        return False


def disk_retry_events(since_min=10) -> int:
    """统计最近 N 分钟 disk 153 (I/O 重试) 事件数 (掉线特征)."""
    try:
        import winreg
        ps = (
            "$e = Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='disk'; "
            "Id=153; StartTime=(Get-Date).AddMinutes(%d)} -ErrorAction SilentlyContinue; "
            "$e.Count" % since_min
        )
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, errors="replace", timeout=45,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return int(r.stdout.strip() or 0)
    except Exception:
        return -1


def try_restore_drive_letter(disk_num=1):
    """分区在但无盘符时恢复 E: (需管理员; 失败仅提示)."""
    ps = (
        f"$p = Get-Partition -DiskNumber {disk_num} -ErrorAction SilentlyContinue | "
        "Where-Object { $_.DriveLetter -eq $null } | Select-Object -First 1; "
        "if ($p) { $p | Set-Partition -NewDriveLetter 'E'; 'restored' } else { 'no-partition-without-letter' }"
    )
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, errors="replace", timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = (r.stdout or "").strip()
        if "restored" in out:
            log("  ↑ 已自动恢复盘符 E: (分区重新枚举成功)")
            return True
        log(f"  → 分区未自动带回盘符 ({out or r.stderr or 'unknown'}); 若 E: 仍不可用, "
            f"请手动执行: disks 网> 或管理员运行: Get-Partition | Set-Partition -NewDriveLetter E")
    except Exception as e:
        log(f"  → 盘符恢复尝试失败: {e}")
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=15, help="探测间隔(秒)")
    ap.add_argument("--disk-number", type=int, default=1, help="U盘磁盘号 (Get-Disk 里 JMicron 那块的 Number)")
    args = ap.parse_args()

    log(f"USB 守护启动: 监控 {DRIVE} ({ROOT_DIR}) · 每 {args.interval}s · 日志 {LOG}")
    down_since = 0.0
    prev_ok = None
    while True:
        ok = drive_ok()
        if ok:
            if prev_ok is False:
                log(f"✅ {DRIVE} 已恢复在线 (掉线 {time.time() - down_since:.0f}s)")
                # 需要恢复盘符
                try_restore_drive_letter(args.disk_number)
            prev_ok = True
        else:
            if prev_ok is not False:
                log(f"⚠ {DRIVE} 无法访问 — U盘掉线/被拔出 (等待重枚举, 每分钟提示一次)")
                down_since = time.time()
            prev_ok = False
            if time.time() - down_since > 60:
                log(f"  … {DRIVE} 仍离线 (已 {time.time() - down_since:.0f}s)")
                down_since = time.time()
        n = disk_retry_events()
        if n < 0:
            break  # 事件查询不可用 → 退出 (环境异常安全)
        if n >= 3 and ok:
            log(f"  ! 最近 10 分钟 disk 153 (I/O重试) x{n} — 功率管理/温升嫌疑, 建议换口/换线/加压供电")
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("守护退出 (Ctrl+C)")
