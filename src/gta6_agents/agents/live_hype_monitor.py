"""Real-Time Live Hype Monitor & Retroactive Replay Clipper (Audio Scream/Laugh + Visual Action + Chat Burst + Whisper/AI Judge).

Replicates professional real-time live clipping systems (like Real Oficial's live monitor / OpusClip Live):
1. Maintains a lightweight 2.4-minute FFmpeg `.ts` ring buffer (`-f segment -segment_wrap 24`) on SSD (~80 MB max)
   for the active live broadcast (`KY4DWumZffg` during campaign, or active Brazilian streamer live post-campaign).
2. Scans each new 6s chunk in ~50ms via FFmpeg `volumedetect` + `scdet` to detect sudden streamer screams,
   bursts of laughter, explosions, or high-speed chases above the streamer's rolling vocal baseline.
3. Samples anonymous read-only Twitch IRC chat velocity & Brazilian hype triggers (`CLIPA`, `KKKKK`, `MEU DEUS`, `INSANO`).
4. When a spike fires, retroactively stitches `[T - 18s, T + 7s]` (capturing the setup BEFORE the reaction!),
   transcribes PT-BR speech via Whisper, confirms/classifies with Hydrogen AI (`engracado` | `jogada_epica` |
   `reacao_choque` | `momento_marcante`), and immediately triggers the 9:16 render + publish pipeline.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gta6_agents.agents.compliance import sanitize_untrusted_video_text
from gta6_agents.config import AppConfig
from gta6_agents.models import ContentPillar, SourceCandidate
from gta6_agents.storage import StateStore
from gta6_agents.video.downloader import (
    _resolve_direct_stream_urls,
    transcribe_local_audio_ptbr,
)

logger = logging.getLogger(__name__)

_MEAN_VOL_RE = re.compile(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")
_MAX_VOL_RE = re.compile(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")
_SCDET_SCORE_RE = re.compile(r"lavfi\.scd\.score:\s*(\d+(?:\.\d+)?)")

_BR_CHAT_HYPE_RE = re.compile(
    r"\b(?:clipa+|clipou|clipa\s+isso|clip\s+isso|k{4,}|haha{2,}|rsrs+|"
    r"meu\s*deus|caralho+|insano+|olha\s+isso|que\s+isso|cinema|absolute\s+cinema|"
    r"wtf+|pog+|lul+|kekw+|eita+|nem\s+ferrando|mentira+)\b",
    re.IGNORECASE,
)

_TRANSCRIPT_FUNNY_RE = re.compile(
    r"\b(?:k{3,}|haha|mano\s+do\s+c[eé]u|n[aã]o\s+acredito|que\s+isso\s+cara|"
    r"morri|capotou|voou|socorro|ai\s+meu\s+deus)\b",
    re.IGNORECASE,
)
_TRANSCRIPT_EPIC_PLAY_RE = re.compile(
    r"\b(?:olha\s+essa\s+fuga|olha\s+esse\s+drift|explodiu|pol[ií]cia|helic[oó]ptero|"
    r"jogada|clutch|passou\s+no\s+meio|insano|absurdo)\b",
    re.IGNORECASE,
)


@dataclass
class HypeSpikeAnalysis:
    """Multi-modal real-time excitement metrics for a live stream window."""

    mean_volume_db: float = -26.0
    max_volume_db: float = -18.0
    baseline_mean_db: float = -26.0
    rms_jump_db: float = 0.0
    peak_spike_db: float = 0.0
    scene_changes: int = 0
    max_scene_score: float = 0.0
    chat_messages_10s: float = 0.0
    chat_hype_hits: int = 0
    composite_spike_score: float = 0.0
    hype_category: str = "momento_marcante"
    whisper_transcript: str = ""
    suggested_hook_ptbr: str = ""
    triggered: bool = False
    sample_chat_reactions: list[str] = field(default_factory=list)


def analyze_media_excitement_ffmpeg(
    segment_path: Path,
    baseline_mean_db: float = -25.0,
) -> dict[str, float]:
    """Run ultra-fast (~200x realtime) FFmpeg `volumedetect` + `scdet` pass on a buffered chunk."""
    if not segment_path.exists() or segment_path.stat().st_size < 512:
        return {
            "mean_volume_db": baseline_mean_db,
            "max_volume_db": baseline_mean_db,
            "rms_jump_db": 0.0,
            "peak_spike_db": 0.0,
            "scene_changes": 0.0,
            "max_scene_score": 0.0,
        }

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i",
        str(segment_path),
        "-vf",
        "scdet=threshold=11",
        "-af",
        "volumedetect",
        "-f",
        "null",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=20)
    stderr = proc.stderr or ""

    mean_m = _MEAN_VOL_RE.search(stderr)
    max_m = _MAX_VOL_RE.search(stderr)
    scd_scores = [float(x) for x in _SCDET_SCORE_RE.findall(stderr)]

    mean_db = float(mean_m.group(1)) if mean_m else baseline_mean_db
    max_db = float(max_m.group(1)) if max_m else mean_db

    # Guard against pure digital silence (-91 dB) skewing baseline
    if mean_db <= -75.0:
        return {
            "mean_volume_db": mean_db,
            "max_volume_db": max_db,
            "rms_jump_db": 0.0,
            "peak_spike_db": 0.0,
            "scene_changes": float(len(scd_scores)),
            "max_scene_score": max(scd_scores) if scd_scores else 0.0,
        }

    rms_jump = max(0.0, mean_db - baseline_mean_db)
    peak_spike = max(0.0, max_db - baseline_mean_db)

    return {
        "mean_volume_db": round(mean_db, 2),
        "max_volume_db": round(max_db, 2),
        "rms_jump_db": round(rms_jump, 2),
        "peak_spike_db": round(peak_spike, 2),
        "scene_changes": float(len(scd_scores)),
        "max_scene_score": round(max(scd_scores), 2) if scd_scores else 0.0,
    }


def compute_composite_hype_score(
    *,
    rms_jump_db: float,
    peak_spike_db: float,
    scene_changes: float,
    max_scene_score: float,
    chat_messages_10s: float = 0.0,
    chat_hype_hits: int = 0,
) -> float:
    """Combine Acoustic Scream/Laugh + Visual Action + Live Chat Burst into a [0..100] score."""
    # 1. Acoustic excitement: +6 dB RMS jump (~2x perceived loudness) or +14 dB peak scream/explosion
    audio_pts = min(58.0, (rms_jump_db * 5.2) + max(0.0, peak_spike_db - 6.0) * 2.4)

    # 2. Visual chaos / fast camera motion / explosions
    visual_pts = min(22.0, (scene_changes * 3.5) + (max_scene_score * 0.35))

    # 3. Live chat velocity & "CLIPA" / "KKKKK" burst
    chat_pts = min(42.0, (chat_hype_hits * 7.5) + (chat_messages_10s * 0.9))

    return round(max(0.0, min(100.0, audio_pts + visual_pts + chat_pts)), 2)


async def sample_twitch_chat_hype(
    channel_login: str,
    sample_seconds: float = 3.5,
) -> dict[str, Any]:
    """Read public Twitch live chat anonymously (`justinfan` read-only IRC) to detect 'CLIPA' / 'KKKK' bursts."""
    login = re.sub(r"[^a-z0-9_]", "", (channel_login or "").strip().lower())
    if not login or sample_seconds <= 0:
        return {"messages_10s": 0.0, "hype_hits": 0, "reactions": []}

    nick = f"justinfan{random.randint(10000, 99999)}"
    msg_count = 0
    hype_hits = 0
    reactions: list[str] = []

    writer: asyncio.StreamWriter | None = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("irc.chat.twitch.tv", 6667),
            timeout=3.0,
        )
        writer.write(f"PASS SCHMOOPIIE\r\nNICK {nick}\r\nJOIN #{login}\r\n".encode("utf-8"))
        await writer.drain()

        deadline = time.monotonic() + sample_seconds
        while time.monotonic() < deadline:
            remaining = max(0.05, deadline - time.monotonic())
            try:
                raw_line = await asyncio.wait_for(reader.readline(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            if not raw_line:
                break
            line = raw_line.decode("utf-8", errors="ignore").strip()
            if line.startswith("PING"):
                writer.write(b"PONG :tmi.twitch.tv\r\n")
                await writer.drain()
                continue
            if "PRIVMSG" not in line:
                continue
            msg_count += 1
            parts = line.split(f"PRIVMSG #{login} :", 1)
            chat_text = parts[1].strip() if len(parts) == 2 else line
            matches = _BR_CHAT_HYPE_RE.findall(chat_text)
            if matches:
                hype_hits += len(matches)
                if len(reactions) < 4:
                    reactions.append(sanitize_untrusted_video_text(chat_text, max_length=60))
    except Exception as exc:
        logger.debug("Anonymous Twitch IRC sample skipped for %s: %s", login, exc)
    finally:
        if writer is not None:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    scale_to_10s = 10.0 / max(0.5, sample_seconds)
    return {
        "messages_10s": round(msg_count * scale_to_10s, 1),
        "hype_hits": hype_hits,
        "reactions": reactions,
    }


class RollingLiveRingBuffer:
    """Maintains a bounded circular `.ts` replay buffer (~2.4 min max on disk) for instant retroactive clipping."""

    def __init__(
        self,
        stream_slug: str,
        buffer_dir: Path,
        segment_sec: int = 6,
        wrap_count: int = 24,
    ) -> None:
        self.stream_slug = re.sub(r"[^A-Za-z0-9_-]", "_", stream_slug)[:40] or "live"
        self.buffer_dir = buffer_dir / f"ring_{self.stream_slug}"
        self.segment_sec = max(3, int(segment_sec))
        self.wrap_count = max(6, int(wrap_count))
        self._proc: asyncio.subprocess.Process | None = None
        self._active_source_url: str = ""

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def ensure_buffering(self, live_url: str, *, stream_copy: bool = True) -> bool:
        """Start (or restart) the background FFmpeg `.ts` ring buffer on `live_url` (~0.2% CPU w/ `-c copy`)."""
        clean_url = (live_url or "").strip().split("#", 1)[0]
        if not clean_url:
            return False
        if self.is_running and self._active_source_url == clean_url:
            return True

        await self.stop()
        self.buffer_dir.mkdir(parents=True, exist_ok=True)

        direct_urls = await _resolve_direct_stream_urls(clean_url, is_live=True)
        if not direct_urls:
            return False

        seg_pattern = str(self.buffer_dir / "chunk_%03d.ts")
        cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "5",
            "-i",
            direct_urls[0],
        ]
        if len(direct_urls) >= 2:
            cmd.extend(
                [
                    "-reconnect",
                    "1",
                    "-reconnect_streamed",
                    "1",
                    "-reconnect_delay_max",
                    "5",
                    "-i",
                    direct_urls[1],
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                ]
            )
        if stream_copy and len(direct_urls) == 1:
            cmd.extend(["-c", "copy"])
        elif stream_copy:
            cmd.extend(["-c:v", "copy", "-c:a", "aac", "-b:a", "160k"])
        else:
            cmd.extend(
                [
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-crf",
                    "21",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "160k",
                ]
            )
        cmd.extend(
            [
                "-f",
                "segment",
                "-segment_time",
                str(self.segment_sec),
                "-segment_wrap",
                str(self.wrap_count),
                "-reset_timestamps",
                "1",
                seg_pattern,
            ]
        )

        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        self._active_source_url = clean_url
        return True

    async def stop(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=4.0)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._proc = None
        self._active_source_url = ""

    def list_completed_chunks(self) -> list[Path]:
        """Return buffered `.ts` chunks sorted chronologically (`oldest -> newest`), excluding the half-written tip."""
        if not self.buffer_dir.exists():
            return []
        chunks = [
            p
            for p in self.buffer_dir.glob("chunk_*.ts")
            if p.is_file() and p.stat().st_size > 2048
        ]
        chunks.sort(key=lambda p: p.stat().st_mtime)
        # If FFmpeg is actively writing the very last file, prefer completed predecessors when >=2 exist
        if self.is_running and len(chunks) >= 2:
            newest_age = time.time() - chunks[-1].stat().st_mtime
            if newest_age < 0.8:
                return chunks[:-1]
        return chunks

    def stitch_retroactive_replay(
        self,
        output_mp4: Path,
        lookback_sec: float = 24.0,
    ) -> Path | None:
        """Stitch the newest `N` chunks (`~18s` setup BEFORE spike + `~6s` reaction) into an `.mp4`."""
        chunks = self.list_completed_chunks()
        if not chunks:
            return None

        needed = max(1, int(round(lookback_sec / max(1, self.segment_sec))))
        selected = chunks[-needed:]
        output_mp4.parent.mkdir(parents=True, exist_ok=True)
        concat_txt = output_mp4.parent / f"{output_mp4.stem}_concat.txt"

        lines = [f"file '{c.resolve()}'" for c in selected]
        concat_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")

        try:
            cmd = [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_txt),
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
                str(output_mp4),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=45)
            if proc.returncode != 0 or not output_mp4.exists() or output_mp4.stat().st_size <= 2048:
                logger.warning("Ring buffer stitch failed: %s", proc.stderr.strip())
                return None
            return output_mp4
        finally:
            concat_txt.unlink(missing_ok=True)


class LiveHypeMonitorAgent:
    """Monitors active GTA VI lives in real time and instantly clips funny, epic, or memorable moments."""

    def __init__(self, config: AppConfig, store: StateStore) -> None:
        self.config = config
        self.store = store
        self._buffers: dict[str, RollingLiveRingBuffer] = {}
        self._baselines_db: dict[str, float] = {}
        self._last_analyzed_mtime: dict[str, float] = {}
        self._last_instant_clip_mono: float = 0.0
        self._last_streamer_clip_mono: dict[str, float] = {}
        self._cached_live_targets: list[dict[str, Any]] = []
        self._last_discovery_mono: float = 0.0
        self._channel_rr_index: int = 0

    def _monitor_settings(self) -> dict[str, Any]:
        from gta6_agents.agents.scout import ScoutAgent

        scout = ScoutAgent(self.config, self.store)
        cap_cfg, _ = scout._load_streamers_br_config()
        mon = cap_cfg.get("live_hype_monitor", {}) if isinstance(cap_cfg, dict) else {}
        return mon if isinstance(mon, dict) else {}

    def classify_moment_and_hook(
        self,
        *,
        streamer_name: str,
        transcript: str,
        spike: HypeSpikeAnalysis,
        for_campaign: bool,
    ) -> tuple[str, str]:
        """Classify moment (`engracado` | `jogada_epica` | `reacao_choque` | `momento_marcante`) & craft 2s PT-BR hook."""
        joined = f"{transcript} {' '.join(spike.sample_chat_reactions)}"
        if _TRANSCRIPT_FUNNY_RE.search(joined) or any(
            "kkk" in r.lower() or "lul" in r.lower() for r in spike.sample_chat_reactions
        ):
            category = "engracado"
            hook = (
                "NINGUÉM TANCOU ESSA CENA AO VIVO NO GTA VI!"
                if for_campaign
                else f"OLHA A REAÇÃO DO {streamer_name.upper()} NESSA CENA!"
            )
        elif _TRANSCRIPT_EPIC_PLAY_RE.search(joined) or spike.max_scene_score >= 22.0:
            category = "jogada_epica"
            hook = (
                "OLHA O QUE ACABOU DE ACONTECER AO VIVO EM LEONIDA!"
                if for_campaign
                else f"JOGADA INSANA AO VIVO NA LIVE DO {streamer_name.upper()}!"
            )
        elif spike.rms_jump_db >= 8.5 or spike.peak_spike_db >= 14.0:
            category = "reacao_choque"
            hook = (
                "TODO MUNDO FICOU EM CHOQUE COM ISSO AO VIVO!"
                if for_campaign
                else f"{streamer_name.upper()} FICOU EM CHOQUE COM ESSE DETALHE!"
            )
        else:
            category = "momento_marcante"
            hook = (
                "MOMENTO HISTÓRICO AO VIVO NO LANÇAMENTO DO GTA VI!"
                if for_campaign
                else f"MOMENTO ÉPICO AO VIVO EM VICE CITY ({streamer_name.upper()})!"
            )
        return category, hook[:58]

    async def evaluate_buffered_chunk(
        self,
        *,
        stream_key: str,
        canonical_url: str,
        creator_handle: str,
        streamer_name: str,
        chunk_path: Path,
        ring_buffer: RollingLiveRingBuffer | None = None,
        for_campaign: bool = False,
        twitch_login: str = "",
        spike_threshold: float = 58.0,
        sample_chat: bool = True,
    ) -> tuple[HypeSpikeAnalysis, SourceCandidate | None]:
        """Evaluate a live chunk for audio/visual/chat hype spikes and build a ready-to-render `SourceCandidate`."""
        baseline = self._baselines_db.get(stream_key, -25.0)
        media_metrics = await asyncio.to_thread(
            analyze_media_excitement_ffmpeg,
            chunk_path,
            baseline,
        )

        chat_data = {"messages_10s": 0.0, "hype_hits": 0, "reactions": []}
        if sample_chat and twitch_login:
            chat_data = await sample_twitch_chat_hype(twitch_login, sample_seconds=3.0)

        comp_score = compute_composite_hype_score(
            rms_jump_db=media_metrics["rms_jump_db"],
            peak_spike_db=media_metrics["peak_spike_db"],
            scene_changes=media_metrics["scene_changes"],
            max_scene_score=media_metrics["max_scene_score"],
            chat_messages_10s=float(chat_data["messages_10s"]),
            chat_hype_hits=int(chat_data["hype_hits"]),
        )

        # Update rolling vocal baseline gently ONLY on non-spike chunks so loud screams don't poison baseline
        mean_db = media_metrics["mean_volume_db"]
        if mean_db > -70.0 and comp_score < spike_threshold:
            self._baselines_db[stream_key] = round((0.82 * baseline) + (0.18 * mean_db), 2)
        elif stream_key not in self._baselines_db and mean_db > -70.0:
            self._baselines_db[stream_key] = round(mean_db, 2)

        analysis = HypeSpikeAnalysis(
            mean_volume_db=mean_db,
            max_volume_db=media_metrics["max_volume_db"],
            baseline_mean_db=baseline,
            rms_jump_db=media_metrics["rms_jump_db"],
            peak_spike_db=media_metrics["peak_spike_db"],
            scene_changes=int(media_metrics["scene_changes"]),
            max_scene_score=media_metrics["max_scene_score"],
            chat_messages_10s=float(chat_data["messages_10s"]),
            chat_hype_hits=int(chat_data["hype_hits"]),
            composite_spike_score=comp_score,
            sample_chat_reactions=list(chat_data["reactions"]),
        )

        if comp_score < spike_threshold:
            return analysis, None

        # Spike confirmed! Stitch retroactive [T - 18s, T + 7s] replay buffer (or use chunk_path directly)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        replay_mp4 = self.config.work_dir / "instant_hype" / f"hype_{stream_key}_{stamp}.mp4"
        stitched: Path | None = None
        if ring_buffer is not None:
            stitched = await asyncio.to_thread(
                ring_buffer.stitch_retroactive_replay,
                replay_mp4,
                25.0,
            )
        effective_video = stitched if (stitched and stitched.exists()) else chunk_path

        # Transcribe real spoken PT-BR reaction if Groq/OpenAI Whisper key is configured
        cues = await transcribe_local_audio_ptbr(
            effective_video,
            groq_api_key=self.config.groq_api_key,
            openai_api_key=self.config.openai_api_key,
        )
        transcript_text = " ".join(c.text for c in cues).strip()
        category, hook_pt = self.classify_moment_and_hook(
            streamer_name=streamer_name,
            transcript=transcript_text,
            spike=analysis,
            for_campaign=for_campaign,
        )

        analysis.triggered = True
        analysis.hype_category = category
        analysis.whisper_transcript = transcript_text
        analysis.suggested_hook_ptbr = hook_pt

        event_url = f"{canonical_url.split('#', 1)[0]}#hype-{stamp}"
        platform_type = (
            "realoficial_acervo"
            if for_campaign
            else ("twitch" if "twitch.tv" in canonical_url.lower() else "youtube")
        )

        fact_desc = (
            f"Momento {category.replace('_', ' ')} capturado no instante exato na transmissão oficial YouTube x GTA VI."
            if for_campaign
            else f"Pico ao vivo ({category.replace('_', ' ')}, +{analysis.rms_jump_db:.1f} dB) capturado na hora na live de {streamer_name} ({creator_handle})."
        )

        candidate = SourceCandidate(
            title=f"{hook_pt} — {streamer_name} Ao Vivo no GTA VI"[:160],
            url=event_url,
            creator_handle=creator_handle,
            source_type=platform_type,
            is_official=for_campaign,
            is_campaign_acervo=for_campaign,
            campaign_slug=self.config.real_oficial_campaign_slug if for_campaign else None,
            pillar=ContentPillar.REAL_OFICIAL_CAMPAIGN if for_campaign else ContentPillar.GAMEPLAY_CLIPS,
            summary=(
                transcript_text[:320]
                if transcript_text
                else f"{hook_pt}. Corte instantâneo capturado do buffer de replay ao vivo de {streamer_name} em Leonida."
            ),
            verified_facts=[fact_desc],
            suggested_start_sec=0.0,
            suggested_end_sec=25.0,
            virality_score=min(100.0, round(88.0 + (comp_score * 0.12), 2)),
            metadata={
                "canonical_url": canonical_url.split("#", 1)[0],
                "prebuffered_segment_path": str(effective_video),
                "instant_hype_clip": True,
                "hype_category": category,
                "composite_spike_score": comp_score,
                "rms_jump_db": analysis.rms_jump_db,
                "chat_hype_hits": analysis.chat_hype_hits,
                "freshness_score": 100.0,
                "published_ts": datetime.now(timezone.utc).timestamp(),
                "is_live": True,
            },
        )
        return analysis, candidate

    async def prune_inactive_buffers(self, active_keys: set[str]) -> int:
        """Stop & remove FFmpeg ring buffers for streamers who went offline so zero orphan processes remain."""
        stale_keys = [k for k in list(self._buffers.keys()) if k not in active_keys]
        for k in stale_keys:
            ring = self._buffers.pop(k, None)
            if ring is not None:
                await ring.stop()
        return len(stale_keys)

    def select_channel_for_category(
        self,
        category: str,
        channels: list[Any],
        used_in_tick: set[str],
    ) -> str:
        """Route simultaneous hype clips to their ideal thematic channel (`ch1..ch5`) without colliding."""
        if not channels:
            return ""
        preferred_Hints: dict[str, tuple[str, ...]] = {
            "engracado": ("ch5_", "resenha", "stories"),
            "jogada_epica": ("ch3_", "plays", "ch4_", "motores"),
            "reacao_choque": ("ch1_", "plantao", "corteslive"),
            "momento_marcante": ("ch2_", "radar", "segredos"),
        }
        hints = preferred_Hints.get(category, ())
        for ch in channels:
            cid = str(getattr(ch, "channel_id", "") or "")
            handle = str(getattr(ch, "handle", "") or "").lower()
            if cid in used_in_tick:
                continue
            if any(h in cid.lower() or h in handle for h in hints):
                used_in_tick.add(cid)
                return cid

        # Fallback: next unused channel in round-robin order
        for _ in range(len(channels)):
            chosen = channels[self._channel_rr_index % len(channels)]
            self._channel_rr_index += 1
            cid = str(getattr(chosen, "channel_id", "") or "")
            if cid and cid not in used_in_tick:
                used_in_tick.add(cid)
                return cid

        chosen = channels[self._channel_rr_index % len(channels)]
        self._channel_rr_index += 1
        return str(getattr(chosen, "channel_id", "") or "")

    async def _discover_active_targets(
        self,
        scout: Any,
        mon_cfg: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Return 1 target (`KY4DWumZffg`) during Phase 1 campaign, or up to `max_concurrent_lives` BR lives in Phase 2."""
        raw_campaign = self.config.campaign if isinstance(self.config.campaign, dict) else {}
        c_block = raw_campaign.get("campaign", raw_campaign) if isinstance(raw_campaign, dict) else {}

        # Phase 1: Official 25h YouTube x GTA VI broadcast (exclusive single source)
        if self.config.real_oficial_enabled and c_block and not scout._is_campaign_blocked_now(c_block):
            auth_live = c_block.get("authorized_live", {}) if isinstance(c_block, dict) else {}
            url = str(auth_live.get("url") or "https://www.youtube.com/watch?v=KY4DWumZffg").strip()
            return [
                {
                    "stream_key": "ky4_official",
                    "target_url": url,
                    "creator_handle": "@YouTube",
                    "streamer_name": "Skorpion & YouTube GTA VI",
                    "for_campaign": True,
                    "twitch_login": "",
                }
            ]

        # Phase 2: Post-25h — Discover ALL currently LIVE Brazilian streamers (cached every 3m)
        max_concurrent = max(1, int(mon_cfg.get("max_concurrent_lives", 5)))
        refresh_sec = max(30.0, float(mon_cfg.get("live_discovery_refresh_sec", 180.0)))
        now_mono = time.monotonic()

        if (
            self._cached_live_targets
            and self._last_discovery_mono > 0.0
            and (now_mono - self._last_discovery_mono) < refresh_sec
        ):
            return self._cached_live_targets[:max_concurrent]

        _, blacklist, blocked_kw = scout._load_creator_lists()
        live_cands = await scout.discover_br_streamer_streams(
            blacklist,
            blocked_kw,
            max_streamers=10,
        )

        targets: list[dict[str, Any]] = []
        seen_handles: set[str] = set()
        for cand in live_cands:
            if not cand.metadata.get("is_live"):
                continue
            handle_norm = (cand.creator_handle or "").strip().lower()
            if not handle_norm or handle_norm in seen_handles:
                continue
            seen_handles.add(handle_norm)

            t_url = str(cand.metadata.get("canonical_url") or cand.url.split("#", 1)[0]).strip()
            if not t_url:
                continue
            tw_login = ""
            if "twitch.tv/" in t_url.lower():
                tw_login = t_url.rstrip("/").split("/")[-1].split("?")[0]

            s_key = re.sub(r"[^A-Za-z0-9_-]", "_", cand.creator_handle.lstrip("@") or "live")[:24]
            s_name = str(cand.metadata.get("streamer_name") or cand.creator_handle.lstrip("@"))
            targets.append(
                {
                    "stream_key": s_key,
                    "target_url": t_url,
                    "creator_handle": cand.creator_handle,
                    "streamer_name": s_name,
                    "for_campaign": False,
                    "twitch_login": tw_login,
                }
            )
            if len(targets) >= max_concurrent:
                break

        # Keyless Twitch Public GQL supplement (ported from streams.py/twitch.py) to fill remaining live slots!
        if len(targets) < max_concurrent and not self.config.dry_run:
            try:
                from gta6_agents.agents.trend_radar import discover_twitch_live_gql

                gql_hits = await discover_twitch_live_gql(
                    game_names=["Grand Theft Auto VI", "GTA VI"],
                    language_filter="pt",
                    limit=max_concurrent * 2,
                )
                for hit in gql_hits:
                    if len(targets) >= max_concurrent:
                        break
                    h_norm = f"@{hit.channel_login.lower()}"
                    if h_norm in seen_handles or hit.channel_login.lower() in blacklist:
                        continue
                    seen_handles.add(h_norm)
                    s_key = re.sub(r"[^A-Za-z0-9_-]", "_", hit.channel_login)[:24] or "tw_live"
                    targets.append(
                        {
                            "stream_key": s_key,
                            "target_url": hit.url,
                            "creator_handle": f"@{hit.channel_login}",
                            "streamer_name": hit.display_name,
                            "for_campaign": False,
                            "twitch_login": hit.channel_login,
                        }
                    )
            except Exception as exc:
                logger.debug("Keyless Twitch GQL supplement skipped: %s", exc)

        self._cached_live_targets = targets
        self._last_discovery_mono = now_mono
        return targets

    async def _poll_single_target(
        self,
        target: dict[str, Any],
        mon_cfg: dict[str, Any],
    ) -> dict[str, Any]:
        """Buffer & evaluate the newest 6s chunk of a single live stream inside the concurrent pool."""
        stream_key = str(target["stream_key"])
        target_url = str(target["target_url"])
        creator_handle = str(target["creator_handle"])
        streamer_name = str(target["streamer_name"])
        for_campaign = bool(target["for_campaign"])
        twitch_login = str(target.get("twitch_login") or "")
        spike_threshold = float(mon_cfg.get("spike_threshold", 58.0))
        stream_copy = bool(mon_cfg.get("stream_copy_ingest", True))
        per_streamer_cd_sec = float(mon_cfg.get("min_minutes_per_streamer", 12.0)) * 60.0

        ring = self._buffers.get(stream_key)
        if ring is None:
            ring = RollingLiveRingBuffer(
                stream_slug=stream_key,
                buffer_dir=self.config.work_dir / "live_rings",
                segment_sec=int(mon_cfg.get("ring_segment_sec", 6)),
                wrap_count=int(mon_cfg.get("ring_wrap_segments", 24)),
            )
            self._buffers[stream_key] = ring

        buffering_ok = await ring.ensure_buffering(target_url, stream_copy=stream_copy)
        if not buffering_ok:
            return {"stream_key": stream_key, "streamer": streamer_name, "status": "stream_offline"}

        chunks = ring.list_completed_chunks()
        if not chunks:
            return {"stream_key": stream_key, "streamer": streamer_name, "status": "warming_ring_buffer"}

        newest_chunk = chunks[-1]
        newest_mtime = newest_chunk.stat().st_mtime
        if newest_mtime <= self._last_analyzed_mtime.get(stream_key, 0.0):
            return {"stream_key": stream_key, "streamer": streamer_name, "status": "awaiting_next_chunk"}
        self._last_analyzed_mtime[stream_key] = newest_mtime

        # Check per-streamer anti-spam cooldown (still update vocal baseline on calm chunks if needed)
        last_st_clip = self._last_streamer_clip_mono.get(stream_key, 0.0)
        if last_st_clip > 0.0 and (time.monotonic() - last_st_clip) < per_streamer_cd_sec:
            return {"stream_key": stream_key, "streamer": streamer_name, "status": "streamer_cooldown"}

        analysis, instant_cand = await self.evaluate_buffered_chunk(
            stream_key=stream_key,
            canonical_url=target_url,
            creator_handle=creator_handle,
            streamer_name=streamer_name,
            chunk_path=newest_chunk,
            ring_buffer=ring,
            for_campaign=for_campaign,
            twitch_login=twitch_login,
            spike_threshold=spike_threshold,
            sample_chat=bool(twitch_login),
        )
        return {
            "stream_key": stream_key,
            "target_url": target_url,
            "streamer": streamer_name,
            "for_campaign": for_campaign,
            "status": "triggered" if instant_cand is not None else "monitoring",
            "analysis": analysis,
            "candidate": instant_cand,
        }

    async def poll_active_live_once(self, orchestrator: Any) -> dict[str, Any]:
        """20s multi-stream tick: monitors up to `max_concurrent_lives` simultaneous lives & routes hype clips across `ch1..ch5`."""
        mon_cfg = self._monitor_settings()
        if not bool(mon_cfg.get("enabled", True)) or self.config.dry_run:
            return {"status": "idle_dry_run"}

        cooldown_min = float(mon_cfg.get("min_minutes_between_instant_clips", 4.0))
        if (
            self._last_instant_clip_mono > 0.0
            and (time.monotonic() - self._last_instant_clip_mono) < (cooldown_min * 60.0)
        ):
            return {"status": "cooldown"}

        targets = await self._discover_active_targets(orchestrator.scout, mon_cfg)
        active_keys = {str(t["stream_key"]) for t in targets}
        pruned = await self.prune_inactive_buffers(active_keys)

        if not targets:
            return {"status": "no_active_live", "pruned_buffers": pruned}

        # Evaluate ALL simultaneous active lives in parallel!
        stream_outcomes = await asyncio.gather(
            *(self._poll_single_target(t, mon_cfg) for t in targets),
            return_exceptions=True,
        )

        valid_outcomes: list[dict[str, Any]] = []
        triggered_outcomes: list[dict[str, Any]] = []
        for out in stream_outcomes:
            if isinstance(out, Exception) or not isinstance(out, dict):
                continue
            valid_outcomes.append(out)
            if out.get("candidate") is not None and out.get("analysis") is not None:
                triggered_outcomes.append(out)

        # Persist multi-live telemetry snapshot for Dashboard & Telegram `/status`
        self.store.set_flag(
            "last_hype_monitor_json",
            json.dumps(
                {
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "active_simultaneous_lives": len(targets),
                    "pruned_offline_buffers": pruned,
                    "streams": [
                        {
                            "streamer": o.get("streamer"),
                            "status": o.get("status"),
                            "score": (
                                o["analysis"].composite_spike_score
                                if isinstance(o.get("analysis"), HypeSpikeAnalysis)
                                else 0.0
                            ),
                        }
                        for o in valid_outcomes
                    ],
                },
                ensure_ascii=False,
            ),
        )

        if not triggered_outcomes:
            return {
                "status": "monitoring",
                "active_simultaneous_lives": len(targets),
                "pruned_buffers": pruned,
            }

        # Rank simultaneous spikes highest-first & dispatch up to `max_instant_clips_per_tick` to distinct channels!
        triggered_outcomes.sort(
            key=lambda o: float(o["analysis"].composite_spike_score),
            reverse=True,
        )
        max_per_tick = max(1, int(mon_cfg.get("max_instant_clips_per_tick", 2)))
        selected_spikes = triggered_outcomes[:max_per_tick]

        channels = (
            orchestrator.multi_channel.load_channels()
            if getattr(orchestrator, "multi_channel", None) is not None
            else []
        )
        used_channels: set[str] = set()
        now_mono = time.monotonic()
        self._last_instant_clip_mono = now_mono

        dispatched: list[dict[str, Any]] = []
        for item in selected_spikes:
            cand: SourceCandidate = item["candidate"]
            analysis: HypeSpikeAnalysis = item["analysis"]
            s_key = str(item["stream_key"])
            self._last_streamer_clip_mono[s_key] = now_mono

            target_ch_id = self.select_channel_for_category(
                analysis.hype_category,
                channels,
                used_channels,
            )
            if target_ch_id:
                cand.metadata["target_channel_id"] = target_ch_id

            res = await orchestrator._process_candidate(
                cand,
                include_campaign=bool(item["for_campaign"]),
                demo_video_fallback=False,
            )
            dispatched.append(
                {
                    "streamer": item["streamer"],
                    "category": analysis.hype_category,
                    "score": analysis.composite_spike_score,
                    "channel_id": target_ch_id,
                    "result": res,
                }
            )

        first = dispatched[0]
        return {
            "status": "instant_clip_triggered",
            "active_simultaneous_lives": len(targets),
            "dispatched_count": len(dispatched),
            "category": first["category"],
            "score": first["score"],
            "channel_id": first["channel_id"],
            "dispatched": dispatched,
        }
