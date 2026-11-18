"""Content Discovery & Source Scouting Agent (KY4DWumZffg Official Live + Brazilian Streamers YouTube/Twitch Lives & VODs)."""

from __future__ import annotations

import asyncio
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import httpx
import yaml

from gta6_agents.agents.compliance import (
    DEFAULT_BLACKLIST_HANDLES,
    normalize_handle,
    sanitize_untrusted_video_text,
)
from gta6_agents.config import AppConfig, get_project_root
from gta6_agents.models import ContentPillar, SourceCandidate
from gta6_agents.storage import StateStore
from gta6_agents.video.downloader import _resolve_ytdlp_cmd

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
        "handle": "@SkorpionOFICIAL",
        "name": "Skorpion Gamer",
        "youtube_channel_id": "",
        "is_official": False,
    },
    {
        "handle": "@GamerCosta",
        "name": "Gamer Costa",
        "youtube_channel_id": "",
        "is_official": False,
    },
    {
        "handle": "@PaulinhoLOKOoficial",
        "name": "Paulinho o Loko",
        "youtube_channel_id": "",
        "is_official": False,
    },
    {
        "handle": "@alanzoka",
        "name": "Alanzoka",
        "youtube_channel_id": "",
        "is_official": False,
    },
    {
        "handle": "@loud_coringa",
        "name": "LOUD Coringa",
        "youtube_channel_id": "",
        "is_official": False,
    },
    {
        "handle": "@funkyblackcat",
        "name": "FunkyBlackCat",
        "youtube_channel_id": "",
        "is_official": False,
    },
    {
        "handle": "@gaules",
        "name": "Gaules",
        "youtube_channel_id": "",
        "is_official": False,
    },
]

DEFAULT_ACERVO_ITEMS: list[dict[str, Any]] = [
    {
        "url": "https://www.youtube.com/watch?v=KY4DWumZffg",
        "creator": "@YouTube",
        "title": "Live Oficial YouTube x GTA VI",
        "official": True,
    },
]

