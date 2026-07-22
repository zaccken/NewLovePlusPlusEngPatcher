@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

REM Drag a New Love Plus+ .cia / .3ds / .cci onto this file, or double-click for a drop window.
title New Love Plus+ - Drop ROM to Patch
set "SRC=%~dp0src"

if not "%~1"=="" goto :run_patch

where powershell >nul 2>&1
if errorlevel 1 (
  echo Double-click opens a drop window ^(needs PowerShell^).
  echo Or drag a .cia / .3ds / .cci file onto this bat.
  echo.
  set /p "CIA=ROM path: "
  if "%CIA%"=="" exit /b 1
  call "%~f0" "%CIA%"
  exit /b %ERRORLEVEL%
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%SRC%\drop_zone.ps1"
exit /b %ERRORLEVEL%

:run_patch
set "CIA=%~1"
set "EXT=%~x1"

if /i not "%EXT%"==".cia" if /i not "%EXT%"==".3ds" if /i not "%EXT%"==".cci" (
  echo.
  echo [!] Drop a .cia / .3ds / .cci file ^(got: %~nx1^)
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
echo   New Love Plus+ English Patcher
echo  ============================================
echo.
echo  Dropped:
echo    %CIA%
echo  Using:
echo    %PYTHON%
echo.

echo Installing Python deps from requirements.txt ...
"%PYTHON%" -m pip install -q -r "%~dp0requirements.txt"
if errorlevel 1 (
  echo [!] pip install failed. Try: %PYTHON% -m pip install -r requirements.txt
  pause
  exit /b 1
)
echo.
echo Fetching / checking CIA tools ^(3dstool, ctrtool, makerom, seeddb, decrypt^) ...
echo decrypt.exe is vendored from Batch CIA 3DS Decryptor Redux ^(see tools\Batch-CIA-3DS-Decryptor-Redux\CREDITS.md^).
"%PYTHON%" "%SRC%\setup_tools.py"
if errorlevel 1 (
  echo [!] Tool setup failed.
  pause
  exit /b 1
)

REM SHA-1 gate: known dumps pass; unknown dumps ask before continuing with --skip-hash.
set "SKIP_HASH="
echo.
echo Checking dump SHA-1 ^(this may take a minute on large .3ds files^)...
"%PYTHON%" -c "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); from patch_cia import sha1_file, ALLOWED_DUMP_SHA1; p=Path(sys.argv[2]); d=sha1_file(p); print(d); raise SystemExit(0 if d.lower() in ALLOWED_DUMP_SHA1 else 2)" "%SRC%" "%CIA%" > "%TEMP%\nlpp_sha1.txt" 2>nul
set HASH_ERR=!ERRORLEVEL!
set "GOT_SHA1="
if exist "%TEMP%\nlpp_sha1.txt" (
  set /p GOT_SHA1=<"%TEMP%\nlpp_sha1.txt"
  del "%TEMP%\nlpp_sha1.txt" >nul 2>&1
)

if "!HASH_ERR!"=="0" (
  echo [hash] OK — known dump
  echo         !GOT_SHA1!
  REM Already verified here; skip the second full-file hash inside patch_cia.py.
  set "SKIP_HASH=--skip-hash"
) else if "!HASH_ERR!"=="2" (
  echo.
  echo [!] SHA-1 is not in the known-dump allowlist.
  echo     got: !GOT_SHA1!
  echo.
  echo     This may still be a valid dump ^(e.g. a decrypted CIA with a
  echo     different hash^), but it was not verified against the list.
  echo.
  set /p "CONT=Continue patching anyway? [y/N]: "
  if /i not "!CONT!"=="y" if /i not "!CONT!"=="yes" (
    echo Aborted.
    pause
    exit /b 1
  )
  echo [hash] continuing with --skip-hash
  set "SKIP_HASH=--skip-hash"
) else (
  echo [!] Could not compute SHA-1 ^(exit !HASH_ERR!^).
  set /p "CONT=Continue without hash check? [y/N]: "
  if /i not "!CONT!"=="y" if /i not "!CONT!"=="yes" (
    echo Aborted.
    pause
    exit /b 1
  )
  set "SKIP_HASH=--skip-hash"
)

echo.
echo Decrypting / injecting scripts + UI / rebuilding CIA...
echo Accepts encrypted or decrypted .cia and .3ds/.cci.
echo This can take several minutes and needs a few GB free disk.
echo.

REM Use sibling extracted RomFS as a *source copy* only — never --in-place-romfs
REM (in-place previously overwrote img.bin with a bad UI pack).
set "EXTRA_ROMFS="
set "SIBLING_ROMFS=%~dp0..\New Love Plus Plus\extracted\romfs"
if exist "%SIBLING_ROMFS%\script\bin\script" (
  echo Using RomFS template from sibling extracted ^(copied, not in-place^)
  set "EXTRA_ROMFS=--romfs %SIBLING_ROMFS%"
)

REM UI ON by default. Durable release artifacts (not wipeable like out/):
REM   release\bake_img.bin     — gold bake (preferred)
REM   release\romfs_overlay\   — TRB overlays (auto-applied when present)
REM Optional PNG scratch:
REM   cache\new_img.bin        — PNG pack only (incomplete vs gold)
REM Opt out: set NLPP_WITH_IMAGES=0
REM Force PNG scratch rebuild: set NLPP_REPACK_IMAGES=1
REM Missing gold bake auto-runs: python tools\rebuild_bake_img.py
if not exist "%~dp0cache" mkdir "%~dp0cache"
if not exist "%~dp0release" mkdir "%~dp0release"
set "PACKED_IMG=%~dp0cache\new_img.bin"
if exist "%~dp0release\bake_img.bin" (
  set "PACKED_IMG=%~dp0release\bake_img.bin"
  echo Using gold bake: release\bake_img.bin
) else if exist "%~dp0cache\bake_img.bin" (
  REM legacy path during transition
  set "PACKED_IMG=%~dp0cache\bake_img.bin"
  echo Using legacy bake: cache\bake_img.bin ^(move to release\^)
)
if exist "%~dp0release\romfs_overlay\SystemData" (
  echo TRB overlay will auto-apply from release\romfs_overlay
) else if exist "%~dp0cache\romfs_overlay\SystemData" (
  echo TRB overlay will auto-apply from cache\romfs_overlay ^(legacy^)
)
if /i "%NLPP_WITH_IMAGES%"=="0" (
  echo Scripts-only patch ^(NLPP_WITH_IMAGES=0 — UI pack skipped^)
  "%PYTHON%" "%SRC%\patch_cia.py" --cia "%CIA%" --out "%~dp0out\NewLovePlusPlus-EN.cia" --no-images %EXTRA_ROMFS% %SKIP_HASH%
) else if /i "%NLPP_REPACK_IMAGES%"=="1" (
  echo UI packing — rebuilding cache\new_img.bin from assets\images ^(not gold bake^)
  "%PYTHON%" "%SRC%\patch_cia.py" --cia "%CIA%" --out "%~dp0out\NewLovePlusPlus-EN.cia" --packed-img "%~dp0cache\new_img.bin" --repack-images %EXTRA_ROMFS% %SKIP_HASH%
) else (
  REM Auto-build gold bake when missing (full: PNG pack + TRB + deploys + SMS).
  if not exist "%~dp0release\bake_img.bin" if not exist "%~dp0cache\bake_img.bin" (
    echo.
    echo No gold bake at release\bake_img.bin — running full tools\rebuild_bake_img.py
    echo This regenerates bake + textresource TRBs from sources.
    echo First full rebuild often takes ~16 hours. Leave this window open.
    echo.
    "%PYTHON%" "%~dp0tools\rebuild_bake_img.py"
    if errorlevel 1 (
      echo [!] rebuild_bake_img.py failed.
      pause
      exit /b 1
    )
    if not exist "%~dp0release\bake_img.bin" (
      echo [!] rebuild finished but release\bake_img.bin is still missing.
      pause
      exit /b 1
    )
    set "PACKED_IMG=%~dp0release\bake_img.bin"
    echo Using newly built gold bake: release\bake_img.bin
  )
  if exist "!PACKED_IMG!" (
    echo Reusing packed img: !PACKED_IMG!
  ) else (
    set "PACKED_IMG=%~dp0release\bake_img.bin"
  )
  "%PYTHON%" "%SRC%\patch_cia.py" --cia "%CIA%" --out "%~dp0out\NewLovePlusPlus-EN.cia" --packed-img "!PACKED_IMG!" %EXTRA_ROMFS% %SKIP_HASH%
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
echo     ^(scratch work dir cleaned up^)
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
