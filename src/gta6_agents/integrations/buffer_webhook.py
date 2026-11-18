"""Official Buffer API & Generic Make/n8n/Zapier Webhook Publisher for cross-posting."""

from __future__ import annotations

import logging
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

BUFFER_CREATE_UPDATE_URL = "https://api.bufferapp.com/1/updates/create.json"


class BufferWebhookPublisher:
    """Cross-posts Reels, TikTok, and Threads via Official Buffer API or Make/n8n/Zapier Webhook."""

    def __init__(self, config: AppConfig, timeout: float = 30.0) -> None:
        self.config = config
        self.timeout = timeout

    @property
    def has_buffer_credentials(self) -> bool:
        return bool(
            self.config.buffer_enabled
            and self.config.buffer_access_token.strip()
            and self.config.buffer_profile_ids
        )

    @property
    def has_webhook(self) -> bool:
        return bool(self.config.webhook_publisher_url.strip())

    @staticmethod
    def build_webhook_payload(
        plan: ClipPlan,
        clip: RenderedClip | None,
        copy: PlatformCopy,
    ) -> dict[str, Any]:
        """Constructs a structured JSON automation payload for Make, n8n, or Zapier."""
        return {
            "event": "gta6_agents.publish",
            "timestamp": utc_now().isoformat(),
            "platform": copy.platform.value,
            "plan_id": plan.id,
            "pillar": plan.pillar.value,
            "language": plan.language,
            "title": copy.title or plan.hook_on_screen,
            "caption": copy.caption,
            "formatted_text": copy.formatted_text(),
            "hashtags": copy.hashtags,
            "credit_line": copy.credit_line,
            "spoiler_tag_required": plan.spoiler_tag_required,
            "campaign_slug": plan.campaign_slug,
            "source": {
                "id": plan.source.id,
                "title": plan.source.title,
                "url": plan.source.url,
                "creator_handle": plan.source.creator_handle,
                "is_official": plan.source.is_official,
            },
            "clip": (
                {
                    "id": clip.id,
                    "video_path": clip.video_path,
                    "thumbnail_path": clip.thumbnail_path,
                    "duration_sec": clip.duration_sec,
                    "width": clip.width,
                    "height": clip.height,
                }
                if clip
                else None
            ),
        }

    async def _publish_via_buffer(
        self,
        http: httpx.AsyncClient,
        plan: ClipPlan,
        clip: RenderedClip | None,
        copy: PlatformCopy,
    ) -> PublicationRecord:
        text = copy.formatted_text()
        form_items: list[tuple[str, str]] = [
            ("text", text),
            ("now", "true"),
        ]
        for pid in self.config.buffer_profile_ids:
            form_items.append(("profile_ids[]", pid))

        public_media_url = plan.source.metadata.get("public_video_url")
        if isinstance(public_media_url, str) and public_media_url.startswith("https://"):
            form_items.append(("media[video]", public_media_url))

        resp = await http.post(
            BUFFER_CREATE_UPDATE_URL,
            headers={
                "Authorization": f"Bearer {self.config.buffer_access_token.strip()}",
            },
            data=form_items,
        )
        resp.raise_for_status()
        data = resp.json()
        updates = data.get("updates") or []
        first_update = updates[0] if updates and isinstance(updates[0], dict) else {}
        update_id = str(first_update.get("id") or data.get("id") or f"buf_{plan.id[-8:]}")

        return PublicationRecord(
            plan_id=plan.id,
            clip_id=clip.id if clip else None,
            platform=copy.platform,
            status="published" if data.get("success", True) else "scheduled",
            external_post_id=update_id,
            public_url=first_update.get("service_link"),
            caption_text=text,
            published_at=utc_now(),
            campaign_slug=plan.campaign_slug,
        )

    async def _publish_via_webhook(
        self,
        http: httpx.AsyncClient,
        plan: ClipPlan,
        clip: RenderedClip | None,
        copy: PlatformCopy,
    ) -> PublicationRecord:
        payload = self.build_webhook_payload(plan, clip, copy)
        resp = await http.post(
            self.config.webhook_publisher_url.strip(),
            headers={"Content-Type": "application/json", "User-Agent": "gta6-agents/1.0"},
            json=payload,
        )
        resp.raise_for_status()

        ext_id = f"webhook_{plan.id[-8:]}"
        pub_url: str | None = None
        if "application/json" in resp.headers.get("content-type", "").lower():
            try:
                resp_json = resp.json()
                if isinstance(resp_json, dict):
                    ext_id = str(resp_json.get("id") or resp_json.get("execution_id") or ext_id)
                    if isinstance(resp_json.get("public_url"), str):
                        pub_url = resp_json["public_url"]
            except ValueError:
                pass

        return PublicationRecord(
            plan_id=plan.id,
            clip_id=clip.id if clip else None,
            platform=copy.platform,
            status="published",
            external_post_id=ext_id,
            public_url=pub_url,
            caption_text=copy.formatted_text(),
            published_at=utc_now(),
            campaign_slug=plan.campaign_slug,
        )

    async def publish(
        self,
        plan: ClipPlan,
        clip: RenderedClip | None,
        copy: PlatformCopy,
        target_platform: Platform | None = None,
    ) -> PublicationRecord:
        platform = target_platform or copy.platform
        effective_copy = (
            copy if copy.platform == platform else copy.model_copy(update={"platform": platform})
        )
        caption_text = effective_copy.formatted_text()

        if self.config.dry_run:
            sim_id = f"dryrun_bw_{platform.value}_{plan.id[-6:]}"
            return PublicationRecord(
                plan_id=plan.id,
                clip_id=clip.id if clip else None,
                platform=platform,
                status="dry_run",
                external_post_id=sim_id,
                caption_text=caption_text,
                published_at=utc_now(),
                campaign_slug=plan.campaign_slug,
            )

        if not self.has_buffer_credentials and not self.has_webhook:
            return PublicationRecord(
                plan_id=plan.id,
                clip_id=clip.id if clip else None,
                platform=platform,
                status="queued",
                caption_text=caption_text,
                campaign_slug=plan.campaign_slug,
                error="Neither Buffer API credentials nor WEBHOOK_PUBLISHER_URL configured yet.",
            )

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                if self.has_webhook:
                    return await self._publish_via_webhook(http, plan, clip, effective_copy)
                return await self._publish_via_buffer(http, plan, clip, effective_copy)
        except Exception as exc:
            logger.exception("Buffer/Webhook publish failed for %s (plan %s)", platform.value, plan.id)
            return PublicationRecord(
                plan_id=plan.id,
                clip_id=clip.id if clip else None,
                platform=platform,
                status="failed",
                caption_text=caption_text,
                campaign_slug=plan.campaign_slug,
                error=str(exc),
            )
