@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo OYE -- installazione su Windows
echo.
py -m pip install -r requirements.txt
if errorlevel 1 (
  echo Errore installazione dipendenze principali.
  pause
  exit /b 1
)
py -m pip install -r requirements-ui.txt
if errorlevel 1 echo WebView facoltativo non disponibile: Oye aprira il browser locale.
echo.
echo Il programma Tesseract OCR per Windows va installato a parte, se necessario.
echo Avvia: apri_studio.bat
pause
