@echo off
chcp 65001 >nul
cd /d "%~dp0"
set QT_QPA_PLATFORM=offscreen
py -m unittest discover -s tests -p test_qt_smoke.py -v
if errorlevel 1 pause
