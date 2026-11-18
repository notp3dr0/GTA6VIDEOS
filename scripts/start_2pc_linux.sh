#!/usr/bin/env bash
# ==============================================================================
# GTA 6 & Real Oficial (gta-vi-youtube) — 5-Day 2nd-PC Keep-Awake & Watchdog
# Supports Linux (systemd-inhibit) and WSL2 (host PowerShell ExecutionState).
# ==============================================================================

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

export GTA6_PROJECT_ROOT="${PROJECT_ROOT}"
export PYTHONPATH="${PROJECT_ROOT}/src:${PYTHONPATH:-}"
export PYTHONUTF8=1

DASHBOARD_PORT="${DASHBOARD_PORT:-8080}"
RESTART_DELAY_SEC="${RESTART_DELAY_SEC:-15}"
RUN_NOW_FLAG="${1:-}"

LOGS_DIR="${PROJECT_ROOT}/logs"
mkdir -p "${LOGS_DIR}"

echo "====================================================================="
echo " GTA 6 & Real Oficial (gta-vi-youtube) - 2nd PC 5-Day Autopilot (SH)"
echo " Project Root: ${PROJECT_ROOT}"
echo "====================================================================="

INHIBIT_PID=""
WSL_KEEP_AWAKE_PID=""
DASHBOARD_LOOP_PID=""

cleanup() {
    echo ""
    echo "[shutdown] Stopping background Mission Control and releasing sleep locks..."
    if [[ -n "${DASHBOARD_LOOP_PID}" ]] && kill -0 "${DASHBOARD_LOOP_PID}" 2>/dev/null; then
        kill "${DASHBOARD_LOOP_PID}" 2>/dev/null || true
    fi
    if [[ -n "${INHIBIT_PID}" ]] && kill -0 "${INHIBIT_PID}" 2>/dev/null; then
        kill "${INHIBIT_PID}" 2>/dev/null || true
    fi
    if [[ -n "${WSL_KEEP_AWAKE_PID}" ]] && kill -0 "${WSL_KEEP_AWAKE_PID}" 2>/dev/null; then
        kill "${WSL_KEEP_AWAKE_PID}" 2>/dev/null || true
    fi
    echo "[shutdown] 2nd PC Autopilot stopped cleanly."
}
trap cleanup INT TERM EXIT

# 1. Activate OS Keep-Awake (systemd-inhibit on Linux / PowerShell on WSL2 / caffeinate)
if [[ -f /proc/sys/fs/binfmt_misc/WSLInterop ]] && command -v powershell.exe >/dev/null 2>&1; then
    echo "[1/3] WSL2 detected: locking Windows host ExecutionState via powershell.exe..."
    powershell.exe -NoProfile -Command '
      $code = "[DllImport(\"kernel32.dll\")] public static extern uint SetThreadExecutionState(uint f);";
      $t = Add-Type -MemberDefinition $code -Name "WinKeepAwake" -Namespace "GTA6" -PassThru;
      while ($true) { $t::SetThreadExecutionState(0x80000041) | Out-Null; Start-Sleep -Seconds 60 }
    ' >/dev/null 2>&1 &
    WSL_KEEP_AWAKE_PID=$!
elif command -v systemd-inhibit >/dev/null 2>&1; then
    echo "[1/3] Acquiring systemd-inhibit idle/sleep lock..."
    systemd-inhibit \
        --what=idle:sleep:handle-lid-switch \
        --who="GTA6-Agents-Autopilot" \
        --why="5-Day GTA 6 & Real Oficial Autonomous Publishing" \
        --mode=block \
        sleep infinity >/dev/null 2>&1 &
    INHIBIT_PID=$!
elif command -v caffeinate >/dev/null 2>&1; then
    echo "[1/3] Acquiring caffeinate keep-awake lock..."
    caffeinate -dims >/dev/null 2>&1 &
    INHIBIT_PID=$!
else
    echo "[1/3] No systemd-inhibit/WSL2 host lock found; ensure OS sleep is disabled in power settings."
fi

# Helper to invoke CLI using .venv, uv, or python3 -m gta6_agents.cli
run_gta6_cli() {
    if [[ -x "${PROJECT_ROOT}/.venv/bin/gta6-agents" ]]; then
        "${PROJECT_ROOT}/.venv/bin/gta6-agents" "$@"
    elif command -v uv >/dev/null 2>&1; then
        uv run gta6-agents "$@"
    elif [[ -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
        "${PROJECT_ROOT}/.venv/bin/python" -m gta6_agents.cli "$@"
    else
        python3 -m gta6_agents.cli "$@"
    fi
}

# 2. Start Mission Control Web Dashboard in a background watchdog subshell
echo "[2/3] Starting Mission Control Dashboard on http://0.0.0.0:${DASHBOARD_PORT} ..."
(
    while true; do
        STAMP="$(date +%Y%m%d_%H%M%S)"
        DASH_LOG="${LOGS_DIR}/dashboard_${STAMP}.log"
        run_gta6_cli dashboard --host 0.0.0.0 --port "${DASHBOARD_PORT}" >>"${DASH_LOG}" 2>&1
        sleep 10
    done
) &
DASHBOARD_LOOP_PID=$!

# 3. Infinite 5-Day Watchdog Loop for `gta6-agents daemon`
echo "[3/3] Entering 5-Day Infinite Watchdog Loop for gta6-agents daemon (Ctrl+C to stop)..."
ATTEMPT=1
while true; do
    STAMP="$(date +%Y%m%d_%H%M%S)"
    DAEMON_LOG="${LOGS_DIR}/daemon_${STAMP}.log"

    EXTRA_ARGS=()
    if [[ "${RUN_NOW_FLAG}" == "--run-now" && "${ATTEMPT}" -eq 1 ]]; then
        EXTRA_ARGS+=("--run-now")
    fi

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Launching daemon (attempt #${ATTEMPT}) -> ${DAEMON_LOG}"
    run_gta6_cli daemon --no-dashboard --port "${DASHBOARD_PORT}" "${EXTRA_ARGS[@]}" 2>&1 | tee -a "${DAEMON_LOG}"
    EXIT_CODE=${PIPESTATUS[0]}

    ATTEMPT=$((ATTEMPT + 1))
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Daemon exited (code=${EXIT_CODE}). Restarting in ${RESTART_DELAY_SEC}s..."
    sleep "${RESTART_DELAY_SEC}"
done
