@echo off
chcp 936 >nul
title YOLO工作台
cd /d "%~dp0"

rem ===========================================================
rem  YOLO工作台 - 统一入口
rem  2026-10-01: 原先 6 个散装 bat 装依赖/配环境/建快捷方式/更新快捷方式/
rem  路径适配/启动  合并到这里 —— 一个入口 + 一个菜单。
rem  直接用  YOLO工作台.bat 1  之类可跳过菜单 见文件末的参数说明 。
rem ===========================================================

set "SEL=%~1"
if not "%SEL%"=="" goto :dispatch

rem --- 找 Python 3.11+（py 启动器优先）---
set "PYEXE="
set "PYCMD=py"
where py >nul 2>nul || set "PYCMD=%LOCALAPPDATA%\Programs\Python\Launcher\py.exe"
for %%v in (3.13 3.12 3.11) do (
  if not defined PYEXE (
    for /f "delims=" %%p in ('"%PYCMD%" -%%v -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%p"
  )
)
if not defined PYEXE ( where python >nul 2>nul && set "PYEXE=python" )

echo.
echo   ==============================================
echo               YOLO 工作台   -   统一入口
echo        数据 -^> 标注 -^> 训练 -^> 检测     一条龙
echo   ==============================================
echo.
if defined PYEXE (
  "%PYEXE%" -c "import sys;v=sys.version.split()[0];ok=sys.version_info[:2]>=(3,11);print('   Python '+v+('' if ok else '   [警告] 低于 3.11, 标注工具不可用'))" 2>nul
) else (
  echo   [警告] 未找到 Python —— 除"路径适配"外都需要它
  echo          请安装 Python 3.11+ 并勾选 Add to PATH
)
echo.
echo   1) 启动工作台          ^(默认, 直接回车^)
echo   2) 安装依赖            ^(走镜像回退, 较慢^)
echo   3) 环境自检            ^(依赖/磁盘/CUDA/数据集/模型^)
echo   4) 更新桌面快捷方式    ^(指向当前目录, 迁移后必做^)
echo   5) 路径适配            ^(数据集/快捷方式/运行目录^)
echo   0) 退出
echo.
set "SEL=1"
set /p "SEL=  请选择 [1]: "
if "%SEL%"=="" set "SEL=1"

:dispatch
if not defined PYEXE (
  where py >nul 2>nul && (set "PYCMD=py") || (set "PYCMD=%LOCALAPPDATA%\Programs\Python\Launcher\py.exe")
  for %%v in (3.13 3.12 3.11) do (
    if not defined PYEXE (
      for /f "delims=" %%p in ('"%PYCMD%" -%%v -c "import sys;print(sys.executable)" 2^>nul') do set "PYEXE=%%p"
    )
  )
  if not defined PYEXE ( where python >nul 2>nul && set "PYEXE=python" )
)

if "%SEL%"=="0" exit /b 0
if "%SEL%"=="2" goto :install
if "%SEL%"=="3" goto :envcheck
if "%SEL%"=="4" goto :shortcut
if "%SEL%"=="5" goto :adapt
goto :launch

rem ----------- 1  启动工作台 -----------
:launch
if not defined PYEXE (
  echo [错误] 未找到 Python 3.11+ ^(启动工作台必需^)
  pause & exit /b 1
)
echo.
echo   正在启动, 请稍候...
rem 用通配符取入口脚本 —— 避开 bat 里写中文文件名 编码风险
for /f "delims=" %%i in ('dir /b YOLO*.py 2^>nul') do (
  "%PYEXE%" "%%i"
  goto :done
)
echo [错误] 未找到入口脚本 YOLO*.py
goto :done

rem ----------- 2  安装依赖 -----------
:install
if not defined PYEXE ( echo [错误] 未找到 Python 3.11+ & pause & exit /b 1 )
echo.
echo   安装依赖^(官方源->清华->阿里 自动回退^), 首次约几分钟...
"%PYEXE%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo   官方源失败, 换清华镜像重试...
  "%PYEXE%" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
)
"%PYEXE%" -c "import ultralytics,cv2,torch,PIL,yaml;print('  [OK] 核心依赖可用')"
goto :done

rem ----------- 3  环境自检 -----------
:envcheck
if not defined PYEXE ( echo [错误] 未找到 Python 3.11+ & pause & exit /b 1 )
echo.
"%PYEXE%" -X utf8 scripts\setup_env.py --check
echo.
echo   (要自动修复缺失项, 可运行: python scripts\setup_env.py --auto)
goto :done

rem ----------- 4  更新桌面快捷方式 -----------
:shortcut
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\create_shortcut.ps1"
if errorlevel 1 (
  echo   [错误] 创建失败, 请确认已装 Python 3.11+
) else (
  echo   [OK] 桌面快捷方式已指向当前目录
)
goto :done

rem ----------- 5  路径适配 -----------
:adapt
if not defined PYEXE ( echo [错误] 未找到 Python 3.11+ & pause & exit /b 1 )
echo.
"%PYEXE%" "%~dp0scripts\adapt_paths.py"
goto :done

:done
echo.
pause
exit /b 0
