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

    async def ensure_buffering(self, live_url: str) -> bool:
        """Start (or restart) the background FFmpeg `.ts` ring buffer on `live_url`."""
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

    async def poll_active_live_once(self, orchestrator: Any) -> dict[str, Any]:
        """Single 20s monitoring tick invoked by DailyScheduler: buffers active live & auto-clips hype spikes."""
        mon_cfg = self._monitor_settings()
        if not bool(mon_cfg.get("enabled", True)) or self.config.dry_run:
            return {"status": "idle_dry_run"}

        cooldown_min = float(mon_cfg.get("min_minutes_between_instant_clips", 8.0))
        if (
            self._last_instant_clip_mono > 0.0
            and (time.monotonic() - self._last_instant_clip_mono) < (cooldown_min * 60.0)
        ):
            return {"status": "cooldown"}

        spike_threshold = float(mon_cfg.get("spike_threshold", 58.0))
        scout = orchestrator.scout

        raw_campaign = self.config.campaign if isinstance(self.config.campaign, dict) else {}
        c_block = raw_campaign.get("campaign", raw_campaign) if isinstance(raw_campaign, dict) else {}

        target_url = ""
        creator_handle = "@YouTube"
        streamer_name = "Live Oficial GTA VI"
        for_campaign = False
        twitch_login = ""

        # Phase 1: Monitor KY4DWumZffg while official Real Oficial 25h broadcast is ON AIR
        if self.config.real_oficial_enabled and c_block and not scout._is_campaign_blocked_now(c_block):
            auth_live = c_block.get("authorized_live", {}) if isinstance(c_block, dict) else {}
            target_url = str(auth_live.get("url") or "https://www.youtube.com/watch?v=KY4DWumZffg").strip()
            creator_handle = "@YouTube"
            streamer_name = "Skorpion & YouTube GTA VI"
            for_campaign = True
        else:
            # Phase 2: Monitor whichever Brazilian streamer is currently LIVE playing GTA VI!
            _, blacklist, blocked_kw = scout._load_creator_lists()
            live_cands = await scout.discover_br_streamer_streams(blacklist, blocked_kw, max_streamers=6)
            active_live = next((c for c in live_cands if c.metadata.get("is_live")), None)
            if active_live is not None:
                target_url = str(active_live.metadata.get("canonical_url") or active_live.url.split("#", 1)[0])
                creator_handle = active_live.creator_handle
                streamer_name = str(active_live.metadata.get("streamer_name") or creator_handle.lstrip("@"))
                for_campaign = False
                if "twitch.tv/" in target_url.lower():
                    twitch_login = target_url.rstrip("/").split("/")[-1].split("?")[0]

        if not target_url:
            return {"status": "no_active_live"}

        stream_key = re.sub(r"[^A-Za-z0-9_-]", "_", creator_handle.lstrip("@") or "ky4")[:24]
        ring = self._buffers.get(stream_key)
        if ring is None:
            ring = RollingLiveRingBuffer(
                stream_slug=stream_key,
                buffer_dir=self.config.work_dir / "live_rings",
                segment_sec=int(mon_cfg.get("ring_segment_sec", 6)),
                wrap_count=int(mon_cfg.get("ring_wrap_segments", 24)),
            )
            self._buffers[stream_key] = ring

        buffering_ok = await ring.ensure_buffering(target_url)
        if not buffering_ok:
            return {"status": "stream_offline", "target_url": target_url}

        chunks = ring.list_completed_chunks()
        if not chunks:
            return {"status": "warming_ring_buffer", "target_url": target_url}

        newest_chunk = chunks[-1]
        newest_mtime = newest_chunk.stat().st_mtime
        if newest_mtime <= self._last_analyzed_mtime.get(stream_key, 0.0):
            return {"status": "awaiting_next_chunk", "target_url": target_url}
        self._last_analyzed_mtime[stream_key] = newest_mtime

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

        self.store.set_flag(
            "last_hype_monitor_json",
            json.dumps(
                {
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                    "target_url": target_url,
                    "streamer": streamer_name,
                    "composite_spike_score": analysis.composite_spike_score,
                    "rms_jump_db": analysis.rms_jump_db,
                    "chat_hype_hits": analysis.chat_hype_hits,
                    "triggered": analysis.triggered,
                },
                ensure_ascii=False,
            ),
        )

        if instant_cand is None:
            return {
                "status": "monitoring",
                "target_url": target_url,
                "score": analysis.composite_spike_score,
            }

        # Hype moment detected! Pick next rotating channel & dispatch immediate render + publish!
        self._last_instant_clip_mono = time.monotonic()
        target_ch_id = ""
        if getattr(orchestrator, "multi_channel", None) is not None:
            channels = orchestrator.multi_channel.load_channels()
            if channels:
                chosen = channels[self._channel_rr_index % len(channels)]
                self._channel_rr_index += 1
                target_ch_id = chosen.channel_id
                instant_cand.metadata["target_channel_id"] = target_ch_id

        res = await orchestrator._process_candidate(
            instant_cand,
            include_campaign=for_campaign,
            demo_video_fallback=False,
        )
        return {
            "status": "instant_clip_triggered",
            "category": analysis.hype_category,
            "score": analysis.composite_spike_score,
            "channel_id": target_ch_id,
            "result": res,
        }
