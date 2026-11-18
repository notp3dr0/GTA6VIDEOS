"""Tests for MultiChannelCoordinator (5 PT-BR Channels x 12/day), TriEnginePool (5h Cooldown + Checkpoints), and LiveKY4CampaignEngine."""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Tuple

import pytest

from gta6_agents.agents.live_campaign_ky4 import (
    BRT_TZ,
    PHASE_LIVE_ON_AIR,
    PHASE_PRE_LIVE,
    PHASE_VIEWS_COUNTING_48H,
    LiveKY4CampaignEngine,
)
from gta6_agents.config import AppConfig, get_project_root, load_config
from gta6_agents.models import ContentPillar
from gta6_agents.orchestrator.multi_channel import MultiChannelCoordinator
from gta6_agents.orchestrator.tri_engine_pool import (
    AIEngine,
    CheckpointStage,
    EffortRole,
    RateLimitDetector,
    TriEnginePool,
)
from gta6_agents.storage import StateStore


@pytest.fixture
def test_env(tmp_path: Path) -> Tuple[AppConfig, StateStore]:
    """Build an isolated AppConfig + StateStore backed by the real YAML configs in tmp_path."""
    repo_root = get_project_root()
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    for fname in ("settings.yaml", "campaign_realoficial.yaml", "channels_matrix.yaml"):
        src = repo_root / "config" / fname
        if src.exists():
            shutil.copy2(src, cfg_dir / fname)

    base_cfg = load_config(tmp_path)
    cfg = base_cfg.model_copy(
        update={
            "root_dir": tmp_path,
            "dry_run": True,
            "llm_provider": "template_fallback",
            "gemini_api_key": "",
            "openai_api_key": "",
            "groq_api_key": "",
        }
    )
    store = StateStore(tmp_path / "data", tmp_path / "memory")
    return cfg, store


def test_5_ptbr_channels_loaded_without_gta5_nostalgia_and_60_daily_slots(
    test_env: Tuple[AppConfig, StateStore],
) -> None:
    cfg, store = test_env
    coordinator = MultiChannelCoordinator(cfg, store)

    channels = coordinator.load_channels()
    assert len(channels) == 5

    expected = [
        ("ch1_plantao_oficial", "Cortes Live Leonida", "@cortesliveleonida", 0.0),
        ("ch2_leonida_segredos", "Radar Streamers Vice", "@radarstreamersvice", 0.4),
        ("ch3_gta6_dicas_pro", "Plays & Clutches GTA 6", "@playsgta6live", 0.8),
        ("ch4_vice_motores", "Fugas & Motores Live", "@fugasmotoreslive", 1.2),
        ("ch5_vice_stories", "Cortes Resenha Vice", "@cortesresenhavice", 1.6),
    ]
    for profile, (exp_id, exp_name, exp_handle, exp_offset) in zip(channels, expected):
        assert profile.channel_id == exp_id
        assert profile.name == exp_name
        assert profile.handle == exp_handle
        assert profile.language == "pt-BR"
        assert profile.micro_trim_offset_sec == pytest.approx(exp_offset)
        assert ContentPillar.GTA5_NOSTALGIA not in profile.pillars

    schedule = coordinator.build_daily_60_schedule(date_str="2026-11-19")
    assert len(schedule) == 60

    per_channel_counts: dict[str, int] = {}
    for idx, slot in enumerate(schedule):
        per_channel_counts[slot["channel_id"]] = per_channel_counts.get(slot["channel_id"], 0) + 1
        assert slot["pillar"] != ContentPillar.GTA5_NOSTALGIA.value
        if idx > 0:
            assert slot["scheduled_at_brt"] >= schedule[idx - 1]["scheduled_at_brt"]

    assert per_channel_counts == {ch.channel_id: 12 for ch in channels}


