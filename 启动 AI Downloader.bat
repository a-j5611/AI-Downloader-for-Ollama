@echo off
rem Launcher for AI Downloader (no console window).
rem Uses the bundled pythonw when available, otherwise falls back to pythonw on PATH.
setlocal
set "HERE=%~dp0"
set "PYW=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\pythonw.exe"
if exist "%PYW%" goto run
where pythonw >nul 2>nul
if %ERRORLEVEL%==0 (
  set "PYW=pythonw"
  goto run
)
echo pythonw.exe not found. Please install Python 3.10+ or adjust this file.
pause
exit /b 1

:run
start "" "%PYW%" "%HERE%AIDownloader.pyw"
exit /b 0
