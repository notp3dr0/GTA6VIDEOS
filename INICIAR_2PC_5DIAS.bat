@echo off
cd /d "%~dp0"
echo ========================================================
echo   GTA 6 + REAL OFICIAL - MODO AUTONOMO 5 DIAS (2o PC)
echo ========================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_2pc_windows.ps1"
if errorlevel 1 (
    echo.
    echo [ERRO] O script terminou com codigo de erro.
    pause
)
