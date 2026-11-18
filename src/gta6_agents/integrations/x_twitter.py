"""Official X (Twitter) API v2 Publisher with stdlib OAuth 1.0a HMAC-SHA1 & chunked video upload."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urlparse, urlunparse

import httpx

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

X_TWEETS_URL = "https://api.x.com/2/tweets"
X_MEDIA_UPLOAD_URL = "https://upload.twitter.com/1.1/media/upload.json"
CHUNK_SIZE_BYTES = 4 * 1024 * 1024  # 4 MiB chunks for APPEND


def _percent_encode(value: str) -> str:
    """RFC 3986 / RFC 5849 OAuth 1.0a percent encoding."""
    return quote(str(value), safe="-._~")


def build_oauth1_header(
    method: str,
    url: str,
    consumer_key: str,
    consumer_secret: str,
    access_token: str,
    access_token_secret: str,
    form_params: dict[str, str] | None = None,
    nonce: str | None = None,
    timestamp: str | None = None,
) -> str:
    """Generates an RFC 5849 OAuth 1.0a Authorization header using stdlib hmac + hashlib."""
    oauth_params: dict[str, str] = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": nonce or secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": timestamp or str(int(time.time())),
        "oauth_token": access_token,
        "oauth_version": "1.0",
    }

    parsed = urlparse(url)
    normalized_netloc = parsed.netloc.lower()
    if normalized_netloc.endswith(":80") and parsed.scheme == "http":
        normalized_netloc = normalized_netloc[:-3]
    elif normalized_netloc.endswith(":443") and parsed.scheme == "https":
        normalized_netloc = normalized_netloc[:-4]
    base_url = urlunparse((parsed.scheme.lower(), normalized_netloc, parsed.path, "", "", ""))

    sig_pairs: list[tuple[str, str]] = []
    for k, v in parse_qsl(parsed.query, keep_blank_values=True):
        sig_pairs.append((_percent_encode(k), _percent_encode(v)))
    for k, v in oauth_params.items():
        sig_pairs.append((_percent_encode(k), _percent_encode(v)))
    if form_params:
        for k, v in form_params.items():
            sig_pairs.append((_percent_encode(k), _percent_encode(v)))

    sig_pairs.sort()
    param_string = "&".join(f"{k}={v}" for k, v in sig_pairs)
    signature_base = "&".join(
        [
            method.upper(),
            _percent_encode(base_url),
            _percent_encode(param_string),
        ]
    )
    signing_key = f"{_percent_encode(consumer_secret)}&{_percent_encode(access_token_secret)}"
    digest = hmac.new(
        signing_key.encode("ascii"),
        signature_base.encode("ascii"),
        hashlib.sha1,
    ).digest()
    oauth_params["oauth_signature"] = base64.b64encode(digest).decode("ascii")

    header_parts = [
        f'{_percent_encode(k)}="{_percent_encode(v)}"' for k, v in sorted(oauth_params.items())
    ]
    return "OAuth " + ", ".join(header_parts)


class XTwitterPublisher:
    """Publishes text & native video posts via Official X API v2 + chunked upload.twitter.com."""

    def __init__(self, config: AppConfig, timeout: float = 60.0) -> None:
        self.config = config
        self.timeout = timeout

    @property
    def has_credentials(self) -> bool:
        return bool(
            self.config.x_api_key.strip()
            and self.config.x_api_secret.strip()
            and self.config.x_access_token.strip()
            and self.config.x_access_token_secret.strip()
        )

    def _auth_header(
        self,
        method: str,
        url: str,
        form_params: dict[str, str] | None = None,
    ) -> str:
        return build_oauth1_header(
            method=method,
            url=url,
            consumer_key=self.config.x_api_key.strip(),
            consumer_secret=self.config.x_api_secret.strip(),
            access_token=self.config.x_access_token.strip(),
            access_token_secret=self.config.x_access_token_secret.strip(),
            form_params=form_params,
        )

    @staticmethod
    def _format_tweet_text(plan: ClipPlan, copy: PlatformCopy) -> str:
        text = copy.formatted_text().strip()
        if not text:
            text = (plan.hook_on_screen or plan.source.title).strip()
        if plan.spoiler_tag_required and "SPOILER" not in text.upper():
            text = f"[SPOILER] {text}"
        if len(text) <= 280:
            return text
        return text[:277].rstrip() + "..."

    async def upload_video_chunked(self, http: httpx.AsyncClient, video_path: Path) -> str:
        """Uploads an MP4 video via INIT -> APPEND -> FINALIZE -> STATUS on upload.twitter.com."""
        total_bytes = video_path.stat().st_size

        # 1. INIT
        init_form = {
            "command": "INIT",
            "total_bytes": str(total_bytes),
            "media_type": "video/mp4",
            "media_category": "tweet_video",
        }
        init_resp = await http.post(
            X_MEDIA_UPLOAD_URL,
            headers={"Authorization": self._auth_header("POST", X_MEDIA_UPLOAD_URL, init_form)},
            data=init_form,
        )
        init_resp.raise_for_status()
        media_id = str(init_resp.json()["media_id_string"])

        # 2. APPEND chunks (multipart/form-data: OAuth 1.0a signs only oauth_* params)
        segment_index = 0
        with video_path.open("rb") as fh:
            while True:
                chunk = fh.read(CHUNK_SIZE_BYTES)
                if not chunk:
                    break
                append_resp = await http.post(
                    X_MEDIA_UPLOAD_URL,
                    headers={"Authorization": self._auth_header("POST", X_MEDIA_UPLOAD_URL)},
                    data={
                        "command": "APPEND",
                        "media_id": media_id,
                        "segment_index": str(segment_index),
                    },
                    files={"media": ("clip.mp4", chunk, "application/octet-stream")},
                )
                append_resp.raise_for_status()
                segment_index += 1

        # 3. FINALIZE
        finalize_form = {
            "command": "FINALIZE",
            "media_id": media_id,
        }
        fin_resp = await http.post(
            X_MEDIA_UPLOAD_URL,
            headers={"Authorization": self._auth_header("POST", X_MEDIA_UPLOAD_URL, finalize_form)},
            data=finalize_form,
        )
        fin_resp.raise_for_status()
        fin_data: dict[str, Any] = fin_resp.json()

        # 4. STATUS polling if async video processing is pending
        proc_info = fin_data.get("processing_info")
        polls = 0
        while isinstance(proc_info, dict) and polls < 20:
            state = proc_info.get("state")
            if state == "succeeded":
                break
            if state == "failed":
                err_info = proc_info.get("error", {})
                raise RuntimeError(f"X video processing failed: {err_info}")
            wait_secs = min(max(int(proc_info.get("check_after_secs", 3)), 1), 10)
            await asyncio.sleep(wait_secs)
            status_url = f"{X_MEDIA_UPLOAD_URL}?command=STATUS&media_id={media_id}"
            status_resp = await http.get(
                status_url,
                headers={"Authorization": self._auth_header("GET", status_url)},
            )
            status_resp.raise_for_status()
            proc_info = status_resp.json().get("processing_info")
            polls += 1

        return media_id

    async def publish(
        self,
        plan: ClipPlan,
        clip: RenderedClip | None,
        copy: PlatformCopy,
    ) -> PublicationRecord:
        tweet_text = self._format_tweet_text(plan, copy)

        if self.config.dry_run:
            sim_id = f"dryrun_x_{plan.id[-8:]}"
            return PublicationRecord(
                plan_id=plan.id,
                clip_id=clip.id if clip else None,
                platform=Platform.X,
                status="dry_run",
                external_post_id=sim_id,
                public_url=f"https://x.com/i/web/status/{sim_id}",
                caption_text=tweet_text,
                published_at=utc_now(),
                campaign_slug=plan.campaign_slug,
            )

        if not self.has_credentials:
            return PublicationRecord(
                plan_id=plan.id,
                clip_id=clip.id if clip else None,
                platform=Platform.X,
                status="queued",
                caption_text=tweet_text,
                campaign_slug=plan.campaign_slug,
                error="X OAuth 1.0a credentials not configured yet.",
            )

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                media_id: str | None = None
                if clip is not None and Path(clip.video_path).is_file():
                    media_id = await self.upload_video_chunked(http, Path(clip.video_path))

                payload: dict[str, Any] = {"text": tweet_text}
                if media_id:
                    payload["media"] = {"media_ids": [media_id]}

                resp = await http.post(
                    X_TWEETS_URL,
                    headers={
                        "Authorization": self._auth_header("POST", X_TWEETS_URL),
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                resp.raise_for_status()
                body = resp.json()
                tweet_id = str(body.get("data", {}).get("id", "")).strip()
                if not tweet_id:
                    raise RuntimeError(f"X API v2 returned no tweet ID: {body}")

                return PublicationRecord(
                    plan_id=plan.id,
                    clip_id=clip.id if clip else None,
                    platform=Platform.X,
                    status="published",
                    external_post_id=tweet_id,
                    public_url=f"https://x.com/i/web/status/{tweet_id}",
                    caption_text=tweet_text,
                    published_at=utc_now(),
                    campaign_slug=plan.campaign_slug,
                )
        except Exception as exc:
            logger.exception("X API v2 publish failed for plan %s", plan.id)
            return PublicationRecord(
                plan_id=plan.id,
                clip_id=clip.id if clip else None,
                platform=Platform.X,
                status="failed",
                caption_text=tweet_text,
                campaign_slug=plan.campaign_slug,
                error=str(exc),
            )
