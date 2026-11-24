@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo ================================
echo  OYE v44.2 - BUILD WINDOWS
echo ================================
echo.
if not "%OS%"=="Windows_NT" goto :windows_only
where py >nul 2>nul
if errorlevel 1 goto :missing_python
if not exist ".build-venv\Scripts\python.exe" (
  py -3 -m venv .build-venv
  if errorlevel 1 goto :failed
)
set "PY=.build-venv\Scripts\python.exe"
"%PY%" -m pip install --disable-pip-version-check -r requirements.txt -r requirements-ui.txt "pyinstaller>=6,<7"
if errorlevel 1 goto :failed
"%PY%" -m unittest discover -s tests -q
if errorlevel 1 goto :failed
"%PY%" -m PyInstaller --noconfirm --clean --onedir --contents-directory . --windowed --name OyeStudio --add-data "web;web" --add-data "assets;assets" --add-data "defaults/config.json;." --collect-submodules webview studio.py
if errorlevel 1 goto :failed
if not exist "dist\OyeStudio\OyeStudio.exe" goto :failed
if not exist "dist\OyeStudio\web\index.html" goto :failed
if not exist "dist\OyeStudio\web\studio37.js" goto :failed
if not exist "dist\OyeStudio\web\training44.js" goto :failed
if not exist "dist\OyeStudio\web\training44.css" goto :failed
if not exist "dist\OyeStudio\web\scanners.html" goto :failed
if not exist "dist\OyeStudio\web\scanners.js" goto :failed
if not exist "dist\OyeStudio\assets\fortnite_1920x1080_v42\lobby.png" goto :failed
if not exist "dist\OyeStudio\config.json" goto :failed
"dist\OyeStudio\OyeStudio.exe" --smoke-test
if errorlevel 1 goto :failed
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ISCC%" goto :compile
set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%ISCC%" goto :compile
where ISCC.exe >nul 2>nul
if errorlevel 1 goto :missing_inno
set "ISCC=ISCC.exe"
:compile
"%ISCC%" "installer\Oye.iss"
if errorlevel 1 goto :failed
if not exist "release\Oye-v44-Setup-Windows-x64.exe" goto :failed
echo.
echo Build completata: release\Oye-v44-Setup-Windows-x64.exe
echo Installer con disinstallazione tramite Windows.
exit /b 0
:missing_inno
echo ERRORE: manca Inno Setup 6, necessario per un vero installer/disinstaller.
echo Installa Inno Setup 6 da https://jrsoftware.org/isinfo.php
echo L'applicazione compilata e in dist\OyeStudio\OyeStudio.exe
exit /b 2
:missing_python
echo ERRORE: Python 3.11+ non trovato. Serve solo per COMPILARE l'installer.
echo Gli utenti finali NON dovranno installare Python.
exit /b 3
:windows_only
echo Questo script si usa su Windows 10/11 x64.
exit /b 4
:failed
echo Build fallita: controlla i messaggi precedenti. Nessun installer validato.
exit /b 1
