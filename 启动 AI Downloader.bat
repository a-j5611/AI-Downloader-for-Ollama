@echo off
rem Launcher for AI Downloader.
rem Prefers the Python runtime bundled inside this project (runtime\python),
rem falls back to a system-wide Python if the bundled one is missing.
setlocal
set "HERE=%~dp0"
set "PYW=%HERE%runtime\python\pythonw.exe"

if exist "%PYW%" goto run

rem Fallback: a python folder next to the project (portable install)
if exist "%HERE%python\pythonw.exe" (
  set "PYW=%HERE%python\pythonw.exe"
  goto run
)

rem Fallback: system pythonw on PATH
where pythonw >nul 2>nul
if %ERRORLEVEL%==0 (
  set "PYW=pythonw"
  goto run
)

echo.
echo   AI Downloader cannot start: no Python interpreter found.
echo.
echo   Expected the bundled runtime at:
echo     %HERE%runtime\python\pythonw.exe
echo   and no system pythonw.exe on PATH either.
echo.
echo   Please re-download the project including the runtime\ folder,
echo   or install Python 3.10+ from https://www.python.org/downloads/windows/
echo   (tick "tcl/tk and IDLE" and run: pip install pillow)
echo.
pause
exit /b 1

:run
start "" "%PYW%" "%HERE%AIDownloader.pyw"
exit /b 0
