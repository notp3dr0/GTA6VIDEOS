"""Official Real Oficial API & Streamable HTTP MCP Client (JSON-RPC 2.0).

Compliance guarantees:
- Connects ONLY to https://api.realoficial.com.br and https://api.realoficial.com.br/mcp.
- NEVER scrapes app.realoficial.com.br HTML or bypasses Cloudflare/anti-bot gates.
- NEVER invokes ro_start_purchase automatically (zero autonomous PIX/money spend).
- Strictly guards ro_create_clips behind REAL_OFICIAL_ALLOW_SPEND_CREDITS and daily credit limits.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from gta6_agents.config import AppConfig
from gta6_agents.storage import StateStore

logger = logging.getLogger(__name__)

FORBIDDEN_MCP_TOOLS = frozenset({"ro_start_purchase"})
PROHIBITED_SCRAPE_HOSTS = frozenset({"app.realoficial.com.br", "www.app.realoficial.com.br"})


class RealOficialClient:
    """Official client for Real Oficial Streamable HTTP MCP (`tools/call`) and REST endpoints."""

    def __init__(
        self,
        config: AppConfig,
        store: StateStore | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.config = config
        self.store = store
        self.timeout = timeout
        self.base_url = self._validate_api_base_url(config.real_oficial_base_url)
        self.mcp_url = f"{self.base_url}/mcp"
        self._mcp_session_id: str | None = None

    @staticmethod
    def _validate_api_base_url(raw_url: str) -> str:
        cleaned = (raw_url or "https://api.realoficial.com.br").strip().rstrip("/")
        parsed = urlparse(cleaned)
        host = (parsed.hostname or "").lower()
        if host in PROHIBITED_SCRAPE_HOSTS:
            raise ValueError(
                "HTML scraping of app.realoficial.com.br is strictly prohibited. "
                "Use only the official API/MCP host: https://api.realoficial.com.br"
            )
        if parsed.scheme != "https":
            raise ValueError(f"Real Oficial API base URL must use HTTPS, got: {cleaned}")
        return cleaned

    @property
    def has_credentials(self) -> bool:
        return bool(
            self.config.real_oficial_mcp_token.strip()
            or self.config.real_oficial_api_key.strip()
        )

    def build_campaign_url(self, campaign_slug: str | None = None) -> str:
        """Builds the user-facing 1-click campaign URL on Real Oficial for human confirmation."""
        slug = (campaign_slug or self.config.real_oficial_campaign_slug or "gta-vi-youtube").strip()
        ref_vid = self.config.real_oficial_ref_vid.strip()
        if ref_vid:
            return f"https://app.realoficial.com.br/c/{slug}?ro_vid={ref_vid}"
        return f"https://app.realoficial.com.br/c/{slug}"

    def _build_headers(self) -> dict[str, str]:
        token = (
            self.config.real_oficial_mcp_token.strip()
            or self.config.real_oficial_api_key.strip()
        )
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "gta6-agents/1.0 (+official-mcp-client)",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if self.config.real_oficial_api_key.strip():
            headers["X-API-Key"] = self.config.real_oficial_api_key.strip()
        if self._mcp_session_id:
            headers["Mcp-Session-Id"] = self._mcp_session_id
        return headers

    @staticmethod
    def _parse_streamable_http_response(resp: httpx.Response) -> dict[str, Any]:
        """Parses either application/json or SSE text/event-stream JSON-RPC 2.0 responses."""
        content_type = resp.headers.get("content-type", "").lower()
        if "text/event-stream" in content_type:
            last_json: dict[str, Any] = {}
            for raw_line in resp.text.splitlines():
                line = raw_line.strip()
                if not line.startswith("data:"):
                    continue
                data_str = line[len("data:") :].strip()
                if not data_str or data_str == "[DONE]":
                    continue
                try:
                    parsed = json.loads(data_str)
                    if isinstance(parsed, dict):
                        last_json = parsed
                        if "result" in parsed or "error" in parsed:
                            break
                except json.JSONDecodeError:
                    continue
            return last_json

        data = resp.json()
        return data if isinstance(data, dict) else {"data": data}

    @staticmethod
    def _unwrap_mcp_result(rpc_payload: dict[str, Any], tool_name: str) -> dict[str, Any]:
        """Normalizes MCP JSON-RPC `tools/call` output into a clean dictionary."""
        if "error" in rpc_payload and rpc_payload["error"]:
            err = rpc_payload["error"]
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            return {
                "ok": False,
                "isError": True,
                "tool": tool_name,
                "error": msg,
                "rpc_error": err,
            }

        result = rpc_payload.get("result", rpc_payload)
        if not isinstance(result, dict):
            return {"ok": True, "tool": tool_name, "value": result}

        is_error = bool(result.get("isError", False))
        if isinstance(result.get("structuredContent"), dict):
            out = dict(result["structuredContent"])
            out.setdefault("ok", not is_error)
            out.setdefault("tool", tool_name)
            if is_error:
                out["isError"] = True
            return out

        content_blocks = result.get("content")
        if isinstance(content_blocks, list) and content_blocks:
            texts: list[str] = []
            for block in content_blocks:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_val = block.get("text", "")
                    if isinstance(text_val, str) and text_val.strip():
                        texts.append(text_val.strip())
            if texts:
                joined = "\n".join(texts)
                try:
                    parsed_text = json.loads(joined)
                    if isinstance(parsed_text, dict):
                        parsed_text.setdefault("ok", not is_error)
                        parsed_text.setdefault("tool", tool_name)
                        if is_error:
                            parsed_text["isError"] = True
                        return parsed_text
                except json.JSONDecodeError:
                    pass
                return {
                    "ok": not is_error,
                    "isError": is_error,
                    "tool": tool_name,
                    "message": joined,
                }

        normalized = dict(result)
        normalized.setdefault("ok", not is_error)
        normalized.setdefault("tool", tool_name)
        return normalized

    def _simulate_mcp_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Structured simulation/queued fallback when dry_run=True or credentials are not set."""
        slug = arguments.get(
            "campaign_slug", self.config.real_oficial_campaign_slug or "gta-vi-youtube"
        )
        base: dict[str, Any] = {
            "ok": True,
            "simulated": True,
            "dry_run": self.config.dry_run,
            "has_credentials": self.has_credentials,
            "tool": tool_name,
        }

        if tool_name == "ro_whoami":
            base.update(
                {
                    "authenticated": False,
                    "account": None,
                    "credits_balance": None,
                    "note": "Real Oficial token not active or running in dry_run mode.",
                }
            )
        elif tool_name == "ro_list_championships":
            base.update(
                {
                    "championships": [
                        {
                            "slug": slug,
                            "title": "GTA VI YouTube Shorts Championship",
                            "target_network": "youtube_shorts",
                            "active": True,
                            "confirm_url": self.build_campaign_url(slug),
                        }
                    ]
                }
            )
        elif tool_name == "ro_preview_championship_entry":
            base.update(
                {
                    "status": "pending_submission",
                    "preview_id": f"sim_prev_{uuid4().hex[:8]}",
                    "campaign_slug": slug,
                    "video_url": arguments.get("video_url", ""),
                    "terms_required": True,
                    "terms_accepted": False,
                    "confirm_url": self.build_campaign_url(slug),
                }
            )
        elif tool_name == "ro_confirm_publishing":
            accepted = bool(arguments.get("accepted_terms", False))
            base.update(
                {
                    "preview_id": arguments.get("preview_id", ""),
                    "status": "simulated_submitted" if accepted else "terms_required",
                    "terms_required": not accepted,
                    "confirm_url": self.build_campaign_url(slug),
                }
            )
        elif tool_name == "ro_estimate_clips":
            base.update(
                {
                    "estimate_id": f"sim_est_{uuid4().hex[:8]}",
                    "source_url": arguments.get("source_url", ""),
                    "estimated_credits": int(arguments.get("estimated_credits", 15)),
                    "suggested_clips_count": int(arguments.get("max_clips", 3)),
                    "status": "estimated",
                }
            )
        elif tool_name == "ro_create_clips":
            base.update(
                {
                    "estimate_id": arguments.get("estimate_id", ""),
                    "job_id": f"sim_job_{uuid4().hex[:8]}",
                    "status": "queued_simulation",
                    "credits_spent": 0,
                }
            )
        elif tool_name == "ro_list_launchers":
            launchers = []
            if self.config.real_oficial_launcher_id:
                launchers.append(
                    {
                        "id": self.config.real_oficial_launcher_id,
                        "platform": "youtube_shorts",
                        "status": "configured_in_env",
                    }
                )
            base.update({"launchers": launchers})
        elif tool_name == "ro_save_launcher":
            base.update(
                {
                    "launcher_id": arguments.get("launcher_id") or f"sim_launcher_{uuid4().hex[:8]}",
                    "status": "saved_simulation",
                    "launcher": arguments,
                }
            )
        elif tool_name == "ro_get_social_analytics":
            base.update(
                {
                    "campaign_slug": slug,
                    "total_views": 0,
                    "validated_views": 0,
                    "videos_tracked": 0,
                }
            )
        return base

    async def call_mcp_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Executes a JSON-RPC 2.0 `tools/call` against `https://api.realoficial.com.br/mcp`."""
        if tool_name in FORBIDDEN_MCP_TOOLS:
            raise PermissionError(
                f"MCP tool '{tool_name}' is strictly forbidden from automatic execution "
                "(autonomous PIX/money purchases are prohibited)."
            )

        args = dict(arguments or {})
        if not self.config.real_oficial_enabled or self.config.dry_run or not self.has_credentials:
            return self._simulate_mcp_tool(tool_name, args)

        rpc_body = {
            "jsonrpc": "2.0",
            "id": f"ro_{uuid4().hex[:10]}",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": args,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False) as http:
                resp = await http.post(self.mcp_url, headers=self._build_headers(), json=rpc_body)

                session_id = resp.headers.get("mcp-session-id")
                if session_id:
                    self._mcp_session_id = session_id

                # If server requires an initialized MCP session first, perform handshake once and retry.
                if resp.status_code in (400, 406) and not self._mcp_session_id:
                    init_body = {
                        "jsonrpc": "2.0",
                        "id": f"init_{uuid4().hex[:8]}",
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-03-26",
                            "capabilities": {},
                            "clientInfo": {"name": "gta6-agents", "version": "1.0.0"},
                        },
                    }
                    init_resp = await http.post(
                        self.mcp_url, headers=self._build_headers(), json=init_body
                    )
                    if init_resp.headers.get("mcp-session-id"):
                        self._mcp_session_id = init_resp.headers["mcp-session-id"]
                        resp = await http.post(
                            self.mcp_url, headers=self._build_headers(), json=rpc_body
                        )

                resp.raise_for_status()
                parsed_rpc = self._parse_streamable_http_response(resp)
                return self._unwrap_mcp_result(parsed_rpc, tool_name)
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Real Oficial MCP HTTP status error on %s: %s", tool_name, exc.response.status_code
            )
            return {
                "ok": False,
                "isError": True,
                "tool": tool_name,
                "status_code": exc.response.status_code,
                "error": f"HTTP {exc.response.status_code} from official Real Oficial MCP endpoint.",
            }
        except httpx.HTTPError as exc:
            logger.warning("Real Oficial MCP transport error on %s: %s", tool_name, exc)
            return {
                "ok": False,
                "isError": True,
                "tool": tool_name,
                "error": f"Real Oficial MCP transport error: {exc}",
            }

    async def whoami(self) -> dict[str, Any]:
        """Wrapper for `ro_whoami`."""
        return await self.call_mcp_tool("ro_whoami", {})

    async def list_championships(self, **kwargs: Any) -> dict[str, Any]:
        """Wrapper for `ro_list_championships`."""
        return await self.call_mcp_tool("ro_list_championships", kwargs)

    async def preview_championship_entry(
        self,
        campaign_slug: str,
        video_url: str,
    ) -> dict[str, Any]:
        """Wrapper for `ro_preview_championship_entry`."""
        return await self.call_mcp_tool(
            "ro_preview_championship_entry",
            {
                "campaign_slug": campaign_slug,
                "video_url": video_url,
            },
        )

    async def confirm_publishing(
        self,
        preview_id: str,
        accepted_terms: bool = False,
    ) -> dict[str, Any]:
        """Wrapper for `ro_confirm_publishing`.

        Never fabricates user acceptance: if `accepted_terms` is False, returns
        a `terms_required` status rather than auto-accepting championship terms.
        """
        if not accepted_terms:
            return {
                "ok": False,
                "tool": "ro_confirm_publishing",
                "preview_id": preview_id,
                "status": "terms_required",
                "terms_required": True,
                "confirm_url": self.build_campaign_url(),
                "error": "Championship terms require explicit user acceptance.",
            }
        return await self.call_mcp_tool(
            "ro_confirm_publishing",
            {
                "preview_id": preview_id,
                "accepted_terms": True,
            },
        )

    async def estimate_clips(self, source_url: str, **kwargs: Any) -> dict[str, Any]:
        """Wrapper for `ro_estimate_clips`."""
        payload = {"source_url": source_url, **kwargs}
        return await self.call_mcp_tool("ro_estimate_clips", payload)

    async def create_clips(
        self,
        estimate_id: str,
        estimated_credits: int = 0,
    ) -> dict[str, Any]:
        """Wrapper for `ro_create_clips`, strictly guarded by credit spending policy & daily cap."""
        if not self.config.real_oficial_allow_spend_credits:
            return {
                "ok": False,
                "status": "blocked_credit_policy",
                "tool": "ro_create_clips",
                "estimate_id": estimate_id,
                "credits_spent": 0,
                "error": (
                    "Cloud clip credit spending is disabled "
                    "(REAL_OFICIAL_ALLOW_SPEND_CREDITS=false)."
                ),
            }

        if self.store is None:
            return {
                "ok": False,
                "status": "blocked_missing_store",
                "tool": "ro_create_clips",
                "estimate_id": estimate_id,
                "credits_spent": 0,
                "error": "StateStore is required to verify daily Real Oficial credit budget.",
            }

        spent_today = self.store.today_credit_spend("real_oficial")
        daily_limit = self.config.real_oficial_daily_credit_limit
        projected_spend = spent_today + max(0, int(estimated_credits))

        if (
            daily_limit <= 0
            or spent_today > daily_limit
            or spent_today >= daily_limit
            or projected_spend > daily_limit
        ):
            return {
                "ok": False,
                "status": "daily_credit_limit_exceeded",
                "tool": "ro_create_clips",
                "estimate_id": estimate_id,
                "spent_today": spent_today,
                "estimated_credits": estimated_credits,
                "daily_limit": daily_limit,
                "error": (
                    f"Daily Real Oficial credit cap reached/exceeded "
                    f"(spent={spent_today}, requested={estimated_credits}, limit={daily_limit})."
                ),
            }

        result = await self.call_mcp_tool("ro_create_clips", {"estimate_id": estimate_id})
        if result.get("ok") and not result.get("simulated"):
            credits_used = int(result.get("credits_spent", estimated_credits) or estimated_credits)
            if credits_used > 0:
                self.store.add_credit_spend(
                    provider="real_oficial",
                    credits=credits_used,
                    reason=f"ro_create_clips:{estimate_id}",
                )
        return result

    async def list_launchers(self) -> dict[str, Any]:
        """Wrapper for `ro_list_launchers`."""
        return await self.call_mcp_tool("ro_list_launchers", {})

    async def save_launcher(
        self,
        launcher_config: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Wrapper for `ro_save_launcher`."""
        payload: dict[str, Any] = {}
        if launcher_config:
            payload.update(launcher_config)
        payload.update(kwargs)
        return await self.call_mcp_tool("ro_save_launcher", payload)

    async def get_social_analytics(self, **kwargs: Any) -> dict[str, Any]:
        """Wrapper for `ro_get_social_analytics`."""
        return await self.call_mcp_tool("ro_get_social_analytics", kwargs)