def test_claim_unique_segment_cross_channel_lock_and_micro_trim_offset(
    test_env: Tuple[AppConfig, StateStore],
) -> None:
    cfg, store = test_env
    coordinator = MultiChannelCoordinator(cfg, store)
    date_str = "2026-11-19"

    # Channel 1 (idx=0 -> +0.0s micro-trim) claims [10.0s, 34.0s] on KY4DWumZffg
    ok_ch1, start_ch1, end_ch1 = coordinator.claim_unique_segment(
        channel_id="ch1_plantao_oficial",
        source_url="https://www.youtube.com/watch?v=KY4DWumZffg",
        start_sec=10.0,
        end_sec=34.0,
        date_str=date_str,
    )
    assert ok_ch1 is True
    assert (start_ch1, end_ch1) == pytest.approx((10.0, 34.0))

    # Channel 2 (idx=1 -> +0.4s micro-trim) tries overlapping [20.0s, 44.0s] via short URL youtu.be -> REJECTED
    ok_ch2_overlap, start_ch2_ov, end_ch2_ov = coordinator.claim_unique_segment(
        channel_id="ch2_leonida_segredos",
        source_url="https://youtu.be/KY4DWumZffg",
        start_sec=20.0,
        end_sec=44.0,
        date_str=date_str,
    )
    assert ok_ch2_overlap is False
    assert (start_ch2_ov, end_ch2_ov) == pytest.approx((20.4, 44.4))

    # Channel 3 (idx=2 -> +0.8s micro-trim) claims non-overlapping [50.0s, 74.0s] -> ALLOWED with +0.8s offset
    ok_ch3, start_ch3, end_ch3 = coordinator.claim_unique_segment(
        channel_id="ch3_gta6_dicas_pro",
        source_url="https://www.youtube.com/watch?v=KY4DWumZffg",
        start_sec=50.0,
        end_sec=74.0,
        date_str=date_str,
    )
    assert ok_ch3 is True
    assert (start_ch3, end_ch3) == pytest.approx((50.8, 74.8))

    fp1 = coordinator.get_render_fingerprint(
        "ch1_plantao_oficial",
        "https://www.youtube.com/watch?v=KY4DWumZffg",
        10.0,
        34.0,
        date_str,
    )
    fp3 = coordinator.get_render_fingerprint(
        "ch3_gta6_dicas_pro",
        "https://www.youtube.com/watch?v=KY4DWumZffg",
        50.0,
        74.0,
        date_str,
    )
    assert fp1["fingerprint_hash"] != fp3["fingerprint_hash"]


def test_select_youtube_oauth_slot_quota_rotation(
    test_env: Tuple[AppConfig, StateStore],
) -> None:
    cfg, store = test_env
    coordinator = MultiChannelCoordinator(cfg, store)
    date_str = "2026-11-21"
    channel_id = "ch1_plantao_oficial"

    # Uploads 1..6 -> project_a (6 * 1,600 = 9,600 / 10,000 units)
    slots_1_to_6 = [
        coordinator.select_youtube_oauth_slot(channel_id, date_str, consume=True)
        for _ in range(6)
    ]
    assert all(s["slot"] == "project_a" for s in slots_1_to_6)
    assert slots_1_to_6[-1]["slot_uploads_used"] == 6
    assert slots_1_to_6[-1]["units_used"] == 9600

    # Uploads 7..12 -> automatically rotates to project_b
    slots_7_to_12 = [
        coordinator.select_youtube_oauth_slot(channel_id, date_str, consume=True)
        for _ in range(6)
    ]
    assert all(s["slot"] == "project_b" for s in slots_7_to_12)
    assert slots_7_to_12[-1]["slot_uploads_used"] == 6
    assert slots_7_to_12[-1]["channel_daily_uploads_used"] == 12

    # Upload 13 -> falls back seamlessly to real_oficial_launcher
    slot_13 = coordinator.select_youtube_oauth_slot(channel_id, date_str, consume=True)
    assert slot_13["slot"] == "real_oficial_launcher"
    assert slot_13["mode"] == "real_oficial_launcher"
    assert slot_13["allowed"] is True
    assert slot_13["channel_daily_uploads_used"] == 13


