"""FastAPI Mission Control Web Dashboard for GTA 6 & Real Oficial Multi-Agent System."""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse

from gta6_agents.config import AppConfig, load_config
from gta6_agents.models import ApprovalStatus
from gta6_agents.storage import StateStore

DEFAULT_CAMPAIGN_URL = (
    "https://app.realoficial.com.br/c/gta-vi-youtube"
    "?ro_vid=a56ec50b-725e-4c73-a353-4e3b2e80678b"
)
AUTONOMY_TARGET_HOURS = 120.0  # 5 days (2nd PC unattended autonomy window)


def _is_paused(store: StateStore) -> bool:
    return store.get_flag("paused", "false").strip().lower() in ("1", "true", "yes", "on")


def _ensure_autonomy_timer(store: StateStore) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    started_raw = store.get_flag("autonomy_started_at", "")
    if not started_raw:
        started_raw = now.isoformat()
        store.set_flag("autonomy_started_at", started_raw)
    try:
        started_dt = datetime.fromisoformat(started_raw)
        if started_dt.tzinfo is None:
            started_dt = started_dt.replace(tzinfo=timezone.utc)
    except ValueError:
        started_dt = now
        started_raw = now.isoformat()
        store.set_flag("autonomy_started_at", started_raw)

    elapsed_hours = max(0.0, (now - started_dt).total_seconds() / 3600.0)
    remaining_hours = max(0.0, AUTONOMY_TARGET_HOURS - elapsed_hours)
    progress_pct = min(100.0, round((elapsed_hours / AUTONOMY_TARGET_HOURS) * 100.0, 1))
    day_index = min(5, int(elapsed_hours // 24) + 1)

    rem_days = int(remaining_hours // 24)
    rem_hrs = int(remaining_hours % 24)
    rem_mins = int((remaining_hours * 60) % 60)

    return {
        "started_at": started_raw,
        "target_hours": AUTONOMY_TARGET_HOURS,
        "elapsed_hours": round(elapsed_hours, 2),
        "remaining_hours": round(remaining_hours, 2),
        "progress_pct": progress_pct,
        "current_day": day_index,
        "formatted_remaining": f"{rem_days}d {rem_hrs:02d}h {rem_mins:02d}m",
    }


def _infer_pillar(pub: dict[str, Any]) -> str:
    if pub.get("campaign_slug"):
        return "real_oficial_campaign"
    caption = (pub.get("caption_text") or "").lower()
    if "rockstar" in caption or "newswire" in caption or "trailer" in caption:
        return "official_news"
    if "dica" in caption or "segredo" in caption or "detalhe" in caption or "tip" in caption:
        return "tips_tutorials"
    if "gta 5" in caption or "gta v" in caption or "nostalgia" in caption:
        return "gta5_nostalgia"
    if "contagem" in caption or "faltam" in caption or "countdown" in caption:
        return "countdown_recap"
    return "gameplay_clips"


def _build_agents_swarm(
    cfg: AppConfig,
    paused: bool,
    publications_count: int,
    entries_count: int,
    pending_count: int,
    credit_today: int,
) -> list[dict[str, Any]]:
    base_state = "PAUSED" if paused else "ONLINE"
    return [
        {
            "id": "scout_agent",
            "name": "01. Scout & Trend Hunter",
            "module": "ScoutAgent",
            "state": base_state,
            "detail": "Rockstar Newswire, YouTube, Reddit & Acervo Real Oficial",
            "metric": "Deduplicação SQLite ativa",
        },
        {
            "id": "compliance_agent",
            "name": "02. Compliance & Fact-Checker",
            "module": "ComplianceFactAgent",
            "state": "GUARD_ACTIVE" if not paused else "PAUSED",
            "detail": "Bloqueio anti-leak/rumor, anti-injection & crédito @creator",
            "metric": f"{pending_count} pendente(s) revisão",
        },
        {
            "id": "copywriter_agent",
            "name": "03. Script & Copywriter",
            "module": "ScriptCopyAgent",
            "state": base_state,
            "detail": f"Hooks 0-2s PT-BR/EN ({cfg.llm_provider}:{cfg.llm_model})",
            "metric": "Zero hashtags no X | <=5 Reels",
        },
        {
            "id": "clipper_agent",
            "name": "04. Clipper & FFmpeg Editor",
            "module": "ClipperEditorAgent",
            "state": base_state,
            "detail": "Render 9:16 (1080x1920), Blur-Pad HUD, -14 LUFS & .ass",
            "metric": "H.264 + AAC 30fps",
        },
        {
            "id": "voiceover_agent",
            "name": "05. Voiceover & Subtitles",
            "module": "synthesize_voiceover + build_ass_subtitles",
            "state": base_state,
            "detail": f"Modo {cfg.voiceover_mode} ({cfg.edge_tts_voice_pt})",
            "metric": "100% gratuito / low-latency",
        },
        {
            "id": "publisher_agent",
            "name": "06. Multi-Platform Publisher",
            "module": "PublisherAgent",
            "state": "DRY_RUN" if cfg.dry_run else base_state,
            "detail": "YouTube Shorts API v3, X v2, Reels, TikTok & Buffer",
            "metric": f"{publications_count} post(s) registrados",
        },
        {
            "id": "real_oficial_agent",
            "name": "07. Real Oficial Championship",
            "module": "RealOficialAgent",
            "state": base_state if cfg.real_oficial_enabled else "DISABLED",
            "detail": f"Campanha #{cfg.real_oficial_campaign_slug} & trava de créditos",
            "metric": f"{entries_count} Shorts | {credit_today}/{cfg.real_oficial_daily_credit_limit} cr",
        },
        {
            "id": "analytics_orchestrator_agent",
            "name": "08. Analytics, Orchestrator & 2PC",
            "module": "AnalyticsLearningAgent + MultiAgentOrchestrator",
            "state": base_state,
            "detail": f"Autopilot '{cfg.autopilot_mode}' ({cfg.timezone}) + Bot Telegram",
            "metric": "Janela autônoma 5 dias",
        },
    ]


def _collect_status_payload(cfg: AppConfig, store: StateStore) -> dict[str, Any]:
    paused = _is_paused(store)
    autonomy = _ensure_autonomy_timer(store)

    raw_pubs = store.list_publications(limit=50)
    publications: list[dict[str, Any]] = []
    today_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    shorts_today = 0
    for pub in raw_pubs:
        enriched = dict(pub)
        enriched["pillar"] = _infer_pillar(enriched)
        publications.append(enriched)
        if (
            enriched.get("platform") in ("instagram_reels", "tiktok", "youtube_shorts")
            and str(enriched.get("created_at", "")).startswith(today_prefix)
        ):
            shorts_today += 1

    campaign_cfg = cfg.campaign.get("campaign", {}) if isinstance(cfg.campaign, dict) else {}
    rewards_cfg = campaign_cfg.get("rewards", {}) if isinstance(campaign_cfg, dict) else {}
    rules_cfg = campaign_cfg.get("rules", {}) if isinstance(campaign_cfg, dict) else {}

    cpm_brl = float(rewards_cfg.get("cpm_brl_per_1k_views", 4.00))
    min_views_threshold = int(rewards_cfg.get("min_views_threshold_per_video", 5000))
    max_payout_per_video = float(rewards_cfg.get("max_payout_brl_per_video", 400.00))

    entries = store.list_championship_entries(limit=50)
    validated_views_total = sum(int(e.get("validated_views") or 0) for e in entries)
    estimated_earnings_brl = round(
        sum(
            min(
                max_payout_per_video,
                (int(e.get("validated_views") or 0) / 1000.0)
                * float(e.get("estimated_cpm_brl") or cpm_brl),
            )
            if int(e.get("validated_views") or 0) >= min_views_threshold
            else 0.0
            for e in entries
        ),
        2,
    )
    default_cpm = float(entries[0].get("estimated_cpm_brl", cpm_brl)) if entries else cpm_brl
    projected_50k_brl = round(min(max_payout_per_video, 50.0 * default_cpm), 2)

    raw_approvals = store.list_pending_approvals()
    pending_approvals: list[dict[str, Any]] = []
    for item in raw_approvals:
        parsed_payload: dict[str, Any] = {}
        raw_json = item.get("payload_json", "{}")
        if isinstance(raw_json, str):
            try:
                parsed_payload = json.loads(raw_json)
            except json.JSONDecodeError:
                parsed_payload = {"raw": raw_json}
        pending_approvals.append(
            {
                "plan_id": item["plan_id"],
                "status": item["status"],
                "created_at": item["created_at"],
                "payload": parsed_payload,
            }
        )

    credit_today = store.today_credit_spend("real_oficial")
    credit_limit = max(1, int(cfg.real_oficial_daily_credit_limit))
    credit_pct = min(100.0, round((credit_today / credit_limit) * 100.0, 1))

    campaign_url = campaign_cfg.get("campaign_url") or (
        f"https://app.realoficial.com.br/c/{cfg.real_oficial_campaign_slug}"
        f"?ro_vid={cfg.real_oficial_ref_vid}"
    ) or DEFAULT_CAMPAIGN_URL

    multi_channel_summary: dict[str, Any] = {}
    daily_shorts_target = 60
    try:
        from gta6_agents.orchestrator.multi_channel import MultiChannelCoordinator

        mc = MultiChannelCoordinator(cfg, store)
        multi_channel_summary = mc.get_daily_quota_summary()
        daily_shorts_target = int(mc.global_scaling.total_daily_videos or 60)
    except Exception:
        pass

    tri_engine_summary: dict[str, Any] = {}
    try:
        from gta6_agents.orchestrator.tri_engine_pool import TriEnginePool

        pool = TriEnginePool(cfg, store)
        tri_engine_summary = pool.get_pool_status()
    except Exception:
        pass

    preferred_times = ["19:00-00:00 (Horário Nobre BR)", "22:30-22:57 (Turbo Skorpion)"]
    required_hashtags = rules_cfg.get("required_hashtags", ["#launchdayonYT"])

    agents = _build_agents_swarm(
        cfg=cfg,
        paused=paused,
        publications_count=len(publications),
        entries_count=len(entries),
        pending_count=len(pending_approvals),
        credit_today=credit_today,
    )

    return {
        "status": "paused" if paused else "active",
        "paused": paused,
        "dry_run": cfg.dry_run,
        "execution_mode": "DRY_RUN" if cfg.dry_run else "LIVE",
        "autopilot_mode": cfg.autopilot_mode,
        "timezone": cfg.timezone,
        "autonomy_timer": autonomy,
        "metrics": {
            "total_publications": len(publications),
            "shorts_today": shorts_today,
            "daily_shorts_target": daily_shorts_target,
            "championship_entries_count": len(entries),
            "pending_approvals_count": len(pending_approvals),
            "validated_views_total": validated_views_total,
            "estimated_earnings_brl": estimated_earnings_brl,
            "projected_50k_views_brl": projected_50k_brl,
            "credit_spend_today": credit_today,
            "daily_credit_limit": credit_limit,
        },
        "credit_spend": {
            "provider": "real_oficial",
            "today": credit_today,
            "daily_limit": credit_limit,
            "remaining": max(0, credit_limit - credit_today),
            "usage_pct": credit_pct,
            "allow_spend_credits": cfg.real_oficial_allow_spend_credits,
        },
        "campaign": {
            "enabled": cfg.real_oficial_enabled,
            "slug": cfg.real_oficial_campaign_slug,
            "campaign_url": campaign_url,
            "authorized_live_url": "https://www.youtube.com/watch?v=KY4DWumZffg",
            "valid_platforms": campaign_cfg.get("valid_platforms", ["instagram_reels", "tiktok"]),
            "daily_shorts_target": daily_shorts_target,
            "shorts_today": shorts_today,
            "preferred_times_brt": preferred_times,
            "required_hashtags": required_hashtags,
            "validated_views_total": validated_views_total,
            "estimated_earnings_brl": estimated_earnings_brl,
            "projected_50k_views_brl": projected_50k_brl,
        },
        "multi_channel": multi_channel_summary,
        "tri_engine_pool": tri_engine_summary,
        "agents": agents,
        "publications": publications,
        "championship_entries": entries,
        "pending_approvals": pending_approvals,
    }


def _render_dashboard_html(state: dict[str, Any]) -> str:
    exec_mode = state["execution_mode"]
    exec_badge_cls = "badge-warn" if state["dry_run"] else "badge-live"
    sys_state_label = "PAUSED" if state["paused"] else "ACTIVE"
    sys_badge_cls = "badge-paused" if state["paused"] else "badge-active"

    autonomy = state["autonomy_timer"]
    campaign = state["campaign"]
    credit = state["credit_spend"]
    metrics = state["metrics"]

    agents_html = "".join(
        f"""
        <div class="agent-card">
          <div class="agent-top">
            <span class="agent-name">{html.escape(ag["name"])}</span>
            <span class="agent-pill pill-{html.escape(ag["state"].lower())}">{html.escape(ag["state"])}</span>
          </div>
          <div class="agent-mod">{html.escape(ag["module"])}</div>
          <p class="agent-detail">{html.escape(ag["detail"])}</p>
          <div class="agent-metric">{html.escape(ag["metric"])}</div>
        </div>
        """
        for ag in state["agents"]
    )

    if state["championship_entries"]:
        entries_rows = "".join(
            f"""
            <tr>
              <td><code>{html.escape(str(e.get("id", ""))[:14])}</code></td>
              <td><a href="{html.escape(str(e.get("youtube_url", "#")))}" target="_blank" rel="noopener">{html.escape(str(e.get("youtube_url", "")))}</a></td>
              <td><span class="status-tag">{html.escape(str(e.get("status", "")))}</span></td>
              <td>{int(e.get("validated_views") or 0):,}</td>
              <td>R$ {((int(e.get("validated_views") or 0) / 1000.0) * float(e.get("estimated_cpm_brl") or 1.25)):.2f} <small>(CPM R$ {float(e.get("estimated_cpm_brl") or 1.25):.2f})</small></td>
            </tr>
            """
            for e in state["championship_entries"]
        )
    else:
        entries_rows = (
            '<tr><td colspan="5" class="empty-row">'
            "Nenhuma submissão ainda — clique em <strong>Rodar Ciclo Demo Agora</strong> para testar o fluxo ponta a ponta."
            "</td></tr>"
        )

    if state["publications"]:
        pub_rows = "".join(
            f"""
            <tr>
              <td><span class="platform-chip">{html.escape(str(p.get("platform", "")))}</span></td>
              <td><code>{html.escape(str(p.get("pillar", "")))}</code></td>
              <td class="caption-cell">{html.escape(str(p.get("caption_text", ""))[:130])}</td>
              <td><span class="status-tag">{html.escape(str(p.get("status", "")))}</span></td>
              <td>{f'<a href="{html.escape(str(p["public_url"]))}" target="_blank" rel="noopener">Abrir Link</a>' if p.get("public_url") else '<span class="muted">local/dry-run</span>'}</td>
            </tr>
            """
            for p in state["publications"]
        )
    else:
        pub_rows = (
            '<tr><td colspan="5" class="empty-row">'
            "Nenhuma publicação registrada ainda."
            "</td></tr>"
        )

    if state["pending_approvals"]:
        approvals_html = "".join(
            f"""
            <div class="approval-item" id="approval-{html.escape(ap["plan_id"])}">
              <div class="approval-info">
                <strong>Plano <code>{html.escape(ap["plan_id"])}</code></strong>
                <span class="muted"> • {html.escape(str(ap.get("created_at", ""))[:19])}</span>
                <div class="approval-preview">{html.escape(json.dumps(ap.get("payload", {}), ensure_ascii=False)[:220])}</div>
              </div>
              <div class="approval-actions">
                <button class="btn btn-approve" onclick="decideApproval('{html.escape(ap["plan_id"])}', 'approved')">Aprovar</button>
                <button class="btn btn-reject" onclick="decideApproval('{html.escape(ap["plan_id"])}', 'rejected')">Rejeitar</button>
              </div>
            </div>
            """
            for ap in state["pending_approvals"]
        )
    else:
        approvals_html = (
            '<div class="empty-box">'
            "Fila limpa! Nenhum clipe aguardando aprovação humana (Aprovar / Rejeitar disponíveis quando acionado pelo Compliance)."
            "</div>"
        )

    hashtags_str = " ".join(str(t) for t in campaign["required_hashtags"])
    slots_str = ", ".join(str(s) for s in campaign["preferred_times_brt"])
    spend_lock_str = (
        "LIBERADO (Até limite diário)"
        if credit["allow_spend_credits"]
        else "TRAVADO (0 gasto sem permissão)"
    )

    initial_json = json.dumps(state, ensure_ascii=False).replace("</", "<\\/")

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>GTA 6 & Real Oficial — Mission Control (Vice City Neon)</title>
  <style>
    :root {{
      --bg-deep: #080713;
      --bg-panel: #121026;
      --bg-card: #191636;
      --border-neon: rgba(255, 46, 147, 0.32);
      --border-cyan: rgba(0, 240, 255, 0.28);
      --vice-pink: #ff2e93;
      --vice-orange: #ff8a00;
      --vice-cyan: #00f0ff;
      --vice-green: #00e676;
      --vice-amber: #ffca28;
      --vice-red: #ff4d6d;
      --text-main: #f5f3ff;
      --text-muted: #a5a1c8;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif;
      background:
        radial-gradient(circle at 12% 0%, rgba(255, 46, 147, 0.18), transparent 40%),
        radial-gradient(circle at 88% 8%, rgba(0, 240, 255, 0.14), transparent 38%),
        var(--bg-deep);
      color: var(--text-main);
      min-height: 100vh;
      padding: 24px;
      line-height: 1.5;
    }}
    .container {{ max-width: 1380px; margin: 0 auto; display: flex; flex-direction: column; gap: 22px; }}
    a {{ color: var(--vice-cyan); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    code {{
      font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, monospace;
      font-size: 0.84rem;
      background: rgba(255, 255, 255, 0.07);
      padding: 2px 6px;
      border-radius: 5px;
      color: #ffd1ec;
    }}
    .hero {{
      background: linear-gradient(135deg, rgba(25, 22, 54, 0.95), rgba(18, 16, 38, 0.95));
      border: 1px solid var(--border-neon);
      box-shadow: 0 0 32px rgba(255, 46, 147, 0.14);
      border-radius: 16px;
      padding: 22px 26px;
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 18px;
    }}
    .brand-title {{
      font-size: 1.55rem;
      font-weight: 800;
      letter-spacing: 0.02em;
      background: linear-gradient(90deg, var(--vice-pink), var(--vice-orange), var(--vice-cyan));
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }}
    .brand-sub {{ color: var(--text-muted); font-size: 0.92rem; margin-top: 4px; }}
    .status-strip {{ display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-top: 12px; }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 0.78rem;
      font-weight: 700;
      letter-spacing: 0.06em;
      padding: 5px 12px;
      border-radius: 999px;
      text-transform: uppercase;
    }}
    .badge-warn {{ background: rgba(255, 202, 40, 0.16); color: var(--vice-amber); border: 1px solid rgba(255, 202, 40, 0.45); }}
    .badge-live {{ background: rgba(0, 230, 118, 0.16); color: var(--vice-green); border: 1px solid rgba(0, 230, 118, 0.45); }}
    .badge-active {{ background: rgba(0, 240, 255, 0.14); color: var(--vice-cyan); border: 1px solid rgba(0, 240, 255, 0.45); }}
    .badge-paused {{ background: rgba(255, 77, 109, 0.18); color: var(--vice-red); border: 1px solid rgba(255, 77, 109, 0.45); }}
    .badge-timer {{ background: rgba(255, 46, 147, 0.15); color: #ff94ca; border: 1px solid rgba(255, 46, 147, 0.4); }}
    .controls {{ display: flex; flex-wrap: wrap; gap: 10px; }}
    .btn {{
      cursor: pointer;
      border: none;
      border-radius: 10px;
      padding: 10px 16px;
      font-weight: 700;
      font-size: 0.88rem;
      transition: transform 0.12s ease, opacity 0.15s ease, box-shadow 0.15s ease;
      color: #fff;
    }}
    .btn:hover {{ transform: translateY(-1px); opacity: 0.95; }}
    .btn:disabled {{ opacity: 0.5; cursor: wait; }}
    .btn-demo {{
      background: linear-gradient(90deg, var(--vice-pink), var(--vice-orange));
      box-shadow: 0 4px 18px rgba(255, 46, 147, 0.35);
    }}
    .btn-pause {{ background: rgba(255, 77, 109, 0.22); border: 1px solid var(--vice-red); color: #ff9ebb; }}
    .btn-resume {{ background: rgba(0, 230, 118, 0.2); border: 1px solid var(--vice-green); color: #8affc1; }}
    .btn-approve {{ background: rgba(0, 230, 118, 0.22); border: 1px solid var(--vice-green); color: #9effca; padding: 7px 13px; }}
    .btn-reject {{ background: rgba(255, 77, 109, 0.22); border: 1px solid var(--vice-red); color: #ff9ebb; padding: 7px 13px; }}
    .toast {{
      display: none;
      padding: 12px 18px;
      border-radius: 10px;
      background: rgba(0, 240, 255, 0.14);
      border: 1px solid var(--vice-cyan);
      color: var(--text-main);
      font-size: 0.9rem;
    }}
    .section {{
      background: var(--bg-panel);
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 16px;
      padding: 20px 24px;
    }}
    .section-ro {{
      border: 1px solid var(--border-cyan);
      box-shadow: 0 0 28px rgba(0, 240, 255, 0.08);
    }}
    .section-header {{
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      margin-bottom: 16px;
    }}
    .section-title {{ font-size: 1.15rem; font-weight: 700; color: #fff; }}
    .kpi-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
      gap: 14px;
      margin-bottom: 18px;
    }}
    .kpi-card {{
      background: var(--bg-card);
      border: 1px solid rgba(255, 255, 255, 0.07);
      border-radius: 12px;
      padding: 14px 16px;
    }}
    .kpi-label {{ font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); }}
    .kpi-val {{ font-size: 1.45rem; font-weight: 800; margin-top: 4px; color: #fff; }}
    .kpi-sub {{ font-size: 0.8rem; color: var(--text-muted); margin-top: 3px; }}
    .meter-track {{
      width: 100%;
      height: 10px;
      background: rgba(255, 255, 255, 0.08);
      border-radius: 999px;
      overflow: hidden;
      margin-top: 8px;
    }}
    .meter-fill {{
      height: 100%;
      background: linear-gradient(90deg, var(--vice-cyan), var(--vice-pink));
      transition: width 0.3s ease;
    }}
    .agents-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(275px, 1fr));
      gap: 14px;
    }}
    .agent-card {{
      background: var(--bg-card);
      border: 1px solid rgba(255, 46, 147, 0.2);
      border-radius: 12px;
      padding: 14px 16px;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }}
    .agent-top {{ display: flex; justify-content: space-between; align-items: center; gap: 8px; }}
    .agent-name {{ font-weight: 700; font-size: 0.94rem; color: #fff; }}
    .agent-mod {{ font-size: 0.78rem; color: var(--vice-cyan); font-family: monospace; }}
    .agent-detail {{ font-size: 0.83rem; color: var(--text-muted); flex: 1; }}
    .agent-metric {{ font-size: 0.79rem; color: #ffd1ec; padding-top: 6px; border-top: 1px solid rgba(255, 255, 255, 0.06); }}
    .agent-pill {{
      font-size: 0.7rem;
      font-weight: 700;
      padding: 2px 8px;
      border-radius: 999px;
      background: rgba(0, 230, 118, 0.16);
      color: var(--vice-green);
    }}
    .pill-paused, .pill-disabled {{ background: rgba(255, 77, 109, 0.2); color: var(--vice-red); }}
    .pill-dry_run {{ background: rgba(255, 202, 40, 0.18); color: var(--vice-amber); }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.88rem; }}
    th, td {{ text-align: left; padding: 11px 12px; border-bottom: 1px solid rgba(255, 255, 255, 0.07); }}
    th {{ color: var(--text-muted); font-size: 0.76rem; text-transform: uppercase; letter-spacing: 0.05em; }}
    .caption-cell {{ max-width: 440px; color: #e2defc; }}
    .platform-chip {{
      display: inline-block;
      padding: 3px 9px;
      border-radius: 6px;
      font-size: 0.76rem;
      font-weight: 700;
      background: rgba(255, 46, 147, 0.18);
      color: #ff94ca;
    }}
    .status-tag {{
      display: inline-block;
      padding: 3px 8px;
      border-radius: 6px;
      font-size: 0.75rem;
      background: rgba(0, 240, 255, 0.14);
      color: var(--vice-cyan);
    }}
    .empty-row, .empty-box {{ color: var(--text-muted); text-align: center; padding: 18px; font-size: 0.9rem; }}
    .approval-item {{
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      padding: 14px;
      border-radius: 10px;
      background: var(--bg-card);
      border: 1px solid rgba(255, 202, 40, 0.35);
      margin-bottom: 10px;
    }}
    .approval-preview {{ font-size: 0.82rem; color: var(--text-muted); margin-top: 4px; word-break: break-word; }}
    .approval-actions {{ display: flex; gap: 8px; }}
    .muted {{ color: var(--text-muted); font-size: 0.82rem; }}
    .table-wrap {{ overflow-x: auto; }}
  </style>
</head>
<body>
  <div class="container">
    <header class="hero">
      <div>
        <div class="brand-title">LEONIDA MISSION CONTROL // GTA 6 & REAL OFICIAL</div>
        <div class="brand-sub">Sistema Multi-Agente Autônomo 24/7 para Página Faceless GTA 6 + Campeonato Real Oficial Shorts</div>
        <div class="status-strip">
          <span id="exec-badge" class="badge {exec_badge_cls}">MODO: {html.escape(exec_mode)}</span>
          <span id="sys-badge" class="badge {sys_badge_cls}">SISTEMA: {html.escape(sys_state_label)}</span>
          <span id="timer-badge" class="badge badge-timer">
            2º PC AUTONOMIA 5 DIAS: Dia {autonomy["current_day"]}/5 • Restam {html.escape(autonomy["formatted_remaining"])} ({autonomy["progress_pct"]}%)
          </span>
          <span class="muted">Autopilot: <code>{html.escape(state["autopilot_mode"])}</code> • TZ: <code>{html.escape(state["timezone"])}</code></span>
        </div>
      </div>
      <div class="controls">
        <button id="btn-demo" class="btn btn-demo" onclick="triggerDemo()">Rodar Ciclo Demo Agora</button>
        <button id="btn-pause" class="btn btn-pause" onclick="togglePause(true)">Pausar Sistema</button>
        <button id="btn-resume" class="btn btn-resume" onclick="togglePause(false)">Retomar Sistema</button>
      </div>
    </header>

    <div id="toast" class="toast"></div>

    <!-- Real Oficial Championship Panel -->
    <section class="section section-ro">
      <div class="section-header">
        <div>
          <h2 class="section-title">🏆 Campeonato Real Oficial (<code>{html.escape(campaign["slug"])}</code>)</h2>
          <div class="muted">
            Link Oficial da Campanha:
            <a href="{html.escape(campaign["campaign_url"])}" target="_blank" rel="noopener">{html.escape(campaign["campaign_url"])}</a>
          </div>
        </div>
        <div class="muted">
          Hashtags Obrigatórias: <code>{html.escape(hashtags_str)}</code> • Horários BRT: <code>{html.escape(slots_str)}</code>
        </div>
      </div>

      <div class="kpi-grid">
        <div class="kpi-card">
          <div class="kpi-label">Meta Diária de Shorts</div>
          <div class="kpi-val" id="kpi-shorts">{campaign["shorts_today"]} / {campaign["daily_shorts_target"]}</div>
          <div class="kpi-sub">YouTube Shorts 9:16 (1080x1920)</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">Entradas no Campeonato</div>
          <div class="kpi-val" id="kpi-entries">{metrics["championship_entries_count"]}</div>
          <div class="kpi-sub">Views Validadas: <strong id="kpi-views">{metrics["validated_views_total"]:,}</strong></div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">Ganhos CPM Estimados</div>
          <div class="kpi-val" id="kpi-earnings">R$ {metrics["estimated_earnings_brl"]:.2f}</div>
          <div class="kpi-sub">Projeção 50k views: R$ {metrics["projected_50k_views_brl"]:.2f}</div>
        </div>
        <div class="kpi-card">
          <div class="kpi-label">Safeguard Diário de Créditos</div>
          <div class="kpi-val" id="kpi-credits">{credit["today"]} / {credit["daily_limit"]}</div>
          <div class="meter-track">
            <div id="credit-bar" class="meter-fill" style="width: {credit["usage_pct"]}%"></div>
          </div>
          <div class="kpi-sub">{html.escape(spend_lock_str)}</div>
        </div>
      </div>

      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>ID Entrada</th>
              <th>YouTube Shorts URL</th>
              <th>Status Real Oficial</th>
              <th>Views Validadas</th>
              <th>Ganho Estimado (CPM)</th>
            </tr>
          </thead>
          <tbody id="entries-tbody">
            {entries_rows}
          </tbody>
        </table>
      </div>
    </section>

    <!-- Swarm of 8 Agents -->
    <section class="section">
      <div class="section-header">
        <h2 class="section-title">🤖 Enxame Ativo — 8 Agentes Especializados</h2>
        <span class="muted">Arquitetura Modular Anti-Ban & Transformação com Valor Agregado</span>
      </div>
      <div class="agents-grid" id="agents-grid">
        {agents_html}
      </div>
    </section>

    <!-- Pending Human Approvals -->
    <section class="section">
      <div class="section-header">
        <h2 class="section-title">🛡️ Fila de Aprovação Humana (<span id="pending-count">{metrics["pending_approvals_count"]}</span>)</h2>
        <span class="muted">Itens retidos pelo ComplianceFactAgent ou Autopilot Semi-Auto</span>
      </div>
      <div id="approvals-list">
        {approvals_html}
      </div>
    </section>

    <!-- Recent Publications -->
    <section class="section">
      <div class="section-header">
        <h2 class="section-title">📡 Publicações Recentes (<span id="pubs-count">{metrics["total_publications"]}</span>)</h2>
        <span class="muted">Histórico SQLite + Ledger <code>memory/posted_history.jsonl</code></span>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Platform</th>
              <th>Pillar</th>
              <th>Caption</th>
              <th>Status</th>
              <th>URL</th>
            </tr>
          </thead>
          <tbody id="pubs-tbody">
            {pub_rows}
          </tbody>
        </table>
      </div>
    </section>
  </div>

  <script>
    window.__INITIAL_STATE__ = {initial_json};

    function showToast(msg) {{
      const el = document.getElementById('toast');
      el.textContent = msg;
      el.style.display = 'block';
      setTimeout(() => {{ el.style.display = 'none'; }}, 5000);
    }}

    function esc(s) {{
      const div = document.createElement('div');
      div.textContent = s == null ? '' : String(s);
      return div.innerHTML;
    }}

    async function refreshStatus() {{
      try {{
        const res = await fetch('/api/status');
        if (!res.ok) return;
        const st = await res.json();
        const sysBadge = document.getElementById('sys-badge');
        sysBadge.textContent = 'SISTEMA: ' + (st.paused ? 'PAUSED' : 'ACTIVE');
        sysBadge.className = 'badge ' + (st.paused ? 'badge-paused' : 'badge-active');

        document.getElementById('kpi-shorts').textContent = st.campaign.shorts_today + ' / ' + st.campaign.daily_shorts_target;
        document.getElementById('kpi-entries').textContent = st.metrics.championship_entries_count;
        document.getElementById('kpi-views').textContent = st.metrics.validated_views_total.toLocaleString('pt-BR');
        document.getElementById('kpi-earnings').textContent = 'R$ ' + Number(st.metrics.estimated_earnings_brl).toFixed(2);
        document.getElementById('kpi-credits').textContent = st.credit_spend.today + ' / ' + st.credit_spend.daily_limit;
        document.getElementById('credit-bar').style.width = st.credit_spend.usage_pct + '%';
        document.getElementById('pending-count').textContent = st.metrics.pending_approvals_count;
        document.getElementById('pubs-count').textContent = st.metrics.total_publications;

        if (st.championship_entries.length > 0) {{
          document.getElementById('entries-tbody').innerHTML = st.championship_entries.map(e => {{
            const views = Number(e.validated_views || 0);
            const cpm = Number(e.estimated_cpm_brl || 1.25);
            const earn = ((views / 1000) * cpm).toFixed(2);
            return `<tr>
              <td><code>${{esc(String(e.id || '').slice(0, 14))}}</code></td>
              <td><a href="${{esc(e.youtube_url)}}" target="_blank" rel="noopener">${{esc(e.youtube_url)}}</a></td>
              <td><span class="status-tag">${{esc(e.status)}}</span></td>
              <td>${{views.toLocaleString('pt-BR')}}</td>
              <td>R$ ${{earn}} <small>(CPM R$ ${{cpm.toFixed(2)}})</small></td>
            </tr>`;
          }}).join('');
        }}

        if (st.publications.length > 0) {{
          document.getElementById('pubs-tbody').innerHTML = st.publications.map(p => `<tr>
            <td><span class="platform-chip">${{esc(p.platform)}}</span></td>
            <td><code>${{esc(p.pillar)}}</code></td>
            <td class="caption-cell">${{esc(String(p.caption_text || '').slice(0, 130))}}</td>
            <td><span class="status-tag">${{esc(p.status)}}</span></td>
            <td>${{p.public_url ? `<a href="${{esc(p.public_url)}}" target="_blank" rel="noopener">Abrir Link</a>` : '<span class="muted">local/dry-run</span>'}}</td>
          </tr>`).join('');
        }}

        const appBox = document.getElementById('approvals-list');
        if (st.pending_approvals.length > 0) {{
          appBox.innerHTML = st.pending_approvals.map(ap => `<div class="approval-item">
            <div class="approval-info">
              <strong>Plano <code>${{esc(ap.plan_id)}}</code></strong>
              <span class="muted"> • ${{esc(String(ap.created_at || '').slice(0, 19))}}</span>
              <div class="approval-preview">${{esc(JSON.stringify(ap.payload || {{}}).slice(0, 220))}}</div>
            </div>
            <div class="approval-actions">
              <button class="btn btn-approve" onclick="decideApproval('${{esc(ap.plan_id)}}', 'approved')">Aprovar</button>
              <button class="btn btn-reject" onclick="decideApproval('${{esc(ap.plan_id)}}', 'rejected')">Rejeitar</button>
            </div>
          </div>`).join('');
        }} else {{
          appBox.innerHTML = '<div class="empty-box">Fila limpa! Nenhum clipe aguardando aprovação humana.</div>';
        }}
      }} catch (err) {{
        console.error('Erro ao atualizar status:', err);
      }}
    }}

    async function togglePause(pause) {{
      const endpoint = pause ? '/api/pause' : '/api/resume';
      const res = await fetch(endpoint, {{ method: 'POST' }});
      if (res.ok) {{
        showToast(pause ? '⏸️ Sistema pausado com segurança.' : '▶️ Sistema retomado e ativo!');
        await refreshStatus();
      }}
    }}

    async function decideApproval(planId, decision) {{
      const res = await fetch(`/api/approvals/${{encodeURIComponent(planId)}}/${{encodeURIComponent(decision)}}`, {{
        method: 'POST'
      }});
      if (res.ok) {{
        showToast(`Plano ${{planId}}: ${{decision === 'approved' ? 'APROVADO ✅' : 'REJEITADO ❌'}}`);
        await refreshStatus();
      }}
    }}

    async function triggerDemo() {{
      const btn = document.getElementById('btn-demo');
      btn.disabled = true;
      btn.textContent = 'Renderizando Ciclo Demo...';
      showToast('🚀 Executando ciclo demo completo (Scout -> Copy -> FFmpeg 9:16 -> Real Oficial)...');
      try {{
        const res = await fetch('/api/trigger-demo', {{ method: 'POST' }});
        const data = await res.json();
        if (res.ok) {{
          showToast('✅ Ciclo Demo concluído com sucesso!');
          await refreshStatus();
        }} else {{
          showToast('⚠️ Erro ao rodar ciclo demo: ' + (data.detail || res.statusText));
        }}
      }} finally {{
        btn.disabled = false;
        btn.textContent = 'Rodar Ciclo Demo Agora';
      }}
    }}

    setInterval(refreshStatus, 8000);
  </script>
</body>
</html>"""


def create_dashboard_app(config: AppConfig | None = None) -> FastAPI:
    """Create the FastAPI Mission Control application bound to config and SQLite StateStore."""
    cfg = config or load_config()
    store = StateStore(data_dir=cfg.data_dir, memory_dir=cfg.memory_dir)

    dashboard = FastAPI(
        title="GTA 6 & Real Oficial — Mission Control",
        version="1.0.0",
        description="Painel Web Vice City Neon para monitoramento 24/7 do enxame de 8 agentes.",
    )
    dashboard.state.config = cfg
    dashboard.state.store = store

    @dashboard.get("/", response_class=HTMLResponse)
    async def mission_control_home() -> HTMLResponse:
        status_payload = _collect_status_payload(cfg, store)
        return HTMLResponse(content=_render_dashboard_html(status_payload))

    @dashboard.get("/api/status")
    async def api_status() -> dict[str, Any]:
        return _collect_status_payload(cfg, store)

    @dashboard.post("/api/pause")
    async def api_pause() -> dict[str, Any]:
        store.set_flag("paused", "true")
        return {"ok": True, "paused": True, "status": "paused"}

    @dashboard.post("/api/resume")
    async def api_resume() -> dict[str, Any]:
        store.set_flag("paused", "false")
        return {"ok": True, "paused": False, "status": "active"}

    @dashboard.post("/api/approvals/{plan_id}/{decision}")
    async def api_decide_approval(plan_id: str, decision: str) -> dict[str, Any]:
        normalized_map = {
            "approved": ApprovalStatus.APPROVED.value,
            "approve": ApprovalStatus.APPROVED.value,
            "rejected": ApprovalStatus.REJECTED.value,
            "reject": ApprovalStatus.REJECTED.value,
        }
        clean_decision = normalized_map.get(decision.strip().lower())
        if not clean_decision:
            raise HTTPException(
                status_code=400,
                detail="Decision must be 'approved' or 'rejected'.",
            )

        updated = store.update_approval_status(plan_id, clean_decision)
        if not updated:
            raise HTTPException(
                status_code=404,
                detail=f"Pending approval for plan_id '{plan_id}' not found.",
            )
        return {
            "ok": True,
            "plan_id": plan_id,
            "decision": clean_decision,
            "status": clean_decision,
        }

    @dashboard.post("/api/trigger-demo")
    async def api_trigger_demo() -> dict[str, Any]:
        from gta6_agents.orchestrator.pipeline import MultiAgentOrchestrator

        orchestrator = MultiAgentOrchestrator(cfg, store)
        summary = await orchestrator.run_daily_cycle(
            max_items=1,
            include_campaign=True,
            demo_video_fallback=True,
        )
        return {
            "ok": True,
            "summary": jsonable_encoder(summary),
        }

    return dashboard


app = create_dashboard_app()
