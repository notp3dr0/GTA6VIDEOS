"""24/7 Autonomous Scheduler (APScheduler) for GTA 6 & Real Oficial 5-Day Operation."""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from gta6_agents.config import AppConfig
from gta6_agents.models import ContentPillar, utc_now
from gta6_agents.orchestrator.pipeline import MultiAgentOrchestrator
from gta6_agents.storage import StateStore

logger = logging.getLogger(__name__)

DEFAULT_CAMPAIGN_SHORT_SLOTS_BRT: list[str] = ["10:00", "14:00", "18:00", "21:30"]

DEFAULT_X_SLOTS: list[dict[str, str]] = [
    {"time": "08:30", "pillar": ContentPillar.OFFICIAL_NEWS.value},
    {"time": "10:30", "pillar": ContentPillar.TIPS_TUTORIALS.value},
    {"time": "13:00", "pillar": ContentPillar.GAMEPLAY_CLIPS.value},
    {"time": "15:30", "pillar": ContentPillar.GTA5_NOSTALGIA.value},
    {"time": "18:30", "pillar": ContentPillar.SPOTLIGHTS.value},
    {"time": "21:30", "pillar": ContentPillar.COUNTDOWN_RECAP.value},
]


def _parse_hhmm(raw: str) -> tuple[int, int]:
    parts = raw.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid HH:MM slot string: {raw!r}")
    hour = int(parts[0])
    minute = int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"Out-of-range HH:MM slot string: {raw!r}")
    return hour, minute


