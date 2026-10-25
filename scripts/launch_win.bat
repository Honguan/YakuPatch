@echo off
rem YakuPatch launcher: checks the bundled packages, then starts the GUI with the bundled Python.
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PY=%~dp0runtime\python.exe"
if not exist "%PY%" (
  echo 找不到 runtime\python.exe，請重新解壓縮 YakuPatch_win.zip 或重新執行安裝腳本。
  pause
  exit /b 1
)
"%PY%" -m yakupatch.depcheck
if errorlevel 2 (
  pause
  exit /b 1
)
if errorlevel 1 (
  choice /c YN /m "是否立即補裝缺少的套件"
  if errorlevel 2 exit /b 1
  "%PY%" -m pip install --no-warn-script-location --require-hashes -r requirements.lock
  if errorlevel 1 (
    echo 補裝失敗，請檢查網路連線後再試。
    pause
    exit /b 1
  )
)
start "" "%~dp0runtime\pythonw.exe" -m yakupatch.gui
