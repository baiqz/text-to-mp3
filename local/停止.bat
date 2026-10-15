@echo off
chcp 936 >nul 2>&1
title 文字转 MP3 · 停止服务
cd /d "%~dp0"

echo ============================================================
echo   文字转 MP3  ·  停止服务
echo ============================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$found = @(); Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*app.py*' } | ForEach-Object { $found += $_.ProcessId; Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; if ($found.Count -gt 0) { Write-Host ('已停止服务进程: ' + ($found -join ', ')) } else { Write-Host '没有发现正在运行的服务。' }"

echo.
pause