class DailyScheduler:
    """Schedules Real Oficial Shorts, 6 daily X slots, 60s Telegram polling, and 6h learning loop."""

    def __init__(
        self,
        config: AppConfig,
        orchestrator: MultiAgentOrchestrator | None = None,
        store: StateStore | None = None,
    ) -> None:
        self.config = config
        self.store = store or (
            orchestrator.store
            if orchestrator is not None
            else StateStore(data_dir=config.data_dir, memory_dir=config.memory_dir)
        )
        self.orchestrator = orchestrator or MultiAgentOrchestrator(config, store=self.store)

        try:
            self.tz: ZoneInfo | timezone = ZoneInfo(config.timezone)
        except ZoneInfoNotFoundError:
            logger.warning("Timezone '%s' not found; falling back to UTC.", config.timezone)
            self.tz = timezone.utc

        self.scheduler = AsyncIOScheduler(
            timezone=self.tz,
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 1800,
            },
        )
        self._jobs_configured = False

    def _resolve_short_slots(self) -> list[str]:
        campaign_root = (
            self.config.campaign.get("campaign", {})
            if isinstance(self.config.campaign, dict)
            else {}
        )
        scaling = (
            campaign_root.get("scaling_5_days", {})
            if isinstance(campaign_root, dict)
            else {}
        )
        raw_slots = scaling.get("preferred_times_brt")
        if isinstance(raw_slots, list) and raw_slots:
            return [str(s).strip() for s in raw_slots if str(s).strip()]
        return list(DEFAULT_CAMPAIGN_SHORT_SLOTS_BRT)

    def _resolve_x_slots(self) -> list[tuple[str, ContentPillar]]:
        platforms_cfg = (self.config.settings or {}).get("platforms", {})
        x_cfg = platforms_cfg.get("x", {}) if isinstance(platforms_cfg, dict) else {}
        raw_slots = x_cfg.get("slots") if isinstance(x_cfg, dict) else None
        source_list = raw_slots if isinstance(raw_slots, list) and raw_slots else DEFAULT_X_SLOTS

        parsed: list[tuple[str, ContentPillar]] = []
        for entry in source_list:
            if not isinstance(entry, dict):
                continue
            time_str = str(entry.get("time", "")).strip()
            pillar_raw = str(entry.get("pillar", ContentPillar.GAMEPLAY_CLIPS.value)).strip()
            if not time_str:
                continue
            try:
                pillar = ContentPillar(pillar_raw)
            except ValueError:
                pillar = ContentPillar.GAMEPLAY_CLIPS
            parsed.append((time_str, pillar))
        return parsed

    async def _run_short_slot(self, slot_time: str) -> None:
        logger.info("Executing scheduled Real Oficial YouTube Short slot (%s %s)...", slot_time, self.config.timezone)
        try:
            await self.orchestrator.run_daily_cycle(
                max_items=1,
                include_campaign=True,
                demo_video_fallback=self.config.dry_run,
            )
        except Exception as exc:
            logger.exception("Scheduled Short slot (%s) failed: %s", slot_time, exc)
            await self.orchestrator.notifier.notify_error(f"Scheduled Short slot {slot_time}", exc)

    async def _run_x_slot(self, pillar: ContentPillar) -> None:
        logger.info("Executing scheduled X slot (pillar=%s)...", pillar.value)
        try:
            await self.orchestrator.run_x_text_slot(pillar)
        except Exception as exc:
            logger.exception("Scheduled X slot (%s) failed: %s", pillar.value, exc)
            await self.orchestrator.notifier.notify_error(f"Scheduled X slot ({pillar.value})", exc)

    async def _run_telegram_poll(self) -> None:
        self.store.set_flag("last_heartbeat_at", utc_now().isoformat())
        try:
            commands = await self.orchestrator.notifier.poll_telegram_commands_once()
            if commands:
                logger.info("Processed remote Telegram command(s): %s", ", ".join(commands))
        except Exception as exc:
            logger.warning("Telegram poll cycle warning: %s", exc)

    async def _run_learning_loop(self) -> None:
        logger.info("Running scheduled 6-hour Analytics Learning Loop...")
        try:
            await self.orchestrator.analytics.run_learning_loop()
        except Exception as exc:
            logger.exception("Scheduled learning loop failed: %s", exc)
            await self.orchestrator.notifier.notify_error("Scheduled Analytics Learning Loop", exc)

    def configure_jobs(self) -> list[dict[str, Any]]:
        """Register all daily CronTriggers and periodic IntervalTriggers on AsyncIOScheduler."""
        # 1. Real Oficial 'gta-vi-youtube' Shorts slots (10:00, 14:00, 18:00, 21:30 BRT)
        for slot_str in self._resolve_short_slots():
            hour, minute = _parse_hhmm(slot_str)
            job_id = f"real_oficial_short_{hour:02d}_{minute:02d}"
            self.scheduler.add_job(
                self._run_short_slot,
                trigger=CronTrigger(hour=hour, minute=minute, timezone=self.tz),
                args=[slot_str],
                id=job_id,
                name=f"Real Oficial Short ({slot_str} {self.config.timezone})",
                replace_existing=True,
            )

        # 2. 6 Daily X (Twitter) slots (08:30, 10:30, 13:00, 15:30, 18:30, 21:30 BRT)
        for slot_str, pillar in self._resolve_x_slots():
            hour, minute = _parse_hhmm(slot_str)
            job_id = f"x_slot_{hour:02d}_{minute:02d}_{pillar.value}"
            self.scheduler.add_job(
                self._run_x_slot,
                trigger=CronTrigger(hour=hour, minute=minute, timezone=self.tz),
                args=[pillar],
                id=job_id,
                name=f"X Slot ({slot_str} -> {pillar.value})",
                replace_existing=True,
            )

        # 3. Mobile Telegram command polling every 60 seconds
        self.scheduler.add_job(
            self._run_telegram_poll,
            trigger=IntervalTrigger(seconds=60, timezone=self.tz),
            id="telegram_poll_60s",
            name="Telegram Remote Control Poll (60s)",
            replace_existing=True,
        )

        # 4. Self-improving Analytics Learning Loop every 6 hours
        self.scheduler.add_job(
            self._run_learning_loop,
            trigger=IntervalTrigger(hours=6, timezone=self.tz),
            id="analytics_learning_6h",
            name="Analytics & CPM Learning Loop (6h)",
            replace_existing=True,
        )

        self._jobs_configured = True
        return self.get_jobs_summary()

    def get_jobs_summary(self) -> list[dict[str, Any]]:
        jobs_info: list[dict[str, Any]] = []
        for job in self.scheduler.get_jobs():
            next_run = getattr(job, "next_run_time", None)
            jobs_info.append(
                {
                    "id": job.id,
                    "name": job.name,
                    "trigger": str(job.trigger),
                    "next_run_time": next_run.isoformat() if next_run else None,
                }
            )
        return jobs_info

    def start(self) -> None:
        if not self._jobs_configured:
            self.configure_jobs()
        if not self.store.get_flag("autonomy_started_at", ""):
            self.store.set_flag("autonomy_started_at", utc_now().isoformat())
        self.store.set_flag("last_heartbeat_at", utc_now().isoformat())
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info(
                "DailyScheduler started in timezone=%s with %d active jobs.",
                self.config.timezone,
                len(self.scheduler.get_jobs()),
            )

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("DailyScheduler shut down cleanly.")

    async def run_forever(self, stop_event: asyncio.Event | None = None) -> None:
        """Start the 24/7 scheduler and block until SIGINT/SIGTERM or stop_event."""
        event = stop_event or asyncio.Event()
        loop = asyncio.get_running_loop()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, event.set)
            except (NotImplementedError, RuntimeError):
                pass

        self.start()
        try:
            await event.wait()
        finally:
            self.shutdown()
