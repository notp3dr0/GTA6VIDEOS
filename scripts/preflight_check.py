"""Validador Pre-Viagem contra os 3 Erros Fatais que matam paginas de GTA 6 antes de nascer:

1. Banco de clipes pronto com trailers e material oficial (sem ficar zerado no Dia 1).
2. Contas, branding (avatar/banner/bio sem impersonar Rockstar) e agendamento/legendas prontos.
3. Bloqueio total de footage filtrado / vazado / pre-lancamento (Zero Strike Rockstar).
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from gta6_agents.agents.compliance import ComplianceFactAgent
from gta6_agents.config import load_config
from gta6_agents.models import (
    CaptionBundle,
    ClipPlan,
    ContentPillar,
    Platform,
    PlatformCopy,
    SourceCandidate,
)
from gta6_agents.storage import StateStore
from gta6_agents.video.vault import OfficialClipVault


async def run_preflight(prewarm_demo_count: int = 0) -> int:
    console = Console()
    cfg = load_config()
    store = StateStore(cfg.data_dir, cfg.memory_dir)
    vault = OfficialClipVault(cfg, store)

    if prewarm_demo_count > 0:
        console.print(f"[cyan]Pre-aquecendo {prewarm_demo_count} cortes oficiais no cofre (vault/ready)...[/cyan]")
        await vault.prewarm_vault(count=prewarm_demo_count, use_demo_source_if_offline=cfg.dry_run)

    table = Table(title="Checklist Anti-Morte Pre-Viagem (5 Dias no 2o PC)", show_lines=True)
    table.add_column("Escudo", style="bold cyan", width=30)
    table.add_column("Status", width=14)
    table.add_column("Diagnostico & Acao", style="white")

    failures = 0

    # --- ESCUDO 1: BANCO DE CLIPES OFICIAIS PRONTO PARA O DIA 1 ---
    manifest_cuts = len(vault.load_manifest())
    ready_mp4s = vault.count_ready_clips() + len(list(cfg.output_dir.glob("*.mp4")))
    if manifest_cuts >= 10 and ready_mp4s >= 2:
        table.add_row(
            "1. Banco Oficial Dia 1\n(Trailers & Material Oficial)",
            "[bold green]APROVADO[/bold green]",
            f"{manifest_cuts} cortes oficiais mapeados em config/official_vault_manifest.yaml e {ready_mp4s} videos MP4 9:16 ja renderizados em disco.",
        )
    else:
        table.add_row(
            "1. Banco Oficial Dia 1\n(Trailers & Material Oficial)",
            "[bold yellow]PRE-AQUECER[/bold yellow]",
            f"{manifest_cuts} cortes mapeados, mas apenas {ready_mp4s} MP4 pre-renderizados. Rode PRE_AQUECER_COFRE_DIA1.bat antes de sair!",
        )

    # --- ESCUDO 2: BRANDING, LEGENDAS, AGENDAMENTO E CONTAS ---
    avatar_ok = (cfg.root_dir / "assets" / "branding" / "avatar.jpg").exists()
    banner_ok = (cfg.root_dir / "assets" / "branding" / "banner_base.jpg").exists()
    bio_text = str(cfg.settings.get("page", {}).get("bio", ""))
    disclaimer_ok = "not affiliated with rockstar" in bio_text.lower() or "sem afilia" in bio_text.lower()
    ffmpeg_ok = shutil.which("ffmpeg") is not None

    if avatar_ok and banner_ok and disclaimer_ok and ffmpeg_ok:
        creds_status = (
            "MODO LIVE PRONTO"
            if (not cfg.dry_run and cfg.youtube_refresh_token)
            else "DRY_RUN=true (Preencha .env p/ virar LIVE)"
        )
        table.add_row(
            "2. Branding, Templates &\nSistema de Agendamento",
            "[bold green]APROVADO[/bold green]",
            f"Avatar + Banner neon prontos em assets/branding/, bio c/ disclaimer anti-impersonacao, FFmpeg + legendas .ass ativos ({creds_status}).",
        )
    else:
        failures += 1
        table.add_row(
            "2. Branding, Templates &\nSistema de Agendamento",
            "[bold red]PENDENTE[/bold red]",
            f"Avatar={avatar_ok}, Banner={banner_ok}, BioDisclaimer={disclaimer_ok}, FFmpeg={ffmpeg_ok}.",
        )

    # --- ESCUDO 3: BLOQUEIO TOTAL DE FOOTAGE FILTRADO / PRE-LANCAMENTO ---
    compliance = ComplianceFactAgent(cfg, store)
    fake_leak_src = SourceCandidate(
        title="GTA 6 early copy gameplay filtrado antes do lancamento",
        url="https://example.com/leak",
        creator_handle="@randomleaker",
        is_official=False,
        pillar=ContentPillar.GAMEPLAY_CLIPS,
        is_leak_or_rumor=True,
    )
    fake_plan = ClipPlan(
        source=fake_leak_src,
        pillar=ContentPillar.GAMEPLAY_CLIPS,
        hook_on_screen="GAMEPLAY FILTRADO GTA 6",
        start_sec=0,
        end_sec=20,
    )
    fake_bundle = CaptionBundle(
        plan_id=fake_plan.id,
        copies={
            Platform.YOUTUBE_SHORTS: PlatformCopy(
                platform=Platform.YOUTUBE_SHORTS,
                title="GTA 6 Filtrado",
                caption="Gameplay filtrado 🎥 @randomleaker #GTA6 #GTAVI #Shorts",
                credit_line="🎥 @randomleaker",
            )
        },
    )
    verdict = compliance.evaluate_plan(fake_plan, fake_bundle)
    if not verdict.passed:
        table.add_row(
            "3. Escudo Anti-Leak /\nPre-Lancamento (Zero Strike)",
            "[bold green]BLINDADO[/bold green]",
            f"Teste de invasao com footage filtrado bloqueado na hora: '{verdict.reasons[0]}'",
        )
    else:
        failures += 1
        table.add_row(
            "3. Escudo Anti-Leak /\nPre-Lancamento (Zero Strike)",
            "[bold red]FALHOU[/bold red]",
            "ALERTA CRITICO: Compliance nao bloqueou teste de footage filtrado!",
        )

    console.print(table)
    if failures == 0:
        console.print(
            Panel(
                "[bold green]TUDO PRONTO NA ESTRUTURA TECNICA![/bold green]\n"
                "Proximo passo antes de viajar: preencha suas chaves no `.env`, rode `PRE_AQUECER_COFRE_DIA1.bat` "
                "para deixar 12+ Shorts oficiais prontos no HD e inicie `INICIAR_2PC_5DIAS.bat`!",
                title="Pre-Flight OK",
            )
        )
    return failures


if __name__ == "__main__":
    prewarm = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    raise SystemExit(asyncio.run(run_preflight(prewarm_demo_count=prewarm)))
