"""Official Meta Instagram Graph API (Reels) & TikTok Content Posting API v2 Publishers."""

from __future__ import annotations

import asyncio
import logging
import math
from pathlib import Path
from typing import Any

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

IG_GRAPH_BASE_URL = "https://graph.facebook.com/v21.0"
IG_RUPLOAD_BASE_URL = "https://rupload.facebook.com/ig-api-upload/v21.0"
TIKTOK_VIDEO_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
TIKTOK_MAX_SINGLE_CHUNK = 64 * 1024 * 1024  # 64 MiB
TIKTOK_DEFAULT_CHUNK = 10 * 1024 * 1024  # 10 MiB


class MetaTikTokPublisher:
    """Official Graph API Reels publisher & TikTok Content Posting API v2 publisher."""

    def __init__(self, config: AppConfig, timeout: float = 90.0) -> None:
        self.config = config
        self.timeout = timeout

    @property
    def has_instagram_credentials(self) -> bool:
        return bool(
            self.config.instagram_access_token.strip()
            and self.config.instagram_ig_user_id.strip()
        )

    @property
    def has_tiktok_credentials(self) -> bool:
        return bool(self.config.tiktok_access_token.strip())

    async def publish_instagram_reel(
        self,
        plan: ClipPlan,
        clip: RenderedClip | None,
        copy: PlatformCopy,
    ) -> PublicationRecord:
        """Publishes a Reel via `/{ig-user-id}/media` (`media_type=REELS`) -> `media_publish`."""
        caption = copy.formatted_text()[:2200]

        if self.config.dry_run:
            sim_id = f"dryrun_ig_{plan.id[-8:]}"
            return PublicationRecord(
                plan_id=plan.id,
                clip_id=clip.id if clip else None,
                platform=Platform.INSTAGRAM_REELS,
                status="dry_run",
                external_post_id=sim_id,
                public_url=f"https://www.instagram.com/reel/{sim_id}/",
                caption_text=caption,
                published_at=utc_now(),
                campaign_slug=plan.campaign_slug,
            )

        if not self.has_instagram_credentials:
            return PublicationRecord(
                plan_id=plan.id,
                clip_id=clip.id if clip else None,
                platform=Platform.INSTAGRAM_REELS,
                status="queued",
                caption_text=caption,
                campaign_slug=plan.campaign_slug,
                error="Instagram Graph API credentials (INSTAGRAM_ACCESS_TOKEN / IG_USER_ID) not set yet.",
            )

        public_video_url = plan.source.metadata.get("public_video_url")
        has_local_file = clip is not None and Path(clip.video_path).is_file()
        if not has_local_file and not (
            isinstance(public_video_url, str) and public_video_url.startswith("https://")
        ):
            return PublicationRecord(
                plan_id=plan.id,
                clip_id=clip.id if clip else None,
                platform=Platform.INSTAGRAM_REELS,
                status="failed",
                caption_text=caption,
                campaign_slug=plan.campaign_slug,
                error="No local rendered MP4 or public HTTPS video_url available for Instagram Reels.",
            )

        ig_user_id = self.config.instagram_ig_user_id.strip()
        access_token = self.config.instagram_access_token.strip()
        media_endpoint = f"{IG_GRAPH_BASE_URL}/{ig_user_id}/media"
        publish_endpoint = f"{IG_GRAPH_BASE_URL}/{ig_user_id}/media_publish"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                # Step 1: Create REELS media container (URL mode or Resumable Upload mode)
                container_params: dict[str, Any] = {
                    "media_type": "REELS",
                    "caption": caption,
                    "share_to_feed": "true",
                    "access_token": access_token,
                }
                use_resumable = not (
                    isinstance(public_video_url, str) and public_video_url.startswith("https://")
                )
                if use_resumable:
                    container_params["upload_type"] = "resumable"
                else:
                    container_params["video_url"] = public_video_url

                init_resp = await http.post(media_endpoint, data=container_params)
                init_resp.raise_for_status()
                init_data = init_resp.json()
                container_id = str(init_data.get("id", "")).strip()
                if not container_id:
                    raise RuntimeError(f"Instagram Graph API returned no container id: {init_data}")

                # Step 1b: Upload binary MP4 if using official Graph API resumable upload
                if use_resumable and clip is not None:
                    video_file = Path(clip.video_path)
                    file_size = video_file.stat().st_size
                    upload_uri = str(
                        init_data.get("uri") or f"{IG_RUPLOAD_BASE_URL}/{container_id}"
                    )
                    video_bytes = await asyncio.to_thread(video_file.read_bytes)
                    up_resp = await http.post(
                        upload_uri,
                        headers={
                            "Authorization": f"OAuth {access_token}",
                            "offset": "0",
                            "file_size": str(file_size),
                            "Content-Type": "application/octet-stream",
                        },
                        content=video_bytes,
                    )
                    up_resp.raise_for_status()

                # Step 2: Poll container readiness before calling media_publish
                status_url = f"{IG_GRAPH_BASE_URL}/{container_id}"
                for _ in range(20):
                    st_resp = await http.get(
                        status_url,
                        params={"fields": "status_code,status", "access_token": access_token},
                    )
                    st_resp.raise_for_status()
                    st_code = str(st_resp.json().get("status_code", "")).upper()
                    if st_code == "FINISHED":
                        break
                    if st_code in ("ERROR", "EXPIRED"):
                        raise RuntimeError(
                            f"Instagram Reels container processing failed: {st_resp.json()}"
                        )
                    await asyncio.sleep(3)

                # Step 3: Publish container via /{ig-user-id}/media_publish
                pub_resp = await http.post(
                    publish_endpoint,
                    data={"creation_id": container_id, "access_token": access_token},
                )
                pub_resp.raise_for_status()
                media_id = str(pub_resp.json().get("id", "")).strip()
                if not media_id:
                    raise RuntimeError(
                        f"Instagram media_publish returned no media id: {pub_resp.json()}"
                    )

                # Step 4: Retrieve canonical permalink when available
                permalink: str | None = None
                perm_resp = await http.get(
                    f"{IG_GRAPH_BASE_URL}/{media_id}",
                    params={"fields": "permalink", "access_token": access_token},
                )
                if perm_resp.is_success:
                    permalink = perm_resp.json().get("permalink")

                return PublicationRecord(
                    plan_id=plan.id,
                    clip_id=clip.id if clip else None,
                    platform=Platform.INSTAGRAM_REELS,
                    status="published",
                    external_post_id=media_id,
                    public_url=permalink or f"https://www.instagram.com/reels/{media_id}/",
                    caption_text=caption,
                    published_at=utc_now(),
                    campaign_slug=plan.campaign_slug,
                )
        except Exception as exc:
            logger.exception("Instagram Reels publish failed for plan %s", plan.id)
            return PublicationRecord(
                plan_id=plan.id,
                clip_id=clip.id if clip else None,
                platform=Platform.INSTAGRAM_REELS,
                status="failed",
                caption_text=caption,
                campaign_slug=plan.campaign_slug,
                error=str(exc),
            )

    async def publish_tiktok(
        self,
        plan: ClipPlan,
        clip: RenderedClip | None,
        copy: PlatformCopy,
    ) -> PublicationRecord:
        """Publishes a video via Official TikTok Content Posting API `/v2/post/publish/video/init/`."""
        caption = copy.formatted_text()[:2200]

        if self.config.dry_run:
            sim_id = f"dryrun_tt_{plan.id[-8:]}"
            return PublicationRecord(
                plan_id=plan.id,
                clip_id=clip.id if clip else None,
                platform=Platform.TIKTOK,
                status="dry_run",
                external_post_id=sim_id,
                public_url=f"https://www.tiktok.com/@gta6/video/{sim_id}",
                caption_text=caption,
                published_at=utc_now(),
                campaign_slug=plan.campaign_slug,
            )

        if not self.has_tiktok_credentials:
            return PublicationRecord(
                plan_id=plan.id,
                clip_id=clip.id if clip else None,
                platform=Platform.TIKTOK,
                status="queued",
                caption_text=caption,
                campaign_slug=plan.campaign_slug,
                error="TikTok Content Posting API token (TIKTOK_ACCESS_TOKEN) not configured yet.",
            )

        if clip is None or not Path(clip.video_path).is_file():
            return PublicationRecord(
                plan_id=plan.id,
                clip_id=clip.id if clip else None,
                platform=Platform.TIKTOK,
                status="failed",
                caption_text=caption,
                campaign_slug=plan.campaign_slug,
                error=f"Rendered MP4 file not found for TikTok upload: {clip.video_path if clip else None}",
            )

        video_file = Path(clip.video_path)
        file_size = video_file.stat().st_size
        if file_size <= TIKTOK_MAX_SINGLE_CHUNK:
            chunk_size = file_size
            total_chunks = 1
        else:
            chunk_size = TIKTOK_DEFAULT_CHUNK
            total_chunks = math.ceil(file_size / chunk_size)

        init_payload: dict[str, Any] = {
            "post_info": {
                "title": caption,
                "privacy_level": "PUBLIC_TO_EVERYONE",
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": file_size,
                "chunk_size": chunk_size,
                "total_chunk_count": total_chunks,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                init_resp = await http.post(
                    TIKTOK_VIDEO_INIT_URL,
                    headers={
                        "Authorization": f"Bearer {self.config.tiktok_access_token.strip()}",
                        "Content-Type": "application/json; charset=UTF-8",
                    },
                    json=init_payload,
                )
                init_resp.raise_for_status()
                resp_json = init_resp.json()
                err_obj = resp_json.get("error") or {}
                if err_obj.get("code") and err_obj.get("code") != "ok":
                    raise RuntimeError(f"TikTok init error: {err_obj}")

                data = resp_json.get("data") or {}
                publish_id = str(data.get("publish_id", "")).strip()
                upload_url = str(data.get("upload_url", "")).strip()
                if not publish_id or not upload_url:
                    raise RuntimeError(f"TikTok init missing publish_id or upload_url: {resp_json}")

                with video_file.open("rb") as fh:
                    for idx in range(total_chunks):
                        start = idx * chunk_size
                        chunk_bytes = fh.read(chunk_size)
                        end = start + len(chunk_bytes) - 1
                        put_resp = await http.put(
                            upload_url,
                            headers={
                                "Content-Type": "video/mp4",
                                "Content-Length": str(len(chunk_bytes)),
                                "Content-Range": f"bytes {start}-{end}/{file_size}",
                            },
                            content=chunk_bytes,
                        )
                        put_resp.raise_for_status()

                return PublicationRecord(
                    plan_id=plan.id,
                    clip_id=clip.id,
                    platform=Platform.TIKTOK,
                    status="published",
                    external_post_id=publish_id,
                    caption_text=caption,
                    published_at=utc_now(),
                    campaign_slug=plan.campaign_slug,
                )
        except Exception as exc:
            logger.exception("TikTok publish failed for plan %s", plan.id)
            return PublicationRecord(
                plan_id=plan.id,
                clip_id=clip.id,
                platform=Platform.TIKTOK,
                status="failed",
                caption_text=caption,
                campaign_slug=plan.campaign_slug,
                error=str(exc),
            )

    async def publish(
        self,
        plan: ClipPlan,
        clip: RenderedClip | None,
        copy: PlatformCopy,
    ) -> PublicationRecord:
        if copy.platform == Platform.INSTAGRAM_REELS:
            return await self.publish_instagram_reel(plan, clip, copy)
        if copy.platform == Platform.TIKTOK:
            return await self.publish_tiktok(plan, clip, copy)
        return PublicationRecord(
            plan_id=plan.id,
            clip_id=clip.id if clip else None,
            platform=copy.platform,
            status="failed",
            caption_text=copy.formatted_text(),
            campaign_slug=plan.campaign_slug,
            error=f"Unsupported platform for MetaTikTokPublisher: {copy.platform.value}",
        )
