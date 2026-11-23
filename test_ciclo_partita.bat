@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -m unittest discover -s tests -p test_single_match.py -v
if errorlevel 1 pause
