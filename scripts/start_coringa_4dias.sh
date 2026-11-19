#!/usr/bin/env bash
# ==============================================================================
# PILOTO AUTOMÁTICO 4 DIAS — CORINGA E SUA RODA (Linux / macOS / WSL2)
# Inicia imediatamente o monitor multi-live (Kick + Twitch + YouTube) dos 16
# streamers da roda do Coringa + 5 canais (60 cortes/dia) + anti-hibernação.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

if [[ ! -f ".env" ]]; then
    echo "[AVISO] .env não encontrado. Copiando .env.example -> .env ..."
    cp .env.example .env
    echo "Edite o .env (DRY_RUN=false, POST_FOR_ME_API_KEY, GROQ_API_KEY) e rode novamente!"
    exit 1
fi

if [[ ! -x ".venv/bin/python" ]]; then
    echo "[SETUP] Criando .venv via uv sync..."
    uv sync
fi

export REAL_OFICIAL_ENABLED=false
export AUTOPILOT_MODE=full_auto
export MULTI_CHANNEL_ENABLED=true

echo "========================================================================"
echo "  PILOTO AUTOMATICO 4 DIAS — CORINGA E SUA RODA (KICK + TWITCH + YT)"
echo "  5 Canais x 12 Cortes/Dia | Monitor 5 Lives Simultaneas | Watchdog 24/7"
echo "========================================================================"

exec bash "${PROJECT_ROOT}/scripts/start_2pc_linux.sh" --run-now
