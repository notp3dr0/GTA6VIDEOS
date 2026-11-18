@echo off
cd /d "%~dp0"
echo ====================================================================
echo   VALIDACAO DOS 3 ESCUDOS ANTES DE VIAJAR (GTA 6 + REAL OFICIAL)
echo ====================================================================
uv run python scripts/preflight_check.py
echo.
pause
