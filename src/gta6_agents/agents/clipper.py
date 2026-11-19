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
    transcribe_local_audio_ptbr,
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

        prebuffered = str((plan.source.metadata or {}).get("prebuffered_segment_path") or "").strip()
        if prebuffered:
            pre_path = Path(prebuffered)
            if pre_path.is_file() and pre_path.stat().st_size > 1024:
                return pre_path, False

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
            prefer_dvr = bool(plan.source.metadata.get("prefer_dvr_window", False))
            segment_path = await download_video_segment(
                url=url,
                start_sec=plan.start_sec,
                end_sec=plan.start_sec + clip_duration,
                work_dir=clip_work_dir,
                prefer_dvr_window=prefer_dvr,
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

        # 0. Frame-level Vision AI guard on real KY4DWumZffg frames when inside Samira Close / restricted window
        if (
            not used_synthetic
            and plan.for_real_oficial_campaign
            and bool(plan.source.metadata.get("requires_vision_or_human_hold", False))
        ):
            from gta6_agents.agents.live_campaign_ky4 import LiveKY4CampaignEngine

            ky4_guard = LiveKY4CampaignEngine(self.config, self.store)
            frame_safe, frame_reason = await ky4_guard.inspect_frames_for_forbidden_persons(
                source_segment,
                self.config,
            )
            if not frame_safe:
                raise RuntimeError(
                    f"Blocked by KY4DWumZffg Vision Gate before render: {frame_reason}"
                )

        # 1. Subtitle cues: on real live/streamer audio prefer real speech transcription (Whisper PT-BR -> YT transcript -> plan cues)
        cues: list[SubtitleCue] = []
        if not used_synthetic:
            cues = await transcribe_local_audio_ptbr(
                source_segment,
                groq_api_key=self.config.groq_api_key,
                openai_api_key=self.config.openai_api_key,
            )
            if not cues:
                langs = self._preferred_subtitle_languages(plan.language)
                cues = await asyncio.to_thread(
                    extract_youtube_subtitles,
                    plan.source.url,
                    plan.start_sec,
                    plan.start_sec + clip_duration,
                    langs,
                )

        if not cues:
            cues = list(plan.subtitles)

        if not cues:
            fallback_script = (
                plan.voiceover_script.strip()
                or plan.commentary_overlay.strip()
                or plan.source.summary.strip()
                or plan.hook_on_screen.strip()
            )
            cues = cues_from_script(fallback_script, total_duration_sec=clip_duration)

        # 1b. Auto EN->PT-BR localization for international live segments (ported from translate.py + Hydrogen Swarm)
        from gta6_agents.agents.hydrogen_swarm import (
            detect_english_text,
            scan_transcript_for_secrets,
            translate_cues_to_ptbr,
        )
        from gta6_agents.video.facecam import detect_facecam_and_game_offset
        from gta6_agents.video.quality_gate import verify_rendered_clip

        joined_speech = " ".join(c.text for c in cues).strip()
        is_english_segment = (
            plan.language.lower().startswith("pt")
            and bool(joined_speech)
            and detect_english_text(joined_speech)
        )
        hook_text_resolved = plan.hook_on_screen
        if is_english_segment:
            cues, translated_hook = await translate_cues_to_ptbr(
                cues,
                hook_text=hook_text_resolved,
                dry_run=self.config.dry_run,
            )
            if translated_hook and not plan.for_real_oficial_campaign:
                hook_text_resolved = translated_hook

        # Auto-log clean Leonida secret/easter-egg leads into memory/secrets.csv (blocking glitches/exploits/leaks)
        if joined_speech and not used_synthetic:
            scan_transcript_for_secrets(
                joined_speech,
                source_channel=plan.source.creator_handle,
                source_url=plan.source.url,
                start_sec=plan.start_sec,
                memory_dir=self.config.memory_dir,
            )

        # 2. Automatic Facecam Split-Screen detection (ported from facecam.py + cut.py react layout)
        video_cfg = (self.config.settings or {}).get("video_pipeline", {}) or {}
        src_meta = plan.source.metadata or {}
        burn_captions = bool(video_cfg.get("burn_captions", True))
        burn_hook = bool(video_cfg.get("burn_hook_banner", True))
        burn_credit_watermark = video_cfg.get("burn_credit_watermark")
        default_hook_dur = 2.0 if plan.for_real_oficial_campaign else 2.5
        hook_duration_sec = float(video_cfg.get("hook_duration_sec", default_hook_dur))

        base_layout = str(src_meta.get("channel_layout") or video_cfg.get("layout_mode", "blur_pad_hud"))
        crf = int(src_meta.get("channel_crf") or video_cfg.get("crf", 20))
        loudnorm_lufs = float(video_cfg.get("loudnorm_lufs", -14.0))
        accent_ass_bgr = str(src_meta.get("subtitle_accent_ass_bgr") or "&H0000EAFF")
        caption_style_mode = str(video_cfg.get("caption_style_mode", "kinetic_word"))
        auto_react = bool(video_cfg.get("auto_react_split_screen", True))

        explicit_cam = str(src_meta.get("explicit_cam_box") or "").strip() or None
        cam_box: tuple[float, float, float, float] | None = None
        cam_side = "right"
        game_crop_x: int | None = None
        effective_layout = base_layout

        if (auto_react or base_layout in ("react", "facecam") or explicit_cam) and (
            not used_synthetic or explicit_cam is not None
        ):
            cam_det = await asyncio.to_thread(
                detect_facecam_and_game_offset,
                source_segment,
                0.0,
                clip_duration,
                explicit_cam,
            )
            if cam_det.mode == "cam" and cam_det.box is not None and cam_det.has_gameplay:
                cam_box = cam_det.box
                cam_side = cam_det.cam_side
                game_crop_x = cam_det.game_crop_x
                effective_layout = "facecam" if base_layout == "facecam" else "react"

        # Rule #10: ZERO watermark or logo burned on video frame for Real Oficial campaign
        if plan.for_real_oficial_campaign or burn_credit_watermark is False:
            credit_text = ""
        else:
            credit_text = _format_creator_credit(plan.source.creator_handle)

        hydrogen_meta = src_meta.get("hydrogen_enrichment") or {}
        hook_meta = hydrogen_meta.get("hook") if isinstance(hydrogen_meta, dict) else {}
        reaction_title = ""
        if effective_layout == "react" and not plan.for_real_oficial_campaign and isinstance(hook_meta, dict):
            reaction_title = str(hook_meta.get("reaction_title") or "")[:64]

        hook_text = hook_text_resolved if burn_hook else ""
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
            accent_ass_bgr=accent_ass_bgr,
            style_mode=caption_style_mode,
            layout_mode=effective_layout,
            reaction_title=reaction_title,
        )

        # 3. Synthesize optional voiceover narration (or 2.5s stock neural hook for EN->PT-BR clips)
        voiceover_path: Path | None = None
        hook_vo_only = False
        vo_text = (plan.voiceover_script or "").strip()
        if not vo_text and is_english_segment and hook_text:
            vo_text = hook_text
            hook_vo_only = True
        elif is_english_segment and vo_text:
            hook_vo_only = True

        if vo_text:
            vo_target = clip_work_dir / f"{plan.id}_voiceover.mp3"
            voiceover_path = await synthesize_voiceover(
                text=vo_text,
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
            effective_layout,
            crf,
            loudnorm_lufs,
            cam_box=cam_box,
            cam_side=cam_side,
            game_crop_x=game_crop_x,
            hook_voiceover_only=hook_vo_only,
        )

        # 5. Technical Pre-Upload Quality Gate & JSON sidecar (ported from scripts/check.py)
        gate_report = await asyncio.to_thread(
            verify_rendered_clip,
            output_video,
            plan,
            "",
            min_duration_sec=1.0 if self.config.dry_run else 5.0,
            max_duration_sec=58.0,
            write_sidecar=True,
            layout_mode=effective_layout,
        )
        if not gate_report.passed:
            logger.warning(
                "QualityGate flagged clip %s (%s): %s",
                clip_id,
                output_video.name,
                ", ".join(gate_report.failures),
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
