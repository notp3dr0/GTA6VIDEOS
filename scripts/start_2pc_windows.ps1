<#
.SYNOPSIS
    GTA 6 & Real Oficial (gta-vi-youtube) — 5-Day 2nd-PC Autonomous Keep-Awake & Watchdog (Windows)
.DESCRIPTION
    Disables Windows AC sleep/standby/hibernation via powercfg + Win32 SetThreadExecutionState,
    starts the Mission Control dashboard on port 8080, and runs `gta6-agents daemon` in an
    infinite auto-restart watchdog loop with timestamped logs in `logs\`.
#>

param(
    [int]$DashboardPort = 8080,
    [int]$RestartDelaySec = 15,
    [switch]$RunStartupCycle
)

$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
$env:GTA6_PROJECT_ROOT = $ProjectRoot
$env:PYTHONUTF8 = "1"

$LogsDir = Join-Path $ProjectRoot "logs"
if (-not (Test-Path $LogsDir)) {
    New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null
}

Write-Host "=====================================================================" -ForegroundColor Magenta
Write-Host " GTA 6 & Real Oficial (gta-vi-youtube) - 2nd PC 5-Day Autopilot" -ForegroundColor Cyan
Write-Host " Project Root: $ProjectRoot" -ForegroundColor Gray
Write-Host "=====================================================================" -ForegroundColor Magenta

# 1. Disable Windows AC Standby / Hibernate / Disk Sleep via powercfg
Write-Host "[1/4] Configuring Windows AC power settings (no sleep / standby)..." -ForegroundColor Yellow
try {
    & powercfg /change standby-timeout-ac 0 | Out-Null
    & powercfg /change hibernate-timeout-ac 0 | Out-Null
    & powercfg /change disk-timeout-ac 0 | Out-Null
    Write-Host "      powercfg standby-timeout-ac = 0 applied." -ForegroundColor Green
} catch {
    Write-Host "      powercfg warning (non-admin session): relying on Win32 SetThreadExecutionState." -ForegroundColor DarkYellow
}

# 2. Win32 Kernel32 SetThreadExecutionState P/Invoke Keep-Awake (works without Admin!)
Write-Host "[2/4] Activating Win32 SetThreadExecutionState keep-awake lock..." -ForegroundColor Yellow
$KeepAwakeCSharp = @"
using System;
using System.Runtime.InteropServices;

public static class KeepAwakeNative {
    [DllImport("kernel32.dll", CharSet = CharSet.Auto, SetLastError = true)]
    public static extern uint SetThreadExecutionState(uint esFlags);

    public const uint ES_CONTINUOUS       = 0x80000000;
    public const uint ES_SYSTEM_REQUIRED  = 0x00000001;
    public const uint ES_DISPLAY_REQUIRED = 0x00000002;
    public const uint ES_AWAYMODE_REQUIRED = 0x00000040;

    public static uint LockAwake() {
        return SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED);
    }

    public static uint ReleaseAwake() {
        return SetThreadExecutionState(ES_CONTINUOUS);
    }
}
"@

if (-not ("KeepAwakeNative" -as [type])) {
    Add-Type -TypeDefinition $KeepAwakeCSharp -Language CSharp
}
[KeepAwakeNative]::LockAwake() | Out-Null
Write-Host "      Win32 ExecutionState locked (ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED)." -ForegroundColor Green

# 3. Resolve CLI runner (uv -> .venv -> python -m gta6_agents.cli)
$VenvCli = Join-Path $ProjectRoot ".venv\Scripts\gta6-agents.exe"
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$UseUv = $null -ne (Get-Command "uv" -ErrorAction SilentlyContinue)

function Invoke-Gta6Cli {
    param([string[]]$CliArgs)
    if (Test-Path $VenvCli) {
        & $VenvCli @CliArgs
    } elseif ($UseUv) {
        & uv run gta6-agents @CliArgs
    } elseif (Test-Path $VenvPython) {
        $env:PYTHONPATH = Join-Path $ProjectRoot "src"
        & $VenvPython -m gta6_agents.cli @CliArgs
    } else {
        $env:PYTHONPATH = Join-Path $ProjectRoot "src"
        & python -m gta6_agents.cli @CliArgs
    }
}

# Detect LAN IP for easy mobile / laptop check before leaving home
$LanIp = (
    Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
    Select-Object -First 1 -ExpandProperty IPAddress
)
if (-not $LanIp) { $LanIp = "localhost" }

# 4. Start Mission Control Dashboard in a background auto-restarting PowerShell Job
Write-Host "[3/4] Starting Mission Control Web Dashboard on http://${LanIp}:${DashboardPort} ..." -ForegroundColor Yellow
$DashboardJob = Start-Job -Name "GTA6MissionControl" -ScriptBlock {
    param($Root, $Port, $Logs, $VenvCliPath, $VenvPyPath, $HasUv)
    Set-Location $Root
    $env:GTA6_PROJECT_ROOT = $Root
    $env:PYTHONUTF8 = "1"
    $env:PYTHONPATH = Join-Path $Root "src"

    while ($true) {
        $Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $DashLog = Join-Path $Logs "dashboard_${Stamp}.log"
        if (Test-Path $VenvCliPath) {
            & $VenvCliPath dashboard --host 0.0.0.0 --port $Port *>> $DashLog
        } elseif ($HasUv) {
            & uv run gta6-agents dashboard --host 0.0.0.0 --port $Port *>> $DashLog
        } elseif (Test-Path $VenvPyPath) {
            & $VenvPyPath -m gta6_agents.cli dashboard --host 0.0.0.0 --port $Port *>> $DashLog
        } else {
            & python -m gta6_agents.cli dashboard --host 0.0.0.0 --port $Port *>> $DashLog
        }
        Start-Sleep -Seconds 10
    }
} -ArgumentList $ProjectRoot, $DashboardPort, $LogsDir, $VenvCli, $VenvPython, $UseUv

Write-Host "      Mission Control active -> http://${LanIp}:${DashboardPort}" -ForegroundColor Green
Write-Host "[4/4] Entering 5-Day Infinite Watchdog Loop for `gta6-agents daemon` (Ctrl+C to stop)..." -ForegroundColor Cyan

$RestartCount = 0
try {
    while ($true) {
        # Re-assert Win32 keep-awake flag on every watchdog cycle
        [KeepAwakeNative]::LockAwake() | Out-Null

        $Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
        $DaemonLog = Join-Path $LogsDir "daemon_${Timestamp}.log"

        $DaemonArgs = @("daemon", "--no-dashboard", "--port", "$DashboardPort")
        if ($RunStartupCycle -and $RestartCount -eq 0) {
            $DaemonArgs += "--run-now"
        }

        Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Launching daemon (attempt #$($RestartCount + 1)) -> $DaemonLog" -ForegroundColor Green
        Invoke-Gta6Cli -CliArgs $DaemonArgs 2>&1 | Tee-Object -FilePath $DaemonLog -Append
        $ExitCode = $LASTEXITCODE

        $RestartCount++
        Write-Host "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] Daemon exited (code=$ExitCode). Auto-restarting in ${RestartDelaySec}s..." -ForegroundColor Yellow
        Start-Sleep -Seconds $RestartDelaySec
    }
} finally {
    Write-Host "`nCleaning up background Mission Control job and releasing Win32 ExecutionState..." -ForegroundColor Yellow
    if ($DashboardJob) {
        Stop-Job -Job $DashboardJob -ErrorAction SilentlyContinue
        Remove-Job -Job $DashboardJob -Force -ErrorAction SilentlyContinue
    }
    [KeepAwakeNative]::ReleaseAwake() | Out-Null
    Write-Host "2nd PC Autopilot stopped cleanly." -ForegroundColor Green
}
