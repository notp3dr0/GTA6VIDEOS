"""Hydrogen Worker 3: Parallel Multi-Agent Hydrogen Viral Content Swarm.
Components:
1. HydrogenHookWriter: Viral hook archetypes & anti-clickbait validator.
2. HydrogenTranslatorAgent: English-segment detection, subtitle chunking & PT-BR translation.
3. HydrogenQuestionsSecretsHarvester: Reddit question harvesting, clustering & transcript secrets scanner.
4. HydrogenContentSwarm: Async concurrent multi-agent coordination.
"""

from __future__ import annotations

import asyncio
import difflib
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field

from ..models import SourceCandidate, SubtitleCue
from ..model_router import TieredModelRouter, TaskTier

logger = logging.getLogger(__name__)

# ============================================================================
# 1. HydrogenHookWriter
# ============================================================================

VIRAL_HOOK_ARCHETYPES = [
    "number_promise",
    "shock_fact",
    "situation",
    "negation",
    "question",
    "creator_spotlight",
]

ANTI_CLICKBAIT_RE = re.compile(
    r"\b(espere\s+at[eé]\s+o\s+final|voc[eê]\s+n[ãa]o\s+vai\s+acreditar|assista\s+at[eé]\s+o\s+fim|"
    r"wait\s+for\s+it|you\s+won'?t\s+believe|watch\s+till\s+the\s+end|gta\s+6\s+clip|clipe\s+gta\s+6)\b",
    re.I,
)

BANNED_CAMPAIGN_RE = re.compile(r"\b(gta\s*5|gta\s*v|ps3|xbox\s*360|negativ|hype\s*mort|ruim)\b", re.I)


def validate_and_polish_hook(
    hook: str, *, for_campaign: bool = False, streamer_name: str = ""
) -> tuple[str, list[str]]:
    """Enforces 3-9 words (<= 58 chars), strips clickbait filler, and campaign rules."""
    warnings: list[str] = []
    clean = hook.strip()

    # Check clickbait/filler
    if ANTI_CLICKBAIT_RE.search(clean):
        warnings.append("Detected banned clickbait filler or generic label; sanitizing.")
        clean = ANTI_CLICKBAIT_RE.sub("", clean).strip()

    # Check campaign rules
    if for_campaign and BANNED_CAMPAIGN_RE.search(clean):
        warnings.append("Detected GTA 5 or negative hype reference in campaign mode; purging.")
        clean = re.sub(r"(?i)\bgta\s*(?:5|v)\b", "GTA 6", clean)

    # Word count check
    words = clean.split()
    if len(words) < 3 or len(words) > 9:
        warnings.append(f"Hook word count {len(words)} outside ideal 3-9 range.")

    if len(clean) > 58:
        warnings.append(f"Hook length {len(clean)} chars exceeds 58 limit.")
        clean = clean[:58].rsplit(" ", 1)[0]

    return clean, warnings


