@echo off
setlocal EnableExtensions
cd /d "%~dp0"

REM Drag a New Love Plus+ .cia onto this file, or double-click for a drop window.
title New Love Plus+ - Drop CIA to Patch
set "SRC=%~dp0src"

if not "%~1"=="" goto :run_patch

where powershell >nul 2>&1
if errorlevel 1 (
  echo Double-click opens a drop window ^(needs PowerShell^).
  echo Or drag a .cia file onto this bat.
  echo.
  set /p "CIA=CIA path: "
  if "%CIA%"=="" exit /b 1
  call "%~f0" "%CIA%"
  exit /b %ERRORLEVEL%
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%SRC%\drop_zone.ps1"
exit /b %ERRORLEVEL%

:run_patch
set "CIA=%~1"

if /i not "%~x1"==".cia" (
  echo.
  echo [!] Drop a .cia file ^(got: %~nx1^)
  echo.
  pause
  exit /b 1
)

if not exist "%CIA%" (
  echo [!] File not found:
  echo     %CIA%
  pause
  exit /b 1
)

where python >nul 2>&1
if errorlevel 1 (
  echo [!] Python not found on PATH. Install Python 3.10+ and retry.
  pause
  exit /b 1
)

echo.
echo  ============================================
echo   New Love Plus+ English CIA Patcher
echo  ============================================
echo.
echo  Dropped:
echo    %CIA%
echo.

python "%SRC%\setup_tools.py"
if errorlevel 1 (
  echo [!] Tool setup failed.
  pause
  exit /b 1
)

echo.
echo Decrypting / injecting scripts + UI / rebuilding CIA...
echo This can take several minutes and needs a few GB free disk.
echo.

set "EXTRA_ROMFS="
if exist "C:\Users\Zepse\nlpp_work\romfs\script\bin\script" (
  echo Using existing RomFS at C:\Users\Zepse\nlpp_work\romfs
  set "EXTRA_ROMFS=--romfs C:\Users\Zepse\nlpp_work\romfs --in-place-romfs"
)

set "REUSE_IMG="
if exist "%~dp0out\new_img.bin" (
  echo Reusing packed UI: out\new_img.bin
  set "REUSE_IMG=--reuse-packed-img"
)

python "%SRC%\patch_cia.py" --cia "%CIA%" --out "%~dp0out\NewLovePlusPlus-EN.cia" --layeredfs-out "%~dp0out\layeredfs" --with-images --packed-img "%~dp0out\new_img.bin" %REUSE_IMG% %EXTRA_ROMFS%
set ERR=%ERRORLEVEL%

echo.
if not "%ERR%"=="0" (
  echo [!] Patch failed ^(exit %ERR%^).
  pause
  exit /b %ERR%
)

echo [+] Patched CIA:
echo     %~dp0out\NewLovePlusPlus-EN.cia
echo [+] LayeredFS:
echo     %~dp0out\layeredfs\
echo.
pause
exit /b 0
