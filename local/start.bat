@echo off
chcp 936 >nul 2>&1
title 文字转 MP3
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem 控制台是 GBK，Python 也跟着用 GBK 输出，避免中文乱码
set "PYTHONIOENCODING=gbk"
set "PYTHONUTF8=0"

echo ============================================================
echo   文字转 MP3  ·  启动中
echo ============================================================
echo.

set "PY="

rem ---- 依次寻找可用的 Python 运行环境（必须已装好依赖）----
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"

if not defined PY if exist "C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe" set "PY=C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

if not defined PY (
  py -3 -c "import flask, edge_tts, mutagen" >nul 2>&1 && set "PY=py -3"
)

if not defined PY (
  python -c "import flask, edge_tts, mutagen" >nul 2>&1 && set "PY=python"
)

if not defined PY (
  echo [错误] 没找到可用的 Python 环境。
  echo.
  echo 本工具需要 Python 以及三个依赖库：
  echo     pip install flask edge-tts mutagen
  echo.
  echo 装好后再双击本文件。
  echo.
  pause
  exit /b 1
)

echo   运行环境: !PY!
echo   正在启动服务，浏览器会自动打开界面……
echo   关闭本窗口即可停止服务（也可双击 停止.bat）。
echo.

!PY! app.py %*
set "RC=!ERRORLEVEL!"

echo.
if not "!RC!"=="0" (
  echo [服务异常退出] 退出代码 !RC!
  echo 看不懂上面的报错？双击 诊断.bat 可生成一份诊断报告。
) else (
  echo 服务已停止。
)
echo.
pause
