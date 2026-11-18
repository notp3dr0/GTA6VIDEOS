"""Persistent SQLite state store & JSONL ledgers (Asset Record, Source Record, History)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from gta6_agents.models import (
    ChampionshipEntry,
    Platform,
    PublicationRecord,
    RenderedClip,
    SourceCandidate,
)


class StateStore:
    """Manages SQLite state for deduplication, queues, credit budgets, and JSONL ledgers."""

    def __init__(self, data_dir: Path, memory_dir: Path) -> None:
        self.data_dir = data_dir
        self.memory_dir = memory_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "state.db"
        self.source_ledger_path = self.memory_dir / "source_ledger.jsonl"
        self.asset_ledger_path = self.memory_dir / "asset_ledger.jsonl"
        self.posted_ledger_path = self.memory_dir / "posted_history.jsonl"
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS seen_sources (
                    url TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    creator_handle TEXT,
                    pillar TEXT,
                    discovered_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS publications (
                    id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    clip_id TEXT,
                    platform TEXT NOT NULL,
                    status TEXT NOT NULL,
                    external_post_id TEXT,
                    public_url TEXT,
                    caption_text TEXT NOT NULL,
                    campaign_slug TEXT,
                    published_at TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS championship_entries (
                    id TEXT PRIMARY KEY,
                    campaign_slug TEXT NOT NULL,
                    publication_id TEXT NOT NULL,
                    youtube_url TEXT NOT NULL,
                    status TEXT NOT NULL,
                    validated_views INTEGER DEFAULT 0,
                    estimated_cpm_brl REAL DEFAULT 1.25,
                    notes TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS pending_approvals (
                    plan_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS credit_spend (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    credits INTEGER NOT NULL,
                    reason TEXT,
                    spent_date TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS system_flags (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    def _append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")

    def is_source_seen(self, url: str) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT 1 FROM seen_sources WHERE url = ?", (url,)).fetchone()
            return row is not None

    def record_source(self, source: SourceCandidate) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO seen_sources (url, source_id, creator_handle, pillar, discovered_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    source.url,
                    source.id,
                    source.creator_handle,
                    source.pillar.value,
                    source.discovered_at.isoformat(),
                ),
            )
        self._append_jsonl(self.source_ledger_path, source.model_dump(mode="json"))

    def record_asset(self, clip: RenderedClip, source_url: str, permission_basis: str = "public_credited_transformed") -> None:
        payload = clip.model_dump(mode="json")
        payload["source_url"] = source_url
        payload["permission_basis"] = permission_basis
        self._append_jsonl(self.asset_ledger_path, payload)

    def record_publication(self, pub: PublicationRecord) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        pub_iso = pub.published_at.isoformat() if pub.published_at else now_iso
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO publications
                (id, plan_id, clip_id, platform, status, external_post_id, public_url, caption_text, campaign_slug, published_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pub.id,
                    pub.plan_id,
                    pub.clip_id,
                    pub.platform.value,
                    pub.status,
                    pub.external_post_id,
                    pub.public_url,
                    pub.caption_text,
                    pub.campaign_slug,
                    pub_iso,
                    now_iso,
                ),
            )
        self._append_jsonl(self.posted_ledger_path, pub.model_dump(mode="json"))

    def recent_captions(self, days: int = 7) -> list[str]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT caption_text FROM publications WHERE created_at >= ?", (cutoff,)
            ).fetchall()
            return [r["caption_text"] for r in rows]

    def last_published_at(self, platform: Platform) -> datetime | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT published_at FROM publications
                WHERE platform = ? AND status IN ('published', 'scheduled')
                ORDER BY published_at DESC LIMIT 1
                """,
                (platform.value,),
            ).fetchone()
            if row and row["published_at"]:
                return datetime.fromisoformat(row["published_at"])
            return None

    def record_championship_entry(self, entry: ChampionshipEntry) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO championship_entries
                (id, campaign_slug, publication_id, youtube_url, status, validated_views, estimated_cpm_brl, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry.id,
                    entry.campaign_slug,
                    entry.publication_id,
                    entry.youtube_url,
                    entry.status,
                    entry.validated_views,
                    entry.estimated_cpm_brl,
                    entry.notes,
                    entry.created_at.isoformat(),
                ),
            )

    def list_publications(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM publications ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def list_championship_entries(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM championship_entries ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def save_pending_approval(self, plan_id: str, payload: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO pending_approvals (plan_id, payload_json, status, created_at)
                VALUES (?, ?, 'pending', ?)
                """,
                (plan_id, json.dumps(payload, ensure_ascii=False, default=str), datetime.now(timezone.utc).isoformat()),
            )

    def update_approval_status(self, plan_id: str, status: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE pending_approvals SET status = ? WHERE plan_id = ?",
                (status, plan_id),
            )
            return cur.rowcount > 0

    def list_pending_approvals(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM pending_approvals WHERE status = 'pending' ORDER BY created_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def add_credit_spend(self, provider: str, credits: int, reason: str = "") -> None:
        now = datetime.now(timezone.utc)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO credit_spend (provider, credits, reason, spent_date, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (provider, credits, reason, now.strftime("%Y-%m-%d"), now.isoformat()),
            )

    def today_credit_spend(self, provider: str = "real_oficial") -> int:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(credits), 0) AS total FROM credit_spend WHERE provider = ? AND spent_date = ?",
                (provider, today),
            ).fetchone()
            return int(row["total"]) if row else 0

    def set_flag(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO system_flags (key, value) VALUES (?, ?)",
                (key, value),
            )

    def get_flag(self, key: str, default: str = "") -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM system_flags WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default
