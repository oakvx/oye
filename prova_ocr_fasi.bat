@echo off
chcp 65001 >nul
cd /d "%~dp0"
if "%~1"=="" (
  echo Trascina uno screenshot PNG/JPEG sopra questo file .bat per testare il riconoscimento.
  echo Oppure esegui: py -3 prova_ocr_fasi.py "C:\percorso\screenshot.png"
  pause
  exit /b 2
)
py -3 prova_ocr_fasi.py "%~1"
pause
