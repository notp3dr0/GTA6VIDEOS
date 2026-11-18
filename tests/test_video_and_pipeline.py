"""End-to-end tests for ClipperEditorAgent (FFmpeg 1080x1920 + .ass), MultiAgentOrchestrator, and Dashboard."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gta6_agents.agents.clipper import ClipperEditorAgent
from gta6_agents.config import AppConfig, get_project_root, load_config
from gta6_agents.dashboard.server import create_dashboard_app
from gta6_agents.models import (
    ClipPlan,
    ContentPillar,
    Platform,
    SourceCandidate,
    SubtitleCue,
)
from gta6_agents.orchestrator.pipeline import MultiAgentOrchestrator
from gta6_agents.storage import StateStore


def _make_pipeline_test_config(tmp_path: Path) -> AppConfig:
    repo_root = get_project_root()
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    for fname in ("settings.yaml", "campaign_realoficial.yaml"):
        src = repo_root / "config" / fname
        if src.exists():
            shutil.copy2(src, cfg_dir / fname)

    base = load_config(repo_root)
    return base.model_copy(
        update={
            "root_dir": tmp_path,
            "dry_run": True,
            "autopilot_mode": "full_auto",
            "llm_provider": "template_fallback",
            "gemini_api_key": "",
            "openai_api_key": "",
            "groq_api_key": "",
            "voiceover_mode": "none",
            "real_oficial_enabled": True,
            "real_oficial_campaign_slug": "gta-vi-youtube",
            "youtube_enabled": True,
        }
    )


@pytest.mark.asyncio
async def test_clipper_editor_renders_1080x1920_mp4_with_ass_subtitles(tmp_path: Path) -> None:
    cfg = _make_pipeline_test_config(tmp_path)
    store = StateStore(cfg.data_dir, cfg.memory_dir)
    clipper = ClipperEditorAgent(cfg, store)

    source = SourceCandidate(
        title="Grand Theft Auto VI Trailer 1 — Vice City Neon",
        url="https://www.youtube.com/watch?v=QdBZY2fkU-0",
        creator_handle="@RockstarGames",
        source_type="realoficial_acervo",
        is_official=True,
        is_campaign_acervo=True,
        campaign_slug="gta-vi-youtube",
        pillar=ContentPillar.REAL_OFICIAL_CAMPAIGN,
        verified_facts=["Vice City confirmada no estado de Leonida."],
        suggested_start_sec=0.0,
        suggested_end_sec=10.5,
    )
    plan = ClipPlan(
        source=source,
        pillar=ContentPillar.REAL_OFICIAL_CAMPAIGN,
        language="pt-BR",
        hook_on_screen="VOCÊ REPAROU NESSE DETALHE EM VICE CITY?",
        voiceover_script="Olha o nível de detalhe da iluminação neon em Leonida no trailer oficial.",
        commentary_overlay="🎥 @RockstarGames",
        start_sec=0.0,
        end_sec=10.5,
        target_platforms=[Platform.YOUTUBE_SHORTS],
        for_real_oficial_campaign=True,
        campaign_slug="gta-vi-youtube",
        subtitles=[
            SubtitleCue(start_sec=0.0, end_sec=3.5, text="VOCÊ REPAROU NESSE DETALHE EM VICE CITY?"),
            SubtitleCue(start_sec=3.5, end_sec=7.0, text="Reflexo neon realista nas ruas de Leonida!"),
            SubtitleCue(start_sec=7.0, end_sec=10.5, text="Crédito oficial @RockstarGames #GTA6"),
        ],
    )

    rendered = await clipper.render_clip_plan(plan, use_demo_source_if_needed=True)

    video_file = Path(rendered.video_path)
    assert video_file.exists(), f"Expected rendered mp4 at {video_file}"
    assert video_file.suffix.lower() == ".mp4"
    assert video_file.stat().st_size > 1000
    assert rendered.width == 1080
    assert rendered.height == 1920
    assert rendered.has_watermark_third_party is False

    # Verify actual dimensions on disk via ffprobe
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,codec_name",
            "-of",
            "json",
            str(video_file),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    probe_data = json.loads(probe.stdout)
    streams = probe_data.get("streams", [])
    assert len(streams) >= 1
    assert int(streams[0]["width"]) == 1080
    assert int(streams[0]["height"]) == 1920

    # Verify .ass subtitle file was generated on disk
    assert rendered.ass_subtitle_path is not None
    ass_file = Path(rendered.ass_subtitle_path)
    assert ass_file.exists(), f"Expected .ass subtitle file at {ass_file}"
    assert ass_file.suffix.lower() == ".ass"
    ass_content = ass_file.read_text(encoding="utf-8")
    assert "[Events]" in ass_content
    assert "Dialogue:" in ass_content


@pytest.mark.asyncio
async def test_orchestrator_daily_cycle_end_to_end_persists_state(tmp_path: Path) -> None:
    cfg = _make_pipeline_test_config(tmp_path)
    store = StateStore(cfg.data_dir, cfg.memory_dir)
    orchestrator = MultiAgentOrchestrator(cfg, store)

    summary = await orchestrator.run_daily_cycle(
        max_items=1,
        include_campaign=True,
        demo_video_fallback=True,
    )
    assert summary is not None

    pubs = store.list_publications(limit=20)
    assert len(pubs) >= 1, "Expected at least 1 PublicationRecord persisted in StateStore"
    assert store.posted_ledger_path.exists()

    entries = store.list_championship_entries(limit=20)
    assert len(entries) >= 1, "Expected at least 1 ChampionshipEntry persisted in StateStore"
    assert entries[0]["campaign_slug"] == "gta-vi-youtube"
    assert entries[0]["youtube_url"]


def test_mission_control_dashboard_routes(tmp_path: Path) -> None:
    cfg = _make_pipeline_test_config(tmp_path)
    dashboard_app = create_dashboard_app(cfg)
    store: StateStore = dashboard_app.state.store

    store.save_pending_approval("plan_demo_99", {"hook": "Novo detalhe em Leonida"})

    client = TestClient(dashboard_app)

    home_resp = client.get("/")
    assert home_resp.status_code == 200
    assert "LEONIDA MISSION CONTROL" in home_resp.text
    assert "gta-vi-youtube" in home_resp.text
    assert "Rodar Ciclo Demo Agora" in home_resp.text

    status_resp = client.get("/api/status")
    assert status_resp.status_code == 200
    status_json = status_resp.json()
    assert status_json["paused"] is False
    assert status_json["metrics"]["pending_approvals_count"] == 1
    assert len(status_json["agents"]) == 8

    pause_resp = client.post("/api/pause")
    assert pause_resp.status_code == 200
    assert pause_resp.json()["paused"] is True
    assert client.get("/api/status").json()["paused"] is True

    resume_resp = client.post("/api/resume")
    assert resume_resp.status_code == 200
    assert resume_resp.json()["paused"] is False

    approve_resp = client.post("/api/approvals/plan_demo_99/approved")
    assert approve_resp.status_code == 200
    assert approve_resp.json()["status"] == "approved"
    assert client.get("/api/status").json()["metrics"]["pending_approvals_count"] == 0
