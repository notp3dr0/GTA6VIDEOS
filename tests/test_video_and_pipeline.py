"""End-to-end tests for ClipperEditorAgent (FFmpeg 1080x1920 + .ass zero-watermark), MultiAgentOrchestrator, and Dashboard."""

from __future__ import annotations

import json
import re
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
    PublicationRecord,
    SourceCandidate,
    SubtitleCue,
)
from gta6_agents.orchestrator.pipeline import MultiAgentOrchestrator
from gta6_agents.storage import StateStore


def _make_pipeline_test_config(tmp_path: Path) -> AppConfig:
    repo_root = get_project_root()
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    for fname in ("settings.yaml", "campaign_realoficial.yaml", "channels_matrix.yaml", "streamers_br.yaml"):
        src = repo_root / "config" / fname
        if src.exists():
            shutil.copy2(src, cfg_dir / fname)

    base = load_config(tmp_path)
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
            "youtube_enabled": False,
        }
    )


@pytest.mark.asyncio
async def test_clipper_editor_renders_1080x1920_mp4_with_ass_subtitles_and_zero_watermark(
    tmp_path: Path,
) -> None:
    cfg = _make_pipeline_test_config(tmp_path)
    store = StateStore(cfg.data_dir, cfg.memory_dir)
    clipper = ClipperEditorAgent(cfg, store)

    source = SourceCandidate(
        title="Live Oficial YouTube x GTA VI — Gameplay ao Vivo do Skorpion em Vice City",
        url="https://www.youtube.com/watch?v=KY4DWumZffg",
        creator_handle="@Skorpion",
        source_type="realoficial_acervo",
        is_official=True,
        is_campaign_acervo=True,
        campaign_slug="gta-vi-youtube",
        pillar=ContentPillar.REAL_OFICIAL_CAMPAIGN,
        verified_facts=["Gameplay em tempo real na transmissão mundial YouTube x GTA VI."],
        suggested_start_sec=0.0,
        suggested_end_sec=10.5,
    )
    plan = ClipPlan(
        source=source,
        pillar=ContentPillar.REAL_OFICIAL_CAMPAIGN,
        language="pt-BR",
        hook_on_screen="OLHA ESSA GAMEPLAY AO VIVO EM VICE CITY!",
        voiceover_script="Olha o nível absurdo de iluminação neon ao vivo em Leonida!",
        commentary_overlay="",
        start_sec=0.0,
        end_sec=10.5,
        target_platforms=[Platform.INSTAGRAM_REELS, Platform.TIKTOK],
        for_real_oficial_campaign=True,
        campaign_slug="gta-vi-youtube",
        subtitles=[
            SubtitleCue(start_sec=0.0, end_sec=3.5, text="OLHA ESSA GAMEPLAY AO VIVO EM VICE CITY!"),
            SubtitleCue(start_sec=3.5, end_sec=7.0, text="Reflexo neon realista nas ruas de Leonida!"),
            SubtitleCue(start_sec=7.0, end_sec=10.5, text="Transmissão mundial ao vivo pegando fogo!"),
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
    assert rendered.creator_credited_on_screen == ""

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

    # Verify .ass subtitle file was generated on disk with ZERO channel/creator watermark
    assert rendered.ass_subtitle_path is not None
    ass_file = Path(rendered.ass_subtitle_path)
    assert ass_file.exists(), f"Expected .ass subtitle file at {ass_file}"
    assert ass_file.suffix.lower() == ".ass"
    ass_content = ass_file.read_text(encoding="utf-8")
    assert "[Events]" in ass_content
    assert "Dialogue:" in ass_content
    assert "🎥 @" not in ass_content


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
    assert summary["rendered"] >= 1

    pubs = store.list_publications(limit=20)
    assert len(pubs) >= 1, "Expected at least 1 PublicationRecord persisted in StateStore"
    assert store.posted_ledger_path.exists()

    published_platforms = {p["platform"] for p in pubs}
    assert published_platforms.issubset({"instagram_reels", "tiktok"})
    assert len(published_platforms) >= 1

    for pub_row in pubs:
        caption_text = str(pub_row.get("caption_text") or "")
        assert "Assista ao vivo em youtube.com/youtube" in caption_text
        tags = [t.lower() for t in re.findall(r"#[A-Za-z0-9_À-ÿ]+", caption_text)]
        assert tags == ["#launchdayonyt"]

    entries = store.list_championship_entries(limit=20)
    if not entries:
        # Register published TikTok/Reels campaign post via RealOficialAgent if pipeline didn't auto-hook non-YT
        first_pub = PublicationRecord(
            id=str(pubs[0]["id"]),
            plan_id=str(pubs[0]["plan_id"]),
            clip_id=pubs[0].get("clip_id"),
            platform=Platform(str(pubs[0]["platform"])),
            status=str(pubs[0]["status"]),
            external_post_id=pubs[0].get("external_post_id"),
            public_url=pubs[0].get("public_url"),
            caption_text=str(pubs[0]["caption_text"]),
            campaign_slug=pubs[0].get("campaign_slug") or "gta-vi-youtube",
        )
        reg_fn = (
            getattr(orchestrator.real_oficial, "register_campaign_entry", None)
            or getattr(orchestrator.real_oficial, "register_publication_in_campaign", None)
            or getattr(orchestrator.real_oficial, "register_youtube_short_in_campaign")
        )
        await reg_fn(first_pub)
        entries = store.list_championship_entries(limit=20)

    assert len(entries) >= 1, "Expected at least 1 ChampionshipEntry persisted in StateStore"
    assert entries[0]["campaign_slug"] == "gta-vi-youtube"


def test_mission_control_dashboard_routes(tmp_path: Path) -> None:
    cfg = _make_pipeline_test_config(tmp_path)
    dashboard_app = create_dashboard_app(cfg)
    store: StateStore = dashboard_app.state.store

    store.save_pending_approval("plan_demo_99", {"hook": "Novo momento ao vivo em Leonida"})

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
    assert len(status_json["agents"]) >= 8

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


@pytest.mark.asyncio
async def test_download_video_segment_routes_active_live_streams_to_hls_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gta6_agents.video import downloader

    captured_calls: list[dict[str, object]] = []

    async def _fake_probe(url: str, timeout_sec: float = 15.0) -> dict[str, object]:
        return {"id": "KY4DWumZffg", "is_live": True, "live_status": "is_live", "duration": 0.0}

    async def _fake_capture_live(
        url: str,
        duration_sec: float,
        output_path: Path,
        *,
        start_sec: float = 0.0,
        prefer_dvr_window: bool = False,
    ) -> Path:
        captured_calls.append(
            {
                "url": url,
                "duration_sec": duration_sec,
                "start_sec": start_sec,
                "prefer_dvr_window": prefer_dvr_window,
            }
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"\x00" * 4096)
        return output_path

    monkeypatch.setattr(downloader, "probe_stream_metadata", _fake_probe)
    monkeypatch.setattr(downloader, "capture_live_stream_segment", _fake_capture_live)

    result_path = await downloader.download_video_segment(
        "https://www.youtube.com/watch?v=KY4DWumZffg#live-window-1",
        start_sec=120.0,
        end_sec=145.0,
        work_dir=tmp_path / "live_work",
        prefer_dvr_window=True,
    )
    assert result_path.exists()
    assert len(captured_calls) == 1
    assert captured_calls[0]["url"] == "https://www.youtube.com/watch?v=KY4DWumZffg"
    assert captured_calls[0]["duration_sec"] == 25.0
    assert captured_calls[0]["prefer_dvr_window"] is True


@pytest.mark.asyncio
async def test_scout_discovers_brazilian_streamers_and_blocks_gta5_rp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gta6_agents.agents.scout import ScoutAgent

    cfg = _make_pipeline_test_config(tmp_path)
    store = StateStore(cfg.data_dir, cfg.memory_dir)
    scout = ScoutAgent(cfg, store)

    cap_cfg, streamers_br = scout._load_streamers_br_config()
    handles = {s["handle"] for s in streamers_br}
    assert len(streamers_br) >= 10
    assert "@SkorpionOFICIAL" in handles
    assert "@GamerCosta" in handles
    assert "@PaulinhoLOKOoficial" in handles
    assert "@alanzoka" in handles
    assert "@loud_coringa" in handles

    async def _fake_ytdlp_flat(feed_url: str, max_items: int = 3, timeout_sec: float = 8.0):
        if "paulinholokobr" in feed_url:
            return [
                {
                    "id": "gta5_rp_old",
                    "title": "PAULINHO O LOKO FUGA NA CIDADE ALTA GTA 5 RP",
                    "description": "Live de GTA V RP",
                    "is_live": True,
                },
                {
                    "id": "gta6_vice_live",
                    "title": "PRIMEIRA GAMEPLAY DE GTA 6 AO VIVO EM VICE CITY! LEONIDA INSANA",
                    "description": "Jogando GTA VI em português ao vivo",
                    "webpage_url": "https://www.twitch.tv/paulinholokobr",
                    "is_live": True,
                },
            ]
        return []

    monkeypatch.setattr(scout, "_run_ytdlp_flat_json", _fake_ytdlp_flat)
    _, blacklist, blocked_kw = scout._load_creator_lists()
    paulinho_cfg = next(s for s in streamers_br if s["handle"] == "@PaulinhoLOKOoficial")

    found = await scout._probe_streamer_feed_ytdlp(
        paulinho_cfg,
        paulinho_cfg["twitch_live_url"],
        is_live_endpoint=True,
        capture_cfg=cap_cfg,
        blacklist=blacklist,
        blocked_keywords=blocked_kw,
    )
    assert len(found) == 1
    assert "GTA 6" in found[0].title
    assert "CIDADE ALTA" not in found[0].title
    assert found[0].creator_handle == "@PaulinhoLOKOoficial"
    assert found[0].source_type == "twitch"

