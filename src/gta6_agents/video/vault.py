"""Day-1 Official Clip Vault Pre-Warmer (Prevents Fatal Mistake #1 & #3).

Pre-renders a bank of 100% official Rockstar Games 1080x1920 vertical Shorts
with burned-in captions and hooks BEFORE Day 1 / before leaving home for 4 days,
so the pipeline never stalls on launch day and never touches leaked footage.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from gta6_agents.agents.clipper import ClipperEditorAgent
from gta6_agents.agents.compliance import ComplianceFactAgent
from gta6_agents.agents.copywriter import ScriptCopyAgent
from gta6_agents.config import AppConfig
from gta6_agents.models import ApprovalStatus, ContentPillar, Platform, SourceCandidate
from gta6_agents.storage import StateStore


class OfficialClipVault:
    """Manages pre-rendering and stock inspection of the Official Rockstar Clip Bank."""

    def __init__(self, config: AppConfig, store: StateStore) -> None:
        self.config = config
        self.store = store
        self.vault_dir = self.config.root_dir / "vault" / "ready"
        self.vault_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.config.root_dir / "config" / "official_vault_manifest.yaml"

    def load_manifest(self) -> list[dict[str, Any]]:
        if not self.manifest_path.exists():
            return []
        data = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8")) or {}
        cuts = data.get("vault_cuts", [])
        return cuts if isinstance(cuts, list) else []

    def count_ready_clips(self) -> int:
        return len(list(self.vault_dir.glob("*.mp4")))

    async def prewarm_vault(
        self,
        count: int = 8,
        use_demo_source_if_offline: bool = False,
    ) -> dict[str, Any]:
        """Pre-render `count` official 1080x1920 Shorts + caption bundles into `vault/ready/`."""
        cuts = self.load_manifest()[: max(1, count)]
        copywriter = ScriptCopyAgent(self.config)
        compliance = ComplianceFactAgent(self.config, self.store)
        clipper = ClipperEditorAgent(self.config, self.store)

        rendered_items: list[dict[str, Any]] = []
        skipped: list[str] = []

        for item in cuts:
            cut_id = str(item.get("id", "cut"))
            target_mp4 = self.vault_dir / f"{cut_id}.mp4"
            target_meta = self.vault_dir / f"{cut_id}.yaml"
            if target_mp4.exists() and target_mp4.stat().st_size > 10_000:
                rendered_items.append({"id": cut_id, "video_path": str(target_mp4), "cached": True})
                continue

            pillar_str = str(item.get("pillar", "spotlights"))
            try:
                pillar = ContentPillar(pillar_str)
            except ValueError:
                pillar = ContentPillar.SPOTLIGHTS

            candidate = SourceCandidate(
                id=f"vault_{cut_id}",
                title=str(item.get("title", "GTA VI Official Highlight")),
                url=f"{item['url']}#{item.get('start_sec', 0)}-{item.get('end_sec', 25)}",
                creator_handle=str(item.get("creator", "@RockstarGames")),
                source_type="official_vault",
                is_official=True,
                is_campaign_acervo=True,
                campaign_slug=self.config.real_oficial_campaign_slug,
                pillar=pillar,
                summary=" ".join(item.get("verified_facts", [])),
                verified_facts=list(item.get("verified_facts", [])),
                suggested_start_sec=float(item.get("start_sec", 5.0)),
                suggested_end_sec=float(item.get("end_sec", 29.0)),
                virality_score=95.0,
            )

            plan = await copywriter.build_clip_plan(candidate, for_campaign=True)
            # Restore raw YouTube URL for yt-dlp segment download
            plan.source.url = str(item["url"])
            bundle = await copywriter.generate_captions(plan)
            verdict = compliance.evaluate_plan(plan, bundle)

            if not verdict.passed or verdict.approval_status == ApprovalStatus.REJECTED:
                skipped.append(f"{cut_id}: {'; '.join(verdict.reasons)}")
                continue

            clip = await clipper.render_clip_plan(
                plan, use_demo_source_if_needed=use_demo_source_if_offline
            )
            src_mp4 = Path(clip.video_path)
            if src_mp4.exists():
                target_mp4.write_bytes(src_mp4.read_bytes())

            meta_payload = {
                "id": cut_id,
                "plan": plan.model_dump(mode="json"),
                "captions": {
                    plat.value: cp.model_dump(mode="json")
                    for plat, cp in bundle.copies.items()
                },
                "video_path": str(target_mp4),
                "duration_sec": clip.duration_sec,
            }
            target_meta.write_text(
                yaml.safe_dump(meta_payload, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            rendered_items.append({"id": cut_id, "video_path": str(target_mp4), "cached": False})

        return {
            "manifest_total": len(self.load_manifest()),
            "vault_ready_total": self.count_ready_clips(),
            "processed": rendered_items,
            "skipped": skipped,
        }
