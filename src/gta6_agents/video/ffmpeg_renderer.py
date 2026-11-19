"""FFmpeg 9:16 vertical renderer with HUD-preserving blur pad, ASS burn-in, ducking, and -14 LUFS loudnorm."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _escape_ffmpeg_filter_path(path: Path) -> str:
    """Escape a filesystem path for safe embedding inside an FFmpeg filtergraph argument."""
    raw = str(path.resolve())
    escaped = raw.replace("\\", "\\\\")
    for ch in (":", "'", ",", "[", "]", ";"):
        escaped = escaped.replace(ch, f"\\{ch}")
    return escaped


def _probe_media(path: Path) -> dict[str, Any]:
    """Inspect streams and duration of a media file using ffprobe."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=codec_type,width,height",
        "-of",
        "json",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not proc.stdout.strip():
        return {"has_audio": False, "duration": 0.0, "width": 0, "height": 0}

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"has_audio": False, "duration": 0.0, "width": 0, "height": 0}

    streams = data.get("streams", []) or []
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    width = 0
    height = 0
    for s in streams:
        if s.get("codec_type") == "video":
            width = int(s.get("width") or 0)
            height = int(s.get("height") or 0)
            break

    fmt_dur = data.get("format", {}).get("duration")
    try:
        duration = float(fmt_dur) if fmt_dur is not None else 0.0
    except (TypeError, ValueError):
        duration = 0.0

    return {
        "has_audio": has_audio,
        "duration": duration,
        "width": width,
        "height": height,
    }


