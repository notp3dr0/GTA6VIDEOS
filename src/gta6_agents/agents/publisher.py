"""Multi-Network Publisher Agent with anti-shadowban spacing & persistent publication ledger."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from gta6_agents.config import AppConfig
from gta6_agents.integrations.buffer_webhook import BufferWebhookPublisher
from gta6_agents.integrations.meta_tiktok import MetaTikTokPublisher
from gta6_agents.integrations.post_for_me import PostForMePublisher
from gta6_agents.integrations.x_twitter import XTwitterPublisher
from gta6_agents.integrations.youtube_shorts import YouTubeShortsPublisher
from gta6_agents.models import (
    CaptionBundle,
    ClipPlan,
    Platform,
    PlatformCopy,
    PublicationRecord,
    RenderedClip,
    utc_now,
)
from gta6_agents.storage import StateStore

logger = logging.getLogger(__name__)

DEFAULT_MIN_INTERVAL_MINUTES = 150


class PublisherAgent:
    """Orchestrates official multi-platform publishing with anti-shadowban cadence protection."""

    def __init__(self, config: AppConfig, store: StateStore) -> None:
        self.config = config
        self.store = store
        self.youtube = YouTubeShortsPublisher(config)
        self.x_twitter = XTwitterPublisher(config)
        self.post_for_me = PostForMePublisher(config)
        self.meta_tiktok = MetaTikTokPublisher(config)
        self.buffer_webhook = BufferWebhookPublisher(config)

    def _min_interval_minutes(self) -> int:
        settings = self.config.settings or {}
        approval_rules = settings.get("approval_rules", {})
        if isinstance(approval_rules, dict) and "min_interval_minutes_same_platform" in approval_rules:
            return int(approval_rules["min_interval_minutes_same_platform"])
        if "min_interval_minutes_same_platform" in settings:
            return int(settings["min_interval_minutes_same_platform"])
        return DEFAULT_MIN_INTERVAL_MINUTES

    def _is_platform_enabled(self, platform: Platform) -> bool:
        platforms_cfg = (self.config.settings or {}).get("platforms", {})
        plat_dict = platforms_cfg.get(platform.value, {}) if isinstance(platforms_cfg, dict) else {}
        yaml_enabled = bool(plat_dict.get("enabled", True)) if isinstance(plat_dict, dict) else True

        if not yaml_enabled:
            return False

        if platform == Platform.YOUTUBE_SHORTS:
            return self.config.youtube_enabled
        if platform == Platform.X:
            return self.config.x_enabled or self.config.dry_run
        return True

    @staticmethod
    def _resolve_copy(plan: ClipPlan, bundle: CaptionBundle, platform: Platform) -> PlatformCopy:
        if platform in bundle.copies:
            return bundle.copies[platform]

        if plan.for_real_oficial_campaign:
            base_text = (plan.commentary_overlay or plan.hook_on_screen or plan.source.title).strip()
            mandatory_phrase = "Assista ao vivo em youtube.com/youtube"
            if mandatory_phrase not in base_text:
                base_text = f"{base_text} {mandatory_phrase}".strip()
            return PlatformCopy(
                platform=platform,
                title=plan.hook_on_screen,
                caption=base_text,
                hashtags=["#launchdayonYT"],
                credit_line="",
            )

        credit = (
            ""
            if plan.source.is_official
            else f"🎥 Crédito: {plan.source.creator_handle}"
        )
        default_tags = ["#GTA6", "#GTAVI"]
        if platform == Platform.YOUTUBE_SHORTS:
            default_tags.append("#Shorts")

        return PlatformCopy(
            platform=platform,
            title=plan.hook_on_screen,
            caption=plan.commentary_overlay or plan.hook_on_screen or plan.source.title,
            hashtags=default_tags,
            credit_line=credit,
        )

    def _check_spacing_block(self, platform: Platform) -> datetime | None:
        """Returns `next_allowed_at` if anti-shadowban spacing blocks immediate live posting."""
        if self.config.dry_run:
            return None

        min_minutes = self._min_interval_minutes()
        if min_minutes <= 0:
            return None

        last_pub = self.store.last_published_at(platform)
        if last_pub is None:
            return None

        if last_pub.tzinfo is None:
            last_pub = last_pub.replace(tzinfo=timezone.utc)

        now = utc_now()
        next_allowed = last_pub + timedelta(minutes=min_minutes)
        if now < next_allowed:
            return next_allowed
        return None

    async def _publish_single(
        self,
        platform: Platform,
        plan: ClipPlan,
        clip: RenderedClip | None,
        copy: PlatformCopy,
    ) -> PublicationRecord:
        if platform == Platform.YOUTUBE_SHORTS:
            return await self.youtube.publish(plan, clip, copy)

        if platform == Platform.X:
            return await self.x_twitter.publish(plan, clip, copy)

        if platform == Platform.INSTAGRAM_REELS:
            if self.post_for_me.has_credentials:
                return await self.post_for_me.publish(
                    plan, clip, copy, target_platform=Platform.INSTAGRAM_REELS
                )
            if self.meta_tiktok.has_instagram_credentials:
                return await self.meta_tiktok.publish_instagram_reel(plan, clip, copy)
            if self.buffer_webhook.has_webhook or self.buffer_webhook.has_buffer_credentials:
                return await self.buffer_webhook.publish(
                    plan, clip, copy, target_platform=Platform.INSTAGRAM_REELS
                )
            return await self.meta_tiktok.publish_instagram_reel(plan, clip, copy)

        if platform == Platform.TIKTOK:
            if self.post_for_me.has_credentials:
                return await self.post_for_me.publish(
                    plan, clip, copy, target_platform=Platform.TIKTOK
                )
            if self.meta_tiktok.has_tiktok_credentials:
                return await self.meta_tiktok.publish_tiktok(plan, clip, copy)
            if self.buffer_webhook.has_webhook or self.buffer_webhook.has_buffer_credentials:
                return await self.buffer_webhook.publish(
                    plan, clip, copy, target_platform=Platform.TIKTOK
                )
            return await self.meta_tiktok.publish_tiktok(plan, clip, copy)

        if platform == Platform.THREADS:
            return await self.buffer_webhook.publish(
                plan, clip, copy, target_platform=Platform.THREADS
            )

        return PublicationRecord(
            plan_id=plan.id,
            clip_id=clip.id if clip else None,
            platform=platform,
            status="failed",
            caption_text=copy.formatted_text(),
            campaign_slug=plan.campaign_slug,
            error=f"No publisher configured for platform: {platform.value}",
        )

    async def publish_all(
        self,
        plan: ClipPlan,
        clip: RenderedClip | None,
        bundle: CaptionBundle,
    ) -> list[PublicationRecord]:
        """Publishes to all enabled platforms in `plan.target_platforms` with spacing safeguards."""
        records: list[PublicationRecord] = []
        seen_platforms: set[Platform] = set()

        for platform in plan.target_platforms:
            if platform in seen_platforms:
                continue
            seen_platforms.add(platform)

            if not self._is_platform_enabled(platform):
                logger.info("Skipping disabled platform %s for plan %s", platform.value, plan.id)
                continue

            copy = self._resolve_copy(plan, bundle, platform)
            next_allowed_at = self._check_spacing_block(platform)

            if next_allowed_at is not None:
                min_mins = self._min_interval_minutes()
                pub = PublicationRecord(
                    plan_id=plan.id,
                    clip_id=clip.id if clip else None,
                    platform=platform,
                    status="queued",
                    caption_text=copy.formatted_text(),
                    scheduled_for=next_allowed_at,
                    campaign_slug=plan.campaign_slug,
                    error=(
                        f"Deferred by anti-shadowban spacing ({min_mins} min minimum interval; "
                        f"next eligible slot at {next_allowed_at.isoformat()})."
                    ),
                )
            else:
                pub = await self._publish_single(platform, plan, clip, copy)
                if not pub.campaign_slug and plan.campaign_slug:
                    pub.campaign_slug = plan.campaign_slug

            self.store.record_publication(pub)
            records.append(pub)

        return records
