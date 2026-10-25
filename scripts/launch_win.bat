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
echo 正在檢查套件…
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
echo 正在啟動 YakuPatch，第一次啟動可能需要數十秒，請稍候
set "YAKUPATCH_READY=%TEMP%\yakupatch-%RANDOM%%RANDOM%.ready"
start "" "%~dp0runtime\pythonw.exe" -m yakupatch.gui
set /a WAITED=0
:wait
rem ping instead of timeout: timeout fails when stdin is redirected.
ping -n 2 127.0.0.1 >nul
if exist "%YAKUPATCH_READY%" goto ready
<nul set /p "=."
set /a WAITED+=1
if %WAITED% lss 120 goto wait
echo.
echo 等候超過兩分鐘仍未看到視窗，請再執行一次；若仍無法啟動，請回報問題。
pause
exit /b 1
:ready
for %%F in ("%YAKUPATCH_READY%") do set "SIZE=%%~zF"
if "%SIZE%"=="0" (
  del "%YAKUPATCH_READY%"
  exit /b 0
)
echo.
echo 啟動失敗：
type "%YAKUPATCH_READY%"
del "%YAKUPATCH_READY%"
pause
exit /b 1
