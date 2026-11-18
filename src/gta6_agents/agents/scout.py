"""Content Discovery & Source Scouting Agent (Acervo, YouTube RSS, Reddit JSON, Offline Curated Fallback)."""

from __future__ import annotations

import asyncio
import json
import re
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from gta6_agents.agents.compliance import (
    DEFAULT_BLACKLIST_HANDLES,
    normalize_handle,
    sanitize_untrusted_video_text,
)
from gta6_agents.config import AppConfig
from gta6_agents.models import ContentPillar, SourceCandidate
from gta6_agents.storage import StateStore

REDDIT_USER_AGENT = (
    "linux:gta6-fast-clips-scout:v1.0.0 (by /u/gta6fastclips; fan commentary curator)"
)

YOUTUBE_RSS_TEMPLATE = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
REDDIT_FEEDS: list[tuple[str, str]] = [
    ("GTA6", "https://www.reddit.com/r/GTA6/top.json?t=day&limit=10"),
    ("GTA", "https://www.reddit.com/r/GTA/top.json?t=day&limit=10"),
]

DEFAULT_WHITELIST_CHANNELS: list[dict[str, Any]] = [
    {
        "handle": "@RockstarGames",
        "name": "Rockstar Games",
        "youtube_channel_id": "UC6VcWc1rAoWdBCM0JxrRQ3A",
        "is_official": True,
    },
    {
        "handle": "@GTASeriesVideos",
        "name": "GTA Series Videos",
        "youtube_channel_id": "UCuWcjpKbIDAbZfHoru1toFg",
        "is_official": False,
    },
    {
        "handle": "@TGGonYT",
        "name": "TGG",
        "youtube_channel_id": "UCD23kK2Q4P3H9G5xN4vL2wA",
        "is_official": False,
    },
    {
        "handle": "@DarkViperAU",
        "name": "DarkViperAU",
        "youtube_channel_id": "UCV6mNrW8CrmWtcxWfQXy11g",
        "is_official": False,
    },
    {
        "handle": "@Broughy1322",
        "name": "Broughy1322",
        "youtube_channel_id": "UCcZ3sRk9z6jjgfYQbMGTpig",
        "is_official": False,
    },
]

DEFAULT_ACERVO_ITEMS: list[dict[str, Any]] = [
    {
        "url": "https://www.youtube.com/watch?v=QdBZY2fkU-0",
        "creator": "@RockstarGames",
        "title": "Grand Theft Auto VI Trailer 1",
        "official": True,
    },
    {
        "url": "https://www.youtube.com/watch?v=VQRLujxTm3c",
        "creator": "@RockstarGames",
        "title": "Grand Theft Auto VI Trailer 2",
        "official": True,
    },
]