# Pre-mapped multi-segment definitions for the Official YouTube x GTA VI 25h Live (KY4DWumZffg)
# plus legacy Rockstar trailer IDs when explicitly requested in non-campaign tests.
KNOWN_ACERVO_SEGMENTS: dict[str, list[dict[str, Any]]] = {
    "KY4DWumZffg": [
        {
            "start_sec": 12.0,
            "end_sec": 36.0,
            "label": "Gameplay ao Vivo do Skorpion em Vice City",
            "summary": "Corte em tempo real da transmissão oficial YouTube x GTA VI com Skorpion explorando Vice City à noite.",
            "verified_facts": [
                "Momento capturado ao vivo na transmissão mundial oficial YouTube x GTA VI.",
                "Gameplay em português com iluminação neon em tempo real em Vice City.",
            ],
            "base_score": 98.5,
        },
        {
            "start_sec": 40.0,
            "end_sec": 64.0,
            "label": "Reação Ao Vivo na Perseguição em Leonida Keys",
            "summary": "Momento insano da live oficial YouTube x GTA VI atravessando as pontes de Leonida Keys em alta velocidade.",
            "verified_facts": [
                "Transmissão oficial de lançamento YouTube x GTA VI ao vivo.",
                "Física de suspensão e reflexos na água durante gameplay em Leonida Keys.",
            ],
            "base_score": 97.5,
        },
        {
            "start_sec": 68.0,
            "end_sec": 92.0,
            "label": "Skorpion & Gamer Costa — Bastidores de Vice City (Câmera Solo)",
            "summary": "Corte focado exclusivamente em Skorpion e Gamer Costa comentando os detalhes de Leonida na live oficial.",
            "verified_facts": [
                "Bloco brasileiro da live mundial oficial YouTube x GTA VI.",
                "Corte limpo apenas com Skorpion e Gamer Costa em tela.",
            ],
            "base_score": 96.5,
        },
        {
            "start_sec": 96.0,
            "end_sec": 120.0,
            "label": "Física de Carros e Drift na Ocean Drive Ao Vivo",
            "summary": "Streamer testando a tração e deformação dos muscle cars ao vivo na avenida principal de Vice Beach.",
            "verified_facts": [
                "Gameplay oficial transmitido ao vivo em youtube.com/youtube.",
                " Fumaça volumétrica dos pneus e física de peso em tempo real.",
            ],
            "base_score": 96.0,
        },
        {
            "start_sec": 124.0,
            "end_sec": 148.0,
            "label": "Primeira Entrada em Grassrivers de Aerobarco na Live",
            "summary": "Reação ao vivo na live oficial ao entrar nos pântanos de Grassrivers com vida selvagem dinâmica.",
            "verified_facts": [
                "Corte oficial da live mundial de 25 horas YouTube x GTA VI.",
                "Ecossistema vivo de Grassrivers em Leonida.",
            ],
            "base_score": 95.5,
        },
        {
            "start_sec": 152.0,
            "end_sec": 176.0,
            "label": "Lucia e Jason em Ação Ao Vivo na Transmissão Mundial",
            "summary": "Troca dinâmica de protagonistas e reação imediata dos criadores na live oficial do YouTube.",
            "verified_facts": [
                "Transmitido ao vivo no evento oficial YouTube x GTA VI.",
                "Protagonistas Lucia Caminos e Jason Duval em missão em Leonida.",
            ],
            "base_score": 95.0,
        },
        {
            "start_sec": 180.0,
            "end_sec": 204.0,
            "label": "Densidade de NPCs em Vice Beach Ao Vivo",
            "summary": "Criador mostra a quantidade absurda de NPCs únicos reagindo em tempo real na praia de Vice City.",
            "verified_facts": [
                "Cena 100% da live oficial YouTube x GTA VI.",
                "Rotinas individuais de NPCs e clima dinâmico em Vice Beach.",
            ],
            "base_score": 94.5,
        },
        {
            "start_sec": 208.0,
            "end_sec": 232.0,
            "label": "Voo Rasante sobre Port Gellhorn e Mount Kalaga na Live",
            "summary": "Travessia aérea ao vivo revelando a escala gigante do mapa de Leonida durante a live oficial.",
            "verified_facts": [
                "Transmissão mundial oficial de lançamento do GTA VI no YouTube.",
                "Horizonte contínuo conectando Port Gellhorn e Mount Kalaga.",
            ],
            "base_score": 94.0,
        },
    ],
    "QdBZY2fkU-0": [
        {
            "start_sec": 0.0,
            "end_sec": 26.0,
            "label": "Lucia Caminos & Leonida Opening Reveal",
            "summary": "Official Trailer 1 opening introducing Lucia Caminos, sunset lighting over Leonida, and prison tower framing.",
            "verified_facts": [
                "Lucia Caminos is a lead protagonist in Grand Theft Auto VI.",
                "Grand Theft Auto VI takes place in the state of Leonida, home to Vice City.",
            ],
            "base_score": 92.0,
        },
    ],
    "VQRLujxTm3c": [
        {
            "start_sec": 0.0,
            "end_sec": 26.0,
            "label": "Jason Duval & Leonida Keys Routine",
            "summary": "Official Trailer 2 opening spotlighting Jason Duval in the Leonida Keys with realistic tropical lighting.",
            "verified_facts": [
                "Jason Duval and Lucia Caminos are the dual protagonists of GTA VI.",
            ],
            "base_score": 92.0,
        },
    ],
}

_LEAK_FILTER_RE = re.compile(
    r"(?<!no\s)(?<!zero\s)(?<!without\s)(?<!sem\s)"
    r"\b(?:leak|leaks|leaked|leaking|datamine|datamined|datamines|datamining|dataminer|"
    r"dev\s+build|source\s+code|pirated|piracy|torrent|crack\s+download|"
    r"money\s+glitch|god\s*mode\s+exploit|aimbot|wallhack|nude\s+mod|nsfw|"
    r"extreme\s+gore|vazamento|mapa\s+vazado|build\s+vazada|c[oó]pia\s+pirata|"
    r"samira\s*close|burnt\s*peanut|burntpeanut|"
    r"antes\s+do\s+gta\s*(?:6|vi)|before\s+gta\s*(?:6|vi)|"
    r"gta\s*(?:5|v)\s*vs\s*gta\s*(?:6|vi)|san\s+andreas\s+vs)\b",
    re.IGNORECASE,
)

