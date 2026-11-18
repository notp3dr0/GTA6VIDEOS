#!/usr/bin/env bash
# Worker Continuo Claude Pro (Opus xhigh Think + Haiku low Exec) com Auto-Resume 5h
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 1
mkdir -p "$REPO_ROOT/logs"
LOG_FILE="$REPO_ROOT/logs/claude_5h_worker.log"
ROUTINE_FILE="$REPO_ROOT/routines/CLAUDE_PRO_5H_ROUTINE.md"

THINK_MODEL="${CLAUDE_THINK_MODEL:-opus}"
THINK_EFFORT="${CLAUDE_THINK_EFFORT:-max}"
EXEC_MODEL="${CLAUDE_EXEC_MODEL:-haiku}"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

while true; do
  PROMPT_BASE="$(cat "$ROUTINE_FILE")"
  log "[CLAUDE THINK - $THINK_MODEL ($THINK_EFFORT)] Retomando checkpoints pendentes e planejando lote dos 5 canais..."

  OUTPUT="$(claude -p --model "$THINK_MODEL" --effort "$THINK_EFFORT" "$PROMPT_BASE

MODO ATUAL: THINK_XHIGH. Retome primeiro qualquer tarefa inacabada em task_checkpoints (PENDING_THINK) e planeje os ganchos dos proximos slots." 2>&1)"
  CODE=$?

  if [[ $CODE -ne 0 ]] || echo "$OUTPUT" | grep -Eiq "5-hour|5 hour|usage limit|rate limit|hit your limit|resets|429"; then
    log "[CLAUDE 5H LIMIT] Janela de 5h atingida! Marcando cooldown no SQLite p/ Codex Plus + Antigravity Ultra assumirem..."
    uv run python -c "from gta6_agents.config import load_config; from gta6_agents.storage import StateStore; from gta6_agents.orchestrator.tri_engine_pool import TriEnginePool, AIEngine; c=load_config(); s=StateStore(c.data_dir,c.memory_dir); TriEnginePool(c,s).mark_engine_5h_cooldown(AIEngine.CLAUDE_PRO, 18000, 'Claude CLI 5h limit')" 2>/dev/null || true
    while true; do
      sleep 900
      PROBE="$(claude -p --model "$EXEC_MODEL" "Reply OK" 2>&1)"
      if [[ $? -eq 0 ]] && echo "$PROBE" | grep -q "OK"; then
        log "[CLAUDE RESUME] Janela de 5h liberada! Retomando tarefas interrompidas imediatamente!"
        uv run python -c "from gta6_agents.config import load_config; from gta6_agents.storage import StateStore; from gta6_agents.orchestrator.tri_engine_pool import TriEnginePool, AIEngine; c=load_config(); s=StateStore(c.data_dir,c.memory_dir); TriEnginePool(c,s).mark_engine_5h_cooldown(AIEngine.CLAUDE_PRO, 0, 'recovered'); TriEnginePool(c,s).clear_expired_cooldowns()" 2>/dev/null || true
        break
      fi
      log "[CLAUDE SONDA 15m] Ainda em cooldown de 5h. Fluxo mantido por Codex + Antigravity..."
    done
    continue
  fi

  log "[CLAUDE EXEC - $EXEC_MODEL (low)] Formatando copies, legendas .ass e metadados em READY_TO_EXECUTE..."
  claude -p --model "$EXEC_MODEL" "$PROMPT_BASE

MODO ATUAL: EXECUTE_LOW. Processe os checkpoints READY_TO_EXECUTE." >>"$LOG_FILE" 2>&1 || true

  log "[CLAUDE CICLO OK] Aguardando 6 minutos..."
  sleep 360
done
