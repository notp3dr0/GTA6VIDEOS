"""End-to-end tests for gtavi-claudecode viral content upgrades & Hydrogen Multi-Agent Swarm."""

from __future__ import annotations

import csv
import json
import subprocess
from pathlib import Path

import httpx
import pytest

from gta6_agents.agents.analytics import AnalyticsLearningAgent
from gta6_agents.agents.clipper import ClipperEditorAgent
from gta6_agents.agents.hydrogen_swarm import (
    HydrogenContentSwarm,
    cluster_questions,
    detect_english_text,
    is_player_question,
    scan_transcript_for_secrets,
    translate_cues_to_ptbr,
    validate_and_polish_hook,
)
from gta6_agents.agents.moment_finder import (
    compute_audio_rms_db_series,
    extract_heatmap_moments,
    score_multimodal_moments,
)
from gta6_agents.agents.trend_radar import discover_twitch_live_gql
from gta6_agents.config import AppConfig, get_project_root, load_config
from gta6_agents.models import (
    ChampionshipEntry,
    ClipPlan,
    ContentPillar,
    Platform,
    PublicationRecord,
    SourceCandidate,
    SubtitleCue,
)
from gta6_agents.orchestrator.scheduler import DailyScheduler
from gta6_agents.storage import StateStore
from gta6_agents.video.facecam import detect_facecam_and_game_offset, parse_cam_box
from gta6_agents.video.quality_gate import verify_rendered_clip
from gta6_agents.video.subtitles import build_ass_subtitles, mask_profanity


def _make_cfg(tmp_path: Path) -> AppConfig:
    import shutil

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
        }
    )


def test_kinetic_word_subtitles_and_ptbr_en_profanity_masking(tmp_path: Path) -> None:
    """Verify word-by-word kinetic cyan/accent highlight, pop animation, and EN+PT-BR slur/swear auto-masking."""
    assert mask_profanity("Que porra é essa, caralho! What the fuck!") == "Que p**** é essa, c******! What the f***!"
    # Slurs remain masked even if allow_profanity=True
    assert "*" in mask_profanity("nigger fuck", allow_profanity=True).split()[0]
    assert mask_profanity("fuck", allow_profanity=True) == "fuck"

    ass_out = tmp_path / "kinetic.ass"
    cues = [
        SubtitleCue(start_sec=0.0, end_sec=3.0, text="Olha essa fuga insana caralho"),
        SubtitleCue(start_sec=3.0, end_sec=6.0, text="Policia cercou Vice City"),
    ]
    build_ass_subtitles(
        cues=cues,
        hook_text="FUGA INSANA EM LEONIDA",
        credit_text="",
        output_ass_path=ass_out,
        hook_duration_sec=2.0,
        total_duration_sec=6.0,
        accent_ass_bgr="&H00FF00FF",
        style_mode="kinetic_word",
        layout_mode="react",
        reaction_title="Alanzoka SURTOU com essa perseguição!",
    )
    txt = ass_out.read_text(encoding="utf-8")
    assert r"{\c&H00FF00FF&}" in txt
    assert r"\fscx112\fscy112" in txt
    assert r"\fad(0,180)" in txt
    assert "TitleBox" in txt
    assert "CARALHO" not in txt
    assert "C******" in txt


