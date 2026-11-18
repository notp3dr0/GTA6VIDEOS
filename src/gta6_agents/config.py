"""Configuration loader merging .env and YAML files for GTA 6 Multi-Agent System."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field


def get_project_root() -> Path:
    env_root = os.getenv("GTA6_PROJECT_ROOT")
    if env_root:
        return Path(env_root).resolve()
    return Path(__file__).resolve().parents[2]


class AppConfig(BaseModel):
    root_dir: Path = Field(default_factory=get_project_root)
    dry_run: bool = True
    autopilot_mode: str = "semi_auto"
    timezone: str = "America/Sao_Paulo"

    llm_provider: str = "gemini"
    llm_model: str = "gemini-2.5-flash"
    gemini_api_key: str = ""
    openai_api_key: str = ""
    groq_api_key: str = ""

    real_oficial_enabled: bool = True
    real_oficial_api_key: str = ""
    real_oficial_mcp_token: str = ""
    real_oficial_base_url: str = "https://api.realoficial.com.br"
    real_oficial_campaign_slug: str = "gta-vi-youtube"
    real_oficial_ref_vid: str = "a56ec50b-725e-4c73-a353-4e3b2e80678b"
    real_oficial_daily_credit_limit: int = 150
    real_oficial_launcher_id: str = ""
    real_oficial_allow_spend_credits: bool = False

    youtube_enabled: bool = True
    youtube_client_id: str = ""
    youtube_client_secret: str = ""
    youtube_refresh_token: str = ""
    youtube_channel_handle: str = ""

    x_enabled: bool = False
    x_api_key: str = ""
    x_api_secret: str = ""
    x_access_token: str = ""
    x_access_token_secret: str = ""
    x_bearer_token: str = ""

    buffer_enabled: bool = False
    buffer_access_token: str = ""
    buffer_profile_ids: list[str] = Field(default_factory=list)
    webhook_publisher_url: str = ""

    post_for_me_api_key: str = ""
    post_for_me_base_url: str = "https://api.postforme.dev/v1"

    instagram_access_token: str = ""
    instagram_ig_user_id: str = ""
    tiktok_access_token: str = ""

    voiceover_mode: str = "edge_tts"
    edge_tts_voice_pt: str = "pt-BR-AntonioNeural"
    edge_tts_voice_en: str = "en-US-ChristopherNeural"
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    discord_webhook_url: str = ""

    settings: dict[str, Any] = Field(default_factory=dict)
    campaign: dict[str, Any] = Field(default_factory=dict)

    @property
    def memory_dir(self) -> Path:
        p = self.root_dir / "memory"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def output_dir(self) -> Path:
        p = self.root_dir / "output"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def work_dir(self) -> Path:
        p = self.root_dir / "work"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def data_dir(self) -> Path:
        p = self.root_dir / "data"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def logs_dir(self) -> Path:
        p = self.root_dir / "logs"
        p.mkdir(parents=True, exist_ok=True)
        return p


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def load_config(root_dir: Path | None = None) -> AppConfig:
    root = (root_dir or get_project_root()).resolve()
    env_path = root / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)

    settings_path = root / "config" / "settings.yaml"
    campaign_path = root / "config" / "campaign_realoficial.yaml"

    settings_data: dict[str, Any] = {}
    if settings_path.exists():
        settings_data = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}

    campaign_data: dict[str, Any] = {}
    if campaign_path.exists():
        campaign_data = yaml.safe_load(campaign_path.read_text(encoding="utf-8")) or {}

    raw_profiles = os.getenv("BUFFER_PROFILE_IDS", "")
    profile_ids = [p.strip() for p in raw_profiles.split(",") if p.strip()]

    return AppConfig(
        root_dir=root,
        dry_run=_env_bool("DRY_RUN", True),
        autopilot_mode=os.getenv("AUTOPILOT_MODE", "semi_auto"),
        timezone=os.getenv("TIMEZONE", settings_data.get("page", {}).get("timezone", "America/Sao_Paulo")),
        llm_provider=os.getenv("LLM_PROVIDER", "gemini"),
        llm_model=os.getenv("LLM_MODEL", "gemini-2.5-flash"),
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        groq_api_key=os.getenv("GROQ_API_KEY", ""),
        real_oficial_enabled=_env_bool("REAL_OFICIAL_ENABLED", True),
        real_oficial_api_key=os.getenv("REAL_OFICIAL_API_KEY", ""),
        real_oficial_mcp_token=os.getenv("REAL_OFICIAL_MCP_TOKEN", ""),
        real_oficial_base_url=os.getenv("REAL_OFICIAL_BASE_URL", "https://api.realoficial.com.br"),
        real_oficial_campaign_slug=os.getenv("REAL_OFICIAL_CAMPAIGN_SLUG", "gta-vi-youtube"),
        real_oficial_ref_vid=os.getenv("REAL_OFICIAL_REF_VID", "a56ec50b-725e-4c73-a353-4e3b2e80678b"),
        real_oficial_daily_credit_limit=int(os.getenv("REAL_OFICIAL_DAILY_CREDIT_LIMIT", "150")),
        real_oficial_launcher_id=os.getenv("REAL_OFICIAL_LAUNCHER_ID", ""),
        real_oficial_allow_spend_credits=_env_bool("REAL_OFICIAL_ALLOW_SPEND_CREDITS", False),
        youtube_enabled=_env_bool("YOUTUBE_ENABLED", True),
        youtube_client_id=os.getenv("YOUTUBE_CLIENT_ID", ""),
        youtube_client_secret=os.getenv("YOUTUBE_CLIENT_SECRET", ""),
        youtube_refresh_token=os.getenv("YOUTUBE_REFRESH_TOKEN", ""),
        youtube_channel_handle=os.getenv("YOUTUBE_CHANNEL_HANDLE", ""),
        x_enabled=_env_bool("X_ENABLED", False),
        x_api_key=os.getenv("X_API_KEY", ""),
        x_api_secret=os.getenv("X_API_SECRET", ""),
        x_access_token=os.getenv("X_ACCESS_TOKEN", ""),
        x_access_token_secret=os.getenv("X_ACCESS_TOKEN_SECRET", ""),
        x_bearer_token=os.getenv("X_BEARER_TOKEN", ""),
        buffer_enabled=_env_bool("BUFFER_ENABLED", False),
        buffer_access_token=os.getenv("BUFFER_ACCESS_TOKEN", ""),
        buffer_profile_ids=profile_ids,
        webhook_publisher_url=os.getenv("WEBHOOK_PUBLISHER_URL", ""),
        post_for_me_api_key=(
            os.getenv("POST_FOR_ME_API_KEY", "") or os.getenv("POSTFORME_API_KEY", "")
        ),
        post_for_me_base_url=os.getenv("POST_FOR_ME_BASE_URL", "https://api.postforme.dev/v1"),
        instagram_access_token=os.getenv("INSTAGRAM_ACCESS_TOKEN", ""),
        instagram_ig_user_id=os.getenv("INSTAGRAM_IG_USER_ID", ""),
        tiktok_access_token=os.getenv("TIKTOK_ACCESS_TOKEN", ""),
        voiceover_mode=os.getenv("VOICEOVER_MODE", "edge_tts"),
        edge_tts_voice_pt=os.getenv("EDGE_TTS_VOICE_PT", "pt-BR-AntonioNeural"),
        edge_tts_voice_en=os.getenv("EDGE_TTS_VOICE_EN", "en-US-ChristopherNeural"),
        elevenlabs_api_key=os.getenv("ELEVENLABS_API_KEY", ""),
        elevenlabs_voice_id=os.getenv("ELEVENLABS_VOICE_ID", ""),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", ""),
        settings=settings_data,
        campaign=campaign_data,
    )
