"""Clipper & Editor Agent orchestrating source download, subtitles, voiceover, and FFmpeg 9:16 rendering."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from urllib.parse import urlparse

from gta6_agents.config import AppConfig
from gta6_agents.models import ClipPlan, RenderedClip, SubtitleCue, new_id
from gta6_agents.storage import StateStore
from gta6_agents.video.downloader import (
    download_video_segment,
    extract_youtube_subtitles,
    generate_synthetic_source_video,
)
from gta6_agents.video.ffmpeg_renderer import render_vertical_short
from gta6_agents.video.subtitles import build_ass_subtitles, cues_from_script
from gta6_agents.video.voiceover import synthesize_voiceover

logger = logging.getLogger(__name__)


def _format_creator_credit(creator_handle: str) -> str:
    handle = (creator_handle or "@RockstarGames").strip()
    if not handle.startswith("@"):
        handle = f"@{handle}"
    return f"🎥 {handle}"


def _is_existing_local_source(url: str) -> bool:
    raw = (url or "").strip()
    if not raw:
        return False
    if raw.startswith("file://"):
        return Path(urlparse(raw).path).is_file()
    if "://" not in raw:
        return Path(raw).is_file()
    return False


def _determine_permission_basis(plan: ClipPlan) -> str:
    if plan.for_real_oficial_campaign or plan.source.is_campaign_acervo:
        return "real_oficial_campaign_acervo"
    if plan.source.is_official:
        return "rockstar_official_media_transformed"
    return "public_credited_transformed"


class ClipperEditorAgent:
    """Transforms a ClipPlan into a broadcast-ready 1080x1920 Short/Reel without third-party watermarks."""

    def __init__(self, config: AppConfig, store: StateStore) -> None:
        self.config = config
        self.store = store

    def _compute_clip_duration(self, plan: ClipPlan) -> float:
        video_cfg = self.config.settings.get("video_pipeline", {}) or {}
        max_sec = float(video_cfg.get("max_clip_sec", 58.0))
        # Ensure Real Oficial / Shorts ceiling of 58s is always respected
        hard_ceiling = min(58.0, max(max_sec, 8.0))

        raw_dur = float(plan.end_sec) - float(plan.start_sec)
        if raw_dur <= 0:
            raw_dur = 15.0
        return max(1.0, min(raw_dur, hard_ceiling))

    def _preferred_subtitle_languages(self, language: str) -> list[str]:
        lang = (language or "pt-BR").strip().lower()
        if lang.startswith("en"):
            return ["en", "pt"]
        return ["pt", "en"]

    async def _acquire_source_segment(
        self,
        plan: ClipPlan,
        clip_duration: float,
        clip_work_dir: Path,
        use_demo_source_if_needed: bool,
    ) -> tuple[Path, bool]:
        """Return (source_segment_path, used_synthetic_source)."""
        url = (plan.source.url or "").strip()
        synthetic_path = clip_work_dir / f"{plan.id}_synthetic_source.mp4"

        if not url or url.startswith(("synthetic://", "demo://", "mock://")):
            path = await asyncio.to_thread(
                generate_synthetic_source_video,
                synthetic_path,
                clip_duration,
            )
            return path, True

        if self.config.dry_run and use_demo_source_if_needed and not _is_existing_local_source(url):
            path = await asyncio.to_thread(
                generate_synthetic_source_video,
                synthetic_path,
                clip_duration,
            )
            return path, True

        try:
            segment_path = await download_video_segment(
                url=url,
                start_sec=plan.start_sec,
                end_sec=plan.start_sec + clip_duration,
                work_dir=clip_work_dir,
            )
            return segment_path, False
        except Exception as exc:
            if use_demo_source_if_needed or self.config.dry_run:
                logger.warning(
                    "Source download unavailable for %s (%s); generating synthetic 1080p demo source.",
                    url,
                    exc,
                )
                path = await asyncio.to_thread(
                    generate_synthetic_source_video,
                    synthetic_path,
                    clip_duration,
                )
                return path, True
            raise

    async def render_clip_plan(
        self,
        plan: ClipPlan,
        use_demo_source_if_needed: bool = False,
    ) -> RenderedClip:
        """Download or synthesize source media, build .ass overlays + voiceover, render 9:16 MP4, and log asset."""
        clip_duration = self._compute_clip_duration(plan)
        clip_work_dir = self.config.work_dir / plan.id
        clip_work_dir.mkdir(parents=True, exist_ok=True)
        self.config.output_dir.mkdir(parents=True, exist_ok=True)

        source_segment, used_synthetic = await self._acquire_source_segment(
            plan=plan,
            clip_duration=clip_duration,
            clip_work_dir=clip_work_dir,
            use_demo_source_if_needed=use_demo_source_if_needed,
        )

        # 1. Subtitle cues: prefer explicit plan cues -> YouTube transcript -> timed script cues
        cues: list[SubtitleCue] = list(plan.subtitles)
        if not cues and not used_synthetic:
            langs = self._preferred_subtitle_languages(plan.language)
            cues = await asyncio.to_thread(
                extract_youtube_subtitles,
                plan.source.url,
                plan.start_sec,
                plan.start_sec + clip_duration,
                langs,
            )

        if not cues:
            fallback_script = (
                plan.voiceover_script.strip()
                or plan.commentary_overlay.strip()
                or plan.source.summary.strip()
                or plan.hook_on_screen.strip()
            )
            cues = cues_from_script(fallback_script, total_duration_sec=clip_duration)

        # 2. Build broadcast .ass overlay (HookBanner + CaptionCenter + optional CreditTag)
        video_cfg = (self.config.settings or {}).get("video_pipeline", {}) or {}
        burn_captions = bool(video_cfg.get("burn_captions", True))
        burn_hook = bool(video_cfg.get("burn_hook_banner", True))
        burn_credit_watermark = video_cfg.get("burn_credit_watermark")
        default_hook_dur = 2.0 if plan.for_real_oficial_campaign else 2.5
        hook_duration_sec = float(video_cfg.get("hook_duration_sec", default_hook_dur))
        layout_mode = str(video_cfg.get("layout_mode", "blur_pad_hud"))
        crf = int(video_cfg.get("crf", 20))
        loudnorm_lufs = float(video_cfg.get("loudnorm_lufs", -14.0))

        # Rule #10: ZERO watermark or logo burned on video frame for Real Oficial campaign
        if plan.for_real_oficial_campaign or burn_credit_watermark is False:
            credit_text = ""
        else:
            credit_text = _format_creator_credit(plan.source.creator_handle)
        hook_text = plan.hook_on_screen if burn_hook else ""
        ass_cues = cues if burn_captions else []

        ass_path = clip_work_dir / f"{plan.id}.ass"
        await asyncio.to_thread(
            build_ass_subtitles,
            ass_cues,
            hook_text,
            credit_text,
            ass_path,
            hook_duration_sec,
            clip_duration,
        )

        # 3. Synthesize optional voiceover narration
        voiceover_path: Path | None = None
        if plan.voiceover_script and plan.voiceover_script.strip():
            vo_target = clip_work_dir / f"{plan.id}_voiceover.mp3"
            voiceover_path = await synthesize_voiceover(
                text=plan.voiceover_script,
                language=plan.language,
                config=self.config,
                output_mp3=vo_target,
            )

        # 4. Render 1080x1920 vertical short via FFmpeg
        clip_id = new_id("clip")
        output_video = self.config.output_dir / f"{clip_id}.mp4"
        render_info = await asyncio.to_thread(
            render_vertical_short,
            source_segment,
            output_video,
            ass_path,
            voiceover_path,
            0.0,
            clip_duration,
            layout_mode,
            crf,
            loudnorm_lufs,
        )

        rendered = RenderedClip(
            id=clip_id,
            plan_id=plan.id,
            video_path=str(render_info["video_path"]),
            audio_path=render_info.get("audio_path"),
            ass_subtitle_path=render_info.get("ass_subtitle_path"),
            thumbnail_path=render_info.get("thumbnail_path"),
            duration_sec=float(render_info["duration_sec"]),
            width=int(render_info.get("width", 1080)),
            height=int(render_info.get("height", 1920)),
            has_watermark_third_party=False,
            creator_credited_on_screen=credit_text,
        )

        self.store.record_asset(
            rendered,
            source_url=plan.source.url,
            permission_basis=_determine_permission_basis(plan),
        )
        return rendered
