#!/usr/bin/env bash
# Worker Continuo Codex Plus (Sol 6.2 xhigh Think + low Exec) com Auto-Resume 5h
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 1
mkdir -p "$REPO_ROOT/logs"
LOG_FILE="$REPO_ROOT/logs/codex_5h_worker.log"
ROUTINE_FILE="$REPO_ROOT/routines/CODEX_PLUS_5H_ROUTINE.md"

THINK_MODEL="${CODEX_THINK_MODEL:-sol-6.2}"
EXEC_MODEL="${CODEX_EXEC_MODEL:-sol-6.2}"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

while true; do
  PROMPT_BASE="$(cat "$ROUTINE_FILE")"
  log "[CODEX THINK - $THINK_MODEL (xhigh)] Retomando checkpoints pendentes e arquitetando roteiros dos 5 canais..."

  OUTPUT="$(codex exec --skip-git-repo-check -m "$THINK_MODEL" -c 'model_reasoning_effort="xhigh"' "$PROMPT_BASE

MODO ATUAL: THINK_XHIGH. Retome primeiro qualquer checkpoint inacabado em task_checkpoints e planeje os proximos lotes." 2>&1)"
  CODE=$?

  if [[ $CODE -ne 0 ]] || echo "$OUTPUT" | grep -Eiq "5-hour|5 hour|usage limit|rate limit|quota|try again|resets|429"; then
    log "[CODEX 5H LIMIT] Janela de 5h atingida! Marcando cooldown no SQLite p/ Claude Pro + Antigravity Ultra assumirem..."
    uv run python -c "from gta6_agents.config import load_config; from gta6_agents.storage import StateStore; from gta6_agents.orchestrator.tri_engine_pool import TriEnginePool, AIEngine; c=load_config(); s=StateStore(c.data_dir,c.memory_dir); TriEnginePool(c,s).mark_engine_5h_cooldown(AIEngine.CODEX_PLUS, 18000, 'Codex CLI 5h limit')" 2>/dev/null || true
    while true; do
      sleep 900
      PROBE="$(codex exec --skip-git-repo-check -m "$EXEC_MODEL" -c 'model_reasoning_effort="low"' "Reply OK" 2>&1)"
      if [[ $? -eq 0 ]] && echo "$PROBE" | grep -q "OK"; then
        log "[CODEX RESUME] Janela de 5h liberada! Retomando tarefas interrompidas imediatamente!"
        uv run python -c "from gta6_agents.config import load_config; from gta6_agents.storage import StateStore; from gta6_agents.orchestrator.tri_engine_pool import TriEnginePool, AIEngine; c=load_config(); s=StateStore(c.data_dir,c.memory_dir); TriEnginePool(c,s).mark_engine_5h_cooldown(AIEngine.CODEX_PLUS, 0, 'recovered'); TriEnginePool(c,s).clear_expired_cooldowns()" 2>/dev/null || true
        break
      fi
      log "[CODEX SONDA 15m] Ainda em cooldown de 5h. Fluxo mantido por Claude + Antigravity..."
    done
    continue
  fi

  log "[CODEX EXEC - $EXEC_MODEL (low)] Executando lotes READY_TO_EXECUTE..."
  codex exec --skip-git-repo-check -m "$EXEC_MODEL" -c 'model_reasoning_effort="low"' "$PROMPT_BASE

MODO ATUAL: EXECUTE_LOW. Processe os checkpoints READY_TO_EXECUTE." >>"$LOG_FILE" 2>&1 || true

  log "[CODEX CICLO OK] Aguardando 6 minutos..."
  sleep 360
done