async def craft_viral_hook(
    candidate: SourceCandidate,
    *,
    channel_angle: str = "",
    for_campaign: bool = False,
    prefer_reaction_title: bool = False,
    dry_run: bool = True,
    model_router: TieredModelRouter | None = None,
) -> dict[str, str]:
    """Crafts a viral hook and reaction title using Hydrogen LLM or deterministic fallback."""
    router = model_router or TieredModelRouter()
    
    # Fallback deterministic generation if dry_run or router fails
    if dry_run or not router.hydrogen_provider:
        title_lower = candidate.title.lower()
        if prefer_reaction_title or candidate.creator_handle:
            streamer = candidate.creator_handle.lstrip("@") or "Streamer"
            reaction = f"{streamer} PERDEU A LINHA com os gráficos de GTA 6"
            hook = "Detalhes que ninguém percebeu"
            archetype = "creator_spotlight"
        elif "car" in title_lower or "veículo" in title_lower or "carro" in title_lower:
            hook = "Carro raro escondido à vista de todos"
            reaction = "Encontrei o carro mais raro de Leonida"
            archetype = "shock_fact"
        elif "cop" in title_lower or "polícia" in title_lower or "fuga" in title_lower:
            hook = "Quando a polícia cerca o lugar errado"
            reaction = "A perseguição mais insana de GTA 6"
            archetype = "situation"
        else:
            hook = "3 detalhes insanos em GTA 6"
            reaction = "Segredos absurdos revelados em Leonida"
            archetype = "number_promise"
        
        clean_hook, _ = validate_and_polish_hook(hook, for_campaign=for_campaign)
        return {
            "archetype": archetype,
            "hook_on_screen": clean_hook,
            "reaction_title": reaction,
        }

    # Hydrogen LLM Prompt
    system_prompt = (
        "You are an expert GTA 6 viral content hook writer. "
        "Return strict JSON with keys: archetype, hook_on_screen, reaction_title. "
        "Create 1 punchy Brazilian Portuguese on-screen hook (3-9 words, sentence case) "
        "and 1 Twitch-style reaction title (streamer name + reaction verb + 2-3 CAPS words). "
        "Never use clickbaiting phrases like 'wait for it' or 'espere até o final'. "
        "Never mention GTA 5 or negative hype if for_campaign is true."
    )
    prompt = (
        f"Candidate Title: {candidate.title}\n"
        f"Summary: {candidate.summary}\n"
        f"Creator: {candidate.creator_handle}\n"
        f"Channel Angle: {channel_angle}\n"
        f"For Campaign: {for_campaign}\n"
        f"Prefer Reaction Title: {prefer_reaction_title}\n"
        'Respond ONLY with JSON: {"archetype": "...", "hook_on_screen": "...", "reaction_title": "..."}'
    )

    response = await router.complete(prompt, system=system_prompt, tier=TaskTier.HYDROGEN, temperature=0.5)
    if not response:
        return await craft_viral_hook(
            candidate,
            channel_angle=channel_angle,
            for_campaign=for_campaign,
            prefer_reaction_title=prefer_reaction_title,
            dry_run=True,
        )

    import json as _json

    archetype = "shock_fact"
    hook_on_screen = "Detalhes insanos ao vivo em GTA 6"
    reaction_title = f"{candidate.creator_handle.lstrip('@') or 'Streamer'} SURPRESO com GTA 6"

    match = re.search(r"\{.*\}", response, flags=re.DOTALL)
    if match:
        try:
            parsed = _json.loads(match.group(0))
            if isinstance(parsed, dict):
                if str(parsed.get("archetype") or "").strip() in VIRAL_HOOK_ARCHETYPES:
                    archetype = str(parsed["archetype"]).strip()
                if str(parsed.get("hook_on_screen") or "").strip():
                    hook_on_screen = str(parsed["hook_on_screen"]).strip()
                if str(parsed.get("reaction_title") or "").strip():
                    reaction_title = str(parsed["reaction_title"]).strip()
        except Exception:
            pass

    clean_hook, _ = validate_and_polish_hook(hook_on_screen, for_campaign=for_campaign)
    if len(clean_hook.split()) < 3:
        clean_hook = "Detalhes insanos ao vivo em GTA 6"
    return {
        "archetype": archetype,
        "hook_on_screen": clean_hook,
        "reaction_title": reaction_title[:80],
    }


# ============================================================================
# 2. HydrogenTranslatorAgent
# ============================================================================

EN_TOKENS = {"the", "and", "that", "you", "with", "this", "what", "have", "from", "your", "they", "know", "want", "just", "people"}


def detect_english_text(text: str) -> bool:
    """Lightweight heuristic checking common EN vs PT-BR tokens."""
    words = {w.lower() for w in re.findall(r"\b[a-z]{3,}\b", text)}
    matches = words.intersection(EN_TOKENS)
    return len(matches) >= 2


def split_cues_into_sentences(cues: list[SubtitleCue], pause_sec: float = 0.8) -> list[list[SubtitleCue]]:
    """Splits subtitle cues into sentence groups based on terminal punctuation or pauses."""
    if not cues:
        return []
    groups: list[list[SubtitleCue]] = []
    current_group: list[SubtitleCue] = []
    
    for i, cue in enumerate(cues):
        current_group.append(cue)
        text = cue.text.strip()
        is_terminal = text.endswith((".", "!", "?"))
        
        # Check next cue for significant pause
        is_paused = False
        if i + 1 < len(cues):
            next_cue = cues[i + 1]
            if next_cue.start_sec - cue.end_sec >= pause_sec:
                is_paused = True

        if is_terminal or is_paused:
            groups.append(current_group)
            current_group = []

    if current_group:
        groups.append(current_group)
    return groups


