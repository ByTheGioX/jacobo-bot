@echo off
cd /d "%~dp0"
echo.
echo ============================================================
echo   AGREGAR AGENCIA NUEVA
echo   Te pedira el nombre y el link del perfil de Idealista.
echo   El codigo interno se genera solo, no hay que inventarlo.
echo ============================================================
echo.
python -m tools.add_agency
echo.
pause
