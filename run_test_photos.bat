@echo off
cd /d "%~dp0"
taskkill /f /im chrome.exe 2>nul
taskkill /f /im chromedriver.exe 2>nul
echo.
echo Iniciando test de modulos de fotos...
echo.
python test_photo_modules.py
echo.
echo Script finalizado. Revisa los mensajes de arriba.
pause