@pytest.mark.asyncio
async def test_facecam_split_screen_react_render_and_quality_gate_sidecar(tmp_path: Path) -> None:
    """Create a real 1280x720 video with a bright top-right webcam box over moving gameplay -> auto-detect -> render 1080x1920 react split-screen -> verify QualityGate + sidecar JSON."""
    cfg = _make_cfg(tmp_path)
    store = StateStore(cfg.data_dir, cfg.memory_dir)
    clipper = ClipperEditorAgent(cfg, store)

    # Synthesize a real 1280x720 6s video with moving testsrc2 gameplay + static high-contrast top-right webcam box!
    src_mp4 = tmp_path / "streamer_with_cam.mp4"
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
            "sine=frequency=440:duration=6",
            "-vf",
            "drawbox=x=896:y=28:w=358:h=245:color=white@1.0:t=fill,"
            "drawbox=x=906:y=38:w=338:h=225:color=0x223344@1.0:t=fill",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(src_mp4),
        ],
        check=True,
    )

    assert parse_cam_box("0.70:0.04:0.28:0.34") == (0.70, 0.04, 0.28, 0.34)
    assert parse_cam_box("bad_spec") is None

    det = detect_facecam_and_game_offset(src_mp4, start_sec=0.0, end_sec=6.0)
    assert det.mode == "cam", f"Expected auto-detected top-right cam box, got: {det}"
    assert det.cam_side == "right"
    assert det.has_gameplay is True

    source = SourceCandidate(
        title="Alanzoka reagindo à fuga em Vice City",
        url=str(src_mp4),
        creator_handle="@alanzoka",
        source_type="twitch",
        metadata={
            "prebuffered_segment_path": str(src_mp4),
            "subtitle_accent_ass_bgr": "&H00FFFF00",
            "hydrogen_enrichment": {
                "hook": {
                    "archetype": "creator_spotlight",
                    "hook_on_screen": "Alanzoka não tankou essa fuga",
                    "reaction_title": "Alanzoka PERDEU A LINHA na perseguição!",
                }
            },
        },
    )
    plan = ClipPlan(
        source=source,
        pillar=ContentPillar.GAMEPLAY_CLIPS,
        language="pt-BR",
        hook_on_screen="Alanzoka não tankou essa fuga",
        start_sec=0.0,
        end_sec=6.0,
        target_platforms=[Platform.TIKTOK],
        for_real_oficial_campaign=False,
        subtitles=[
            SubtitleCue(start_sec=0.0, end_sec=3.0, text="Olha essa fisica absurda em Leonida"),
            SubtitleCue(start_sec=3.0, end_sec=6.0, text="Capotou o carro no meio da praia"),
        ],
    )

    rendered = await clipper.render_clip_plan(plan, use_demo_source_if_needed=False)
    out_mp4 = Path(rendered.video_path)
    assert out_mp4.exists()

    sidecar = out_mp4.with_suffix(".json")
    assert sidecar.exists(), "QualityGate must emit a .json sidecar alongside every rendered MP4"
    sidecar_data = json.loads(sidecar.read_text(encoding="utf-8"))
    assert sidecar_data["layout"] == "react"
    assert sidecar_data["width"] == 1080
    assert sidecar_data["height"] == 1920
    assert sidecar_data["passed"] is True

    # Verify QualityGate rejects leaked/banned terms in caption
    bad_report = verify_rendered_clip(
        out_mp4,
        plan,
        caption_text="Download cracked play early leaked build money glitch!",
        min_duration_sec=1.0,
        write_sidecar=False,
    )
    assert bad_report.passed is False
    assert "no banned terms" in bad_report.failures


def test_multimodal_moment_finder_youtube_heatmap_and_audio_rms(tmp_path: Path) -> None:
    """Verify YouTube Most Replayed heatmap peak extraction + pure-Python FFmpeg RMS audio spike scoring."""
    heatmap = [
        {"start_time": 0.0, "end_time": 30.0, "value": 0.95},  # Intro -> skipped
        {"start_time": 120.0, "end_time": 150.0, "value": 0.20},
        {"start_time": 420.0, "end_time": 450.0, "value": 0.98},  # Peak #1!
        {"start_time": 900.0, "end_time": 930.0, "value": 0.86},  # Peak #2!
    ]
    hm_moments = extract_heatmap_moments(heatmap, duration_sec=1800.0, top_k=2, min_sec=15.0, max_sec=26.0)
    assert len(hm_moments) == 2
    assert 400.0 <= hm_moments[0].start_sec <= 440.0
    assert 880.0 <= hm_moments[1].start_sec <= 920.0
    assert "most replayed" in hm_moments[0].reasons[0]

    # Multimodal scoring combining audio spike + PT-BR hype words + chat burst + heatmap
    audio_db = [-28.0] * 120
    for sec in range(62, 68):
        audio_db[sec] = -3.0  # Huge scream spike at 62–68s!
    words = [
        {"start": 60.0, "end": 61.5, "text": "Calma"},
        {"start": 63.0, "end": 64.5, "text": "MEU DEUS QUE INSANO"},
        {"start": 74.0, "end": 75.5, "text": "Clipe absurdo"},
    ]
    chat_counts = [1.0] * 120
    chat_counts[64] = 45.0
    chat_counts[65] = 60.0

    mm = score_multimodal_moments(
        duration_sec=120.0,
        audio_db=audio_db,
        words=words,
        chat_counts=chat_counts,
        top_k=1,
        min_sec=15.0,
        max_sec=28.0,
    )
    assert len(mm) == 1
    assert 50.0 <= mm[0].peak_sec <= 72.0
    assert any(r in ("loudness spike", "chat spike", "hype words") for r in mm[0].reasons)

    # Pure-Python FFmpeg RMS reader smoke test
    beep_mp4 = tmp_path / "beep.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-c:a", "aac", str(beep_mp4),
        ],
        check=True,
    )
    series = compute_audio_rms_db_series(beep_mp4)
    assert len(series) >= 2
    assert all(db > -30.0 for db in series)


