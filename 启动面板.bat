@echo off
REM ============================================================
REM  Fallen Doll - camera FOV panel launcher
REM
REM  Starts tools\fd_cam_panel.py with pythonw (no console window).
REM  Falls back to python (console stays) if pythonw is missing.
REM
REM  Hotkeys: Ctrl+F7 / Ctrl+F8   -/+ 5 deg
REM           Ctrl+F9            follow game slider / manual
REM           Ctrl+F10           show / hide panel
REM           Ctrl+F11           rescan camera
REM
REM  100%% ASCII on purpose. No parentheses inside blocks.
REM ============================================================
chcp 65001 >nul 2>nul
setlocal
cd /d "%~dp0"

set "HERE=%~dp0"
set "PYW="
set "PY="

where pythonw >nul 2>nul && set "PYW=pythonw"
where python   >nul 2>nul && set "PY=python"

if not defined PYW goto :trypy
start "" %PYW% "%HERE%tools\fd_cam_panel.py" --fov 110 %*
exit /b 0

:trypy
if not defined PY goto :nopython
echo   pythonw not found, falling back to python (a console will stay open)
%PY% "%HERE%tools\fd_cam_panel.py" --fov 110 %*
exit /b 0

:nopython
echo   [!] Python not found on PATH.
echo       Install Python 3.10+ with "Add Python to PATH", then retry.
pause
exit /b 1
