@echo off
cd /d "%~dp0"
TITLE GTA 6 - Piloto Automatico 4 Dias (Coringa e Sua Roda - 5 Canais / 60 Cortes/Dia)
COLOR 0B

echo ============================================================================
echo   PILOTO AUTOMATICO 4 DIAS - CORINGA E SUA RODA (KICK + TWITCH + YOUTUBE)
echo   5 Canais x 12 Cortes/Dia = 60 Cortes/Dia (120 Posts/Dia TikTok + Reels)
echo ============================================================================
echo.

REM 1. Garante que o arquivo .env existe antes de comecar
if not exist ".env" (
    echo [AVISO] Arquivo .env nao encontrado! Criando copia a partir de .env.example...
    copy /Y ".env.example" ".env" >nul
    echo.
    echo ============================================================================
    echo   PREENCHA AS CHAVES NO .env QUE ACABOU DE ABRIR E SALVE!
    echo   Obrigatorio p/ postar sozinho nos 4 dias:
    echo     - DRY_RUN=false
    echo     - AUTOPILOT_MODE=full_auto
    echo     - REAL_OFICIAL_ENABLED=false
    echo     - POST_FOR_ME_API_KEY=...  (conecta os 5 TikToks + 5 Instagrams)
    echo     - GROQ_API_KEY=...         (gratis p/ legendar falas reais c/ Whisper)
    echo     - GEMINI_API_KEY=...       (opcional p/ titulos extras Hydrogen)
    echo     - TELEGRAM_BOT_TOKEN=...   (opcional p/ acompanhar pelo celular)
    echo ============================================================================
    notepad ".env"
    echo Depois de salvar o .env, pressione qualquer tecla para iniciar os 4 dias!
    pause
)

REM 2. Instala dependencias automaticamente se .venv ainda nao existir neste PC
if not exist ".venv\Scripts\python.exe" (
    echo [SETUP] Preparando ambiente Python (.venv) com uv sync...
    where uv >nul 2>&1
    if %errorlevel%==0 (
        uv sync
    ) else (
        python -m venv .venv
        .venv\Scripts\pip install -e .
    )
)

REM 3. Forca MODO STREAMERS (Coringa, Gabepeixe, Paulinho, Alanzoka, Jon Vlogs, Luquet4, Gaules...)
set REAL_OFICIAL_ENABLED=false
set AUTOPILOT_MODE=full_auto
set MULTI_CHANNEL_ENABLED=true

echo [1/3] Ligando Maestro 24/7 + Monitor Multi-Live (5 lives simultaneas) + Anti-Hibernacao...
start "GTA6 - Coringa & Roda 24/7 + Painel" powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_2pc_windows.ps1" -RunStartupCycle

timeout /t 3 /nobreak >nul

REM 4. Liga workers auxiliares de 5h auto-resume se Claude/Codex CLI estiverem instalados neste PC
where claude >nul 2>&1
if %errorlevel%==0 (
    echo [2/3] Claude CLI detectado! Ligando Worker Claude 5h Auto-Resume...
    start "GTA6 - Claude 5h Loop" powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\worker_claude_5h.ps1"
) else (
    echo [2/3] Claude CLI opcional nao encontrado - seguindo com motor nativo + Gemini/Groq.
)

where codex >nul 2>&1
if %errorlevel%==0 (
    echo [3/3] Codex CLI detectado! Ligando Worker Codex 5h Auto-Resume...
    start "GTA6 - Codex 5h Loop" powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\worker_codex_5h.ps1"
) else (
    echo [3/3] Codex CLI opcional nao encontrado - seguindo com motor nativo + Gemini/Groq.
)

echo.
echo ============================================================================
echo   TUDO NO AR! PODE DEIXAR O PC LIGADO E VIAJAR TRANQUILO POR 4 DIAS!
echo   - Painel local: http://localhost:8080
echo   - Logs salvos em: logs\
echo   - Cortes salvos em: output\
echo   - Windows travado contra hibernacao/suspensao (powercfg + Win32 Lock)
echo ============================================================================
pause
