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

    # Dynamic static-edge corner scan across variable webcam sizes (15%..42% width, 18%..46% height)
    # Works for ANY streamer (Coringa 24x28%, Alanzoka, Skorpion, Gaules, etc.) with ZERO OpenCV needed!
    mean_global_std = sum(std_img) / len(std_img)

    # Precompute 1D edge gradients on the temporal-median frame (static webcam borders survive median; gameplay blurs!)
    gx = [0] * (w_px * h_px)
    gy = [0] * (w_px * h_px)
    for y in range(1, h_px - 1):
        row_off = y * w_px
        for x in range(1, w_px - 1):
            gx[row_off + x] = abs(med_img[row_off + x + 1] - med_img[row_off + x - 1])
            gy[row_off + x] = abs(med_img[row_off + w_px + x] - med_img[row_off - w_px + x])

    def _scan_corner(name: str, is_right: bool, is_bottom: bool) -> tuple[float, str, str, tuple[float, float, float, float]] | None:
        # Search exact inner vertical border column (v_col) and inner horizontal border row (h_row)
        v_range = range(int(w_px * 0.58), int(w_px * 0.85)) if is_right else range(int(w_px * 0.15), int(w_px * 0.42))
        h_range = range(int(h_px * 0.52), int(h_px * 0.82)) if is_bottom else range(int(h_px * 0.18), int(h_px * 0.46))

        best_v = (0.0, 0)
        for vc in v_range:
            y_lo = int(h_px * 0.60) if is_bottom else 2
            y_hi = h_px - 2 if is_bottom else int(h_px * 0.30)
            # Peak edge across 1px neighborhood to handle crisp or anti-aliased borders
            vals = [max(gx[y * w_px + vc], gx[y * w_px + vc - 1], gx[y * w_px + vc + 1]) for y in range(y_lo, y_hi)]
            # Combine average edge + fraction of strong edge pixels along the straight line
            strong_frac = sum(1 for v in vals if v >= 16) / max(1, len(vals))
            mean_e = sum(vals) / max(1, len(vals))
            score = mean_e * (0.4 + 0.6 * strong_frac)
            if score > best_v[0]:
                best_v = (score, vc)

        best_h = (0.0, 0)
        for hr in h_range:
            x_lo = best_v[1] if is_right else 2
            x_hi = w_px - 2 if is_right else best_v[1]
            if x_hi - x_lo < 16:
                continue
            vals = [max(gy[hr * w_px + x], gy[(hr - 1) * w_px + x], gy[(hr + 1) * w_px + x]) for x in range(x_lo, x_hi)]
            strong_frac = sum(1 for v in vals if v >= 16) / max(1, len(vals))
            mean_e = sum(vals) / max(1, len(vals))
            score = mean_e * (0.4 + 0.6 * strong_frac)
            if score > best_h[0]:
                best_h = (score, hr)

        vc = best_v[1]
        hr = best_h[1]
        if vc <= 0 or hr <= 0:
            return None

        # Re-evaluate vertical border strictly along the exact height of the detected corner box [y0..y1]
        y0 = hr if is_bottom else 1
        y1 = h_px - 1 if is_bottom else hr
        x0 = vc if is_right else 1
        x1 = w_px - 1 if is_right else vc
        if (y1 - y0) < 14 or (x1 - x0) < 20:
            return None

        exact_v = [max(gx[y * w_px + vc], gx[y * w_px + vc - 1], gx[y * w_px + vc + 1]) for y in range(y0, y1)]
        exact_h = [max(gy[hr * w_px + x], gy[(hr - 1) * w_px + x], gy[(hr + 1) * w_px + x]) for x in range(x0, x1)]
        v_strong = sum(1 for v in exact_v if v >= 14) / max(1, len(exact_v))
        h_strong = sum(1 for v in exact_h if v >= 14) / max(1, len(exact_h))
        v_mean = sum(exact_v) / max(1, len(exact_v))
        h_mean = sum(exact_h) / max(1, len(exact_h))

        # Require BOTH straight edges to be consistent along >=65% of the box perimeter
        if v_strong < 0.65 or h_strong < 0.65:
            return None

        border_strength = min(v_mean, h_mean)
        if border_strength < 16.0:
            return None

        # Check outer edge inset (in case webcam has a 2-5% margin from screen edge)
        fx = (vc / w_px) if is_right else 0.0
        fy = (hr / h_px) if is_bottom else 0.0
        fw = ((w_px - vc) / w_px) if is_right else (vc / w_px)
        fh = ((h_px - hr) / h_px) if is_bottom else (hr / h_px)
        # Tiny 1% trim inward to avoid including the 1px OBS border line in the cropped cam
        fx = min(0.95, max(0.0, fx + (0.005 if is_right else 0.0)))
        fy = min(0.95, max(0.0, fy + (0.005 if is_bottom else 0.0)))
        fw = max(0.12, min(1.0 - fx, fw - 0.006))
        fh = max(0.15, min(1.0 - fy, fh - 0.006))

        side = "right" if is_right else "left"
        return (border_strength, name, side, (round(fx, 4), round(fy, 4), round(fw, 4), round(fh, 4)))

    corner_specs = [
        ("top_left", False, False),
        ("top_right", True, False),
        ("bottom_left", False, True),
        ("bottom_right", True, True),
    ]
    best_corner: tuple[float, str, str, tuple[float, float, float, float]] | None = None
    for cname, is_r, is_b in corner_specs:
        hit = _scan_corner(cname, is_r, is_b)
        if hit is not None and (best_corner is None or hit[0] > best_corner[0]):
            best_corner = hit

    has_gp = mean_global_std >= 2.2
    if best_corner is not None:
        strength, cname, side, frac_box = best_corner
        conf = min(0.98, round(strength / 45.0, 2))
        scaled_gw = int(1920 * game_h_target / 1080) // 2 * 2
        max_gx = max(0, scaled_gw - game_w_target)
        # Keep gameplay cleanly clear of the webcam side (+2.8% safety margin so zero webcam edge sliver peeks in!)
        game_crop_x = min(max_gx, int((frac_box[0] + frac_box[2] + 0.028) * scaled_gw)) if side == "left" else 0
        return FacecamDetection(
            mode="cam",
            box=frac_box,
            cam_side=side,
            has_gameplay=has_gp,
            game_crop_x=game_crop_x,
            confidence=conf,
            reason=f"Detected static {cname} webcam border (edge={strength:.1f}, box={frac_box})",
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
