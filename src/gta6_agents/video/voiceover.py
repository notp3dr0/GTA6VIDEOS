"""Voiceover synthesis supporting silent mode, Edge TTS, and ElevenLabs HTTP API."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import httpx

from gta6_agents.config import AppConfig

logger = logging.getLogger(__name__)

_DEFAULT_ELEVENLABS_VOICE_ID = "pNInz6obpgDQGcFmaJgB"


def _select_edge_voice(language: str, config: AppConfig) -> str:
    lang_norm = (language or "pt-BR").strip().lower()
    if lang_norm.startswith("en"):
        return config.edge_tts_voice_en or "en-US-ChristopherNeural"
    if lang_norm.startswith("pt"):
        return config.edge_tts_voice_pt or "pt-BR-AntonioNeural"
    return config.edge_tts_voice_pt or "pt-BR-AntonioNeural"


async def synthesize_voiceover(
    text: str,
    language: str,
    config: AppConfig,
    output_mp3: Path,
) -> Path | None:
    """Synthesize narration MP3 via configured provider, returning None when disabled or offline."""
    cleaned_text = " ".join((text or "").split())
    if not cleaned_text:
        return None

    mode = (config.voiceover_mode or "none").strip().lower()
    if mode in ("none", "off", "disabled", ""):
        return None

    output_mp3.parent.mkdir(parents=True, exist_ok=True)

    if mode == "edge_tts":
        try:
            import edge_tts

            voice = _select_edge_voice(language, config)
            proxy = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or None
            communicate = edge_tts.Communicate(
                cleaned_text,
                voice=voice,
                proxy=proxy,
                connect_timeout=8,
                receive_timeout=25,
            )
            await communicate.save(str(output_mp3))
            if output_mp3.exists() and output_mp3.stat().st_size > 0:
                return output_mp3
        except Exception as exc:
            logger.warning("edge_tts synthesis unavailable/offline (falling back to clip audio): %s", exc)
        output_mp3.unlink(missing_ok=True)
        return None

    if mode == "elevenlabs":
        api_key = (config.elevenlabs_api_key or "").strip()
        if not api_key:
            logger.warning("voiceover_mode='elevenlabs' selected without ELEVENLABS_API_KEY; skipping voiceover.")
            return None

        voice_id = (config.elevenlabs_voice_id or "").strip() or _DEFAULT_ELEVENLABS_VOICE_ID
        endpoint = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        headers = {
            "xi-api-key": api_key,
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
        }
        payload = {
            "text": cleaned_text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {
                "stability": 0.45,
                "similarity_boost": 0.78,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(endpoint, headers=headers, json=payload)
                resp.raise_for_status()
                if resp.content:
                    output_mp3.write_bytes(resp.content)
                    if output_mp3.stat().st_size > 0:
                        return output_mp3
        except Exception as exc:
            logger.warning("ElevenLabs TTS synthesis failed (falling back to clip audio): %s", exc)
        output_mp3.unlink(missing_ok=True)
        return None

    logger.warning("Unknown voiceover_mode '%s'; skipping voiceover synthesis.", config.voiceover_mode)
    return None
