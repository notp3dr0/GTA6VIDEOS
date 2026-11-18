"""Shared strongly-typed Pydantic models across all GTA 6 & Real Oficial agents."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str = "item") -> str:
    return f"{prefix}_{uuid4().hex[:10]}"


class ContentPillar(str, Enum):
    OFFICIAL_NEWS = "official_news"
    TIPS_TUTORIALS = "tips_tutorials"
    GAMEPLAY_CLIPS = "gameplay_clips"
    GTA5_NOSTALGIA = "gta5_nostalgia"
    SPOTLIGHTS = "spotlights"
    COUNTDOWN_RECAP = "countdown_recap"
    QUESTIONS = "questions"
    REAL_OFICIAL_CAMPAIGN = "real_oficial_campaign"


class Platform(str, Enum):
    YOUTUBE_SHORTS = "youtube_shorts"
    INSTAGRAM_REELS = "instagram_reels"
    TIKTOK = "tiktok"
    X = "x"
    THREADS = "threads"


class ApprovalStatus(str, Enum):
    AUTO_APPROVED = "auto_approved"
    PENDING_HUMAN = "pending_human"
    APPROVED = "approved"
    REJECTED = "rejected"


class SourceCandidate(BaseModel):
    id: str = Field(default_factory=lambda: new_id("src"))
    title: str
    url: str
    creator_handle: str = "@RockstarGames"
    source_type: str = "youtube"  # official_newswire | youtube | reddit | realoficial_acervo | x
    is_official: bool = False
    is_campaign_acervo: bool = False
    campaign_slug: Optional[str] = None
    pillar: ContentPillar = ContentPillar.GAMEPLAY_CLIPS
    summary: str = ""
    verified_facts: list[str] = Field(default_factory=list)
    suggested_start_sec: float = 0.0
    suggested_end_sec: float = 25.0
    virality_score: float = 75.0
    contains_spoiler: bool = False
    contains_nudity_or_gore: bool = False
    is_leak_or_rumor: bool = False
    discovered_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SubtitleCue(BaseModel):
    start_sec: float
    end_sec: float
    text: str


class ClipPlan(BaseModel):
    id: str = Field(default_factory=lambda: new_id("plan"))
    source: SourceCandidate
    pillar: ContentPillar
    language: str = "pt-BR"
    hook_on_screen: str
    voiceover_script: str = ""
    commentary_overlay: str = ""
    start_sec: float = 0.0
    end_sec: float = 25.0
    target_platforms: list[Platform] = Field(
        default_factory=lambda: [Platform.YOUTUBE_SHORTS, Platform.INSTAGRAM_REELS, Platform.TIKTOK]
    )
    for_real_oficial_campaign: bool = False
    campaign_slug: Optional[str] = None
    spoiler_tag_required: bool = False
    subtitles: list[SubtitleCue] = Field(default_factory=list)


class PlatformCopy(BaseModel):
    platform: Platform
    title: str = ""
    caption: str
    hashtags: list[str] = Field(default_factory=list)
    credit_line: str = ""

    def formatted_text(self) -> str:
        parts = [self.caption.strip()]
        if self.credit_line and self.credit_line not in self.caption:
            parts.append(self.credit_line.strip())
        if self.hashtags and self.platform != Platform.X:
            tag_str = " ".join(
                t if t.startswith("#") else f"#{t}" for t in self.hashtags
            )
            if tag_str not in self.caption:
                parts.append(tag_str)
        return "\n\n".join(p for p in parts if p)


class CaptionBundle(BaseModel):
    plan_id: str
    copies: dict[Platform, PlatformCopy] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class ComplianceVerdict(BaseModel):
    passed: bool
    approval_status: ApprovalStatus = ApprovalStatus.AUTO_APPROVED
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    campaign_compliant: bool = True


class RenderedClip(BaseModel):
    id: str = Field(default_factory=lambda: new_id("clip"))
    plan_id: str
    video_path: str
    audio_path: Optional[str] = None
    ass_subtitle_path: Optional[str] = None
    thumbnail_path: Optional[str] = None
    duration_sec: float
    width: int = 1080
    height: int = 1920
    has_watermark_third_party: bool = False
    creator_credited_on_screen: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class PublicationRecord(BaseModel):
    id: str = Field(default_factory=lambda: new_id("pub"))
    plan_id: str
    clip_id: Optional[str] = None
    platform: Platform
    status: str = "queued"  # queued | dry_run | scheduled | published | failed
    external_post_id: Optional[str] = None
    public_url: Optional[str] = None
    caption_text: str = ""
    scheduled_for: Optional[datetime] = None
    published_at: Optional[datetime] = None
    campaign_slug: Optional[str] = None
    error: Optional[str] = None


class ChampionshipEntry(BaseModel):
    id: str = Field(default_factory=lambda: new_id("ro_entry"))
    campaign_slug: str = "gta-vi-youtube"
    publication_id: str
    youtube_url: str
    status: str = "pending_submission"  # pending_submission | previewed | submitted | manual_action_needed
    estimated_cpm_brl: float = 1.25
    validated_views: int = 0
    notes: str = ""
    created_at: datetime = Field(default_factory=utc_now)
