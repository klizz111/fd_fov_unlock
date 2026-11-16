@echo off
REM ============================================================
REM  Fallen Doll - camera FOV control panel (external overlay)
REM
REM  Small always-on-top panel with its own 60..150 slider, so the
REM  game's 20..90 slider is no longer a limit.
REM
REM  Before use: stand in the free-camera / photo screen and press
REM  F6 once in game, that refreshes the pointer manifest.
REM
REM  Hotkeys:  Ctrl+F7  -5 deg      Ctrl+F8  +5 deg
REM            Ctrl+F9  follow game slider / manual
REM            Ctrl+F10 show / hide panel
REM
REM  100%% ASCII. No parentheses inside blocks.
REM ============================================================
chcp 65001 >nul 2>nul
setlocal
cd /d "%~dp0"

set "HERE=%~dp0"
set "PYW="
set "PY="

where pythonw >nul 2>nul && set "PYW=pythonw"
where python  >nul 2>nul && set "PY=python"

if not defined PYW goto :trypy

REM try detached start first; if no pythonw shows up in 2s, run in foreground
start "" %PYW% "%HERE%tools\fd_cam_panel.py" --fov 110 %*
ping -n 3 127.0.0.1 >nul
tasklist /FI "IMAGENAME eq pythonw.exe" 2>nul | find /I "pythonw.exe" >nul
if errorlevel 1 goto :directpyw
echo   panel started, no console window
exit /b 0

:directpyw
%PYW% "%HERE%tools\fd_cam_panel.py" --fov 110 %*
exit /b 0

:trypy
if not defined PY goto :nopython
echo   pythonw not found, falling back to python
%PY% "%HERE%tools\fd_cam_panel.py" --fov 110 %*
exit /b 0

:nopython
echo   [!] Python not found on PATH.
pause
exit /b 1
