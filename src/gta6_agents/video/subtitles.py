"""Broadcast-grade ASS (Advanced SubStation Alpha) 1080x1920 overlay & dynamic caption builder."""

from __future__ import annotations

import re
from pathlib import Path

from gta6_agents.models import SubtitleCue


SLURS_PATTERNS = [
    r"\bnigg\w*\b",
    r"\bfagg?\w*\b",
    r"\bretard\w*\b",
    r"\btranny\w*\b",
    r"\bviad(?:o|inho|ão)\b",
]

SWEARS_PATTERNS = [
    r"\bfuck(?:ing|ed|ers|er)?\b",
    r"\bshit(?:ty|bag|s)?\b",
    r"\bbitch(?:es)?\b",
    r"\basshole\b",
    r"\bcock\b",
    r"\bpussy\b",
    r"\bdick\b",
    r"\bwhore\b",
    r"\bslut\b",
    r"\bcaralho\b",
    r"\bporra\b",
    r"\bfoda(?:-se)?\b",
    r"\bputa(?:o|ria)?\b",
    r"\bmerda\b",
    r"\bcuz[aã]o\b",
    r"\btnc\b",
    r"\bpqp\b",
]

COMPILED_SLURS_RE = re.compile("|".join(SLURS_PATTERNS), re.IGNORECASE)
COMPILED_PROFANITY_RE = re.compile("|".join(SWEARS_PATTERNS), re.IGNORECASE)


def mask_profanity(text: str, allow_profanity: bool = False) -> str:
    """Always mask slurs; also mask heavy EN & PT-BR swears unless allow_profanity=True."""
    if not text:
        return text

    def _mask_match(match: re.Match[str]) -> str:
        word = match.group(0)
        if len(word) <= 1:
            return "*"
        return word[0] + "*" * (len(word) - 1)

    cleaned = COMPILED_SLURS_RE.sub(_mask_match, text)
    return cleaned if allow_profanity else COMPILED_PROFANITY_RE.sub(_mask_match, cleaned)


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


def _group_words_into_chunks(words: list[str], target_size: int = 3, max_size: int = 4) -> list[str]:
    """Group words into punchy 2-3 word phrases suitable for mobile vertical viewing."""
    if not words:
        return []
    if len(words) <= max_size:
        return [" ".join(words)]

    chunks: list[list[str]] = []
    idx = 0
    total = len(words)
    while idx < total:
        rem = total - idx
        if rem <= max_size:
            chunks.append(words[idx:])
            break
        chunks.append(words[idx : idx + target_size])
        idx += target_size

    return [" ".join(c) for c in chunks if c]


