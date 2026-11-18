<#
.SYNOPSIS
    Worker Continuo Claude Pro (Opus xhigh p/ pensar | Haiku low p/ executar) com Auto-Resume da janela de 5h.
    Sempre retoma checkpoints inacabados em data/state.db assim que a janela de 5h reseta!
#>

$ErrorActionPreference = "Continue"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

$RoutineFile = Join-Path $RepoRoot "routines\CLAUDE_PRO_5H_ROUTINE.md"
$LogDir = Join-Path $RepoRoot "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
$LogFile = Join-Path $LogDir "claude_5h_worker.log"

$ThinkModel = if ($env:CLAUDE_THINK_MODEL) { $env:CLAUDE_THINK_MODEL } else { "opus" }
$ThinkEffort = if ($env:CLAUDE_THINK_EFFORT) { $env:CLAUDE_THINK_EFFORT } else { "max" }
$ExecModel = if ($env:CLAUDE_EXEC_MODEL) { $env:CLAUDE_EXEC_MODEL } else { "haiku" }

function Write-WorkerLog([string]$Msg, [string]$Color = "White") {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $Msg"
    Write-Host $line -ForegroundColor $Color
    Add-Content -Path $LogFile -Value $line
}

while ($true) {
    $PromptBase = Get-Content -Path $RoutineFile -Raw -Encoding UTF8

    # Etapa 1: THINK (Opus xhigh/max) - retoma checkpoints pendentes e planeja proximos cortes dos 5 canais
    Write-WorkerLog "[CLAUDE THINK - $ThinkModel ($ThinkEffort)] Checando checkpoints pendentes e planejando lote..." "Cyan"
    $ThinkPrompt = "$PromptBase`n`nMODO ATUAL: THINK_XHIGH. Retome primeiro qualquer tarefa inacabada em task_checkpoints (PENDING_THINK) e planeje os ganchos dos proximos slots sem executar trabalho bracal."

    $Output = & claude -p --model $ThinkModel --effort $ThinkEffort $ThinkPrompt 2>&1 | Out-String
    $ExitCode = $LASTEXITCODE

    if ($ExitCode -ne 0 -or $Output -match "(?i)(5-hour|5 hour|usage limit|rate limit|hit your limit|resets|429)") {
        Write-WorkerLog "[CLAUDE 5H LIMIT] Janela de 5h atingida! Marcando cooldown no banco para Codex Plus + Antigravity Ultra assumirem sem parar o fluxo..." "Yellow"
        uv run python -c "from gta6_agents.config import load_config; from gta6_agents.storage import StateStore; from gta6_agents.orchestrator.tri_engine_pool import TriEnginePool, AIEngine; c=load_config(); s=StateStore(c.data_dir,c.memory_dir); TriEnginePool(c,s).mark_engine_5h_cooldown(AIEngine.CLAUDE_PRO, 18000, 'Claude CLI 5h limit')" 2>$null
        # Sonda leve a cada 15 min para voltar no minuto exato em que o limite de 5h liberar!
        while ($true) {
            Start-Sleep -Seconds 900
            $Probe = & claude -p --model $ExecModel "Reply OK" 2>&1 | Out-String
            if ($LASTEXITCODE -eq 0 -and $Probe -match "OK") {
                Write-WorkerLog "[CLAUDE RESUME] Janela de 5h liberada! Limpando cooldown e retomando tarefas interrompidas imediatamente!" "Green"
                uv run python -c "from gta6_agents.config import load_config; from gta6_agents.storage import StateStore; from gta6_agents.orchestrator.tri_engine_pool import TriEnginePool, AIEngine; c=load_config(); s=StateStore(c.data_dir,c.memory_dir); TriEnginePool(c,s).mark_engine_5h_cooldown(AIEngine.CLAUDE_PRO, 0, 'recovered'); TriEnginePool(c,s).clear_expired_cooldowns()" 2>$null
                break
            }
            Write-WorkerLog "[CLAUDE SONDA 15m] Ainda em janela de 5h. Codex + Antigravity seguem cobrindo os 60 videos/dia..." "DarkYellow"
        }
        continue
    }

    # Etapa 2: EXECUTE (Haiku/low) - trabalho bracal de legendas, tags e JSON sem gastar cota do Opus
    Write-WorkerLog "[CLAUDE EXEC - $ExecModel (low)] Executando formatacao em lote dos checkpoints READY_TO_EXECUTE..." "Green"
    $ExecPrompt = "$PromptBase`n`nMODO ATUAL: EXECUTE_LOW. Processe apenas os itens em READY_TO_EXECUTE gerando copies finais, hashtags e legendas .ass."
    $ExecOut = & claude -p --model $ExecModel $ExecPrompt 2>&1 | Out-String

    Write-WorkerLog "[CLAUDE CICLO OK] Lote salvo no SQLite. Aguardando 6 minutos para o proximo ciclo..." "Green"
    Start-Sleep -Seconds 360
}