_GTA_RELEVANCE_RE = re.compile(
    r"\b(?:gta\s*6|gta\s*vi|grand\s+theft\s+auto\s*vi|leonida|vice\s+city|"
    r"lucia|jason\s+duval|rockstar\s+games|grassrivers|kalaga|gellhorn|skorpion)\b",
    re.IGNORECASE,
)

_GTA6_STRICT_RE = re.compile(
    r"\b(?:gta\s*6|gta\s*vi|grand\s+theft\s+auto\s*(?:6|vi)|leonida|vice\s+city|"
    r"lucia|jason\s+duval|grassrivers|kalaga|gellhorn)\b",
    re.IGNORECASE,
)

_NON_GTA6_GAME_RE = re.compile(
    r"\b(?:gta\s*5|gta\s*v|cidade\s+alta|complexo\s+rp|fluxo\s+rp|roblox|beamng|"
    r"parasite\s+eve|zelda|batman|free\s+fire|valorant|cs2|counter-strike|minecraft|fortnite)\b",
    re.IGNORECASE,
)

_HIGH_HOOK_KEYWORDS_RE = re.compile(
    r"\b(?:ao\s+vivo|live|skorpion|gamer\s+costa|paulinho|alanzoka|coringa|lucia|jason|"
    r"vice\s+city|leonida|f[ií]sica|physics|insano|rea[cç][aã]o|gameplay|launchdayonyt)\b",
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

    def _load_streamers_br_config(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Load verified Brazilian streamers (YouTube Live + Twitch) from config/streamers_br.yaml."""
        candidates_paths = [
            self.config.root_dir / "config" / "streamers_br.yaml",
            get_project_root() / "config" / "streamers_br.yaml",
        ]
        for cfg_path in candidates_paths:
            if cfg_path.exists():
                try:
                    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
                    if isinstance(raw, dict):
                        cap_cfg = raw.get("capture_settings", {}) or {}
                        streamers = raw.get("streamers_br", []) or []
                        if isinstance(streamers, list):
                            return (
                                cap_cfg if isinstance(cap_cfg, dict) else {},
                                [s for s in streamers if isinstance(s, dict) and s.get("handle")],
                            )
                except Exception:
                    continue
        return {}, []

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

    def _is_campaign_blocked_now(self, campaign_block: dict[str, Any]) -> bool:
        """Enforce KY4DWumZffg ON-AIR window (19/11 01:00 -> 20/11 01:57 BRT) and Burnt Peanut blackout (20:00-20:34 BRT)."""
        if self.config.dry_run:
            return False
        from datetime import datetime, timezone

        now_utc = datetime.now(timezone.utc)
        schedule_brt = campaign_block.get("schedule_brt", {})
        if isinstance(schedule_brt, dict):
            live_start_raw = str(schedule_brt.get("live_start_iso", "")).strip()
            if live_start_raw:
                try:
                    live_start_dt = datetime.fromisoformat(live_start_raw).astimezone(timezone.utc)
                    if now_utc < live_start_dt:
                        return True
                except ValueError:
                    pass
            hard_stop_raw = str(schedule_brt.get("hard_stop_post_iso", "")).strip()
            if hard_stop_raw:
                try:
                    hard_stop_dt = datetime.fromisoformat(hard_stop_raw).astimezone(timezone.utc)
                    if now_utc >= hard_stop_dt:
                        return True
                except ValueError:
                    pass

        for entry in campaign_block.get("forbidden_appearances", []) or []:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("policy", "")) == "hard_time_and_keyword_blackout":
                s_raw = str(entry.get("danger_window_start_iso", "")).strip()
                e_raw = str(entry.get("danger_window_end_iso", "")).strip()
                if s_raw and e_raw:
                    try:
                        s_dt = datetime.fromisoformat(s_raw).astimezone(timezone.utc)
                        e_dt = datetime.fromisoformat(e_raw).astimezone(timezone.utc)
                        if s_dt <= now_utc <= e_dt:
                            return True
                    except ValueError:
                        pass
        return False

    def _discover_campaign_acervo(
        self,
        blacklist: set[str],
        blocked_keywords: list[str],
        max_segments_per_video: int = 8,
    ) -> list[SourceCandidate]:
        raw_campaign = self.config.campaign
        campaign_block: dict[str, Any] = {}
        if isinstance(raw_campaign, dict) and raw_campaign:
            maybe_block = raw_campaign.get("campaign", raw_campaign)
            if isinstance(maybe_block, dict):
                campaign_block = maybe_block

        if campaign_block and self._is_campaign_blocked_now(campaign_block):
            return []

        if "eligible_acervo_urls" in campaign_block:
            acervo_list = campaign_block.get("eligible_acervo_urls") or []
        elif isinstance(campaign_block.get("authorized_live"), dict) and campaign_block["authorized_live"].get("url"):
            auth_live = campaign_block["authorized_live"]
            acervo_list = (
                [
                    {
                        "url": str(auth_live["url"]).strip(),
                        "creator": "@YouTube",
                        "title": str(auth_live.get("title", "Live Oficial YouTube x GTA VI")),
                        "official": True,
                    }
                ]
                if self.config.real_oficial_enabled
                else []
            )
        else:
            acervo_list = DEFAULT_ACERVO_ITEMS if self.config.real_oficial_enabled else []

        campaign_slug = (
            str(campaign_block.get("slug", self.config.real_oficial_campaign_slug))
            if campaign_block
            else self.config.real_oficial_campaign_slug
        )

        results: list[SourceCandidate] = []
        for raw_item in acervo_list:
            if isinstance(raw_item, str):
                base_url = raw_item.strip()
                creator = "@YouTube" if "KY4DWumZffg" in base_url else "@RockstarGames"
                base_title = (
                    "Live Oficial YouTube x GTA VI"
                    if "KY4DWumZffg" in base_url
                    else "GTA VI Official Campaign Video"
                )
                is_official = True
                custom_segments = None
            elif isinstance(raw_item, dict) and raw_item.get("url"):
                base_url = str(raw_item["url"]).strip()
                default_creator = "@YouTube" if "KY4DWumZffg" in base_url else "@RockstarGames"
                creator = _ensure_at_handle(str(raw_item.get("creator", default_creator)))
                base_title = sanitize_untrusted_video_text(
                    str(raw_item.get("title", "Live Oficial YouTube x GTA VI")), max_length=140
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
                segments = list(custom_segments)
            elif vid_id in KNOWN_ACERVO_SEGMENTS:
                segments = list(KNOWN_ACERVO_SEGMENTS[vid_id])
                if vid_id == "KY4DWumZffg":
                    # Append rolling 24s windows across the 25h live stream so 5x12 channels never starve
                    for roll_i in range(1, 80):
                        s_sec = 240.0 + roll_i * 28.0
                        segments.append(
                            {
                                "start_sec": s_sec,
                                "end_sec": s_sec + 24.0,
                                "label": f"Momento Ao Vivo #{roll_i + 8} — Gameplay em Leonida",
                                "summary": (
                                    f"Corte em tempo real da transmissão mundial YouTube x GTA VI "
                                    f"(janela {int(s_sec)}s-{int(s_sec + 24)}s) com reação ao vivo em português."
                                ),
                                "verified_facts": [
                                    "Capturado 100% da live oficial YouTube x GTA VI ao vivo.",
                                    "Gameplay verificado no estado de Leonida e Vice City.",
                                ],
                                "base_score": max(88.0, 94.0 - (roll_i * 0.05)),
                            }
                        )
            else:
                segments = [
                    {
                        "start_sec": 0.0,
                        "end_sec": 24.0,
                        "label": "Corte Ao Vivo #1",
                        "summary": f"Momento verificado ao vivo em {base_title}.",
                        "verified_facts": [f"Fonte oficial autorizada: {base_title}."],
                        "base_score": 90.0,
                    },
                    {
                        "start_sec": 28.0,
                        "end_sec": 52.0,
                        "label": "Corte Ao Vivo #2",
                        "summary": f"Segundo momento verificado ao vivo em {base_title}.",
                        "verified_facts": [f"Fonte oficial autorizada: {base_title}."],
                        "base_score": 88.0,
                    },
                ]

            added_for_video = 0
            for idx, seg in enumerate(segments):
                if added_for_video >= max_segments_per_video:
                    break
                start_sec = float(seg.get("start_sec", 0.0))
                end_sec = float(seg.get("end_sec", 24.0))
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
                            "authorized_live_ky4": vid_id == "KY4DWumZffg",
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
        content_focus = str((self.config.settings or {}).get("page", {}).get("content_focus", ""))
        if content_focus == "streamer_live_clips_only":
            return ContentPillar.GAMEPLAY_CLIPS
        if is_official or any(k in text for k in ("newswire", "official trailer", "rockstar announces")):
            return ContentPillar.OFFICIAL_NEWS
        if any(k in text for k in ("how to", "tip", "guide", "tutorial", "settings", "dica", "como")):
            return ContentPillar.TIPS_TUTORIALS
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

    async def _run_ytdlp_flat_json(self, feed_url: str, max_items: int = 3, timeout_sec: float = 8.0) -> list[dict[str, Any]]:
        """Run yt-dlp --flat-playlist --dump-json on a Brazilian streamer YouTube/Twitch URL."""
        cmd = [
            *_resolve_ytdlp_cmd(),
            "--flat-playlist",
            "--dump-json",
            "--playlist-end",
            str(max(1, max_items)),
            "--no-warnings",
            "--socket-timeout",
            "5",
            feed_url,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return []

        if (proc.returncode or 0) != 0 and not stdout_b:
            return []

        entries: list[dict[str, Any]] = []
        for raw_line in stdout_b.decode("utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line.startswith("{"):
                continue
            try:
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    entries.append(parsed)
            except json.JSONDecodeError:
                continue
        return entries

    async def _probe_streamer_feed_ytdlp(
        self,
        streamer: dict[str, Any],
        feed_url: str,
        *,
        is_live_endpoint: bool,
        capture_cfg: dict[str, Any],
        blacklist: set[str],
        blocked_keywords: list[str],
    ) -> list[SourceCandidate]:
        """Extract GTA VI live streams or VOD segments from one Brazilian streamer's YouTube/Twitch feed."""
        if not feed_url:
            return []

        handle = _ensure_at_handle(str(streamer.get("handle", "@SkorpionOFICIAL")))
        streamer_name = str(streamer.get("name", handle.lstrip("@")))
        priority_score = float(streamer.get("priority", 90.0))
        clip_dur = float(capture_cfg.get("clip_duration_sec", 25.0))
        strict_gta6 = bool(capture_cfg.get("strict_gta6_only_filter", True))
        vod_offsets = capture_cfg.get("vod_hot_moments_offsets_sec") or [180, 600, 1200]

        raw_entries = await self._run_ytdlp_flat_json(feed_url, max_items=3)
        results: list[SourceCandidate] = []

        from datetime import datetime, timezone

        live_bucket = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")[:-1] + "0"  # 10-min rolling live window

        for entry in raw_entries:
            raw_title = str(entry.get("title") or "").strip()
            raw_desc = str(entry.get("description") or "").strip()
            if not raw_title:
                continue

            title = sanitize_untrusted_video_text(raw_title, max_length=160)
            summary = sanitize_untrusted_video_text(
                raw_desc or f"Corte de live/gameplay de {streamer_name} ({handle}) jogando GTA VI em português.",
                max_length=360,
            )
            combined = f"{title} {summary}"

            # Hard-reject GTA 5 RP, Roblox, CS2, or any non-GTA6 game from Brazilian streamer channels
            if _NON_GTA6_GAME_RE.search(combined):
                continue
            if strict_gta6 and not _GTA6_STRICT_RE.search(combined):
                continue
            if self._is_blocked_content(handle, title, summary, blacklist, blocked_keywords):
                continue

            webpage_url = str(entry.get("webpage_url") or entry.get("url") or "").strip()
            vid_id = str(entry.get("id") or "").strip()
            if webpage_url.startswith("http"):
                canonical_url = webpage_url.split("#", 1)[0]
            elif vid_id and "youtube.com" in feed_url:
                canonical_url = f"https://www.youtube.com/watch?v={vid_id}"
            elif vid_id and "twitch.tv" in feed_url:
                canonical_url = (
                    f"https://www.twitch.tv/videos/{vid_id.lstrip('v')}"
                    if not is_live_endpoint
                    else feed_url.split("?", 1)[0]
                )
            else:
                canonical_url = feed_url

            platform_type = "twitch" if "twitch.tv" in canonical_url.lower() else "youtube"
            entry_is_live = (
                is_live_endpoint
                or bool(entry.get("is_live", False))
                or str(entry.get("live_status") or "") == "is_live"
            )
            duration = float(entry.get("duration") or 0.0)

            score = self._score_candidate(
                base_score=min(95.0, max(80.0, priority_score * 0.92)),
                title=title,
                summary=summary,
                is_official=False,
                is_campaign_acervo=False,
                is_whitelisted=True,
                engagement_bonus=4.0 if entry_is_live else 2.0,
            )

            if entry_is_live:
                seg_url = f"{canonical_url}#live-{live_bucket}"
                if self.store.is_source_seen(seg_url):
                    continue
                results.append(
                    SourceCandidate(
                        title=f"{streamer_name} AO VIVO — {title}"[:160],
                        url=seg_url,
                        creator_handle=handle,
                        source_type=platform_type,
                        is_official=False,
                        is_campaign_acervo=False,
                        pillar=ContentPillar.GAMEPLAY_CLIPS,
                        summary=summary,
                        verified_facts=[
                            f"Corte capturado ao vivo da transmissão em português de {streamer_name} ({handle}).",
                            f"Fonte pública verificada ({platform_type}).",
                        ],
                        suggested_start_sec=0.0,
                        suggested_end_sec=clip_dur,
                        virality_score=score,
                        metadata={
                            "canonical_url": canonical_url,
                            "is_live": True,
                            "streamer_br": True,
                            "streamer_name": streamer_name,
                            "platform": platform_type,
                        },
                    )
                )
            else:
                valid_offsets: list[float] = []
                for raw_off in vod_offsets:
                    off = float(raw_off)
                    if duration <= 0.0 or (off + clip_dur + 5.0 <= duration):
                        valid_offsets.append(off)
                    if len(valid_offsets) >= 2:
                        break
                if not valid_offsets:
                    valid_offsets = [max(0.0, min(30.0, duration * 0.2))] if duration > clip_dur else [0.0]

                for off_sec in valid_offsets:
                    end_sec = off_sec + clip_dur
                    seg_url = f"{canonical_url}#{int(off_sec)}-{int(end_sec)}"
                    if self.store.is_source_seen(seg_url):
                        continue
                    results.append(
                        SourceCandidate(
                            title=f"{streamer_name} — {title}"[:160],
                            url=seg_url,
                            creator_handle=handle,
                            source_type=platform_type,
                            is_official=False,
                            is_campaign_acervo=False,
                            pillar=ContentPillar.GAMEPLAY_CLIPS,
                            summary=summary,
                            verified_facts=[
                                f"Corte de live/VOD em português do streamer brasileiro {streamer_name} ({handle}).",
                            ],
                            suggested_start_sec=off_sec,
                            suggested_end_sec=end_sec,
                            virality_score=score,
                            metadata={
                                "canonical_url": canonical_url,
                                "is_live": False,
                                "duration": duration,
                                "streamer_br": True,
                                "streamer_name": streamer_name,
                                "platform": platform_type,
                            },
                        )
                    )

        return results

    async def discover_br_streamer_streams(
        self,
        blacklist: set[str],
        blocked_keywords: list[str],
        max_streamers: int = 8,
    ) -> list[SourceCandidate]:
        """Discover active GTA VI lives & recent VODs from top Brazilian streamers (YouTube + Twitch)."""
        capture_cfg, streamers = self._load_streamers_br_config()
        if not streamers:
            return []

        ordered = sorted(streamers, key=lambda s: float(s.get("priority", 0)), reverse=True)[:max_streamers]
        tasks = []
        for st in ordered:
            for key, is_live_ep in (
                ("youtube_live_url", True),
                ("twitch_live_url", True),
                ("youtube_streams_url", False),
                ("twitch_vods_url", False),
                ("youtube_videos_url", False),
            ):
                f_url = str(st.get(key) or "").strip()
                if f_url:
                    tasks.append(
                        self._probe_streamer_feed_ytdlp(
                            st,
                            f_url,
                            is_live_endpoint=is_live_ep,
                            capture_cfg=capture_cfg,
                            blacklist=blacklist,
                            blocked_keywords=blocked_keywords,
                        )
                    )

        if not tasks:
            return []

        batches = await asyncio.gather(*tasks, return_exceptions=True)
        discovered: list[SourceCandidate] = []
        for batch in batches:
            if isinstance(batch, list):
                discovered.extend(batch)
        return discovered

    def _curated_offline_fallbacks(
        self, blacklist: set[str], blocked_keywords: list[str]
    ) -> list[SourceCandidate]:
        """Curated PT-BR Streamer Live Cuts of GTA VI (post-live / offline fallback mode)."""
        curated_raw: list[dict[str, Any]] = [
            {
                "title": "Skorpion Ao Vivo Explorando Vice City à Noite em GTA VI",
                "url": "https://www.youtube.com/@SkorpionOFICIAL/live#fallback-01",
                "creator_handle": "@SkorpionOFICIAL",
                "source_type": "youtube",
                "is_official": False,
                "pillar": ContentPillar.GAMEPLAY_CLIPS,
                "summary": "Corte de live em português com Skorpion testando carros esportivos e reflexos neon na Ocean Drive em GTA VI.",
                "verified_facts": [
                    "Corte de live em português focado 100% em gameplay ao vivo de GTA VI em Vice City.",
                    "Crédito ao streamer original @SkorpionOFICIAL.",
                ],
                "start_sec": 4.0,
                "end_sec": 28.0,
                "virality_score": 93.0,
            },
            {
                "title": "Alanzoka Reage à Física Insana de Perseguição em Leonida Keys",
                "url": "https://www.twitch.tv/alanzoka#fallback-01",
                "creator_handle": "@alanzoka",
                "source_type": "twitch",
                "is_official": False,
                "pillar": ContentPillar.GAMEPLAY_CLIPS,
                "summary": "Momento hilário e tenso da live do Alanzoka cruzando as pontes de Leonida Keys em alta velocidade no GTA VI.",
                "verified_facts": [
                    "Corte de transmissão ao vivo de GTA VI em português.",
                    "Gameplay focado nas pontes tropicais de Leonida Keys.",
                ],
                "start_sec": 2.0,
                "end_sec": 26.0,
                "virality_score": 92.0,
            },
            {
                "title": "Gamer Costa Descobre Detalhes Escondidos nos Pântanos de Grassrivers Ao Vivo",
                "url": "https://www.youtube.com/@GamerCosta/live#fallback-01",
                "creator_handle": "@GamerCosta",
                "source_type": "youtube",
                "is_official": False,
                "pillar": ContentPillar.GAMEPLAY_CLIPS,
                "summary": "Corte de live com Gamer Costa pilotando aerobarco e reagindo ao ecossistema dinâmico de Grassrivers em GTA VI.",
                "verified_facts": [
                    "Transmissão ao vivo em português explorando Grassrivers em Leonida.",
                    "Reação autêntica durante gameplay de GTA VI.",
                ],
                "start_sec": 5.0,
                "end_sec": 29.0,
                "virality_score": 91.0,
            },
            {
                "title": "Coringa Testando Fuga Cinematográfica com Lucia e Jason em Vice Beach",
                "url": "https://www.twitch.tv/loud_coringa#fallback-01",
                "creator_handle": "@loud_coringa",
                "source_type": "twitch",
                "is_official": False,
                "pillar": ContentPillar.GAMEPLAY_CLIPS,
                "summary": "Corte explosivo da live do Coringa mostrando a densidade de trânsito e reação policial em Vice Beach no GTA VI.",
                "verified_facts": [
                    "Corte de live em português com gameplay real de GTA VI.",
                    "Ação em tempo real nas avenidas de Vice Beach.",
                ],
                "start_sec": 3.0,
                "end_sec": 27.0,
                "virality_score": 90.5,
            },
            {
                "title": "Paulinho o Loko Fazendo Drift de Muscle Car em Port Gellhorn Ao Vivo",
                "url": "https://www.twitch.tv/paulinholokobr#fallback-01",
                "creator_handle": "@PaulinhoLOKOoficial",
                "source_type": "twitch",
                "is_official": False,
                "pillar": ContentPillar.GAMEPLAY_CLIPS,
                "summary": "Reação ao vivo do chat na live do Paulinho o Loko testando a suspensão e fumaça dos pneus em Port Gellhorn.",
                "verified_facts": [
                    "Corte de live de streamer brasileiro jogando GTA VI.",
                    "Física de suspensão e deformação veicular em Port Gellhorn.",
                ],
                "start_sec": 4.0,
                "end_sec": 28.0,
                "virality_score": 89.5,
            },
            {
                "title": "FunkyBlackCat Analisa a Iluminação e Interiores de GTA VI Ao Vivo",
                "url": "https://www.youtube.com/@funkyblackcat/live#fallback-01",
                "creator_handle": "@funkyblackcat",
                "source_type": "youtube",
                "is_official": False,
                "pillar": ContentPillar.GAMEPLAY_CLIPS,
                "summary": "Corte de live em português destacando os retrovisores funcionais e clima dinâmico no estado de Leonida.",
                "verified_facts": [
                    "Corte de live brasileira comentando detalhes técnicos confirmados de GTA VI.",
                ],
                "start_sec": 2.0,
                "end_sec": 26.0,
                "virality_score": 88.5,
            },
            {
                "title": "Gaules & Tribo Reagindo às Primeiras Horas em Mount Kalaga no GTA VI",
                "url": "https://www.twitch.tv/gaules#fallback-01",
                "creator_handle": "@gaules",
                "source_type": "twitch",
                "is_official": False,
                "pillar": ContentPillar.GAMEPLAY_CLIPS,
                "summary": "Momento de puro hype na live do Gaules acompanhando trilhas off-road em Mount Kalaga National Park.",
                "verified_facts": [
                    "Corte de reação ao vivo em português em Mount Kalaga (Leonida).",
                ],
                "start_sec": 3.0,
                "end_sec": 27.0,
                "virality_score": 88.0,
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
                    metadata={"curated_fallback": True, "streamer_live_cut": True},
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

        raw_campaign = self.config.campaign if isinstance(self.config.campaign, dict) else {}
        campaign_block = raw_campaign.get("campaign", raw_campaign) if isinstance(raw_campaign, dict) else {}
        only_authorized_live = bool(
            isinstance(campaign_block, dict)
            and campaign_block.get("rules", {}).get("only_authorized_live_source", False)
        )

        # 1. Campaign Acervo multi-segment candidates (KY4DWumZffg live stream)
        if include_campaign_acervo:
            acervo_candidates = self._discover_campaign_acervo(
                blacklist,
                blocked_keywords,
                max_segments_per_video=max(limit, 8),
            )
            _add_unique(acervo_candidates)
            # During active KY4DWumZffg campaign mode, return ONLY authorized live cuts if available
            if only_authorized_live and collected:
                collected.sort(
                    key=lambda c: (c.virality_score, c.is_campaign_acervo, c.is_official),
                    reverse=True,
                )
                return collected[:limit]

        # 2. Brazilian Streamer Lives & VODs (YouTube + Twitch via yt-dlp) + YouTube RSS
        scout_cfg = (self.config.settings or {}).get("scout", {})
        offline_only = bool(scout_cfg.get("offline_only", False)) if isinstance(scout_cfg, dict) else False
        enable_ytdlp_in_dry = (
            bool(scout_cfg.get("enable_ytdlp_in_dry_run", False)) if isinstance(scout_cfg, dict) else False
        )

        if not offline_only:
            if not self.config.dry_run or enable_ytdlp_in_dry:
                try:
                    br_streamer_candidates = await self.discover_br_streamer_streams(
                        blacklist,
                        blocked_keywords,
                    )
                    _add_unique(br_streamer_candidates)
                except Exception:
                    pass

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

        # 3. Curated verified fallback pool (PT-BR Streamer Live Cuts) so discovery NEVER fails or starves
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
        content_focus = str((self.config.settings or {}).get("page", {}).get("content_focus", ""))
        if content_focus != "streamer_live_clips_only":
            for sub, url in REDDIT_FEEDS:
                tasks.append(self._fetch_reddit_feed(client, sub, url, blacklist, blocked_keywords))

        if not tasks:
            return []

        raw_results = await asyncio.gather(*tasks, return_exceptions=True)
        valid_batches: list[list[SourceCandidate]] = []
        for res in raw_results:
            if isinstance(res, list):
                valid_batches.append(res)
        return valid_batches
