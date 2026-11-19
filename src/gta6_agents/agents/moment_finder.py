"""Multi-signal viral moment finder for GTA6 videos and live streams using pure Python stdlib."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import statistics
import subprocess
from array import array
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from gta6_agents.video.downloader import _resolve_ytdlp_cmd, _run_subprocess

logger = logging.getLogger(__name__)


@dataclass
class ViralMoment:
    rank: int
    start_sec: float
    end_sec: float
    peak_sec: float
    score: float
    reasons: list[str] = field(default_factory=list)
    transcript_excerpt: str = ""
    heatmap_value: float = 0.0


HYPE_WORDS_RE = re.compile(
    r"\b("
    r"meu deus|caralho|insano|absurdo|não acredito|nao acredito|olha isso|que isso|"
    r"clipa|capotou|explodiu|socorro|"
    r"no way|oh my god|omg|let's go|holy|insane|crazy|wtf"
    r")\b",
    re.IGNORECASE,
)

CHAT_HYPE_RE = re.compile(
    r"(?:\b(?:CLIPA+|CLIPOU|K{4,}|CINEMA|LMAO|KEKW|OMEGALUL|POG|W+)\b|[🔥💀😂])",
    re.IGNORECASE,
)


def _moving_avg(vals: list[float], window: int) -> list[float]:
    if not vals:
        return []
    w = max(1, int(window))
    n = len(vals)
    res: list[float] = []
    current_sum = 0.0
    for i in range(n):
        current_sum += vals[i]
        if i >= w:
            current_sum -= vals[i - w]
            res.append(current_sum / w)
        else:
            res.append(current_sum / (i + 1))
    return res


def _zscore(vals: list[float]) -> list[float]:
    if not vals:
        return []
    try:
        med = statistics.median(vals)
        # Median absolute deviation (MAD) for robust z-scoring
        mad = statistics.median([abs(v - med) for v in vals])
        std = mad * 1.4826
    except Exception:
        std = 0.0

    if std < 1e-6:
        try:
            mean_val = statistics.mean(vals)
            std = statistics.stdev(vals) if len(vals) > 1 else 0.0
        except Exception:
            mean_val = 0.0
            std = 0.0

    if std < 1e-6:
        return [0.0 for _ in vals]

    try:
        mean_val = statistics.mean(vals)
    except Exception:
        mean_val = 0.0

    return [(v - mean_val) / std for v in vals]


def extract_heatmap_moments(
    heatmap: list[dict[str, Any]],
    duration_sec: float,
    *,
    top_k: int = 8,
    min_sec: float = 15.0,
    max_sec: float = 32.0,
    intro_skip_sec: float = 45.0,
) -> list[ViralMoment]:
    """Select top non-overlapping viral peaks from YouTube Most Replayed heatmap data."""
    if not heatmap or duration_sec <= 0:
        return []

    valid_points: list[dict[str, Any]] = []
    for item in heatmap:
        if not isinstance(item, dict):
            continue
        start = float(item.get("start_time", 0.0))
        end = float(item.get("end_time", start + 5.0))
        val = float(item.get("value", 0.0))
        peak = (start + end) / 2.0

        if duration_sec > intro_skip_sec + 30.0 and peak < intro_skip_sec:
            continue
        if val <= 0.0:
            continue
        valid_points.append({"start": start, "end": end, "peak": peak, "value": val})

    if not valid_points:
        return []

    # Sort descending by heatmap intensity value
    valid_points.sort(key=lambda x: x["value"], reverse=True)

    selected: list[dict[str, Any]] = []
    target_duration = max(min_sec, min(max_sec, 25.0))
    # Leading setup offset: -0.45 * target_duration before peak
    setup_offset = 0.45 * target_duration

    for pt in valid_points:
        if len(selected) >= top_k:
            break
        peak_t = pt["peak"]
        start_t = max(0.0, peak_t - setup_offset)
        end_t = min(duration_sec, start_t + target_duration)
        if end_t - start_t < min_sec:
            end_t = min(duration_sec, start_t + min_sec)

        # Check overlap with already selected moments
        overlap = False
        for s in selected:
            if not (end_t <= s["start_t"] or start_t >= s["end_t"]):
                overlap = True
                break
        if not overlap:
            selected.append(
                {
                    "start_t": start_t,
                    "end_t": end_t,
                    "peak_t": peak_t,
                    "value": pt["value"],
                }
            )

    selected.sort(key=lambda x: x["start_t"])
    moments: list[ViralMoment] = []
    for idx, item in enumerate(selected, start=1):
        val = item["value"]
        moments.append(
            ViralMoment(
                rank=idx,
                start_sec=round(item["start_t"], 2),
                end_sec=round(item["end_t"], 2),
                peak_sec=round(item["peak_t"], 2),
                score=round(float(val * 10.0), 2),
                reasons=[f"most replayed {val:.2f}"],
                heatmap_value=round(val, 3),
            )
        )
    return moments


async def fetch_youtube_heatmap_moments(
    url: str,
    *,
    top_k: int = 8,
    min_sec: float = 15.0,
    max_sec: float = 32.0,
    timeout_sec: float = 15.0,
) -> tuple[list[ViralMoment], dict[str, Any]]:
    """Fetch YouTube metadata and extract Most Replayed heatmap moments."""
    clean_url = (url or "").strip().split("#", 1)[0]
    if not clean_url:
        return [], {}

    cmd = [
        *_resolve_ytdlp_cmd(),
        "--no-playlist",
        "--no-warnings",
        "--skip-download",
        "--dump-single-json",
        clean_url,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        if proc.returncode != 0 or not stdout_b.strip():
            return [], {}
        info = json.loads(stdout_b.decode("utf-8", errors="replace").strip().splitlines()[-1])
        if not isinstance(info, dict):
            return [], {}

        duration = float(info.get("duration") or 0.0)
        heatmap = info.get("heatmap")
        if not isinstance(heatmap, list):
            heatmap = []

        moments = extract_heatmap_moments(
            heatmap,
            duration,
            top_k=top_k,
            min_sec=min_sec,
            max_sec=max_sec,
        )
        return moments, info
    except Exception as exc:
        logger.debug("fetch_youtube_heatmap_moments failed for %s: %s", clean_url, exc)
        return [], {}


def compute_audio_rms_db_series(video_path: Path) -> list[float]:
    """Compute 1-second RMS dB series from a local video file using ffmpeg and pure Python."""
    p = Path(video_path)
    if not p.exists():
        return []

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(p),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "s16le",
        "pipe:1",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, check=False)
        if proc.returncode != 0 or not proc.stdout:
            return []
        raw_bytes = proc.stdout
    except Exception:
        return []

    samples = array("h")
    try:
        samples.frombytes(raw_bytes)
    except Exception:
        try:
            samples.frombytes(raw_bytes[: len(raw_bytes) - (len(raw_bytes) % 2)])
        except Exception:
            return []

    sample_rate = 16000
    chunk_size = sample_rate  # 1 second chunks
    db_series: list[float] = []

    for i in range(0, len(samples), chunk_size):
        chunk = samples[i : i + chunk_size]
        if len(chunk) < chunk_size // 2:
            break
        sum_sq = sum(float(s) * float(s) for s in chunk)
        rms = math.sqrt(sum_sq / len(chunk)) / 32768.0
        db = 20.0 * math.log10(rms + 1e-9)
        db_series.append(round(db, 2))

    return db_series


def score_multimodal_moments(
    duration_sec: float,
    *,
    audio_db: list[float] | None = None,
    heatmap: list[dict[str, Any]] | None = None,
    words: list[dict[str, Any]] | None = None,
    chat_counts: list[float] | None = None,
    top_k: int = 8,
    min_sec: float = 15.0,
    max_sec: float = 35.0,
) -> list[ViralMoment]:
    """Combine multimodal signals (audio loudness, hype words, YouTube heatmap, chat activity) into ranked viral moments."""
    dur = max(5.0, float(duration_sec))
    step = 1.0
    num_bins = int(math.ceil(dur / step))

    # Build aligned time series for each signal across num_bins
    audio_series = [0.0] * num_bins
    if audio_db:
        for i, val in enumerate(audio_db[:num_bins]):
            audio_series[i] = val

    heatmap_series = [0.0] * num_bins
    if heatmap:
        for hm in heatmap:
            if not isinstance(hm, dict):
                continue
            s_t = float(hm.get("start_time", 0.0))
            e_t = float(hm.get("end_time", s_t + 5.0))
            val = float(hm.get("value", 0.0))
            bi_start = max(0, int(s_t // step))
            bi_end = min(num_bins, int(math.ceil(e_t / step)))
            for b in range(bi_start, bi_end):
                heatmap_series[b] = max(heatmap_series[b], val)

    hype_series = [0.0] * num_bins
    if words:
        for w in words:
            if not isinstance(w, dict):
                continue
            t = float(w.get("start", w.get("start_sec", 0.0)))
            txt = str(w.get("text", w.get("word", "")))
            if HYPE_WORDS_RE.search(txt):
                b = int(t // step)
                if 0 <= b < num_bins:
                    hype_series[b] += 1.0

    chat_series = [0.0] * num_bins
    if chat_counts:
        for i, c in enumerate(chat_counts[:num_bins]):
            chat_series[i] = float(c)

    # Normalize via zscore
    loud_z = _zscore(audio_series)
    hype_z = _zscore(hype_series)
    heat_z = _zscore(heatmap_series)
    chat_z = _zscore(chat_series)

    # Combined composite score per bin: 1.0 * loud + 0.8 * hype + 2.0 * heat + 1.5 * chat
    composite = [
        1.0 * loud_z[i] + 0.8 * hype_z[i] + 2.0 * heat_z[i] + 1.5 * chat_z[i]
        for i in range(num_bins)
    ]

    # Find local peaks in composite score
    candidate_bins: list[tuple[int, float]] = []
    for i in range(1, num_bins - 1):
        if composite[i] > composite[i - 1] and composite[i] >= composite[i + 1] and composite[i] > 0.0:
            candidate_bins.append((i, composite[i]))

    if not candidate_bins and num_bins > 0:
        # Fallback to top bins by composite score
        indexed = sorted(enumerate(composite), key=lambda x: x[1], reverse=True)
        candidate_bins = [(idx, val) for idx, val in indexed[: top_k * 2]]

    candidate_bins.sort(key=lambda x: x[1], reverse=True)

    target_duration = max(min_sec, min(max_sec, 25.0))
    selected_moments: list[dict[str, Any]] = []

    for bin_idx, score_val in candidate_bins:
        if len(selected_moments) >= top_k:
            break
        peak_t = float(bin_idx) * step
        start_t = max(0.0, peak_t - (0.45 * target_duration))
        end_t = min(dur, start_t + target_duration)
        if end_t - start_t < min_sec:
            end_t = min(dur, start_t + min_sec)

        # Snap start/end to word boundaries if words provided (-0.3s start, +0.6s end)
        if words:
            best_start_word = start_t
            best_end_word = end_t
            for w in words:
                w_start = float(w.get("start", w.get("start_sec", 0.0)))
                w_end = float(w.get("end", w.get("end_sec", w_start + 0.5)))
                if abs(w_start - start_t) < 3.0:
                    best_start_word = min(best_start_word, max(0.0, w_start - 0.3))
                if abs(w_end - end_t) < 3.0:
                    best_end_word = max(best_end_word, min(dur, w_end + 0.6))
            if best_end_word > best_start_word and (best_end_word - best_start_word) <= max_sec + 5.0:
                start_t = best_start_word
                end_t = best_end_word

        # Check overlap
        overlap = False
        for sm in selected_moments:
            if not (end_t <= sm["start_t"] or start_t >= sm["end_t"]):
                overlap = True
                break
        if not overlap:
            reasons = []
            if heat_z[bin_idx] > 0.5:
                reasons.append(f"most replayed {heatmap_series[bin_idx]:.2f}")
            if loud_z[bin_idx] > 0.8:
                reasons.append("loudness spike")
            if hype_z[bin_idx] > 0.5:
                reasons.append("hype words")
            if chat_z[bin_idx] > 0.5:
                reasons.append("chat spike")
            if not reasons:
                reasons.append("composite viral peak")

            selected_moments.append(
                {
                    "start_t": start_t,
                    "end_t": end_t,
                    "peak_t": peak_t,
                    "score": score_val,
                    "reasons": reasons,
                    "heatmap_value": heatmap_series[bin_idx],
                }
            )

    selected_moments.sort(key=lambda x: x["start_t"])
    result: list[ViralMoment] = []
    for idx, sm in enumerate(selected_moments, start=1):
        result.append(
            ViralMoment(
                rank=idx,
                start_sec=round(sm["start_t"], 2),
                end_sec=round(sm["end_t"], 2),
                peak_sec=round(sm["peak_t"], 2),
                score=round(float(sm["score"] + 10.0), 2),
                reasons=sm["reasons"],
                heatmap_value=round(sm["heatmap_value"], 3),
            )
        )
    return result
