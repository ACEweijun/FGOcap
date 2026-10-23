@echo off
rem ============================================================
rem  FGO 一键抓包 - 恢复代理（RestoreProxy）
rem  异常中断后一键清代理 + 停 mitmdump（等价 RestoreProxy 动作）
rem ============================================================
chcp 65001 >nul
setlocal

rem 查找 python（优先 PATH，其次常见安装位置）
set "PY="
for %%p in (python py) do (
    where %%p >nul 2>nul
    if not errorlevel 1 (
        set "PY=%%p"
        goto found
    )
)
if exist "%LocalAppData%\Programs\Python\Python314\python.exe" set "PY=%LocalAppData%\Programs\Python\Python314\python.exe" & goto found
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PY=%LocalAppData%\Programs\Python\Python312\python.exe" & goto found
if exist "C:\Python312\python.exe" set "PY=C:\Python312\python.exe" & goto found
goto nopython

:found
cd /d "%~dp0"
"%PY%" restore.py %*
if errorlevel 1 (
    echo.
    echo [WARN] 恢复未完全成功，请检查上方输出。
)
pause
exit /b %errorlevel%

:nopython
echo [ERROR] 未找到 Python，请先安装 Python 3.11+ 并勾选 Add to PATH
echo         https://www.python.org/downloads/
pause
exit /b 1
