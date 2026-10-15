@echo off
chcp 936 >nul 2>&1
title 文字转 MP3 · 网络诊断
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PYTHONIOENCODING=gbk"
set "PYTHONUTF8=0"

echo ============================================================
echo   文字转 MP3  ·  网络诊断
echo ============================================================
echo.

set "PY="

if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"

if not defined PY if exist "C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe" set "PY=C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

if not defined PY (
  py -3 -c "import edge_tts" >nul 2>&1 && set "PY=py -3"
)

if not defined PY (
  python -c "import edge_tts" >nul 2>&1 && set "PY=python"
)

if not defined PY (
  echo [错误] 没找到装好 edge-tts 的 Python 环境。
  echo   请执行: pip install flask edge-tts mutagen
  echo.
  pause
  exit /b 1
)

!PY! doctor.py
echo.
pause
