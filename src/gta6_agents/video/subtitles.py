"""Broadcast-grade ASS (Advanced SubStation Alpha) 1080x1920 overlay & dynamic caption builder."""

from __future__ import annotations

from pathlib import Path

from gta6_agents.models import SubtitleCue


def format_ass_timestamp(seconds: float) -> str:
    """Format seconds into ASS timestamp format H:MM:SS.cc (centiseconds)."""
    clamped = max(0.0, float(seconds))
    total_cs = int(round(clamped * 100))
    hours = total_cs // 360000
    rem = total_cs % 360000
    minutes = rem // 6000
    rem = rem % 6000
    secs = rem // 100
    cs = rem % 100
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def _escape_ass_text(text: str) -> str:
    """Escape literal braces and newlines so arbitrary text cannot break ASS override tags."""
    cleaned = (text or "").strip()
    cleaned = cleaned.replace("{", "(").replace("}", ")")
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    lines = [" ".join(line.split()) for line in cleaned.split("\n") if line.strip()]
    return r"\N".join(lines)


def _group_words_into_chunks(words: list[str], target_size: int = 4, max_size: int = 6) -> list[str]:
    """Group words into punchy 3-6 word chunks suitable for mobile vertical viewing."""
    if not words:
        return []
    if len(words) <= max_size:
        if len(words) == 6:
            return [" ".join(words[:3]), " ".join(words[3:])]
        return [" ".join(words)]

    chunks: list[list[str]] = []
    idx = 0
    total = len(words)
    while idx < total:
        rem = total - idx
        if rem <= max_size:
            if rem <= 2 and chunks and len(chunks[-1]) + rem <= max_size:
                chunks[-1].extend(words[idx:])
            elif rem == 6:
                chunks.append(words[idx : idx + 3])
                chunks.append(words[idx + 3 : idx + 6])
            else:
                chunks.append(words[idx:])
            break
        chunks.append(words[idx : idx + target_size])
        idx += target_size

    return [" ".join(c) for c in chunks if c]


def cues_from_script(script_text: str, total_duration_sec: float) -> list[SubtitleCue]:
    """Split a narration or commentary script into timed 3-6 word SubtitleCue chunks."""
    cleaned = " ".join((script_text or "").split())
    if not cleaned:
        return []

    duration = max(1.0, float(total_duration_sec))
    words = cleaned.split()
    chunk_texts = _group_words_into_chunks(words, target_size=4, max_size=6)
    if not chunk_texts:
        return []

    # Leave a small tail buffer so the final caption finishes cleanly right as the clip ends.
    usable_duration = max(0.6, duration - 0.15) if duration > 1.5 else duration
    weights = [max(3, len(chunk)) for chunk in chunk_texts]
    total_weight = sum(weights)

    cues: list[SubtitleCue] = []
    cursor = 0.0
    for idx, (chunk, weight) in enumerate(zip(chunk_texts, weights)):
        if idx == len(chunk_texts) - 1:
            end_t = usable_duration
        else:
            share = usable_duration * (weight / total_weight)
            end_t = min(usable_duration - 0.05 * (len(chunk_texts) - 1 - idx), cursor + max(0.25, share))
        if end_t <= cursor:
            end_t = min(duration, cursor + 0.25)
        cues.append(
            SubtitleCue(
                start_sec=round(cursor, 2),
                end_sec=round(end_t, 2),
                text=chunk,
            )
        )
        cursor = end_t

    return cues


