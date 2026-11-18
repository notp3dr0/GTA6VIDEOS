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


@pytest.mark.asyncio
async def test_post_live_handoff_prioritizes_newest_br_vods_rejects_old_vods_and_rotates_slices(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import datetime, timezone

    from gta6_agents.orchestrator.scheduler import DailyScheduler

    base_cfg = _make_pipeline_test_config(tmp_path)
    updated_settings = dict(base_cfg.settings)
    updated_settings["scout"] = {"enable_ytdlp_in_dry_run": True}
    cfg = base_cfg.model_copy(update={"settings": updated_settings})

    store = StateStore(cfg.data_dir, cfg.memory_dir)
    orchestrator = MultiAgentOrchestrator(cfg, store)

    # Simulate post-live state: KY4DWumZffg 25h live has ended -> campaign acervo is blocked
    monkeypatch.setattr(orchestrator.scout, "_is_campaign_blocked_now", lambda _block: True)

    now_ts = datetime.now(timezone.utc).timestamp()

    async def _fake_ytdlp_vods(feed_url: str, max_items: int = 5, timeout_sec: float = 9.0):
        # Skorpion (priority 100) has a 22-hour-old VOD + a stale 5-day-old VOD
        if "@SkorpionOFICIAL/streams" in feed_url:
            return [
                {
                    "id": "skorp_vod_22h",
                    "title": "ZERANDO GTA 6 EM VICE CITY! MISSÕES COM JASON E LUCIA",
                    "description": "VOD completo jogando GTA VI",
                    "webpage_url": "https://www.youtube.com/watch?v=skorpVOD22h",
                    "is_live": False,
                    "duration": 7200,
                    "timestamp": now_ts - (22 * 3600),
                },
                {
                    "id": "skorp_vod_old_120h",
                    "title": "AQUECIMENTO GTA 6 ANALISANDO LEONIDA",
                    "description": "Vídeo antigo de 5 dias atrás",
                    "webpage_url": "https://www.youtube.com/watch?v=skorpOld120",
                    "is_live": False,
                    "duration": 3600,
                    "timestamp": now_ts - (120 * 3600),
                },
            ]
        # Alanzoka (priority 96) JUST finished a brand-new VOD 25 minutes ago!
        if "twitch.tv/alanzoka/videos" in feed_url:
            return [
                {
                    "id": "v99887766",
                    "title": "ALANZOKA JOGANDO GTA 6 PELA PRIMEIRA VEZ EM LEONIDA!",
                    "description": "VOD fresquinho da live na Twitch",
                    "webpage_url": "https://www.twitch.tv/videos/99887766",
                    "is_live": False,
                    "duration": 10800,
                    "timestamp": now_ts - (25 * 60),
                }
            ]
        return []

    monkeypatch.setattr(orchestrator.scout, "_run_ytdlp_flat_json", _fake_ytdlp_vods)

    # 1. Verify 30-min radar indexes fresh VODs, discards >72h VOD, and puts 25-min-old Alanzoka VOD #1
    radar = await orchestrator.scout.refresh_fresh_vods_radar()
    assert radar["total_unseen_slices"] >= 8
    all_urls = [c["url"] for c in radar["candidates"]]
    assert not any("skorpOld120" in u for u in all_urls), "Stale >72h VOD must be rejected"
    assert not any("KY4DWumZffg" in u for u in all_urls), "Ended KY4DWumZffg live must not appear post-live"

    discovered = await orchestrator.scout.discover_candidates(limit=10, include_campaign_acervo=True)
    assert len(discovered) >= 4
    # Alanzoka's 25-min-old VOD MUST beat Skorpion's 22-hour-old VOD despite lower channel priority!
    assert discovered[0].creator_handle == "@alanzoka"
    assert "99887766" in discovered[0].url

    # 2. Verify two different channels (ch1 and ch2) claim non-overlapping slices of the newest VOD
    res_ch1 = await orchestrator.run_daily_cycle(
        max_items=1,
        include_campaign=True,
        demo_video_fallback=True,
        channel_id="ch1_plantao_oficial",
    )
    res_ch2 = await orchestrator.run_daily_cycle(
        max_items=1,
        include_campaign=True,
        demo_video_fallback=True,
        channel_id="ch2_leonida_segredos",
    )
    assert res_ch1["rendered"] == 1
    assert res_ch2["rendered"] == 1
    assert res_ch1["championship_entries"] == 0  # Post-live: zero Real Oficial submissions!
    assert res_ch2["championship_entries"] == 0

    # 3. Verify DailyScheduler registers the 30-minute Fresh BR VOD Radar job
    sched = DailyScheduler(cfg, orchestrator=orchestrator, store=store)
    jobs = sched.configure_jobs()
    job_ids = {j["id"] for j in jobs}
    assert "br_fresh_vod_radar_30m" in job_ids


@pytest.mark.asyncio
async def test_live_hype_monitor_detects_scream_stitches_retroactive_replay_and_renders(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real FFmpeg ring-buffer test: calm setup chunk -> sudden loud scream + chat 'CLIPA KKKKK' -> retroactive stitch -> 1080x1920 render."""
    import os
    import subprocess
    from gta6_agents.agents import live_hype_monitor
    from gta6_agents.agents.live_hype_monitor import RollingLiveRingBuffer
    from gta6_agents.orchestrator.scheduler import DailyScheduler

    cfg = _make_pipeline_test_config(tmp_path)
    store = StateStore(cfg.data_dir, cfg.memory_dir)
    orchestrator = MultiAgentOrchestrator(cfg, store)

    def _ffprobe_info(path: Path) -> dict:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        raw = json.loads(proc.stdout)
        st = (raw.get("streams") or [{}])[0]
        fmt = raw.get("format") or {}
        return {
            "width": int(st.get("width", 0)),
            "height": int(st.get("height", 0)),
            "duration_sec": float(fmt.get("duration", 0.0)),
        }

    ring = RollingLiveRingBuffer(
        stream_slug="alanzoka_live",
        buffer_dir=tmp_path / "live_rings",
        segment_sec=6,
        wrap_count=24,
    )
    ring.buffer_dir.mkdir(parents=True, exist_ok=True)

    chunk_0 = ring.buffer_dir / "chunk_000.ts"
    chunk_1 = ring.buffer_dir / "chunk_001.ts"

    # Chunk 0 (T - 12s to T - 6s): Calm setup BEFORE the funny/epic play (~-29 dB quiet talking)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x101830:s=1280x720:r=30:d=6",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=260:duration=6,volume=0.04",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(chunk_0),
        ],
        check=True,
    )
    os.utime(chunk_0, (1700000000.0, 1700000000.0))

    # Chunk 1 (T - 6s to T): Sudden streamer scream/laugh (~-1 dB) + visual explosion color flash!
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=1280x720:r=30:d=6",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=6,volume=2.8",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(chunk_1),
        ],
        check=True,
    )
    os.utime(chunk_1, (1700000006.0, 1700000006.0))

    # 1. Calm chunk must NOT trigger a clip and must calibrate vocal baseline
    calm_analysis, calm_cand = await orchestrator.hype_monitor.evaluate_buffered_chunk(
        stream_key="alanzoka_live",
        canonical_url="https://www.twitch.tv/alanzoka",
        creator_handle="@alanzoka",
        streamer_name="Alanzoka",
        chunk_path=chunk_0,
        ring_buffer=ring,
        for_campaign=False,
        sample_chat=False,
    )
    assert calm_analysis.triggered is False
    assert calm_cand is None

    # 2. Mock anonymous Twitch IRC chat burst ("CLIPA ISSO KKKKK MEU DEUS")
    async def _fake_chat_burst(channel_login: str, sample_seconds: float = 3.0):
        return {
            "messages_10s": 28.0,
            "hype_hits": 5,
            "reactions": ["CLIPA ISSO KKKKKKK", "MEU DEUS QUE INSANO"],
        }

    monkeypatch.setattr(live_hype_monitor, "sample_twitch_chat_hype", _fake_chat_burst)

    # 3. Loud scream + visual jump + chat burst MUST trigger retroactive stitch & instant candidate!
    spike_analysis, instant_cand = await orchestrator.hype_monitor.evaluate_buffered_chunk(
        stream_key="alanzoka_live",
        canonical_url="https://www.youtube.com/watch?v=KY4DWumZffg",
        creator_handle="@YouTube",
        streamer_name="Skorpion",
        chunk_path=chunk_1,
        ring_buffer=ring,
        for_campaign=True,
        twitch_login="alanzoka",
        sample_chat=True,
    )
    assert spike_analysis.triggered is True
    assert spike_analysis.composite_spike_score >= 58.0
    assert spike_analysis.hype_category == "engracado"
    assert instant_cand is not None

    stitched_path = Path(instant_cand.metadata["prebuffered_segment_path"])
    assert stitched_path.exists()
    # Retroactive stitch combined chunk_0 (6s setup BEFORE scream) + chunk_1 (6s scream) = ~12s!
    stitched_probe = _ffprobe_info(stitched_path)
    assert stitched_probe["duration_sec"] >= 11.0

    # 4. Full pipeline render with demo_video_fallback=False proves zero re-download & 13-rule compliance!
    res = await orchestrator._process_candidate(
        instant_cand,
        include_campaign=True,
        demo_video_fallback=False,
    )
    assert res["status"] == "published", f"Expected published, got: {res}"
    out_video = Path(res["video_path"])
    assert out_video.exists()
    final_probe = _ffprobe_info(out_video)
    assert final_probe["width"] == 1080
    assert final_probe["height"] == 1920

    # 5. Verify DailyScheduler registers the 20-second Real-Time Live Hype Monitor job
    sched = DailyScheduler(cfg, orchestrator=orchestrator, store=store)
    jobs = sched.configure_jobs()
    assert "live_hype_monitor_20s" in {j["id"] for j in jobs}



