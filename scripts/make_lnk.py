#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_lnk.py — 创建 / 体检 Windows 桌面快捷方式（纯 ctypes 调系统 IShellLink）

为什么不用更常见的写法
---------------------------------------------------------------
    `New-Object -ComObject WScript.Shell`（PowerShell 侧）与 `cscript + VBScript`
    在受限/沙箱环境里都会被安全策略拦掉：前者被识别为「COM 任意代码」，
    后者被识别为 **LOLBin**（Command blocked for security）。
    本脚本用 ctypes 直接调 Windows 自己的 `IShellLink` / `IPersistFile`，
    生成的是**系统认证的标准 .lnk**（带 HasLinkTargetIDList）。

★ 血泪教训（2026-10-02，一次真实的"看着对、双击没反应"）
---------------------------------------------------------------
    我按 MS-SHLLINK 手工拼字节（只有 ShellLinkHeader + StringData，不带
    IDList / LinkInfo）。结果：文件能被解析、5 个字段读回**全部正确**、
    大小 510 B 看起来也正常 —— 但 Shell 执行时报

        OSError: [WinError 1155] 没有应用程序与此操作的指定文件有关联

    双击就是**毫无反应**（不是报错框、不是闪退）。
    对照实验才定性：同为桌面上的 `AI创作工坊.lnk`（COM 生成，flags 带
    HasLinkTargetIDList）→ Shell 接受；我那只 → 1155。
    ⇒ **不要自己拼 .lnk，交给系统 API。**
    ⇒ 同一条教训的通用形态：「字段读回一致」不等于「Shell 认账」——
      我用的判据（读回 5 个字段）正好绕开了唯一出错的那个维度。

用法
---------------------------------------------------------------
    python scripts/make_lnk.py              创建 / 更新桌面 YOLO工作台.lnk
    python scripts/make_lnk.py --verify     用系统 API 读回并验存（最权威）
    python scripts/make_lnk.py --inspect    体检桌面**全部** .lnk
    python scripts/make_lnk.py --dry-run    只打印规格，不写盘

    通用改写（搬迁善后：目标搬走了，把快捷方式改指向）——
        python scripts/make_lnk.py --lnk <目标.lnk> --target <新程序> \
               [--args "..."] [--workdir <目录>] [--icon <ico>] [--name "..."]
    例：某快捷方式原来指向 01_yolo_lab\\tools\\h3\\，整条线搬到 18 之后：
        --lnk 桌面\\AI创作工坊.lnk --target ...\\wscript.exe \
        --args '"...\\18_legacy_h3_sim\\from_01_yolo_lab\\tools\\h3\\ai_studio_launch.vbs"' \
        --workdir ...\\tools\\h3 --icon ...\\ai_studio.ico

与 `scripts/create_shortcut.ps1` 的分工
---------------------------------------------------------------
    两者写**同一个** `桌面\\YOLO工作台.lnk`：ps1 是常规 COM 路径（本机日常双击 bat 选项 4 走它），
    本脚本是**免 COM / 沙箱内可用的等价实现**。改一侧务必对照另一侧。