def test_rate_limit_detector_parses_5h_limits() -> None:
    limited_claude, sec_claude = RateLimitDetector.detect_5h_limit(
        "You've hit your 5-hour usage limit. Resets in 2h 30m",
        exit_code=1,
    )
    assert limited_claude is True
    assert sec_claude == 9000

    limited_codex, sec_codex = RateLimitDetector.detect_5h_limit(
        "Error: 5 hours quota exceeded for model sol-6.2, try again in 45m",
        exit_code=1,
    )
    assert limited_codex is True
    assert sec_codex == 2700

    limited_429, sec_429 = RateLimitDetector.detect_5h_limit("", exit_code=429)
    assert limited_429 is True
    assert sec_429 == 18000

    ok_clean, sec_clean = RateLimitDetector.detect_5h_limit(
        '{"hook_on_screen": "SKORPION AO VIVO EM VICE CITY"}',
        exit_code=0,
    )
    assert ok_clean is False
    assert sec_clean == 0


def test_tri_engine_pool_cooldown_failover_and_expiration_restore(
    test_env: Tuple[AppConfig, StateStore],
) -> None:
    cfg, store = test_env
    pool = TriEnginePool(cfg, store)
    t0 = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone.utc)

    assert pool.pick_available_engine(preferred=AIEngine.CLAUDE_PRO, now=t0) == AIEngine.CLAUDE_PRO

    # 1. Claude Pro hits 5h limit -> fails over to Codex Plus
    pool.mark_engine_5h_cooldown(
        AIEngine.CLAUDE_PRO,
        cooldown_seconds=18000,
        reason="Claude 5-hour usage limit",
        now=t0,
    )
    t_plus_15m = t0 + timedelta(minutes=15)
    assert pool.pick_available_engine(preferred=AIEngine.CLAUDE_PRO, now=t_plus_15m) == AIEngine.CODEX_PLUS

    # 2. Codex Plus hits 5h limit -> fails over to Antigravity Ultra
    pool.mark_engine_5h_cooldown(
        AIEngine.CODEX_PLUS,
        cooldown_seconds=18000,
        reason="Codex Plus 5-hour usage limit",
        now=t_plus_15m,
    )
    assert (
        pool.pick_available_engine(preferred=AIEngine.CLAUDE_PRO, now=t_plus_15m)
        == AIEngine.ANTIGRAVITY_ULTRA
    )

    # 3. Antigravity Ultra also hits limit -> safe deterministic TEMPLATE_FALLBACK
    pool.mark_engine_5h_cooldown(
        AIEngine.ANTIGRAVITY_ULTRA,
        cooldown_seconds=18000,
        reason="Quota 429",
        now=t_plus_15m,
    )
    assert (
        pool.pick_available_engine(preferred=AIEngine.CLAUDE_PRO, now=t_plus_15m)
        == AIEngine.TEMPLATE_FALLBACK
    )

    # 4. After 5h window expires -> clear_expired_cooldowns restores primary engines
    t_after_5h = t0 + timedelta(hours=5, minutes=20)
    restored = pool.clear_expired_cooldowns(now=t_after_5h)
    assert set(restored) == {
        AIEngine.CLAUDE_PRO,
        AIEngine.CODEX_PLUS,
        AIEngine.ANTIGRAVITY_ULTRA,
    }
    assert pool.pick_available_engine(preferred=AIEngine.CLAUDE_PRO, now=t_after_5h) == AIEngine.CLAUDE_PRO


