"""Real Oficial Campaign Agent (`gta-vi-youtube` tracking, entry registration, & cloud clip budgeting)."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from gta6_agents.config import AppConfig
from gta6_agents.integrations.real_oficial_client import RealOficialClient
from gta6_agents.models import ChampionshipEntry, Platform, PublicationRecord
from gta6_agents.storage import StateStore

logger = logging.getLogger(__name__)

VALID_YOUTUBE_HOSTS = frozenset(
    {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
)
VALID_INSTAGRAM_HOSTS = frozenset(
    {"instagram.com", "www.instagram.com", "m.instagram.com"}
)
VALID_TIKTOK_HOSTS = frozenset(
    {"tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com", "vt.tiktok.com"}
)

DEFAULT_CAMPAIGN_CPM_BRL = 4.00
CAMPAIGN_REWARD_NOTE = (
    "R$ 4,00/1k views above 5,000 views, max R$ 400/video, register immediately!"
)


def is_valid_public_youtube_url(url: str | None) -> bool:
    """Validates that a URL is a public HTTPS YouTube video or Shorts link."""
    if not url or not isinstance(url, str):
        return False
    parsed = urlparse(url.strip())
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    if host not in VALID_YOUTUBE_HOSTS:
        return False
    if host == "youtu.be":
        return bool(parsed.path.strip("/"))
    if parsed.path.startswith("/shorts/"):
        video_part = parsed.path[len("/shorts/") :].strip("/")
        return bool(video_part)
    if parsed.path == "/watch" and "v=" in parsed.query:
        return True
    return False


def is_valid_public_instagram_url(url: str | None) -> bool:
    """Validates that a URL is a public HTTPS Instagram Reel / Carousel permalink."""
    if not url or not isinstance(url, str):
        return False
    parsed = urlparse(url.strip())
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    if host not in VALID_INSTAGRAM_HOSTS:
        return False
    for prefix in ("/reel/", "/reels/", "/p/", "/tv/"):
        if parsed.path.startswith(prefix):
            code = parsed.path[len(prefix) :].strip("/")
            return bool(code)
    return False


def is_valid_public_tiktok_url(url: str | None) -> bool:
    """Validates that a URL is a public HTTPS TikTok video permalink."""
    if not url or not isinstance(url, str):
        return False
    parsed = urlparse(url.strip())
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    if host not in VALID_TIKTOK_HOSTS:
        return False
    path = parsed.path.strip("/")
    if not path:
        return False
    if host in ("vm.tiktok.com", "vt.tiktok.com"):
        return True
    if "/video/" in parsed.path or "/photo/" in parsed.path or parsed.path.startswith("/t/"):
        return True
    return False


def is_valid_campaign_post_url(url: str | None, platform: Platform | None = None) -> bool:
    """Validates a public HTTPS TikTok or Instagram Reels URL for the Real Oficial KY4 campaign."""
    if platform == Platform.INSTAGRAM_REELS:
        return is_valid_public_instagram_url(url)
    if platform == Platform.TIKTOK:
        return is_valid_public_tiktok_url(url)
    if platform is None:
        return is_valid_public_instagram_url(url) or is_valid_public_tiktok_url(url)
    return False


class RealOficialAgent:
    """Coordinates official Real Oficial championship synchronization and entry submissions."""

    def __init__(
        self,
        config: AppConfig,
        store: StateStore,
        client: RealOficialClient | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.client = client or RealOficialClient(config=config, store=store)

    def _campaign_meta(self) -> dict[str, Any]:
        raw = self.config.campaign or {}
        inner = raw.get("campaign", raw)
        return inner if isinstance(inner, dict) else {}

    async def sync_campaign_status(self) -> dict[str, Any]:
        """Checks credentials, active `gta-vi-youtube` rules, credit budget, and submitted entries."""
        campaign_cfg = self._campaign_meta()
        slug = (
            str(campaign_cfg.get("slug") or self.config.real_oficial_campaign_slug or "gta-vi-youtube")
            .strip()
        )
        confirm_url = self.client.build_campaign_url(slug)
        rewards_cfg = campaign_cfg.get("rewards", {}) if isinstance(campaign_cfg, dict) else {}
        default_cpm = float(
            rewards_cfg.get("cpm_brl_per_1k_views", DEFAULT_CAMPAIGN_CPM_BRL)
            if isinstance(rewards_cfg, dict)
            else DEFAULT_CAMPAIGN_CPM_BRL
        )

        whoami_info = await self.client.whoami()
        championships_info = await self.client.list_championships()

        spent_today = self.store.today_credit_spend("real_oficial")
        daily_limit = self.config.real_oficial_daily_credit_limit
        remaining_today = max(0, daily_limit - spent_today)

        entries = self.store.list_championship_entries(limit=200)
        by_status: dict[str, int] = {
            "submitted": 0,
            "previewed": 0,
            "pending_submission": 0,
            "manual_action_needed": 0,
        }
        validated_views_total = 0
        estimated_earnings_brl = 0.0

        for row in entries:
            st = str(row.get("status", "pending_submission"))
            by_status[st] = by_status.get(st, 0) + 1
            views = int(row.get("validated_views") or 0)
            cpm = float(row.get("estimated_cpm_brl") or default_cpm)
            validated_views_total += views
            estimated_earnings_brl += (views / 1000.0) * cpm

        return {
            "enabled": self.config.real_oficial_enabled,
            "dry_run": self.config.dry_run,
            "has_credentials": self.client.has_credentials,
            "whoami": whoami_info,
            "campaign_slug": slug,
            "confirm_url": confirm_url,
            "campaign_rules": campaign_cfg.get("rules", {}),
            "authorized_live": campaign_cfg.get("authorized_live", {}),
            "valid_platforms": campaign_cfg.get("valid_platforms", ["instagram_reels", "tiktok"]),
            "rewards": rewards_cfg,
            "eligible_acervo_urls": campaign_cfg.get("eligible_acervo_urls", []),
            "scaling_plan": campaign_cfg.get("scaling_5_days", {}),
            "championships": championships_info.get("championships", []),
            "credits": {
                "allow_spend_credits": self.config.real_oficial_allow_spend_credits,
                "daily_limit": daily_limit,
                "spent_today": spent_today,
                "remaining_today": remaining_today,
            },
            "entries_summary": {
                "total": len(entries),
                "by_status": by_status,
                "validated_views_total": validated_views_total,
                "estimated_earnings_brl": round(estimated_earnings_brl, 2),
                "recent": entries[:10],
            },
        }

    async def register_campaign_post(
        self,
        pub: PublicationRecord,
        *,
        _allow_legacy_youtube: bool = False,
    ) -> ChampionshipEntry:
        """Registers a published (or dry-run) TikTok or Instagram Reel in the `gta-vi-youtube` championship.

        Payout rules: R$ 4,00/1k views above 5,000 views, max R$ 400/video; register immediately after posting
        and never delete, archive, or privatize for 48 hours after the live broadcast ends.
        """
        cpm_brl = 4.00
        slug = (pub.campaign_slug or self.config.real_oficial_campaign_slug or "gta-vi-youtube").strip()
        confirm_link = self.client.build_campaign_url(slug)

        is_valid_url = is_valid_campaign_post_url(pub.public_url, pub.platform) or (
            _allow_legacy_youtube
            and pub.platform == Platform.YOUTUBE_SHORTS
            and is_valid_public_youtube_url(pub.public_url)
        )

        if not is_valid_url:
            entry = ChampionshipEntry(
                campaign_slug=slug,
                publication_id=pub.id,
                youtube_url=pub.public_url or "",
                status="manual_action_needed",
                estimated_cpm_brl=cpm_brl,
                notes=(
                    f"Publication requires a valid public TikTok or Instagram Reels HTTPS URL "
                    f"({CAMPAIGN_REWARD_NOTE}). Submit manually in 1 click once live: {confirm_link}"
                ),
            )
            self.store.record_championship_entry(entry)
            return entry

        post_url = str(pub.public_url).strip()

        if not self.config.real_oficial_enabled:
            entry = ChampionshipEntry(
                campaign_slug=slug,
                publication_id=pub.id,
                youtube_url=post_url,
                status="pending_submission",
                estimated_cpm_brl=cpm_brl,
                notes=(
                    f"Real Oficial integration disabled in config ({CAMPAIGN_REWARD_NOTE}). "
                    f"1-click link: {confirm_link}"
                ),
            )
            self.store.record_championship_entry(entry)
            return entry

        if self.config.dry_run or pub.status == "dry_run" or not self.client.has_credentials:
            reason = (
                "Dry-run simulation"
                if (self.config.dry_run or pub.status == "dry_run")
                else "Real Oficial MCP token not configured yet"
            )
            entry = ChampionshipEntry(
                campaign_slug=slug,
                publication_id=pub.id,
                youtube_url=post_url,
                status="pending_submission",
                estimated_cpm_brl=cpm_brl,
                notes=(
                    f"{reason} ({CAMPAIGN_REWARD_NOTE}). "
                    f"Confirm entry in 1 click at: {confirm_link}"
                ),
            )
            self.store.record_championship_entry(entry)
            return entry

        preview = await self.client.preview_championship_entry(
            campaign_slug=slug,
            video_url=post_url,
        )

        if preview.get("isError") or not preview.get("ok", True):
            err_msg = preview.get("error", "Unknown MCP preview error")
            entry = ChampionshipEntry(
                campaign_slug=slug,
                publication_id=pub.id,
                youtube_url=post_url,
                status="pending_submission",
                estimated_cpm_brl=cpm_brl,
                notes=(
                    f"MCP preview failed ({err_msg}) — {CAMPAIGN_REWARD_NOTE} "
                    f"Confirm in 1 click at: {confirm_link}"
                ),
            )
            self.store.record_championship_entry(entry)
            return entry

        preview_id = str(preview.get("preview_id", "")).strip()
        terms_required = bool(preview.get("terms_required", True))
        terms_already_accepted = bool(preview.get("terms_accepted", False))

        # Never fabricate terms acceptance on user's first submission.
        if terms_required or not terms_already_accepted or not preview_id:
            status = "previewed" if preview_id else "pending_submission"
            entry = ChampionshipEntry(
                campaign_slug=slug,
                publication_id=pub.id,
                youtube_url=post_url,
                status=status,
                estimated_cpm_brl=cpm_brl,
                notes=(
                    f"Previewed ({preview_id or 'ready'}) — {CAMPAIGN_REWARD_NOTE} "
                    f"Explicit championship terms/1-click confirmation required at: {confirm_link}"
                ),
            )
            self.store.record_championship_entry(entry)
            return entry

        confirm_resp = await self.client.confirm_publishing(
            preview_id=preview_id,
            accepted_terms=True,
        )
        if confirm_resp.get("ok") and not confirm_resp.get("terms_required", False):
            entry = ChampionshipEntry(
                campaign_slug=slug,
                publication_id=pub.id,
                youtube_url=post_url,
                status="submitted",
                estimated_cpm_brl=cpm_brl,
                notes=(
                    f"Submitted via official Real Oficial MCP (preview_id={preview_id}) — "
                    f"{CAMPAIGN_REWARD_NOTE} Panel: {confirm_link}"
                ),
            )
        else:
            entry = ChampionshipEntry(
                campaign_slug=slug,
                publication_id=pub.id,
                youtube_url=post_url,
                status="previewed",
                estimated_cpm_brl=cpm_brl,
                notes=(
                    f"Previewed (preview_id={preview_id}) — {CAMPAIGN_REWARD_NOTE} "
                    f"Complete 1-click confirmation at: {confirm_link}"
                ),
            )

        self.store.record_championship_entry(entry)
        return entry

    async def register_youtube_short_in_campaign(
        self,
        pub: PublicationRecord,
    ) -> ChampionshipEntry:
        """Backward-compatible wrapper delegating to `register_campaign_post`."""
        return await self.register_campaign_post(pub, _allow_legacy_youtube=True)

    async def estimate_and_queue_cloud_clips(
        self,
        youtube_url: str,
        auto_confirm: bool = False,
    ) -> dict[str, Any]:
        """Estimates Real Oficial cloud clip credits and optionally queues creation within budget."""
        if not is_valid_public_youtube_url(youtube_url):
            return {
                "ok": False,
                "status": "invalid_url",
                "youtube_url": youtube_url,
                "error": "Expected a valid public HTTPS YouTube URL.",
            }

        estimate = await self.client.estimate_clips(source_url=youtube_url.strip())
        if estimate.get("isError") or not estimate.get("ok", True):
            return {
                "ok": False,
                "status": "estimate_failed",
                "youtube_url": youtube_url,
                "estimate": estimate,
                "error": estimate.get("error", "Failed to estimate cloud clips."),
            }

        estimate_id = str(estimate.get("estimate_id", "")).strip()
        estimated_credits = int(estimate.get("estimated_credits", 0) or 0)
        spent_today = self.store.today_credit_spend("real_oficial")
        daily_limit = self.config.real_oficial_daily_credit_limit
        remaining_before = max(0, daily_limit - spent_today)

        if not auto_confirm:
            return {
                "ok": True,
                "status": "estimated_awaiting_confirmation",
                "youtube_url": youtube_url,
                "estimate_id": estimate_id,
                "estimated_credits": estimated_credits,
                "credits_remaining_today": remaining_before,
                "allow_spend_credits": self.config.real_oficial_allow_spend_credits,
                "estimate": estimate,
            }

        creation = await self.client.create_clips(
            estimate_id=estimate_id,
            estimated_credits=estimated_credits,
        )
        spent_after = self.store.today_credit_spend("real_oficial")
        return {
            "ok": bool(creation.get("ok", False)),
            "status": str(creation.get("status", "created" if creation.get("ok") else "blocked")),
            "youtube_url": youtube_url,
            "estimate_id": estimate_id,
            "estimated_credits": estimated_credits,
            "credits_remaining_today": max(0, daily_limit - spent_after),
            "estimate": estimate,
            "creation": creation,
        }