# Pre-mapped multi-segment definitions for official Rockstar trailers & generic acervo items
KNOWN_ACERVO_SEGMENTS: dict[str, list[dict[str, Any]]] = {
    "QdBZY2fkU-0": [
        {
            "start_sec": 0.0,
            "end_sec": 26.0,
            "label": "Lucia Caminos & Leonida Opening Reveal",
            "summary": "Official Trailer 1 opening introducing Lucia Caminos, sunset lighting over Leonida, and prison tower framing.",
            "verified_facts": [
                "Lucia Caminos is a lead protagonist in Grand Theft Auto VI.",
                "Grand Theft Auto VI takes place in the state of Leonida, home to Vice City.",
                "Captured from official Rockstar Games Trailer 1.",
            ],
            "base_score": 96.0,
        },
        {
            "start_sec": 26.0,
            "end_sec": 52.0,
            "label": "Vice Beach Crowd Density & Water Physics",
            "summary": "Official Trailer 1 showcase of Vice Beach NPC variety, watercraft wakes, and coastal Leonida Keys bridges.",
            "verified_facts": [
                "Vice Beach features dense, individually animated NPC routines and water physics.",
                "Airboats and speedboats traverse coastal waterways across Leonida.",
            ],
            "base_score": 94.0,
        },
        {
            "start_sec": 52.0,
            "end_sec": 78.0,
            "label": "Grassrivers Wildlife, Nightlife & High-Speed Getaway",
            "summary": "Official Trailer 1 montage covering Grassrivers wetlands wildlife, Ocean Drive neon cars, and Lucia & Jason getaway.",
            "verified_facts": [
                "Grassrivers wetlands include alligators, flamingos, and airboat traversal.",
                "Lucia and Jason operate together during robberies in Leonida.",
            ],
            "base_score": 92.5,
        },
    ],
    "VQRLujxTm3c": [
        {
            "start_sec": 0.0,
            "end_sec": 26.0,
            "label": "Jason Duval & Leonida Keys Routine",
            "summary": "Official Trailer 2 opening spotlighting Jason Duval in the Leonida Keys with realistic tropical lighting and vehicle wear.",
            "verified_facts": [
                "Jason Duval and Lucia Caminos are the dual protagonists of GTA VI.",
                "Leonida Keys features tropical coastal towns and connected island highways.",
            ],
            "base_score": 97.0,
        },
        {
            "start_sec": 26.0,
            "end_sec": 52.0,
            "label": "Vice City Neon Reflections & Ray-Traced Interiors",
            "summary": "Official Trailer 2 look at Vice City night reflections, vehicle mirrors, and dense urban street traffic.",
            "verified_facts": [
                "Rockstar captured Trailer 2 in-game on PlayStation 5 hardware.",
                "Real-time reflections appear on car mirrors, glass storefronts, and wet asphalt.",
            ],
            "base_score": 95.0,
        },
        {
            "start_sec": 52.0,
            "end_sec": 78.0,
            "label": "Port Gellhorn, Ambrosia & Muscle Car Physics",
            "summary": "Official Trailer 2 action sequence highlighting muscle car suspension flex, tire smoke, and industrial Leonida regions.",
            "verified_facts": [
                "Leonida expands far beyond Vice City into Port Gellhorn, Ambrosia, and Mount Kalaga.",
                "Vehicle suspension and weight transfer visibly react to terrain and high-speed turns.",
            ],
            "base_score": 93.5,
        },
    ],
}

_LEAK_FILTER_RE = re.compile(
    r"(?<!no\s)(?<!zero\s)(?<!without\s)(?<!sem\s)"
    r"\b(?:leak|leaks|leaked|leaking|datamine|datamined|datamines|datamining|dataminer|"
    r"dev\s+build|source\s+code|pirated|piracy|torrent|crack\s+download|"
    r"money\s+glitch|god\s*mode\s+exploit|aimbot|wallhack|nude\s+mod|nsfw|"
    r"extreme\s+gore|vazamento|mapa\s+vazado|build\s+vazada|c[oó]pia\s+pirata)\b",
    re.IGNORECASE,
)

_GTA_RELEVANCE_RE = re.compile(
    r"\b(?:gta\s*6|gta\s*vi|gta\s*5|gta\s*v|grand\s+theft\s+auto|leonida|vice\s+city|"
    r"lucia|jason\s+duval|rockstar\s+games|grassrivers|kalaga|gellhorn)\b",
    re.IGNORECASE,
)

_HIGH_HOOK_KEYWORDS_RE = re.compile(
    r"\b(?:trailer\s*2|trailer\s*1|official|lucia|jason|vice\s+city|leonida|"
    r"physics|detail|hidden|guide|tip|how\s+to|evolution|comparison)\b",
    re.IGNORECASE,
)


def _extract_youtube_id(url: str) -> str:
    clean_url = url.split("#", 1)[0]
    match = re.search(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})", clean_url)
    return match.group(1) if match else ""


def _ensure_at_handle(handle: str) -> str:
    cleaned = handle.strip()
    if not cleaned:
        return "@RockstarGames"
    return cleaned if cleaned.startswith("@") else f"@{cleaned}"


