"""Compliance, Fact-Checking, Deduplication & Prompt-Injection Sanitization Agent."""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Iterable

from gta6_agents.agents.live_campaign_ky4 import (
    AUTHORIZED_LIVE_VIDEO_ID,
    MANDATORY_CAPTION_PHRASE,
    MANDATORY_ONLY_HASHTAG,
)
from gta6_agents.config import AppConfig
from gta6_agents.models import (
    ApprovalStatus,
    CaptionBundle,
    ClipPlan,
    ComplianceVerdict,
    ContentPillar,
    Platform,
)
from gta6_agents.storage import StateStore

VALID_CAMPAIGN_PLATFORMS: frozenset[Platform] = frozenset(
    {Platform.INSTAGRAM_REELS, Platform.TIKTOK}
)
FORBIDDEN_TRAILER_VIDEO_IDS: tuple[str, ...] = ("QdBZY2fkU-0", "VQRLujxTm3c")

DEFAULT_BLACKLIST_HANDLES: set[str] = {
    "gta6leakshub",
    "vicecitydataminer",
    "fakerockstarinsider",
    "norepostvault",
}

STANDARD_PILLARS: set[ContentPillar] = {
    ContentPillar.OFFICIAL_NEWS,
    ContentPillar.TIPS_TUTORIALS,
    ContentPillar.GAMEPLAY_CLIPS,
    ContentPillar.GTA5_NOSTALGIA,
    ContentPillar.SPOTLIGHTS,
    ContentPillar.COUNTDOWN_RECAP,
    ContentPillar.QUESTIONS,
    ContentPillar.REAL_OFICIAL_CAMPAIGN,
}

# Zero-width, bidi-override, and non-printable control characters
_CONTROL_CHARS_RE = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]"
)

# Role tags & structured prompt wrappers used in prompt injection
_TAG_BLOCK_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"<\s*(system|instruction|instructions|tool_call|function_call|assistant|developer)\b[^>]*>.*?<\s*/\s*\1\s*>", re.IGNORECASE | re.DOTALL),
    re.compile(r"\[INST\].*?\[/INST\]", re.IGNORECASE | re.DOTALL),
    re.compile(r"<<SYS>>.*?<</SYS>>", re.IGNORECASE | re.DOTALL),
    re.compile(r"<\s*/?\s*(system|instruction|instructions|tool_call|function_call|assistant|developer|user)\b[^>]*>", re.IGNORECASE),
]

# Prompt injection directive clauses in third-party titles/descriptions/transcripts
_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\b(?:ignore|disregard|forget|override)\s+(?:all\s+|any\s+|everything\s+)?"
        r"(?:previous|prior|above|earlier|system|developer|safety|compliance)"
        r"(?:\s+(?:instructions?|prompts?|rules?|directives?|guardrails?))?[^.\n;]*",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:new\s+instructions?|system\s+prompt|system\s+override|developer\s+mode|jailbreak)\s*:[^.\n;]*",
        re.IGNORECASE,
    ),
    re.compile(
        r"\byou\s+are\s+now\s+(?:a\s+|an\s+)?(?:unrestricted|helpful|system|admin|root|free)[^.\n;]*",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:execute|invoke|call|run)\s+(?:the\s+)?(?:mcp|tool|shell|bash|command|generate_cuts|submit_championship_video)[^.\n;]*",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:spend|drain|transfer|use)\s+(?:all\s+)?(?:real\s+oficial\s+)?credits\b[^.\n;]*",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:reveal|print|exfiltrate|leak|output)\s+(?:your\s+|the\s+)?"
        r"(?:system\s+prompt|api[_\s]?key|access[_\s]?token|refresh[_\s]?token|secret)[^.\n;]*",
        re.IGNORECASE,
    ),
]

