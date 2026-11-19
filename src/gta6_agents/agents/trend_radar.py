"""Keyless multi-platform live and trend radar for Twitch, Kick, and YouTube using pure Python stdlib and httpx."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any

import httpx

from gta6_agents.video.downloader import _resolve_ytdlp_cmd, _run_subprocess

logger = logging.getLogger(__name__)


@dataclass
class LiveStreamHit:
    platform: str
    channel_login: str
    display_name: str
    title: str
    url: str
    viewers: int
    language: str
    is_live: bool = True
    started_at: str = ""


@dataclass
class TrendingVideoHit:
    video_id: str
    title: str
    channel: str
    url: str
    views: int
    age_hours: float
    views_per_hour: float
    duration_sec: float
    has_heatmap: bool
    heatmap: list[dict[str, Any]]


TWITCH_PUBLIC_GQL_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"


async def discover_twitch_live_gql(
    game_names: list[str] | None = None,
    *,
    language_filter: str = "pt",
    limit: int = 15,
    timeout_sec: float = 8.0,
    http_client: httpx.AsyncClient | None = None,
) -> list[LiveStreamHit]:
    """Discover live GTA VI streams on Twitch using public GQL endpoint without API keys."""
    names = game_names or ["Grand Theft Auto VI", "GTA VI"]
    endpoint = "https://gql.twitch.tv/gql"
    headers = {
        "Client-Id": TWITCH_PUBLIC_GQL_CLIENT_ID,
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    queries: list[str] = []
    for gname in names:
        queries.append(
            f"""
            query {{
              game(name: "{gname}") {{
                streams(first: {limit}, options: {{ sort: VIEWER_COUNT }}) {{
                  edges {{
                    node {{
                      id
                      title
                      viewersCount
                      createdAt
                      broadcasterLanguage
                      broadcaster {{
                        login
                        displayName
                      }}
                    }}
                  }}
                }}
              }}
            }}
            """
        )

    client = http_client or httpx.AsyncClient(timeout=timeout_sec)
    owns_client = http_client is None
    all_hits: dict[str, LiveStreamHit] = {}

    try:
        for q_str in queries:
            try:
                resp = await client.post(endpoint, json={"query": q_str}, headers=headers)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                if not isinstance(data, dict):
                    continue
                data_inner = data.get("data")
                if not isinstance(data_inner, dict):
                    continue
                game_obj = data_inner.get("game")
                if not isinstance(game_obj, dict) or not game_obj:
                    continue
                streams_obj = game_obj.get("streams")
                if not isinstance(streams_obj, dict):
                    continue
                edges = streams_obj.get("edges")
                if not isinstance(edges, list):
                    continue

                lang_pref = (language_filter or "").strip().lower()

                for edge in edges:
                    if not isinstance(edge, dict):
                        continue
                    node = edge.get("node")
                    if not isinstance(node, dict):
                        continue
                    broadcaster = node.get("broadcaster")
                    if not isinstance(broadcaster, dict):
                        continue
                    login = str(broadcaster.get("login") or "").strip()
                    if not login or login in all_hits:
                        continue

                    display_name = str(broadcaster.get("displayName") or login)
                    title = str(node.get("title") or "")
                    viewers = int(node.get("viewersCount") or 0)
                    lang = str(node.get("broadcasterLanguage") or "").lower()
                    started_at = str(node.get("createdAt") or "")

                    if lang_pref and lang:
                        if lang_pref == "pt":
                            if not lang.startswith("pt") and lang != "portuguese":
                                continue
                        elif lang_pref not in lang and lang not in lang_pref:
                            continue

                    url = f"https://www.twitch.tv/{login}"
                    all_hits[login] = LiveStreamHit(
                        platform="twitch",
                        channel_login=login,
                        display_name=display_name,
                        title=title,
                        url=url,
                        viewers=viewers,
                        language=lang,
                        is_live=True,
                        started_at=started_at,
                    )
            except Exception as exc:
                logger.debug("Twitch GQL query failed for game query: %s", exc)
    finally:
        if owns_client:
            await client.aclose()

    hits = list(all_hits.values())
    hits.sort(key=lambda x: x.viewers, reverse=True)
    return hits[:limit]


async def discover_kick_live(
    query: str = "GTA",
    limit: int = 10,
    timeout_sec: float = 6.0,
    http_client: httpx.AsyncClient | None = None,
) -> list[LiveStreamHit]:
    """Discover live GTA streams on Kick via public channels API."""
    url = f"https://kick.com/api/v2/channels?query={urllib.parse.quote(query)}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
    }
    client = http_client or httpx.AsyncClient(timeout=timeout_sec)
    owns_client = http_client is None

    try:
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            return []
        data = resp.json()
        if not isinstance(data, (dict, list)):
            return []

        channels = data.get("data") if isinstance(data, dict) else data
        if not isinstance(channels, list):
            return []

        hits: list[LiveStreamHit] = []
        for ch in channels:
            if not isinstance(ch, dict):
                continue
            is_live = bool(ch.get("is_live", False) or ch.get("livestream"))
            if not is_live:
                continue

            user_obj = ch.get("user") or ch.get("broadcaster") or {}
            login = str(user_obj.get("username") or user_obj.get("slug") or ch.get("slug") or "").strip()
            if not login:
                continue

            display_name = str(user_obj.get("username") or login)
            livestream = ch.get("livestream") if isinstance(ch.get("livestream"), dict) else ch
            title = str(livestream.get("session_title") or livestream.get("title") or ch.get("name") or "")
            viewers = int(livestream.get("viewer_count") or livestream.get("viewers") or 0)
            lang = str(livestream.get("language") or "pt").lower()

            # Filter for GTA 6 / VI relevance if needed
            title_lower = title.lower()
            if "gta" not in title_lower and "grand theft auto" not in title_lower:
                continue

            stream_url = f"https://kick.com/{login}"
            hits.append(
                LiveStreamHit(
                    platform="kick",
                    channel_login=login,
                    display_name=display_name,
                    title=title,
                    url=stream_url,
                    viewers=viewers,
                    language=lang,
                    is_live=True,
                )
            )

        hits.sort(key=lambda x: x.viewers, reverse=True)
        return hits[:limit]
    except Exception as exc:
        logger.debug("discover_kick_live failed: %s", exc)
        return []
    finally:
        if owns_client:
            await client.aclose()


async def scan_youtube_trend_velocity(
    query: str = "GTA 6 gameplay pt br",
    *,
    max_age_hours: float = 24.0,
    limit: int = 10,
    timeout_sec: float = 18.0,
) -> list[TrendingVideoHit]:
    """Scan YouTube upload search for high-velocity GTA 6 trending videos."""
    search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}&sp=CAI%253D"
    cmd = [
        *_resolve_ytdlp_cmd(),
        "--flat-playlist",
        "--dump-json",
        "--no-warnings",
        "--socket-timeout",
        "10",
        search_url,
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        if proc.returncode != 0 or not stdout_b.strip():
            return []

        import time

        now_ts = time.time()
        hits: list[TrendingVideoHit] = []

        for line in stdout_b.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            if not isinstance(item, dict):
                continue

            vid = str(item.get("id") or item.get("display_id") or "")
            if not vid:
                continue

            url = str(item.get("url") or f"https://www.youtube.com/watch?v={vid}")
            title = str(item.get("title") or "")
            channel = str(item.get("uploader") or item.get("channel") or "")
            duration = float(item.get("duration") or 0.0)

            title_lower = title.lower()
            # Skip shorts or non-GTA6 or GTA 5 RP clickbait if needed
            if "/shorts/" in url or "shorts" in title_lower:
                continue
            if "gta v" in title_lower and "gta vi" not in title_lower and "gta 6" not in title_lower:
                continue

            views = int(item.get("view_count") or 0)
            # Estimate upload timestamp if available
            timestamp = item.get("timestamp") or item.get("release_timestamp")
            if timestamp:
                age_h = max(0.25, (now_ts - float(timestamp)) / 3600.0)
            else:
                age_h = 12.0  # default fallback age

            if age_h > max_age_hours and max_age_hours > 0:
                continue

            views_per_h = views / age_h
            heatmap = item.get("heatmap") if isinstance(item.get("heatmap"), list) else []

            hits.append(
                TrendingVideoHit(
                    video_id=vid,
                    title=title,
                    channel=channel,
                    url=url,
                    views=views,
                    age_hours=round(age_h, 2),
                    views_per_hour=round(views_per_h, 2),
                    duration_sec=duration,
                    has_heatmap=bool(heatmap),
                    heatmap=heatmap,
                )
            )

        hits.sort(key=lambda x: x.views_per_hour, reverse=True)
        return hits[:limit]
    except Exception as exc:
        logger.debug("scan_youtube_trend_velocity failed for query %s: %s", query, exc)
        return []
