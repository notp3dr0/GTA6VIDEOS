@echo off
cd /d "%~dp0"
echo ====================================================================
echo   PRE-AQUECENDO BANCO OFICIAL DE CLIPES DIA 1 (12 CORTES ROCKSTAR)
echo ====================================================================
uv run python -c "import asyncio; from gta6_agents.config import load_config; from gta6_agents.storage import StateStore; from gta6_agents.video.vault import OfficialClipVault; cfg=load_config(); st=StateStore(cfg.data_dir, cfg.memory_dir); print(asyncio.run(OfficialClipVault(cfg, st).prewarm_vault(count=12, use_demo_source_if_offline=False)))"
echo.
echo [CONCLUIDO] Clipes oficiais salvos em vault\ready\!
pause
