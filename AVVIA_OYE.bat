@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo OYE 44.2 - WEB STUDIO
echo.
where powershell.exe >nul 2>nul
if errorlevel 1 (
  echo PowerShell Windows non trovato. Impossibile avviare il setup automatico.
  pause
  exit /b 1
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\Avvia-Oye.ps1"
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" (
  echo.
  echo Avvio non riuscito. Controlla logs\avvio_v44.log
  pause
)
exit /b %RESULT%