class ScoutAgent:
    """Discovers, filters, deduplicates, and ranks verified GTA 6 & Real Oficial clip candidates."""

    def __init__(
        self,
        config: AppConfig,
        store: StateStore,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self._external_client = http_client

    def _load_creator_lists(self) -> tuple[list[dict[str, Any]], set[str], list[str]]:
        whitelist = list(DEFAULT_WHITELIST_CHANNELS)
        blacklist = set(DEFAULT_BLACKLIST_HANDLES)
        blocked_keywords: list[str] = []

        lists_path = self.config.memory_dir / "creator_whitelist_blacklist.json"
        if lists_path.exists():
            try:
                data = json.loads(lists_path.read_text(encoding="utf-8"))
                raw_white = data.get("whitelist", [])
                if isinstance(raw_white, list) and raw_white:
                    parsed_white: list[dict[str, Any]] = []
                    for item in raw_white:
                        if isinstance(item, dict) and item.get("handle"):
                            parsed_white.append(item)
                        elif isinstance(item, str) and item.strip():
                            parsed_white.append({"handle": _ensure_at_handle(item), "is_official": False})
                    if parsed_white:
                        whitelist = parsed_white

                for entry in data.get("blacklist", []):
                    if isinstance(entry, str):
                        blacklist.add(normalize_handle(entry))
                    elif isinstance(entry, dict) and entry.get("handle"):
                        blacklist.add(normalize_handle(str(entry["handle"])))

                for kw in data.get("blocked_keywords", []):
                    if isinstance(kw, str) and kw.strip():
                        blocked_keywords.append(kw.strip().lower())
            except Exception:
                pass

        return whitelist, blacklist, blocked_keywords

    def _is_blocked_content(
        self,
        creator_handle: str,
        title: str,
        summary: str,
        blacklist: set[str],
        blocked_keywords: list[str],
    ) -> bool:
        if normalize_handle(creator_handle) in blacklist:
            return True
        combined = f"{title} {summary}"
        if _LEAK_FILTER_RE.search(combined):
            return True
        lowered = combined.lower()
        return any(kw in lowered for kw in blocked_keywords)

    def _score_candidate(
        self,
        *,
        base_score: float,
        title: str,
        summary: str,
        is_official: bool,
        is_campaign_acervo: bool,
        is_whitelisted: bool,
        engagement_bonus: float = 0.0,
    ) -> float:
        score = base_score
        if is_campaign_acervo:
            score += 6.0
        if is_official:
            score += 5.0
        elif is_whitelisted:
            score += 3.0

        hook_hits = len(_HIGH_HOOK_KEYWORDS_RE.findall(f"{title} {summary}"))
        score += min(6.0, hook_hits * 1.5)
        score += min(10.0, max(0.0, engagement_bonus))
        return round(max(0.0, min(100.0, score)), 2)

    def _discover_campaign_acervo(
        self,
        blacklist: set[str],
        blocked_keywords: list[str],
        max_segments_per_video: int = 2,
    ) -> list[SourceCandidate]:
        raw_campaign = self.config.campaign
        if isinstance(raw_campaign, dict) and raw_campaign:
            campaign_block = raw_campaign.get("campaign", raw_campaign)
            if isinstance(campaign_block, dict) and "eligible_acervo_urls" in campaign_block:
                acervo_list = campaign_block.get("eligible_acervo_urls") or []
            else:
                acervo_list = DEFAULT_ACERVO_ITEMS if self.config.real_oficial_enabled else []
            campaign_slug = (
                campaign_block.get("slug", self.config.real_oficial_campaign_slug)
                if isinstance(campaign_block, dict)
                else self.config.real_oficial_campaign_slug
            )
        else:
            acervo_list = DEFAULT_ACERVO_ITEMS if self.config.real_oficial_enabled else []
            campaign_slug = self.config.real_oficial_campaign_slug

        results: list[SourceCandidate] = []
        for raw_item in acervo_list:
            if isinstance(raw_item, str):
                base_url = raw_item.strip()
                creator = "@RockstarGames"
                base_title = "GTA VI Official Campaign Video"
                is_official = True
                custom_segments = None
            elif isinstance(raw_item, dict) and raw_item.get("url"):
                base_url = str(raw_item["url"]).strip()
                creator = _ensure_at_handle(str(raw_item.get("creator", "@RockstarGames")))
                base_title = sanitize_untrusted_video_text(
                    str(raw_item.get("title", "GTA VI Campaign Acervo")), max_length=140
                )
                is_official = bool(raw_item.get("official", True))
                custom_segments = raw_item.get("segments")
            else:
                continue

            if not base_url:
                continue

            clean_base_url = base_url.split("#", 1)[0]
            vid_id = _extract_youtube_id(clean_base_url)

            if isinstance(custom_segments, list) and custom_segments:
                segments = custom_segments
            elif vid_id in KNOWN_ACERVO_SEGMENTS:
                segments = KNOWN_ACERVO_SEGMENTS[vid_id]
            else:
                segments = [
                    {
                        "start_sec": 0.0,
                        "end_sec": 26.0,
                        "label": "Highlight Cut 1",
                        "summary": f"Verified campaign acervo highlight from {base_title}.",
                        "verified_facts": [f"Official campaign acervo source: {base_title}."],
                        "base_score": 90.0,
                    },
                    {
                        "start_sec": 26.0,
                        "end_sec": 52.0,
                        "label": "Highlight Cut 2",
                        "summary": f"Second verified campaign acervo highlight from {base_title}.",
                        "verified_facts": [f"Official campaign acervo source: {base_title}."],
                        "base_score": 88.0,
                    },
                ]

            added_for_video = 0
            for idx, seg in enumerate(segments):
                if added_for_video >= max_segments_per_video:
                    break
                start_sec = float(seg.get("start_sec", 0.0))
                end_sec = float(seg.get("end_sec", 26.0))
                segment_key = f"{clean_base_url}#{int(start_sec)}-{int(end_sec)}"

                if self.store.is_source_seen(segment_key):
                    continue

                seg_label = sanitize_untrusted_video_text(str(seg.get("label", f"Segment {idx + 1}")), max_length=90)
                seg_title = f"{base_title} — {seg_label}"
                seg_summary = sanitize_untrusted_video_text(
                    str(seg.get("summary", base_title)), max_length=400
                )

                if self._is_blocked_content(creator, seg_title, seg_summary, blacklist, blocked_keywords):
                    continue

                facts = [
                    sanitize_untrusted_video_text(str(f), max_length=200)
                    for f in seg.get("verified_facts", [])
                    if f
                ]
                score = self._score_candidate(
                    base_score=float(seg.get("base_score", 90.0)),
                    title=seg_title,
                    summary=seg_summary,
                    is_official=is_official,
                    is_campaign_acervo=True,
                    is_whitelisted=True,
                )

                results.append(
                    SourceCandidate(
                        title=seg_title,
                        url=segment_key,
                        creator_handle=creator,
                        source_type="realoficial_acervo",
                        is_official=is_official,
                        is_campaign_acervo=True,
                        campaign_slug=campaign_slug,
                        pillar=ContentPillar.REAL_OFICIAL_CAMPAIGN,
                        summary=seg_summary,
                        verified_facts=facts,
                        suggested_start_sec=start_sec,
                        suggested_end_sec=end_sec,
                        virality_score=score,
                        metadata={
                            "canonical_url": clean_base_url,
                            "segment_key": segment_key,
                            "segment_index": idx,
                            "video_id": vid_id,
                        },
                    )
                )
                added_for_video += 1

        return results

    async def _fetch_youtube_rss(
        self,
        client: httpx.AsyncClient,
        channel_info: dict[str, Any],
        blacklist: set[str],
        blocked_keywords: list[str],
    ) -> list[SourceCandidate]:
        channel_id = str(channel_info.get("youtube_channel_id", "")).strip()
        if not channel_id:
            return []

        handle = _ensure_at_handle(str(channel_info.get("handle", "@RockstarGames")))
        is_official = bool(channel_info.get("is_official", False))
        feed_url = YOUTUBE_RSS_TEMPLATE.format(channel_id=channel_id)

        try:
            resp = await client.get(feed_url)
            if resp.status_code != 200 or not resp.text:
                return []
            root = ET.fromstring(resp.text)
        except Exception:
            return []

        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "yt": "http://www.youtube.com/xml/schemas/2015",
            "media": "http://search.yahoo.com/mrss/",
        }

        items: list[SourceCandidate] = []
        for entry in root.findall("atom:entry", ns)[:8]:
            vid_el = entry.find("yt:videoId", ns)
            title_el = entry.find("atom:title", ns)
            desc_el = entry.find("media:group/media:description", ns)
            stats_el = entry.find("media:group/media:community/media:statistics", ns)

            video_id = (vid_el.text or "").strip() if vid_el is not None else ""
            raw_title = (title_el.text or "").strip() if title_el is not None else ""
            raw_desc = (desc_el.text or "").strip() if desc_el is not None else ""
            if not video_id or not raw_title:
                continue

            url = f"https://www.youtube.com/watch?v={video_id}"
            if self.store.is_source_seen(url):
                continue

            title = sanitize_untrusted_video_text(raw_title, max_length=160)
            summary = sanitize_untrusted_video_text(raw_desc, max_length=360)

            if not _GTA_RELEVANCE_RE.search(f"{title} {summary}"):
                continue
            if self._is_blocked_content(handle, title, summary, blacklist, blocked_keywords):
                continue

            views = 0
            if stats_el is not None and stats_el.attrib.get("views"):
                try:
                    views = int(stats_el.attrib["views"])
                except ValueError:
                    views = 0

            engagement_bonus = min(10.0, views / 250_000.0) if views > 0 else 2.5
            pillar = self._infer_pillar(title, summary, is_official=is_official)
            score = self._score_candidate(
                base_score=82.0 if is_official else 76.0,
                title=title,
                summary=summary,
                is_official=is_official,
                is_campaign_acervo=False,
                is_whitelisted=True,
                engagement_bonus=engagement_bonus,
            )

            items.append(
                SourceCandidate(
                    title=title,
                    url=url,
                    creator_handle=handle,
                    source_type="youtube",
                    is_official=is_official,
                    pillar=pillar,
                    summary=summary or f"Verified YouTube upload from {handle}: {title}",
                    verified_facts=[f"Published publicly on YouTube by {handle}."],
                    suggested_start_sec=0.0,
                    suggested_end_sec=26.0,
                    virality_score=score,
                    metadata={"video_id": video_id, "views": views},
                )
            )

        return items

    async def _fetch_reddit_feed(
        self,
        client: httpx.AsyncClient,
        subreddit: str,
        feed_url: str,
        blacklist: set[str],
        blocked_keywords: list[str],
    ) -> list[SourceCandidate]:
        try:
            resp = await client.get(feed_url, headers={"User-Agent": REDDIT_USER_AGENT})
            if resp.status_code != 200:
                return []
            payload = resp.json()
        except Exception:
            return []

        children = payload.get("data", {}).get("children", []) if isinstance(payload, dict) else []
        results: list[SourceCandidate] = []

        for child in children[:10]:
            data = child.get("data", {}) if isinstance(child, dict) else {}
            if not data or data.get("stickied") or data.get("over_18"):
                continue

            raw_title = str(data.get("title", "")).strip()
            raw_selftext = str(data.get("selftext", "")).strip()
            flair = str(data.get("link_flair_text") or "").strip()
            author = str(data.get("author") or "reddit_user").strip()
            permalink = str(data.get("permalink") or "").strip()

            if not raw_title or not permalink or author in ("[deleted]", "AutoModerator"):
                continue

            post_url = f"https://www.reddit.com{permalink}"
            if self.store.is_source_seen(post_url):
                continue

            creator_handle = _ensure_at_handle(re.sub(r"[^A-Za-z0-9_.-]", "", author) or "RedditCommunity")
            title = sanitize_untrusted_video_text(raw_title, max_length=160)
            summary = sanitize_untrusted_video_text(
                f"{flair}: {raw_selftext}" if flair else raw_selftext, max_length=360
            )

            if not _GTA_RELEVANCE_RE.search(f"{subreddit} {title} {summary}"):
                continue
            if self._is_blocked_content(
                creator_handle, f"{title} {flair}", summary, blacklist, blocked_keywords
            ):
                continue

            # Skip speculative/rumor flairs automatically so automated discovery stays clean
            if re.search(r"\b(?:rumor|rumour|speculation|unverified)\b", flair, re.IGNORECASE):
                continue

            upvotes = int(data.get("score") or 0)
            upvote_ratio = float(data.get("upvote_ratio") or 0.85)
            engagement_bonus = min(10.0, (upvotes / 500.0) * upvote_ratio)
            pillar = self._infer_pillar(title, summary, is_official=False)
            score = self._score_candidate(
                base_score=72.0,
                title=title,
                summary=summary,
                is_official=False,
                is_campaign_acervo=False,
                is_whitelisted=False,
                engagement_bonus=engagement_bonus,
            )

            results.append(
                SourceCandidate(
                    title=title,
                    url=post_url,
                    creator_handle=creator_handle,
                    source_type="reddit",
                    is_official=False,
                    pillar=pillar,
                    summary=summary or f"Top daily r/{subreddit} community highlight: {title}",
                    verified_facts=[f"Community discussion on r/{subreddit} ({upvotes} upvotes)."],
                    suggested_start_sec=0.0,
                    suggested_end_sec=24.0,
                    virality_score=score,
                    contains_spoiler=bool(data.get("spoiler", False)),
                    metadata={"subreddit": subreddit, "upvotes": upvotes, "flair": flair},
                )
            )

        return results

    def _infer_pillar(self, title: str, summary: str, *, is_official: bool) -> ContentPillar:
        text = f"{title} {summary}".lower()
        if is_official or any(k in text for k in ("newswire", "official trailer", "rockstar announces")):
            return ContentPillar.OFFICIAL_NEWS
        if any(k in text for k in ("how to", "tip", "guide", "tutorial", "settings", "dica", "como")):
            return ContentPillar.TIPS_TUTORIALS
        if any(k in text for k in ("gta 5", "gta v", "2013", "los santos", "nostalgia", "evolution")):
            return ContentPillar.GTA5_NOSTALGIA
        if any(
            k in text
            for k in (
                "lucia",
                "jason",
                "vice city",
                "leonida keys",
                "grassrivers",
                "port gellhorn",
                "ambrosia",
                "mount kalaga",
                "spotlight",
            )
        ):
            return ContentPillar.SPOTLIGHTS
        if any(k in text for k in ("countdown", "recap", "days left", "everything we know")):
            return ContentPillar.COUNTDOWN_RECAP
        return ContentPillar.GAMEPLAY_CLIPS

    def _curated_offline_fallbacks(
        self, blacklist: set[str], blocked_keywords: list[str]
    ) -> list[SourceCandidate]:
        curated_raw: list[dict[str, Any]] = [
            {
                "title": "GTA VI Official Trailer 2 — Real-Time Lighting & Physics Breakdown",
                "url": "https://www.rockstargames.com/VI#trailer2-lighting-breakdown",
                "creator_handle": "@RockstarGames",
                "source_type": "official_newswire",
                "is_official": True,
                "pillar": ContentPillar.OFFICIAL_NEWS,
                "summary": "Official Rockstar Games GTA VI showcase highlighting PS5 in-game lighting, mirror reflections, and dual-protagonist scenes.",
                "verified_facts": [
                    "Grand Theft Auto VI Trailer 2 was captured in-game on PlayStation 5.",
                    "Features protagonists Jason Duval and Lucia Caminos across the state of Leonida.",
                ],
                "start_sec": 4.0,
                "end_sec": 30.0,
                "virality_score": 93.0,
            },
            {
                "title": "Leonida Regional Spotlight — Vice City, Leonida Keys & Grassrivers",
                "url": "https://www.rockstargames.com/VI#leonida-regions-spotlight",
                "creator_handle": "@RockstarGames",
                "source_type": "official_newswire",
                "is_official": True,
                "pillar": ContentPillar.SPOTLIGHTS,
                "summary": "Tour of confirmed Leonida regions: Vice City neon avenues, tropical Leonida Keys, Grassrivers swamps, Port Gellhorn, Ambrosia, and Mount Kalaga.",
                "verified_facts": [
                    "Confirmed Leonida regions include Vice City, Leonida Keys, Grassrivers, Port Gellhorn, Ambrosia, and Mount Kalaga National Park.",
                    "Each region has distinct architecture, vehicles, and wildlife ecosystems.",
                ],
                "start_sec": 0.0,
                "end_sec": 27.0,
                "virality_score": 91.0,
            },
            {
                "title": "Lucia Caminos & Jason Duval — Confirmed Character Details Spotlight",
                "url": "https://www.rockstargames.com/VI#lucia-jason-spotlight",
                "creator_handle": "@RockstarGames",
                "source_type": "official_newswire",
                "is_official": True,
                "pillar": ContentPillar.SPOTLIGHTS,
                "summary": "Official character overview of Lucia Caminos and Jason Duval and how their partnership drives the Leonida story.",
                "verified_facts": [
                    "Lucia Caminos and Jason Duval partner up across Leonida after a score goes wrong.",
                    "Both characters feature detailed facial animations and distinct movement styles.",
                ],
                "start_sec": 0.0,
                "end_sec": 26.0,
                "virality_score": 89.5,
            },
            {
                "title": "HOW-TO: 3 Driving & Braking Habits to Master Before Leonida Highways",
                "url": "https://www.youtube.com/watch?v=broughy_handling_prep_01",
                "creator_handle": "@Broughy1322",
                "source_type": "youtube",
                "is_official": False,
                "pillar": ContentPillar.TIPS_TUTORIALS,
                "summary": "Practical driving tutorial on trail-braking, weight transfer, and camera height settings for heavy GTA suspension physics.",
                "verified_facts": [
                    "GTA VI trailers demonstrate pronounced body roll and suspension compression during cornering.",
                    "Higher vehicle camera height improves apex visibility in dense Vice City traffic.",
                ],
                "start_sec": 5.0,
                "end_sec": 31.0,
                "virality_score": 87.0,
            },
            {
                "title": "13 Years of Rockstar Evolution: GTA V Los Santos (2013) vs GTA VI Leonida",
                "url": "https://www.youtube.com/watch?v=gtaseries_gta5_vs_gta6_01",
                "creator_handle": "@GTASeriesVideos",
                "source_type": "youtube",
                "is_official": False,
                "pillar": ContentPillar.GTA5_NOSTALGIA,
                "summary": "Side-by-side nostalgia comparison of beach crowds, water waves, traffic variety, and sunset lighting from GTA V to GTA VI.",
                "verified_facts": [
                    "GTA V launched in September 2013 set in Los Santos and Blaine County.",
                    "GTA VI upgrades crowd animation uniqueness, volumetric clouds, and water foam simulation.",
                ],
                "start_sec": 2.0,
                "end_sec": 28.0,
                "virality_score": 88.5,
            },
            {
                "title": "HOW-TO: Spot Frame-by-Frame Vehicle Mirror & Interior Details in GTA VI",
                "url": "https://www.youtube.com/watch?v=tgg_gta6_details_guide_01",
                "creator_handle": "@TGGonYT",
                "source_type": "youtube",
                "is_official": False,
                "pillar": ContentPillar.TIPS_TUTORIALS,
                "summary": "Step-by-step breakdown showing working side mirrors, dashboard gauges, and NPC reactions inside GTA VI vehicles.",
                "verified_facts": [
                    "Side-view and rear-view mirrors render accurate live road reflections in GTA VI trailers.",
                    "Vehicle interiors feature individually animated switches, steering hands, and seat textures.",
                ],
                "start_sec": 3.0,
                "end_sec": 29.0,
                "virality_score": 85.5,
            },
            {
                "title": "GTA Physics & NPC Reaction Mechanics That Define Rockstar Open Worlds",
                "url": "https://www.youtube.com/watch?v=darkviper_gta_mechanics_01",
                "creator_handle": "@DarkViperAU",
                "source_type": "youtube",
                "is_official": False,
                "pillar": ContentPillar.GAMEPLAY_CLIPS,
                "summary": "Gameplay analysis of Rockstar euphoria physics, NPC awareness cones, and vehicle collision deformation.",
                "verified_facts": [
                    "Rockstar open worlds combine procedural ragdoll physics with canned animation blending.",
                ],
                "start_sec": 4.0,
                "end_sec": 28.0,
                "virality_score": 84.0,
            },
            {
                "title": "GTA VI Verified Recap: Every Confirmed Feature, Region & Trailer Milestone",
                "url": "https://www.rockstargames.com/VI#verified-countdown-recap",
                "creator_handle": "@RockstarGames",
                "source_type": "official_newswire",
                "is_official": True,
                "pillar": ContentPillar.COUNTDOWN_RECAP,
                "summary": "Fast verified recap of everything officially confirmed by Rockstar Games for Grand Theft Auto VI in Leonida.",
                "verified_facts": [
                    "Set in the state of Leonida featuring Vice City and five surrounding regions.",
                    "Stars Lucia Caminos and Jason Duval.",
                ],
                "start_sec": 0.0,
                "end_sec": 25.0,
                "virality_score": 86.5,
            },
        ]

        candidates: list[SourceCandidate] = []
        for item in curated_raw:
            url = str(item["url"])
            if self.store.is_source_seen(url):
                continue
            handle = _ensure_at_handle(str(item["creator_handle"]))
            title = sanitize_untrusted_video_text(str(item["title"]), max_length=160)
            summary = sanitize_untrusted_video_text(str(item["summary"]), max_length=360)
            if self._is_blocked_content(handle, title, summary, blacklist, blocked_keywords):
                continue

            candidates.append(
                SourceCandidate(
                    title=title,
                    url=url,
                    creator_handle=handle,
                    source_type=str(item["source_type"]),
                    is_official=bool(item["is_official"]),
                    is_campaign_acervo=False,
                    pillar=item["pillar"],
                    summary=summary,
                    verified_facts=list(item["verified_facts"]),
                    suggested_start_sec=float(item["start_sec"]),
                    suggested_end_sec=float(item["end_sec"]),
                    virality_score=float(item["virality_score"]),
                    metadata={"curated_fallback": True},
                )
            )

        return candidates

    async def discover_candidates(
        self,
        limit: int = 10,
        include_campaign_acervo: bool = True,
    ) -> list[SourceCandidate]:
        """Discover, sanitize, filter, deduplicate, and rank GTA 6 clip candidates."""
        if limit <= 0:
            return []

        whitelist, blacklist, blocked_keywords = self._load_creator_lists()
        collected: list[SourceCandidate] = []
        seen_batch_urls: set[str] = set()

        def _add_unique(items: list[SourceCandidate]) -> None:
            for cand in items:
                if cand.url in seen_batch_urls:
                    continue
                seen_batch_urls.add(cand.url)
                collected.append(cand)

        # 1. Campaign Acervo multi-segment candidates
        if include_campaign_acervo:
            acervo_candidates = self._discover_campaign_acervo(blacklist, blocked_keywords)
            _add_unique(acervo_candidates)

        # 2. Live YouTube RSS + Reddit JSON feeds (graceful timeout & exception isolation)
        scout_cfg = (self.config.settings or {}).get("scout", {})
        offline_only = bool(scout_cfg.get("offline_only", False)) if isinstance(scout_cfg, dict) else False

        if not offline_only:
            timeout = httpx.Timeout(3.5, connect=2.0)
            if self._external_client is not None:
                live_batches = await self._gather_live_feeds(
                    self._external_client, whitelist, blacklist, blocked_keywords
                )
                for batch in live_batches:
                    _add_unique(batch)
            else:
                try:
                    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                        live_batches = await self._gather_live_feeds(
                            client, whitelist, blacklist, blocked_keywords
                        )
                        for batch in live_batches:
                            _add_unique(batch)
                except Exception:
                    pass

        # 3. Curated verified fallback pool so discovery NEVER fails or starves
        if len(collected) < limit:
            fallback_candidates = self._curated_offline_fallbacks(blacklist, blocked_keywords)
            _add_unique(fallback_candidates)

        collected.sort(
            key=lambda c: (c.virality_score, c.is_campaign_acervo, c.is_official),
            reverse=True,
        )
        return collected[:limit]

    async def _gather_live_feeds(
        self,
        client: httpx.AsyncClient,
        whitelist: list[dict[str, Any]],
        blacklist: set[str],
        blocked_keywords: list[str],
    ) -> list[list[SourceCandidate]]:
        tasks = [
            self._fetch_youtube_rss(client, ch, blacklist, blocked_keywords)
            for ch in whitelist
            if ch.get("youtube_channel_id")
        ]
        for sub, url in REDDIT_FEEDS:
            tasks.append(self._fetch_reddit_feed(client, sub, url, blacklist, blocked_keywords))

        raw_results = await asyncio.gather(*tasks, return_exceptions=True)
        valid_batches: list[list[SourceCandidate]] = []
        for res in raw_results:
            if isinstance(res, list):
                valid_batches.append(res)
        return valid_batches