def cues_from_script(script_text: str, total_duration_sec: float) -> list[SubtitleCue]:
    """Split a narration or commentary script into timed 2-3 word SubtitleCue chunks."""
    cleaned = " ".join((script_text or "").split())
    if not cleaned:
        return []

    duration = max(1.0, float(total_duration_sec))
    words = cleaned.split()
    chunk_texts = _group_words_into_chunks(words, target_size=3, max_size=4)
    if not chunk_texts:
        return []

    usable_duration = max(0.6, duration - 0.15) if duration > 1.5 else duration
    weights = [max(2, len(chunk)) for chunk in chunk_texts]
    total_weight = sum(weights)

    cues: list[SubtitleCue] = []
    cursor = 0.0
    for idx, (chunk, weight) in enumerate(zip(chunk_texts, weights)):
        if idx == len(chunk_texts) - 1:
            end_t = usable_duration
        else:
            share = usable_duration * (weight / total_weight)
            end_t = min(usable_duration - 0.05 * (len(chunk_texts) - 1 - idx), cursor + max(0.2, share))
        if end_t <= cursor:
            end_t = min(duration, cursor + 0.2)
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
    """Ensure cues fit [0, total_duration_sec] and split long cues into 2-3 word dynamic chunks."""
    if not cues:
        return []

    duration = max(1.0, float(total_duration_sec))
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
        if end - start < 0.1:
            continue

        words = raw_text.split()
        if len(words) <= 4:
            normalized.append(SubtitleCue(start_sec=round(start, 2), end_sec=round(end, 2), text=raw_text))
            continue

        sub_chunks = _group_words_into_chunks(words, target_size=3, max_size=4)
        span = end - start
        total_chars = max(1, sum(len(s) for s in sub_chunks))
        sub_cursor = start
        for idx, sub_text in enumerate(sub_chunks):
            if idx == len(sub_chunks) - 1:
                sub_end = end
            else:
                sub_end = min(end - 0.03, sub_cursor + max(0.15, span * (len(sub_text) / total_chars)))
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
    *,
    accent_ass_bgr: str = "&H0000EAFF",
    style_mode: str = "kinetic_word",
    layout_mode: str = "blur_pad_hud",
    reaction_title: str = "",
    allow_profanity: bool = False,
) -> Path:
    """Generate a 1080x1920 .ass file with advanced kinetic word highlights, profanity masking, and custom layouts."""
    output_ass_path.parent.mkdir(parents=True, exist_ok=True)
    clip_dur = max(1.0, float(total_duration_sec))
    hook_dur = max(0.5, min(float(hook_duration_sec), clip_dur))

    # Layout-based vertical margin for CaptionCenter
    margin_v_map = {
        "react": 360,
        "facecam": 800,
        "blur_pad_hud": 460,
        "smart_crop": 540,
    }
    caption_margin_v = margin_v_map.get(layout_mode, 480)

    is_split = layout_mode in ("react", "facecam")
    seam_pos = r"\an5\pos(540,730)" if is_split else ""

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
                "Style: HookBanner,DejaVu Sans,50,&H0000EAFF,&H00FFFFFF,"
                "&H18000000,&H80000000,-1,0,0,0,100,100,0,0,3,16,0,8,65,65,210,1"
            ),
            (
                f"Style: CaptionCenter,DejaVu Sans,70,&H00FFFFFF,{accent_ass_bgr},"
                f"&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,3,2,65,65,{caption_margin_v},1"
            ),
            (
                "Style: CreditTag,DejaVu Sans,34,&H18FFFFFF,&H00FFFFFF,"
                "&H40000000,&H80000000,-1,0,0,0,100,100,0,0,1,2,1,1,60,60,380,1"
            ),
            (
                "Style: TitleBox,DejaVu Sans,46,&H00000000,&H00000000,"
                "&H00FFFFFF,&H00FFFFFF,-1,0,0,0,100,100,0,0,3,16,0,5,65,65,140,1"
            ),
            (
                "Style: TitleOut,DejaVu Sans,44,&H00FFFFFF,&H0000EAFF,"
                "&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,70,70,140,1"
            ),
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
    )

    events: list[str] = []

    # Credit Tag
    masked_credit = mask_profanity(credit_text, allow_profanity)
    escaped_credit = _escape_ass_text(masked_credit)
    if escaped_credit:
        events.append(
            f"Dialogue: 1,{format_ass_timestamp(0.0)},{format_ass_timestamp(clip_dur)},"
            f"CreditTag,,0,0,0,,{escaped_credit}"
        )

    # Single unified banner (never stack TitleBox + HookBanner on top of each other!)
    # In react/facecam split-screen, anchor cleanly on the y=730 seam so the streamer's face stays 100% clear!
    anim_tags = r"\fad(0,180)\fscx92\fscy92\t(0,120,\fscx100\fscy100)"
    if reaction_title:
        masked_rt = mask_profanity(reaction_title, allow_profanity)
        escaped_rt = _escape_ass_text(masked_rt)
        if escaped_rt:
            rt_tags = f"{{{seam_pos}{anim_tags}}}"
            events.append(
                f"Dialogue: 3,{format_ass_timestamp(0.0)},{format_ass_timestamp(min(clip_dur, max(hook_dur, 3.5)))},"
                f"TitleBox,,0,0,0,,{rt_tags}{escaped_rt}"
            )
    else:
        masked_hook = mask_profanity(hook_text, allow_profanity)
        escaped_hook = _escape_ass_text(masked_hook)
        if escaped_hook:
            hook_tags = f"{{{seam_pos}{anim_tags}}}"
            events.append(
                f"Dialogue: 3,{format_ass_timestamp(0.0)},{format_ass_timestamp(hook_dur)},"
                f"HookBanner,,0,0,0,,{hook_tags}{escaped_hook}"
            )

    prepared_cues = _normalize_and_chunk_cues(cues, total_duration_sec=clip_dur)
    for cue in prepared_cues:
        masked_cue_text = mask_profanity(cue.text, allow_profanity)
        words = masked_cue_text.split()
        if not words:
            continue

        if style_mode == "kinetic_word" and len(words) > 1:
            # Interpolate per-word start/end timestamps cleanly with zero overlap flicker
            cue_dur = max(0.2, cue.end_sec - cue.start_sec)
            word_dur = cue_dur / len(words)
            norm_accent = accent_ass_bgr.rstrip("&")
            for i, _target_word in enumerate(words):
                w_start = cue.start_sec + i * word_dur
                w_end = cue.end_sec if i == len(words) - 1 else (cue.start_sec + (i + 1) * word_dur)
                if w_end <= w_start:
                    continue

                # Highlight active spoken word and add pop animation on first word of group
                highlighted_parts = []
                for j, w in enumerate(words):
                    clean_w = _escape_ass_text(w.upper())
                    if j == i:
                        prefix = r"{\fscx112\fscy112\t(0,90,\fscx100\fscy100)}" if i == 0 else ""
                        highlighted_parts.append(f"{prefix}{{\\c{norm_accent}&}}{clean_w}{{\\c&H00FFFFFF&}}")
                    else:
                        highlighted_parts.append(clean_w)

                line_text = " ".join(highlighted_parts)
                events.append(
                    f"Dialogue: 2,{format_ass_timestamp(w_start)},{format_ass_timestamp(w_end)},"
                    f"CaptionCenter,,0,0,0,,{line_text}"
                )
        else:
            escaped_cue = _escape_ass_text(masked_cue_text)
            if escaped_cue:
                events.append(
                    f"Dialogue: 2,{format_ass_timestamp(cue.start_sec)},{format_ass_timestamp(cue.end_sec)},"
                    f"CaptionCenter,,0,0,0,,{escaped_cue}"
                )

    ass_content = header + "\n" + "\n".join(events) + "\n"
    output_ass_path.write_text(ass_content, encoding="utf-8")
    return output_ass_path
