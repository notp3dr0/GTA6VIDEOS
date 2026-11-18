"""Video segment downloader, YouTube transcript extractor, and offline synthetic clip generator."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from gta6_agents.models import SubtitleCue

logger = logging.getLogger(__name__)

# Cap downloads at 1080p MP4 (skip 4K) to keep rendering and Shorts/Reels uploads fast.
YTDLP_1080P_FORMAT = (
    "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
    "bestvideo[height<=1080]+bestaudio/"
    "best[height<=1080][ext=mp4]/"
    "best[height<=1080]/best"
)

_YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def extract_youtube_video_id(url: str) -> str | None:
    """Extract the 11-character YouTube video ID from standard, Shorts, live, or short URLs."""
    raw = (url or "").strip()
    if not raw:
        return None
    if _YT_ID_RE.match(raw):
        return raw

    try:
        parsed = urlparse(raw)
    except ValueError:
        return None

    host = (parsed.hostname or "").lower()
    path_parts = [p for p in parsed.path.split("/") if p]

    if host in ("youtu.be", "www.youtu.be") and path_parts:
        candidate = path_parts[0]
        return candidate if _YT_ID_RE.match(candidate) else None

    if "youtube.com" in host or "youtube-nocookie.com" in host:
        if parsed.path == "/watch":
            v_list = parse_qs(parsed.query).get("v", [])
            if v_list and _YT_ID_RE.match(v_list[0]):
                return v_list[0]
        if len(path_parts) >= 2 and path_parts[0] in ("shorts", "embed", "live", "v"):
            candidate = path_parts[1]
            if _YT_ID_RE.match(candidate):
                return candidate

    return None


def _trim_local_video(source_path: Path, output_path: Path, start_sec: float, duration_sec: float) -> Path:
    """Trim a local video file cleanly to [start_sec, start_sec + duration_sec] via FFmpeg."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{max(0.0, start_sec):.3f}",
        "-i",
        str(source_path),
        "-t",
        f"{max(1.0, duration_sec):.3f}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"FFmpeg trim failed for {source_path}: {proc.stderr.strip()}")
    return output_path


def _resolve_ytdlp_cmd() -> list[str]:
    """Return command prefix to invoke yt-dlp from current Python environment or PATH."""
    venv_bin = Path(sys.executable).parent / "yt-dlp"
    if venv_bin.exists():
        return [str(venv_bin)]
    which_bin = shutil.which("yt-dlp")
    if which_bin:
        return [which_bin]
    return [sys.executable, "-m", "yt_dlp"]


async def _run_subprocess(cmd: list[str], timeout_sec: float = 180.0) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.communicate()
        raise RuntimeError(f"Command timed out after {timeout_sec}s: {' '.join(cmd[:4])}") from exc
    return (
        proc.returncode or 0,
        stdout_b.decode("utf-8", errors="replace"),
        stderr_b.decode("utf-8", errors="replace"),
    )


async def download_video_segment(
    url: str,
    start_sec: float,
    end_sec: float,
    work_dir: Path,
) -> Path:
    """Download a 1080p MP4 segment [start_sec, end_sec] using yt-dlp (with FFmpeg post-trim fallback)."""
    raw_url = (url or "").strip()
    if not raw_url:
        raise ValueError("Source video URL cannot be empty.")

    start = max(0.0, float(start_sec))
    end = max(start + 1.0, float(end_sec))
    duration = end - start

    work_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(f"{raw_url}|{start:.2f}|{end:.2f}".encode("utf-8")).hexdigest()[:12]
    output_path = work_dir / f"segment_{digest}_{int(start)}_{int(end)}.mp4"

    if output_path.exists() and output_path.stat().st_size > 1024:
        return output_path

    # Support local file paths or file:// URLs directly.
    local_candidate: Path | None = None
    if raw_url.startswith("file://"):
        local_candidate = Path(urlparse(raw_url).path)
    elif "://" not in raw_url:
        p = Path(raw_url)
        if p.exists() and p.is_file():
            local_candidate = p

    if local_candidate is not None:
        if not local_candidate.exists():
            raise FileNotFoundError(f"Local source video does not exist: {local_candidate}")
        return await asyncio.to_thread(_trim_local_video, local_candidate, output_path, start, duration)

    if raw_url.startswith(("synthetic://", "demo://", "mock://")):
        raise RuntimeError(f"Synthetic scheme URL cannot be downloaded remotely: {raw_url}")

    ytdlp_prefix = _resolve_ytdlp_cmd()
    temp_section_template = str(work_dir / f"raw_sec_{digest}.%(ext)s")
    section_spec = f"*{start:.2f}-{end:.2f}"

    section_cmd = [
        *ytdlp_prefix,
        "--no-playlist",
        "--no-warnings",
        "--socket-timeout",
        "10",
        "--retries",
        "1",
        "-f",
        YTDLP_1080P_FORMAT,
        "--merge-output-format",
        "mp4",
        "--download-sections",
        section_spec,
        "--force-keyframes-at-cuts",
        "-o",
        temp_section_template,
        raw_url,
    ]

    rc, _, err = await _run_subprocess(section_cmd, timeout_sec=120.0)
    downloaded_sec = work_dir / f"raw_sec_{digest}.mp4"
    if rc == 0 and downloaded_sec.exists() and downloaded_sec.stat().st_size > 1024:
        downloaded_sec.replace(output_path)
        return output_path

    logger.debug("yt-dlp --download-sections failed (%s); trying full <=1080p download + FFmpeg trim.", err.strip())

    # Fallback: download full <=1080p video and trim locally with FFmpeg.
    temp_full_template = str(work_dir / f"raw_full_{digest}.%(ext)s")
    full_cmd = [
        *ytdlp_prefix,
        "--no-playlist",
        "--no-warnings",
        "--socket-timeout",
        "10",
        "--retries",
        "1",
        "-f",
        YTDLP_1080P_FORMAT,
        "--merge-output-format",
        "mp4",
        "-o",
        temp_full_template,
        raw_url,
    ]
    rc_full, _, err_full = await _run_subprocess(full_cmd, timeout_sec=180.0)
    downloaded_full = work_dir / f"raw_full_{digest}.mp4"
    if rc_full != 0 or not downloaded_full.exists() or downloaded_full.stat().st_size == 0:
        raise RuntimeError(
            f"yt-dlp failed to download {raw_url}: {(err_full or err).strip() or f'exit code {rc_full}'}"
        )

    try:
        return await asyncio.to_thread(_trim_local_video, downloaded_full, output_path, start, duration)
    finally:
        downloaded_full.unlink(missing_ok=True)


