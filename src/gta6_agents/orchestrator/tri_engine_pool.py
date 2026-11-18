"""Tri-Engine AI Pool (Claude Pro + Codex Plus + Antigravity/Gemini Ultra).

Implements:
- Effort-split execution: `THINK_XHIGH` (strategic hook, script angle, compliance & viral scoring)
  vs. `EXECUTE_LOW` (mechanical caption formatting, subtitle chunking, hashtag packing, metadata JSON).
- Automatic 5-hour rolling window rate-limit detection (`RateLimitDetector`), cooldown tracking,
  periodic 15-minute lightweight unlock probes (`probe_interval_sec = 900`), and instant failover
  (`CLAUDE_PRO -> CODEX_PLUS -> ANTIGRAVITY_ULTRA -> TEMPLATE_FALLBACK`).
- SQLite-backed task checkpointing (`task_checkpoints`) and engine telemetry (`engine_cooldowns`)
  on `StateStore.db_path` so zero work is lost across rate limits or machine restarts.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
import shlex
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable

import httpx

from gta6_agents.config import AppConfig
from gta6_agents.model_router import TaskTier, TieredModelRouter
from gta6_agents.models import utc_now
from gta6_agents.storage import StateStore

logger = logging.getLogger(__name__)


class AIEngine(str, Enum):
    CLAUDE_PRO = "claude_pro"
    CODEX_PLUS = "codex_plus"
    ANTIGRAVITY_ULTRA = "antigravity_ultra"
    TEMPLATE_FALLBACK = "template_fallback"


class EffortRole(str, Enum):
    THINK_XHIGH = "think_xhigh"
    EXECUTE_LOW = "execute_low"


class CheckpointStage(str, Enum):
    PENDING_THINK = "PENDING_THINK"
    READY_TO_EXECUTE = "READY_TO_EXECUTE"
    READY_TO_RENDER = "READY_TO_RENDER"
    READY_TO_PUBLISH = "READY_TO_PUBLISH"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


PRIMARY_ENGINES: tuple[AIEngine, ...] = (
    AIEngine.CLAUDE_PRO,
    AIEngine.CODEX_PLUS,
    AIEngine.ANTIGRAVITY_ULTRA,
)

FAILOVER_CHAIN: tuple[AIEngine, ...] = (
    AIEngine.CLAUDE_PRO,
    AIEngine.CODEX_PLUS,
    AIEngine.ANTIGRAVITY_ULTRA,
    AIEngine.TEMPLATE_FALLBACK,
)

ENGINE_STATUS_AVAILABLE = "AVAILABLE"
ENGINE_STATUS_COOLDOWN_5H = "COOLDOWN_5H"


class RateLimitError(RuntimeError):
    """Raised when an AI engine hits a 5-hour rolling window or HTTP 429 quota limit."""

    def __init__(
        self,
        message: str,
        cooldown_seconds: int = 18000,
        engine: AIEngine | None = None,
    ) -> None:
        super().__init__(message)
        self.cooldown_seconds = max(1, int(cooldown_seconds))
        self.engine = engine


class _EngineUnavailableError(RuntimeError):
    """Raised internally when a local CLI binary and API key are both unconfigured."""


def _ensure_utc(dt: datetime | None = None) -> datetime:
    if dt is None:
        return utc_now()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_iso_utc(raw: str | None) -> datetime | None:
    if not raw or not raw.strip():
        return None
    cleaned = raw.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        return _ensure_utc(datetime.fromisoformat(cleaned))
    except ValueError:
        return None


class RateLimitDetector:
    """Detects Claude Pro, Codex Plus, and API 5-hour rolling window rate limits."""

    default_cooldown_sec: int = 18000
    probe_interval_sec: int = 900

    _LIMIT_PATTERNS: tuple[re.Pattern[str], ...] = (
        re.compile(r"5-hour", re.IGNORECASE),
        re.compile(r"5\s+hours?", re.IGNORECASE),
        re.compile(r"usage\s+limit", re.IGNORECASE),
        re.compile(r"rate[\s_-]*limit", re.IGNORECASE),
        re.compile(r"quota\s+exceeded", re.IGNORECASE),
        re.compile(r"insufficient_quota", re.IGNORECASE),
        re.compile(r"hit\s+your\s+(?:[\w-]+\s+)?limit", re.IGNORECASE),
        re.compile(r"reached\s+your\s+(?:[\w-]+\s+)?limit", re.IGNORECASE),
        re.compile(r"resets?\s+in\b", re.IGNORECASE),
        re.compile(r"try\s+again\s+in\b", re.IGNORECASE),
        re.compile(r"too\s+many\s+requests", re.IGNORECASE),
        re.compile(r"\b429\b"),
    )

    _RESET_ANCHOR_RE = re.compile(
        r"(?:resets?\s+in|try\s+again\s+in|retry\s+(?:in|after)|unlocks?\s+in|available\s+in|wait(?:\s+for)?)"
        r"\s*:?\s*([^\n.;,()]{1,64})",
        re.IGNORECASE,
    )
    _HOURS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h)\b", re.IGNORECASE)
    _MINUTES_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:minutes?|mins?|m)\b", re.IGNORECASE)
    _SECONDS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:seconds?|secs?|s)\b", re.IGNORECASE)
    _HHMMSS_RE = re.compile(r"\b(\d{1,2}):(\d{2})(?::(\d{2}))?\b")
    _COMPOUND_HM_RE = re.compile(
        r"\b(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h)\s*(?:and\s*)?(\d+(?:\.\d+)?)\s*(?:minutes?|mins?|m)\b",
        re.IGNORECASE,
    )
    _REMAINING_UNIT_RE = re.compile(
        r"\b(\d+(?:\.\d+)?)\s*(hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\s+(?:remaining|left)\b",
        re.IGNORECASE,
    )

    @classmethod
    def _extract_segment_seconds(cls, segment: str) -> int | None:
        hours_match = cls._HOURS_RE.search(segment)
        minutes_match = cls._MINUTES_RE.search(segment)
        seconds_match = cls._SECONDS_RE.search(segment)

        if hours_match or minutes_match or seconds_match:
            hours = float(hours_match.group(1)) if hours_match else 0.0
            minutes = float(minutes_match.group(1)) if minutes_match else 0.0
            seconds = float(seconds_match.group(1)) if seconds_match else 0.0
            total = round(hours * 3600.0 + minutes * 60.0 + seconds)
            if total > 0:
                return int(total)

        hhmm = cls._HHMMSS_RE.search(segment)
        if hhmm and not re.search(r"\b(?:am|pm)\b", segment, re.IGNORECASE):
            hrs = int(hhmm.group(1))
            mins = int(hhmm.group(2))
            secs = int(hhmm.group(3) or 0)
            total = hrs * 3600 + mins * 60 + secs
            if total > 0:
                return int(total)

        return None

    @classmethod
    def parse_cooldown_seconds(
        cls,
        output_or_error: str,
        default_seconds: int | None = None,
    ) -> int:
        fallback = int(
            default_seconds if default_seconds is not None else cls.default_cooldown_sec
        )
        if not output_or_error:
            return fallback

        for match in cls._RESET_ANCHOR_RE.finditer(output_or_error):
            parsed = cls._extract_segment_seconds(match.group(1))
            if parsed is not None:
                return parsed

        # Fallback: look for explicit remaining compound durations outside "5-hour limit" phrasing
        scrubbed = re.sub(r"\b5[\s-]+hours?\b", "", output_or_error, flags=re.IGNORECASE)
        compound = cls._COMPOUND_HM_RE.search(scrubbed)
        if compound:
            total = round(float(compound.group(1)) * 3600.0 + float(compound.group(2)) * 60.0)
            if total > 0:
                return int(total)

        rem = cls._REMAINING_UNIT_RE.search(scrubbed)
        if rem:
            val = float(rem.group(1))
            unit = rem.group(2).lower()
            if unit.startswith("h"):
                return max(1, round(val * 3600.0))
            if unit.startswith("m"):
                return max(1, round(val * 60.0))
            return max(1, round(val))

        return fallback

    @staticmethod
    def detect_5h_limit(output_or_error: str, exit_code: int = 0) -> tuple[bool, int]:
        """Detect 5-hour rolling window / quota exhaustion and return `(is_limited, cooldown_sec)`."""
        text = (output_or_error or "").strip()
        if not text:
            if exit_code == 429:
                return True, RateLimitDetector.default_cooldown_sec
            return False, 0

        matched = exit_code == 429 or any(
            pattern.search(text) for pattern in RateLimitDetector._LIMIT_PATTERNS
        )
        if not matched:
            return False, 0

        cooldown_sec = RateLimitDetector.parse_cooldown_seconds(
            text,
            default_seconds=RateLimitDetector.default_cooldown_sec,
        )
        return True, max(1, cooldown_sec)

    @classmethod
    def should_probe(
        cls,
        cooldown_until: str | datetime | None,
        last_probe_at: str | datetime | None,
        now: datetime | None = None,
        probe_interval_sec: int | None = None,
    ) -> bool:
        """Return True when an engine in cooldown is due for a 15-minute lightweight unlock probe."""
        current = _ensure_utc(now)
        interval = int(
            probe_interval_sec if probe_interval_sec is not None else cls.probe_interval_sec
        )

        until_dt = (
            _ensure_utc(cooldown_until)
            if isinstance(cooldown_until, datetime)
            else _parse_iso_utc(cooldown_until)
        )
        if until_dt is not None and current >= until_dt:
            return True

        probe_dt = (
            _ensure_utc(last_probe_at)
            if isinstance(last_probe_at, datetime)
            else _parse_iso_utc(last_probe_at)
        )
        if probe_dt is None:
            return True

        return (current - probe_dt).total_seconds() >= interval


class TriEnginePool:
    """Resilient Tri-Engine AI Pool with xhigh/low split, 5h cooldowns, and SQLite checkpoint resume."""

    def __init__(
        self,
        config: AppConfig,
        store: StateStore | None = None,
        router: TieredModelRouter | None = None,
        probe_interval_sec: int = RateLimitDetector.probe_interval_sec,
    ) -> None:
        self.config = config
        self.store = store or StateStore(
            data_dir=config.data_dir,
            memory_dir=config.memory_dir,
        )
        self.db_path = self.store.db_path
        self.router = router or TieredModelRouter()
        self.probe_interval_sec = int(probe_interval_sec)
        self._rr_index = 0
        self._init_tables()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_tables(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS engine_cooldowns (
                    engine TEXT PRIMARY KEY,
                    status TEXT,
                    cooldown_until TEXT,
                    last_probe_at TEXT,
                    fail_count INTEGER,
                    completed_think INTEGER DEFAULT 0,
                    completed_exec INTEGER DEFAULT 0,
                    last_error TEXT
                );

                CREATE TABLE IF NOT EXISTS task_checkpoints (
                    task_id TEXT PRIMARY KEY,
                    channel_id TEXT,
                    slot_time TEXT,
                    stage TEXT,
                    assigned_engine TEXT,
                    payload_json TEXT,
                    updated_at TEXT
                );
                """
            )
            for eng in FAILOVER_CHAIN:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO engine_cooldowns
                    (engine, status, cooldown_until, last_probe_at, fail_count, completed_think, completed_exec, last_error)
                    VALUES (?, ?, NULL, NULL, 0, 0, 0, '')
                    """,
                    (eng.value, ENGINE_STATUS_AVAILABLE),
                )

    # ------------------------------------------------------------------
    # CLI & API Driver Inspection and Command Builders
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_cli_binary(env_var: str, default_bin: str) -> list[str] | None:
        override = os.getenv(env_var, "").strip()
        if override:
            parts = shlex.split(override)
            if parts and (shutil.which(parts[0]) or os.path.isfile(parts[0])):
                return parts
            return None
        resolved = shutil.which(default_bin)
        return [resolved] if resolved else None

    def is_engine_configured(self, engine: AIEngine) -> bool:
        """Return True if the CLI binary or corresponding API key is available for `engine`."""
        if engine == AIEngine.CLAUDE_PRO:
            has_cli = self._resolve_cli_binary("CLAUDE_CLI_CMD", "claude") is not None
            has_key = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
            return has_cli or has_key

        if engine == AIEngine.CODEX_PLUS:
            has_cli = self._resolve_cli_binary("CODEX_CLI_CMD", "codex") is not None
            has_key = bool(
                os.getenv("OPENAI_API_KEY", "").strip()
                or (self.config.openai_api_key or "").strip()
            )
            return has_cli or has_key

        if engine == AIEngine.ANTIGRAVITY_ULTRA:
            return bool(
                os.getenv("GEMINI_API_KEY", "").strip()
                or os.getenv("GOOGLE_API_KEY", "").strip()
                or (self.config.gemini_api_key or "").strip()
            )

        return True

    def build_claude_command(
        self,
        role: EffortRole,
        prompt: str,
        effort_override: str | None = None,
    ) -> list[str]:
        """Build exact `claude -p` invocation for `THINK_XHIGH` or `EXECUTE_LOW`."""
        bin_parts = self._resolve_cli_binary("CLAUDE_CLI_CMD", "claude") or ["claude"]
        if role == EffortRole.THINK_XHIGH:
            model = os.getenv("CLAUDE_THINK_MODEL", "claude-opus-4-6").strip() or "claude-opus-4-6"
            effort = (
                effort_override
                or os.getenv("CLAUDE_THINK_EFFORT", "max").strip()
                or "max"
            )
            return [*bin_parts, "-p", "--model", model, "--effort", effort, prompt]

        model = os.getenv("CLAUDE_EXEC_MODEL", "claude-haiku-4-5").strip() or "claude-haiku-4-5"
        effort = (
            effort_override
            if effort_override is not None
            else os.getenv("CLAUDE_EXEC_EFFORT", "").strip()
        )
        cmd = [*bin_parts, "-p", "--model", model]
        if effort:
            cmd.extend(["--effort", effort])
        cmd.append(prompt)
        return cmd

    def build_codex_command(self, role: EffortRole, prompt: str) -> list[str]:
        """Build exact `codex exec` invocation for `THINK_XHIGH` or `EXECUTE_LOW`."""
        bin_parts = self._resolve_cli_binary("CODEX_CLI_CMD", "codex") or ["codex"]
        if role == EffortRole.THINK_XHIGH:
            model = os.getenv("CODEX_THINK_MODEL", "gpt-5-codex").strip() or "gpt-5-codex"
            effort = os.getenv("CODEX_THINK_EFFORT", "xhigh").strip() or "xhigh"
        else:
            model = os.getenv("CODEX_EXEC_MODEL", "gpt-5-codex").strip() or "gpt-5-codex"
            effort = os.getenv("CODEX_EXEC_EFFORT", "low").strip() or "low"

        return [
            *bin_parts,
            "exec",
            "--skip-git-repo-check",
            "-m",
            model,
            "-c",
            f'model_reasoning_effort="{effort}"',
            prompt,
        ]

    # ------------------------------------------------------------------
    # Subprocess & HTTP API Execution Drivers
    # ------------------------------------------------------------------

    async def _run_cli_subprocess(
        self,
        cmd: list[str],
        engine: AIEngine,
        timeout_sec: float,
    ) -> tuple[str, str, int]:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout_sec,
            )
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.communicate()
            raise RuntimeError(
                f"{engine.value} CLI timed out after {timeout_sec:.0f}s"
            ) from exc

        stdout_text = stdout_b.decode("utf-8", errors="replace").strip()
        stderr_text = stderr_b.decode("utf-8", errors="replace").strip()
        code = int(proc.returncode or 0)
        return stdout_text, stderr_text, code

    async def _execute_claude_api(self, role: EffortRole, prompt: str) -> str:
        api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            raise _EngineUnavailableError("Neither `claude` CLI nor ANTHROPIC_API_KEY is configured.")

        alias_map = {
            "opus": "claude-opus-4-6",
            "sonnet": "claude-sonnet-4-6",
            "haiku": "claude-haiku-4-5",
        }
        raw_model = (
            os.getenv("CLAUDE_THINK_MODEL", "claude-opus-4-6").strip()
            if role == EffortRole.THINK_XHIGH
            else os.getenv("CLAUDE_EXEC_MODEL", "claude-haiku-4-5").strip()
        )
        model = alias_map.get(raw_model.lower(), raw_model or "claude-opus-4-6")
        max_tokens = 2048 if role == EffortRole.THINK_XHIGH else 1024

        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": 0.35 if role == EffortRole.THINK_XHIGH else 0.2,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        timeout = 90.0 if role == EffortRole.THINK_XHIGH else 45.0
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload,
            )

        if resp.status_code != 200:
            retry_hdr = resp.headers.get("retry-after", "")
            err_body = f"{resp.text} (retry after {retry_hdr}s)" if retry_hdr else resp.text
            limited, cooldown_sec = RateLimitDetector.detect_5h_limit(err_body, resp.status_code)
            if limited:
                raise RateLimitError(
                    f"Claude API rate limit ({resp.status_code}): {resp.text[:300]}",
                    cooldown_seconds=cooldown_sec,
                    engine=AIEngine.CLAUDE_PRO,
                )
            raise RuntimeError(f"Claude API HTTP {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        blocks = data.get("content", [])
        text = "\n".join(
            str(b.get("text", ""))
            for b in blocks
            if isinstance(b, dict) and b.get("type") == "text"
        ).strip()
        if not text:
            raise RuntimeError("Claude API returned empty content.")
        return text

    async def _execute_claude_pro(self, role: EffortRole, prompt: str) -> str:
        cli_bin = self._resolve_cli_binary("CLAUDE_CLI_CMD", "claude")
        if cli_bin is not None:
            timeout = float(
                os.getenv(
                    "CLAUDE_CLI_TIMEOUT_SEC",
                    "180" if role == EffortRole.THINK_XHIGH else "90",
                )
            )
            cmd = self.build_claude_command(role=role, prompt=prompt)
            stdout_text, stderr_text, code = await self._run_cli_subprocess(
                cmd, AIEngine.CLAUDE_PRO, timeout
            )
            combined = "\n".join(p for p in (stdout_text, stderr_text) if p)
            limited, cooldown_sec = RateLimitDetector.detect_5h_limit(combined, code)
            if limited:
                raise RateLimitError(
                    combined or "Claude Pro 5-hour usage limit reached",
                    cooldown_seconds=cooldown_sec,
                    engine=AIEngine.CLAUDE_PRO,
                )

            # Handle CLI versions where `--effort xhigh` maps to `--effort max`
            if (
                code != 0
                and role == EffortRole.THINK_XHIGH
                and "--effort" in cmd
                and "xhigh" in cmd
                and "effort" in combined.lower()
            ):
                retry_cmd = self.build_claude_command(
                    role=role, prompt=prompt, effort_override="max"
                )
                stdout_text, stderr_text, code = await self._run_cli_subprocess(
                    retry_cmd, AIEngine.CLAUDE_PRO, timeout
                )
                combined = "\n".join(p for p in (stdout_text, stderr_text) if p)
                limited, cooldown_sec = RateLimitDetector.detect_5h_limit(combined, code)
                if limited:
                    raise RateLimitError(
                        combined or "Claude Pro 5-hour usage limit reached",
                        cooldown_seconds=cooldown_sec,
                        engine=AIEngine.CLAUDE_PRO,
                    )

            if code == 0 and stdout_text:
                return stdout_text
            if os.getenv("ANTHROPIC_API_KEY", "").strip():
                return await self._execute_claude_api(role=role, prompt=prompt)
            raise RuntimeError(
                f"Claude CLI exited with code {code}: {combined[:300]}"
            )

        return await self._execute_claude_api(role=role, prompt=prompt)

    async def _execute_codex_api(self, role: EffortRole, prompt: str) -> str:
        api_key = (
            os.getenv("OPENAI_API_KEY", "").strip()
            or (self.config.openai_api_key or "").strip()
        )
        if not api_key:
            raise _EngineUnavailableError("Neither `codex` CLI nor OPENAI_API_KEY is configured.")

        if role == EffortRole.THINK_XHIGH:
            model = os.getenv("CODEX_API_THINK_MODEL") or os.getenv("CODEX_THINK_MODEL") or "o3"
            if model in ("sol-6.2", "gpt-5-codex") and not os.getenv("CODEX_API_THINK_MODEL"):
                model = "o3"
        else:
            model = (
                os.getenv("CODEX_API_EXEC_MODEL")
                or os.getenv("CODEX_EXEC_MODEL")
                or "gpt-4o-mini"
            )
            if model in ("sol-6.2", "gpt-5-codex") and not os.getenv("CODEX_API_EXEC_MODEL"):
                model = "gpt-4o-mini"

        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if model.startswith("o") or model.startswith("gpt-5"):
            payload["reasoning_effort"] = (
                "high" if role == EffortRole.THINK_XHIGH else "low"
            )
        else:
            payload["temperature"] = 0.35 if role == EffortRole.THINK_XHIGH else 0.2

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        timeout = 90.0 if role == EffortRole.THINK_XHIGH else 45.0
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json=payload,
            )

        if resp.status_code != 200:
            retry_hdr = resp.headers.get("retry-after", "")
            err_body = f"{resp.text} (retry after {retry_hdr}s)" if retry_hdr else resp.text
            limited, cooldown_sec = RateLimitDetector.detect_5h_limit(err_body, resp.status_code)
            if limited:
                raise RateLimitError(
                    f"Codex/OpenAI API rate limit ({resp.status_code}): {resp.text[:300]}",
                    cooldown_seconds=cooldown_sec,
                    engine=AIEngine.CODEX_PLUS,
                )
            raise RuntimeError(f"Codex/OpenAI API HTTP {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("Codex/OpenAI API returned empty choices.")
        content = str(choices[0].get("message", {}).get("content", "")).strip()
        if not content:
            raise RuntimeError("Codex/OpenAI API returned empty content.")
        return content

    async def _execute_codex_plus(self, role: EffortRole, prompt: str) -> str:
        cli_bin = self._resolve_cli_binary("CODEX_CLI_CMD", "codex")
        if cli_bin is not None:
            timeout = float(
                os.getenv(
                    "CODEX_CLI_TIMEOUT_SEC",
                    "180" if role == EffortRole.THINK_XHIGH else "90",
                )
            )
            cmd = self.build_codex_command(role=role, prompt=prompt)
            stdout_text, stderr_text, code = await self._run_cli_subprocess(
                cmd, AIEngine.CODEX_PLUS, timeout
            )
            combined = "\n".join(p for p in (stdout_text, stderr_text) if p)
            limited, cooldown_sec = RateLimitDetector.detect_5h_limit(combined, code)
            if limited:
                raise RateLimitError(
                    combined or "Codex Plus 5-hour usage limit reached",
                    cooldown_seconds=cooldown_sec,
                    engine=AIEngine.CODEX_PLUS,
                )
            if code == 0 and stdout_text:
                return stdout_text
            if (
                os.getenv("OPENAI_API_KEY", "").strip()
                or (self.config.openai_api_key or "").strip()
            ):
                return await self._execute_codex_api(role=role, prompt=prompt)
            raise RuntimeError(
                f"Codex CLI exited with code {code}: {combined[:300]}"
            )

        return await self._execute_codex_api(role=role, prompt=prompt)

    async def _execute_antigravity_ultra(self, role: EffortRole, prompt: str) -> str:
        tier = (
            TaskTier.CARBON
            if role == EffortRole.THINK_XHIGH
            else TaskTier.HYDROGEN
        )
        temperature = 0.35 if role == EffortRole.THINK_XHIGH else 0.2

        # Bridge AppConfig.gemini_api_key if environment variable is unset
        had_env_key = bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
        injected_env = False
        if not had_env_key and (self.config.gemini_api_key or "").strip():
            os.environ["GEMINI_API_KEY"] = self.config.gemini_api_key.strip()
            injected_env = True

        try:
            response = await self.router.complete(
                prompt=prompt,
                tier=tier,
                temperature=temperature,
            )
        except Exception as exc:
            limited, cooldown_sec = RateLimitDetector.detect_5h_limit(str(exc), exit_code=1)
            if limited:
                raise RateLimitError(
                    str(exc),
                    cooldown_seconds=cooldown_sec,
                    engine=AIEngine.ANTIGRAVITY_ULTRA,
                ) from exc
            raise
        finally:
            if injected_env:
                os.environ.pop("GEMINI_API_KEY", None)

        if response is None or not str(response).strip():
            raise _EngineUnavailableError(
                "Antigravity/Gemini TieredModelRouter returned None (API key unset or upstream unavailable)."
            )

        text = str(response).strip()
        limited, cooldown_sec = RateLimitDetector.detect_5h_limit(text, exit_code=0)
        if limited:
            raise RateLimitError(
                text,
                cooldown_seconds=cooldown_sec,
                engine=AIEngine.ANTIGRAVITY_ULTRA,
            )
        return text

    @staticmethod
    def _execute_template_fallback(role: EffortRole, prompt: str) -> str:
        snippet = re.sub(r"\s+", " ", (prompt or "").strip())[:140]
        if role == EffortRole.THINK_XHIGH:
            return json.dumps(
                {
                    "engine": AIEngine.TEMPLATE_FALLBACK.value,
                    "role": EffortRole.THINK_XHIGH.value,
                    "hook_on_screen": "DETALHE OFICIAL NO GTA 6 EM LEONIDA",
                    "script_angle": "verified_visual_breakdown",
                    "voiceover_script": (
                        "Olha esse detalhe capturado em material verificado de GTA 6. "
                        "A simulação de mundo em Leonida e Vice City eleva o nível de imersão. "
                        "Você tinha reparado nisso?"
                    ),
                    "commentary_overlay": "Análise transformativa verificada • Leonida / Vice City",
                    "compliance_passed": True,
                    "virality_score": 80.0,
                    "prompt_excerpt": snippet,
                },
                ensure_ascii=False,
            )

        return json.dumps(
            {
                "engine": AIEngine.TEMPLATE_FALLBACK.value,
                "role": EffortRole.EXECUTE_LOW.value,
                "youtube_title": "GTA 6: Detalhe Verificado em Leonida #Shorts",
                "caption": (
                    "DETALHE OFICIAL NO GTA 6 EM LEONIDA\n\n"
                    "Página de fãs com análise transformativa, sem afiliação com a Rockstar Games.\n\n"
                    "🎥 @RockstarGames\n\n"
                    "#GTA6 #GTAVI #Shorts"
                ),
                "hashtags": ["#GTA6", "#GTAVI", "#Shorts"],
                "subtitles": [
                    {"start_sec": 0.0, "end_sec": 3.5, "text": "Olha esse detalhe em GTA 6."},
                    {"start_sec": 3.5, "end_sec": 8.0, "text": "Leonida e Vice City impressionam."},
                ],
                "metadata": {"fallback": True, "source_excerpt": snippet},
            },
            ensure_ascii=False,
        )

    async def execute_role(
        self,
        engine: AIEngine,
        role: EffortRole,
        prompt: str,
    ) -> str:
        """Execute a single `(engine, role, prompt)` call using the real CLI/API driver."""
        if engine == AIEngine.CLAUDE_PRO:
            return await self._execute_claude_pro(role=role, prompt=prompt)
        if engine == AIEngine.CODEX_PLUS:
            return await self._execute_codex_plus(role=role, prompt=prompt)
        if engine == AIEngine.ANTIGRAVITY_ULTRA:
            return await self._execute_antigravity_ultra(role=role, prompt=prompt)
        return self._execute_template_fallback(role=role, prompt=prompt)

    @staticmethod
    async def _invoke_override(
        runner_override: Callable[..., Any],
        engine: AIEngine,
        role: EffortRole,
        prompt: str,
    ) -> str:
        sig = inspect.signature(runner_override)
        params = [
            p
            for p in sig.parameters.values()
            if p.kind
            in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        ]
        has_varargs = any(
            p.kind == inspect.Parameter.VAR_POSITIONAL
            for p in sig.parameters.values()
        )

        if has_varargs or len(params) >= 3:
            raw = runner_override(engine, role, prompt)
        elif len(params) == 2:
            raw = runner_override(engine, prompt)
        elif len(params) == 1:
            raw = runner_override(prompt)
        else:
            raw = runner_override()

        if inspect.isawaitable(raw):
            raw = await raw

        exit_code = 0
        if isinstance(raw, tuple) and len(raw) == 2 and isinstance(raw[1], int):
            output_text = str(raw[0] if raw[0] is not None else "")
            exit_code = int(raw[1])
        elif isinstance(raw, dict) and (
            "exit_code" in raw or "returncode" in raw
        ):
            exit_code = int(raw.get("exit_code", raw.get("returncode", 0)))
            output_text = str(
                raw.get("output")
                or raw.get("stdout")
                or raw.get("stderr")
                or ""
            )
        elif isinstance(raw, dict):
            output_text = json.dumps(raw, ensure_ascii=False)
        else:
            output_text = str(raw if raw is not None else "")

        limited, cooldown_sec = RateLimitDetector.detect_5h_limit(output_text, exit_code)
        if limited:
            raise RateLimitError(
                output_text or f"{engine.value} 5-hour rate limit hit",
                cooldown_seconds=cooldown_sec,
                engine=engine,
            )
        if exit_code != 0:
            raise RuntimeError(
                f"Runner override for {engine.value} exited with code {exit_code}: {output_text}"
            )
        return output_text

    # ------------------------------------------------------------------
    # SQLite Cooldown Management & 15-Minute Periodic Probing
    # ------------------------------------------------------------------

    def mark_engine_5h_cooldown(
        self,
        engine: AIEngine,
        cooldown_seconds: int = 18000,
        reason: str = "",
        now: datetime | None = None,
    ) -> None:
        """Mark `engine` in `COOLDOWN_5H` until `now + cooldown_seconds`."""
        eng = AIEngine(engine)
        if eng == AIEngine.TEMPLATE_FALLBACK:
            return

        current = _ensure_utc(now)
        duration = max(1, int(cooldown_seconds))
        until_iso = (current + timedelta(seconds=duration)).isoformat()
        now_iso = current.isoformat()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO engine_cooldowns
                (engine, status, cooldown_until, last_probe_at, fail_count, completed_think, completed_exec, last_error)
                VALUES (?, ?, ?, ?, 1, 0, 0, ?)
                ON CONFLICT(engine) DO UPDATE SET
                    status = excluded.status,
                    cooldown_until = excluded.cooldown_until,
                    last_probe_at = excluded.last_probe_at,
                    fail_count = COALESCE(engine_cooldowns.fail_count, 0) + 1,
                    last_error = excluded.last_error
                """,
                (eng.value, ENGINE_STATUS_COOLDOWN_5H, until_iso, now_iso, reason[:1000]),
            )

        logger.warning(
            "Engine %s placed in %s for %ds (until %s): %s",
            eng.value,
            ENGINE_STATUS_COOLDOWN_5H,
            duration,
            until_iso,
            reason[:180],
        )

    def clear_expired_cooldowns(self, now: datetime | None = None) -> list[AIEngine]:
        """Unlock any engine whose `cooldown_until <= now` and return newly restored engines."""
        current = _ensure_utc(now)
        restored: list[AIEngine] = []

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT engine, status, cooldown_until
                FROM engine_cooldowns
                WHERE status = ? OR cooldown_until IS NOT NULL
                """,
                (ENGINE_STATUS_COOLDOWN_5H,),
            ).fetchall()

            for row in rows:
                until_dt = _parse_iso_utc(row["cooldown_until"])
                if until_dt is None or until_dt <= current:
                    eng_name = str(row["engine"])
                    conn.execute(
                        """
                        UPDATE engine_cooldowns
                        SET status = ?, cooldown_until = NULL, last_probe_at = NULL
                        WHERE engine = ?
                        """,
                        (ENGINE_STATUS_AVAILABLE, eng_name),
                    )
                    if row["status"] == ENGINE_STATUS_COOLDOWN_5H:
                        try:
                            restored.append(AIEngine(eng_name))
                        except ValueError:
                            pass

        if restored:
            logger.info(
                "Unlocked expired 5h cooldowns for engines: %s",
                ", ".join(e.value for e in restored),
            )
        return restored

    async def probe_cooldown_engines(
        self,
        now: datetime | None = None,
        runner_override: Callable[..., Any] | None = None,
    ) -> list[AIEngine]:
        """Run lightweight 15-minute probes on cooldown engines so unlocked windows rejoin immediately."""
        current = _ensure_utc(now)
        unlocked = list(self.clear_expired_cooldowns(now=current))

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT engine, cooldown_until, last_probe_at
                FROM engine_cooldowns
                WHERE status = ?
                """,
                (ENGINE_STATUS_COOLDOWN_5H,),
            ).fetchall()

        for row in rows:
            try:
                eng = AIEngine(str(row["engine"]))
            except ValueError:
                continue

            if not RateLimitDetector.should_probe(
                cooldown_until=row["cooldown_until"],
                last_probe_at=row["last_probe_at"],
                now=current,
                probe_interval_sec=self.probe_interval_sec,
            ):
                continue

            if runner_override is None and not self.is_engine_configured(eng):
                continue

            probe_prompt = "Reply with OK."
            try:
                if runner_override is not None:
                    await self._invoke_override(
                        runner_override, eng, EffortRole.EXECUTE_LOW, probe_prompt
                    )
                else:
                    await self.execute_role(
                        engine=eng, role=EffortRole.EXECUTE_LOW, prompt=probe_prompt
                    )

                with self._connect() as conn:
                    conn.execute(
                        """
                        UPDATE engine_cooldowns
                        SET status = ?, cooldown_until = NULL, last_probe_at = ?
                        WHERE engine = ?
                        """,
                        (ENGINE_STATUS_AVAILABLE, current.isoformat(), eng.value),
                    )
                unlocked.append(eng)
                logger.info("15m probe succeeded — %s rejoined active pool early!", eng.value)
            except RateLimitError as rl_exc:
                until_dt = _parse_iso_utc(row["cooldown_until"])
                new_until = (
                    (current + timedelta(seconds=rl_exc.cooldown_seconds)).isoformat()
                    if rl_exc.cooldown_seconds != RateLimitDetector.default_cooldown_sec
                    or until_dt is None
                    else row["cooldown_until"]
                )
                with self._connect() as conn:
                    conn.execute(
                        """
                        UPDATE engine_cooldowns
                        SET last_probe_at = ?, cooldown_until = ?, last_error = ?
                        WHERE engine = ?
                        """,
                        (current.isoformat(), new_until, str(rl_exc)[:1000], eng.value),
                    )
            except Exception as exc:
                limited, cooldown_sec = RateLimitDetector.detect_5h_limit(
                    str(exc), exit_code=1
                )
                with self._connect() as conn:
                    conn.execute(
                        """
                        UPDATE engine_cooldowns
                        SET last_probe_at = ?, last_error = ?
                        WHERE engine = ?
                        """,
                        (current.isoformat(), str(exc)[:1000], eng.value),
                    )
                if not limited:
                    logger.debug("Probe for %s encountered non-rate-limit error: %s", eng.value, exc)
                else:
                    _ = cooldown_sec

        return unlocked

    def _increment_engine_counter(self, engine: AIEngine, role: EffortRole) -> None:
        col = "completed_think" if role == EffortRole.THINK_XHIGH else "completed_exec"
        with self._connect() as conn:
            conn.execute(
                f"""
                UPDATE engine_cooldowns
                SET {col} = COALESCE({col}, 0) + 1
                WHERE engine = ?
                """,
                (engine.value,),
            )

    def pick_available_engine(
        self,
        preferred: AIEngine | None = None,
        now: datetime | None = None,
        exclude: set[AIEngine] | None = None,
    ) -> AIEngine:
        """Prioritize `preferred` (or round-robin when `None`), skipping `COOLDOWN_5H` engines.

        Failover order: `CLAUDE_PRO -> CODEX_PLUS -> ANTIGRAVITY_ULTRA -> TEMPLATE_FALLBACK`.
        """
        self.clear_expired_cooldowns(now=now)
        excluded = exclude or set()

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT engine, status FROM engine_cooldowns"
            ).fetchall()
        in_cooldown = {
            str(r["engine"])
            for r in rows
            if str(r["status"]) == ENGINE_STATUS_COOLDOWN_5H
        }

        if preferred is not None:
            pref = AIEngine(preferred)
            if pref == AIEngine.TEMPLATE_FALLBACK:
                return AIEngine.TEMPLATE_FALLBACK

            ordered = [pref] + [e for e in PRIMARY_ENGINES if e != pref]
            for candidate in ordered:
                if candidate not in excluded and candidate.value not in in_cooldown:
                    return candidate
            return AIEngine.TEMPLATE_FALLBACK

        # Round-robin across healthy primary engines
        total_primary = len(PRIMARY_ENGINES)
        start_idx = self._rr_index % total_primary
        for offset in range(total_primary):
            idx = (start_idx + offset) % total_primary
            candidate = PRIMARY_ENGINES[idx]
            if candidate not in excluded and candidate.value not in in_cooldown:
                self._rr_index = (idx + 1) % total_primary
                return candidate

        return AIEngine.TEMPLATE_FALLBACK

    # ------------------------------------------------------------------
    # SQLite Checkpoint Persistence & Resume
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_checkpoint_dict(row: sqlite3.Row) -> dict[str, Any]:
        raw_json = row["payload_json"] or "{}"
        try:
            parsed = json.loads(raw_json)
            if not isinstance(parsed, dict):
                parsed = {"value": parsed}
        except Exception:
            parsed = {}

        return {
            "task_id": str(row["task_id"]),
            "channel_id": str(row["channel_id"] or ""),
            "slot_time": str(row["slot_time"] or ""),
            "stage": str(row["stage"] or ""),
            "assigned_engine": row["assigned_engine"],
            "payload_json": raw_json,
            "payload": parsed,
            "updated_at": str(row["updated_at"] or ""),
        }

    def save_checkpoint(
        self,
        task_id: str,
        channel_id: str,
        slot_time: str,
        stage: CheckpointStage,
        payload: dict[str, Any],
        engine: AIEngine | None = None,
    ) -> None:
        """Persist task stage and JSON payload atomically in `task_checkpoints`."""
        stage_str = stage.value if isinstance(stage, CheckpointStage) else str(stage)
        engine_str = (
            engine.value
            if isinstance(engine, AIEngine)
            else (str(engine) if engine is not None else None)
        )
        payload_json = json.dumps(payload or {}, ensure_ascii=False, default=str)
        now_iso = utc_now().isoformat()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO task_checkpoints
                (task_id, channel_id, slot_time, stage, assigned_engine, payload_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    channel_id = excluded.channel_id,
                    slot_time = excluded.slot_time,
                    stage = excluded.stage,
                    assigned_engine = COALESCE(excluded.assigned_engine, task_checkpoints.assigned_engine),
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    task_id,
                    channel_id,
                    slot_time,
                    stage_str,
                    engine_str,
                    payload_json,
                    now_iso,
                ),
            )

    def get_checkpoint(self, task_id: str) -> dict[str, Any] | None:
        """Load a single task checkpoint by `task_id`, if present."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT task_id, channel_id, slot_time, stage, assigned_engine, payload_json, updated_at
                FROM task_checkpoints
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
        return self._row_to_checkpoint_dict(row) if row is not None else None

    def load_incomplete_checkpoints(self) -> list[dict[str, Any]]:
        """Return all interrupted tasks (`stage NOT IN ('COMPLETED', 'FAILED')`) ordered by `slot_time`."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT task_id, channel_id, slot_time, stage, assigned_engine, payload_json, updated_at
                FROM task_checkpoints
                WHERE stage NOT IN ('COMPLETED', 'FAILED')
                ORDER BY slot_time ASC, updated_at ASC
                """
            ).fetchall()
        return [self._row_to_checkpoint_dict(r) for r in rows]

    def get_pool_status(self, now: datetime | None = None) -> dict[str, Any]:
        """Return live telemetry for all engines and incomplete checkpoints."""
        current = _ensure_utc(now)
        self.clear_expired_cooldowns(now=current)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM engine_cooldowns ORDER BY engine ASC"
            ).fetchall()

        engines_info: dict[str, dict[str, Any]] = {}
        for r in rows:
            eng_name = str(r["engine"])
            until_dt = _parse_iso_utc(r["cooldown_until"])
            remaining_sec = (
                max(0, int((until_dt - current).total_seconds()))
                if until_dt and r["status"] == ENGINE_STATUS_COOLDOWN_5H
                else 0
            )
            try:
                configured = self.is_engine_configured(AIEngine(eng_name))
            except ValueError:
                configured = False

            engines_info[eng_name] = {
                "engine": eng_name,
                "status": r["status"],
                "configured": configured,
                "cooldown_until": r["cooldown_until"],
                "remaining_cooldown_sec": remaining_sec,
                "last_probe_at": r["last_probe_at"],
                "fail_count": int(r["fail_count"] or 0),
                "completed_think": int(r["completed_think"] or 0),
                "completed_exec": int(r["completed_exec"] or 0),
                "last_error": r["last_error"] or "",
            }

        incomplete = self.load_incomplete_checkpoints()
        return {
            "engines": engines_info,
            "incomplete_checkpoints": len(incomplete),
            "checked_at": current.isoformat(),
        }

    # ------------------------------------------------------------------
    # Resilient Checkpointed Task Execution (`THINK_XHIGH` -> `EXECUTE_LOW`)
    # ------------------------------------------------------------------

    async def _run_role_with_failover(
        self,
        *,
        task_id: str,
        channel_id: str,
        slot_time: str,
        checkpoint_stage_on_limit: CheckpointStage,
        payload: dict[str, Any],
        role: EffortRole,
        prompt: str,
        preferred_engine: AIEngine | None,
        runner_override: Callable[..., Any] | None,
        failovers: list[dict[str, Any]],
    ) -> tuple[str, AIEngine]:
        skipped_unconfigured: set[AIEngine] = set()

        for _attempt in range(len(FAILOVER_CHAIN) + 1):
            engine = self.pick_available_engine(
                preferred=preferred_engine,
                exclude=skipped_unconfigured,
            )

            try:
                if runner_override is not None:
                    output = await self._invoke_override(
                        runner_override, engine, role, prompt
                    )
                else:
                    output = await self.execute_role(
                        engine=engine, role=role, prompt=prompt
                    )

                self._increment_engine_counter(engine, role)
                return output, engine

            except _EngineUnavailableError as unavail_exc:
                if engine == AIEngine.TEMPLATE_FALLBACK:
                    fallback_out = self._execute_template_fallback(role=role, prompt=prompt)
                    self._increment_engine_counter(AIEngine.TEMPLATE_FALLBACK, role)
                    return fallback_out, AIEngine.TEMPLATE_FALLBACK

                skipped_unconfigured.add(engine)
                logger.debug(
                    "Engine %s unconfigured during %s (%s); failing over seamlessly.",
                    engine.value,
                    role.value,
                    unavail_exc,
                )
                continue

            except Exception as exc:
                if isinstance(exc, RateLimitError):
                    is_limited = True
                    cooldown_sec = exc.cooldown_seconds
                else:
                    is_limited, cooldown_sec = RateLimitDetector.detect_5h_limit(
                        str(exc), exit_code=1
                    )

                if not is_limited:
                    raise

                if engine == AIEngine.TEMPLATE_FALLBACK:
                    fallback_out = self._execute_template_fallback(role=role, prompt=prompt)
                    self._increment_engine_counter(AIEngine.TEMPLATE_FALLBACK, role)
                    return fallback_out, AIEngine.TEMPLATE_FALLBACK

                reason = str(exc)
                self.mark_engine_5h_cooldown(
                    engine=engine,
                    cooldown_seconds=cooldown_sec,
                    reason=reason,
                )
                failover_event = {
                    "engine": engine.value,
                    "role": role.value,
                    "cooldown_seconds": cooldown_sec,
                    "reason": reason[:300],
                }
                failovers.append(failover_event)
                payload["failovers"] = list(failovers)
                self.save_checkpoint(
                    task_id=task_id,
                    channel_id=channel_id,
                    slot_time=slot_time,
                    stage=checkpoint_stage_on_limit,
                    payload=payload,
                    engine=engine,
                )

        # Ultimate guarantee: pipeline never stops even if all primary engines exhausted
        fallback_out = self._execute_template_fallback(role=role, prompt=prompt)
        self._increment_engine_counter(AIEngine.TEMPLATE_FALLBACK, role)
        return fallback_out, AIEngine.TEMPLATE_FALLBACK

    async def run_checkpointed_task(
        self,
        task_id: str,
        channel_id: str,
        slot_time: str,
        think_prompt: str,
        execute_prompt_fn: Callable[[str], str],
        preferred_engine: AIEngine = AIEngine.CLAUDE_PRO,
        runner_override: Callable[..., Any] | None = None,
        final_stage: CheckpointStage = CheckpointStage.COMPLETED,
    ) -> dict[str, Any]:
        """Run or resume a two-phase (`THINK_XHIGH` -> `EXECUTE_LOW`) task with instant 5h failover."""
        if runner_override is None:
            await self.probe_cooldown_engines()
        else:
            self.clear_expired_cooldowns()

        existing = self.get_checkpoint(task_id)
        resumed = existing is not None
        skipped_think = False

        payload: dict[str, Any] = dict(existing["payload"]) if existing else {}
        failovers: list[dict[str, Any]] = list(payload.get("failovers") or [])
        current_stage = str(existing["stage"]).upper() if existing else ""

        if existing and current_stage == CheckpointStage.COMPLETED.value:
            return {
                **payload,
                "task_id": task_id,
                "channel_id": existing["channel_id"] or channel_id,
                "slot_time": existing["slot_time"] or slot_time,
                "stage": CheckpointStage.COMPLETED.value,
                "assigned_engine": existing["assigned_engine"] or payload.get("execute_engine"),
                "resumed": True,
                "skipped_think": True,
                "updated_at": existing["updated_at"],
            }

        if existing and current_stage in (
            CheckpointStage.READY_TO_RENDER.value,
            CheckpointStage.READY_TO_PUBLISH.value,
        ) and payload.get("execute_output") is not None:
            if final_stage == CheckpointStage.COMPLETED:
                self.save_checkpoint(
                    task_id=task_id,
                    channel_id=channel_id,
                    slot_time=slot_time,
                    stage=CheckpointStage.COMPLETED,
                    payload=payload,
                )
            return {
                **payload,
                "task_id": task_id,
                "channel_id": channel_id,
                "slot_time": slot_time,
                "stage": (
                    CheckpointStage.COMPLETED.value
                    if final_stage == CheckpointStage.COMPLETED
                    else current_stage
                ),
                "assigned_engine": existing["assigned_engine"] or payload.get("execute_engine"),
                "resumed": True,
                "skipped_think": True,
                "updated_at": utc_now().isoformat(),
            }

        # Stage 1: THINK_XHIGH (skip if checkpoint already reached READY_TO_EXECUTE)
        cached_think = (
            payload.get("think_output")
            or payload.get("think_result")
            or payload.get("think")
        )
        if existing and current_stage == CheckpointStage.READY_TO_EXECUTE.value and cached_think is not None:
            think_output = str(cached_think)
            think_engine_str = str(
                payload.get("think_engine")
                or existing.get("assigned_engine")
                or preferred_engine.value
            )
            skipped_think = True
        else:
            if not existing:
                self.save_checkpoint(
                    task_id=task_id,
                    channel_id=channel_id,
                    slot_time=slot_time,
                    stage=CheckpointStage.PENDING_THINK,
                    payload=payload,
                    engine=preferred_engine,
                )

            think_output, think_engine = await self._run_role_with_failover(
                task_id=task_id,
                channel_id=channel_id,
                slot_time=slot_time,
                checkpoint_stage_on_limit=CheckpointStage.PENDING_THINK,
                payload=payload,
                role=EffortRole.THINK_XHIGH,
                prompt=think_prompt,
                preferred_engine=preferred_engine,
                runner_override=runner_override,
                failovers=failovers,
            )
            think_engine_str = think_engine.value
            payload["think_output"] = think_output
            payload["think_engine"] = think_engine_str
            payload["failovers"] = list(failovers)

            self.save_checkpoint(
                task_id=task_id,
                channel_id=channel_id,
                slot_time=slot_time,
                stage=CheckpointStage.READY_TO_EXECUTE,
                payload=payload,
                engine=think_engine,
            )

        # Stage 2: EXECUTE_LOW
        execute_prompt = execute_prompt_fn(think_output)
        execute_output, execute_engine = await self._run_role_with_failover(
            task_id=task_id,
            channel_id=channel_id,
            slot_time=slot_time,
            checkpoint_stage_on_limit=CheckpointStage.READY_TO_EXECUTE,
            payload=payload,
            role=EffortRole.EXECUTE_LOW,
            prompt=execute_prompt,
            preferred_engine=preferred_engine,
            runner_override=runner_override,
            failovers=failovers,
        )

        execute_engine_str = execute_engine.value
        payload["execute_output"] = execute_output
        payload["execute_engine"] = execute_engine_str
        payload["failovers"] = list(failovers)

        self.save_checkpoint(
            task_id=task_id,
            channel_id=channel_id,
            slot_time=slot_time,
            stage=final_stage,
            payload=payload,
            engine=execute_engine,
        )

        now_iso = utc_now().isoformat()
        return {
            "task_id": task_id,
            "channel_id": channel_id,
            "slot_time": slot_time,
            "stage": final_stage.value,
            "think_engine": think_engine_str,
            "execute_engine": execute_engine_str,
            "assigned_engine": execute_engine_str,
            "think_output": think_output,
            "execute_output": execute_output,
            "failovers": failovers,
            "resumed": resumed,
            "skipped_think": skipped_think,
            "updated_at": now_iso,
        }
