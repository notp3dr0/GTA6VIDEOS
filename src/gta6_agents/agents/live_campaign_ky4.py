"""Real Oficial 'YouTube x GTA VI' 25-Hour Live Championship (`KY4DWumZffg`) Safety & Phase Engine.

Enforces the exact official championship schedule (America/Sao_Paulo, UTC-3),
forbidden-person blackouts (The Burnt Peanut & Samira Close), multimodal Vision AI
frame inspection, Prime Time / Skorpion PT-BR Gameplay burst detection, and
automatic transition to Post-Live PT-BR Streamer Clipping mode.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from gta6_agents.config import AppConfig

logger = logging.getLogger(__name__)

try:
    BRT_TZ: timezone | ZoneInfo = ZoneInfo("America/Sao_Paulo")
except Exception:  # pragma: no cover
    BRT_TZ = timezone(timedelta(hours=-3))

# Official Real Oficial "YouTube x GTA VI" Live Campaign Constants
AUTHORIZED_LIVE_VIDEO_ID = "KY4DWumZffg"
AUTHORIZED_LIVE_URL = "https://www.youtube.com/watch?v=KY4DWumZffg"
MANDATORY_CAPTION_PHRASE = "Assista ao vivo em youtube.com/youtube"
MANDATORY_ONLY_HASHTAG = "#launchdayonYT"
MANDATORY_PINNED_COMMENT = "Assista em https://www.youtube.com/watch?v=KY4DWumZffg"

# Campaign Lifecycle Phases
PHASE_PRE_LIVE = "PRE_LIVE"
PHASE_LIVE_ON_AIR = "LIVE_ON_AIR"
PHASE_VIEWS_COUNTING_48H = "VIEWS_COUNTING_48H"
PHASE_POST_CAMPAIGN = "POST_CAMPAIGN"

# Exact BRT (UTC-3) Schedule Boundaries
LIVE_START_BRT = datetime(2026, 11, 19, 1, 0, 0, tzinfo=BRT_TZ)
HARD_STOP_POST_BRT = datetime(2026, 11, 20, 1, 57, 0, tzinfo=BRT_TZ)
LIVE_END_BRT = datetime(2026, 11, 20, 2, 0, 0, tzinfo=BRT_TZ)
VIEWS_COUNT_UNTIL_BRT = datetime(2026, 11, 22, 2, 0, 0, tzinfo=BRT_TZ)

# Creator Safety & Highlight Windows (BRT)
SAMIRA_DANGER_START_BRT = datetime(2026, 11, 19, 7, 2, 0, tzinfo=BRT_TZ)
SAMIRA_DANGER_END_BRT = datetime(2026, 11, 19, 8, 12, 0, tzinfo=BRT_TZ)

BURNT_PEANUT_START_BRT = datetime(2026, 11, 19, 20, 0, 0, tzinfo=BRT_TZ)
BURNT_PEANUT_END_BRT = datetime(2026, 11, 19, 20, 34, 0, tzinfo=BRT_TZ)

SKORPION_GOLDEN_START_BRT = datetime(2026, 11, 19, 22, 30, 0, tzinfo=BRT_TZ)
SKORPION_GOLDEN_END_BRT = datetime(2026, 11, 19, 22, 57, 0, tzinfo=BRT_TZ)

PRIME_TIME_START_BRT = datetime(2026, 11, 19, 19, 0, 0, tzinfo=BRT_TZ)
PRIME_TIME_END_BRT = datetime(2026, 11, 20, 0, 0, 0, tzinfo=BRT_TZ)

SAMIRA_HOLD_MESSAGE = (
    "Held: requires Vision API key or Telegram human check during 07:04-08:10 Samira Close window"
)

DEFAULT_PTBR_STREAMERS: list[dict[str, str]] = [
    {"name": "Skorpion", "handle": "@Skorpion"},
    {"name": "Gamer Costa", "handle": "@GamerCosta"},
    {"name": "Alanzoka", "handle": "@alanzoka"},
    {"name": "Coringa", "handle": "@loud_coringa"},
    {"name": "Paulinho o Loko", "handle": "@PaulinhooLoko"},
    {"name": "FunkyBlackCat", "handle": "@FunkyBlackCat"},
    {"name": "Gaules", "handle": "@Gaules"},
]

_BURNT_PEANUT_RE = re.compile(r"\bburnt\s*peanut\b", re.IGNORECASE)
_SAMIRA_VISIBLE_RE = re.compile(
    r"\bsamira\s+close\b|\bsamira_on_screen\b|\bsamira\s+(?:na\s+tela|on\s+screen|vis[ií]vel)\b",
    re.IGNORECASE,
)


def _to_brt(dt: datetime | None = None) -> datetime:
    """Normalize any naive or timezone-aware datetime into America/Sao_Paulo (UTC-3)."""
    if dt is None:
        return datetime.now(BRT_TZ)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=BRT_TZ)
    return dt.astimezone(BRT_TZ)


def is_authorized_live_url(url: str | None) -> bool:
    """Return True only if URL points to the single authorized live stream `KY4DWumZffg`."""
    if not url or not isinstance(url, str):
        return False
    return AUTHORIZED_LIVE_VIDEO_ID in url


def get_campaign_phase(now: datetime | None = None) -> str:
    """Return current championship phase in America/Sao_Paulo (UTC-3).

    - `"PRE_LIVE"`: before `2026-11-19T01:00:00-03:00`
    - `"LIVE_ON_AIR"`: `2026-11-19T01:00:00-03:00` .. `2026-11-20T01:57:00-03:00` (3-min safety buffer)
    - `"VIEWS_COUNTING_48H"`: `2026-11-20T01:57:01-03:00` .. `2026-11-22T02:00:00-03:00`
    - `"POST_CAMPAIGN"`: after `2026-11-22T02:00:00-03:00`
    """
    dt_brt = _to_brt(now)
    if dt_brt < LIVE_START_BRT:
        return PHASE_PRE_LIVE
    if dt_brt <= HARD_STOP_POST_BRT:
        return PHASE_LIVE_ON_AIR
    if dt_brt <= VIEWS_COUNT_UNTIL_BRT:
        return PHASE_VIEWS_COUNTING_48H
    return PHASE_POST_CAMPAIGN


def check_segment_window_safety(
    segment_dt_brt: datetime,
    transcript_or_title: str = "",
) -> tuple[bool, str, bool]:
    """Evaluate segment timestamp & text against forbidden creators and priority burst windows.

    Returns:
        `(allowed, reason, requires_vision_or_human_hold)`
    """
    dt_brt = _to_brt(segment_dt_brt)
    text = str(transcript_or_title or "")

    # 1. HARD REJECT: The Burnt Peanut time slot (20:00-20:34 BRT) or any mention
    if _BURNT_PEANUT_RE.search(text) or (BURNT_PEANUT_START_BRT <= dt_brt <= BURNT_PEANUT_END_BRT):
        return (
            False,
            "HARD REJECT: The Burnt Peanut slot (20:00-20:34 BRT) or mention detected — strictly forbidden.",
            False,
        )

    # 2. HARD REJECT: Explicit Samira Close appearance / mention
    if _SAMIRA_VISIBLE_RE.search(text):
        return (
            False,
            "HARD REJECT: Samira Close flagged as visible/present — zero seconds on screen allowed.",
            False,
        )

    # 3. DANGER ZONE: Passaporte pra Vice City (07:02-08:12 BRT) hosted by Samira Close
    if SAMIRA_DANGER_START_BRT <= dt_brt <= SAMIRA_DANGER_END_BRT:
        return (
            True,
            (
                "DANGER ZONE (Passaporte pra Vice City 07:02-08:12 BRT): Samira Close is studio host; "
                "requires Vision AI check or Telegram human hold (only Skorpion & Gamer Costa solo shots allowed)."
            ),
            True,
        )

    # 4. PRIORITY BURST (3x): Skorpion PT-BR Gameplay Golden Window (22:30-22:57 BRT)
    if SKORPION_GOLDEN_START_BRT <= dt_brt <= SKORPION_GOLDEN_END_BRT:
        return (
            True,
            "SKORPION_GAMEPLAY_GOLDEN_WINDOW (22:30-22:57 BRT): Priority 3x PT-BR gameplay burst!",
            False,
        )

    # 5. PRIORITY BURST: Prime Time (19:00-00:00 BRT outside 20:00-20:34 Burnt Peanut blackout)
    if PRIME_TIME_START_BRT <= dt_brt <= PRIME_TIME_END_BRT:
        return (
            True,
            "PRIME_TIME (19:00-00:00 BRT): High-traffic evening priority burst window.",
            False,
        )

    return (
        True,
        "SAFE_LIVE_WINDOW: Segment clear for authorized KY4DWumZffg clipping.",
        False,
    )


def _extract_keyframes_fps1(video_path: Path, out_dir: Path, max_frames: int = 12) -> list[Path]:
    """Extract 1-fps JPEG keyframes via ffmpeg and return up to `max_frames` evenly sampled frames."""
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin or not video_path.is_file():
        return []

    pattern = out_dir / "frame_%04d.jpg"
    cmd = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        "fps=1,scale=640:-2",
        "-q:v",
        "4",
        str(pattern),
    ]
    try:
        subprocess.run(cmd, check=False, capture_output=True, timeout=30)
    except Exception as exc:
        logger.warning("FFmpeg keyframe extraction warning on %s: %s", video_path, exc)
        return []

    all_frames = sorted(out_dir.glob("frame_*.jpg"))
    if len(all_frames) <= max_frames:
        return all_frames

    step = (len(all_frames) - 1) / float(max_frames - 1)
    indices = sorted({min(len(all_frames) - 1, int(round(i * step))) for i in range(max_frames)})
    return [all_frames[i] for i in indices]


def _parse_vision_json(raw_text: str) -> dict[str, Any] | None:
    if not raw_text:
        return None
    cleaned = raw_text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, flags=re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    else:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end > start:
            cleaned = cleaned[start : end + 1]
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


async def inspect_frames_for_forbidden_persons(
    video_path: Path,
    config: AppConfig,
    segment_dt_brt: datetime | None = None,
) -> tuple[bool, str]:
    """Extract 1-fps keyframes via FFmpeg and run multimodal Vision AI gate for forbidden persons.

    Rejects any clip containing Samira Close, a female studio presenter/host, or The Burnt Peanut.
    If no Vision API key (`gemini_api_key` / `openai_api_key`) is configured during the 07:02-08:12
    Samira Close window (or when invoked directly without `segment_dt_brt`), holds the clip safely.
    """
    path_obj = Path(video_path)
    has_gemini = bool(config and config.gemini_api_key and config.gemini_api_key.strip())
    has_openai = bool(config and config.openai_api_key and config.openai_api_key.strip())
    has_vision_key = has_gemini or has_openai

    if segment_dt_brt is not None:
        dt_brt = _to_brt(segment_dt_brt)
        if BURNT_PEANUT_START_BRT <= dt_brt <= BURNT_PEANUT_END_BRT:
            return (
                False,
                "Rejected: segment falls inside 20:00-20:34 BRT The Burnt Peanut blackout window.",
            )
        in_samira_window = SAMIRA_DANGER_START_BRT <= dt_brt <= SAMIRA_DANGER_END_BRT
    else:
        now_brt = _to_brt(None)
        if BURNT_PEANUT_START_BRT <= now_brt <= BURNT_PEANUT_END_BRT:
            return (
                False,
                "Rejected: current time falls inside 20:00-20:34 BRT The Burnt Peanut blackout window.",
            )
        # When called directly without an explicit timestamp, treat as active Samira/vision gate check
        in_samira_window = True

    with tempfile.TemporaryDirectory(prefix="ky4_vision_") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        frames = await asyncio.to_thread(_extract_keyframes_fps1, path_obj, tmp_dir)

        if not has_vision_key:
            if in_samira_window:
                return (False, SAMIRA_HOLD_MESSAGE)
            return (
                True,
                "Outside 07:02-08:12 Samira Close window; no Vision API key required.",
            )

        if not frames:
            return (
                False,
                f"Held: could not extract keyframes from {path_obj} for Vision safety verification.",
            )

        encoded_frames = [
            base64.b64encode(frame.read_bytes()).decode("ascii") for frame in frames
        ]

    vision_prompt = (
        "You are a strict compliance vision inspector for the 'YouTube x GTA VI' live championship. "
        "Inspect ALL attached 1-fps video frames. Flag `forbidden_detected: true` if EVEN A SINGLE FRAME "
        "shows ANY of the following forbidden appearances:\n"
        "1. Samira Close or ANY female studio host/presenter on a podcast/studio set (such as 'Passaporte pra Vice City'). "
        "Only solo shots of male creators (Skorpion, Gamer Costa) or pure GTA VI gameplay are allowed.\n"
        "2. The Burnt Peanut (creator, peanut avatar/VTuber, or overlay).\n"
        "Return strict JSON: {\"forbidden_detected\": bool, \"person\": string, \"reason\": string}."
    )

    timeout = httpx.Timeout(20.0, connect=5.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if has_gemini:
                model = (
                    config.llm_model.strip()
                    if (config.llm_model and config.llm_model.strip().startswith("gemini"))
                    else "gemini-2.5-flash"
                )
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                parts: list[dict[str, Any]] = [{"text": vision_prompt}]
                for b64 in encoded_frames:
                    parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64}})
                payload = {
                    "contents": [{"role": "user", "parts": parts}],
                    "generationConfig": {
                        "temperature": 0.0,
                        "responseMimeType": "application/json",
                    },
                }
                resp = await client.post(
                    url,
                    headers={
                        "x-goog-api-key": config.gemini_api_key.strip(),
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                resp.raise_for_status()
                resp_data = resp.json()
                cand_parts = (
                    resp_data.get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [])
                )
                raw_text = "".join(
                    str(p.get("text", "")) for p in cand_parts if isinstance(p, dict)
                )
            else:
                content_blocks: list[dict[str, Any]] = [{"type": "text", "text": vision_prompt}]
                for b64 in encoded_frames:
                    content_blocks.append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{b64}",
                                "detail": "low",
                            },
                        }
                    )
                payload_oa = {
                    "model": "gpt-4o-mini",
                    "temperature": 0.0,
                    "response_format": {"type": "json_object"},
                    "messages": [{"role": "user", "content": content_blocks}],
                }
                resp = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {config.openai_api_key.strip()}",
                        "Content-Type": "application/json",
                    },
                    json=payload_oa,
                )
                resp.raise_for_status()
                raw_text = str(
                    resp.json()
                    .get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )

        verdict = _parse_vision_json(raw_text)
        if verdict is None:
            return (
                False,
                "Held: Vision API returned unparseable verdict; holding clip for human review.",
            )

        if bool(verdict.get("forbidden_detected", False)):
            person = str(verdict.get("person") or "forbidden presenter").strip()
            reason = str(verdict.get("reason") or "detected in extracted keyframes").strip()
            return (False, f"Rejected by Vision AI: {person} ({reason}).")

        return (
            True,
            f"Vision AI verified {len(encoded_frames)} keyframe(s): zero forbidden persons detected.",
        )
    except Exception as exc:
        logger.warning("Vision API inspection error on %s: %s", path_obj, exc)
        return (
            False,
            f"Held: Vision API check failed ({exc}); requires Telegram human verification.",
        )


class _HybridInspectDescriptor:
    """Allows `inspect_frames_for_forbidden_persons` to be called on class OR instance (with optional config)."""

    def __get__(self, instance: LiveKY4CampaignEngine | None, owner: type[LiveKY4CampaignEngine]) -> Any:
        if instance is None:
            return inspect_frames_for_forbidden_persons

        @wraps(inspect_frames_for_forbidden_persons)
        async def _bound(
            video_path: Path,
            config: AppConfig | None = None,
            segment_dt_brt: datetime | None = None,
        ) -> tuple[bool, str]:
            resolved_cfg = config or instance.config
            if resolved_cfg is None:
                resolved_cfg = AppConfig()
            return await inspect_frames_for_forbidden_persons(
                video_path=video_path,
                config=resolved_cfg,
                segment_dt_brt=segment_dt_brt,
            )

        return _bound


class LiveKY4CampaignEngine:
    """Orchestration helper enforcing all time-window, vision, and post-live streamer rules for `KY4DWumZffg`."""

    AUTHORIZED_LIVE_VIDEO_ID = AUTHORIZED_LIVE_VIDEO_ID
    AUTHORIZED_LIVE_URL = AUTHORIZED_LIVE_URL
    MANDATORY_CAPTION_PHRASE = MANDATORY_CAPTION_PHRASE
    MANDATORY_ONLY_HASHTAG = MANDATORY_ONLY_HASHTAG
    MANDATORY_PINNED_COMMENT = MANDATORY_PINNED_COMMENT

    def __init__(self, config: AppConfig | None = None, store: Any | None = None) -> None:
        self.config = config
        self.store = store

    @staticmethod
    def get_campaign_phase(now: datetime | None = None) -> str:
        return get_campaign_phase(now=now)

    @staticmethod
    def check_segment_window_safety(
        segment_dt_brt: datetime,
        transcript_or_title: str = "",
    ) -> tuple[bool, str, bool]:
        return check_segment_window_safety(
            segment_dt_brt=segment_dt_brt,
            transcript_or_title=transcript_or_title,
        )

    inspect_frames_for_forbidden_persons = _HybridInspectDescriptor()

    @staticmethod
    def is_authorized_live_url(url: str | None) -> bool:
        return is_authorized_live_url(url)

    @classmethod
    def is_campaign_posting_allowed(cls, now: datetime | None = None) -> bool:
        """Campaign clips may ONLY be posted during `LIVE_ON_AIR` (`01:00` Nov 19 .. `01:57` Nov 20 BRT)."""
        return cls.get_campaign_phase(now) == PHASE_LIVE_ON_AIR

    @classmethod
    def should_switch_to_post_live_streamers(cls, now: datetime | None = None) -> bool:
        """After `2026-11-20T01:57:00-03:00`, stop campaign uploads and switch channels to PT-BR streamer cuts."""
        return cls.get_campaign_phase(now) in (PHASE_VIEWS_COUNTING_48H, PHASE_POST_CAMPAIGN)

    def get_ptbr_priority_streamers(self) -> list[dict[str, str]]:
        """Return prioritized PT-BR GTA VI live streamers for post-live clipping mode."""
        if self.config and isinstance(self.config.campaign, dict):
            camp = self.config.campaign.get("campaign", self.config.campaign)
            if isinstance(camp, dict):
                post_cfg = camp.get("post_live_streamer_mode", {})
                if isinstance(post_cfg, dict):
                    raw_list = post_cfg.get("streamers_ptbr_priority")
                    if isinstance(raw_list, list) and raw_list:
                        return [
                            {
                                "name": str(item.get("name", "")).strip(),
                                "handle": str(item.get("handle", "")).strip(),
                            }
                            for item in raw_list
                            if isinstance(item, dict) and item.get("handle")
                        ]
        return list(DEFAULT_PTBR_STREAMERS)
