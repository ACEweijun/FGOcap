@echo off
rem 职责：入口 A（含抓包）→ auto_capture.py；CMD 窗口保持，关窗即全收、关模拟器即离线收工
chcp 65001 >nul
title FGO Auto Capture
cd /d "%~dp0"

rem 查找 python（公共段，见 find_python.cmd）
call find_python.cmd
if errorlevel 1 goto nopython

:found
echo [OK] Python: %PY%
"%PY%" -u auto_capture.py
if errorlevel 1 (
    echo.
    echo [ERROR] Script failed. Check the popup messages above.
    pause
)
exit /b

:nopython
echo [ERROR] 未找到 Python！
echo 请先安装 Python 3.11+（勾选 Add to PATH）：
echo   https://www.python.org/downloads/
echo 然后安装 mitmproxy： pip install mitmproxy
pause
exit /b 1