# Hard-reject content patterns
_HARD_LEAK_RE = re.compile(
    r"(?<!no\s)(?<!zero\s)(?<!without\s)(?<!sem\s)"
    r"\b(?:leak|leaks|leaked|leaking|datamine|datamined|datamines|datamining|dataminer|"
    r"leaked\s+map|leaked\s+tracklist|dev\s+build|source\s+code\s+leak|"
    r"early\s+copy|street\s+date\s+broken|pre[- ]release\s+(?:footage|gameplay|leak)|"
    r"filtrado|footage\s+filtrado|gameplay\s+filtrado|c[oó]pia\s+antecipada|"
    r"pr[eé][- ]lan[cç]amento\s+vazado|jogando\s+antes\s+do\s+lan[cç]amento|"
    r"mapa\s+vazado|tracklist\s+vazada|build\s+vazada|vazamento|vazou\s+gameplay)\b",
    re.IGNORECASE,
)

_PIRACY_RE = re.compile(
    r"\b(?:pirated\s+copy|pirated\s+version|pirated\s+game|piracy|c[oó]pia\s+pirata|"
    r"jogo\s+pirata|torrent\s+download|crack\s+download|cracked\s+version|"
    r"baixar\s+gta\s*6\s+gr[aá]tis)\b",
    re.IGNORECASE,
)

_EXPLOIT_RE = re.compile(
    r"\b(?:code\s+exploit|money\s+glitch|god\s*mode\s+exploit|duplication\s+exploit|"
    r"rp\s+glitch\s+exploit|mod\s+menu\s+cheat|aimbot|wallhack|exploit\s+de\s+dinheiro|"
    r"online\s+exploit)\b",
    re.IGNORECASE,
)

_NUDITY_GORE_RE = re.compile(
    r"\b(?:nudity|nude\s+mod|nsfw\s+mod|explicit\s+nudity|explicit\s+sex|"
    r"extreme\s+gore|graphic\s+gore|dismemberment\s+gore|nudez\s+expl[ií]cita)\b",
    re.IGNORECASE,
)

_WATERMARK_RE = re.compile(
    r"\b(?:tiktok\s+watermark|capcut\s+watermark|kwai\s+watermark|reels\s+watermark|"
    r"third[- ]party\s+watermark|made\s+with\s+capcut|"
    r"marca\s+d['’]?[aá]gua\s+(?:do\s+|de\s+)?(?:tiktok|capcut|kwai|terceiros))\b",
    re.IGNORECASE,
)

_ROCKSTAR_IMPERSONATION_RE = re.compile(
    r"\b(?:we\s+at\s+rockstar|here\s+at\s+rockstar|our\s+team\s+at\s+rockstar|"
    r"i\s+work\s+at\s+rockstar|we\s+are\s+rockstar\s+games|"
    r"official\s+rockstar\s+(?:games\s+)?(?:account|page|channel)\s+here|"
    r"n[oó]s\s+da\s+rockstar|aqui\s+na\s+rockstar|somos\s+a\s+rockstar|"
    r"nosso\s+est[uú]dio\s+rockstar|somos\s+o\s+youtube\s+oficial|"
    r"canal\s+oficial\s+d[ao]\s+rockstar)\b",
    re.IGNORECASE,
)

_SPECULATIVE_CLICKBAIT_RE = re.compile(
    r"\b(?:this\s+confirms|100%\s+confirmed(?:\s+leak)?|secret\s+insider|"
    r"insider\s+confirmed|guaranteed\s+confirmed|isso\s+confirma|"
    r"100%\s+confirmado|fonte\s+secreta|confirmado\s+por\s+insider|"
    r"rumor\s+100%\s+real)\b",
    re.IGNORECASE,
)

_OUTSIDE_PILLARS_DRAMA_RE = re.compile(
    r"\b(?:creator\s+drama|youtuber\s+beef|streamer\s+drama|off[- ]topic\s+rant)\b",
    re.IGNORECASE,
)

# Real Oficial KY4DWumZffg 13-Rule Campaign Patterns
_FORBIDDEN_CAMPAIGN_PEOPLE_RE = re.compile(
    r"\b(?:samira\s+close|the\s+burnt\s+peanut|burnt\s*peanut)\b",
    re.IGNORECASE,
)

