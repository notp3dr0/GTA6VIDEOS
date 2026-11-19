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
        daily_target = 60
        campaign_posts_count = (
            publications_by_platform.get("instagram_reels", 0)
            + publications_by_platform.get("tiktok", 0)
        )

        if self.config.real_oficial_enabled:
            if campaign_posts_count < daily_target:
                recs.append(
                    f"Acelerar cadência de cortes ao vivo no TikTok + Instagram Reels ({campaign_posts_count}/{daily_target} hoje) "
                    f"no horário nobre BR (19:00-00:00 e turbo Skorpion 22:30-22:57) para '{self.config.real_oficial_campaign_slug}'."
                )
            else:
                recs.append(
                    f"Meta diária atingida ({campaign_posts_count}/{daily_target}); "
                    f"manter retenção nos primeiros 2.0s para passar de 5.000 views/vídeo ({validated_views:,} views / R$ {estimated_earnings_brl:.2f})."
                )

        if pending_count > 0:
            recs.append(
                f"Revisar {pending_count} item(ns) pendente(s) no Telegram (/approve <plan_id> ou /reject <plan_id>) — ex: validação visual anti-Samira Close."
            )

        credit_limit = max(1, self.config.real_oficial_daily_credit_limit)
        if credits_today >= int(credit_limit * 0.8):
            recs.append(
                f"Alerta de créditos: {credits_today}/{credit_limit} créditos Real Oficial usados hoje; manter pipeline local FFmpeg 100% gratuito."
            )

        if total_entries == 0 and self.config.real_oficial_enabled:
            recs.append(
                "Nenhuma entrada registrada ainda na campanha; registre o link imediatamente após postar no TikTok/Instagram Reels (só contam views pós-registro!)."
            )

        if not recs:
            recs.append("Pipeline saudável nos 5 canais PT-BR e 3 IAs; manter escala autônoma.")

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

        campaign_cfg = (
            self.config.campaign.get("campaign", {})
            if isinstance(self.config.campaign, dict)
            else {}
        )
        rewards_cfg = campaign_cfg.get("rewards", {}) if isinstance(campaign_cfg, dict) else {}
        default_cpm = float(rewards_cfg.get("cpm_brl_per_1k_views", 4.00))
        min_views = int(rewards_cfg.get("min_views_threshold_per_video", 5000))
        max_per_video = float(rewards_cfg.get("max_payout_brl_per_video", 400.00))

        entry_status_counter: Counter[str] = Counter()
        validated_views = 0
        estimated_earnings_raw = 0.0
        cpm_values: list[float] = []

        for entry in entries:
            entry_status_counter[str(entry.get("status") or "unknown")] += 1
            views = int(entry.get("validated_views") or 0)
            cpm = float(entry.get("estimated_cpm_brl") or default_cpm)
            if views >= min_views:
                validated_views += views
                estimated_earnings_raw += min(max_per_video, (views / 1000.0) * cpm)
            cpm_values.append(cpm)

        estimated_earnings_brl = round(min(20000.0, estimated_earnings_raw), 2)
        avg_cpm_brl = round(sum(cpm_values) / len(cpm_values), 2) if cpm_values else default_cpm

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

        scoreboard_path = self._sync_scoreboard_csv(publications, now_local)
        payouts_path = self._sync_payouts_csv(
            entries=entries,
            default_cpm=default_cpm,
            min_views=min_views,
            max_per_video=max_per_video,
            now_local=now_local,
        )

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
            "scoreboard_csv_path": str(scoreboard_path),
            "payouts_csv_path": str(payouts_path),
        }
        return summary

    def _sync_scoreboard_csv(
        self,
        publications: list[dict[str, Any]],
        now_local: datetime,
    ) -> Any:
        """Persist 12h/24h/72h learning-loop scoreboard (`memory/scoreboard.csv`, ported from learning-loop skill)."""
        import csv

        path = self.config.memory_dir / "scoreboard.csv"
        fieldnames = [
            "post_id",
            "date",
            "time",
            "platform",
            "pillar",
            "hook_preview",
            "public_url",
            "status",
            "test_name",
            "views_12h",
            "views_24h",
            "views_72h",
            "notes",
        ]
        existing_ids: set[str] = set()
        if path.exists():
            try:
                with path.open("r", encoding="utf-8", newline="") as fh:
                    for row in csv.DictReader(fh):
                        pid = (row.get("post_id") or "").strip()
                        if pid:
                            existing_ids.add(pid)
            except Exception:
                pass

        write_header = not path.exists() or path.stat().st_size == 0
        with path.open("a", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            if write_header:
                writer.writeheader()
            for pub in publications:
                pid = str(pub.get("id") or "").strip()
                if not pid or pid in existing_ids:
                    continue
                existing_ids.add(pid)
                caption_first_line = str(pub.get("caption_text") or "").splitlines()[0][:80] if pub.get("caption_text") else ""
                writer.writerow(
                    {
                        "post_id": pid,
                        "date": now_local.strftime("%Y-%m-%d"),
                        "time": now_local.strftime("%H:%M"),
                        "platform": str(pub.get("platform") or ""),
                        "pillar": self._infer_pillar_from_publication(pub),
                        "hook_preview": caption_first_line,
                        "public_url": str(pub.get("public_url") or ""),
                        "status": str(pub.get("status") or ""),
                        "test_name": "kinetic_react_v2",
                        "views_12h": "",
                        "views_24h": "",
                        "views_72h": "",
                        "notes": "Read after 24h/72h before changing rule",
                    }
                )
        return path

    def _sync_payouts_csv(
        self,
        *,
        entries: list[dict[str, Any]],
        default_cpm: float,
        min_views: int,
        max_per_video: float,
        now_local: datetime,
    ) -> Any:
        """Write transparent campaign payout ledger (`memory/payouts.csv`, ported from scripts/payouts.py)."""
        import csv

        path = self.config.memory_dir / "payouts.csv"
        fieldnames = [
            "date",
            "campaign",
            "platform",
            "post_url",
            "views",
            "rate_per_1k_brl",
            "est_payout_brl",
            "status",
        ]
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for entry in entries:
                views = int(entry.get("validated_views") or 0)
                cpm = float(entry.get("estimated_cpm_brl") or default_cpm)
                est = (
                    round(min(max_per_video, (views / 1000.0) * cpm), 2)
                    if views >= min_views
                    else 0.0
                )
                writer.writerow(
                    {
                        "date": str(entry.get("submitted_at") or now_local.strftime("%Y-%m-%d"))[:10],
                        "campaign": str(entry.get("campaign_slug") or self.config.real_oficial_campaign_slug),
                        "platform": str(entry.get("platform") or "tiktok_or_reels"),
                        "post_url": str(entry.get("public_url") or entry.get("youtube_url") or ""),
                        "views": views,
                        "rate_per_1k_brl": f"{cpm:.2f}",
                        "est_payout_brl": f"{est:.2f}",
                        "status": str(entry.get("status") or "pending_submission"),
                    }
                )
        return path