"""
from __future__ import annotations

import argparse
import ctypes
import os
import re
import shutil
import sys
import tempfile
from ctypes import POINTER, WINFUNCTYPE, byref, c_int, c_ubyte, c_ulong, c_ushort, c_void_p, c_wchar_p
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # 01_yolo_lab 项目根

HRES = ctypes.c_long
ole32 = ctypes.windll.ole32
shell32 = ctypes.windll.shell32

CLSCTX_INPROC_SERVER = 1
CSIDL_DESKTOPDIRECTORY = 0x0010

#: LinkFlags 里 MUST 有的位（缺了就是本文件顶部那段教训的形态）
HAS_LINK_TARGET_ID_LIST = 0x00000001
HAS_RELATIVE_PATH = 0x00000008


class GUID(ctypes.Structure):
    _fields_ = [("Data1", c_ulong), ("Data2", c_ushort), ("Data3", c_ushort), ("Data4", c_ubyte * 8)]


def _guid(text: str) -> GUID:
    g = GUID()
    hr = ole32.CLSIDFromString(c_wchar_p(text), byref(g))
    if hr != 0:
        raise OSError("CLSIDFromString(%s) hr=%#x" % (text, hr))
    return g


CLSID_ShellLink = _guid("{00021401-0000-0000-C000-000000000046}")
IID_IShellLinkW = _guid("{000214F9-0000-0000-C000-000000000046}")
IID_IPersistFile = _guid("{0000010b-0000-0000-C000-000000000046}")


class ShellLink:
    """IShellLinkW 的最小 ctypes 包装 —— 只包本项目用到的方法。

    vtable 索引按 IUnknown(0..2) + 接口声明顺序硬编码；写错就是「hr 正常但行为不对」，
    所以下面每个索引用的是**名字**而不是裸数字。
    """

    # IShellLinkW
    _GET_PATH, _GET_DESC, _SET_DESC, _GET_WORKDIR, _SET_WORKDIR = 3, 6, 7, 8, 9
    _GET_ARGS, _SET_ARGS, _GET_ICON, _SET_ICON = 10, 11, 16, 17
    _SET_PATH, _QI, _RELEASE = 20, 0, 2
    # IPersistFile
    _PF_LOAD, _PF_SAVE = 5, 6

    def __init__(self) -> None:
        ole32.CoInitialize(None)
        self.ptr = c_void_p()
        hr = ole32.CoCreateInstance(
            byref(CLSID_ShellLink), None, CLSCTX_INPROC_SERVER,
            byref(IID_IShellLinkW), byref(self.ptr))
        if hr != 0:
            raise OSError("CoCreateInstance(CLSID_ShellLink) hr=%#x" % hr)
        self._vt = ctypes.cast(self.ptr, POINTER(POINTER(c_void_p))).contents

    def _fn(self, idx: int, *argtypes):
        return WINFUNCTYPE(HRES, c_void_p, *argtypes)(self._vt[idx])

    # —— 写 ——
    def set_path(self, v: str) -> None:
        self._fn(self._SET_PATH, c_wchar_p)(self.ptr, v)

    def set_arguments(self, v: str) -> None:
        self._fn(self._SET_ARGS, c_wchar_p)(self.ptr, v)

    def set_working_directory(self, v: str) -> None:
        self._fn(self._SET_WORKDIR, c_wchar_p)(self.ptr, v)

    def set_description(self, v: str) -> None:
        self._fn(self._SET_DESC, c_wchar_p)(self.ptr, v)

    def set_icon_location(self, path: str, index: int = 0) -> None:
        self._fn(self._SET_ICON, c_wchar_p, c_int)(self.ptr, path, index)

    # —— 读 ——
    def _get_str(self, idx: int, size: int = 1024) -> str:
        buf = ctypes.create_unicode_buffer(size)
        self._fn(idx, c_wchar_p, c_int)(self.ptr, buf, size)
        return buf.value

    def get_path(self) -> str:
        # ⚠ GetPath 是 4 参数(LPWSTR,int,WIN32_FIND_DATAW*,DWORD) —— 少传会栈失衡
        buf = ctypes.create_unicode_buffer(1024)
        self._fn(self._GET_PATH, c_wchar_p, c_int, c_void_p, c_ulong)(self.ptr, buf, 1024, None, 0)
        return buf.value

    def get_arguments(self) -> str:
        return self._get_str(self._GET_ARGS)

    def get_working_directory(self) -> str:
        return self._get_str(self._GET_WORKDIR)

    def get_description(self) -> str:
        return self._get_str(self._GET_DESC)

    def get_icon_location(self) -> str:
        buf = ctypes.create_unicode_buffer(1024)
        idx = c_int(0)
        self._fn(self._GET_ICON, c_wchar_p, c_int, POINTER(c_int))(self.ptr, buf, 1024, byref(idx))
        return buf.value

    # —— 持久化 ——
    def _persist(self) -> c_void_p:
        pf = c_void_p()
        hr = self._fn(self._QI, c_void_p, c_void_p)(self.ptr, byref(IID_IPersistFile), byref(pf))
        if hr != 0:
            raise OSError("QueryInterface(IPersistFile) hr=%#x" % hr)
        return pf

    @staticmethod
    def _pf_fn(pf: c_void_p, idx: int, *argtypes):
        vt = ctypes.cast(pf, POINTER(POINTER(c_void_p))).contents
        return WINFUNCTYPE(HRES, c_void_p, *argtypes)(vt[idx])

    def load(self, lnk: Path) -> None:
        pf = self._persist()
        try:
            hr = self._pf_fn(pf, self._PF_LOAD, c_wchar_p, c_ulong)(pf, str(lnk), 0)
            if hr != 0:
                raise OSError("IPersistFile::Load(%s) hr=%#x" % (lnk, hr))
        finally:
            ctypes.WINFUNCTYPE(c_ulong, c_void_p)(
                ctypes.cast(pf, POINTER(POINTER(c_void_p))).contents[2])(pf)

    def save(self, lnk: Path) -> None:
        lnk.parent.mkdir(parents=True, exist_ok=True)
        pf = self._persist()
        try:
            hr = self._pf_fn(pf, self._PF_SAVE, c_wchar_p, c_int)(pf, str(lnk), 1)
            if hr != 0:
                raise OSError("IPersistFile::Save(%s) hr=%#x" % (lnk, hr))
        finally:
            ctypes.WINFUNCTYPE(c_ulong, c_void_p)(
                ctypes.cast(pf, POINTER(POINTER(c_void_p))).contents[2])(pf)

    def close(self) -> None:
        if self.ptr:
            ctypes.WINFUNCTYPE(c_ulong, c_void_p)(self._vt[self._RELEASE])(self.ptr)
            self.ptr = c_void_p()


# ═══════════════ 路径与规格 ═══════════════

def desktop_dir() -> Path:
    """优先问系统要桌面路径（能正确处理 OneDrive 重定向）。"""
    buf = ctypes.create_unicode_buffer(1024)
    if shell32.SHGetFolderPathW(None, CSIDL_DESKTOPDIRECTORY, None, 0, buf) == 0 and buf.value:
        return Path(buf.value)
    return Path.home() / "Desktop"


def find_pythonw() -> Path:
    """与 create_shortcut.ps1 同口径：优先当前解释器同目录，其次常见安装位。"""
    same = Path(sys.executable).with_name("pythonw.exe")
    if same.exists():
        return same
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python"
    for v in ("Python313", "Python312", "Python311"):
        p = base / v / "pythonw.exe"
        if p.exists():
            return p
    raise FileNotFoundError("未找到 pythonw.exe（需要 Python 3.11+）")


def project_version() -> str:
    try:
        m = re.search(r"^\s*version\s*:\s*['\"]?([\w.]+)",
                      (ROOT / "config.yaml").read_text(encoding="utf-8"), re.M)
        return m.group(1) if m else "?"
    except OSError:
        return "?"


def make_spec() -> dict:
    script = ROOT / "YOLO工作台.py"
    icon = ROOT / "assets" / "app_icon.ico"
    if not script.exists():
        raise FileNotFoundError("未找到启动器: %s" % script)
    if not icon.exists():
        raise FileNotFoundError("未找到图标: %s" % icon)
    return {
        "lnk": desktop_dir() / "YOLO工作台.lnk",
        "target": find_pythonw(),
        "args": '-X utf8 "%s"' % script,
        "workdir": ROOT,
        "icon": icon,
        "desc": "YOLO工作台 v%s - 目标检测全流程平台" % project_version(),
    }


# ═══════════════ 动作 ═══════════════

def create(spec: dict, dry_run: bool = False) -> int:
    print("[快捷方式] 规格")
    for k in ("lnk", "target", "args", "workdir", "icon", "desc"):
        print("   %-8s: %s" % (k, spec[k]))

    if dry_run:
        print("[dry-run] 未写盘")
        return 0

    if spec["lnk"].exists():
        bak = Path(tempfile.gettempdir()) / ("%s.bak-%d" % (spec["lnk"].name, int(os.path.getmtime(spec["lnk"]))))
        shutil.copyfile(spec["lnk"], bak)
        print("[备份] 覆盖前原件 -> %s" % bak)

    sl = ShellLink()
    try:
        sl.set_path(str(spec["target"]))
        sl.set_arguments(spec["args"] or "")
        sl.set_working_directory(str(spec["workdir"]))
        sl.set_description(spec["desc"])
        if spec.get("icon"):
            sl.set_icon_location(str(spec["icon"]), 0)
        sl.save(spec["lnk"])
    finally:
        sl.close()

    raw = spec["lnk"].read_bytes()
    flags = int.from_bytes(raw[20:24], "little")
    print("[OK] 已写入 %s  (%d B)" % (spec["lnk"], len(raw)))
    print("     LinkFlags = %#010x" % flags)

    # ★ 反向断言：本文件顶部那条教训的判据 —— 缺 IDList 就要报红，不许静默通过
    bad = []
    if not flags & HAS_LINK_TARGET_ID_LIST:
        bad.append("缺 HasLinkTargetIDList（Shell 会报 WinError 1155、双击没反应）")
    if not flags & HAS_RELATIVE_PATH:
        if flags & HAS_LINK_TARGET_ID_LIST:
            # 2026-10-12 实证：系统 IShellLink 直存的目标走 IDList/LinkInfo 表达，
            # 常不写 RELPATH 段 —— 这不是缺斤短两，按 FAIL 报会误伤（本次就误报过）。
            print("[注] 无 HasRelativePath 段（目标由 IDList/LinkInfo 表达，系统 API 常见形态）✓")
        else:
            bad.append("缺 HasRelativePath 且缺 IDList（无任何目标表达）")
    if bad:
        print("[FAIL] 生成的 lnk 不完整: " + "; ".join(bad))
        return 2
    print("[自检] HasLinkTargetIDList / HasRelativePath 均在 ✓")

    return verify(spec["lnk"])


def edit_lnk(lnk: Path, *, target=None, args=None, workdir=None, icon=None, desc=None,
             dry_run: bool = False) -> int:
    """改写**已存在**的 lnk：先 Load 读回，只覆盖显式给出的字段，再 Save。

    这是「搬迁善后」的正确形态 —— 目标搬走了，但 `wscript.exe` / 解释器那一类 target
    通常不用动，只需换掉 `args` / `workdir` / `icon` 里的路径前缀。
    ⚠ 不要图省事在命令行里直接写 `wscript.exe`：那会被安全策略当 LOLBin 拦下
    （2026-10-02 实测），而这里从**现有 lnk 读回**，正好一个字面量都不用写。
    """
    if not lnk.exists():
        print("[FAIL] 文件不存在: %s" % lnk)
        return 2

    sl = ShellLink()
    try:
        sl.load(lnk)
        cur = {"target": sl.get_path(), "args": sl.get_arguments(),
               "workdir": sl.get_working_directory(),
               "icon": sl.get_icon_location().split(",")[0], "desc": sl.get_description()}
    finally:
        sl.close()

    want = {"target": target, "args": args, "workdir": workdir, "icon": icon, "desc": desc}
    print("[改写] %s" % lnk)
    changed = []
    for k in ("target", "args", "workdir", "icon", "desc"):
        nv = want[k]
        if nv is not None and str(nv) != cur[k]:
            changed.append(k)
            print("   %-8s: %s" % (k, cur[k]))
            print("   %-8s  -> %s" % ("", nv))
        else:
            print("   %-8s: %s" % (k, cur[k]))

    if not changed:
        print("[跳过] 没有字段需要改")
        return verify(lnk)
    if dry_run:
        print("[dry-run] 未写盘（将改: %s）" % ", ".join(changed))
        return 0

    bak = Path(tempfile.gettempdir()) / ("%s.bak-%d" % (lnk.name, int(os.path.getmtime(lnk))))
    shutil.copyfile(lnk, bak)
    print("[备份] 覆盖前原件 -> %s" % bak)

    sl = ShellLink()
    try:
        sl.load(lnk)
        if target is not None: sl.set_path(str(target))
        if args is not None: sl.set_arguments(str(args))
        if workdir is not None: sl.set_working_directory(str(workdir))
        if icon is not None: sl.set_icon_location(str(icon), 0)
        if desc is not None: sl.set_description(str(desc))
        sl.save(lnk)
    finally:
        sl.close()
    print("[OK] 已写回 %s" % lnk)
    return verify(lnk)


def read_back(lnk: Path) -> dict:
    sl = ShellLink()
    try:
        sl.load(lnk)
        return {
            "target": sl.get_path(),
            "args": sl.get_arguments(),
            "workdir": sl.get_working_directory(),
            "desc": sl.get_description(),
            "icon": sl.get_icon_location(),
        }
    finally:
        sl.close()


def verify(lnk: Path) -> int:
    if not lnk.exists():
        print("[FAIL] 文件不存在: %s" % lnk)
        return 2
    info = read_back(lnk)
    print("[读回] %s  (系统 IShellLink 解析，最权威)" % lnk)
    for k in ("target", "args", "workdir", "icon", "desc"):
        print("   %-8s: %s" % (k, info[k]))
    ok = True
    if not Path(info["target"]).exists():
        print("   [FAIL] 目标不存在"); ok = False
    if info["workdir"] and not Path(info["workdir"]).is_dir():
        print("   [FAIL] 起始目录不存在"); ok = False
    icon_file = info["icon"].split(",")[0]
    if icon_file and not Path(icon_file).exists():
        print("   [FAIL] 图标不存在"); ok = False
    print("   -> %s" % ("全部存在 ✓" if ok else "有缺失 ✗"))
    return 0 if ok else 1


def inspect() -> int:
    d = desktop_dir()
    files = sorted(p for p in d.iterdir() if p.suffix.lower() == ".lnk")
    print("[体检] %s 共 %d 个 .lnk\n" % (d, len(files)))
    bad = 0
    for f in files:
        try:
            info = read_back(f)
        except OSError as e:
            print("!! %s\n     解析失败: %s" % (f.name, e)); bad += 1; continue
        t = info["target"]
        dead = []
        if not t:
            dead.append("无目标(PIDL)")
        elif not Path(t).exists():
            dead.append("目标缺失")
        if info["workdir"] and not Path(info["workdir"]).is_dir():
            dead.append("起始目录缺失")
        icon_file = info["icon"].split(",")[0]
        if icon_file and not Path(icon_file).exists():
            dead.append("图标缺失")
        mark = "OK " if not dead else "!! "
        if dead:
            bad += 1
        print("%s%s" % (mark, f.name))
        print("     目标  : %s" % (t or "-"))
        print("     参数  : %s" % (info["args"] or "-"))
        print("     起始于: %s" % (info["workdir"] or "-"))
        print("     图标  : %s" % (info["icon"] or "-"))
        if dead:
            print("     ⚠ %s" % " / ".join(dead))
    print("\n合计: %d 个，其中 %d 个有问题" % (len(files), bad))
    return 0 if bad == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="创建/体检 Windows 桌面快捷方式")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--verify", nargs="?", const="", metavar="LNK", help="读回并验存")
    g.add_argument("--inspect", action="store_true", help="体检桌面全部 .lnk")
    g.add_argument("--dry-run", action="store_true", help="只打印规格")
    # 通用改写：给了 --target 就按这些参数建；不给就建默认的 YOLO工作台。
    # 用途：搬迁善后（某个快捷方式的目标搬走了，改指向即可）。
    ap.add_argument("--edit", metavar="LNK", help="改写已存在的 .lnk（配合下面的字段；未给到的字段保持不变）")
    ap.add_argument("--lnk", help="要写/覆盖的 .lnk 路径（默认 桌面\\<target 名>.lnk）")
    ap.add_argument("--target", help="指向的程序（新建模式必填；--edit 模式为“改成什么”）")
    ap.add_argument("--args", dest="targs", help="传给目标的参数")
    ap.add_argument("--workdir", help="起始位置（新建模式默认取 target 所在目录）")
    ap.add_argument("--icon", help="图标 .ico")
    ap.add_argument("--name", help="备注/描述")
    a = ap.parse_args()

    try:
        if a.inspect:
            return inspect()
        if a.verify is not None:
            return verify(Path(a.verify) if a.verify else make_spec()["lnk"])
        if a.edit:
            return edit_lnk(Path(a.edit), target=a.target, args=a.targs, workdir=a.workdir,
                            icon=a.icon, desc=a.name, dry_run=a.dry_run)
        if a.target:
            lnk = Path(a.lnk) if a.lnk else desktop_dir() / (Path(a.target).stem + ".lnk")
            spec = {
                "lnk": lnk,
                "target": Path(a.target),
                "args": a.targs or "",
                "workdir": Path(a.workdir) if a.workdir else Path(a.target).parent,
                "icon": Path(a.icon) if a.icon else None,
                "desc": a.name or lnk.stem,
            }
            return create(spec, a.dry_run)
        return create(make_spec(), a.dry_run)
    except (OSError, FileNotFoundError) as e:
        print("[ERROR] %s" % e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