@pytest.mark.asyncio
async def test_keyless_twitch_gql_discovery_and_hydrogen_parallel_swarm(tmp_path: Path) -> None:
    """Verify keyless Twitch GQL discovery (PT-BR GTA VI only), Hydrogen parallel swarm, Questions clustering, Secrets CSV, and Analytics scoreboard/payouts CSV."""
    # 1. Keyless Twitch GQL mock
    def _mock_gql(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Client-Id") == "kimne78kx3ncx6brgo4mv6wki5h1ko"
        return httpx.Response(
            200,
            json={
                "data": {
                    "game": {
                        "streams": {
                            "edges": [
                                {
                                    "node": {
                                        "id": "1",
                                        "title": "ALANZOKA JOGANDO GTA 6 AO VIVO!",
                                        "viewersCount": 95000,
                                        "createdAt": "2026-11-20T02:10:00Z",
                                        "broadcasterLanguage": "pt",
                                        "broadcaster": {"login": "alanzoka", "displayName": "alanzoka"},
                                    }
                                },
                                {
                                    "node": {
                                        "id": "2",
                                        "title": "English stream should be filtered out",
                                        "viewersCount": 120000,
                                        "createdAt": "2026-11-20T02:10:00Z",
                                        "broadcasterLanguage": "en",
                                        "broadcaster": {"login": "english_streamer", "displayName": "EnglishStreamer"},
                                    }
                                },
                            ]
                        }
                    }
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(_mock_gql)) as client:
        hits = await discover_twitch_live_gql(language_filter="pt", limit=10, http_client=client)
    assert len(hits) == 1
    assert hits[0].channel_login == "alanzoka"
    assert hits[0].viewers == 95000

    # 2. Hydrogen Hook anti-clickbait + EN->PT-BR detector/translator + Questions clustering + Secrets CSV
    clean_hook, warns = validate_and_polish_hook("Espere até o final: 3 segredos no GTA 5", for_campaign=True)
    assert "espere até o final" not in clean_hook.lower()
    assert "gta 5" not in clean_hook.lower()
    assert len(warns) >= 2

    assert detect_english_text("Look at this crazy police chase in the city and what they have done") is True
    assert detect_english_text("Olha essa fuga absurda em Vice City com o Skorpion") is False

    tr_cues, _ = await translate_cues_to_ptbr(
        [SubtitleCue(start_sec=0.0, end_sec=2.0, text="Look at this crazy police car")],
        hook_text="Look at this crazy police chase",
        dry_run=True,
    )
    assert "polícia" in tr_cues[0].text.lower()

    assert is_player_question("Como desbloquear o carro secreto em Leonida?") is True
    clustered = cluster_questions(
        [
            {"text": "Where is the hidden sports car in Leonida?", "score": 50, "link": "https://reddit.com/1"},
            {"text": "Where is the hidden sports car in Leonida", "score": 30, "link": "https://reddit.com/2"},
            {"text": "Como ganhar dinheiro rápido sem glitch em Vice City?", "score": 20, "link": "https://reddit.com/3"},
        ]
    )
    assert len(clustered) == 2
    assert clustered[0]["count"] == 2

    # Secrets scanner blocks money glitches/exploits and logs clean Leonida secrets to CSV!
    blocked = scan_transcript_for_secrets(
        "Found an infinite money glitch secret spot in Vice City!",
        source_channel="@badchannel",
        source_url="https://youtube.com/watch?v=xxx",
        memory_dir=tmp_path / "memory",
    )
    assert blocked == []

    # 3. Hydrogen Parallel Content Swarm across 4 candidates concurrently
    swarm = HydrogenContentSwarm(max_concurrency=6, dry_run=True, memory_dir=tmp_path / "memory")
    cands = [
        SourceCandidate(
            title="Carro secreto descoberto em Vice Beach",
            url="https://www.youtube.com/watch?v=vod1",
            creator_handle="@SkorpionOFICIAL",
            summary="Achamos um carro raro escondido atrás do pier de Vice Beach!",
        ),
        SourceCandidate(
            title="Fuga policial insana em Leonida",
            url="https://www.twitch.tv/alanzoka",
            creator_handle="@alanzoka",
            summary="Look at this crazy police chase and what they have in Vice City!",
        ),
    ]
    swarm_res = await swarm.enrich_candidates_parallel(cands, channel_angle="Segredos", for_campaign=False)
    assert swarm_res["enriched_count"] == 2
    assert swarm_res["english_detected"] == 1
    assert swarm_res["secrets_logged"] >= 1
    assert (tmp_path / "memory" / "secrets.csv").exists()
    assert "hydrogen_enrichment" in cands[0].metadata

    # 4. Analytics Learning Loop writes scoreboard.csv & payouts.csv + DailyScheduler registers 6h Hydrogen job
    cfg = _make_cfg(tmp_path)
    store = StateStore(cfg.data_dir, cfg.memory_dir)
    store.record_publication(
        PublicationRecord(
            plan_id="plan_1",
            platform=Platform.TIKTOK,
            status="published",
            public_url="https://www.tiktok.com/@cortesliveleonida/video/123",
            caption_text="Corte ao vivo! Assista ao vivo em youtube.com/youtube #launchdayonYT",
            campaign_slug="gta-vi-youtube",
        )
    )
    store.record_championship_entry(
        ChampionshipEntry(
            publication_id="pub_1",
            campaign_slug="gta-vi-youtube",
            youtube_url="https://www.tiktok.com/@cortesliveleonida/video/123",
            status="submitted",
            validated_views=25000,
            estimated_cpm_brl=4.0,
        )
    )
    analytics = AnalyticsLearningAgent(cfg, store)
    summary = await analytics.run_learning_loop()
    assert Path(summary["scoreboard_csv_path"]).exists()
    payouts_csv = Path(summary["payouts_csv_path"])
    assert payouts_csv.exists()
    with payouts_csv.open("r", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    assert rows[0]["est_payout_brl"] == "100.00"  # 25,000 views * R$ 4.00 / 1k = R$ 100.00

    sched = DailyScheduler(cfg, store=store)
    job_ids = {j["id"] for j in sched.configure_jobs()}
    assert "hydrogen_questions_trends_6h" in job_ids


@pytest.mark.asyncio
async def test_kick_live_discovery_and_m3u8_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify Kick public API v2 live discovery in ScoutAgent and direct .m3u8 resolution in downloader."""
    import asyncio as _asyncio

    from gta6_agents.agents.scout import ScoutAgent
    from gta6_agents.video import downloader as dl_mod

    kick_payload = json.dumps(
        {
            "slug": "coringa",
            "playback_url": "https://fa723fc1b171.us-west-2.playback.live-video.net/api/video/v1/coringa.m3u8",
            "livestream": {
                "is_live": True,
                "session_title": "GTA VI | DIA 2",
                "viewer_count": 47374,
                "categories": [{"name": "Grand Theft Auto VI"}],
            },
        }
    ).encode("utf-8")

    class _FakeProc:
        returncode = 0

        async def communicate(self):
            return (kick_payload, b"")

    async def _fake_exec(*_args, **_kwargs):
        return _FakeProc()

    monkeypatch.setattr(_asyncio, "create_subprocess_exec", _fake_exec)

    base_cfg = _make_cfg(tmp_path)
    cfg = base_cfg.model_copy(
        update={"settings": {**base_cfg.settings, "scout": {"enable_kick_in_dry_run": True}}}
    )
    store = StateStore(cfg.data_dir, cfg.memory_dir)
    scout = ScoutAgent(cfg, store)

    cands = await scout._probe_kick_live(
        {"handle": "@loud_coringa", "name": "LOUD Coringa", "priority": 95},
        "https://kick.com/coringa",
        capture_cfg={"clip_duration_sec": 25.0, "strict_gta6_only_filter": True},
        blacklist=set(),
        blocked_keywords=[],
    )
    assert len(cands) == 1
    assert cands[0].source_type == "kick"
    assert cands[0].creator_handle == "@loud_coringa"
    assert cands[0].metadata["is_live"] is True
    assert cands[0].metadata["canonical_url"] == "https://kick.com/coringa"
    assert cands[0].metadata["viewer_count"] == 47374

    # Also test downloader._resolve_direct_stream_urls for kick.com URLs
    async def _fake_run_subproc(*_args, **_kwargs):
        return (0, kick_payload.decode("utf-8"), "")

    monkeypatch.setattr(dl_mod, "_run_subprocess", _fake_run_subproc)
    urls = await dl_mod._resolve_direct_stream_urls("https://kick.com/coringa#live-0", is_live=True)
    assert len(urls) == 1
    assert urls[0].endswith("coringa.m3u8")

