"""Multi-Agent Orchestrator coordinating all 7 specialized GTA 6 & Real Oficial agents."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from datetime import date, datetime, timezone
from typing import Any

from gta6_agents.agents.analytics import AnalyticsLearningAgent
from gta6_agents.agents.clipper import ClipperEditorAgent
from gta6_agents.agents.compliance import ComplianceFactAgent
from gta6_agents.agents.copywriter import ScriptCopyAgent
from gta6_agents.agents.publisher import PublisherAgent
from gta6_agents.agents.real_oficial import RealOficialAgent
from gta6_agents.agents.scout import ScoutAgent
from gta6_agents.config import AppConfig
from gta6_agents.models import (
    ApprovalStatus,
    CaptionBundle,
    ChampionshipEntry,
    ClipPlan,
    ComplianceVerdict,
    ContentPillar,
    Platform,
    PlatformCopy,
    PublicationRecord,
    SourceCandidate,
    new_id,
    utc_now,
)
from gta6_agents.orchestrator.notifier import RemoteNotifier
from gta6_agents.storage import StateStore

logger = logging.getLogger(__name__)


def _instantiate_agent(agent_cls: type[Any], config: AppConfig, store: StateStore) -> Any:
    """Instantiate an agent safely whether its __init__ accepts (config, store) or (config)."""
    sig = inspect.signature(agent_cls.__init__)
    params = [p for name, p in sig.parameters.items() if name != "self"]
    param_names = [p.name for p in params]
    has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params)
    has_var_pos = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params)

    if "store" in param_names:
        if "config" in param_names:
            return agent_cls(config=config, store=store)
        return agent_cls(config, store)
    if len(params) >= 2 or has_var_pos:
        second = params[1] if len(params) >= 2 else None
        if second is None or second.default is inspect.Parameter.empty:
            return agent_cls(config, store)
    if len(params) >= 1 or has_var_kw:
        return agent_cls(config)
    return agent_cls()


class MultiAgentOrchestrator:
    """Coordinates Scout, Copywriter, Compliance, Clipper, Publisher, RealOficial, and Analytics."""

    def __init__(self, config: AppConfig, store: StateStore | None = None) -> None:
        self.config = config
        self.store = store or StateStore(data_dir=config.data_dir, memory_dir=config.memory_dir)

        # 7 specialized agents + mobile notifier + 5-channel matrix + 3-AI pool
        self.scout = _instantiate_agent(ScoutAgent, self.config, self.store)
        self.copywriter = _instantiate_agent(ScriptCopyAgent, self.config, self.store)
        self.compliance = _instantiate_agent(ComplianceFactAgent, self.config, self.store)
        self.clipper = _instantiate_agent(ClipperEditorAgent, self.config, self.store)
        self.publisher = _instantiate_agent(PublisherAgent, self.config, self.store)
        self.real_oficial = _instantiate_agent(RealOficialAgent, self.config, self.store)
        self.analytics = AnalyticsLearningAgent(self.config, self.store)
        self.notifier = RemoteNotifier(self.config, self.store)

        from gta6_agents.agents.live_hype_monitor import LiveHypeMonitorAgent
        from gta6_agents.orchestrator.multi_channel import MultiChannelCoordinator
        from gta6_agents.orchestrator.tri_engine_pool import TriEnginePool

        self.hype_monitor = LiveHypeMonitorAgent(self.config, self.store)

        try:
            self.multi_channel: MultiChannelCoordinator | None = MultiChannelCoordinator(
                self.config, self.store
            )
        except Exception as exc:
            logger.warning("MultiChannelCoordinator init fallback: %s", exc)
            self.multi_channel = None

        try:
            self.tri_engine_pool: TriEnginePool | None = TriEnginePool(self.config, self.store)
        except Exception as exc:
            logger.warning("TriEnginePool init fallback: %s", exc)
            self.tri_engine_pool = None

        # Concurrency semaphores tuned for 2nd PC autonomous execution
        self.worker_semaphore = asyncio.BoundedSemaphore(3)
        self.render_semaphore = asyncio.BoundedSemaphore(2)

    def _primary_language(self) -> str:
        page_cfg = (self.config.settings or {}).get("page", {})
        if isinstance(page_cfg, dict) and page_cfg.get("primary_language"):
            return str(page_cfg["primary_language"])
        return "pt-BR"

    def _valid_campaign_platforms(self) -> set[Platform]:
        raw_campaign = self.config.campaign if isinstance(self.config.campaign, dict) else {}
        block = raw_campaign.get("campaign", raw_campaign) if isinstance(raw_campaign, dict) else {}
        configured = block.get("valid_platforms") if isinstance(block, dict) else None
        if isinstance(configured, list) and configured:
            valid: set[Platform] = set()
            for p_raw in configured:
                try:
                    valid.add(Platform(str(p_raw).strip()))
                except ValueError:
                    continue
            if valid:
                return valid
        return {Platform.INSTAGRAM_REELS, Platform.TIKTOK}

    async def _evaluate_compliance(
        self, plan: ClipPlan, bundle: CaptionBundle
    ) -> ComplianceVerdict:
        res = self.compliance.evaluate_plan(plan, bundle)
        if inspect.isawaitable(res):
            return await res
        return res

    async def _render_publish_and_register(
        self,
        plan: ClipPlan,
        bundle: CaptionBundle,
        demo_video_fallback: bool,
    ) -> dict[str, Any]:
        """Render vertical 1080x1920 clip, publish to target platforms, and immediately register in Real Oficial."""
        async with self.render_semaphore:
            clip = await self.clipper.render_clip_plan(
                plan,
                use_demo_source_if_needed=demo_video_fallback,
            )

        publications = await self.publisher.publish_all(plan, clip, bundle)

        valid_campaign_platforms = self._valid_campaign_platforms()
        register_fn = getattr(
            self.real_oficial,
            "register_campaign_post",
            self.real_oficial.register_youtube_short_in_campaign,
        )

        entries: list[ChampionshipEntry] = []
        for pub in publications:
            if (
                pub.platform in valid_campaign_platforms
                and plan.for_real_oficial_campaign
                and self.config.real_oficial_enabled
                and pub.status != "failed"
            ):
                entry = await register_fn(pub)
                if entry is not None:
                    entries.append(entry)
                    await self.notifier.notify_campaign_entry(entry, pub)

        await self.notifier.notify_published(plan, publications, entries)

        return {
            "status": "published",
            "plan_id": plan.id,
            "clip_id": clip.id,
            "title": plan.source.title,
            "hook": plan.hook_on_screen,
            "video_path": clip.video_path,
            "ass_subtitle_path": clip.ass_subtitle_path,
            "duration_sec": clip.duration_sec,
            "publications": [p.model_dump(mode="json") for p in publications],
            "championship_entries": [e.model_dump(mode="json") for e in entries],
        }

    async def _process_approved_queue(self, demo_video_fallback: bool = False) -> list[dict[str, Any]]:
        """Render and publish any pending plans approved remotely via Telegram (/approve) or Dashboard."""
        results: list[dict[str, Any]] = []
        try:
            with self.store._connect() as conn:
                rows = conn.execute(
                    "SELECT plan_id, payload_json FROM pending_approvals WHERE status = ?",
                    (ApprovalStatus.APPROVED.value,),
                ).fetchall()
        except Exception as exc:
            logger.warning("Could not query approved queue: %s", exc)
            return results

        for row in rows:
            plan_id = str(row["plan_id"])
            try:
                payload = json.loads(row["payload_json"])
                plan = ClipPlan.model_validate(payload["plan"])
                bundle = CaptionBundle.model_validate(payload["bundle"])
                item_res = await self._render_publish_and_register(
                    plan=plan,
                    bundle=bundle,
                    demo_video_fallback=demo_video_fallback,
                )
                self.store.update_approval_status(plan_id, "completed")
                results.append(item_res)
            except Exception as exc:
                logger.exception("Failed processing human-approved plan %s: %s", plan_id, exc)
                self.store.update_approval_status(plan_id, "failed")
                await self.notifier.notify_error(f"Approved plan {plan_id}", exc)
                results.append({"status": "error", "plan_id": plan_id, "error": str(exc)})

        return results

    async def _process_candidate(
        self,
        source: SourceCandidate,
        include_campaign: bool,
        demo_video_fallback: bool,
    ) -> dict[str, Any]:
        """Run ScriptCopyAgent -> ComplianceFactAgent -> ClipperEditorAgent -> PublisherAgent -> RealOficialAgent."""
        async with self.worker_semaphore:
            try:
                raw_campaign = self.config.campaign if isinstance(self.config.campaign, dict) else {}
                c_block = raw_campaign.get("campaign", raw_campaign) if isinstance(raw_campaign, dict) else {}
                only_auth_live = bool(
                    isinstance(c_block, dict)
                    and c_block.get("rules", {}).get("only_authorized_live_source", False)
                )
                if only_auth_live:
                    eligible_source = (
                        "KY4DWumZffg" in source.url
                        or source.is_campaign_acervo
                        or source.pillar == ContentPillar.REAL_OFICIAL_CAMPAIGN
                        or bool(source.campaign_slug)
                    )
                else:
                    eligible_source = (
                        source.is_campaign_acervo
                        or source.is_official
                        or source.pillar == ContentPillar.REAL_OFICIAL_CAMPAIGN
                        or bool(source.campaign_slug)
                    )

                for_campaign = bool(
                    include_campaign
                    and self.config.real_oficial_enabled
                    and eligible_source
                )
                language = "pt-BR" if for_campaign else self._primary_language()

                plan = await self.copywriter.build_clip_plan(
                    source,
                    language=language,
                    for_campaign=for_campaign,
                )
                bundle = await self.copywriter.generate_captions(plan)
                verdict = await self._evaluate_compliance(plan, bundle)

                if verdict.approval_status == ApprovalStatus.REJECTED or (
                    not verdict.passed and verdict.approval_status != ApprovalStatus.PENDING_HUMAN
                ):
                    logger.info(
                        "Skipping rejected plan %s (%s): %s",
                        plan.id,
                        source.title,
                        "; ".join(verdict.reasons),
                    )
                    return {
                        "status": "rejected",
                        "plan_id": plan.id,
                        "title": source.title,
                        "reasons": verdict.reasons,
                    }

                if (
                    verdict.approval_status == ApprovalStatus.PENDING_HUMAN
                    and self.config.autopilot_mode != "full_auto"
                ):
                    approval_payload = {
                        "hook": plan.hook_on_screen,
                        "title": source.title,
                        "reasons": verdict.reasons + verdict.warnings,
                        "plan": plan.model_dump(mode="json"),
                        "bundle": bundle.model_dump(mode="json"),
                        "verdict": verdict.model_dump(mode="json"),
                    }
                    self.store.save_pending_approval(plan.id, approval_payload)
                    if not self.store.is_source_seen(source.url):
                        self.store.record_source(source)
                    await self.notifier.notify_pending_approval(plan, verdict, bundle)
                    return {
                        "status": "pending_human",
                        "plan_id": plan.id,
                        "title": source.title,
                        "reasons": verdict.reasons + verdict.warnings,
                    }

                if not self.store.is_source_seen(source.url):
                    self.store.record_source(source)

                return await self._render_publish_and_register(
                    plan=plan,
                    bundle=bundle,
                    demo_video_fallback=demo_video_fallback,
                )

            except Exception as exc:
                logger.exception("Error processing candidate '%s': %s", source.title, exc)
                await self.notifier.notify_error(f"Candidate '{source.title}'", exc)
                return {
                    "status": "error",
                    "title": source.title,
                    "source_url": source.url,
                    "error": str(exc),
                }

    async def run_daily_cycle(
        self,
        max_items: int = 4,
        include_campaign: bool = True,
        demo_video_fallback: bool = False,
        channel_id: str = "",
    ) -> dict[str, Any]:
        """Run a full multi-agent discovery, verification, 9:16 rendering, publishing, and learning cycle."""
        self.store.set_flag("last_heartbeat_at", utc_now().isoformat())

        if self.store.get_flag("paused", "false").strip().lower() == "true":
            logger.info("Orchestrator is paused via system_flags (paused=true); skipping cycle.")
            return {
                "status": "paused",
                "paused": True,
                "discovered": 0,
                "rendered": 0,
                "published_posts": 0,
                "pending_approval": 0,
                "rejected": 0,
                "championship_entries": 0,
                "items": [],
                "errors": [],
            }

        campaign_status: dict[str, Any] | None = None
        if include_campaign and self.config.real_oficial_enabled:
            try:
                campaign_status = await self.real_oficial.sync_campaign_status()
            except Exception as exc:
                logger.warning("Real Oficial status sync warning: %s", exc)

        approved_items = await self._process_approved_queue(demo_video_fallback=demo_video_fallback)

        active_mc_channel = ""
        if channel_id and self.multi_channel is not None:
            try:
                self.multi_channel.get_channel(channel_id)
                active_mc_channel = channel_id
            except ValueError:
                active_mc_channel = ""

        discover_limit = max(max_items * 6, 8) if active_mc_channel else max_items
        try:
            candidates = await self.scout.discover_candidates(
                limit=discover_limit,
                include_campaign_acervo=include_campaign,
            )
        except Exception as exc:
            logger.exception("ScoutAgent discovery failed: %s", exc)
            await self.notifier.notify_error("ScoutAgent.discover_candidates", exc)
            candidates = []

        selected_candidates: list[SourceCandidate] = []
        if active_mc_channel and self.multi_channel is not None:
            today_str = self.multi_channel._today_brt_str()
            for cand in candidates:
                if len(selected_candidates) >= max(0, max_items):
                    break
                canonical = str(cand.metadata.get("canonical_url") or cand.url.split("#", 1)[0])
                end_sec = max(cand.suggested_start_sec + 1.0, cand.suggested_end_sec)
                claimed, adj_start, adj_end = self.multi_channel.claim_unique_segment(
                    channel_id=active_mc_channel,
                    source_url=canonical,
                    start_sec=cand.suggested_start_sec,
                    end_sec=end_sec,
                    date_str=today_str,
                )
                if claimed:
                    updated_meta = dict(cand.metadata)
                    updated_meta["target_channel_id"] = active_mc_channel
                    selected_candidates.append(
                        cand.model_copy(
                            update={
                                "suggested_start_sec": adj_start,
                                "suggested_end_sec": adj_end,
                                "metadata": updated_meta,
                            }
                        )
                    )
        else:
            selected_candidates = list(candidates)[: max(0, max_items)]

        worker_results = await asyncio.gather(
            *(
                self._process_candidate(
                    src,
                    include_campaign=include_campaign,
                    demo_video_fallback=demo_video_fallback,
                )
                for src in selected_candidates
            )
        )

        all_items = approved_items + list(worker_results)

        rendered_count = sum(1 for item in all_items if item.get("status") == "published")
        published_posts = sum(len(item.get("publications", [])) for item in all_items)
        championship_entries = sum(len(item.get("championship_entries", [])) for item in all_items)
        pending_count = sum(1 for item in all_items if item.get("status") == "pending_human")
        rejected_count = sum(1 for item in all_items if item.get("status") == "rejected")
        errors = [item for item in all_items if item.get("status") == "error"]

        try:
            learning_summary = await self.analytics.run_learning_loop()
        except Exception as exc:
            logger.warning("Analytics learning loop warning: %s", exc)
            learning_summary = {}

        summary: dict[str, Any] = {
            "status": "completed",
            "paused": False,
            "dry_run": self.config.dry_run,
            "autopilot_mode": self.config.autopilot_mode,
            "discovered": len(selected_candidates),
            "rendered": rendered_count,
            "published_posts": published_posts,
            "championship_entries": championship_entries,
            "pending_approval": pending_count,
            "rejected": rejected_count,
            "errors": errors,
            "items": all_items,
            "campaign_status": campaign_status,
            "learning": learning_summary,
            "completed_at": utc_now().isoformat(),
        }

        await self.notifier.notify_cycle_summary(summary, learning_summary)
        return summary

    def _build_evergreen_x_candidate(self, pillar: ContentPillar) -> SourceCandidate:
        launch_raw = str(
            (self.config.settings or {}).get("page", {}).get("gta6_launch_date", "2026-11-19")
        )
        try:
            launch_dt = date.fromisoformat(launch_raw)
            days_diff = (launch_dt - datetime.now(timezone.utc).date()).days
        except ValueError:
            days_diff = 1

        countdown_note = (
            f"Faltam {days_diff} dia(s) para o lançamento oficial de GTA VI em Leonida"
            if days_diff > 0
            else f"Dia {abs(days_diff) + 1} explorando Vice City e Leonida em GTA VI"
        )
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

        return SourceCandidate(
            id=new_id("xsrc"),
            title=f"GTA VI Official Briefing — {pillar.value} ({stamp})",
            url=f"https://www.rockstargames.com/VI#{pillar.value}-{stamp}",
            creator_handle="@RockstarGames",
            source_type="official_newswire",
            is_official=True,
            is_campaign_acervo=False,
            pillar=pillar,
            summary=f"{countdown_note}. Detalhes oficiais verificados nos Trailers 1 e 2 da Rockstar Games.",
            verified_facts=[
                countdown_note,
                "GTA VI se passa no estado de Leonida, incluindo Vice City.",
                "Lucia e Jason protagonizam a campanha oficial apresentada pela Rockstar Games.",
            ],
            suggested_start_sec=0.0,
            suggested_end_sec=20.0,
            virality_score=82.0,
        )

    async def run_x_text_slot(self, pillar: ContentPillar) -> PublicationRecord | None:
        """Fast text/image post pipeline for the 6 daily X (Twitter) slots."""
        self.store.set_flag("last_heartbeat_at", utc_now().isoformat())

        if self.store.get_flag("paused", "false").strip().lower() == "true":
            logger.info("Orchestrator paused; skipping X slot for pillar=%s.", pillar.value)
            return None

        try:
            discovered = await self.scout.discover_candidates(
                limit=6,
                include_campaign_acervo=False,
            )
        except Exception as exc:
            logger.warning("Scout discovery fallback for X slot (%s): %s", pillar.value, exc)
            discovered = []

        matching = next((c for c in discovered if c.pillar == pillar), None)
        if matching is not None:
            candidate = matching
        elif discovered:
            candidate = discovered[0].model_copy(update={"pillar": pillar})
        else:
            candidate = self._build_evergreen_x_candidate(pillar)

        try:
            language = self._primary_language()
            plan = await self.copywriter.build_clip_plan(
                candidate,
                language=language,
                for_campaign=False,
            )
            plan.pillar = pillar
            plan.for_real_oficial_campaign = False
            plan.campaign_slug = None
            plan.target_platforms = [Platform.X]

            bundle = await self.copywriter.generate_captions(plan)

            handle_clean = plan.source.creator_handle.strip()
            if not handle_clean.startswith("@"):
                handle_clean = f"@{handle_clean}"
            credit_tag = f"🎥 {handle_clean}"

            x_copy = bundle.copies.get(Platform.X)
            if x_copy is None:
                base_caption = (
                    plan.commentary_overlay.strip()
                    or plan.hook_on_screen.strip()
                    or candidate.summary.strip()
                    or candidate.title.strip()
                )
                x_copy = PlatformCopy(
                    platform=Platform.X,
                    title=plan.hook_on_screen,
                    caption=base_caption,
                    hashtags=[],
                    credit_line=credit_tag,
                )
            else:
                x_copy.hashtags = []
                if "🎥 @" not in x_copy.formatted_text():
                    x_copy.credit_line = credit_tag

            x_only_bundle = CaptionBundle(
                plan_id=plan.id,
                copies={Platform.X: x_copy},
            )

            verdict = await self._evaluate_compliance(plan, x_only_bundle)

            if verdict.approval_status == ApprovalStatus.REJECTED or (
                not verdict.passed and verdict.approval_status != ApprovalStatus.PENDING_HUMAN
            ):
                logger.info(
                    "X slot plan %s rejected (%s): %s",
                    plan.id,
                    pillar.value,
                    "; ".join(verdict.reasons),
                )
                return None

            if (
                verdict.approval_status == ApprovalStatus.PENDING_HUMAN
                and self.config.autopilot_mode != "full_auto"
            ):
                self.store.save_pending_approval(
                    plan.id,
                    {
                        "hook": plan.hook_on_screen,
                        "title": candidate.title,
                        "reasons": verdict.reasons + verdict.warnings,
                        "plan": plan.model_dump(mode="json"),
                        "bundle": x_only_bundle.model_dump(mode="json"),
                        "verdict": verdict.model_dump(mode="json"),
                    },
                )
                await self.notifier.notify_pending_approval(plan, verdict, x_only_bundle)
                return None

            publications = await self.publisher.publish_all(plan, None, x_only_bundle)
            if publications:
                await self.notifier.notify_published(plan, publications, [])
                return publications[0]
            return None

        except Exception as exc:
            logger.exception("Failed running X slot for pillar=%s: %s", pillar.value, exc)
            await self.notifier.notify_error(f"X slot ({pillar.value})", exc)
            return None
