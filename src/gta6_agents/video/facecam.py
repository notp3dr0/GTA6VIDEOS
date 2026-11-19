"""Facecam detection, bounding box parsing, and gameplay crop calculation."""

from __future__ import annotations

import json
import subprocess
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

try:
    import cv2
    import numpy as np
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False


@dataclass
class FacecamDetection:
    mode: str  # "cam" | "face" | "none"
    box: tuple[float, float, float, float] | None  # normalized [x, y, w, h]
    cam_side: str  # "left" | "right"
    has_gameplay: bool
    game_crop_x: int | None
    confidence: float
    reason: str


def parse_cam_box(cam_spec: str) -> tuple[float, float, float, float] | None:
    """Parse 'x:y:w:h' string into 4 normalized floats in [0, 1], or None if invalid."""
    if not cam_spec:
        return None
    parts = cam_spec.split(":")
    if len(parts) != 4:
        return None
    try:
        vals = [float(p) for p in parts]
    except ValueError:
        return None
    if not all(0.0 <= v <= 1.0 for v in vals):
        return None
    x, y, w, h = vals
    if w <= 0 or h <= 0 or x + w > 1.001 or y + h > 1.001:
        return None
    return (x, y, w, h)


def detect_facecam_and_game_offset(
    video_path: Path,
    start_sec: float = 0.0,
    end_sec: float | None = None,
    explicit_cam: str | None = None,
    game_w_target: int = 1080,
    game_h_target: int = 1190,
) -> FacecamDetection:
    """Detect webcam / facebox and compute optimal gameplay crop offset."""
    if explicit_cam:
        parsed = parse_cam_box(explicit_cam)
        if parsed:
            x, y, w, h = parsed
            cam_side = "left" if (x + w / 2.0) < 0.5 else "right"
            return FacecamDetection(
                mode="cam",
                box=parsed,
                cam_side=cam_side,
                has_gameplay=True,
                game_crop_x=0 if cam_side == "right" else max(0, int(w * 1080)),
                confidence=1.0,
                reason="Explicit valid --cam spec provided",
            )

    video_path = Path(video_path)
    if not video_path.exists():
        return FacecamDetection(
            mode="none",
            box=None,
            cam_side="right",
            has_gameplay=True,
            game_crop_x=None,
            confidence=0.0,
            reason=f"Video not found: {video_path}",
        )

    ffmpeg_exe = shutil.which("ffmpeg")
    if not ffmpeg_exe:
        return FacecamDetection(
            mode="none",
            box=None,
            cam_side="right",
            has_gameplay=True,
            game_crop_x=None,
            confidence=0.0,
            reason="ffmpeg not available",
        )

    duration = 10.0
    try:
        probe_cmd = [shutil.which("ffprobe") or "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video_path)]
        res = subprocess.run(probe_cmd, capture_output=True, text=True, check=True)
        data = json.loads(res.stdout)
        duration = float(data.get("format", {}).get("duration", 10.0) or 10.0)
    except Exception:
        pass

    s = max(0.0, start_sec)
    e = min(end_sec if end_sec is not None else duration, duration)
    if e <= s:
        e = s + 5.0

    pass_frames = 6
    interval = max(0.4, (e - s) / pass_frames)

    raw_frames: list[bytes] = []
    for i in range(pass_frames):
        t_pt = s + (i + 0.5) * interval
        if t_pt >= e:
            t_pt = max(s, e - 0.1)
        frame_cmd = [
            ffmpeg_exe,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{t_pt:.2f}",
            "-i",
            str(video_path),
            "-vframes",
            "1",
            "-vf",
            "scale=160:90:flags=fast_bilinear,format=gray",
            "-f",
            "rawvideo",
            "pipe:1",
        ]
        try:
            proc = subprocess.run(frame_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=4)
            if proc.returncode == 0 and len(proc.stdout) == 160 * 90:
                raw_frames.append(proc.stdout)
        except Exception:
            continue

    if len(raw_frames) < 3:
        return FacecamDetection(
            mode="none",
            box=None,
            cam_side="right",
            has_gameplay=True,
            game_crop_x=None,
            confidence=0.0,
            reason="Insufficient decoded frames for corner webcam analysis",
        )

    # Pure-Python static border & temporal variance analysis (160x90 = 14,400 pixels, ~25ms total!)
    w_px, h_px = 160, 90
    n_fr = len(raw_frames)
    med_img = [0] * (w_px * h_px)
    std_img = [0.0] * (w_px * h_px)
    mid_idx = n_fr // 2

    for idx in range(w_px * h_px):
        vals = sorted(fr[idx] for fr in raw_frames)
        med_img[idx] = vals[mid_idx]
        mean_v = sum(vals) / n_fr
        var_v = sum((v - mean_v) ** 2 for v in vals) / n_fr
        std_img[idx] = var_v ** 0.5

    # Check 4 standard streamer webcam corners for strong straight static border lines (Sobel/grad on median frame)
    # Candidate boxes in 160x90 coords: (name, x0, y0, x1, y1, side, frac_box)
    candidates = [
        ("top_right", 112, 4, 156, 34, "right", (0.70, 0.04, 0.28, 0.34)),
        ("bottom_right", 112, 56, 156, 86, "right", (0.70, 0.62, 0.28, 0.34)),
        ("top_left", 4, 4, 48, 34, "left", (0.025, 0.04, 0.28, 0.34)),
        ("bottom_left", 4, 56, 48, 86, "left", (0.025, 0.62, 0.28, 0.34)),
    ]

    mean_global_std = sum(std_img) / len(std_img)
    best_corner: tuple[float, str, str, tuple[float, float, float, float]] | None = None

    for name, x0, y0, x1, y1, side, frac_box in candidates:
        # Measure static straight-line edge contrast along the INNER vertical & horizontal borders of the corner box
        v_col = x0 if side == "right" else x1
        h_row = y1 if "top" in name else y0

        v_edges = [
            abs(med_img[y * w_px + min(w_px - 1, v_col + 1)] - med_img[y * w_px + max(0, v_col - 1)])
            for y in range(max(1, y0), min(h_px - 1, y1))
        ]
        h_edges = [
            abs(med_img[min(h_px - 1, h_row + 1) * w_px + x] - med_img[max(0, h_row - 1) * w_px + x])
            for x in range(max(1, x0), min(w_px - 1, x1))
        ]
        v_score = sum(v_edges) / max(1, len(v_edges))
        h_score = sum(h_edges) / max(1, len(h_edges))
        border_strength = min(v_score, h_score)

        # Also check that the interior of the corner differs from static gameplay outer frame
        if border_strength >= 24.0:
            if best_corner is None or border_strength > best_corner[0]:
                best_corner = (border_strength, name, side, frac_box)

    has_gp = mean_global_std >= 3.0
    if best_corner is not None:
        strength, cname, side, frac_box = best_corner
        conf = min(0.95, round(strength / 60.0, 2))
        scaled_gw = int(1920 * game_h_target / 1080) // 2 * 2
        max_gx = max(0, scaled_gw - game_w_target)
        game_crop_x = max_gx if side == "left" else 0
        return FacecamDetection(
            mode="cam",
            box=frac_box,
            cam_side=side,
            has_gameplay=has_gp,
            game_crop_x=game_crop_x,
            confidence=conf,
            reason=f"Detected static {cname} webcam border (edge={strength:.1f})",
        )

    return FacecamDetection(
        mode="none",
        box=None,
        cam_side="right",
        has_gameplay=has_gp,
        game_crop_x=None,
        confidence=0.0,
        reason="No prominent static corner webcam border detected; using channel default layout",
    )
