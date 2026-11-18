"""Official YouTube Data API v3 Shorts Publisher (resumable upload via OAuth2 refresh token)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

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

YOUTUBE_TOKEN_URI = "https://oauth2.googleapis.com/token"
YOUTUBE_UPLOAD_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
GAMING_CATEGORY_ID = "20"


class YouTubeShortsPublisher:
    """Publishes vertical 9:16 clips to YouTube Shorts via the official YouTube Data API v3."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    @property
    def has_credentials(self) -> bool:
        return bool(
            self.config.youtube_client_id.strip()
            and self.config.youtube_client_secret.strip()
            and self.config.youtube_refresh_token.strip()
        )

    def _campaign_rules(self) -> dict[str, Any]:
        camp_root = self.config.campaign or {}
        camp = camp_root.get("campaign", camp_root)
        return camp.get("rules", {}) if isinstance(camp, dict) else {}

    def _build_title(self, plan: ClipPlan, copy: PlatformCopy) -> str:
        raw_title = (copy.title or plan.hook_on_screen or plan.source.title or "GTA 6 #Shorts").strip()
        if plan.spoiler_tag_required and "SPOILER" not in raw_title.upper():
            raw_title = f"[SPOILER] {raw_title}"
        if "#shorts" not in raw_title.lower():
            if len(raw_title) <= 92:
                raw_title = f"{raw_title} #Shorts"
            else:
                raw_title = f"{raw_title[:91].rstrip()} #Shorts"
        return raw_title[:100]

    def _build_description_and_tags(
        self,
        plan: ClipPlan,
        copy: PlatformCopy,
    ) -> tuple[str, list[str]]:
        description = copy.formatted_text()
        rules = self._campaign_rules()

        merged_tags: list[str] = list(copy.hashtags)
        if plan.for_real_oficial_campaign:
            for req_tag in rules.get("required_hashtags", ["#GTA6", "#GTAVI", "#Shorts"]):
                if req_tag not in merged_tags:
                    merged_tags.append(req_tag)
                if req_tag.lower() not in description.lower():
                    description = f"{description}\n{req_tag}".strip()

            footer = str(rules.get("required_description_footer", "")).strip()
            if footer and footer not in description:
                description = f"{description}\n\n{footer}".strip()

        if "#shorts" not in description.lower():
            description = f"{description}\n\n#Shorts #GTA6".strip()

        clean_tags: list[str] = []
        total_chars = 0
        for tag in merged_tags + ["GTA6", "GTAVI", "Shorts"]:
            normalized = tag.lstrip("#").strip()
            if not normalized or normalized.lower() in {t.lower() for t in clean_tags}:
                continue
            if total_chars + len(normalized) + 1 > 480:
                break
            clean_tags.append(normalized)
            total_chars += len(normalized) + 1

        return description[:5000], clean_tags

    def _upload_sync(
        self,
        video_path: Path,
        title: str,
        description: str,
        tags: list[str],
        language: str,
    ) -> str:
        """Runs the synchronous google-api-python-client resumable upload in a worker thread."""
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload
        except ImportError as exc:
            raise RuntimeError(
                "Optional YouTube dependencies are not installed. "
                "Install google-api-python-client and google-auth (`pip install gta6-agents[youtube]`)."
            ) from exc

        creds = Credentials(
            token=None,
            refresh_token=self.config.youtube_refresh_token.strip(),
            token_uri=YOUTUBE_TOKEN_URI,
            client_id=self.config.youtube_client_id.strip(),
            client_secret=self.config.youtube_client_secret.strip(),
            scopes=YOUTUBE_UPLOAD_SCOPES,
        )
        creds.refresh(Request())

        youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
        body: dict[str, Any] = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": GAMING_CATEGORY_ID,
                "defaultLanguage": language,
                "defaultAudioLanguage": language,
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(
            str(video_path),
            mimetype="video/mp4",
            chunksize=4 * 1024 * 1024,
            resumable=True,
        )
        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        response: dict[str, Any] | None = None
        while response is None:
            _, response = request.next_chunk()

        video_id = str(response.get("id", "")).strip()
        if not video_id:
            raise RuntimeError(f"YouTube upload completed without returning a video ID: {response}")
        return video_id

    async def publish(
        self,
        plan: ClipPlan,
        clip: RenderedClip | None,
        copy: PlatformCopy,
    ) -> PublicationRecord:
        title = self._build_title(plan, copy)
        description, tags = self._build_description_and_tags(plan, copy)
        campaign_slug = plan.campaign_slug or (
            self.config.real_oficial_campaign_slug if plan.for_real_oficial_campaign else None
        )

        if self.config.dry_run:
            sim_id = f"dryrun_{plan.id[-8:]}"
            return PublicationRecord(
                plan_id=plan.id,
                clip_id=clip.id if clip else None,
                platform=Platform.YOUTUBE_SHORTS,
                status="dry_run",
                external_post_id=sim_id,
                public_url=f"https://www.youtube.com/shorts/{sim_id}",
                caption_text=f"{title}\n\n{description}",
                published_at=utc_now(),
                campaign_slug=campaign_slug,
            )

        if not self.has_credentials:
            return PublicationRecord(
                plan_id=plan.id,
                clip_id=clip.id if clip else None,
                platform=Platform.YOUTUBE_SHORTS,
                status="queued",
                caption_text=f"{title}\n\n{description}",
                campaign_slug=campaign_slug,
                error="YouTube OAuth2 refresh token credentials not configured yet.",
            )

        if clip is None or not Path(clip.video_path).is_file():
            return PublicationRecord(
                plan_id=plan.id,
                clip_id=clip.id if clip else None,
                platform=Platform.YOUTUBE_SHORTS,
                status="failed",
                caption_text=f"{title}\n\n{description}",
                campaign_slug=campaign_slug,
                error=f"Rendered video file not found for YouTube upload: {clip.video_path if clip else None}",
            )

        try:
            video_id = await asyncio.to_thread(
                self._upload_sync,
                Path(clip.video_path),
                title,
                description,
                tags,
                plan.language,
            )
            return PublicationRecord(
                plan_id=plan.id,
                clip_id=clip.id,
                platform=Platform.YOUTUBE_SHORTS,
                status="published",
                external_post_id=video_id,
                public_url=f"https://www.youtube.com/shorts/{video_id}",
                caption_text=f"{title}\n\n{description}",
                published_at=utc_now(),
                campaign_slug=campaign_slug,
            )
        except Exception as exc:
            logger.exception("YouTube Shorts upload failed for plan %s", plan.id)
            return PublicationRecord(
                plan_id=plan.id,
                clip_id=clip.id,
                platform=Platform.YOUTUBE_SHORTS,
                status="failed",
                caption_text=f"{title}\n\n{description}",
                campaign_slug=campaign_slug,
                error=str(exc),
            )
