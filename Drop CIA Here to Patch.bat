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

REM Resolve a real Python (not the Microsoft Store stub). Prefer PATH, then py launcher,
REM then common install folders — new installs often only get "py" or miss PATH.
call :find_python
if not defined PYTHON (
  echo.
  echo [!] Python 3.10+ not found.
  echo.
  echo     Fix ^(pick one^):
  echo       1. Install from https://www.python.org/downloads/
  echo          and CHECK "Add python.exe to PATH"
  echo       2. Or install from Microsoft Store: "Python 3.12"
  echo.
  echo     If you disabled "App execution aliases" for python.exe:
  echo     that only helps after a real install is on PATH / via py.
  echo     Try opening a NEW Command Prompt and running:  py -3 --version
  echo.
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
echo  Using:
echo    %PYTHON%
echo.

"%PYTHON%" "%SRC%\setup_tools.py"
if errorlevel 1 (
  echo [!] Tool setup failed.
  pause
  exit /b 1
)

echo.
echo Decrypting / injecting scripts + UI / rebuilding CIA...
echo This can take several minutes and needs a few GB free disk.
echo.

REM Use extracted RomFS as a *source copy* only — never --in-place-romfs
REM (in-place previously overwrote img.bin with a bad UI pack).
set "EXTRA_ROMFS="
if exist "C:\Users\Zepse\nlpp_work\romfs\script\bin\script" (
  echo Using RomFS template from C:\Users\Zepse\nlpp_work\romfs ^(copied, not in-place^)
  set "EXTRA_ROMFS=--romfs C:\Users\Zepse\nlpp_work\romfs"
)

REM UI packing is opt-in: only same-size BCLIM swaps are safe. Default = scripts only
REM (broken grey panels were caused by png2bclim format/size changes).
if /i "%NLPP_WITH_IMAGES%"=="1" (
  echo UI image packing enabled ^(NLPP_WITH_IMAGES=1^)
  set "REUSE_IMG="
  if exist "%~dp0out\new_img.bin" set "REUSE_IMG=--reuse-packed-img"
  "%PYTHON%" "%SRC%\patch_cia.py" --cia "%CIA%" --out "%~dp0out\NewLovePlusPlus-EN.cia" --layeredfs-out "%~dp0out\layeredfs" --with-images --packed-img "%~dp0out\new_img.bin" %REUSE_IMG% %EXTRA_ROMFS%
) else (
  echo Scripts-only patch ^(UI images off — set NLPP_WITH_IMAGES=1 to enable^)
  "%PYTHON%" "%SRC%\patch_cia.py" --cia "%CIA%" --out "%~dp0out\NewLovePlusPlus-EN.cia" --layeredfs-out "%~dp0out\layeredfs" %EXTRA_ROMFS%
)
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

:find_python
set "PYTHON="
REM 1) python on PATH — skip the zero-byte Microsoft Store alias stub
where python >nul 2>&1
if not errorlevel 1 (
  for /f "delims=" %%P in ('where python 2^>nul') do (
    if not defined PYTHON call :try_python "%%P"
  )
)
if defined PYTHON exit /b 0

REM 2) Windows Python launcher (survives missing PATH / disabled aliases)
where py >nul 2>&1
if not errorlevel 1 (
  for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable) if sys.version_info >= (3,10) else None" 2^>nul') do (
    if not defined PYTHON if exist "%%P" call :try_python "%%P"
  )
)
if defined PYTHON exit /b 0

REM 3) Common install locations when PATH was never set
for %%V in (314 313 312 311 310) do (
  if not defined PYTHON if exist "%LocalAppData%\Programs\Python\Python%%V\python.exe" (
    call :try_python "%LocalAppData%\Programs\Python\Python%%V\python.exe"
  )
)
if defined PYTHON exit /b 0
for %%V in (3.14 3.13 3.12 3.11 3.10) do (
  if not defined PYTHON if exist "%ProgramFiles%\Python%%V\python.exe" (
    call :try_python "%ProgramFiles%\Python%%V\python.exe"
  )
)
if defined PYTHON exit /b 0
if exist "%LocalAppData%\Microsoft\WindowsApps\PythonSoftwareFoundation.Python*\python.exe" (
  for /d %%D in ("%LocalAppData%\Microsoft\WindowsApps\PythonSoftwareFoundation.Python*") do (
    if not defined PYTHON if exist "%%D\python.exe" call :try_python "%%D\python.exe"
  )
)
exit /b 0

:try_python
REM Reject Store stub (opens Store / tiny file) and require 3.10+
set "CAND=%~1"
if not exist "%CAND%" exit /b 1
for %%A in ("%CAND%") do if %%~zA LSS 1024 exit /b 1
REM Bare WindowsApps\python.exe is the Store alias stub; real Store Python is under PythonSoftwareFoundation.*
set "CAND_DIR=%~dp1"
echo "%CAND_DIR%" | findstr /i /c:"\WindowsApps\" >nul
if not errorlevel 1 (
  echo "%CAND_DIR%" | findstr /i /c:"PythonSoftwareFoundation" >nul
  if errorlevel 1 exit /b 1
)
"%CAND%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 exit /b 1
set "PYTHON=%CAND%"
exit /b 0
