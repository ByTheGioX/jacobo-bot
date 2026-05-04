@echo off
cd /d "%~dp0"
echo.
echo ============================================================
echo   RECUPERACION DE FOTOS ATRASADAS
echo   Ignora processed_photo_ids.json y re-escanea el grupo.
echo   Solo usa esto cuando el bot estuvo offline varios dias.
echo   Las fotos ya enviadas NO se duplican (hay otro control).
echo ============================================================
echo.

taskkill /f /im chrome.exe 2>nul
taskkill /f /im chromedriver.exe 2>nul
timeout /t 2 /nobreak >nul

echo Iniciando recuperacion...
echo.
python test_photo_modules.py --reprocess
echo.
echo Script finalizado. Revisa los mensajes de arriba.
pause
