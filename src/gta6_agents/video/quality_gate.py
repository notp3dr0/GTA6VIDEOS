"""Technical & rule verification gate ported from reference check.py."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gta6_agents.models import ClipPlan

BANNED_CLIP_TERMS_RE = re.compile(
    r"\b("
    r"leak|leaked\s+build|play\s+early|early\s+access\s+download|"
    r"free\s+download|crack|piracy|mod\s+menu|money\s+glitch|afk\s+bot|"
    r"samira\s+close|burnt\s+peanut|burntpeanut"
    r")\b",
    re.IGNORECASE,
)


@dataclass
class QualityGateReport:
    passed: bool
    checks: list[dict[str, Any]] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    video_info: dict[str, Any] = field(default_factory=dict)
    sidecar_path: str | None = None


def _fps_of(s: str) -> float:
    try:
        parts = s.split("/")
        if len(parts) == 2:
            return float(parts[0]) / float(parts[1])
        return float(s)
    except Exception:
        return 0.0


def verify_rendered_clip(
    video_path: Path,
    plan: ClipPlan,
    caption_text: str = "",
    *,
    min_duration_sec: float = 5.0,
    max_duration_sec: float = 58.0,
    write_sidecar: bool = True,
    layout_mode: str = "blur_pad_hud",
) -> QualityGateReport:
    """Verify rendered clip resolution, FPS, audio, duration, and banned terms."""
    video_path = Path(video_path)
    checks: list[dict[str, Any]] = []

    def add_check(name: str, passed: bool, detail: str = "") -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    if not video_path.exists() or video_path.stat().st_size <= 1024:
        add_check("file exists & > 1KB", False, f"File missing or empty: {video_path}")
        return QualityGateReport(
            passed=False,
            checks=checks,
            failures=["file exists & > 1KB"],
            video_info={},
            sidecar_path=None,
        )
    add_check("file exists & > 1KB", True, f"{video_path.stat().st_size} bytes")

    ffprobe_exe = shutil.which("ffprobe")
    video_info = {}
    streams = []
    duration = 0.0
    width = 0
    height = 0
    fps = 0.0
    has_audio = False

    if ffprobe_exe:
        try:
            cmd = [
                ffprobe_exe,
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type,width,height,r_frame_rate",
                "-of",
                "json",
                str(video_path),
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            video_info = json.loads(res.stdout)
            streams = video_info.get("streams", [])
            duration = float(video_info.get("format", {}).get("duration", 0) or 0)
        except Exception as e:
            add_check("ffprobe execution", False, str(e))

    v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    au_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if v_stream:
        width = int(v_stream.get("width", 0))
        height = int(v_stream.get("height", 0))
        fps = _fps_of(v_stream.get("r_frame_rate", "0/1"))

    has_audio = au_stream is not None

    add_check("resolution 1080x1920", (width, height) == (1080, 1920), f"{width}x{height}")
    add_check("fps in [28.5, 31.5]", 28.5 <= fps <= 31.5, f"{fps:.2f}")
    add_check("has audio stream", has_audio)
    add_check(
        f"duration [{min_duration_sec:g}, {max_duration_sec:g}s]",
        min_duration_sec <= duration <= max_duration_sec,
        f"{duration:.1f}s",
    )

    combined_text = f"{plan.hook_on_screen} {plan.voiceover_script} {caption_text}"
    banned_matches = BANNED_CLIP_TERMS_RE.findall(combined_text)
    unique_banned = sorted(list(set(banned_matches)))
    add_check(
        "no banned terms",
        not unique_banned,
        ", ".join(unique_banned) if unique_banned else "clean",
    )

    if plan.for_real_oficial_campaign and caption_text.strip():
        cap_low = caption_text.lower()
        has_mention = "youtube.com/youtube" in cap_low
        has_tag = "#launchdayonyt" in cap_low
        tags = [t.lower() for t in re.findall(r"#[A-Za-z0-9_À-ÿ]+", caption_text)]
        only_campaign_tag = tags == ["#launchdayonyt"]
        add_check("campaign required mention (youtube.com/youtube)", has_mention)
        add_check("campaign required hashtag (#launchdayonYT)", has_tag)
        add_check("campaign zero extra hashtags", only_campaign_tag, ", ".join(tags))

    failures = [c["name"] for c in checks if not c["passed"]]
    passed = len(failures) == 0

    sidecar_path_str: str | None = None
    if write_sidecar:
        sidecar_p = video_path.with_suffix(".json")
        sidecar_data = {
            "plan_id": plan.id,
            "source_url": plan.source.url,
            "creator": plan.source.creator_handle,
            "layout": layout_mode,
            "hook": plan.hook_on_screen,
            "duration_sec": duration,
            "width": width,
            "height": height,
            "fps": fps,
            "passed": passed,
            "checks": checks,
        }
        try:
            sidecar_p.write_text(json.dumps(sidecar_data, indent=2, ensure_ascii=False), encoding="utf-8")
            sidecar_path_str = str(sidecar_p)
        except Exception:
            pass

    return QualityGateReport(
        passed=passed,
        checks=checks,
        failures=failures,
        video_info=video_info,
        sidecar_path=sidecar_path_str,
    )
