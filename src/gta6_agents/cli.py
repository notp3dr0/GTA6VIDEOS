"""Typer + Rich Command-Line Interface for GTA 6 & Real Oficial Multi-Agent System."""

from __future__ import annotations

import asyncio
import logging
import shutil
import socket
from pathlib import Path
from typing import Any, Optional

import typer
import uvicorn
from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table

from gta6_agents.config import AppConfig, load_config
from gta6_agents.models import ContentPillar
from gta6_agents.storage import StateStore

app = typer.Typer(
    name="gta6-agents",
    help="Autonomous Multi-Agent System for Faceless GTA 6 Clipping & Real Oficial (gta-vi-youtube) Championship.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


def _configure_logging(logs_dir: Path | None = None, verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    handlers: list[logging.Handler] = [
        RichHandler(console=console, rich_tracebacks=True, show_path=False)
    ]
    if logs_dir is not None:
        logs_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(logs_dir / "gta6_agents.log", encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        handlers.append(file_handler)

    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=handlers,
        force=True,
    )


def _badge(ok: bool, yes_text: str = "READY", no_text: str = "MISSING") -> str:
    return f"[bold green]{yes_text}[/bold green]" if ok else f"[yellow]{no_text}[/yellow]"


def _is_port_available(host: str, port: int) -> bool:
    check_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((check_host, port)) != 0


def _resolve_dashboard_app(config: AppConfig) -> Any:
    from gta6_agents.dashboard.server import create_dashboard_app

    return create_dashboard_app(config)


def _count_seen_sources(store: StateStore) -> int:
    try:
        with store._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS total FROM seen_sources").fetchone()
            return int(row["total"]) if row else 0
    except Exception:
        return 0


def _render_cycle_results(summary: dict[str, Any], title: str) -> None:
    if summary.get("paused"):
        console.print(
            Panel(
                "[yellow]System is currently PAUSED (`paused=true`). Send `/resume` on Telegram or click Resume in Mission Control.[/yellow]",
                title=title,
                border_style="yellow",
            )
        )
        return

    kpi_table = Table(title=title, show_header=True, header_style="bold magenta")
    kpi_table.add_column("Metric", style="cyan")
    kpi_table.add_column("Value", style="bold white")
    kpi_table.add_row("Execution Mode", "DRY_RUN (Safe Simulation)" if summary.get("dry_run") else "LIVE")
    kpi_table.add_row("Autopilot Mode", str(summary.get("autopilot_mode", "")))
    kpi_table.add_row("Candidates Discovered", str(summary.get("discovered", 0)))
    kpi_table.add_row("9:16 Clips Rendered (1080x1920)", str(summary.get("rendered", 0)))
    kpi_table.add_row("Platform Publications", str(summary.get("published_posts", 0)))
    kpi_table.add_row("Real Oficial (gta-vi-youtube) Entries", str(summary.get("championship_entries", 0)))
    kpi_table.add_row("Pending Human Approval", str(summary.get("pending_approval", 0)))
    kpi_table.add_row("Compliance Rejected", str(summary.get("rejected", 0)))
    kpi_table.add_row("Errors", str(len(summary.get("errors", []))))
    console.print(kpi_table)

    items = summary.get("items") or []
    published_items = [it for it in items if it.get("status") == "published"]
    if published_items:
        clip_table = Table(
            title="Rendered 1080x1920 Shorts & Campaign Submissions",
            show_header=True,
            header_style="bold green",
        )
        clip_table.add_column("Plan ID", style="cyan")
        clip_table.add_column("Hook (0-2s)", style="white")
        clip_table.add_column("MP4 Video Path", style="green")
        clip_table.add_column("Subtitles (.ass)", style="dim")
        clip_table.add_column("Real Oficial Status", style="magenta")

        for item in published_items:
            entries = item.get("championship_entries") or []
            ro_status = (
                ", ".join(f"{e.get('status')} ({e.get('campaign_slug')})" for e in entries)
                if entries
                else "n/a"
            )
            clip_table.add_row(
                str(item.get("plan_id", "")),
                str(item.get("hook", ""))[:48],
                str(item.get("video_path", "")),
                str(item.get("ass_subtitle_path") or "burned-in"),
                ro_status,
            )
        console.print(clip_table)

    learning = summary.get("learning") or {}
    if learning:
        recs = learning.get("recommendations") or []
        rec_text = "\n".join(f"• {r}" for r in recs[:3])
        console.print(
            Panel(
                f"[bold cyan]Validated Views:[/bold cyan] {learning.get('validated_views', 0):,}   "
                f"[bold green]Est. Earnings:[/bold green] R$ {float(learning.get('estimated_earnings_brl', 0.0)):.2f}\n"
                f"[bold yellow]Insights Logged:[/bold yellow] {learning.get('learning_log_path', 'memory/learning_loop.md')}\n\n"
                f"{rec_text}",
                title="Analytics & Learning Loop",
                border_style="cyan",
            )
        )


@app.command("status")
def status() -> None:
    """Show full health check, configured keys, Real Oficial gta-vi-youtube config, and DB counts."""
    config = load_config()
    store = StateStore(data_dir=config.data_dir, memory_dir=config.memory_dir)

    paused = store.get_flag("paused", "false").strip().lower() == "true"
    heartbeat = store.get_flag("last_heartbeat_at", "never")
    seen_count = _count_seen_sources(store)
    pubs = store.list_publications(limit=5000)
    entries = store.list_championship_entries(limit=5000)
    pending = store.list_pending_approvals()
    credits_today = store.today_credit_spend("real_oficial")

    total_views = sum(int(e.get("validated_views") or 0) for e in entries)
    est_brl = round(
        sum(
            (int(e.get("validated_views") or 0) / 1000.0)
            * float(e.get("estimated_cpm_brl") or 4.00)
            for e in entries
        ),
        2,
    )

    console.print(
        Panel(
            f"[bold magenta]GTA 6 Fast Clips & Real Oficial (`{config.real_oficial_campaign_slug}`)[/bold magenta]\n"
            f"State: {'[bold yellow]PAUSED[/bold yellow]' if paused else '[bold green]ACTIVE[/bold green]'} | "
            f"Mode: {'[yellow]DRY_RUN=true[/yellow]' if config.dry_run else '[bold green]LIVE (DRY_RUN=false)[/bold green]'} | "
            f"Autopilot: [cyan]{config.autopilot_mode}[/cyan] | TZ: [cyan]{config.timezone}[/cyan]\n"
            f"Last Heartbeat: [dim]{heartbeat}[/dim]",
            title="System Overview",
            border_style="magenta",
        )
    )

    health_table = Table(title="Toolchain & API Credentials Check", header_style="bold cyan")
    health_table.add_column("Component", style="white")
    health_table.add_column("Status", style="white")
    health_table.add_column("Details", style="dim")

    ffmpeg_bin = shutil.which("ffmpeg")
    ffprobe_bin = shutil.which("ffprobe")
    ytdlp_bin = shutil.which("yt-dlp")

    health_table.add_row("FFmpeg / FFprobe", _badge(bool(ffmpeg_bin and ffprobe_bin)), ffmpeg_bin or "Install ffmpeg on PATH")
    health_table.add_row("yt-dlp", _badge(bool(ytdlp_bin)), ytdlp_bin or "Synthetic fallback available")

    llm_has_key = bool(
        (config.llm_provider == "gemini" and config.gemini_api_key)
        or (config.llm_provider == "openai" and config.openai_api_key)
        or (config.llm_provider == "groq" and config.groq_api_key)
    )
    health_table.add_row(
        f"LLM ({config.llm_provider}:{config.llm_model})",
        _badge(llm_has_key, "API KEY SET", "TEMPLATE FALLBACK"),
        "Tri-Engine Pool (Claude Pro + Codex Plus + Antigravity Ultra) + PT-BR fallback",
    )

    ro_has_auth = bool(config.real_oficial_api_key or config.real_oficial_mcp_token)
    health_table.add_row(
        "Real Oficial API / MCP",
        _badge(ro_has_auth, "CONNECTED", "1-CLICK QUEUE MODE"),
        f"Slug: {config.real_oficial_campaign_slug} | Source: KY4DWumZffg | Credits: {credits_today}/{config.real_oficial_daily_credit_limit}",
    )

    ig_tt_ready = bool(config.instagram_access_token or config.tiktok_access_token)
    health_table.add_row(
        "TikTok + Instagram Reels",
        _badge(ig_tt_ready, "API READY", "DRY-RUN / LAUNCHER"),
        f"IG={bool(config.instagram_access_token)} | TikTok={bool(config.tiktok_access_token)} (Campanha paga R$ 4/1k > 5k views)",
    )

    yt_has_auth = bool(
        config.youtube_client_id and config.youtube_client_secret and config.youtube_refresh_token
    )
    health_table.add_row(
        "YouTube Data API v3",
        _badge(yt_has_auth, "OAUTH READY", "POS-LIVE / UNSET"),
        f"Enabled={config.youtube_enabled} | Channel={config.youtube_channel_handle or '5-channel matrix'}",
    )

    tg_ready = bool(config.telegram_bot_token and config.telegram_chat_id)
    dc_ready = bool(config.discord_webhook_url)
    health_table.add_row(
        "Mobile Remote Control",
        _badge(tg_ready or dc_ready, "CONFIGURED", "OPTIONAL"),
        f"Telegram={'ON' if tg_ready else 'OFF'} | Discord={'ON' if dc_ready else 'OFF'}",
    )
    console.print(health_table)

    campaign_cfg = (config.campaign or {}).get("campaign", {})
    rules_cfg = campaign_cfg.get("rules", {}) if isinstance(campaign_cfg, dict) else {}
    camp_url = campaign_cfg.get(
        "campaign_url",
        f"https://app.realoficial.com.br/c/{config.real_oficial_campaign_slug}?ro_vid={config.real_oficial_ref_vid}",
    )

    db_table = Table(title="Database & Real Oficial Championship Telemetry", header_style="bold green")
    db_table.add_column("Item", style="cyan")
    db_table.add_column("Value", style="bold white")
    db_table.add_row("SQLite State DB", str(store.db_path))
    db_table.add_row("Campaign URL", str(camp_url))
    db_table.add_row(
        "5-Channel Matrix (BRT)",
        "5 páginas PT-BR × 12 cortes/dia = 60 cortes/dia (120 posts TikTok + Reels; Turbo Skorpion 22:30-22:57)",
    )
    db_table.add_row(
        "Required Hashtag (Exclusive)",
        " ".join(rules_cfg.get("required_hashtags", ["#launchdayonYT"])),
    )
    db_table.add_row("Seen Sources (Deduplicated)", str(seen_count))
    db_table.add_row("Total Publications", str(len(pubs)))
    db_table.add_row("Championship Entries (gta-vi-youtube)", str(len(entries)))
    db_table.add_row("Validated Views / Est. BRL", f"{total_views:,} views  |  R$ {est_brl:.2f}")
    db_table.add_row("Pending Human Approvals", str(len(pending)))
    console.print(db_table)


@app.command("demo-run")
def demo_run(
    max_items: int = typer.Option(
        2,
        "--max-items",
        "-n",
        help="Number of real 1080x1920 MP4 clips to generate in the demo cycle.",
    ),
    include_x: bool = typer.Option(
        True,
        "--include-x/--no-include-x",
        help="Also run one simulated X text slot.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging."),
) -> None:
    """Run a complete end-to-end multi-agent cycle with synthetic/demo video fallback in safe DRY_RUN mode."""
    config = load_config()
    config.dry_run = True
    config.autopilot_mode = "full_auto"
    _configure_logging(config.logs_dir, verbose=verbose)

    store = StateStore(data_dir=config.data_dir, memory_dir=config.memory_dir)
    was_paused = store.get_flag("paused", "false")
    if was_paused.strip().lower() == "true":
        store.set_flag("paused", "false")

    from gta6_agents.orchestrator.pipeline import MultiAgentOrchestrator

    console.print(
        Panel(
            f"Starting end-to-end [bold cyan]demo-run[/bold cyan] (`max_items={max_items}`, `1080x1920` H.264 + burned `.ass` captions + `gta-vi-youtube` registration)...",
            title="GTA 6 Multi-Agent Demo",
            border_style="cyan",
        )
    )

    async def _execute_demo() -> dict[str, Any]:
        orchestrator = MultiAgentOrchestrator(config, store=store)
        cycle_summary = await orchestrator.run_daily_cycle(
            max_items=max_items,
            include_campaign=True,
            demo_video_fallback=True,
        )
        if include_x:
            x_pub = await orchestrator.run_x_text_slot(ContentPillar.OFFICIAL_NEWS)
            if x_pub is not None:
                cycle_summary["x_demo_publication"] = x_pub.model_dump(mode="json")
        return cycle_summary

    try:
        summary = asyncio.run(_execute_demo())
    finally:
        if was_paused.strip().lower() == "true":
            store.set_flag("paused", "true")

    _render_cycle_results(summary, title="Demo Cycle Results (Safe Dry-Run)")


@app.command("run-once")
def run_once(
    max_items: int = typer.Option(
        4,
        "--max-items",
        "-n",
        help="Maximum source candidates to process in this cycle.",
    ),
    include_campaign: bool = typer.Option(
        True,
        "--campaign/--no-campaign",
        help="Include Real Oficial gta-vi-youtube acervo & registration.",
    ),
    demo_fallback: bool = typer.Option(
        False,
        "--demo-fallback",
        help="Fallback to synthetic 1080p source if yt-dlp download fails.",
    ),
    x_pillar: Optional[str] = typer.Option(
        None,
        "--x-pillar",
        help="Optional X text slot pillar (e.g. official_news, tips_tutorials, countdown_recap).",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging."),
) -> None:
    """Run one complete multi-agent discovery, rendering, publishing, and campaign cycle immediately."""
    config = load_config()
    _configure_logging(config.logs_dir, verbose=verbose)
    store = StateStore(data_dir=config.data_dir, memory_dir=config.memory_dir)

    from gta6_agents.orchestrator.pipeline import MultiAgentOrchestrator

    async def _execute_once() -> dict[str, Any]:
        orchestrator = MultiAgentOrchestrator(config, store=store)
        summary = await orchestrator.run_daily_cycle(
            max_items=max_items,
            include_campaign=include_campaign,
            demo_video_fallback=demo_fallback,
        )
        if x_pillar:
            pillar_enum = ContentPillar(x_pillar.strip().lower())
            x_pub = await orchestrator.run_x_text_slot(pillar_enum)
            if x_pub is not None:
                summary["x_slot_publication"] = x_pub.model_dump(mode="json")
        return summary

    summary = asyncio.run(_execute_once())
    _render_cycle_results(summary, title="Single Cycle Execution Summary")


@app.command("daemon")
def daemon(
    with_dashboard: bool = typer.Option(
        True,
        "--with-dashboard/--no-dashboard",
        help="Serve the Mission Control web dashboard in the background.",
    ),
    host: str = typer.Option("0.0.0.0", "--host", help="Dashboard bind address."),
    port: int = typer.Option(8080, "--port", "-p", help="Dashboard port."),
    run_now: bool = typer.Option(
        False,
        "--run-now/--no-run-now",
        help="Trigger one immediate cycle on startup before waiting for scheduled BRT slots.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging."),
) -> None:
    """Start the 24/7 5-day autonomous scheduler + optional Mission Control server on port 8080."""
    config = load_config()
    _configure_logging(config.logs_dir, verbose=verbose)
    store = StateStore(data_dir=config.data_dir, memory_dir=config.memory_dir)

    from gta6_agents.orchestrator.pipeline import MultiAgentOrchestrator
    from gta6_agents.orchestrator.scheduler import DailyScheduler

    orchestrator = MultiAgentOrchestrator(config, store=store)
    scheduler = DailyScheduler(config, orchestrator=orchestrator, store=store)
    jobs = scheduler.configure_jobs()

    jobs_table = Table(
        title=f"24/7 Autonomous Schedule ({config.timezone})",
        header_style="bold magenta",
    )
    jobs_table.add_column("Job ID", style="cyan")
    jobs_table.add_column("Description", style="white")
    jobs_table.add_column("Trigger", style="green")
    for job in jobs:
        jobs_table.add_row(str(job["id"]), str(job["name"]), str(job["trigger"]))
    console.print(jobs_table)

    async def _run_daemon_loop() -> None:
        uv_server: uvicorn.Server | None = None
        uv_task: asyncio.Task[Any] | None = None

        if with_dashboard:
            if _is_port_available(host, port):
                dashboard_app = _resolve_dashboard_app(config)
                uv_cfg = uvicorn.Config(
                    dashboard_app,
                    host=host,
                    port=port,
                    log_level="warning",
                )
                uv_server = uvicorn.Server(uv_cfg)
                uv_server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
                uv_task = asyncio.create_task(uv_server.serve())
                console.print(
                    f"[bold green]Mission Control Dashboard listening at:[/bold green] http://{host}:{port}"
                )
            else:
                console.print(
                    f"[yellow]Port {port} is already in use (external dashboard active); continuing with scheduler.[/yellow]"
                )

        if run_now:
            console.print("[cyan]Running startup cycle (--run-now)...[/cyan]")
            await orchestrator.run_daily_cycle(
                max_items=1,
                include_campaign=True,
                demo_video_fallback=config.dry_run,
            )

        console.print(
            "[bold green]24/7 Autonomous Daemon is LIVE for your 5-day away window! Press Ctrl+C to stop.[/bold green]"
        )
        try:
            await scheduler.run_forever()
        finally:
            if uv_server is not None:
                uv_server.should_exit = True
            if uv_task is not None:
                try:
                    await asyncio.wait_for(uv_task, timeout=5.0)
                except Exception:
                    uv_task.cancel()

    try:
        asyncio.run(_run_daemon_loop())
    except KeyboardInterrupt:
        console.print("\n[yellow]Daemon stopped cleanly.[/yellow]")


@app.command("dashboard")
def dashboard(
    host: str = typer.Option("0.0.0.0", "--host", help="Host interface to bind."),
    port: int = typer.Option(8080, "--port", "-p", help="Port for Mission Control web UI."),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload for local dev."),
) -> None:
    """Launch the Mission Control FastAPI web dashboard (uvicorn)."""
    config = load_config()
    console.print(
        Panel(
            f"Launching [bold magenta]GTA 6 & Real Oficial Mission Control[/bold magenta] on [bold cyan]http://{host}:{port}[/bold cyan]",
            border_style="magenta",
        )
    )
    if reload:
        uvicorn.run(
            "gta6_agents.dashboard.server:app",
            host=host,
            port=port,
            reload=True,
        )
    else:
        dashboard_app = _resolve_dashboard_app(config)
        uvicorn.run(dashboard_app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    app()
