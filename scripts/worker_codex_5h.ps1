<#
.SYNOPSIS
    Worker Continuo Codex Plus (Sol 6.2 xhigh p/ pensar | low p/ executar) com Auto-Resume da janela de 5h.
    Sempre retoma checkpoints inacabados em data/state.db assim que a janela de 5h reseta!
#>

$ErrorActionPreference = "Continue"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

$RoutineFile = Join-Path $RepoRoot "routines\CODEX_PLUS_5H_ROUTINE.md"
$LogDir = Join-Path $RepoRoot "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
$LogFile = Join-Path $LogDir "codex_5h_worker.log"

$ThinkModel = if ($env:CODEX_THINK_MODEL) { $env:CODEX_THINK_MODEL } else { "sol-6.2" }
$ExecModel = if ($env:CODEX_EXEC_MODEL) { $env:CODEX_EXEC_MODEL } else { "sol-6.2" }

function Write-WorkerLog([string]$Msg, [string]$Color = "White") {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $Msg"
    Write-Host $line -ForegroundColor $Color
    Add-Content -Path $LogFile -Value $line
}

while ($true) {
    $PromptBase = Get-Content -Path $RoutineFile -Raw -Encoding UTF8

    # Etapa 1: THINK (xhigh) - retoma checkpoints pendentes e arquiteta roteiros/cortes exclusivos
    Write-WorkerLog "[CODEX THINK - $ThinkModel (xhigh)] Checando checkpoints interrompidos e planejando lote..." "Cyan"
    $ThinkPrompt = "$PromptBase`n`nMODO ATUAL: THINK_XHIGH. Retome primeiro qualquer checkpoint inacabado em task_checkpoints e arquitete os ganchos/roteiros exclusivos dos canais atribuidos."

    $Output = & codex exec --skip-git-repo-check -m $ThinkModel -c 'model_reasoning_effort="xhigh"' $ThinkPrompt 2>&1 | Out-String
    $ExitCode = $LASTEXITCODE

    if ($ExitCode -ne 0 -or $Output -match "(?i)(5-hour|5 hour|usage limit|rate limit|quota|try again|resets|429)") {
        Write-WorkerLog "[CODEX 5H LIMIT] Janela de 5h atingida! Marcando cooldown no banco para Claude Pro + Antigravity Ultra cobrirem os slots..." "Yellow"
        uv run python -c "from gta6_agents.config import load_config; from gta6_agents.storage import StateStore; from gta6_agents.orchestrator.tri_engine_pool import TriEnginePool, AIEngine; c=load_config(); s=StateStore(c.data_dir,c.memory_dir); TriEnginePool(c,s).mark_engine_5h_cooldown(AIEngine.CODEX_PLUS, 18000, 'Codex CLI 5h limit')" 2>$null
        while ($true) {
            Start-Sleep -Seconds 900
            $Probe = & codex exec --skip-git-repo-check -m $ExecModel -c 'model_reasoning_effort="low"' "Reply OK" 2>&1 | Out-String
            if ($LASTEXITCODE -eq 0 -and $Probe -match "OK") {
                Write-WorkerLog "[CODEX RESUME] Janela de 5h liberada! Retomando tarefas do checkpoint exato imediatamente!" "Green"
                uv run python -c "from gta6_agents.config import load_config; from gta6_agents.storage import StateStore; from gta6_agents.orchestrator.tri_engine_pool import TriEnginePool, AIEngine; c=load_config(); s=StateStore(c.data_dir,c.memory_dir); TriEnginePool(c,s).mark_engine_5h_cooldown(AIEngine.CODEX_PLUS, 0, 'recovered'); TriEnginePool(c,s).clear_expired_cooldowns()" 2>$null
                break
            }
            Write-WorkerLog "[CODEX SONDA 15m] Ainda em janela de 5h. Claude + Antigravity seguem cobrindo os 60 videos/dia..." "DarkYellow"
        }
        continue
    }

    # Etapa 2: EXECUTE (low) - execucao rapida de legendas, metadados e validacao
    Write-WorkerLog "[CODEX EXEC - $ExecModel (low)] Executando tarefas mecânicas em READY_TO_EXECUTE..." "Green"
    $ExecPrompt = "$PromptBase`n`nMODO ATUAL: EXECUTE_LOW. Processe os checkpoints em READY_TO_EXECUTE gerando copies, tempos de legenda .ass e JSONs finais."
    $ExecOut = & codex exec --skip-git-repo-check -m $ExecModel -c 'model_reasoning_effort="low"' $ExecPrompt 2>&1 | Out-String

    Write-WorkerLog "[CODEX CICLO OK] Lote salvo no SQLite. Aguardando 6 minutos para o proximo ciclo..." "Green"
    Start-Sleep -Seconds 360
}
