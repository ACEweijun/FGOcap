@echo off
rem 职责：入口 B（不抓包）→ start_plain.py；CMD 窗口保持，关窗即全收、关模拟器即离线收工
chcp 65001 >nul
title FGO Boot Plain
cd /d "%~dp0"

rem 查找 python（公共段，见 find_python.cmd）
call find_python.cmd
if errorlevel 1 goto nopython

:found
echo [OK] Python: %PY%
"%PY%" -u start_plain.py
if errorlevel 1 (
    echo.
    echo [ERROR] 开机流程未完成，见上方输出
    pause
)
exit /b

:nopython
echo [ERROR] 未找到 Python！
echo 请先安装 Python 3.10+（勾选 Add to PATH）并 pip install -r requirements.txt
pause
exit /b 1
