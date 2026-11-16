@echo off
REM ============================================================
REM  Fallen Doll - free-camera vertical FOV slider: 60 .. 150 deg
REM
REM  Runs tools\fov_remap.py, which remaps the game's own slider
REM  range (20..90) onto 60..150 by writing CameraComponent+0x250
REM  once the drag settles. No DLL injection.
REM
REM  BEFORE RUNNING: be in the free-camera / photo screen, and
REM  press F6 once in game (that refreshes the pointer manifest
REM  ...\Binaries\Win64\FDCamDump\objects.txt).
REM
REM  100%% ASCII on purpose.
REM ============================================================
chcp 65001 >nul 2>nul
setlocal
cd /d "%~dp0"

set "KIT=%~dp0"
set "PY="
python -c "import sys" >nul 2>nul
if not errorlevel 1 set "PY=python"
if not defined PY (
    py -c "import sys" >nul 2>nul
    if not errorlevel 1 set "PY=py"
)
if not defined PY goto :nopython

tasklist /FI "IMAGENAME eq Paralogue-Win64-Shipping.exe" 2>nul | find /I "Paralogue-Win64-Shipping.exe" >nul
if errorlevel 1 goto :nogame

echo.
echo   ============================================
echo    FOV slider  -^>  60 .. 150 degrees
echo   ============================================
echo.
echo   Drag the vertical-view slider, let go, wait ~0.35 s.
echo   Ctrl+C in this window to stop.
echo.

%PY% "%KIT%tools\fov_remap.py" --out-min 60 --out-max 150 %*
echo.
echo   Stopped. The camera is back to the game's own 20..90.
pause
exit /b 0

:no_game
echo   [!] The game is not running. Start it first, get to the
echo       free-camera screen, press F6 once, then run this again.
pause
exit /b 1

:no_python
echo   [!] Python not found on PATH. Install it, or run
echo       tools\fov_remap.py with the interpreter you do have.
pause
exit /b 1
