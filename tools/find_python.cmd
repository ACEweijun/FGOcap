@echo off
chcp 65001 >nul
rem Common Python lookup: known install paths first (avoids WindowsApps store stub), then PATH.
rem 2026-10-25: old version took whatever `where python` returned first -- hitting the
rem WindowsApps execution alias (store stub) made the bat open Microsoft Store instead
rem of running, and the chosen candidate was never version-checked.
rem Now: known install paths first, PATH candidates skip WindowsApps stubs,
rem and every candidate must pass a 3.11+ smoke test.
rem NOTE: keep this file ASCII-only -- cmd.exe mis-parses multi-byte lines flakily.
set "PY="

if not defined PY call :try "%LocalAppData%\Programs\Python\Python314\python.exe"
if not defined PY call :try "%LocalAppData%\Programs\Python\Python313\python.exe"
if not defined PY call :try "%LocalAppData%\Programs\Python\Python312\python.exe"
if not defined PY call :try "%LocalAppData%\Programs\Python\Python311\python.exe"
if not defined PY call :try "C:\Python314\python.exe"
if not defined PY call :try "C:\Python313\python.exe"
if not defined PY call :try "C:\Python312\python.exe"
if not defined PY call :try "C:\Python311\python.exe"
if defined PY goto found

call :scan_path python
if defined PY goto found
call :scan_path py
if defined PY goto found
goto nopython

:scan_path
for /f "delims=" %%i in ('where %1 2^>nul') do call :try "%%i"
goto :eof

:try
rem %1 = candidate python.exe: must exist, must NOT be a WindowsApps stub, must be 3.11+
if not exist "%~1" goto :eof
echo %~1 | findstr /i /c:"WindowsApps" >nul 2>&1
if not errorlevel 1 goto :eof
"%~1" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 goto :eof
set "PY=%~1"
goto :eof

:found
exit /b 0

:nopython
echo [ERROR] No usable Python 3.11+ found!
echo Install Python 3.11+ with "Add to PATH" checked:
echo   https://www.python.org/downloads/
echo Then install mitmproxy: pip install mitmproxy
exit /b 1