_OLD_GTA_COMPARISON_RE = re.compile(
    r"\b(?:gta\s*(?:v|5|iv|4|iii|3)|san\s+andreas|los\s+santos|"
    r"antes\s+d[eo]\s+gta\s*(?:6|vi)|before\s+gta\s*(?:6|vi)|"
    r"tivemos\s+isso\s+antes|comparando\s+com\s+(?:o\s+)?gta|"
    r"compara[cç][aã]o\s+com\s+(?:o\s+)?gta)\b",
    re.IGNORECASE,
)

_NEGATIVE_MOCKERY_RE = re.compile(
    r"\b(?:jogo\s+lixo|que\s+lixo|flopou|vai\s+flopar|horr[ií]vel|"
    r"decep[cç][aã]o|decepcionante|downgrade|todo\s+bugado|"
    r"cheio\s+de\s+bug|muito\s+bugado)\b",
    re.IGNORECASE,
)

_MANDATORY_LIVE_PHRASE_RE = re.compile(
    r"Assista ao vivo em (?i:youtube\.com/youtube)(?=$|\s|[.,!?;:])"
)

_CREDIT_TAG_RE = re.compile(r"🎥\s*@[A-Za-z0-9_.-]+")
_HASHTAG_RE = re.compile(r"(?:^|\s)#[A-Za-z0-9_À-ÿ]+")
_INLINE_HASHTAG_RE = re.compile(r"(?:^|(?<=\s))#[A-Za-z0-9_À-ÿ]+")