def _expand_language_preferences(languages: list[str]) -> list[str]:
    expanded: list[str] = []
    for lang in languages:
        code = lang.strip()
        if not code:
            continue
        if code.lower() == "pt":
            candidates = ["pt-BR", "pt", "pt-PT"]
        elif code.lower() == "en":
            candidates = ["en", "en-US", "en-GB"]
        else:
            base = code.split("-")[0]
            candidates = [code, base]
        for c in candidates:
            if c not in expanded:
                expanded.append(c)
    return expanded or ["pt-BR", "pt", "en"]


def _split_cue_into_short_chunks(start_sec: float, end_sec: float, text: str, max_words: int = 5) -> list[SubtitleCue]:
    words = text.split()
    if not words:
        return []
    if len(words) <= max_words:
        return [SubtitleCue(start_sec=round(start_sec, 2), end_sec=round(end_sec, 2), text=" ".join(words))]

    chunks: list[list[str]] = []
    idx = 0
    while idx < len(words):
        remaining = len(words) - idx
        if remaining <= max_words + 1 and remaining <= 6:
            if remaining == 6:
                chunks.append(words[idx : idx + 3])
                chunks.append(words[idx + 3 : idx + 6])
            else:
                chunks.append(words[idx:])
            break
        chunks.append(words[idx : idx + 4])
        idx += 4

    total_chars = max(1, sum(len(" ".join(c)) for c in chunks))
    span = max(0.2, end_sec - start_sec)
    cursor = start_sec
    cues: list[SubtitleCue] = []
    for i, chunk_words in enumerate(chunks):
        chunk_str = " ".join(chunk_words)
        if i == len(chunks) - 1:
            cue_end = end_sec
        else:
            weight = len(chunk_str) / total_chars
            cue_end = min(end_sec - 0.05, cursor + max(0.15, span * weight))
        if cue_end > cursor:
            cues.append(
                SubtitleCue(
                    start_sec=round(cursor, 2),
                    end_sec=round(cue_end, 2),
                    text=chunk_str,
                )
            )
            cursor = cue_end
    return cues


def extract_youtube_subtitles(
    url: str,
    start_sec: float,
    end_sec: float,
    languages: list[str] = ["pt", "en"],
) -> list[SubtitleCue]:
    """Fetch YouTube transcript cues within [start_sec, end_sec] (relative to 0.0s), falling back to []."""
    video_id = extract_youtube_video_id(url)
    if not video_id:
        return []

    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except Exception:
        return []

    lang_codes = _expand_language_preferences(list(languages))
    raw_entries: list[dict[str, float | str]] = []

    try:
        if hasattr(YouTubeTranscriptApi, "fetch"):
            api = YouTubeTranscriptApi()
            fetched = api.fetch(video_id, languages=lang_codes)
            for item in fetched:
                if isinstance(item, dict):
                    raw_entries.append(item)
                else:
                    raw_entries.append(
                        {
                            "text": str(getattr(item, "text", "")),
                            "start": float(getattr(item, "start", 0.0)),
                            "duration": float(getattr(item, "duration", 2.0)),
                        }
                    )
        elif hasattr(YouTubeTranscriptApi, "get_transcript"):
            raw_entries = YouTubeTranscriptApi.get_transcript(video_id, languages=lang_codes)  # type: ignore[attr-defined]
    except Exception as exc:
        logger.debug("Could not retrieve YouTube transcript for %s: %s", video_id, exc)
        return []

    clip_start = max(0.0, float(start_sec))
    clip_end = max(clip_start + 0.5, float(end_sec))
    clip_duration = clip_end - clip_start

    cues: list[SubtitleCue] = []
    for entry in raw_entries:
        text = " ".join(str(entry.get("text", "")).replace("\n", " ").split())
        if not text or re.fullmatch(r"\[[^\]]+\]", text):
            continue
        seg_start = float(entry.get("start", 0.0))
        seg_dur = max(0.25, float(entry.get("duration", 2.0)))
        seg_end = seg_start + seg_dur

        if seg_end <= clip_start or seg_start >= clip_end:
            continue

        rel_start = max(0.0, seg_start - clip_start)
        rel_end = min(clip_duration, seg_end - clip_start)
        if rel_end - rel_start < 0.15:
            continue

        cues.extend(_split_cue_into_short_chunks(rel_start, rel_end, text))

    return cues


def generate_synthetic_source_video(output_path: Path, duration_sec: float = 12.0) -> Path:
    """Generate a clean 16:9 1080p synthetic MP4 with 440Hz sine audio for fast offline tests & dry runs."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration = max(1.0, float(duration_sec))

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=1920x1080:rate=30",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=44100",
        "-t",
        f"{duration:.2f}",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        "-shortest",
        str(output_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"Failed to generate synthetic source video at {output_path}: {proc.stderr.strip()}")
    return output_path