@pytest.mark.asyncio
async def test_run_checkpointed_task_mid_task_5h_failover_and_incomplete_checkpoint_resume(
    test_env: Tuple[AppConfig, StateStore],
) -> None:
    cfg, store = test_env
    pool = TriEnginePool(cfg, store)

    # Part A: Mid-task 5h rate-limit failover during EXECUTE_LOW without re-running THINK_XHIGH
    call_log: list[tuple[AIEngine, EffortRole]] = []

    async def mid_task_runner(engine: AIEngine, role: EffortRole, prompt: str):
        call_log.append((engine, role))
        if engine == AIEngine.CLAUDE_PRO and role == EffortRole.THINK_XHIGH:
            return '{"hook": "SKORPION FUGA INSANA EM VICE CITY", "angle": "gameplay_live"}'
        if engine == AIEngine.CLAUDE_PRO and role == EffortRole.EXECUTE_LOW:
            return ("You've hit your 5-hour usage limit. Resets in 4h 0m", 1)
        if engine == AIEngine.CODEX_PLUS and role == EffortRole.EXECUTE_LOW:
            assert "SKORPION FUGA INSANA EM VICE CITY" in prompt
            return '{"caption": "Fuga ao vivo! Assista ao vivo em youtube.com/youtube #launchdayonYT"}'
        raise AssertionError(f"Unexpected call: {(engine, role)}")

    result = await pool.run_checkpointed_task(
        task_id="ky4_ch3_2230",
        channel_id="ch3_gta6_dicas_pro",
        slot_time="22:30",
        think_prompt="Planeje gancho viral da gameplay do Skorpion às 22:30 BRT",
        execute_prompt_fn=lambda think_out: f"Formate legenda da campanha com base em: {think_out}",
        preferred_engine=AIEngine.CLAUDE_PRO,
        runner_override=mid_task_runner,
    )

    assert result["stage"] == CheckpointStage.COMPLETED.value
    assert result["think_engine"] == AIEngine.CLAUDE_PRO.value
    assert result["execute_engine"] == AIEngine.CODEX_PLUS.value
    # THINK_XHIGH ran exactly ONCE on Claude Pro and was NOT re-run after EXECUTE_LOW failed over!
    assert [r for _, r in call_log].count(EffortRole.THINK_XHIGH) == 1
    assert call_log == [
        (AIEngine.CLAUDE_PRO, EffortRole.THINK_XHIGH),
        (AIEngine.CLAUDE_PRO, EffortRole.EXECUTE_LOW),
        (AIEngine.CODEX_PLUS, EffortRole.EXECUTE_LOW),
    ]
    assert len(result["failovers"]) == 1
    assert result["failovers"][0]["engine"] == AIEngine.CLAUDE_PRO.value

    # Part B: load_incomplete_checkpoints() + crash-recovery resume from READY_TO_EXECUTE
    pool.save_checkpoint(
        task_id="ky4_ch2_2245",
        channel_id="ch2_leonida_segredos",
        slot_time="22:45",
        stage=CheckpointStage.READY_TO_EXECUTE,
        payload={
            "think_output": '{"hook": "EASTER EGG AO VIVO EM LEONIDA"}',
            "think_engine": AIEngine.CODEX_PLUS.value,
        },
        engine=AIEngine.CODEX_PLUS,
    )
    pool.save_checkpoint(
        task_id="ky4_ch1_1930",
        channel_id="ch1_plantao_oficial",
        slot_time="19:30",
        stage=CheckpointStage.PENDING_THINK,
        payload={},
        engine=AIEngine.CLAUDE_PRO,
    )

    incomplete = pool.load_incomplete_checkpoints()
    assert [item["task_id"] for item in incomplete] == ["ky4_ch1_1930", "ky4_ch2_2245"]

    resume_calls: list[tuple[AIEngine, EffortRole]] = []

    async def resume_runner(engine: AIEngine, role: EffortRole, prompt: str):
        resume_calls.append((engine, role))
        return '{"caption": "Easter egg ao vivo! Assista ao vivo em youtube.com/youtube #launchdayonYT"}'

    resumed_result = await pool.run_checkpointed_task(
        task_id="ky4_ch2_2245",
        channel_id="ch2_leonida_segredos",
        slot_time="22:45",
        think_prompt="Prompt que NÃO deve ser re-executado",
        execute_prompt_fn=lambda think_out: f"Execute a partir do checkpoint: {think_out}",
        preferred_engine=AIEngine.CODEX_PLUS,
        runner_override=resume_runner,
    )

    assert resumed_result["resumed"] is True
    assert resumed_result["skipped_think"] is True
    assert resumed_result["stage"] == CheckpointStage.COMPLETED.value
    assert resume_calls == [(AIEngine.CODEX_PLUS, EffortRole.EXECUTE_LOW)]

    remaining_incomplete = pool.load_incomplete_checkpoints()
    assert [item["task_id"] for item in remaining_incomplete] == ["ky4_ch1_1930"]


