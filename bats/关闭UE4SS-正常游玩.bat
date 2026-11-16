@echo off
REM ============================================================
REM  FallenDoll - turn UE4SS OFF (normal play)
REM
REM  Moves dwmapi.dll and ue4ss\ out of Binaries\Win64 into
REM  _ue4ss_off\. Nothing is deleted, so the "UE4SS ON" .bat
REM  puts it back exactly as it was.
REM
REM  This file is 100%% ASCII on purpose: cmd.exe parses .bat with
REM  the system ANSI code page and non-ASCII bytes break commands.
REM  No parentheses inside parenthesised blocks either.
REM ============================================================
chcp 65001 >nul 2>nul
setlocal
cd /d "%~dp0"

set "W64=Paralogue\Binaries\Win64"
set "STASH=_ue4ss_off"
set "EXE=%W64%\Paralogue-Win64-Shipping.exe"

echo.
echo   ============================================
echo    Fallen Doll  -  UE4SS OFF  /  normal play
echo   ============================================
echo.

if not exist "%EXE%" goto :no_game

if not exist "%W64%\dwmapi.dll" goto :check2
goto :running_check
:check2
if not exist "%W64%\ue4ss" goto :already_off
goto :running_check

:already_off
echo   [i] UE4SS is already OFF.
echo       Binaries\Win64 has no dwmapi.dll and no ue4ss folder.
goto :done

:running_check
tasklist /FI "IMAGENAME eq Paralogue-Win64-Shipping.exe" 2>nul | find /I "Paralogue-Win64-Shipping.exe" >nul
if errorlevel 1 goto :do_move
echo   [!] The game is running.
echo       Close the game completely, then run this again.
goto :done

:do_move
if not exist "%STASH%" mkdir "%STASH%"

if not exist "%W64%\dwmapi.dll" goto :move_ue4ss
move /Y "%W64%\dwmapi.dll" "%STASH%\dwmapi.dll" >nul
if exist "%W64%\dwmapi.dll" goto :move_fail
echo   [OK] dwmapi.dll  moved to  %STASH%\

:move_ue4ss
if not exist "%W64%\ue4ss" goto :verify
if exist "%STASH%\ue4ss" rmdir /S /Q "%STASH%\ue4ss"
move /Y "%W64%\ue4ss" "%STASH%\ue4ss" >nul
if exist "%W64%\ue4ss" goto :move_fail
echo   [OK] ue4ss      moved to  %STASH%\

:verify
if exist "%W64%\dwmapi.dll" goto :move_fail
if exist "%W64%\ue4ss" goto :move_fail
echo.
echo   [OK] UE4SS is OFF. The game now starts clean, no console
echo        window, no injected DLL.
echo.
echo        Play:      use the usual daily launcher .bat in this folder
echo        Turn back: run the "UE4SS ON" .bat in this folder
goto :done

:move_fail
echo.
echo   [X] Could not move the files. Most likely the game or a
echo       virus scanner still holds dwmapi.dll open.
echo       Close everything and try again.
goto :done

:no_game
echo   [X] Not found: %EXE%
echo       Put this .bat in the game root, next to the other .bat files.
goto :done

:done
echo.
pause
exit /b 0
