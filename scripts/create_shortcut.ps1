# 创建/更新桌面快捷方式: YOLO工作台 (无控制台窗口, pythonw 启动)
# 策略: 开发目录(永远最新代码)优先 -> 当前安装目录回退
#
# ⚠️ 同一个目标文件还有一条**免 COM 回退**：scripts/make_lnk.py（纯 ctypes 调系统 IShellLink，2026-10-02 加）。
#   存在理由：部分环境（含本机沙箱）禁用 COM，`New-Object -ComObject WScript.Shell` 会被安全策略拦掉，
#   本 ps1 就整条跑不动（`cscript + VBScript` 那条路也被当 LOLBin 拦）。分工：**本 ps1 是常规路径**，
#   make_lnk.py 是等价的免 COM 实现，两者写同一个 `桌面\YOLO工作台.lnk`。**改一侧务必对照另一侧**。
#
# ⚠️⚠️ 2026-10-02 事故备忘：**不要手工按 MS-SHLLINK 拼 .lnk 字节**。
#   试过一版「ShellLinkHeader + StringData，不带 IDList/LinkInfo」的手工构造：文件能被解析、
#   5 个字段读回全对、大小 510 B 看着正常 —— 但 Shell 执行时报 WinError 1155，
#   **双击毫无反应**。判据（读回字段）恰好绕开了唯一出错的维度（缺 HasLinkTargetIDList）。
#   ⇒ 一律交给系统 API 生成，并在写盘后用 LinkFlags 做反向断言（见 make_lnk.py）。
# 每次更新程序/构建安装包后运行本脚本, 保证快捷方式永远指向最新版本
$ErrorActionPreference = 'Stop'
# 根目录自动定位: 脚本所在目录优先 (迁移后文件夹位置随时可变)
$currentRoot = Split-Path -Parent $PSScriptRoot
function Test-ProjectRoot($p) {
    # ASCII-only 检测 (避免 PS5.1 按 ANSI 读取 UTF-8 中文乱码)
    return (Test-Path (Join-Path $p 'config.yaml')) -and (Test-Path (Join-Path $p 'src\gui\main_window.py'))
}
$root = $null
if (Test-ProjectRoot $currentRoot) {
    $root = $currentRoot
    Write-Host "[快捷方式] 目标: 当前目录 -> $root"
} else {
    $cands = @()
    $cands += "E:\AI_projects\01_yolo_lab"   # 2026-10-12 项目迁至 E 盘, 候选首位
    $cands += "$env:USERPROFILE\Desktop\yolo_lab"
    $cands += "<项目根>\01_yolo_lab"; $cands += "C:\yolo_lab"
    foreach ($c in $cands) {
        if ((Test-Path $c) -and (Test-ProjectRoot $c)) { $root = $c; break }
    }
    if (-not $root) {
        foreach ($g in (Get-ChildItem "$env:USERPROFILE\Desktop" -Directory -Filter "yolo_lab" -ErrorAction SilentlyContinue)) {
            if (Test-ProjectRoot $g.FullName) { $root = $g.FullName; break }
        }
    }
    if ($root) { Write-Host "[快捷方式] 目标: 自动定位 -> $root" }
}
if (-not $root) {
    Write-Host "[ERROR] 未找到 YOLO工作台 项目目录 (缺少 YOLO工作台.py / config.yaml)"
    exit 1
}

# 查找 pythonw.exe (3.13 -> 3.12 -> 3.11 -> 默认路径回退)
$pyw = $null
$pyLauncher = Join-Path $env:LOCALAPPDATA 'Programs\Python\Launcher\py.exe'
$pyCmd = $null
if (Get-Command py -ErrorAction SilentlyContinue) { $pyCmd = 'py' }
elseif (Test-Path $pyLauncher) { $pyCmd = $pyLauncher }
foreach ($v in @('3.13', '3.12', '3.11')) {
    if ($pyCmd) {
        $out = & $pyCmd "-$v" -c "import sys, os; print(os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'))" 2>$null
        if ($out -and (Test-Path $out)) { $pyw = $out; break }
    }
}
if (-not $pyw) {
    $pyw = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\pythonw.exe'
    if (-not (Test-Path $pyw)) {
        Write-Host "[ERROR] 未找到 Python 3.11+ (py -3.13/3.12/3.11), 请先安装 Python 3.13"
        exit 1
    }
}

# 读取目标目录版本号
$version = '?'
$cfg = Join-Path $root 'config.yaml'
if (Test-Path $cfg) {
    $m = Select-String -Path $cfg -Pattern 'version\s*:\s*["'']?([\w.]+)' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($m) { $version = $m.Matches[0].Groups[1].Value }
}

$desktop = [Environment]::GetFolderPath('Desktop')
$lnkPath = Join-Path $desktop 'YOLO工作台.lnk'
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($lnkPath)
$lnk.TargetPath = $pyw
$script = Join-Path $root 'YOLO工作台.py'
$lnk.Arguments = "-X utf8 `"$script`""   # 绝对路径+引号+utf8: 保证中文路径稳定启动
$lnk.WorkingDirectory = $root
$lnk.Description = "YOLO工作台 v$version - 目标检测全流程平台"
$icon = Join-Path $root 'assets\app_icon.ico'
if (Test-Path $icon) {
    $lnk.IconLocation = $icon
} else {
    $lnk.IconLocation = "$pyw,0"
}
$lnk.Save()
Write-Host "[OK] 桌面快捷方式已更新: $lnkPath"
Write-Host "     版本: v$version | 工作目录: $root"
Write-Host "     启动器: $pyw"
