@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "OYE_OPEN_TAB=timeline"
call "%~dp0AVVIA_OYE.bat"
