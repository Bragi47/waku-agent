@echo off
rem ============================================================
rem  Waku — everything in ONE window: dashboard(+Telegram panel),
rem         voice (mic) in background, browser opens automatically.
rem  Close this window = stop everything.
rem ============================================================
setlocal
cd /d "%~dp0"
set "VENV=%~dp0.venv\Scripts\python.exe"

if not exist "%VENV%" (
    echo .venv not found — run  python -m venv .venv  first.
    pause
    exit /b 1
)

rem --- telegram replies with synthesized voice ---
set "WAKU_TG_VOICE=1"

title Waku — dashboard + telegram + voice
echo.
echo  Waku starting...
echo    dashboard + Telegram  -  this window
echo    voice / mic           -  background
echo    browser               -  http://localhost:7777
echo  Close this window to stop everything.
echo.

rem --- voice in the background (no extra window) ---
start "" /b "%VENV%" -u -m waku voice

rem --- small pause so voice boots quietly first ---
timeout /t 2 /nobreak >nul

rem --- dashboard (panel + Telegram poller) foreground ---
"%VENV%" -u -m waku.ops.dashboard

rem --- dashboard closed: kill any leftover waku python ---
taskkill /f /im python.exe >nul 2>&1
endlocal