def _extract_thumbnail(video_path: Path, thumbnail_path: Path, timestamp_sec: float = 0.8) -> Path | None:
    """Extract a 1080x1920 JPEG thumbnail from the rendered vertical MP4."""
    thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{max(0.0, timestamp_sec):.2f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-vf",
        "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
        "-q:v",
        "2",
        str(thumbnail_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode == 0 and thumbnail_path.exists() and thumbnail_path.stat().st_size > 0:
        return thumbnail_path
    logger.warning("Thumbnail extraction failed for %s: %s", video_path, proc.stderr.strip())
    return None


def _even(value: float) -> int:
    return max(2, int(value) // 2 * 2)


def render_vertical_short(
    source_video: Path,
    output_video: Path,
    ass_path: Path | None,
    voiceover_audio: Path | None,
    start_sec: float = 0.0,
    duration_sec: float = 15.0,
    layout_mode: str = "blur_pad_hud",
    crf: int = 20,
    loudnorm_lufs: float = -14.0,
    *,
    cam_box: tuple[float, float, float, float] | None = None,
    cam_side: str = "right",
    game_crop_x: int | None = None,
    hook_voiceover_only: bool = False,
    hook_vo_seconds: float = 2.5,
    seam_band_height: int = 0,
) -> dict[str, Any]:
    """Render a 1080x1920 30fps Short/Reel (blur_pad_hud | smart_crop | react | facecam), ASS overlays, and -14 LUFS audio."""
    if not source_video.exists():
        raise FileNotFoundError(f"Source video does not exist: {source_video}")

    output_video.parent.mkdir(parents=True, exist_ok=True)

    probe_in = _probe_media(source_video)
    source_has_audio = bool(probe_in["has_audio"])
    source_dur = float(probe_in["duration"] or 0.0)
    src_w = int(probe_in["width"] or 1920)
    src_h = int(probe_in["height"] or 1080)

    seek_start = max(0.0, float(start_sec))
    requested_dur = max(1.0, float(duration_sec))
    if source_dur > seek_start + 0.5:
        target_dur = min(requested_dur, source_dur - seek_start)
    else:
        target_dur = requested_dur

    vo_probe = _probe_media(voiceover_audio) if (voiceover_audio and voiceover_audio.exists()) else {}
    has_vo = (
        voiceover_audio is not None
        and voiceover_audio.exists()
        and voiceover_audio.stat().st_size > 0
        and bool(vo_probe.get("has_audio", False))
    )
    vo_dur = float(vo_probe.get("duration") or 0.0) if has_vo else 0.0

    filter_parts: list[str] = []

    # 1. Video layout pipeline (1080x1920 @ 30fps, yuv420p)
    mode = (layout_mode or "blur_pad_hud").strip().lower()
    if mode in ("react", "facecam") and cam_box is None:
        # Safe fallback when no webcam was detected
        mode = "blur_pad_hud"

    if mode == "react" and cam_box is not None:
        # Streamer React Split-Screen (ported from gtavi-claudecode): 730px cam top + 1190px gameplay bottom
        top_h = 730
        game_h = 1920 - top_h  # 1190
        cx, cy, cw_f, ch_f = cam_box
        crop_w = min(src_w, _even(max(64.0, src_w * cw_f)))
        crop_h = min(src_h, _even(max(64.0, src_h * ch_f)))
        crop_x = min(max(0, _even(src_w * cx)), max(0, src_w - crop_w))
        crop_y = min(max(0, _even(src_h * cy)), max(0, src_h - crop_h))

        scaled_gw = _even(src_w * game_h / max(1, src_h))
        if scaled_gw < 1080:
            scaled_gw = 1080
        max_gx = max(0, scaled_gw - 1080)
        if game_crop_x is not None:
            gx = min(max(0, _even(game_crop_x)), max_gx)
        else:
            gx = max_gx if cam_side == "left" else 0

        filter_parts.append("[0:v]split=2[vcam_in][vgame_in]")
        filter_parts.append(
            f"[vcam_in]crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
            f"scale=1080:{top_h}:force_original_aspect_ratio=increase,"
            f"crop=1080:{top_h},setsar=1[vcam]"
        )
        filter_parts.append(
            f"[vgame_in]scale={scaled_gw}:{game_h}:force_original_aspect_ratio=increase,"
            f"crop=1080:{game_h}:{gx}:0,setsar=1[vgame]"
        )
        if seam_band_height > 0:
            band_y = max(0, top_h - seam_band_height // 2)
            filter_parts.append(
                f"[vcam][vgame]vstack,"
                f"drawbox=x=0:y={band_y}:w=iw:h={int(seam_band_height)}:color=white@0.95:t=fill,"
                f"setsar=1,fps=30,format=yuv420p[vbase]"
            )
        else:
            filter_parts.append("[vcam][vgame]vstack,setsar=1,fps=30,format=yuv420p[vbase]")
    elif mode == "facecam" and cam_box is not None:
        # Classic 38% webcam top + 62% full-height cropped gameplay bottom
        top_h = _even(1920 * 0.38)  # 728
        game_h = 1920 - top_h  # 1192
        cx, cy, cw_f, ch_f = cam_box
        crop_w = min(src_w, _even(max(64.0, src_w * cw_f)))
        crop_h = min(src_h, _even(max(64.0, src_h * ch_f)))
        crop_x = min(max(0, _even(src_w * cx)), max(0, src_w - crop_w))
        crop_y = min(max(0, _even(src_h * cy)), max(0, src_h - crop_h))

        filter_parts.append("[0:v]split=2[vcam_in][vgame_in]")
        filter_parts.append(
            f"[vcam_in]crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
            f"scale=1080:{top_h}:force_original_aspect_ratio=increase,"
            f"crop=1080:{top_h},setsar=1[vcam]"
        )
        filter_parts.append(
            f"[vgame_in]scale=1080:{game_h}:force_original_aspect_ratio=increase,"
            f"crop=1080:{game_h},setsar=1[vgame]"
        )
        filter_parts.append("[vcam][vgame]vstack,setsar=1,fps=30,format=yuv420p[vbase]")
    elif mode == "smart_crop":
        # Center 9:16 crop (`scale=-2:1920,crop=1080:1920` generalized for any input aspect ratio)
        filter_parts.append(
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,setsar=1,fps=30,format=yuv420p[vbase]"
        )
    else:
        # `blur_pad_hud`: blurred 1080x1920 background + sharp 1080-wide foreground preserving GTA HUD/minimap
        filter_parts.append("[0:v]split=2[vbg_in][vfg_in]")
        filter_parts.append(
            "[vbg_in]scale=270:480:force_original_aspect_ratio=increase,"
            "crop=270:480,gblur=sigma=10,scale=1080:1920:flags=bilinear,eq=brightness=-0.06[vbg]"
        )
        filter_parts.append(
            "[vfg_in]scale=1080:1920:force_original_aspect_ratio=decrease:force_divisible_by=2[vfg]"
        )
        filter_parts.append(
            "[vbg][vfg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps=30,format=yuv420p[vbase]"
        )

    # 2. Optional .ass subtitle / hook / creator credit burn-in
    if ass_path is not None and ass_path.exists():
        escaped_ass = _escape_ffmpeg_filter_path(ass_path)
        filter_parts.append(f"[vbase]ass=filename='{escaped_ass}'[vout]")
    else:
        filter_parts.append("[vbase]null[vout]")

    # 3. Audio ducking, mixing, and EBU R128 loudness normalization (-14 LUFS, -1.5 dBTP, LRA 11)
    loudnorm_expr = f"loudnorm=I={float(loudnorm_lufs):.1f}:TP=-1.5:LRA=11"
    if source_has_audio and has_vo and hook_voiceover_only:
        # Ported from translate.py: duck game/streamer audio ONLY for the 2.5s hook, then restore 100% reaction audio!
        h_sec = max(1.0, min(float(hook_vo_seconds), target_dur))
        tempo_filter = (
            f"atempo={min(2.0, vo_dur / h_sec):.3f},"
            if vo_dur > h_sec + 0.05
            else ""
        )
        fade_st = max(0.0, h_sec - 0.15)
        filter_parts.append(
            f"[0:a]aresample=44100,aformat=sample_rates=44100:channel_layouts=stereo,"
            f"apad=whole_dur={target_dur:.3f},atrim=0:{target_dur:.3f},asetpts=PTS-STARTPTS,"
            f"volume=0.20:enable='lt(t,{h_sec:.2f})'[bg_a]"
        )
        filter_parts.append(
            f"[1:a]aresample=44100,aformat=sample_rates=44100:channel_layouts=stereo,"
            f"{tempo_filter}atrim=0:{h_sec:.3f},afade=t=out:st={fade_st:.2f}:d=0.15,"
            f"apad=whole_dur={target_dur:.3f},atrim=0:{target_dur:.3f},asetpts=PTS-STARTPTS,volume=1.0[vo_a]"
        )
        filter_parts.append(
            f"[bg_a][vo_a]amix=inputs=2:duration=first:dropout_transition=0.2:normalize=0,{loudnorm_expr}[aout]"
        )
    elif source_has_audio and has_vo:
        filter_parts.append(
            f"[0:a]aresample=44100,aformat=sample_rates=44100:channel_layouts=stereo,"
            f"apad=whole_dur={target_dur:.3f},atrim=0:{target_dur:.3f},asetpts=PTS-STARTPTS,volume=0.25[bg_a]"
        )
        filter_parts.append(
            f"[1:a]aresample=44100,aformat=sample_rates=44100:channel_layouts=stereo,"
            f"apad=whole_dur={target_dur:.3f},atrim=0:{target_dur:.3f},asetpts=PTS-STARTPTS,volume=1.0[vo_a]"
        )
        filter_parts.append(
            f"[bg_a][vo_a]amix=inputs=2:duration=first:dropout_transition=0.2:normalize=0,{loudnorm_expr}[aout]"
        )
    elif source_has_audio:
        filter_parts.append(
            f"[0:a]aresample=44100,aformat=sample_rates=44100:channel_layouts=stereo,"
            f"apad=whole_dur={target_dur:.3f},atrim=0:{target_dur:.3f},asetpts=PTS-STARTPTS,{loudnorm_expr}[aout]"
        )
    elif has_vo:
        filter_parts.append(
            f"[1:a]aresample=44100,aformat=sample_rates=44100:channel_layouts=stereo,"
            f"apad=whole_dur={target_dur:.3f},atrim=0:{target_dur:.3f},asetpts=PTS-STARTPTS,{loudnorm_expr}[aout]"
        )
    else:
        filter_parts.append(
            f"anullsrc=channel_layout=stereo:sample_rate=44100,atrim=0:{target_dur:.3f}[aout]"
        )

    filter_complex = ";".join(filter_parts)

    cmd: list[str] = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{seek_start:.3f}",
        "-t",
        f"{target_dur:.3f}",
        "-i",
        str(source_video),
    ]
    if has_vo and voiceover_audio is not None:
        cmd.extend(["-i", str(voiceover_audio)])

    cmd.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-t",
            f"{target_dur:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            str(int(crf)),
            "-pix_fmt",
            "yuv420p",
            "-r",
            "30",
            "-movflags",
            "+faststart",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "44100",
            "-ac",
            "2",
            str(output_video),
        ]
    )

    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not output_video.exists() or output_video.stat().st_size == 0:
        raise RuntimeError(f"FFmpeg vertical render failed for {source_video}: {proc.stderr.strip()}")

    probe_out = _probe_media(output_video)
    actual_duration = float(probe_out["duration"] or target_dur)
    width = int(probe_out["width"] or 1080)
    height = int(probe_out["height"] or 1920)

    thumb_ts = 0.8 if actual_duration >= 1.0 else max(0.0, actual_duration * 0.5)
    thumbnail_candidate = output_video.with_suffix(".jpg")
    thumbnail_path = _extract_thumbnail(output_video, thumbnail_candidate, timestamp_sec=thumb_ts)

    return {
        "video_path": str(output_video),
        "thumbnail_path": str(thumbnail_path) if thumbnail_path is not None else None,
        "ass_subtitle_path": str(ass_path) if ass_path is not None and ass_path.exists() else None,
        "audio_path": str(voiceover_audio) if has_vo and voiceover_audio is not None else None,
        "duration_sec": round(actual_duration, 2),
        "width": width,
        "height": height,
        "fps": 30,
        "layout_mode": mode,
        "loudnorm_lufs": float(loudnorm_lufs),
    }
