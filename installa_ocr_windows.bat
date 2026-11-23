@echo off
cd /d "%~dp0"
py -m pip install -r requirements-learning.txt
if errorlevel 1 ( echo Errore installazione dipendenze & pause & exit /b 1 )
echo Installa anche Tesseract OCR per Windows e aggiungilo al PATH.
echo Verifica con: tesseract --version
pause
