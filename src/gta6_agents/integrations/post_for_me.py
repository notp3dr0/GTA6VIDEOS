"""Unified Post For Me (`api.postforme.dev/v1`) Multi-Account Publisher for TikTok & Instagram Reels.

Eliminates Meta/TikTok App Review wait times for the 5-channel × 120 posts/day operation:
1. Auto-discovers connected TikTok & Instagram `spc_...` account IDs via `GET /v1/social-accounts`
   matching each channel's `@handle` (or explicit env/YAML account ID override).
2. Uploads local FFmpeg-rendered `1080x1920` `.mp4` via `POST /v1/media/create-upload-url` -> `PUT upload_url`
   (zero watermarks added by Post For Me, preserving 100% Real Oficial Rule #10 compliance).
3. Publishes via `POST /v1/social-posts` (`placement: "reels"`, `share_to_feed: true` for Instagram).
4. Polls `GET /v1/social-post-results` to capture the live public permalink (`platform_data.url`)
   for immediate Real Oficial MCP registration and Telegram mobile notification.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx
import yaml

from gta6_agents.config import AppConfig
from gta6_agents.models import (
    ClipPlan,
    Platform,
    PlatformCopy,
    PublicationRecord,
    RenderedClip,
    utc_now,
)

logger = logging.getLogger(__name__)

DEFAULT_POST_FOR_ME_BASE_URL = "https://api.postforme.dev/v1"


class PostForMePublisher:
    """Publishes local 9:16 MP4 clips across 5 TikTok + 5 Instagram Reels accounts via Post For Me."""

    def __init__(self, config: AppConfig, timeout: float = 90.0) -> None:
        self.config = config
        self.timeout = timeout
        self.base_url = (
            getattr(config, "post_for_me_base_url", "") or DEFAULT_POST_FOR_ME_BASE_URL
        ).rstrip("/")
        self._accounts_cache: list[dict[str, Any]] = []
        self._accounts_cached_mono: float = 0.0
        self._channel_handles_cache: dict[str, str] | None = None

    @property
    def api_key(self) -> str:
        return (
            getattr(self.config, "post_for_me_api_key", "").strip()
            or os.getenv("POST_FOR_ME_API_KEY", "").strip()
            or os.getenv("POSTFORME_API_KEY", "").strip()
        )

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _channel_handle_map(self) -> dict[str, str]:
        """Map `channel_id` (e.g. `ch1_plantao_oficial`) -> normalized username (`cortesliveleonida`)."""
        if self._channel_handles_cache is not None:
            return self._channel_handles_cache
        mapping: dict[str, str] = {}
        matrix_path = self.config.root_dir / "config" / "channels_matrix.yaml"
        if matrix_path.exists():
            try:
                raw = yaml.safe_load(matrix_path.read_text(encoding="utf-8")) or {}
                for ch in raw.get("channels", []) or []:
                    if isinstance(ch, dict) and ch.get("handle"):
                        cid = ch.get("channel_id") or ch.get("id")
                        if cid:
                            mapping[str(cid).strip()] = str(ch["handle"]).strip().lstrip("@").lower()
            except Exception as exc:
                logger.debug("Could not read channels_matrix.yaml for PostForMe mapping: %s", exc)
        self._channel_handles_cache = mapping
        return mapping

    async def fetch_connected_accounts(self, http: httpx.AsyncClient) -> list[dict[str, Any]]:
        """Fetch & cache connected `social-accounts` from Post For Me (`spc_...`)."""
        now = time.monotonic()
        if self._accounts_cache and (now - self._accounts_cached_mono) < 600.0:
            return self._accounts_cache

        resp = await http.get(f"{self.base_url}/social-accounts", headers=self._headers())
        resp.raise_for_status()
        payload = resp.json()
        rows = payload.get("data", payload) if isinstance(payload, dict) else payload
        accounts = [a for a in (rows or []) if isinstance(a, dict)]
        self._accounts_cache = accounts
        self._accounts_cached_mono = now
        return accounts

    async def resolve_social_account_id(
        self,
        http: httpx.AsyncClient,
        platform: Platform,
        channel_id: str,
    ) -> str:
        """Resolve the target `spc_...` account ID by env override, `@handle` match, or platform fallback."""
        plat_key = "instagram" if platform == Platform.INSTAGRAM_REELS else "tiktok"
        ch_num = ""
        if channel_id.startswith("ch") and len(channel_id) >= 3 and channel_id[2].isdigit():
            ch_num = channel_id[2]

        # 1. Explicit per-channel env var override (e.g. POST_FOR_ME_IG_CH1=spc_xxx)
        if ch_num:
            env_prefix = "POST_FOR_ME_IG_CH" if plat_key == "instagram" else "POST_FOR_ME_TT_CH"
            explicit = os.getenv(f"{env_prefix}{ch_num}", "").strip()
            if explicit:
                return explicit

        # 2. Auto-discover via GET /v1/social-accounts matching channel `@handle`
        accounts = await self.fetch_connected_accounts(http)
        plat_accounts = [
            a
            for a in accounts
            if plat_key in str(a.get("platform", "")).lower()
            and str(a.get("status", "connected")).lower() != "disconnected"
        ]
        if not plat_accounts:
            return ""

        desired_username = self._channel_handle_map().get(channel_id, "").lower()
        if desired_username:
            for acc in plat_accounts:
                uname = str(acc.get("username") or acc.get("handle") or "").strip().lstrip("@").lower()
                if uname == desired_username:
                    return str(acc.get("id") or "").strip()

        # 3. Deterministic index fallback across connected accounts of that platform
        idx = max(0, int(ch_num) - 1) if ch_num else 0
        chosen = plat_accounts[idx % len(plat_accounts)]
        return str(chosen.get("id") or "").strip()

    async def upload_local_mp4(self, http: httpx.AsyncClient, video_path: Path) -> str:
        """Stage local `.mp4` via `POST /v1/media/create-upload-url` + binary `PUT` -> `media_url`."""
        init_resp = await http.post(
            f"{self.base_url}/media/create-upload-url",
            headers=self._headers(),
            json={"filename": video_path.name, "content_type": "video/mp4"},
        )
        init_resp.raise_for_status()
        init_data = init_resp.json()
        upload_url = str(init_data.get("upload_url") or "").strip()
        media_url = str(init_data.get("media_url") or init_data.get("url") or "").strip()
        if not upload_url or not media_url:
            raise RuntimeError(f"Post For Me /media/create-upload-url missing URLs: {init_data}")

        video_bytes = await asyncio.to_thread(video_path.read_bytes)
        put_resp = await http.put(
            upload_url,
            headers={"Content-Type": "video/mp4"},
            content=video_bytes,
        )
        put_resp.raise_for_status()
        return media_url

    async def _poll_live_permalink(
        self,
        http: httpx.AsyncClient,
        post_id: str,
        max_polls: int = 8,
    ) -> tuple[str | None, str | None]:
        """Poll `GET /v1/social-post-results?post_id=...` briefly to obtain the live TikTok/Reel permalink."""
        for _ in range(max_polls):
            await asyncio.sleep(3.0)
            try:
                res = await http.get(
                    f"{self.base_url}/social-post-results",
                    headers=self._headers(),
                    params={"post_id": post_id},
                )
                if not res.is_success:
                    continue
                body = res.json()
                items = body.get("data", body) if isinstance(body, dict) else body
                if not isinstance(items, list) or not items:
                    continue
                first = items[0]
                if not isinstance(first, dict):
                    continue
                if first.get("success") is False:
                    err = str(first.get("error") or "Post For Me reported publish error")
                    return None, err
                plat_data = first.get("platform_data") or {}
                live_url = str(
                    (plat_data.get("url") if isinstance(plat_data, dict) else "")
                    or first.get("url")
                    or ""
                ).strip()
                if live_url.startswith("https://"):
                    return live_url, None
            except Exception:
                continue
        return None, None

    async def publish(
        self,
        plan: ClipPlan,
        clip: RenderedClip | None,
        copy: PlatformCopy,
        target_platform: Platform,
    ) -> PublicationRecord:
        """Upload & publish a 9:16 clip to TikTok or Instagram Reels via Post For Me."""
        max_len = 2200
        caption = copy.formatted_text()[:max_len]
        channel_id = str(
            plan.source.metadata.get("channel_id")
            or plan.source.metadata.get("target_channel_id")
            or "ch1_plantao_oficial"
        )

        if self.config.dry_run:
            sim_id = f"pfm_dry_{target_platform.value[:2]}_{plan.id[-8:]}"
            fake_url = (
                f"https://www.instagram.com/reel/{sim_id}/"
                if target_platform == Platform.INSTAGRAM_REELS
                else f"https://www.tiktok.com/@cortesliveleonida/video/{sim_id}"
            )
            return PublicationRecord(
                plan_id=plan.id,
                clip_id=clip.id if clip else None,
                platform=target_platform,
                status="dry_run",
                external_post_id=sim_id,
                public_url=fake_url,
                caption_text=caption,
                published_at=utc_now(),
                campaign_slug=plan.campaign_slug,
            )

        if not self.has_credentials:
            return PublicationRecord(
                plan_id=plan.id,
                clip_id=clip.id if clip else None,
                platform=target_platform,
                status="queued",
                caption_text=caption,
                campaign_slug=plan.campaign_slug,
                error="POST_FOR_ME_API_KEY not configured.",
            )

        public_video_url = plan.source.metadata.get("public_video_url")
        has_local_file = clip is not None and Path(clip.video_path).is_file()
        if not has_local_file and not (
            isinstance(public_video_url, str) and public_video_url.startswith("https://")
        ):
            return PublicationRecord(
                plan_id=plan.id,
                clip_id=clip.id if clip else None,
                platform=target_platform,
                status="failed",
                caption_text=caption,
                campaign_slug=plan.campaign_slug,
                error="No local rendered MP4 or public video URL available for Post For Me.",
            )

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                account_id = await self.resolve_social_account_id(
                    http=http,
                    platform=target_platform,
                    channel_id=channel_id,
                )
                if not account_id:
                    return PublicationRecord(
                        plan_id=plan.id,
                        clip_id=clip.id if clip else None,
                        platform=target_platform,
                        status="failed",
                        caption_text=caption,
                        campaign_slug=plan.campaign_slug,
                        error=f"No connected {target_platform.value} account found in Post For Me for {channel_id}.",
                    )

                if isinstance(public_video_url, str) and public_video_url.startswith("https://"):
                    media_url = public_video_url
                else:
                    assert clip is not None
                    media_url = await self.upload_local_mp4(http, Path(clip.video_path))

                post_payload: dict[str, Any] = {
                    "caption": caption,
                    "media": [{"url": media_url}],
                    "social_accounts": [account_id],
                }
                if target_platform == Platform.INSTAGRAM_REELS:
                    post_payload["platform_configurations"] = {
                        "instagram": {
                            "placement": "reels",
                            "share_to_feed": True,
                        }
                    }

                post_resp = await http.post(
                    f"{self.base_url}/social-posts",
                    headers=self._headers(),
                    json=post_payload,
                )
                post_resp.raise_for_status()
                post_data = post_resp.json()
                post_id = str(post_data.get("id") or "").strip()

                live_url, poll_err = (
                    await self._poll_live_permalink(http, post_id) if post_id else (None, None)
                )
                if poll_err:
                    return PublicationRecord(
                        plan_id=plan.id,
                        clip_id=clip.id if clip else None,
                        platform=target_platform,
                        status="failed",
                        external_post_id=post_id or None,
                        caption_text=caption,
                        campaign_slug=plan.campaign_slug,
                        error=poll_err,
                    )

                return PublicationRecord(
                    plan_id=plan.id,
                    clip_id=clip.id if clip else None,
                    platform=target_platform,
                    status="published",
                    external_post_id=post_id or f"pfm_{plan.id[-8:]}",
                    public_url=live_url,
                    caption_text=caption,
                    published_at=utc_now(),
                    campaign_slug=plan.campaign_slug,
                )
        except Exception as exc:
            logger.error("Post For Me publish failed (%s / %s): %s", target_platform.value, channel_id, exc)
            return PublicationRecord(
                plan_id=plan.id,
                clip_id=clip.id if clip else None,
                platform=target_platform,
                status="failed",
                caption_text=caption,
                campaign_slug=plan.campaign_slug,
                error=str(exc),
            )
