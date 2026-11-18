"""Multi-Channel Matrix (5 Channels x 12 Shorts/Day = 60/Day, 300/5d) & Anti-Shadowban Coordinator."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, Field

from gta6_agents.config import AppConfig
from gta6_agents.models import ContentPillar
from gta6_agents.storage import StateStore


def _resolve_tz(tz_name: str = "America/Sao_Paulo") -> timezone | ZoneInfo:
    """Resolve IANA timezone with safe UTC-3 fallback if system tzdata is absent."""
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=-3), name="BRT")


def _canonicalize_source_url(url: str) -> str:
    """Normalize YouTube and web URLs so identical videos share a single lock key."""
    cleaned = url.strip()
    if not cleaned:
        return cleaned
    try:
        parsed = urlparse(cleaned)
        host = parsed.netloc.lower().removeprefix("www.").removeprefix("m.")
        if host == "youtu.be":
            vid = parsed.path.strip("/").split("/")[0]
            if vid:
                return f"youtube:{vid}"
        if host in ("youtube.com", "youtube-nocookie.com"):
            if parsed.path == "/watch":
                vid = parse_qs(parsed.query).get("v", [""])[0]
                if vid:
                    return f"youtube:{vid}"
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) >= 2 and parts[0] in ("shorts", "embed", "live", "v"):
                return f"youtube:{parts[1]}"
    except Exception:
        pass
    return cleaned


class YouTubeQuotaPool(BaseModel):
    """Per-channel YouTube Data API v3 dual-GCP OAuth & Real Oficial Launcher pool.

    YouTube Data API v3 grants 10,000 units/day per GCP project, and `videos.insert`
    costs 1,600 units (6 * 1600 = 9,600 units = max 6 uploads/GCP project/day).
    Pairing `project_a` (uploads 1..6) + `project_b` (uploads 7..12) or routing via
    `real_oficial_launcher_id` unlocks all 12 daily Shorts per channel without HTTP 403 quota errors.
    """

    project_a_env_prefix: str
    project_b_env_prefix: str = ""
    max_uploads_per_gcp_project_per_day: int = 6
    upload_cost_units: int = 1600
    daily_project_quota_units: int = 10000
    real_oficial_launcher_id: str = ""


class GlobalScalingConfig(BaseModel):
    """Network-wide scaling & anti-shadowban configuration across all 5 channels."""

    timezone: str = "America/Sao_Paulo"
    total_channels: int = 5
    videos_per_channel_per_day: int = 12
    total_daily_videos: int = 60
    total_5day_videos: int = 300
    min_spacing_minutes_per_channel: int = 90
    cross_channel_stagger_minutes: int = 15
    warmup_strategy: str = "full_12"  # "full_12" | "safe_ramp_6_to_12"
    warmup_strategies_catalog: dict[str, Any] = Field(default_factory=dict)
    anti_shadowban: dict[str, Any] = Field(default_factory=dict)


class ChannelProfile(BaseModel):
    """Distinct editorial identity, visual/audio fingerprint, schedule, and OAuth pool for one channel."""

    channel_id: str
    channel_idx: int
    name: str
    handle: str = ""
    editorial_angle: str
    language: str = "pt-BR"
    voice: str
    tts_rate: str = "+0%"
    tts_pitch: str = "+0Hz"
    layout: str = "blur_pad_hud"  # "blur_pad_hud" | "smart_crop"
    subtitle_accent: str = "cyan"
    subtitle_accent_ass_bgr: str = "&H00FFFF00"
    preferred_ai_thinker: str = "claude_pro"
    color_grade_preset: str = "broadcast_crisp_cyan"
    ffmpeg_crf: int = 20
    pillars: list[ContentPillar] = Field(default_factory=lambda: [ContentPillar.GAMEPLAY_CLIPS])
    stagger_minutes: int = 0
    daily_slots_brt: list[str] = Field(default_factory=list)
    youtube_quota_pool: YouTubeQuotaPool
    micro_trim_offset_sec: float = 0.0
    real_oficial_campaign_slug: str = "gta-vi-youtube"

    @property
    def id(self) -> str:
        return self.channel_id

    @property
    def layout_mode(self) -> str:
        return self.layout

    @property
    def voice_pt(self) -> str:
        return self.voice

    def build_fingerprint_metadata(
        self,
        source_url: str = "",
        adjusted_start_sec: float = 0.0,
        adjusted_end_sec: float = 0.0,
        date_str: str = "",
    ) -> dict[str, Any]:
        """Return unique visual/audio/encoding metadata ensuring distinct MP4 hashes & frames."""
        meta = {
            "channel_id": self.channel_id,
            "channel_idx": self.channel_idx,
            "editorial_angle": self.editorial_angle,
            "language": self.language,
            "voice": self.voice,
            "tts_rate": self.tts_rate,
            "tts_pitch": self.tts_pitch,
            "layout": self.layout,
            "subtitle_accent": self.subtitle_accent,
            "subtitle_accent_ass_bgr": self.subtitle_accent_ass_bgr,
            "preferred_ai_thinker": self.preferred_ai_thinker,
            "color_grade_preset": self.color_grade_preset,
            "ffmpeg_crf": self.ffmpeg_crf,
            "micro_trim_offset_sec": self.micro_trim_offset_sec,
            "source_url": source_url,
            "start_sec": adjusted_start_sec,
            "end_sec": adjusted_end_sec,
            "date_str": date_str,
        }
        canonical = json.dumps(meta, sort_keys=True, ensure_ascii=False)
        meta["fingerprint_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
        return meta


class MultiChannelCoordinator:
    """Coordinates 5 GTA 6 channels (60 Shorts/day, 300 in 5 days) with anti-shadowban & quota routing."""

    def __init__(
        self,
        config: AppConfig,
        store: StateStore | None = None,
        matrix_path: Path | None = None,
    ) -> None:
        self.config = config
        self.store = store or StateStore(data_dir=config.data_dir, memory_dir=config.memory_dir)
        self.matrix_path = matrix_path or (self.config.root_dir / "config" / "channels_matrix.yaml")

        self.global_scaling = GlobalScalingConfig(timezone=self.config.timezone)
        self._channels: list[ChannelProfile] = []
        self._channels_by_id: dict[str, ChannelProfile] = {}

        self._init_tables()
        self.load_channels()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.store.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000;")
        return conn

    def _init_tables(self) -> None:
        """Automatically initialize multi-channel anti-shadowban & YouTube quota tables on store.db_path."""
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS channel_segment_locks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    date_str TEXT NOT NULL,
                    raw_start_sec REAL NOT NULL,
                    raw_end_sec REAL NOT NULL,
                    start_sec REAL NOT NULL,
                    end_sec REAL NOT NULL,
                    micro_trim_offset_sec REAL NOT NULL,
                    layout TEXT NOT NULL,
                    voice TEXT NOT NULL,
                    subtitle_accent TEXT NOT NULL,
                    fingerprint_hash TEXT NOT NULL,
                    fingerprint_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_channel_segment_locks_source_date
                    ON channel_segment_locks (source_key, date_str);

                CREATE INDEX IF NOT EXISTS idx_channel_segment_locks_url_date
                    ON channel_segment_locks (source_url, date_str);

                CREATE TABLE IF NOT EXISTS youtube_quota_usage (
                    channel_id TEXT NOT NULL,
                    project_slot TEXT NOT NULL,
                    date_str TEXT NOT NULL,
                    uploads_count INTEGER NOT NULL DEFAULT 0,
                    units_used INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (channel_id, project_slot, date_str)
                );
                """
            )

    def _micro_trim_step_sec(self) -> float:
        step = self.global_scaling.anti_shadowban.get("micro_trim_offset_step_sec", 0.4)
        try:
            return float(step)
        except (TypeError, ValueError):
            return 0.4

    def _today_brt_str(self) -> str:
        tz = _resolve_tz(self.global_scaling.timezone or self.config.timezone)
        return datetime.now(tz).strftime("%Y-%m-%d")

    def load_channels(self) -> list[ChannelProfile]:
        """Load and validate all 5 distinct GTA 6 channel profiles from config/channels_matrix.yaml."""
        if not self.matrix_path.exists():
            raise FileNotFoundError(f"Multi-channel matrix config not found: {self.matrix_path}")

        raw_data = yaml.safe_load(self.matrix_path.read_text(encoding="utf-8")) or {}
        scaling_raw = raw_data.get("global_scaling") or {}
        self.global_scaling = GlobalScalingConfig.model_validate(scaling_raw)

        step_sec = self._micro_trim_step_sec()
        campaign_slug = str(
            self.global_scaling.anti_shadowban.get(
                "campaign_slug", self.config.real_oficial_campaign_slug
            )
        )

        loaded: list[ChannelProfile] = []
        for idx, ch_raw in enumerate(raw_data.get("channels") or []):
            ch_dict = dict(ch_raw)
            ch_idx = int(ch_dict.get("channel_idx", idx))
            ch_dict["channel_idx"] = ch_idx
            ch_dict.setdefault("micro_trim_offset_sec", round(step_sec * ch_idx, 3))
            ch_dict.setdefault("real_oficial_campaign_slug", campaign_slug)
            profile = ChannelProfile.model_validate(ch_dict)
            profile.micro_trim_offset_sec = round(step_sec * profile.channel_idx, 3)
            loaded.append(profile)

        loaded.sort(key=lambda c: c.channel_idx)
        self._channels = loaded
        self._channels_by_id = {c.channel_id: c for c in loaded}
        return list(self._channels)

    def get_channel(self, channel_id: str) -> ChannelProfile:
        """Retrieve a configured ChannelProfile by ID."""
        if not self._channels_by_id:
            self.load_channels()
        if channel_id not in self._channels_by_id:
            valid = ", ".join(sorted(self._channels_by_id))
            raise ValueError(f"Unknown channel_id '{channel_id}'. Expected one of: {valid}")
        return self._channels_by_id[channel_id]

    def build_daily_60_schedule(
        self,
        date_str: str | None = None,
        warmup_day: int | None = None,
    ) -> list[dict[str, Any]]:
        """Build all 60 daily scheduled slot dicts sorted chronologically across the 5 channels."""
        channels = self._channels or self.load_channels()
        target_date_str = date_str or self._today_brt_str()
        target_day = date.fromisoformat(target_date_str)
        tz = _resolve_tz(self.global_scaling.timezone or self.config.timezone)

        per_channel_cap = self.global_scaling.videos_per_channel_per_day
        if self.global_scaling.warmup_strategy == "safe_ramp_6_to_12":
            ramp_caps = (
                self.global_scaling.warmup_strategies_catalog.get("safe_ramp_6_to_12", {}).get(
                    "daily_cap_by_day"
                )
                or [6, 6, 8, 10, 12]
            )
            day_idx = max(0, min(len(ramp_caps) - 1, (warmup_day or 1) - 1))
            per_channel_cap = int(ramp_caps[day_idx])

        schedule: list[dict[str, Any]] = []
        for ch in channels:
            slots = ch.daily_slots_brt[:per_channel_cap]
            max_proj_a = ch.youtube_quota_pool.max_uploads_per_gcp_project_per_day

            for slot_idx, hhmm in enumerate(slots):
                hour_s, minute_s = hhmm.strip().split(":", maxsplit=1)
                dt_brt = datetime.combine(
                    target_day,
                    time(hour=int(hour_s), minute=int(minute_s)),
                    tzinfo=tz,
                )
                dt_utc = dt_brt.astimezone(timezone.utc)

                pillar = (
                    ch.pillars[slot_idx % len(ch.pillars)]
                    if ch.pillars
                    else ContentPillar.GAMEPLAY_CLIPS
                )
                upload_num = slot_idx + 1
                if upload_num <= max_proj_a:
                    expected_oauth_slot = "project_a"
                elif ch.youtube_quota_pool.project_b_env_prefix:
                    expected_oauth_slot = "project_b"
                else:
                    expected_oauth_slot = "real_oficial_launcher"

                schedule.append(
                    {
                        "date": target_date_str,
                        "time_brt": hhmm.strip(),
                        "scheduled_at_brt": dt_brt.isoformat(),
                        "scheduled_at_utc": dt_utc.isoformat(),
                        "channel_id": ch.channel_id,
                        "channel_idx": ch.channel_idx,
                        "channel_name": ch.name,
                        "handle": ch.handle,
                        "editorial_angle": ch.editorial_angle,
                        "channel_slot_index": slot_idx,
                        "channel_upload_number": upload_num,
                        "pillar": pillar.value,
                        "language": ch.language,
                        "voice": ch.voice,
                        "tts_rate": ch.tts_rate,
                        "tts_pitch": ch.tts_pitch,
                        "layout": ch.layout,
                        "subtitle_accent": ch.subtitle_accent,
                        "subtitle_accent_ass_bgr": ch.subtitle_accent_ass_bgr,
                        "preferred_ai_thinker": ch.preferred_ai_thinker,
                        "color_grade_preset": ch.color_grade_preset,
                        "ffmpeg_crf": ch.ffmpeg_crf,
                        "micro_trim_offset_sec": ch.micro_trim_offset_sec,
                        "expected_oauth_slot": expected_oauth_slot,
                        "campaign_slug": ch.real_oficial_campaign_slug,
                        "fingerprint_metadata": ch.build_fingerprint_metadata(
                            date_str=target_date_str
                        ),
                    }
                )

        schedule.sort(key=lambda item: (item["scheduled_at_brt"], item["channel_idx"]))
        for global_idx, item in enumerate(schedule, start=1):
            item["global_slot_number"] = global_idx
        return schedule

    def build_5day_300_schedule(
        self,
        start_date_str: str | None = None,
        days: int = 5,
    ) -> list[dict[str, Any]]:
        """Build the complete 5-day (300-Short) multi-channel schedule sorted chronologically."""
        base_date = date.fromisoformat(start_date_str or self._today_brt_str())
        full_plan: list[dict[str, Any]] = []
        for day_offset in range(days):
            d_str = (base_date + timedelta(days=day_offset)).isoformat()
            daily = self.build_daily_60_schedule(date_str=d_str, warmup_day=day_offset + 1)
            for item in daily:
                item["campaign_day"] = day_offset + 1
                full_plan.append(item)
        for idx, item in enumerate(full_plan, start=1):
            item["sprint_video_number"] = idx
        return full_plan

    def get_render_fingerprint(
        self,
        channel_id: str,
        source_url: str = "",
        start_sec: float = 0.0,
        end_sec: float = 25.0,
        date_str: str | None = None,
    ) -> dict[str, Any]:
        """Compute per-channel micro-trimmed timestamps and unique FFmpeg/TTS fingerprint metadata."""
        ch = self.get_channel(channel_id)
        offset = round(self._micro_trim_step_sec() * ch.channel_idx, 3)
        adj_start = round(max(0.0, float(start_sec)) + offset, 3)
        adj_end = round(float(end_sec) + offset, 3)
        return ch.build_fingerprint_metadata(
            source_url=source_url.strip(),
            adjusted_start_sec=adj_start,
            adjusted_end_sec=adj_end,
            date_str=date_str or self._today_brt_str(),
        )

    def claim_unique_segment(
        self,
        channel_id: str,
        source_url: str,
        start_sec: float,
        end_sec: float,
        date_str: str,
    ) -> tuple[bool, float, float]:
        """Atomically lock a non-overlapping daily source segment and apply per-channel micro-trim.

        Guarantees two channels NEVER post overlapping segments of the same video on the same day,
        and shifts timestamps by `+0.4s * channel_idx` alongside channel-specific layout/voice/color
        fingerprint metadata stored in `channel_segment_locks`.

        Returns:
            `(claimed, adjusted_start_sec, adjusted_end_sec)`
        """
        ch = self.get_channel(channel_id)
        raw_start = max(0.0, float(start_sec))
        raw_end = float(end_sec)
        if raw_end <= raw_start:
            raise ValueError(f"end_sec ({raw_end}) must be greater than start_sec ({raw_start})")

        offset_sec = round(self._micro_trim_step_sec() * ch.channel_idx, 3)
        adj_start = round(raw_start + offset_sec, 3)
        adj_end = round(raw_end + offset_sec, 3)

        clean_url = source_url.strip()
        source_key = _canonicalize_source_url(clean_url)
        target_date = date_str.strip()

        fingerprint_meta = ch.build_fingerprint_metadata(
            source_url=clean_url,
            adjusted_start_sec=adj_start,
            adjusted_end_sec=adj_end,
            date_str=target_date,
        )
        fingerprint_hash = str(fingerprint_meta["fingerprint_hash"])
        fingerprint_json = json.dumps(fingerprint_meta, ensure_ascii=False, sort_keys=True)
        now_iso = datetime.now(timezone.utc).isoformat()

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing_rows = conn.execute(
                """
                SELECT channel_id, raw_start_sec, raw_end_sec, start_sec, end_sec
                FROM channel_segment_locks
                WHERE source_key = ? AND date_str = ?
                """,
                (source_key, target_date),
            ).fetchall()

            for row in existing_rows:
                raw_overlaps = raw_start < float(row["raw_end_sec"]) and raw_end > float(
                    row["raw_start_sec"]
                )
                adj_overlaps = adj_start < float(row["end_sec"]) and adj_end > float(
                    row["start_sec"]
                )
                if raw_overlaps or adj_overlaps:
                    conn.rollback()
                    return (False, adj_start, adj_end)

            conn.execute(
                """
                INSERT INTO channel_segment_locks (
                    channel_id,
                    source_url,
                    source_key,
                    date_str,
                    raw_start_sec,
                    raw_end_sec,
                    start_sec,
                    end_sec,
                    micro_trim_offset_sec,
                    layout,
                    voice,
                    subtitle_accent,
                    fingerprint_hash,
                    fingerprint_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ch.channel_id,
                    clean_url,
                    source_key,
                    target_date,
                    raw_start,
                    raw_end,
                    adj_start,
                    adj_end,
                    offset_sec,
                    ch.layout,
                    ch.voice,
                    ch.subtitle_accent,
                    fingerprint_hash,
                    fingerprint_json,
                    now_iso,
                ),
            )
            conn.commit()
            return (True, adj_start, adj_end)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _resolve_oauth_env_credentials(
        self, env_prefix: str, fallback_to_default: bool = False
    ) -> dict[str, str]:
        if not env_prefix:
            return {"client_id": "", "client_secret": "", "refresh_token": ""}

        client_id = os.getenv(f"{env_prefix}_CLIENT_ID", "")
        client_secret = os.getenv(f"{env_prefix}_CLIENT_SECRET", "")
        refresh_token = os.getenv(f"{env_prefix}_REFRESH_TOKEN", "")

        if fallback_to_default and not (client_id and client_secret and refresh_token):
            client_id = client_id or self.config.youtube_client_id
            client_secret = client_secret or self.config.youtube_client_secret
            refresh_token = refresh_token or self.config.youtube_refresh_token

        return {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        }

    def select_youtube_oauth_slot(
        self,
        channel_id: str,
        date_str: str,
        consume: bool = True,
    ) -> dict[str, Any]:
        """Select and track the active daily YouTube publishing route for a channel.

        Tracks uploads per `(channel_id, project_slot, date_str)` in SQLite `youtube_quota_usage`:
        - Uploads 1..6: `project_a` (`6 * 1,600 = 9,600 / 10,000` units)
        - Uploads 7..12: automatically switches to `project_b` (if configured) or `real_oficial_launcher`
        - Upload 13+ (or if `project_b` reaches 6): falls over to `real_oficial_launcher`
        """
        ch = self.get_channel(channel_id)
        pool = ch.youtube_quota_pool
        target_date = date_str.strip()
        max_per_project = pool.max_uploads_per_gcp_project_per_day
        launcher_id = (
            os.getenv(f"REAL_OFICIAL_LAUNCHER_ID_{ch.channel_idx + 1}", "")
            or pool.real_oficial_launcher_id
            or self.config.real_oficial_launcher_id
        )

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                """
                SELECT project_slot, uploads_count, units_used
                FROM youtube_quota_usage
                WHERE channel_id = ? AND date_str = ?
                """,
                (ch.channel_id, target_date),
            ).fetchall()

            counts: dict[str, int] = {r["project_slot"]: int(r["uploads_count"]) for r in rows}
            units: dict[str, int] = {r["project_slot"]: int(r["units_used"]) for r in rows}

            proj_a_used = counts.get("project_a", 0)
            proj_b_used = counts.get("project_b", 0)
            launcher_used = counts.get("real_oficial_launcher", 0)

            if proj_a_used < max_per_project:
                selected_slot = "project_a"
                mode = "youtube_oauth"
                env_prefix = pool.project_a_env_prefix
                cost_units = pool.upload_cost_units
                slot_limit: int | None = max_per_project
                allowed = True
            elif pool.project_b_env_prefix and proj_b_used < max_per_project:
                selected_slot = "project_b"
                mode = "youtube_oauth"
                env_prefix = pool.project_b_env_prefix
                cost_units = pool.upload_cost_units
                slot_limit = max_per_project
                allowed = True
            elif launcher_id:
                selected_slot = "real_oficial_launcher"
                mode = "real_oficial_launcher"
                env_prefix = ""
                cost_units = 0
                slot_limit = None
                allowed = True
            else:
                conn.rollback()
                total_used = proj_a_used + proj_b_used + launcher_used
                return {
                    "channel_id": ch.channel_id,
                    "date": target_date,
                    "slot": "quota_exhausted",
                    "project_slot": "quota_exhausted",
                    "mode": "blocked",
                    "allowed": False,
                    "env_prefix": "",
                    "client_id": "",
                    "client_secret": "",
                    "refresh_token": "",
                    "credentials_present": False,
                    "real_oficial_launcher_id": "",
                    "slot_uploads_used": max_per_project,
                    "slot_uploads_limit": max_per_project,
                    "channel_daily_uploads_used": total_used,
                    "units_used": units.get("project_b", units.get("project_a", 0)),
                    "units_remaining": 0,
                    "daily_quota_units": pool.daily_project_quota_units,
                    "upload_cost_units": pool.upload_cost_units,
                }

            new_slot_uploads = counts.get(selected_slot, 0) + (1 if consume else 0)
            new_slot_units = units.get(selected_slot, 0) + (cost_units if consume else 0)

            if consume:
                now_iso = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    """
                    INSERT INTO youtube_quota_usage (
                        channel_id, project_slot, date_str, uploads_count, units_used, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(channel_id, project_slot, date_str) DO UPDATE SET
                        uploads_count = excluded.uploads_count,
                        units_used = excluded.units_used,
                        updated_at = excluded.updated_at
                    """,
                    (
                        ch.channel_id,
                        selected_slot,
                        target_date,
                        new_slot_uploads,
                        new_slot_units,
                        now_iso,
                    ),
                )
                conn.commit()
            else:
                conn.rollback()

            creds = self._resolve_oauth_env_credentials(
                env_prefix,
                fallback_to_default=(selected_slot == "project_a" and ch.channel_idx == 0),
            )
            total_channel_uploads = (
                proj_a_used + proj_b_used + launcher_used + (1 if consume else 0)
            )
            units_remaining = (
                max(0, pool.daily_project_quota_units - new_slot_units)
                if mode == "youtube_oauth"
                else None
            )

            return {
                "channel_id": ch.channel_id,
                "date": target_date,
                "slot": selected_slot,
                "project_slot": selected_slot,
                "mode": mode,
                "allowed": allowed,
                "env_prefix": env_prefix,
                "client_id": creds["client_id"],
                "client_secret": creds["client_secret"],
                "refresh_token": creds["refresh_token"],
                "credentials_present": bool(
                    creds["client_id"] and creds["client_secret"] and creds["refresh_token"]
                ),
                "real_oficial_launcher_id": launcher_id,
                "slot_uploads_used": new_slot_uploads,
                "slot_uploads_limit": slot_limit,
                "channel_daily_uploads_used": total_channel_uploads,
                "units_used": new_slot_units,
                "units_remaining": units_remaining,
                "daily_quota_units": pool.daily_project_quota_units,
                "upload_cost_units": cost_units,
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_daily_quota_summary(self, date_str: str | None = None) -> dict[str, Any]:
        """Return aggregated daily upload & quota consumption across all 5 channels."""
        target_date = date_str or self._today_brt_str()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT channel_id, project_slot, uploads_count, units_used, updated_at
                FROM youtube_quota_usage
                WHERE date_str = ?
                ORDER BY channel_id, project_slot
                """,
                (target_date,),
            ).fetchall()

        by_channel: dict[str, dict[str, Any]] = {
            ch.channel_id: {"total_uploads": 0, "slots": {}} for ch in self._channels
        }
        total_network_uploads = 0
        for row in rows:
            cid = str(row["channel_id"])
            by_channel.setdefault(cid, {"total_uploads": 0, "slots": {}})
            count = int(row["uploads_count"])
            by_channel[cid]["total_uploads"] += count
            by_channel[cid]["slots"][str(row["project_slot"])] = {
                "uploads_count": count,
                "units_used": int(row["units_used"]),
                "updated_at": row["updated_at"],
            }
            total_network_uploads += count

        return {
            "date": target_date,
            "total_network_uploads": total_network_uploads,
            "daily_target": self.global_scaling.total_daily_videos,
            "channels": by_channel,
        }
