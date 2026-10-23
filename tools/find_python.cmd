@echo off
rem 公共 Python 查找段：优先 PATH（where python / py），其次常见安装位置
set "PY="
for %%p in (python py) do (
    where %%p >nul 2>nul && set "PY=%%p" && goto found
)
if exist "%LocalAppData%\Programs\Python\Python314\python.exe" set "PY=%LocalAppData%\Programs\Python\Python314\python.exe" & goto found
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PY=%LocalAppData%\Programs\Python\Python312\python.exe" & goto found
if exist "C:\Python312\python.exe" set "PY=C:\Python312\python.exe" & goto found
goto nopython

:found
exit /b 0

:nopython
echo [ERROR] 未找到 Python！
echo 请先安装 Python 3.11+（勾选 Add to PATH）：
echo   https://www.python.org/downloads/
echo 然后安装 mitmproxy： pip install mitmproxy
exit /b 1
