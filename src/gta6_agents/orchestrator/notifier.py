"""Mobile Remote Control & Alerting via Telegram Bot API and Discord Webhooks."""

from __future__ import annotations

import html
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from gta6_agents.config import AppConfig
from gta6_agents.models import (
    ApprovalStatus,
    CaptionBundle,
    ChampionshipEntry,
    ClipPlan,
    ComplianceVerdict,
    PublicationRecord,
)
from gta6_agents.storage import StateStore

logger = logging.getLogger(__name__)

DISCORD_COLORS: dict[str, int] = {
    "info": 0x00D4FF,
    "success": 0x00FF9D,
    "campaign": 0xFF2A85,
    "warning": 0xFFB800,
    "error": 0xFF3366,
}


class RemoteNotifier:
    """Sends rich mobile alerts (Telegram/Discord) and polls Telegram remote commands."""

    def __init__(self, config: AppConfig, store: StateStore) -> None:
        self.config = config
        self.store = store

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.config.telegram_bot_token.strip() and self.config.telegram_chat_id.strip())

    @property
    def discord_enabled(self) -> bool:
        return bool(self.config.discord_webhook_url.strip())

    def _telegram_api_url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.config.telegram_bot_token.strip()}/{method}"

    async def _send_telegram(
        self,
        html_text: str,
        plain_fallback: str | None = None,
        chat_id: str | None = None,
    ) -> bool:
        token = self.config.telegram_bot_token.strip()
        target_chat = (chat_id or self.config.telegram_chat_id).strip()
        if not token or not target_chat:
            return False

        url = self._telegram_api_url("sendMessage")
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.post(
                    url,
                    json={
                        "chat_id": target_chat,
                        "text": html_text[:4000],
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                )
                if resp.status_code == 200:
                    return True

                fallback = (plain_fallback or html_text)[:4000]
                resp2 = await client.post(
                    url,
                    json={
                        "chat_id": target_chat,
                        "text": fallback,
                        "disable_web_page_preview": True,
                    },
                )
                return resp2.status_code == 200
        except Exception as exc:
            logger.warning("Telegram sendMessage failed: %s", exc)
            return False

    async def _send_discord(
        self,
        title: str,
        description: str,
        level: str = "info",
    ) -> bool:
        webhook = self.config.discord_webhook_url.strip()
        if not webhook:
            return False

        payload = {
            "username": "GTA 6 & Real Oficial Autopilot",
            "embeds": [
                {
                    "title": title[:250],
                    "description": description[:3900],
                    "color": DISCORD_COLORS.get(level, DISCORD_COLORS["info"]),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "footer": {"text": f"Campaign: {self.config.real_oficial_campaign_slug} | Mode: {self.config.autopilot_mode}"},
                }
            ],
        }
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.post(webhook, json=payload)
                return 200 <= resp.status_code < 300
        except Exception as exc:
            logger.warning("Discord webhook send failed: %s", exc)
            return False

    async def send_alert(
        self,
        title: str,
        lines: list[str],
        level: str = "info",
    ) -> dict[str, bool]:
        """Broadcast a rich formatted alert to configured Telegram and Discord channels."""
        escaped_title = html.escape(title)
        escaped_lines = [html.escape(line) for line in lines]
        tg_html = f"<b>{escaped_title}</b>\n\n" + "\n".join(escaped_lines)
        plain = f"{title}\n\n" + "\n".join(lines)

        tg_ok = await self._send_telegram(tg_html, plain_fallback=plain)
        dc_ok = await self._send_discord(title=title, description="\n".join(lines), level=level)
        return {"telegram": tg_ok, "discord": dc_ok}

    async def notify_published(
        self,
        plan: ClipPlan,
        publications: list[PublicationRecord],
        entries: list[ChampionshipEntry] | None = None,
    ) -> dict[str, bool]:
        if not publications:
            return {"telegram": False, "discord": False}

        mode_label = "DRY-RUN" if self.config.dry_run else "LIVE"
        lines = [
            f"🎬 Hook: {plan.hook_on_screen}",
            f"📂 Pillar: {plan.pillar.value} | Source: {plan.source.creator_handle}",
        ]
        for pub in publications:
            link_part = f" -> {pub.public_url}" if pub.public_url else ""
            lines.append(f"• [{pub.platform.value}] status={pub.status}{link_part}")

        if entries:
            for entry in entries:
                lines.append(
                    f"🏆 Real Oficial ({entry.campaign_slug}): {entry.status} | {entry.youtube_url}"
                )

        return await self.send_alert(
            title=f"✅ GTA 6 Clip Published ({mode_label})",
            lines=lines,
            level="success",
        )

    async def notify_campaign_entry(
        self,
        entry: ChampionshipEntry,
        pub: PublicationRecord | None = None,
    ) -> dict[str, bool]:
        lines = [
            f"🏆 Campaign: {entry.campaign_slug}",
            f"📺 YouTube Short: {entry.youtube_url}",
            f"📊 Entry Status: {entry.status}",
            f"💰 Est. CPM: R$ {entry.estimated_cpm_brl:.2f} / 1k views",
        ]
        if entry.notes:
            lines.append(f"📝 Notes: {entry.notes}")
        if pub and pub.id:
            lines.append(f"🔗 Publication ID: {pub.id}")

        return await self.send_alert(
            title=f"🚀 Entered in Real Oficial ({entry.campaign_slug})",
            lines=lines,
            level="campaign",
        )

    async def notify_pending_approval(
        self,
        plan: ClipPlan,
        verdict: ComplianceVerdict,
        bundle: CaptionBundle | None = None,
    ) -> dict[str, bool]:
        reasons = verdict.reasons + verdict.warnings
        reason_str = "; ".join(reasons) if reasons else "Requires human confirmation under semi_auto rules"
        lines = [
            f"🆔 Plan ID: {plan.id}",
            f"🎬 Hook: {plan.hook_on_screen}",
            f"📰 Source: {plan.source.title} ({plan.source.creator_handle})",
            f"🔗 URL: {plan.source.url}",
            f"⚠️ Reason: {reason_str}",
        ]
        if bundle and bundle.copies:
            first_copy = next(iter(bundle.copies.values()))
            lines.append(f"📝 Preview: {first_copy.caption[:180]}")

        lines.extend(
            [
                "",
                "📱 Reply on Telegram to decide from anywhere:",
                f"  /approve {plan.id}",
                f"  /reject {plan.id}",
            ]
        )
        return await self.send_alert(
            title="⏳ Approval Requested (GTA 6 Autopilot)",
            lines=lines,
            level="warning",
        )

    async def notify_error(self, context: str, error: Exception | str) -> dict[str, bool]:
        err_text = str(error)
        lines = [
            f"📍 Context: {context}",
            f"❌ Error: {err_text[:600]}",
            "",
            "📱 Mobile commands: /status | /pause | /resume",
        ]
        return await self.send_alert(
            title="🚨 Autopilot Error Alert (2nd PC Watchdog)",
            lines=lines,
            level="error",
        )

    async def notify_cycle_summary(
        self,
        cycle_stats: dict[str, Any],
        learning_summary: dict[str, Any] | None = None,
    ) -> dict[str, bool]:
        lines = [
            f"🔎 Discovered: {cycle_stats.get('discovered', 0)} | Rendered: {cycle_stats.get('rendered', 0)}",
            f"📤 Publications: {cycle_stats.get('published_posts', 0)} | 🏆 Campaign Entries: {cycle_stats.get('championship_entries', 0)}",
            f"⏳ Pending Approval: {cycle_stats.get('pending_approval', 0)} | 🚫 Rejected: {cycle_stats.get('rejected', 0)}",
        ]
        if learning_summary:
            views = learning_summary.get("validated_views", 0)
            earnings = learning_summary.get("estimated_earnings_brl", 0.0)
            lines.append(
                f"💰 Total Validated Views: {views:,} | Est. Earnings: R$ {earnings:.2f}"
            )
            recs = learning_summary.get("recommendations") or []
            if recs:
                lines.append(f"💡 Insight: {recs[0]}")

        return await self.send_alert(
            title="📊 GTA 6 Cycle Summary",
            lines=lines,
            level="info",
        )

    def _build_status_report_lines(self) -> list[str]:
        paused = self.store.get_flag("paused", "false").strip().lower() == "true"
        heartbeat = self.store.get_flag("last_heartbeat_at", "just now")
        pubs = self.store.list_publications(limit=500)
        entries = self.store.list_championship_entries(limit=500)
        pending = self.store.list_pending_approvals()
        credits_today = self.store.today_credit_spend("real_oficial")

        total_views = sum(int(e.get("validated_views") or 0) for e in entries)
        est_brl = round(
            sum(
                (int(e.get("validated_views") or 0) / 1000.0)
                * float(e.get("estimated_cpm_brl") or 1.25)
                for e in entries
            ),
            2,
        )

        lines = [
            f"⚡ State: {'⏸️ PAUSED' if paused else '🟢 ACTIVE'}",
            f"🛠️ Mode: {'DRY_RUN' if self.config.dry_run else 'LIVE'} | Autopilot: {self.config.autopilot_mode}",
            f"💓 Last Heartbeat: {heartbeat}",
            f"📤 Publications: {len(pubs)} | 🏆 '{self.config.real_oficial_campaign_slug}': {len(entries)} entries",
            f"👀 Validated Views: {total_views:,} | 💰 Est. CPM: R$ {est_brl:.2f}",
            f"🪙 Credits Today: {credits_today}/{self.config.real_oficial_daily_credit_limit}",
            f"⏳ Pending Approvals: {len(pending)}",
        ]
        if pending:
            ids = ", ".join(str(p.get("plan_id")) for p in pending[:5])
            lines.append(f"📋 Queue IDs: {ids}")
        return lines

    async def process_command(
        self,
        raw_text: str,
        chat_id: str | None = None,
        send_reply: bool = True,
    ) -> str | None:
        """Execute a single Telegram slash command and optionally send reply."""
        text = (raw_text or "").strip()
        if not text.startswith("/"):
            return None

        parts = text.split()
        cmd_token = parts[0].split("@", 1)[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        reply_lines: list[str] = []
        canonical_cmd: str | None = None

        if cmd_token == "/status":
            canonical_cmd = "/status"
            reply_lines = ["📡 GTA 6 & Real Oficial — Remote Status", ""] + self._build_status_report_lines()

        elif cmd_token == "/pause":
            self.store.set_flag("paused", "true")
            canonical_cmd = "/pause"
            reply_lines = [
                "⏸️ Autopilot PAUSED (paused=true).",
                "Scheduled publishing is on hold. Send /resume anytime to continue.",
            ]

        elif cmd_token == "/resume":
            self.store.set_flag("paused", "false")
            canonical_cmd = "/resume"
            reply_lines = [
                "▶️ Autopilot RESUMED (paused=false).",
                "24/7 GTA 6 & Real Oficial schedule is active!",
            ]

        elif cmd_token == "/approve":
            if not arg:
                pending = self.store.list_pending_approvals()
                ids = ", ".join(p["plan_id"] for p in pending[:5]) or "none"
                reply_lines = [f"⚠️ Usage: /approve <plan_id> (Pending: {ids})"]
                canonical_cmd = "/approve"
            else:
                updated = self.store.update_approval_status(arg, ApprovalStatus.APPROVED.value)
                canonical_cmd = f"/approve {arg}"
                if updated:
                    reply_lines = [
                        f"✅ Plan '{arg}' marked APPROVED.",
                        "It will be rendered & published automatically in the next cycle.",
                    ]
                else:
                    reply_lines = [f"⚠️ Plan ID '{arg}' was not found in pending_approvals."]

        elif cmd_token == "/reject":
            if not arg:
                pending = self.store.list_pending_approvals()
                ids = ", ".join(p["plan_id"] for p in pending[:5]) or "none"
                reply_lines = [f"⚠️ Usage: /reject <plan_id> (Pending: {ids})"]
                canonical_cmd = "/reject"
            else:
                updated = self.store.update_approval_status(arg, ApprovalStatus.REJECTED.value)
                canonical_cmd = f"/reject {arg}"
                if updated:
                    reply_lines = [f"🚫 Plan '{arg}' marked REJECTED and skipped."]
                else:
                    reply_lines = [f"⚠️ Plan ID '{arg}' was not found in pending_approvals."]

        elif cmd_token in ("/help", "/start"):
            canonical_cmd = cmd_token
            reply_lines = [
                "🎮 GTA 6 & Real Oficial — Mobile Remote Control",
                "/status — Full pipeline, credits & championship status",
                "/pause — Pause autonomous posting",
                "/resume — Resume autonomous posting",
                "/approve <plan_id> — Approve queued clip",
                "/reject <plan_id> — Reject queued clip",
            ]

        if canonical_cmd and send_reply and reply_lines:
            plain = "\n".join(reply_lines)
            escaped = "\n".join(html.escape(line) for line in reply_lines)
            await self._send_telegram(escaped, plain_fallback=plain, chat_id=chat_id)

        return canonical_cmd

    async def poll_telegram_commands_once(self) -> list[str]:
        """Poll Telegram getUpdates once and execute authorized remote commands."""
        token = self.config.telegram_bot_token.strip()
        if not token:
            return []

        last_offset_raw = self.store.get_flag("telegram_last_update_id", "0")
        try:
            last_offset = int(last_offset_raw or "0")
        except ValueError:
            last_offset = 0

        url = self._telegram_api_url("getUpdates")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    url,
                    params={
                        "offset": last_offset + 1,
                        "timeout": 1,
                        "allowed_updates": '["message"]',
                    },
                )
                if resp.status_code != 200:
                    logger.debug("Telegram getUpdates returned HTTP %s", resp.status_code)
                    return []
                payload = resp.json()
        except Exception as exc:
            logger.warning("Telegram polling error: %s", exc)
            return []

        if not isinstance(payload, dict) or not payload.get("ok"):
            return []

        updates = payload.get("result") or []
        handled: list[str] = []
        max_update_id = last_offset
        allowed_chat = self.config.telegram_chat_id.strip()

        for upd in updates:
            if not isinstance(upd, dict):
                continue
            upd_id = int(upd.get("update_id", 0))
            if upd_id > max_update_id:
                max_update_id = upd_id

            msg = upd.get("message") or {}
            chat = msg.get("chat") or {}
            sender_chat_id = str(chat.get("id", "")).strip()
            if allowed_chat and sender_chat_id != allowed_chat:
                logger.warning("Ignoring Telegram command from unauthorized chat_id=%s", sender_chat_id)
                continue

            text = str(msg.get("text") or "").strip()
            if not text:
                continue

            cmd_result = await self.process_command(text, chat_id=sender_chat_id, send_reply=True)
            if cmd_result:
                handled.append(cmd_result)

        if max_update_id > last_offset:
            self.store.set_flag("telegram_last_update_id", str(max_update_id))

        return handled