def _normalize_and_chunk_cues(cues: list[SubtitleCue], total_duration_sec: float) -> list[SubtitleCue]:
    """Ensure cues fit [0, total_duration_sec] and split long cues into 3-5 word dynamic chunks."""
    if not cues:
        return []

    duration = max(1.0, float(total_duration_sec))
    # If caller passed unshifted timestamps (all >= duration), shift relative to the earliest cue.
    min_start = min(c.start_sec for c in cues)
    offset = min_start if min_start >= duration else 0.0

    normalized: list[SubtitleCue] = []
    for cue in cues:
        raw_text = " ".join((cue.text or "").split())
        if not raw_text:
            continue
        start = max(0.0, float(cue.start_sec) - offset)
        end = min(duration, float(cue.end_sec) - offset)
        if start >= duration:
            continue
        if end <= start:
            end = min(duration, start + 1.2)
        if end - start < 0.12:
            continue

        words = raw_text.split()
        if len(words) <= 5:
            normalized.append(SubtitleCue(start_sec=round(start, 2), end_sec=round(end, 2), text=raw_text))
            continue

        sub_chunks = _group_words_into_chunks(words, target_size=4, max_size=5)
        span = end - start
        total_chars = max(1, sum(len(s) for s in sub_chunks))
        sub_cursor = start
        for idx, sub_text in enumerate(sub_chunks):
            if idx == len(sub_chunks) - 1:
                sub_end = end
            else:
                sub_end = min(end - 0.04, sub_cursor + max(0.15, span * (len(sub_text) / total_chars)))
            if sub_end > sub_cursor:
                normalized.append(
                    SubtitleCue(
                        start_sec=round(sub_cursor, 2),
                        end_sec=round(sub_end, 2),
                        text=sub_text,
                    )
                )
                sub_cursor = sub_end

    return normalized


def build_ass_subtitles(
    cues: list[SubtitleCue],
    hook_text: str,
    credit_text: str,
    output_ass_path: Path,
    hook_duration_sec: float = 2.5,
    total_duration_sec: float = 15.0,
) -> Path:
    """Generate a 1080x1920 .ass file with HookBanner, CaptionCenter, and CreditTag styles."""
    output_ass_path.parent.mkdir(parents=True, exist_ok=True)
    clip_dur = max(1.0, float(total_duration_sec))
    hook_dur = max(0.5, min(float(hook_duration_sec), clip_dur))

    # ASS colors use &HAABBGGRR (00 = fully opaque, FF = fully transparent):
    # - &H0000EAFF -> Vibrant Yellow (#FFEA00) for top hook
    # - &H00FFFFFF -> Crisp White (#FFFFFF) for center captions
    # - &H18FFFFFF -> Subtle high-legibility white for creator attribution
    # - &H28000000 -> Dark semi-opaque banner fill for HookBanner (BorderStyle=3)
    header = "\n".join(
        [
            "[Script Info]",
            "Title: GTA 6 Fast Clips 9x16 Broadcast Overlay",
            "ScriptType: v4.00+",
            "WrapStyle: 0",
            "ScaledBorderAndShadow: yes",
            "YCbCr Matrix: TV.709",
            "PlayResX: 1080",
            "PlayResY: 1920",
            "",
            "[V4+ Styles]",
            (
                "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
                "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
                "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
                "Alignment, MarginL, MarginR, MarginV, Encoding"
            ),
            (
                "Style: HookBanner,DejaVu Sans,52,&H0000EAFF,&H00FFFFFF,"
                "&H28000000,&H80000000,-1,0,0,0,100,100,0,0,3,14,0,8,70,70,240,1"
            ),
            (
                "Style: CaptionCenter,DejaVu Sans,62,&H00FFFFFF,&H0000EAFF,"
                "&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,70,70,480,1"
            ),
            (
                "Style: CreditTag,DejaVu Sans,34,&H18FFFFFF,&H00FFFFFF,"
                "&H40000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,1,1,60,60,380,1"
            ),
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
    )

    events: list[str] = []

    escaped_credit = _escape_ass_text(credit_text)
    if escaped_credit:
        events.append(
            f"Dialogue: 1,{format_ass_timestamp(0.0)},{format_ass_timestamp(clip_dur)},"
            f"CreditTag,,0,0,0,,{escaped_credit}"
        )

    escaped_hook = _escape_ass_text(hook_text)
    if escaped_hook:
        events.append(
            f"Dialogue: 3,{format_ass_timestamp(0.0)},{format_ass_timestamp(hook_dur)},"
            f"HookBanner,,0,0,0,,{escaped_hook}"
        )

    prepared_cues = _normalize_and_chunk_cues(cues, total_duration_sec=clip_dur)
    for cue in prepared_cues:
        escaped_cue = _escape_ass_text(cue.text)
        if not escaped_cue:
            continue
        events.append(
            f"Dialogue: 2,{format_ass_timestamp(cue.start_sec)},{format_ass_timestamp(cue.end_sec)},"
            f"CaptionCenter,,0,0,0,,{escaped_cue}"
        )

    ass_content = header + "\n" + "\n".join(events) + "\n"
    output_ass_path.write_text(ass_content, encoding="utf-8")
    return output_ass_path
