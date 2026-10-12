# -*- coding: utf-8 -*-
"""证明自检**能失败** —— 三个注入点，逐个验。

跑法:
    python tools/qc/verify_smoke_can_fail.py

裁决: 退出码 0=三个注入都被自检抓到 / 1=有注入没被抓到（那条检查形同虚设）
      / 2=夹具对不上（代码变了，本脚本要跟着更新）/ 3=恢复失败（必须手工修回）

★ 为什么非做不可：**一个永远通过的检查比没有检查更坏** —— 它会让人以为
  "这一块有人守着"。而"恒真"最常见的成因不是逻辑错，是**判据吃到了自己的
  说明书**（docstring / 注释里的字面量）：扫描器读到自己写的禁例，
  于是永远命中、永远通过。

★ 恢复保证：每个用例都在 try/finally 里原样写回，并校验 sha256 一致；
  恢复失败一律 rc=3 并明确告知"文件已被改动"。
★ 全过程只碰 src/ 下两个文件，不生成部署包、不改配置。
"""
import hashlib
import os
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ★ 上溯三层: tools/qc/verify_smoke_can_fail.py -> tools -> 项目根
#   （曾写成两层，于是目标文件被判"不存在"；上溯层数要数清）
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PAGE = os.path.join(ROOT, "src", "gui", "pages", "deployment_page.py")
DEPLOYER = os.path.join(ROOT, "src", "core", "deployer.py")
SMOKE_PAGE = os.path.join(ROOT, "tools", "qc", "smoke_deploy_page.py")
SMOKE_PKG = os.path.join(ROOT, "tools", "qc", "smoke_deploy_package.py")
PY = sys.executable


def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


CASES = [
    {
        "name": "部署页把 dry / live 两态合并成一个标签",
        "path": PAGE,
        "good": '    return "dry" if dry_run else "live"',
        "bad": '    return "dry"',
        "smoke": SMOKE_PAGE,
        "expect": None,
    },
    {
        # ★ 2026-09-18 更新：旧的注入点是"状态块退回读 get_config()"，但状态块
        #   已经重写成读 `effective_control()` 了，那个片段不存在 ⇒ 本脚本自己
        #   报 rc=2「代码变了，本脚本必须跟着更新」。**这正是它该有的反应**：
        #   夹具失效必须响，不能悄悄算过。
        #   新注入点改为"在状态块里再读一次 config.yaml 的 control.enabled" ——
        #   对应的是"同一件事有两个真源"这个真实故障（两边各自都能跑通，只有
        #   摆在一起看才发现界面在说另一件事）。
        "name": "部署页重新引入第二个真源（再读一次 config.yaml 的 control.*）",
        "path": PAGE,
        "good": ('            from core import control_preset as cp\n'
                 '            ctl, src, warns = cp.effective_control()\n'),
        "bad": ('            from core import control_preset as cp\n'
                '            ctl, src, warns = cp.effective_control()\n'
                '            _en2 = get_config().get("control.enabled", False)\n'),
        "smoke": SMOKE_PKG,
        "expect": "部署页",
    },
    {
        "name": "生成包时无视预设（写回出厂默认）",
        "path": DEPLOYER,
        "good": ('        if control is None:\n'
                 '            ctl, source = copy.deepcopy(DEFAULT_CONTROL_JSON), '
                 'source or "出厂默认"\n'
                 '        else:\n'
                 '            ctl = copy.deepcopy(control)\n'),
        "bad": ('        ctl = copy.deepcopy(DEFAULT_CONTROL_JSON)\n'),
        "smoke": SMOKE_PKG,
        "expect": "door",
    },
]


def run_case(case):
    path = case["path"]
    with open(path, "r", encoding="utf-8") as f:
        orig = f.read()
    h0 = sha(orig)

    if case["good"] not in orig:
        print("  ！找不到要破坏的代码段 —— 代码变了，本脚本必须跟着更新")
        print("    文件: %s" % os.path.relpath(path, ROOT))
        return 2

    broken = orig.replace(case["good"], case["bad"], 1)
    if broken == orig:
        print("  ！替换没有生效（内容相同？）")
        return 2

    rc = None
    fails = []
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(broken)
        r = subprocess.run([PY, case["smoke"]], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        rc = r.returncode
        out = (r.stdout or "") + (r.stderr or "")
        fails = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("[x]")]
    finally:
        with open(path, "w", encoding="utf-8") as f:
            f.write(orig)
        with open(path, "r", encoding="utf-8") as f:
            now = f.read()
        same = (sha(now) == h0)
    if not same:
        print("  ！恢复失败，文件已被改动，必须手工修回: %s" % path)
        return 3

    print("  坏版本下自检退出码 = %s（期望 1），报出失败 %d 条" % (rc, len(fails)))
    for ln in fails[:5]:
        print("     " + ln)

    if rc != 1:
        print("  ✗ 破坏了代码它却仍然通过 -> 这条检查形同虚设")
        return 1
    if case["expect"] and not any(case["expect"] in ln for ln in fails):
        print("  ✗ 自检虽然失败，但**失败原因不是这一条**（期望失败项里出现 %r）"
              % case["expect"])
        print("     —— 这种「歪打正着」同样不能算数：检查可能压根没在跑")
        return 1
    print("  ✓ 被抓到了，且失败原因正是这条检查")
    return 0


def main():
    print("自检的自检：证明每条检查都能失败")
    if not os.path.isfile(PAGE) or not os.path.isfile(DEPLOYER):
        print("[无法运行] 找不到目标源文件")
        return 2
    bad = 0
    for i, case in enumerate(CASES, 1):
        print("\n[%d/%d] %s" % (i, len(CASES), case["name"]))
        print("    文件: %s" % os.path.relpath(case["path"], ROOT))
        rc = run_case(case)
        if rc != 0:
            bad += 1
            if rc == 3:
                print("\n恢复失败，立即停止（避免继续在坏文件上操作）")
                return 3
    print("\n结果: %d/%d 个注入被自检抓到" % (len(CASES) - bad, len(CASES)))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
