@echo off
cd /d "%~dp0"
TITLE GTA 6 Videos - Pool Tri-IA (Claude Pro + Codex Plus + Antigravity Ultra) - 5 Canais (60/dia)
COLOR 0A

echo ===========================================================================
echo   POOL TRI-IA 24/7 (AUTO-RESUME 5H) - 5 CANAIS x 12 VIDEOS/DIA (60/DIA)
echo ===========================================================================
echo.

echo [1/3] Iniciando Orquestrador Principal 24/7 + Antigravity/Gemini + Painel Web...
start "GTA6 - Maestro 5 Canais & Painel" powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_2pc_windows.ps1"

timeout /t 3 /nobreak >nul

echo [2/3] Iniciando Worker Claude Pro (Opus xhigh Think + Haiku low Exec + Auto-Resume 5h)...
start "GTA6 - Claude Pro 5h Loop" powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\worker_claude_5h.ps1"

timeout /t 2 /nobreak >nul

echo [3/3] Iniciando Worker Codex Plus (Sol 6.2 xhigh Think + low Exec + Auto-Resume 5h)...
start "GTA6 - Codex Plus 5h Loop" powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\worker_codex_5h.ps1"

echo.
echo ===========================================================================
echo   As 3 IAs e os 5 Canais (60 videos/dia) estao rodando com failover de 5h!
echo ===========================================================================
pause