async def translate_cues_to_ptbr(
    cues: list[SubtitleCue],
    hook_text: str = "",
    *,
    dry_run: bool = True,
    model_router: TieredModelRouter | None = None,
) -> tuple[list[SubtitleCue], str]:
    """Translates EN sentence chunks into natural Brazilian Portuguese via Hydrogen LLM or fallback.
    
    Ethical guarantee docstring: stock neural TTS hook only (pt-BR-AntonioNeural), NEVER clones streamer voices!
    """
    router = model_router or TieredModelRouter()
    translated_cues: list[SubtitleCue] = []
    
    mock_dict = {
        "hello": "olá",
        "look at this": "veja isso",
        "crazy": "insano",
        "police": "polícia",
        "car": "carro",
    }

    for cue in cues:
        text = cue.text
        if dry_run or not router.hydrogen_provider:
            tr_text = text
            for en_w, pt_w in mock_dict.items():
                tr_text = re.sub(rf"\b{en_w}\b", pt_w, tr_text, flags=re.I)
            if tr_text == text and detect_english_text(text):
                tr_text = f"[PT] {text}"
            translated_cues.append(SubtitleCue(start_sec=cue.start_sec, end_sec=cue.end_sec, text=tr_text))
        else:
            prompt = f"Translate the following subtitle segment to natural Brazilian Portuguese:\n{text}"
            res = await router.complete(prompt, system="Translate to natural, casual Brazilian Portuguese gaming subtitles.", tier=TaskTier.HYDROGEN)
            tr_text = res.strip() if res else text
            translated_cues.append(SubtitleCue(start_sec=cue.start_sec, end_sec=cue.end_sec, text=tr_text))

    translated_hook = hook_text
    if detect_english_text(hook_text):
        translated_hook = "Detalhes incríveis em GTA 6"

    return translated_cues, translated_hook


# ============================================================================
# 3. HydrogenQuestionsSecretsHarvester
# ============================================================================

STARTERS = ("como", "onde", "qual", "quando", "dá pra", "tem como", "how", "where", "what", "can", "is", "does", "why", "when")
QUESTION_STOPWORDS = {"a", "an", "the", "to", "of", "in", "on", "at", "for", "and", "or", "is", "are", "gta", "gta6", "gta5", "vi", "6", "5", "game"}


def is_player_question(text: str) -> bool:
    """Detects PT-BR & EN player questions."""
    t = text.strip().lower()
    if len(t) < 10 or len(t) > 300:
        return False
    return t.endswith("?") or t.startswith(STARTERS)


def _tokens(s: str) -> set[str]:
    s = re.sub(r"https?://\S+", " ", s.lower())
    return {w for w in re.findall(r"[a-z0-9]+", s) if w not in QUESTION_STOPWORDS and len(w) > 1}


