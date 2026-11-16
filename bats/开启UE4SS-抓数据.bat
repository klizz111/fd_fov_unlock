@echo off
REM ============================================================
REM  FallenDoll - turn UE4SS back ON (dump / mod mode)
REM
REM  Moves dwmapi.dll and ue4ss\ back from _ue4ss_off\ into
REM  Binaries\Win64. Symmetric counterpart of the "UE4SS OFF" .bat
REM
REM  100%% ASCII on purpose. No parentheses inside blocks.
REM ============================================================
chcp 65001 >nul 2>nul
setlocal
cd /d "%~dp0"

set "W64=Paralogue\Binaries\Win64"
set "STASH=_ue4ss_off"
set "EXE=%W64%\Paralogue-Win64-Shipping.exe"

echo.
echo   ============================================
echo    Fallen Doll  -  UE4SS ON   /  dump mode
echo   ============================================
echo.

if not exist "%EXE%" goto :no_game

if not exist "%STASH%\dwmapi.dll" goto :check2
goto :running_check
:check2
if not exist "%STASH%\ue4ss" goto :nothing
goto :running_check

:nothing
echo   [i] Nothing to restore: %STASH%\ is empty.
echo       Re-run Install-FDCamDump.ps1 if the files are gone for good.
goto :done

:running_check
tasklist /FI "IMAGENAME eq Paralogue-Win64-Shipping.exe" 2>nul | find /I "Paralogue-Win64-Shipping.exe" >nul
if errorlevel 1 goto :do_move
echo   [!] The game is running. Close it first.
goto :done

:do_move
if exist "%STASH%\dwmapi.dll" goto :move_dll
goto :move_ue4ss
:move_dll
move /Y "%STASH%\dwmapi.dll" "%W64%\dwmapi.dll" >nul
if not exist "%W64%\dwmapi.dll" goto :move_fail
echo   [OK] dwmapi.dll  moved to  %W64%\

:move_ue4ss
if not exist "%STASH%\ue4ss" goto :verify
if exist "%W64%\ue4ss" rmdir /S /Q "%W64%\ue4ss"
move /Y "%STASH%\ue4ss" "%W64%\ue4ss" >nul
if not exist "%W64%\ue4ss" goto :move_fail
echo   [OK] ue4ss      moved to  %W64%\

:verify
if not exist "%W64%\dwmapi.dll" goto :move_fail
if not exist "%W64%\ue4ss" goto :move_fail
echo.
echo   [OK] UE4SS is ON.
echo.
echo        Start the game with the usual daily launcher .bat.
echo        Hotkeys inside the game:
echo          F5  object dump  -^> ue4ss\UE4SS_ObjectDump.txt
echo          F6  class scan   -^> Win64\FDCamDump\classes.txt
echo          F7  live values  -^> Win64\FDCamDump\live_NN.txt
echo          F10 jmap native  -^> Win64\*.jmap
echo          F9  jmap + BP    -^> Win64\*.jmap
echo        Turn off again: the "UE4SS OFF" .bat in this folder
goto :done

:move_fail
echo.
echo   [X] Move failed. Check that %STASH%\ still holds the files
echo       and that nothing is holding them open.
goto :done

:no_game
echo   [X] Not found: %EXE%
echo       Put this .bat in the game root, next to the other .bat files.
goto :done

:done
echo.
pause
exit /b 0
