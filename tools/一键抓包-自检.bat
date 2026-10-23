@echo off
chcp 65001 >nul
title FGO Doctor
cd /d "%~dp0"

rem 查找 python（优先 PATH，其次常见安装位置）
set "PY="
for %%p in (python py) do (
    where %%p >nul 2>nul && set "PY=%%p" && goto found
)
if exist "%LocalAppData%\Programs\Python\Python314\python.exe" set "PY=%LocalAppData%\Programs\Python\Python314\python.exe" & goto found
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PY=%LocalAppData%\Programs\Python\Python312\python.exe" & goto found
if exist "C:\Python312\python.exe" set "PY=C:\Python312\python.exe" & goto found
goto nopython

:found
echo [OK] Python: %PY%
"%PY%" -u doctor.py %*
if errorlevel 1 (
    echo.
    echo [!] 自检发现阻塞项，按上面每条的提示处理后再抓
)
pause
exit /b

:nopython
echo [ERROR] 未找到 Python！
echo 请先安装 Python 3.11+（勾选 Add to PATH）：
echo   https://www.python.org/downloads/
echo 然后安装 mitmproxy： pip install mitmproxy
pause
exit /b 1