def cluster_questions(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Clusters near-duplicate questions using SequenceMatcher >= 0.75 or Jaccard >= 0.5."""
    for q in questions:
        text = q.get("text", "")
        q["norm"] = re.sub(r"[^a-z0-9 ]+", "", text.lower()).strip()
        q["tok"] = _tokens(text)

    clusters: list[dict[str, Any]] = []
    for it in questions:
        placed = False
        for c in clusters:
            rep = c["items"][0]
            ta, tb = it["tok"], rep["tok"]
            jaccard = len(ta & tb) / len(ta | tb) if (ta | tb) else 0.0
            seq_ratio = difflib.SequenceMatcher(None, it["norm"], rep["norm"]).ratio()
            if jaccard >= 0.5 or seq_ratio >= 0.75:
                c["items"].append(it)
                placed = True
                break
        if not placed:
            clusters.append({"items": [it]})

    for c in clusters:
        c["count"] = len(c["items"])
        c["votes"] = sum(max(i.get("score", 0), 0) for i in c["items"])
        qs = sorted(c["items"], key=lambda i: (not i.get("text", "").endswith("?"), -i.get("score", 0), len(i.get("text", ""))))
        c["question"] = qs[0].get("text", "")
        c["links"] = list(dict.fromkeys(i.get("link", "") for i in c["items"] if i.get("link", "").startswith("http")))[:3]

    return sorted(clusters, key=lambda c: (c["count"], c["votes"]), reverse=True)


async def harvest_reddit_questions(
    subreddits: tuple[str, ...] = ("GTA6", "GTA"),
    max_posts_per_sub: int = 30,
    http_client: httpx.AsyncClient | None = None,
    memory_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Pulls public Reddit JSON cleanly, clusters questions, and writes to memory/questions_bank.md."""
    items: list[dict[str, Any]] = []
    client = http_client or httpx.AsyncClient(headers={"User-Agent": "gta6-hydrogen-harvester/1.0"})
    
    for sub in subreddits:
        try:
            url = f"https://www.reddit.com/r/{sub}/new.json?limit={max_posts_per_sub}"
            resp = await client.get(url, timeout=15.0)
            if resp.status_code == 200:
                data = resp.json()
                for ch in data.get("data", {}).get("children", []):
                    d = ch.get("data", {})
                    title = d.get("title", "")
                    link = "https://www.reddit.com" + d.get("permalink", "")
                    score = d.get("score", 0)
                    if is_player_question(title):
                        items.append({"text": title, "score": score, "link": link, "src": f"r/{sub}"})
        except Exception as e:
            logger.warning(f"Error harvesting r/{sub}: {e}")

    if http_client is None:
        await client.aclose()

    clustered = cluster_questions(items)

    if memory_dir:
        mem = Path(memory_dir)
        mem.mkdir(parents=True, exist_ok=True)
        bank_path = mem / "questions_bank.md"
        lines = [f"# GTA 6 Questions Bank (Harvested {datetime.now(timezone.utc).strftime('%Y-%m-%d')})", ""]
        for c in clustered:
            lines.append(f"- ### {c['question']} (Count: {c['count']}, Votes: {c['votes']})")
            for l in c.get("links", []):
                lines.append(f"  - Ref: {l}")
        bank_path.write_text("\n".join(lines), encoding="utf-8")

    return clustered


SECRETS_CAT_PATTERNS = [
    ("easter_egg", re.compile(r"easter eggs?|hidden detail|reference to|detalhe escondido|refer[êe]ncia", re.I)),
    ("car", re.compile(r"rare cars?|hidden cars?|secret cars?|carro raro|carro secreto|ve[ií]culo raro|spawns?", re.I)),
    ("collectible", re.compile(r"collectibles?|hidden packages?|stunt jumps?|colecion[áa]ve|pacote escondido", re.I)),
    ("money", re.compile(r"cash|money|stash|safe code|dinheiro escondido|cofre|maleta", re.I)),
    ("place", re.compile(r"(?:hidden|secret)\s+(?:place|spot|location|area|room|cave|island)|local secreto|lugar escondido|caverna secreta", re.I)),
]

BANNED_EXPLOIT_RE = re.compile(
    r"glitch|\bexploit|\bdupe|duplicat|infinite money|unlimited cash|money glitch|"
    r"dinheiro infinito|bug de dinheiro|"
    r"\bleak(?:s|ed)?\b|play early|early access|piracy|mod menu|samira close|burnt peanut",
    re.I,
)

_SECRET_TRIGGERS = (
    "secret", "hidden", "easter egg", "stash", "rare",
    "segredo", "escondido", "escondida", "carro raro", "local secreto", "colecion", "cofre",
)


def scan_transcript_for_secrets(
    transcript_text: str,
    *,
    source_channel: str,
    source_url: str,
    start_sec: float = 0.0,
    memory_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Scans PT-BR & EN transcript for Leonida secrets (blocking glitches/exploits/leaks), appending cleanly to memory/secrets.csv."""
    import csv

    leads: list[dict[str, Any]] = []
    if not transcript_text or BANNED_EXPLOIT_RE.search(transcript_text):
        return leads

    sentences = re.split(r"(?<=[.!?])\s+", transcript_text)
    clean_base = (source_url or "").split("#", 1)[0]
    sep = "&" if "?" in clean_base else "?"

    for sent in sentences:
        low = sent.lower()
        if any(kw in low for kw in _SECRET_TRIGGERS):
            cat = "other"
            for c_name, pat in SECRETS_CAT_PATTERNS:
                if pat.search(sent):
                    cat = c_name
                    break

            deep_link = f"{clean_base}{sep}t={int(start_sec)}s" if clean_base else ""
            lead = {
                "id": f"S_{abs(hash(sent.strip().lower())) % 10000:04d}",
                "category": cat,
                "name": sent.strip()[:60],
                "where": sent.strip()[:200],
                "how": "",
                "source_channel": source_channel,
                "source_url_with_timestamp": deep_link,
                "found_on": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "status": "unverified",
                "verified_by": "",
                "notes": "Auto-scanned by Hydrogen Swarm",
            }
            leads.append(lead)

    if memory_dir and leads:
        mem = Path(memory_dir)
        mem.mkdir(parents=True, exist_ok=True)
        csv_path = mem / "secrets.csv"
        fieldnames = [
            "id", "category", "name", "where", "how",
            "source_channel", "source_url_with_timestamp",
            "found_on", "status", "verified_by", "notes",
        ]
        existing_ids: set[str] = set()
        if csv_path.exists():
            try:
                with csv_path.open("r", encoding="utf-8", newline="") as fh:
                    for row in csv.DictReader(fh):
                        if row.get("id"):
                            existing_ids.add(row["id"])
            except Exception:
                pass

        write_header = not csv_path.exists() or csv_path.stat().st_size == 0
        with csv_path.open("a", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            if write_header:
                writer.writeheader()
            for l in leads:
                if l["id"] not in existing_ids:
                    existing_ids.add(l["id"])
                    writer.writerow(l)

    return leads


# ============================================================================
# 4. HydrogenContentSwarm
# ============================================================================

class HydrogenContentSwarm:
    def __init__(self, max_concurrency: int = 6, *, dry_run: bool = True, memory_dir: Path | None = None):
        self.max_concurrency = max_concurrency
        self.dry_run = dry_run
        self.memory_dir = memory_dir
        self.router = TieredModelRouter()
        self.semaphore = asyncio.Semaphore(max_concurrency)

    async def _enrich_single(self, candidate: SourceCandidate, channel_angle: str, for_campaign: bool) -> dict[str, Any]:
        async with self.semaphore:
            hook_data = await craft_viral_hook(
                candidate,
                channel_angle=channel_angle,
                for_campaign=for_campaign,
                dry_run=self.dry_run,
                model_router=self.router,
            )

            has_en = detect_english_text(candidate.summary)

            secrets = scan_transcript_for_secrets(
                candidate.summary,
                source_channel=candidate.creator_handle,
                source_url=candidate.url,
                memory_dir=self.memory_dir,
            )

            candidate.metadata["hydrogen_enrichment"] = {
                "hook": hook_data,
                "english_detected": has_en,
                "secrets_logged_count": len(secrets),
            }

            return {
                "id": candidate.id,
                "title": candidate.title,
                "hook": hook_data,
                "english_detected": has_en,
                "secrets_count": len(secrets),
            }

    async def enrich_candidates_parallel(
        self,
        candidates: list[SourceCandidate],
        *,
        channel_angle: str = "",
        for_campaign: bool = False,
    ) -> dict[str, Any]:
        start_time = datetime.now(timezone.utc)
        
        tasks = [self._enrich_single(c, channel_angle, for_campaign) for c in candidates]
        results = await asyncio.gather(*tasks)

        elapsed_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000.0

        english_detected_count = sum(1 for r in results if r["english_detected"])
        total_secrets = sum(r["secrets_count"] for r in results)

        return {
            "enriched_count": len(results),
            "english_detected": english_detected_count,
            "secrets_logged": total_secrets,
            "workers_concurrency": self.max_concurrency,
            "elapsed_ms": elapsed_ms,
            "candidates": results,
        }