def test_live_ky4_campaign_engine_phases_and_window_safety_shields(
    test_env: Tuple[AppConfig, StateStore],
) -> None:
    cfg, store = test_env
    ky4 = LiveKY4CampaignEngine(cfg, store)

    # 1. Campaign lifecycle phases (BRT / America/Sao_Paulo)
    dt_pre_live = datetime(2026, 11, 19, 0, 30, 0, tzinfo=BRT_TZ)
    dt_during_live = datetime(2026, 11, 19, 22, 40, 0, tzinfo=BRT_TZ)
    dt_post_live = datetime(2026, 11, 20, 3, 0, 0, tzinfo=BRT_TZ)

    assert ky4.get_campaign_phase(dt_pre_live) == PHASE_PRE_LIVE
    assert ky4.is_campaign_posting_allowed(dt_pre_live) is False
    assert ky4.should_switch_to_post_live_streamers(dt_pre_live) is False

    assert ky4.get_campaign_phase(dt_during_live) == PHASE_LIVE_ON_AIR
    assert ky4.is_campaign_posting_allowed(dt_during_live) is True
    assert ky4.should_switch_to_post_live_streamers(dt_during_live) is False

    assert ky4.get_campaign_phase(dt_post_live) == PHASE_VIEWS_COUNTING_48H
    assert ky4.is_campaign_posting_allowed(dt_post_live) is False
    assert ky4.should_switch_to_post_live_streamers(dt_post_live) is True

    # 2. Segment window safety shields:
    # - 20:15 BRT -> HARD REJECT (The Burnt Peanut 20:03-20:30 / 20:00-20:34 safety window)
    dt_burnt_peanut = datetime(2026, 11, 19, 20, 15, 0, tzinfo=BRT_TZ)
    allowed_bp, reason_bp, hold_bp = ky4.check_segment_window_safety(
        dt_burnt_peanut,
        transcript_or_title="Live YouTube x GTA VI às 20:15",
    )
    assert allowed_bp is False
    assert hold_bp is False
    assert "burnt peanut" in reason_bp.lower()

    # - 07:30 BRT -> FLAGGED danger window (Samira Close hosts Passaporte pra Vice City 07:04-08:10)
    dt_samira_window = datetime(2026, 11, 19, 7, 30, 0, tzinfo=BRT_TZ)
    allowed_sc, reason_sc, hold_sc = ky4.check_segment_window_safety(
        dt_samira_window,
        transcript_or_title="Skorpion comentando Vice City no Passaporte",
    )
    assert allowed_sc is True
    assert hold_sc is True
    assert "samira close" in reason_sc.lower()

    # Explicit Samira Close presence is immediately HARD REJECTED
    allowed_sc_vis, reason_sc_vis, _ = ky4.check_segment_window_safety(
        dt_samira_window,
        transcript_or_title="Samira Close falando na bancada",
    )
    assert allowed_sc_vis is False
    assert "samira close" in reason_sc_vis.lower()

    # - 22:40 BRT -> ALLOWED without hold (Skorpion PT-BR Gameplay Golden Window 22:30-22:57)
    dt_skorpion = datetime(2026, 11, 19, 22, 40, 0, tzinfo=BRT_TZ)
    allowed_sk, reason_sk, hold_sk = ky4.check_segment_window_safety(
        dt_skorpion,
        transcript_or_title="Skorpion jogando GTA VI ao vivo em português!",
    )
    assert allowed_sk is True
    assert hold_sk is False
    assert "skorpion" in reason_sk.lower()
