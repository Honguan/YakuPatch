@echo off
rem YakuPatch launcher: checks the bundled packages, then runs the GUI in this console so all its output shows here.
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PY=%~dp0runtime\python.exe"
if not exist "%PY%" (
  echo runtime\python.exe not found. Unzip YakuPatch_win.zip again or rerun the install script.
  pause
  exit /b 1
)
echo Checking packages...
echo ^> "%PY%" -m yakupatch.depcheck
"%PY%" -m yakupatch.depcheck
if errorlevel 2 (
  pause
  exit /b 1
)
if errorlevel 1 (
  choice /c YN /m "Install the missing packages now"
  if errorlevel 2 exit /b 1
  echo ^> "%PY%" -m pip install --no-warn-script-location --require-hashes -r requirements.lock
  "%PY%" -m pip install --no-warn-script-location --require-hashes -r requirements.lock
  if errorlevel 1 (
    echo Install failed. Check your network connection and try again.
    pause
    exit /b 1
  )
)
echo Starting YakuPatch. The first start can take a few dozen seconds, please wait.
echo ^> "%PY%" -m yakupatch.gui
"%PY%" -m yakupatch.gui
set "RC=%errorlevel%"
if not "%RC%"=="0" (
  echo.
  echo YakuPatch exited with code %RC%. The error is shown above.
  pause
  exit /b 1
)
