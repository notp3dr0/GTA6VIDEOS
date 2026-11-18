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
) -> dict[str, Any]:
    """Render a 1080x1920 30fps Short/Reel with HUD blur-pad or smart crop, ASS overlays, and -14 LUFS audio."""
    if not source_video.exists():
        raise FileNotFoundError(f"Source video does not exist: {source_video}")

    output_video.parent.mkdir(parents=True, exist_ok=True)

    probe_in = _probe_media(source_video)
    source_has_audio = bool(probe_in["has_audio"])
    source_dur = float(probe_in["duration"] or 0.0)

    seek_start = max(0.0, float(start_sec))
    requested_dur = max(1.0, float(duration_sec))
    if source_dur > seek_start + 0.5:
        target_dur = min(requested_dur, source_dur - seek_start)
    else:
        target_dur = requested_dur

    has_vo = (
        voiceover_audio is not None
        and voiceover_audio.exists()
        and voiceover_audio.stat().st_size > 0
        and _probe_media(voiceover_audio)["has_audio"]
    )

    filter_parts: list[str] = []

    # 1. Video layout pipeline (1080x1920 @ 30fps, yuv420p)
    mode = (layout_mode or "blur_pad_hud").strip().lower()
    if mode == "smart_crop":
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
    if source_has_audio and has_vo:
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
