"""Scriptwriting, Hook Design, Subtitle Timing & Section 7 Multi-Platform Copywriting Agent."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from gta6_agents.agents.compliance import sanitize_untrusted_video_text
from gta6_agents.agents.live_campaign_ky4 import (
    MANDATORY_CAPTION_PHRASE,
    MANDATORY_ONLY_HASHTAG,
    MANDATORY_PINNED_COMMENT,
)
from gta6_agents.config import AppConfig
from gta6_agents.models import (
    CaptionBundle,
    ClipPlan,
    ContentPillar,
    Platform,
    PlatformCopy,
    SourceCandidate,
    SubtitleCue,
)

_HASHTAG_STRIP_RE = re.compile(r"#[\wÀ-ÿ]+")
_HANDLE_OR_CREDIT_STRIP_RE = re.compile(r"(?:🎥\s*)?@[A-Za-z0-9_.-]+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")
_CAMPAIGN_FORBIDDEN_TERMS_RE = re.compile(
    r"\b(?:gta\s*(?:v|5|iv|4|iii|3)|san\s+andreas|los\s+santos|"
    r"antes\s+d[eo]\s+gta\s*(?:6|vi)|before\s+gta\s*(?:6|vi)|"
    r"tivemos\s+isso\s+antes|comparando\s+com\s+(?:o\s+)?gta|"
    r"compara[cç][aã]o\s+com\s+(?:o\s+)?gta|"
    r"samira\s+close|the\s+burnt\s+peanut|burnt\s*peanut|"
    r"jogo\s+lixo|que\s+lixo|flopou|vai\s+flopar|horr[ií]vel|"
    r"decep[cç][aã]o|decepcionante|downgrade|todo\s+bugado|cheio\s+de\s+bug)\b",
    re.IGNORECASE,
)


def _ensure_at_handle(handle: str) -> str:
    cleaned = handle.strip()
    if not cleaned:
        return "@RockstarGames"
    return cleaned if cleaned.startswith("@") else f"@{cleaned}"


def _truncate_clean(text: str, max_len: int) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= max_len:
        return cleaned
    cut = cleaned[: max_len - 1].rsplit(" ", 1)[0].strip(" -—:,;.")
    if not cut:
        cut = cleaned[: max_len - 1].strip()
    return f"{cut}…"


def _strip_hashtags(text: str) -> str:
    no_tags = _HASHTAG_STRIP_RE.sub("", text)
    return re.sub(r"[ \t]+", " ", no_tags).strip()


def _clean_campaign_text(text: str, max_len: int = 120) -> str:
    """Remove hashtags, @handles/logos, old-GTA comparisons, and negative slang from campaign text."""
    cleaned = sanitize_untrusted_video_text(text, max_length=max_len * 2)
    cleaned = _HASHTAG_STRIP_RE.sub(" ", cleaned)
    cleaned = _HANDLE_OR_CREDIT_STRIP_RE.sub(" ", cleaned)
    cleaned = _CAMPAIGN_FORBIDDEN_TERMS_RE.sub(" ", cleaned)
    cleaned = re.sub(
        r"assista\s+ao\s+vivo\s+em\s+youtube\.com/youtube",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -—:,;|")
    return _truncate_clean(cleaned, max_len) if cleaned else ""


def build_subtitle_cues(voiceover_script: str, duration_sec: float) -> list[SubtitleCue]:
    """Generate realistic timed SubtitleCue items from a voiceover script across clip duration."""
    clean_script = re.sub(r"\s+", " ", voiceover_script).strip()
    if not clean_script or duration_sec <= 0:
        return []

    # Break into sentences first, then 4-7 word vertical-video subtitle chunks
    raw_clauses = [s.strip() for s in _SENTENCE_SPLIT_RE.split(clean_script) if s.strip()]
    phrases: list[str] = []
    max_words_per_cue = 6

    for clause in raw_clauses:
        words = clause.split()
        for idx in range(0, len(words), max_words_per_cue):
            chunk = " ".join(words[idx : idx + max_words_per_cue]).strip()
            if chunk:
                phrases.append(chunk)

    if not phrases:
        return []

    usable_duration = max(1.0, round(duration_sec - 0.15, 2))
    weights = [max(1, len(p)) for p in phrases]
    total_weight = sum(weights)

    cues: list[SubtitleCue] = []
    cursor = 0.0
    for idx, (phrase, weight) in enumerate(zip(phrases, weights)):
        if idx == len(phrases) - 1:
            end_t = usable_duration
        else:
            share = usable_duration * (weight / total_weight)
            end_t = min(usable_duration, cursor + max(0.6, share))

        start_rounded = round(cursor, 2)
        end_rounded = round(max(start_rounded + 0.35, min(duration_sec, end_t)), 2)
        if start_rounded >= duration_sec:
            break

        cues.append(
            SubtitleCue(
                start_sec=start_rounded,
                end_sec=end_rounded,
                text=phrase,
            )
        )
        cursor = end_rounded

    return cues


def _extract_json_object(raw_text: str) -> dict[str, Any] | None:
    if not raw_text:
        return None
    stripped = raw_text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", stripped, flags=re.DOTALL)
    if fence_match:
        stripped = fence_match.group(1).strip()
    else:
        brace_start = stripped.find("{")
        brace_end = stripped.rfind("}")
        if brace_start != -1 and brace_end > brace_start:
            stripped = stripped[brace_start : brace_end + 1]
    try:
        parsed = json.loads(stripped)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


class ScriptCopyAgent:
    """Builds transformative GTA 6 ClipPlans and Section 7 platform-native CaptionBundles."""

    def __init__(
        self,
        config: AppConfig,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self._external_client = http_client

    def _resolve_language(self, explicit_lang: str | None, for_campaign: bool) -> str:
        if for_campaign:
            return "pt-BR"

        if explicit_lang and explicit_lang.strip():
            norm = explicit_lang.strip()
            if norm.lower().startswith("pt"):
                return "pt-BR"
            if norm.lower().startswith("en"):
                return "en-US"
            return norm

        page_cfg = (self.config.settings or {}).get("page", {})
        if isinstance(page_cfg, dict) and page_cfg.get("primary_language"):
            return str(page_cfg["primary_language"]).strip()
        return "pt-BR"

    def _resolve_target_platforms(self, is_campaign: bool) -> list[Platform]:
        if is_campaign:
            return [Platform.INSTAGRAM_REELS, Platform.TIKTOK]

        platforms_cfg = (self.config.settings or {}).get("platforms", {})
        selected: list[Platform] = []

        mapping = [
            (Platform.YOUTUBE_SHORTS, "youtube_shorts", True),
            (Platform.INSTAGRAM_REELS, "instagram_reels", True),
            (Platform.TIKTOK, "tiktok", True),
            (Platform.X, "x", True),
            (Platform.THREADS, "threads", False),
        ]
        for enum_val, key, default_enabled in mapping:
            cfg_entry = platforms_cfg.get(key) if isinstance(platforms_cfg, dict) else None
            enabled = (
                bool(cfg_entry.get("enabled", default_enabled))
                if isinstance(cfg_entry, dict)
                else default_enabled
            )
            if enabled:
                selected.append(enum_val)

        if not selected:
            selected = [Platform.INSTAGRAM_REELS, Platform.TIKTOK, Platform.YOUTUBE_SHORTS]
        return selected

    def _campaign_rules(self) -> dict[str, Any]:
        raw = self.config.campaign or {}
        camp = raw.get("campaign", raw) if isinstance(raw, dict) else {}
        rules = camp.get("rules", {}) if isinstance(camp, dict) else {}
        return rules if isinstance(rules, dict) else {}

    def _select_llm_backend(self, tier: str = "carbon") -> tuple[str, str, str] | None:
        import os

        tier_prefix = "HYDROGEN" if tier == "hydrogen" else "CARBON"
        preferred = (
            os.getenv(f"{tier_prefix}_PROVIDER")
            or self.config.llm_provider
            or "gemini"
        ).strip().lower()
        tier_model = os.getenv(f"{tier_prefix}_MODEL", "").strip()
        default_gemini = "gemini-2.5-flash-lite" if tier == "hydrogen" else (self.config.llm_model or "gemini-2.5-pro")
        default_openai = "gpt-4o-mini" if tier == "hydrogen" else "gpt-4.1"
        default_groq = "llama-3.1-8b-instant" if tier == "hydrogen" else "llama-3.3-70b-versatile"

        candidates = [
            ("gemini", self.config.gemini_api_key, tier_model or default_gemini),
            ("openai", self.config.openai_api_key, tier_model or default_openai),
            ("groq", self.config.groq_api_key, tier_model or default_groq),
        ]
        # Re-order so preferred provider comes first
        candidates.sort(key=lambda item: 0 if item[0] == preferred else 1)

        for provider, key, chosen_model in candidates:
            if not key or not key.strip():
                continue
            model = chosen_model
            if provider == "openai" and not (model.startswith("gpt-") or model.startswith("o")):
                model = default_openai
            elif provider == "groq" and (model.startswith("gemini") or model.startswith("gpt-")):
                model = default_groq
            elif provider == "gemini" and not model.startswith("gemini"):
                model = default_gemini
            return provider, key.strip(), model
        return None

    async def _call_llm_json(self, system_prompt: str, user_prompt: str, tier: str = "carbon") -> dict[str, Any] | None:
        backend = self._select_llm_backend(tier=tier)
        if backend is None:
            return None

        provider, api_key, model = backend
        timeout = httpx.Timeout(8.0, connect=3.0)

        async def _dispatch(client: httpx.AsyncClient) -> dict[str, Any] | None:
            if provider == "gemini":
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                payload = {
                    "system_instruction": {"parts": [{"text": system_prompt}]},
                    "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                    "generationConfig": {
                        "temperature": 0.45,
                        "responseMimeType": "application/json",
                    },
                }
                resp = await client.post(
                    url,
                    headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                    json=payload,
                )
                if resp.status_code != 200:
                    return None
                data = resp.json()
                parts = (
                    data.get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [])
                )
                text = "".join(str(p.get("text", "")) for p in parts if isinstance(p, dict))
                return _extract_json_object(text)

            endpoint = (
                "https://api.openai.com/v1/chat/completions"
                if provider == "openai"
                else "https://api.groq.com/openai/v1/chat/completions"
            )
            payload = {
                "model": model,
                "temperature": 0.45,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
            resp = await client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            text = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )
            return _extract_json_object(str(text))

        try:
            if self._external_client is not None:
                return await _dispatch(self._external_client)
            async with httpx.AsyncClient(timeout=timeout) as client:
                return await _dispatch(client)
        except Exception:
            return None

    def _deterministic_script(
        self,
        *,
        clean_title: str,
        clean_summary: str,
        clean_facts: list[str],
        pillar: ContentPillar,
        language: str,
        spoiler: bool,
        is_campaign: bool = False,
    ) -> tuple[str, str, str]:
        if is_campaign:
            camp_topic = _clean_campaign_text(clean_title, 42) or "Momento épico em Leonida"
            raw_fact = clean_facts[0] if clean_facts else clean_summary
            camp_fact = (
                _clean_campaign_text(raw_fact, 130)
                or "Reação incrível ao vivo explorando Vice City e Leonida no GTA VI"
            )
            camp_secondary = (
                _clean_campaign_text(clean_facts[1], 110) if len(clean_facts) > 1 else ""
            )
            hook = _truncate_clean(f"AO VIVO NO GTA VI: {camp_topic}", 60)
            vo_parts_camp = [
                f"Olha essa reação insana ao vivo em {camp_topic}!",
                camp_fact.rstrip(".") + ".",
            ]
            if camp_secondary:
                vo_parts_camp.append(camp_secondary.rstrip(".") + ".")
            vo_parts_camp.append(
                "O nível de imersão em Vice City está simplesmente surreal!"
            )
            voiceover = " ".join(vo_parts_camp)
            overlay = _truncate_clean(f"Destaque ao vivo • {camp_fact}", 100)
            return hook, voiceover, overlay

        is_pt = language.lower().startswith("pt")
        topic_short = _truncate_clean(_strip_hashtags(clean_title), 54)
        primary_fact = (
            clean_facts[0]
            if clean_facts
            else (
                clean_summary
                if clean_summary
                else (
                    "Detalhes visuais capturados em material verificado de Grand Theft Auto VI."
                    if is_pt
                    else "Visual details captured from verified Grand Theft Auto VI footage."
                )
            )
        )
        secondary_fact = clean_facts[1] if len(clean_facts) > 1 else ""

        if is_pt:
            pillar_hooks: dict[ContentPillar, str] = {
                ContentPillar.OFFICIAL_NEWS: f"OFICIAL NO GTA 6: {topic_short}",
                ContentPillar.TIPS_TUTORIALS: f"DICA DE GTA 6: {topic_short}",
                ContentPillar.GAMEPLAY_CLIPS: f"CORTE DA LIVE DE GTA 6: {topic_short}",
                ContentPillar.GTA5_NOSTALGIA: f"13 ANOS DE EVOLUÇÃO: {topic_short}",
                ContentPillar.SPOTLIGHTS: f"RAIO-X DE LEONIDA: {topic_short}",
                ContentPillar.COUNTDOWN_RECAP: f"RESUMO GTA 6: {topic_short}",
                ContentPillar.QUESTIONS: f"QUAL SUA ESCOLHA EM LEONIDA? {topic_short}",
                ContentPillar.REAL_OFICIAL_CAMPAIGN: f"AO VIVO NO GTA VI: {topic_short}",
            }
            hook = _truncate_clean(pillar_hooks.get(pillar, f"GTA 6: {topic_short}"), 64)
            if spoiler and not hook.upper().startswith("⚠️ SPOILER"):
                hook = _truncate_clean(f"⚠️ SPOILER — {hook}", 64)

            vo_parts = [
                f"Olha esse momento em {topic_short}.",
                primary_fact.rstrip(".") + ".",
            ]
            if secondary_fact:
                vo_parts.append(secondary_fact.rstrip(".") + ".")
            vo_parts.append(
                "A atenção aos detalhes em Leonida muda completamente a imersão. Você tinha reparado nisso?"
            )
            voiceover = " ".join(vo_parts)
            overlay = _truncate_clean(f"Análise verificada • {primary_fact}", 110)
        else:
            pillar_hooks_en: dict[ContentPillar, str] = {
                ContentPillar.OFFICIAL_NEWS: f"OFFICIAL GTA 6 DETAIL: {topic_short}",
                ContentPillar.TIPS_TUTORIALS: f"GTA 6 PRO TIP: {topic_short}",
                ContentPillar.GAMEPLAY_CLIPS: f"DID YOU CATCH THIS PHYSICS DETAIL? {topic_short}",
                ContentPillar.GTA5_NOSTALGIA: f"13 YEARS OF ROCKSTAR EVOLUTION: {topic_short}",
                ContentPillar.SPOTLIGHTS: f"INSIDE LEONIDA: {topic_short}",
                ContentPillar.COUNTDOWN_RECAP: f"GTA 6 VERIFIED RECAP: {topic_short}",
                ContentPillar.QUESTIONS: f"WHAT IS YOUR DAY-1 MOVE? {topic_short}",
                ContentPillar.REAL_OFICIAL_CAMPAIGN: f"LIVE GTA VI HIGHLIGHT: {topic_short}",
            }
            hook = _truncate_clean(pillar_hooks_en.get(pillar, f"GTA 6: {topic_short}"), 64)
            if spoiler and not hook.upper().startswith("⚠️ SPOILER"):
                hook = _truncate_clean(f"⚠️ SPOILER — {hook}", 64)

            vo_parts_en = [
                f"Watch closely in {topic_short}.",
                primary_fact.rstrip(".") + ".",
            ]
            if secondary_fact:
                vo_parts_en.append(secondary_fact.rstrip(".") + ".")
            vo_parts_en.append(
                "Rockstar's world simulation in Leonida keeps raising the bar. Did you spot this on first watch?"
            )
            voiceover = " ".join(vo_parts_en)
            overlay = _truncate_clean(f"Verified breakdown • {primary_fact}", 110)

        return hook, voiceover, overlay

    async def build_clip_plan(
        self,
        source: SourceCandidate,
        language: str | None = None,
        for_campaign: bool = False,
    ) -> ClipPlan:
        """Create a transformative ClipPlan with hook, voiceover script, and timed SubtitleCues."""
        is_campaign = bool(
            for_campaign
            or source.is_campaign_acervo
            or source.pillar == ContentPillar.REAL_OFICIAL_CAMPAIGN
        )
        lang = self._resolve_language(language, is_campaign)
        pillar = ContentPillar.REAL_OFICIAL_CAMPAIGN if is_campaign else source.pillar
        campaign_slug = (
            source.campaign_slug or self.config.real_oficial_campaign_slug
            if is_campaign
            else None
        )
        spoiler_required = bool(source.contains_spoiler) and not is_campaign

        if is_campaign:
            source.metadata["mandatory_pinned_comment"] = MANDATORY_PINNED_COMMENT

        clean_title = sanitize_untrusted_video_text(source.title, max_length=160)
        clean_summary = sanitize_untrusted_video_text(source.summary, max_length=400)
        clean_facts = [
            sanitize_untrusted_video_text(f, max_length=200)
            for f in source.verified_facts
            if f and sanitize_untrusted_video_text(f, max_length=200)
        ]

        start_sec = max(0.0, float(source.suggested_start_sec))
        raw_duration = float(source.suggested_end_sec) - start_sec

        if is_campaign:
            rules = self._campaign_rules()
            min_dur = max(10.0, float(rules.get("min_duration_sec", 10.0)))
            max_dur = min(58.0, float(rules.get("max_duration_sec", 58.0)))
            duration = raw_duration if min_dur <= raw_duration <= max_dur else min(26.0, max_dur)
            duration = max(min_dur, min(max_dur, duration))
        else:
            duration = raw_duration if 8.0 <= raw_duration <= 56.0 else 25.0

        end_sec = round(start_sec + duration, 2)

        hook, voiceover, overlay = self._deterministic_script(
            clean_title=clean_title,
            clean_summary=clean_summary,
            clean_facts=clean_facts,
            pillar=pillar,
            language=lang,
            spoiler=spoiler_required,
            is_campaign=is_campaign,
        )

        # Optional LLM enhancement with strict validation fallback
        if is_campaign:
            sys_prompt = (
                "Você escreve roteiros curtos em PT-BR para cortes oficiais da Live YouTube x GTA VI. "
                "Tom 100% positivo e empolgado. PROIBIDO mencionar GTA V/5, San Andreas, Los Santos, "
                "'antes do GTA 6', críticas/bugs, arrobas (@), logos ou hashtags. Retorne JSON com: "
                "hook_on_screen (<=56 chars, gancho de 2s), voiceover_script (30-55 palavras), commentary_overlay (<=95 chars)."
            )
        else:
            sys_prompt = (
                "You write transformative short-form GTA 6 video scripts for a fan commentary page "
                "(not affiliated with Rockstar Games). Never claim unverified leaks, never say "
                "'this confirms' or '100% confirmed' or 'we at Rockstar'. Return JSON with keys: "
                "hook_on_screen (<=60 chars), voiceover_script (35-65 words), commentary_overlay (<=100 chars)."
            )

        llm_data = await self._call_llm_json(
            system_prompt=sys_prompt,
            user_prompt=(
                f"Language: {lang}\nPillar: {pillar.value}\nTitle: {clean_title}\n"
                f"Summary: {clean_summary}\nVerified facts: {clean_facts}\n"
                f"Duration seconds: {duration:.1f}\nSpoiler warning needed: {spoiler_required}"
            ),
        )
        if llm_data:
            cand_hook = sanitize_untrusted_video_text(str(llm_data.get("hook_on_screen", "")), max_length=64)
            cand_vo = sanitize_untrusted_video_text(str(llm_data.get("voiceover_script", "")), max_length=600)
            cand_ov = sanitize_untrusted_video_text(str(llm_data.get("commentary_overlay", "")), max_length=110)
            if is_campaign:
                cand_hook = _clean_campaign_text(cand_hook, 60)
                cand_vo = _clean_campaign_text(cand_vo, 500)
                cand_ov = _clean_campaign_text(cand_ov, 100)
            if cand_hook and cand_vo and not self._contains_unsafe_phrasing(
                f"{cand_hook} {cand_vo} {cand_ov}", for_campaign=is_campaign
            ):
                if spoiler_required and "SPOILER" not in cand_hook.upper():
                    cand_hook = _truncate_clean(f"⚠️ SPOILER — {cand_hook}", 64)
                hook = cand_hook
                voiceover = cand_vo
                overlay = cand_ov or overlay

        subtitles = build_subtitle_cues(voiceover, duration)
        target_platforms = self._resolve_target_platforms(is_campaign)

        return ClipPlan(
            source=source,
            pillar=pillar,
            language=lang,
            hook_on_screen=hook,
            voiceover_script=voiceover,
            commentary_overlay=overlay,
            start_sec=round(start_sec, 2),
            end_sec=end_sec,
            target_platforms=target_platforms,
            for_real_oficial_campaign=is_campaign,
            campaign_slug=campaign_slug,
            spoiler_tag_required=spoiler_required,
            subtitles=subtitles,
        )

    @staticmethod
    def _contains_unsafe_phrasing(text: str, for_campaign: bool = False) -> bool:
        if re.search(
            r"\b(?:this\s+confirms|100%\s+confirmed|secret\s+insider|isso\s+confirma|"
            r"100%\s+confirmado|fonte\s+secreta|we\s+at\s+rockstar|n[oó]s\s+da\s+rockstar|"
            r"leak|leaked|datamine|datamined|vazamento)\b",
            text,
            re.IGNORECASE,
        ):
            return True
        if for_campaign and _CAMPAIGN_FORBIDDEN_TERMS_RE.search(text):
            return True
        return False

    def _build_campaign_copies(self, plan: ClipPlan, llm_hook: str = "") -> dict[Platform, PlatformCopy]:
        """Build strict Rule #10 Real Oficial KY4DWumZffg copies for Instagram Reels & TikTok ONLY."""
        base_hook = _clean_campaign_text(llm_hook or plan.hook_on_screen, 72)
        if not base_hook:
            base_hook = _clean_campaign_text(plan.source.title, 72) or "Momento épico ao vivo no GTA VI!"

        detail_source = (
            plan.source.verified_facts[0]
            if plan.source.verified_facts
            else (plan.source.summary or plan.source.title)
        )
        detail_clean = _clean_campaign_text(detail_source, 72)
        if detail_clean and detail_clean.lower() not in base_hook.lower():
            positive_live_hook = _truncate_clean(f"{base_hook} — {detail_clean}", 140)
        else:
            positive_live_hook = base_hook

        positive_live_hook = positive_live_hook.rstrip(" -—:,;")
        campaign_caption = (
            f"{positive_live_hook} {MANDATORY_CAPTION_PHRASE} {MANDATORY_ONLY_HASHTAG}"
        )
        campaign_title = _truncate_clean(positive_live_hook, 70)

        reels_copy = PlatformCopy(
            platform=Platform.INSTAGRAM_REELS,
            title=campaign_title,
            caption=campaign_caption,
            hashtags=[MANDATORY_ONLY_HASHTAG],
            credit_line="",
        )
        tiktok_copy = PlatformCopy(
            platform=Platform.TIKTOK,
            title=campaign_title,
            caption=campaign_caption,
            hashtags=[MANDATORY_ONLY_HASHTAG],
            credit_line="",
        )
        return {
            Platform.INSTAGRAM_REELS: reels_copy,
            Platform.TIKTOK: tiktok_copy,
        }

    def _deterministic_copies(self, plan: ClipPlan) -> dict[Platform, PlatformCopy]:
        is_pt = plan.language.lower().startswith("pt")
        creator = _ensure_at_handle(plan.source.creator_handle)
        credit_line = f"🎥 {creator}"

        clean_title = _strip_hashtags(sanitize_untrusted_video_text(plan.source.title, max_length=130))
        clean_summary = _strip_hashtags(sanitize_untrusted_video_text(plan.source.summary, max_length=260))
        clean_hook = _strip_hashtags(sanitize_untrusted_video_text(plan.hook_on_screen, max_length=90))
        facts = [
            _strip_hashtags(sanitize_untrusted_video_text(f, max_length=150))
            for f in plan.source.verified_facts
            if f and _strip_hashtags(sanitize_untrusted_video_text(f, max_length=150))
        ]

        spoiler_prefix = "⚠️ SPOILER: " if plan.spoiler_tag_required else ""

        fact_one = facts[0] if facts else (clean_summary or clean_title)
        fact_two = (
            facts[1]
            if len(facts) > 1
            else (
                "Análise quadro a quadro do universo de Leonida."
                if is_pt
                else "Frame-by-frame breakdown from the state of Leonida."
            )
        )

        # 1. X (Twitter): NO hashtags, short headline, blank line, bullets/reaction, 🎥 @creator last
        x_headline = _truncate_clean(f"{spoiler_prefix}{clean_title}", 95)
        x_b1 = _truncate_clean(fact_one, 72)
        x_b2 = _truncate_clean(fact_two, 72)
        x_caption = _strip_hashtags(f"{x_headline}\n\n• {x_b1}\n• {x_b2}\n\n{credit_line}")
        if len(x_caption) > 280:
            x_caption = _strip_hashtags(f"{_truncate_clean(x_headline, 80)}\n\n• {_truncate_clean(x_b1, 110)}\n\n{credit_line}")

        x_copy = PlatformCopy(
            platform=Platform.X,
            title="",
            caption=x_caption,
            hashtags=[],
            credit_line=credit_line,
        )

        # 2. Instagram Reels: Hook line, 1-2 value sentences, 🎥 @creator, 3 to 5 hashtags max
        if is_pt:
            reels_tags = ["#GTA6", "#GTAVI", "#GTA6Brasil", "#ViceCity"]
            reels_cta = "Qual detalhe mais chamou sua atenção em Leonida?"
        else:
            reels_tags = ["#GTA6", "#GTAVI", "#ViceCity", "#Leonida"]
            reels_cta = "Which Leonida detail caught your eye first?"

        if plan.source.is_official and len(reels_tags) < 5:
            reels_tags.append("#RockstarGames")

        reels_hook = clean_hook if clean_hook else f"{spoiler_prefix}{clean_title}"
        if plan.spoiler_tag_required and "SPOILER" not in reels_hook.upper():
            reels_hook = f"⚠️ SPOILER — {reels_hook}"
        reels_value = f"{fact_one.rstrip('.')}. {reels_cta}"
        reels_caption = (
            f"{reels_hook}\n\n{reels_value}\n\n{credit_line}\n\n{' '.join(reels_tags[:5])}"
        )
        reels_copy = PlatformCopy(
            platform=Platform.INSTAGRAM_REELS,
            title=_truncate_clean(reels_hook, 70),
            caption=reels_caption,
            hashtags=reels_tags[:5],
            credit_line=credit_line,
        )

        # 3. TikTok: Short punchy caption + 3-4 hashtags + 🎥 @creator
        if is_pt:
            tiktok_tags = ["#GTA6", "#GTAVI", "#GTA6Brasil", "#Games"]
            tiktok_punch = f"{reels_hook} — { _truncate_clean(fact_one, 90) }"
        else:
            tiktok_tags = ["#GTA6", "#GTAVI", "#Leonida", "#Gaming"]
            tiktok_punch = f"{reels_hook} — { _truncate_clean(fact_one, 90) }"

        tiktok_tags = tiktok_tags[:4]
        tiktok_caption = f"{tiktok_punch}\n\n{' '.join(tiktok_tags)}\n\n{credit_line}"
        tiktok_copy = PlatformCopy(
            platform=Platform.TIKTOK,
            title=_truncate_clean(reels_hook, 70),
            caption=tiktok_caption,
            hashtags=tiktok_tags,
            credit_line=credit_line,
        )

        # 4. YouTube Shorts: High-CTR title (<= 70 chars) + description with 🎥 @creator + #GTA6 #GTAVI #Shorts
        rules = self._campaign_rules()
        req_tags_raw = ["#GTA6", "#GTAVI", "#Shorts"]
        yt_tags: list[str] = []
        for t in req_tags_raw:
            tag = t if str(t).startswith("#") else f"#{t}"
            if tag.lower() not in {existing.lower() for existing in yt_tags}:
                yt_tags.append(tag)
        extra_yt = "#GTA6Brasil" if is_pt else "#RockstarGames"
        if extra_yt.lower() not in {existing.lower() for existing in yt_tags} and len(yt_tags) < 5:
            yt_tags.append(extra_yt)

        raw_yt_title = (
            f"{spoiler_prefix}GTA 6: {clean_title}"
            if not clean_title.upper().startswith("GTA")
            else f"{spoiler_prefix}{clean_title}"
        )
        yt_title = _truncate_clean(raw_yt_title, 70)

        disclaimer = (
            "Página de fãs com análise transformativa, sem afiliação com a Rockstar Games."
            if is_pt
            else "Transformative fan page commentary, not affiliated with Rockstar Games."
        )
        footer = str(rules.get("required_description_footer", "")).strip()
        desc_parts = [
            f"{reels_hook}",
            f"{fact_one.rstrip('.')}. {fact_two.rstrip('.')}.",
            disclaimer,
        ]
        if footer:
            desc_parts.append(footer)
        desc_parts.append(credit_line)
        desc_parts.append(" ".join(yt_tags))

        yt_copy = PlatformCopy(
            platform=Platform.YOUTUBE_SHORTS,
            title=yt_title,
            caption="\n\n".join(desc_parts),
            hashtags=yt_tags,
            credit_line=credit_line,
        )

        # 5. Threads (optional conversational copy if enabled)
        threads_caption = f"{reels_hook}\n\n{fact_one.rstrip('.')}.\n\n{credit_line}"
        threads_copy = PlatformCopy(
            platform=Platform.THREADS,
            title="",
            caption=threads_caption,
            hashtags=["#GTA6"],
            credit_line=credit_line,
        )

        return {
            Platform.YOUTUBE_SHORTS: yt_copy,
            Platform.INSTAGRAM_REELS: reels_copy,
            Platform.TIKTOK: tiktok_copy,
            Platform.X: x_copy,
            Platform.THREADS: threads_copy,
        }

    async def generate_captions(self, plan: ClipPlan) -> CaptionBundle:
        """Generate Section 7 platform-native captions (or strict Rule #10 KY4 campaign copies)."""
        duration = max(1.0, plan.end_sec - plan.start_sec)
        if not plan.subtitles and plan.voiceover_script.strip():
            plan.subtitles = build_subtitle_cues(plan.voiceover_script, duration)

        if plan.for_real_oficial_campaign:
            plan.language = "pt-BR"
            plan.source.metadata["mandatory_pinned_comment"] = MANDATORY_PINNED_COMMENT

            llm_hook = ""
            llm_camp = await self._call_llm_json(
                system_prompt=(
                    "Você cria um gancho curto e super positivo em PT-BR (máx 90 caracteres) sobre um corte "
                    "da Live Oficial YouTube x GTA VI. PROIBIDO usar hashtags (#), arrobas (@), logos, "
                    "comparações com GTA V/San Andreas/'antes do GTA 6' ou críticas. "
                    "Retorne JSON: {\"positive_live_hook\": \"...\"}"
                ),
                user_prompt=(
                    f"Título: {sanitize_untrusted_video_text(plan.source.title, 120)}\n"
                    f"Hook da tela: {sanitize_untrusted_video_text(plan.hook_on_screen, 80)}\n"
                    f"Fatos: {plan.source.verified_facts}"
                ),
                tier="hydrogen",
            )
            if llm_camp:
                candidate_hook = _clean_campaign_text(
                    str(llm_camp.get("positive_live_hook", "")), 95
                )
                if candidate_hook and not self._contains_unsafe_phrasing(
                    candidate_hook, for_campaign=True
                ):
                    llm_hook = candidate_hook

            camp_copies = self._build_campaign_copies(plan, llm_hook=llm_hook)
            valid_requested = [
                p
                for p in plan.target_platforms
                if p in (Platform.INSTAGRAM_REELS, Platform.TIKTOK)
            ]
            if not valid_requested:
                valid_requested = [Platform.INSTAGRAM_REELS, Platform.TIKTOK]
            else:
                # Ensure both campaign networks have ready-to-publish copies
                for req_p in (Platform.INSTAGRAM_REELS, Platform.TIKTOK):
                    if req_p not in valid_requested:
                        valid_requested.append(req_p)

            return CaptionBundle(
                plan_id=plan.id,
                copies={p: camp_copies[p] for p in valid_requested if p in camp_copies},
            )

        base_copies = self._deterministic_copies(plan)
        creator = _ensure_at_handle(plan.source.creator_handle)
        credit_line = f"🎥 {creator}"

        # Optional LLM copy refinement with strict guardrails per platform
        llm_data = await self._call_llm_json(
            system_prompt=(
                "You write platform-native GTA 6 social copy for a fan page (not affiliated with Rockstar Games). "
                "NEVER use 'this confirms', '100% confirmed', 'secret insider', 'we at Rockstar', or mention leaks. "
                "Return JSON with keys: x_headline, x_bullets (list of 2 strings), reels_hook, reels_body, "
                "tiktok_punchline, youtube_title (<=70 chars), youtube_summary."
            ),
            user_prompt=(
                f"Language: {plan.language}\nTitle: {sanitize_untrusted_video_text(plan.source.title, 140)}\n"
                f"Hook: {sanitize_untrusted_video_text(plan.hook_on_screen, 90)}\n"
                f"Facts: {plan.source.verified_facts}\nCreator: {creator}\n"
                f"Spoiler required: {plan.spoiler_tag_required}"
            ),
            tier="hydrogen",
        )
        if llm_data and not self._contains_unsafe_phrasing(json.dumps(llm_data, ensure_ascii=False)):
            self._apply_llm_refinements(plan, base_copies, llm_data, credit_line)

        # Always include the 4 Section 7 core platforms + any extra requested in plan.target_platforms
        desired_platforms: list[Platform] = [
            Platform.YOUTUBE_SHORTS,
            Platform.INSTAGRAM_REELS,
            Platform.TIKTOK,
            Platform.X,
        ]
        for plat in plan.target_platforms:
            if plat not in desired_platforms:
                desired_platforms.append(plat)

        selected_copies = {
            plat: base_copies[plat] for plat in desired_platforms if plat in base_copies
        }
        return CaptionBundle(plan_id=plan.id, copies=selected_copies)

    def _apply_llm_refinements(
        self,
        plan: ClipPlan,
        copies: dict[Platform, PlatformCopy],
        llm_data: dict[str, Any],
        credit_line: str,
    ) -> None:
        spoiler_prefix = "⚠️ SPOILER: " if plan.spoiler_tag_required else ""

        # X refinement (strictly zero hashtags, 🎥 @creator last)
        x_head = _strip_hashtags(sanitize_untrusted_video_text(str(llm_data.get("x_headline", "")), 90))
        x_bullets_raw = llm_data.get("x_bullets", [])
        if x_head and isinstance(x_bullets_raw, list) and x_bullets_raw:
            bullets = [
                f"• {_strip_hashtags(sanitize_untrusted_video_text(str(b), 80))}"
                for b in x_bullets_raw[:3]
                if str(b).strip()
            ]
            if bullets:
                if plan.spoiler_tag_required and "SPOILER" not in x_head.upper():
                    x_head = f"{spoiler_prefix}{x_head}"
                x_text = _strip_hashtags(f"{x_head}\n\n" + "\n".join(bullets) + f"\n\n{credit_line}")
                if len(x_text) <= 280:
                    copies[Platform.X].caption = x_text
                    copies[Platform.X].hashtags = []

        # Reels refinement (hook + 1-2 value sentences + credit + 3-5 hashtags)
        r_hook = _strip_hashtags(sanitize_untrusted_video_text(str(llm_data.get("reels_hook", "")), 90))
        r_body = _strip_hashtags(sanitize_untrusted_video_text(str(llm_data.get("reels_body", "")), 220))
        if r_hook and r_body:
            if plan.spoiler_tag_required and "SPOILER" not in r_hook.upper():
                r_hook = f"⚠️ SPOILER — {r_hook}"
            r_tags = copies[Platform.INSTAGRAM_REELS].hashtags[:5]
            copies[Platform.INSTAGRAM_REELS].caption = (
                f"{r_hook}\n\n{r_body}\n\n{credit_line}\n\n{' '.join(r_tags)}"
            )

        # TikTok refinement (punchy caption + 3-4 hashtags + credit)
        tt_punch = _strip_hashtags(sanitize_untrusted_video_text(str(llm_data.get("tiktok_punchline", "")), 130))
        if tt_punch:
            if plan.spoiler_tag_required and "SPOILER" not in tt_punch.upper():
                tt_punch = f"{spoiler_prefix}{tt_punch}"
            tt_tags = copies[Platform.TIKTOK].hashtags[:4]
            copies[Platform.TIKTOK].caption = (
                f"{tt_punch}\n\n{' '.join(tt_tags)}\n\n{credit_line}"
            )

        # YouTube Shorts refinement (title <= 70 chars + required hashtags + credit)
        yt_title = _strip_hashtags(sanitize_untrusted_video_text(str(llm_data.get("youtube_title", "")), 70))
        yt_sum = _strip_hashtags(sanitize_untrusted_video_text(str(llm_data.get("youtube_summary", "")), 260))
        if yt_title:
            if plan.spoiler_tag_required and "SPOILER" not in yt_title.upper():
                yt_title = f"{spoiler_prefix}{yt_title}"
            copies[Platform.YOUTUBE_SHORTS].title = _truncate_clean(yt_title, 70)
        if yt_sum:
            yt_tags = copies[Platform.YOUTUBE_SHORTS].hashtags
            is_pt = plan.language.lower().startswith("pt")
            disclaimer = (
                "Página de fãs com análise transformativa, sem afiliação com a Rockstar Games."
                if is_pt
                else "Transformative fan page commentary, not affiliated with Rockstar Games."
            )
            copies[Platform.YOUTUBE_SHORTS].caption = (
                f"{yt_sum}\n\n{disclaimer}\n\n{credit_line}\n\n{' '.join(yt_tags)}"
            )
