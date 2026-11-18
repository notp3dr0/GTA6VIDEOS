"""Analytics & Self-Improvement Learning Loop Agent for GTA 6 & Real Oficial."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from gta6_agents.config import AppConfig
from gta6_agents.models import ContentPillar
from gta6_agents.storage import StateStore


class AnalyticsLearningAgent:
    """Analyzes publication metrics, pillar balance, and Real Oficial CPM earnings."""

    def __init__(self, config: AppConfig, store: StateStore) -> None:
        self.config = config
        self.store = store

    def _local_now(self) -> datetime:
        try:
            tz = ZoneInfo(self.config.timezone)
        except ZoneInfoNotFoundError:
            tz = timezone.utc
        return datetime.now(tz)

    @staticmethod
    def _infer_pillar_from_publication(pub: dict[str, Any]) -> str:
        if pub.get("campaign_slug"):
            return ContentPillar.REAL_OFICIAL_CAMPAIGN.value
        caption = str(pub.get("caption_text") or "").lower()
        if any(k in caption for k in ("newswire", "rockstar", "trailer", "oficial", "official")):
            return ContentPillar.OFFICIAL_NEWS.value
        if any(k in caption for k in ("dica", "segredo", "detalhe", "tutorial", "tip", "hidden")):
            return ContentPillar.TIPS_TUTORIALS.value
        if any(k in caption for k in ("gta 5", "gta v", "nostalgia", "los santos")):
            return ContentPillar.GTA5_NOSTALGIA.value
        if any(k in caption for k in ("contagem", "faltam", "countdown", "recap", "lancamento", "lançamento")):
            return ContentPillar.COUNTDOWN_RECAP.value
        if any(k in caption for k in ("spotlight", "personagem", "lucia", "jason", "vice city", "leonida")):
            return ContentPillar.SPOTLIGHTS.value
        if "?" in caption:
            return ContentPillar.QUESTIONS.value
        return ContentPillar.GAMEPLAY_CLIPS.value

    def _seen_source_pillars(self) -> dict[str, int]:
        counts: Counter[str] = Counter()
        try:
            with self.store._connect() as conn:
                rows = conn.execute(
                    "SELECT pillar, COUNT(*) AS cnt FROM seen_sources GROUP BY pillar"
                ).fetchall()
                for row in rows:
                    pillar_name = str(row["pillar"] or ContentPillar.GAMEPLAY_CLIPS.value)
                    counts[pillar_name] += int(row["cnt"] or 0)
        except Exception:
            pass
        return dict(counts)

    def _build_recommendations(
        self,
        pillar_distribution: dict[str, int],
        publications_by_platform: dict[str, int],
        total_entries: int,
        validated_views: int,
        estimated_earnings_brl: float,
        pending_count: int,
        credits_today: int,
    ) -> list[str]:
        recs: list[str] = []

        campaign_cfg = (
            self.config.campaign.get("campaign", {})
            if isinstance(self.config.campaign, dict)
            else {}
        )
        scaling_cfg = (
            campaign_cfg.get("scaling_5_days", {})
            if isinstance(campaign_cfg, dict)
            else {}
        )
        daily_target = int(scaling_cfg.get("youtube_shorts_per_day", 4))
        yt_count = publications_by_platform.get("youtube_shorts", 0)

        if self.config.real_oficial_enabled:
            if yt_count < daily_target:
                recs.append(
                    f"Prioritize YouTube Shorts cadence ({yt_count}/{daily_target} posted so far) "
                    f"at BRT peak windows (10:00, 14:00, 18:00, 21:30) for '{self.config.real_oficial_campaign_slug}'."
                )
            else:
                recs.append(
                    f"Daily YouTube Shorts target reached ({yt_count}/{daily_target}); "
                    f"maintain hook retention in first 2.0s to grow validated views ({validated_views:,} views / R$ {estimated_earnings_brl:.2f})."
                )

        core_pillars = [
            ContentPillar.OFFICIAL_NEWS.value,
            ContentPillar.TIPS_TUTORIALS.value,
            ContentPillar.GAMEPLAY_CLIPS.value,
            ContentPillar.SPOTLIGHTS.value,
            ContentPillar.COUNTDOWN_RECAP.value,
        ]
        missing_pillars = [p for p in core_pillars if pillar_distribution.get(p, 0) == 0]
        if missing_pillars:
            recs.append(
                f"Diversify upcoming slots with underrepresented pillars: {', '.join(missing_pillars[:3])}."
            )

        if pending_count > 0:
            recs.append(
                f"Review {pending_count} pending item(s) via Telegram (/approve <plan_id> or /reject <plan_id>) or Mission Control."
            )

        credit_limit = max(1, self.config.real_oficial_daily_credit_limit)
        if credits_today >= int(credit_limit * 0.8):
            recs.append(
                f"Credit guard alert: {credits_today}/{credit_limit} Real Oficial credits used today; stick to zero-credit local FFmpeg pipeline."
            )

        if total_entries == 0 and self.config.real_oficial_enabled:
            recs.append(
                "No Real Oficial championship entries recorded yet; ensure YouTube Shorts uploads complete and register in 'gta-vi-youtube'."
            )

        if not recs:
            recs.append("Pipeline healthy and balanced across platforms and pillars; continue autonomous schedule.")

        return recs

    async def run_learning_loop(self) -> dict[str, Any]:
        """Analyze publications & championship entries, write insights to memory/learning_loop.md, and return summary."""
        now_local = self._local_now()
        now_utc = datetime.now(timezone.utc)

        publications = self.store.list_publications(limit=500)
        entries = self.store.list_championship_entries(limit=500)
        pending = self.store.list_pending_approvals()
        credits_today = self.store.today_credit_spend("real_oficial")

        platform_counter: Counter[str] = Counter()
        status_counter: Counter[str] = Counter()
        pub_pillar_counter: Counter[str] = Counter()

        for pub in publications:
            platform_counter[str(pub.get("platform") or "unknown")] += 1
            status_counter[str(pub.get("status") or "unknown")] += 1
            pub_pillar_counter[self._infer_pillar_from_publication(pub)] += 1

        source_pillars = self._seen_source_pillars()
        combined_pillars: Counter[str] = Counter(pub_pillar_counter)
        for pillar_key, count in source_pillars.items():
            if pillar_key not in combined_pillars:
                combined_pillars[pillar_key] = count

        pillar_distribution = dict(sorted(combined_pillars.items(), key=lambda kv: (-kv[1], kv[0])))

        entry_status_counter: Counter[str] = Counter()
        validated_views = 0
        estimated_earnings_raw = 0.0
        cpm_values: list[float] = []

        for entry in entries:
            entry_status_counter[str(entry.get("status") or "unknown")] += 1
            views = int(entry.get("validated_views") or 0)
            cpm = float(entry.get("estimated_cpm_brl") or 1.25)
            validated_views += views
            estimated_earnings_raw += (views / 1000.0) * cpm
            cpm_values.append(cpm)

        estimated_earnings_brl = round(estimated_earnings_raw, 2)
        avg_cpm_brl = round(sum(cpm_values) / len(cpm_values), 2) if cpm_values else 1.25

        recommendations = self._build_recommendations(
            pillar_distribution=pillar_distribution,
            publications_by_platform=dict(platform_counter),
            total_entries=len(entries),
            validated_views=validated_views,
            estimated_earnings_brl=estimated_earnings_brl,
            pending_count=len(pending),
            credits_today=credits_today,
        )

        learning_path = self.config.memory_dir / "learning_loop.md"
        if not learning_path.exists():
            learning_path.write_text(
                "# GTA 6 & Real Oficial (`gta-vi-youtube`) — Analytics Learning Loop\n\n"
                "Automated multi-agent telemetry, pillar balance, and estimated CPM earnings log.\n\n",
                encoding="utf-8",
            )

        pillar_str = (
            ", ".join(f"`{k}`: {v}" for k, v in pillar_distribution.items())
            if pillar_distribution
            else "none yet"
        )
        platform_str = (
            ", ".join(f"`{k}`: {v}" for k, v in platform_counter.items())
            if platform_counter
            else "none yet"
        )
        rec_lines = "\n".join(f"  - {r}" for r in recommendations)

        markdown_entry = (
            f"## [{now_local.strftime('%Y-%m-%d %H:%M:%S %Z')}] Learning Cycle Insight\n"
            f"- **Total Publications**: {len(publications)} ({platform_str})\n"
            f"- **Pillar Distribution**: {pillar_str}\n"
            f"- **Real Oficial (`{self.config.real_oficial_campaign_slug}`)**: "
            f"{len(entries)} entries | **{validated_views:,}** validated views | "
            f"**R$ {estimated_earnings_brl:.2f}** est. earnings (avg CPM R$ {avg_cpm_brl:.2f})\n"
            f"- **Credits Today**: {credits_today}/{self.config.real_oficial_daily_credit_limit} | "
            f"**Pending Approvals**: {len(pending)}\n"
            f"- **Next-Cycle Adjustments**:\n{rec_lines}\n\n"
        )
        with learning_path.open("a", encoding="utf-8") as fh:
            fh.write(markdown_entry)

        summary: dict[str, Any] = {
            "timestamp": now_utc.isoformat(),
            "local_time": now_local.isoformat(),
            "total_publications": len(publications),
            "publications_by_platform": dict(platform_counter),
            "publications_by_status": dict(status_counter),
            "pillar_distribution": pillar_distribution,
            "source_pillars": source_pillars,
            "validated_views": validated_views,
            "estimated_earnings_brl": estimated_earnings_brl,
            "average_cpm_brl": avg_cpm_brl,
            "championship": {
                "campaign_slug": self.config.real_oficial_campaign_slug,
                "total_entries": len(entries),
                "entries_by_status": dict(entry_status_counter),
                "validated_views": validated_views,
                "average_cpm_brl": avg_cpm_brl,
                "estimated_earnings_brl": estimated_earnings_brl,
            },
            "pending_approvals": len(pending),
            "credits_spent_today": credits_today,
            "daily_credit_limit": self.config.real_oficial_daily_credit_limit,
            "recommendations": recommendations,
            "learning_log_path": str(learning_path),
        }
        return summary