def sanitize_untrusted_video_text(text: str, max_length: int = 1500) -> str:
    """Strip prompt-injection attempts, role tags, and control chars from untrusted video text."""
    if not text:
        return ""

    cleaned = _CONTROL_CHARS_RE.sub("", str(text))

    for tag_re in _TAG_BLOCK_PATTERNS:
        cleaned = tag_re.sub(" ", cleaned)

    for inj_re in _INJECTION_PATTERNS:
        cleaned = inj_re.sub(" ", cleaned)

    # Remove markdown code fences that try to smuggle fake JSON/system payloads
    cleaned = re.sub(r"```+.*?```+", " ", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if max_length > 0 and len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rsplit(" ", 1)[0].strip()

    return cleaned


def normalize_handle(handle: str) -> str:
    return handle.strip().lstrip("@").lower()


def _normalize_text(text: str) -> str:
    lowered = text.lower()
    lowered = re.sub(r"[^\w\s]", " ", lowered, flags=re.UNICODE)
    return re.sub(r"\s+", " ", lowered).strip()


def _strip_credits_and_hashtags(text: str) -> str:
    without_credit = _CREDIT_TAG_RE.sub(" ", text)
    without_tags = _HASHTAG_RE.sub(" ", without_credit)
    # Strip standard boilerplate fan disclaimer & mandatory campaign CTA so distinct hooks aren't false-matched
    without_boilerplate = re.sub(
        r"(?:fan\s+page,?\s+not\s+affiliated\s+with\s+rockstar\s+games\.?|"
        r"p[aá]gina\s+de\s+f[aã]s,?\s+sem\s+afilia[cç][aã]o(?:\s+oficial)?\s+com\s+a\s+rockstar\s+games\.?|"
        r"assista\s+ao\s+vivo\s+em\s+youtube\.com/youtube)",
        " ",
        without_tags,
        flags=re.IGNORECASE,
    )
    return _normalize_text(without_boilerplate)


class ComplianceFactAgent:
    """Evaluates ClipPlan and CaptionBundle against editorial, copyright, and Real Oficial rules."""

    def __init__(self, config: AppConfig, store: StateStore) -> None:
        self.config = config
        self.store = store

    @staticmethod
    def sanitize_text(text: str, max_length: int = 1500) -> str:
        return sanitize_untrusted_video_text(text, max_length=max_length)

    def _load_blacklist_and_keywords(self) -> tuple[set[str], list[str]]:
        blacklist = set(DEFAULT_BLACKLIST_HANDLES)
        blocked_keywords: list[str] = []

        lists_path = self.config.memory_dir / "creator_whitelist_blacklist.json"
        if lists_path.exists():
            try:
                data = json.loads(lists_path.read_text(encoding="utf-8"))
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

        return blacklist, blocked_keywords

    def _campaign_rules(self) -> dict:
        raw = self.config.campaign or {}
        campaign_block = raw.get("campaign", raw) if isinstance(raw, dict) else {}
        rules = campaign_block.get("rules", {}) if isinstance(campaign_block, dict) else {}
        return rules if isinstance(rules, dict) else {}

    def _approval_rules(self) -> dict:
        raw = self.config.settings or {}
        rules = raw.get("approval_rules", {}) if isinstance(raw, dict) else {}
        return rules if isinstance(rules, dict) else {}

    def _collect_corpus(self, plan: ClipPlan, bundle: CaptionBundle) -> str:
        chunks: list[str] = [
            plan.source.title,
            plan.source.summary,
            " ".join(plan.source.verified_facts),
            plan.hook_on_screen,
            plan.voiceover_script,
            plan.commentary_overlay,
            " ".join(c.text for c in plan.subtitles),
        ]
        for copy in bundle.copies.values():
            chunks.extend([copy.title, copy.caption, copy.credit_line, " ".join(copy.hashtags)])
        return "\n".join(c for c in chunks if c)

    def _check_deduplication(
        self, bundle: CaptionBundle, recent_captions: Iterable[str]
    ) -> str | None:
        recent_list = [r for r in recent_captions if r and r.strip()]
        if not recent_list:
            return None

        prepared_recent = [
            (_normalize_text(r), _strip_credits_and_hashtags(r)) for r in recent_list
        ]

        for platform, copy in bundle.copies.items():
            full_candidate = copy.formatted_text()
            norm_full = _normalize_text(full_candidate)
            norm_core = _strip_credits_and_hashtags(full_candidate)

            for prev_full, prev_core in prepared_recent:
                if norm_full and norm_full == prev_full:
                    return (
                        f"Duplicate caption detected on {platform.value} matching a post from the last 7 days."
                    )
                if len(norm_core) >= 8 and len(prev_core) >= 8:
                    if norm_core == prev_core:
                        return (
                            f"Duplicate core caption detected on {platform.value} within the 7-day window."
                        )
                    similarity = SequenceMatcher(None, norm_core, prev_core).ratio()
                    if similarity >= 0.88:
                        return (
                            f"Near-duplicate caption on {platform.value} "
                            f"(similarity {similarity:.2f} >= 0.88 within 7 days)."
                        )
        return None

    def evaluate_plan(self, plan: ClipPlan, bundle: CaptionBundle) -> ComplianceVerdict:
        reasons: list[str] = []
        warnings: list[str] = []
        campaign_compliant = True

        blacklist, blocked_keywords = self._load_blacklist_and_keywords()
        corpus = self._collect_corpus(plan, bundle)
        corpus_lower = corpus.lower()

        # 1. Blacklisted creator or explicit no-repost bio flag
        creator_norm = normalize_handle(plan.source.creator_handle)
        if not creator_norm and not plan.for_real_oficial_campaign:
            reasons.append("Missing creator_handle on source candidate.")
        elif creator_norm and (
            creator_norm in blacklist or plan.source.metadata.get("blacklisted") is True
        ):
            reasons.append(f"Creator @{creator_norm} is on the blacklist.")

        if plan.source.metadata.get("no_reposts") is True or re.search(
            r"\b(?:no\s+reposts?|do\s+not\s+repost|proibido\s+repostar)\b",
            plan.source.summary,
            re.IGNORECASE,
        ):
            reasons.append(f"Creator @{creator_norm} disallows reposts.")

        # 2. Nudity / gore flags & text patterns
        if plan.source.contains_nudity_or_gore or _NUDITY_GORE_RE.search(corpus):
            reasons.append("Rejected: contains nudity, NSFW content, or extreme gore.")

        # 3. Leaks, datamines, leaked maps/tracklists, pirated copies, code exploits
        if _HARD_LEAK_RE.search(corpus):
            reasons.append("Rejected: references leaks, datamines, dev builds, or leaked map/tracklist.")

        if _PIRACY_RE.search(corpus):
            reasons.append("Rejected: references pirated copies, cracks, or torrents.")

        if _EXPLOIT_RE.search(corpus):
            reasons.append("Rejected: references code exploits, money glitches, or online cheats.")

        for kw in blocked_keywords:
            if kw in corpus_lower:
                reasons.append(f"Rejected: contains blocked policy keyword '{kw}'.")
                break

        # 4. Third-party watermark mentions or flags
        if plan.source.metadata.get("has_watermark_third_party") is True or _WATERMARK_RE.search(corpus):
            reasons.append("Rejected: third-party watermark detected or referenced.")
            if plan.for_real_oficial_campaign:
                campaign_compliant = False

        # 5. Fake official Rockstar / YouTube impersonation
        if _ROCKSTAR_IMPERSONATION_RE.search(corpus):
            reasons.append("Rejected: impersonates official Rockstar Games or YouTube account.")
            if plan.for_real_oficial_campaign:
                campaign_compliant = False

        # 6. Unverified speculative clickbait confirmations
        if _SPECULATIVE_CLICKBAIT_RE.search(corpus):
            reasons.append(
                "Rejected: uses unverified speculative clickbait phrasing ('this confirms', '100% confirmed', 'secret insider')."
            )

        # 7. Transformation checks (duration 7s-58s + non-empty hook_on_screen)
        duration = plan.end_sec - plan.start_sec
        if plan.start_sec < 0 or duration < 7.0 or duration > 58.0:
            reasons.append(
                f"Rejected: clip duration {duration:.1f}s must be between 7.0s and 58.0s."
            )

        if not plan.hook_on_screen or not plan.hook_on_screen.strip():
            reasons.append("Rejected: hook_on_screen must be non-empty for transformative editing.")

        if not plan.voiceover_script.strip() and not plan.commentary_overlay.strip():
            warnings.append("Neither voiceover_script nor commentary_overlay is populated; relying on hook banner only.")

        # 8. Creator credit check (post-live streamer mode ONLY; Rule #10 forbids watermarks/extra tags on campaign!)
        if not bundle.copies:
            reasons.append("Rejected: CaptionBundle contains no platform copies.")
            if plan.for_real_oficial_campaign:
                campaign_compliant = False
        elif not plan.for_real_oficial_campaign:
            for platform, copy in bundle.copies.items():
                formatted = copy.formatted_text()
                if not _CREDIT_TAG_RE.search(formatted):
                    reasons.append(
                        f"Rejected: missing mandatory creator credit ('🎥 @creator') in {platform.value} copy."
                    )
                if platform == Platform.X and (copy.hashtags or _HASHTAG_RE.search(copy.caption)):
                    warnings.append("X copy contains hashtags; brand voice requires zero hashtags on X.")

        # 9. 7-day caption deduplication against StateStore
        dedup_error = self._check_deduplication(bundle, self.store.recent_captions(days=7))
        if dedup_error:
            reasons.append(f"Rejected: {dedup_error}")

        # 10. Official Real Oficial 'YouTube x GTA VI' (`KY4DWumZffg`) 13-Rule Campaign Validation
        if plan.for_real_oficial_campaign:
            rules = self._campaign_rules()
            meta = plan.source.metadata or {}
            src_url = (plan.source.url or "").strip()

            # Rule: Authorized Live Source ONLY (`KY4DWumZffg`) — reject trailers & outside videos
            if AUTHORIZED_LIVE_VIDEO_ID not in src_url or any(
                tid in src_url for tid in FORBIDDEN_TRAILER_VIDEO_IDS
            ):
                campaign_compliant = False
                reasons.append(
                    f"Rejected: Real Oficial campaign allows ONLY authorized live source {AUTHORIZED_LIVE_VIDEO_ID} "
                    "(trailers QdBZY2fkU-0/VQRLujxTm3c and outside videos are prohibited)."
                )

            # Rule: Valid Platforms ONLY (Instagram Reels and TikTok)
            invalid_platforms = [
                p.value for p in bundle.copies if p not in VALID_CAMPAIGN_PLATFORMS
            ]
            if invalid_platforms:
                campaign_compliant = False
                reasons.append(
                    f"Rejected: Real Oficial campaign pays ONLY on instagram_reels and tiktok "
                    f"(unsupported copy platforms: {', '.join(invalid_platforms)})."
                )
            if plan.target_platforms and not any(
                p in VALID_CAMPAIGN_PLATFORMS for p in plan.target_platforms
            ):
                campaign_compliant = False
                reasons.append(
                    "Rejected: plan.target_platforms must target instagram_reels and/or tiktok for Real Oficial campaign."
                )

            # Rule: Clip duration window (10s - 58s)
            min_camp_dur = float(rules.get("min_duration_sec", 10.0))
            max_camp_dur = float(rules.get("max_duration_sec", 58.0))
            if duration < min_camp_dur or duration > max_camp_dur:
                campaign_compliant = False
                reasons.append(
                    f"Real Oficial campaign requires duration between {min_camp_dur:.0f}s and {max_camp_dur:.0f}s (got {duration:.1f}s)."
                )

            # Rule: Forbidden People (Samira Close & The Burnt Peanut)
            meta_str_values = " ".join(str(v) for v in meta.values() if isinstance(v, str))
            people_text = f"{corpus}\n{plan.source.creator_handle}\n{src_url}\n{meta_str_values}"
            if (
                bool(meta.get("samira_on_screen"))
                or bool(meta.get("samira_close_on_screen"))
                or bool(meta.get("burnt_peanut_on_screen"))
                or _FORBIDDEN_CAMPAIGN_PEOPLE_RE.search(people_text)
            ):
                campaign_compliant = False
                reasons.append(
                    "Rejected: forbidden appearance/mention detected (Samira Close or The Burnt Peanut cannot appear in campaign clips)."
                )

            # Rule: No Old GTA / No 'Before GTA 6' Comparisons
            if _OLD_GTA_COMPARISON_RE.search(corpus):
                campaign_compliant = False
                reasons.append(
                    "Rejected: references previous GTA titles or 'antes do GTA 6' / 'before GTA 6' comparisons."
                )

            # Rule: Positive Hype Only (zero negative memes/mockery)
            if _NEGATIVE_MOCKERY_RE.search(corpus):
                campaign_compliant = False
                reasons.append(
                    "Rejected: negative mockery/criticism detected; Real Oficial campaign requires positive hype only."
                )

            # Rule: No AI Video & No Music Lyrics on Screen & No Logos/Watermarks
            if bool(meta.get("is_ai_video")) or bool(meta.get("ai_generated")):
                campaign_compliant = False
                reasons.append(
                    "Rejected: AI-generated video clips are strictly prohibited in Real Oficial campaign."
                )

            if bool(meta.get("has_music_lyrics_on_screen")):
                campaign_compliant = False
                reasons.append(
                    "Rejected: song lyrics on screen are prohibited in Real Oficial campaign clips."
                )

            if (
                bool(meta.get("has_logo_or_watermark"))
                or bool(meta.get("has_channel_logo"))
                or bool(meta.get("burn_credit_watermark"))
            ):
                campaign_compliant = False
                reasons.append(
                    "Rejected: channel logos and burned-in watermarks violate Rule #10 of the Real Oficial campaign."
                )

            # Rule #10: Exact phrase "Assista ao vivo em youtube.com/youtube" + ONLY hashtag #launchdayonYT
            overlay_hashtags = _INLINE_HASHTAG_RE.findall(
                f"{plan.hook_on_screen} {plan.commentary_overlay}"
            )
            bad_overlay_tags = [
                t for t in overlay_hashtags if t.lower() != MANDATORY_ONLY_HASHTAG.lower()
            ]
            if bad_overlay_tags:
                campaign_compliant = False
                reasons.append(
                    f"Rejected: unauthorized hashtag(s) {', '.join(bad_overlay_tags)} in overlay "
                    f"(Rule #10 allows ONLY {MANDATORY_ONLY_HASHTAG})."
                )

            for platform, copy in bundle.copies.items():
                if platform not in VALID_CAMPAIGN_PLATFORMS:
                    continue
                formatted = copy.formatted_text()

                if not _MANDATORY_LIVE_PHRASE_RE.search(formatted):
                    campaign_compliant = False
                    reasons.append(
                        f"Rejected: {platform.value} copy missing mandatory phrase '{MANDATORY_CAPTION_PHRASE}'."
                    )

                inline_tags = _INLINE_HASHTAG_RE.findall(formatted)
                explicit_tags = [
                    t.strip() if str(t).strip().startswith("#") else f"#{str(t).strip()}"
                    for t in copy.hashtags
                    if str(t).strip()
                ]
                all_tags = inline_tags + explicit_tags

                if not any(t.lower() == MANDATORY_ONLY_HASHTAG.lower() for t in all_tags):
                    campaign_compliant = False
                    reasons.append(
                        f"Rejected: {platform.value} copy missing mandatory hashtag '{MANDATORY_ONLY_HASHTAG}'."
                    )

                extra_tags = list(
                    dict.fromkeys(
                        t for t in all_tags if t.lower() != MANDATORY_ONLY_HASHTAG.lower()
                    )
                )
                if extra_tags:
                    campaign_compliant = False
                    reasons.append(
                        f"Rejected: {platform.value} copy contains unauthorized hashtag(s) "
                        f"{', '.join(extra_tags)} — Rule #10 allows ONLY '{MANDATORY_ONLY_HASHTAG}'."
                    )

        if reasons:
            return ComplianceVerdict(
                passed=False,
                approval_status=ApprovalStatus.REJECTED,
                reasons=reasons,
                warnings=warnings,
                campaign_compliant=campaign_compliant,
            )

        # 11. Human approval routing for rumors or outside-pillar content
        approval_rules = self._approval_rules()
        require_rumor_approval = bool(approval_rules.get("require_approval_for_rumors", True))
        require_outside_approval = bool(approval_rules.get("require_approval_outside_pillars", True))

        configured_pillars_raw = approval_rules.get("standard_pillars")
        if isinstance(configured_pillars_raw, list) and configured_pillars_raw:
            allowed_pillar_values = {str(p) for p in configured_pillars_raw}
            is_outside_pillars = plan.pillar.value not in allowed_pillar_values
        else:
            is_outside_pillars = plan.pillar not in STANDARD_PILLARS

        if (
            plan.source.metadata.get("outside_pillars") is True
            or plan.source.metadata.get("outside_standard_pillars") is True
            or _OUTSIDE_PILLARS_DRAMA_RE.search(corpus)
        ):
            is_outside_pillars = True

        approval_status = ApprovalStatus.AUTO_APPROVED

        if plan.source.is_leak_or_rumor and require_rumor_approval:
            approval_status = ApprovalStatus.PENDING_HUMAN
            warnings.append("Source is flagged as unverified rumor; queued for human approval.")

        if is_outside_pillars and require_outside_approval:
            approval_status = ApprovalStatus.PENDING_HUMAN
            warnings.append("Content falls outside standard autonomous pillars; queued for human approval.")

        if plan.source.contains_spoiler and not plan.spoiler_tag_required:
            approval_status = ApprovalStatus.PENDING_HUMAN
            warnings.append("Source contains spoilers without spoiler_tag_required enabled; queued for human approval.")

        if self.config.autopilot_mode == "manual" or approval_rules.get("auto_publish_verified_clips") is False:
            if approval_status == ApprovalStatus.AUTO_APPROVED:
                approval_status = ApprovalStatus.PENDING_HUMAN
                warnings.append("Manual approval mode active in configuration.")

        return ComplianceVerdict(
            passed=True,
            approval_status=approval_status,
            reasons=[],
            warnings=warnings,
            campaign_compliant=campaign_compliant,
        )
