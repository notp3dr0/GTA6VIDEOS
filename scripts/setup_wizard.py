#!/usr/bin/env python3
"""Assistente interativo em Português (PT-BR) para configurar o `.env` antes dos 5 dias fora."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_ENV_VALUES: dict[str, str] = {
    "DRY_RUN": "true",
    "AUTOPILOT_MODE": "semi_auto",
    "TIMEZONE": "America/Sao_Paulo",
    "LLM_PROVIDER": "gemini",
    "GEMINI_API_KEY": "",
    "OPENAI_API_KEY": "",
    "GROQ_API_KEY": "",
    "LLM_MODEL": "gemini-2.5-flash",
    "REAL_OFICIAL_ENABLED": "true",
    "REAL_OFICIAL_API_KEY": "",
    "REAL_OFICIAL_MCP_TOKEN": "",
    "REAL_OFICIAL_BASE_URL": "https://api.realoficial.com.br",
    "REAL_OFICIAL_CAMPAIGN_SLUG": "gta-vi-youtube",
    "REAL_OFICIAL_REF_VID": "a56ec50b-725e-4c73-a353-4e3b2e80678b",
    "REAL_OFICIAL_DAILY_CREDIT_LIMIT": "150",
    "REAL_OFICIAL_LAUNCHER_ID": "",
    "REAL_OFICIAL_ALLOW_SPEND_CREDITS": "false",
    "YOUTUBE_ENABLED": "true",
    "YOUTUBE_CLIENT_ID": "",
    "YOUTUBE_CLIENT_SECRET": "",
    "YOUTUBE_REFRESH_TOKEN": "",
    "YOUTUBE_CHANNEL_HANDLE": "",
    "X_ENABLED": "false",
    "X_API_KEY": "",
    "X_API_SECRET": "",
    "X_ACCESS_TOKEN": "",
    "X_ACCESS_TOKEN_SECRET": "",
    "X_BEARER_TOKEN": "",
    "BUFFER_ENABLED": "false",
    "BUFFER_ACCESS_TOKEN": "",
    "BUFFER_PROFILE_IDS": "",
    "WEBHOOK_PUBLISHER_URL": "",
    "INSTAGRAM_ACCESS_TOKEN": "",
    "INSTAGRAM_IG_USER_ID": "",
    "TIKTOK_ACCESS_TOKEN": "",
    "VOICEOVER_MODE": "edge_tts",
    "EDGE_TTS_VOICE_PT": "pt-BR-AntonioNeural",
    "EDGE_TTS_VOICE_EN": "en-US-ChristopherNeural",
    "ELEVENLABS_API_KEY": "",
    "ELEVENLABS_VOICE_ID": "",
    "TELEGRAM_BOT_TOKEN": "",
    "TELEGRAM_CHAT_ID": "",
    "DISCORD_WEBHOOK_URL": "",
}


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _parse_existing_env(env_path: Path) -> dict[str, str]:
    values = dict(DEFAULT_ENV_VALUES)
    if not env_path.exists():
        return values

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        # Strip inline comment only when preceded by whitespace and not inside quotes
        val = val.strip()
        if " #" in val and not (val.startswith('"') or val.startswith("'")):
            val = val.split(" #", 1)[0].strip()
        val = val.strip('"').strip("'")
        if key:
            values[key] = val
    return values


def _mask_secret(val: str) -> str:
    if not val:
        return "vazio"
    if len(val) <= 6:
        return "******"
    return f"{val[:3]}...{val[-3:]}"


def _ask(prompt_label: str, current: str, secret: bool = False) -> str:
    shown = _mask_secret(current) if secret else (current or "vazio")
    try:
        raw = input(f"  {prompt_label} [{shown}]: ").strip()
    except EOFError:
        return current
    if not raw:
        return current
    return raw


def _ask_yes_no(prompt_label: str, default_yes: bool = True) -> bool:
    hint = "S/n" if default_yes else "s/N"
    try:
        raw = input(f"  {prompt_label} [{hint}]: ").strip().lower()
    except EOFError:
        return default_yes
    if not raw:
        return default_yes
    return raw in ("s", "sim", "y", "yes", "1", "true")


def _format_env_file(values: dict[str, str]) -> str:
    return f"""# ==============================================================================
# MODO DE EXECUCAO (Gerado pelo setup_wizard.py)
# ==============================================================================
DRY_RUN={values['DRY_RUN']}
AUTOPILOT_MODE={values['AUTOPILOT_MODE']}
TIMEZONE={values['TIMEZONE']}

# ==============================================================================
# LLM PARA AGENTES DE ROTEIRO, COPY E VERIFICACAO
# ==============================================================================
LLM_PROVIDER={values['LLM_PROVIDER']}
GEMINI_API_KEY={values['GEMINI_API_KEY']}
OPENAI_API_KEY={values['OPENAI_API_KEY']}
GROQ_API_KEY={values['GROQ_API_KEY']}
LLM_MODEL={values['LLM_MODEL']}

# ==============================================================================
# CAMPANHA REAL OFICIAL (https://app.realoficial.com.br/c/gta-vi-youtube)
# ==============================================================================
REAL_OFICIAL_ENABLED={values['REAL_OFICIAL_ENABLED']}
REAL_OFICIAL_API_KEY={values['REAL_OFICIAL_API_KEY']}
REAL_OFICIAL_MCP_TOKEN={values['REAL_OFICIAL_MCP_TOKEN']}
REAL_OFICIAL_BASE_URL={values['REAL_OFICIAL_BASE_URL']}
REAL_OFICIAL_CAMPAIGN_SLUG={values['REAL_OFICIAL_CAMPAIGN_SLUG']}
REAL_OFICIAL_REF_VID={values['REAL_OFICIAL_REF_VID']}
REAL_OFICIAL_DAILY_CREDIT_LIMIT={values['REAL_OFICIAL_DAILY_CREDIT_LIMIT']}
REAL_OFICIAL_LAUNCHER_ID={values['REAL_OFICIAL_LAUNCHER_ID']}
REAL_OFICIAL_ALLOW_SPEND_CREDITS={values['REAL_OFICIAL_ALLOW_SPEND_CREDITS']}

# ==============================================================================
# YOUTUBE DATA API v3 (Obrigatorio p/ postar Shorts na campanha gta-vi-youtube)
# ==============================================================================
YOUTUBE_ENABLED={values['YOUTUBE_ENABLED']}
YOUTUBE_CLIENT_ID={values['YOUTUBE_CLIENT_ID']}
YOUTUBE_CLIENT_SECRET={values['YOUTUBE_CLIENT_SECRET']}
YOUTUBE_REFRESH_TOKEN={values['YOUTUBE_REFRESH_TOKEN']}
YOUTUBE_CHANNEL_HANDLE={values['YOUTUBE_CHANNEL_HANDLE']}

# ==============================================================================
# X (TWITTER) API v2 (6 slots diarios do fluxo Faceless GTA 6)
# ==============================================================================
X_ENABLED={values['X_ENABLED']}
X_API_KEY={values['X_API_KEY']}
X_API_SECRET={values['X_API_SECRET']}
X_ACCESS_TOKEN={values['X_ACCESS_TOKEN']}
X_ACCESS_TOKEN_SECRET={values['X_ACCESS_TOKEN_SECRET']}
X_BEARER_TOKEN={values['X_BEARER_TOKEN']}

# ==============================================================================
# BUFFER / WEBHOOK (Instagram Reels, TikTok, Threads, X)
# ==============================================================================
BUFFER_ENABLED={values['BUFFER_ENABLED']}
BUFFER_ACCESS_TOKEN={values['BUFFER_ACCESS_TOKEN']}
BUFFER_PROFILE_IDS={values['BUFFER_PROFILE_IDS']}
WEBHOOK_PUBLISHER_URL={values['WEBHOOK_PUBLISHER_URL']}

# ==============================================================================
# INSTAGRAM GRAPH API & TIKTOK API DIRETA (Opcional)
# ==============================================================================
INSTAGRAM_ACCESS_TOKEN={values['INSTAGRAM_ACCESS_TOKEN']}
INSTAGRAM_IG_USER_ID={values['INSTAGRAM_IG_USER_ID']}
TIKTOK_ACCESS_TOKEN={values['TIKTOK_ACCESS_TOKEN']}

# ==============================================================================
# VOZ IA OPCIONAL (edge_tts gratuito nativo; elevenlabs opcional)
# ==============================================================================
VOICEOVER_MODE={values['VOICEOVER_MODE']}
EDGE_TTS_VOICE_PT={values['EDGE_TTS_VOICE_PT']}
EDGE_TTS_VOICE_EN={values['EDGE_TTS_VOICE_EN']}
ELEVENLABS_API_KEY={values['ELEVENLABS_API_KEY']}
ELEVENLABS_VOICE_ID={values['ELEVENLABS_VOICE_ID']}

# ==============================================================================
# CONTROLE REMOTO PELO CELULAR NOS 4 DIAS FORA (Telegram / Discord)
# ==============================================================================
TELEGRAM_BOT_TOKEN={values['TELEGRAM_BOT_TOKEN']}
TELEGRAM_CHAT_ID={values['TELEGRAM_CHAT_ID']}
DISCORD_WEBHOOK_URL={values['DISCORD_WEBHOOK_URL']}
"""


def run_wizard(non_interactive: bool = False, check_only: bool = False) -> int:
    root = _project_root()
    env_path = root / ".env"
    values = _parse_existing_env(env_path)

    if check_only:
        print(f"[OK] Raiz do projeto: {root}")
        print(f"[OK] Arquivo .env {'encontrado' if env_path.exists() else 'ainda não criado (usando padrões)'}")
        print(f"     DRY_RUN={values['DRY_RUN']} | AUTOPILOT_MODE={values['AUTOPILOT_MODE']} | CAMPAIGN={values['REAL_OFICIAL_CAMPAIGN_SLUG']}")
        return 0

    print("=" * 74)
    print(" 🎮 ASSISTENTE DE CONFIGURAÇÃO — GTA 6 & REAL OFICIAL (gta-vi-youtube)")
    print(" Prepare seu 2º PC para rodar 100% no piloto automático pelos próximos 5 dias!")
    print(" Dica: pressione ENTER para manter o valor atual entre colchetes.")
    print("=" * 74)

    if not non_interactive:
        print("\n[1/6] MODO DE OPERAÇÃO NOS DIAS FORA DE CASA")
        live_now = _ask_yes_no(
            "Deseja ativar postagem REAL agora (DRY_RUN=false)? (Escolha 'n' p/ testar em modo seguro primeiro)",
            default_yes=(values["DRY_RUN"].lower() == "false"),
        )
        values["DRY_RUN"] = "false" if live_now else "true"

        full_auto = _ask_yes_no(
            "Usar modo 100% autônomo (full_auto) enquanto estiver viajando? ('n' = semi_auto pede ok no Telegram p/ exceções)",
            default_yes=(values["AUTOPILOT_MODE"] == "full_auto"),
        )
        values["AUTOPILOT_MODE"] = "full_auto" if full_auto else "semi_auto"
        values["TIMEZONE"] = _ask("Fuso horário dos agendamentos", values["TIMEZONE"])

        print("\n[2/6] IA / LLM PARA ROTEIROS E HOOKS VIRAIS (0-2s)")
        values["LLM_PROVIDER"] = _ask(
            "Provedor LLM (gemini | openai | groq | template_fallback)",
            values["LLM_PROVIDER"],
        ).lower()
        if values["LLM_PROVIDER"] == "gemini":
            values["GEMINI_API_KEY"] = _ask("GEMINI_API_KEY", values["GEMINI_API_KEY"], secret=True)
            values["LLM_MODEL"] = _ask("Modelo Gemini", values["LLM_MODEL"] or "gemini-2.5-flash")
        elif values["LLM_PROVIDER"] == "openai":
            values["OPENAI_API_KEY"] = _ask("OPENAI_API_KEY", values["OPENAI_API_KEY"], secret=True)
            values["LLM_MODEL"] = _ask("Modelo OpenAI", values["LLM_MODEL"] or "gpt-4o-mini")
        elif values["LLM_PROVIDER"] == "groq":
            values["GROQ_API_KEY"] = _ask("GROQ_API_KEY", values["GROQ_API_KEY"], secret=True)
            values["LLM_MODEL"] = _ask("Modelo Groq", values["LLM_MODEL"] or "llama-3.3-70b-versatile")

        print("\n[3/6] CAMPANHA REAL OFICIAL — GTA VI YOUTUBE")
        print(
            f"  Link oficial da campanha: https://app.realoficial.com.br/c/{values['REAL_OFICIAL_CAMPAIGN_SLUG']}"
            f"?ro_vid={values['REAL_OFICIAL_REF_VID']}"
        )
        ro_on = _ask_yes_no(
            "Ativar integração com o campeonato gta-vi-youtube no Real Oficial?",
            default_yes=(values["REAL_OFICIAL_ENABLED"].lower() == "true"),
        )
        values["REAL_OFICIAL_ENABLED"] = "true" if ro_on else "false"
        if ro_on:
            values["REAL_OFICIAL_API_KEY"] = _ask(
                "REAL_OFICIAL_API_KEY (opcional se usar MCP token ou fila 1-clique)",
                values["REAL_OFICIAL_API_KEY"],
                secret=True,
            )
            values["REAL_OFICIAL_MCP_TOKEN"] = _ask(
                "REAL_OFICIAL_MCP_TOKEN (token ro_mcp_... do painel Real Oficial)",
                values["REAL_OFICIAL_MCP_TOKEN"],
                secret=True,
            )
            values["REAL_OFICIAL_LAUNCHER_ID"] = _ask(
                "REAL_OFICIAL_LAUNCHER_ID (opcional)",
                values["REAL_OFICIAL_LAUNCHER_ID"],
            )
            spend_ok = _ask_yes_no(
                "Permitir gasto de créditos nuvem do Real Oficial? ('n' recomendado = FFmpeg local custo zero!)",
                default_yes=(values["REAL_OFICIAL_ALLOW_SPEND_CREDITS"].lower() == "true"),
            )
            values["REAL_OFICIAL_ALLOW_SPEND_CREDITS"] = "true" if spend_ok else "false"

        print("\n[4/6] YOUTUBE DATA API v3 (Upload automático de Shorts 9:16)")
        yt_on = _ask_yes_no(
            "Habilitar publicação no YouTube Shorts?",
            default_yes=(values["YOUTUBE_ENABLED"].lower() == "true"),
        )
        values["YOUTUBE_ENABLED"] = "true" if yt_on else "false"
        if yt_on:
            values["YOUTUBE_CLIENT_ID"] = _ask("YOUTUBE_CLIENT_ID", values["YOUTUBE_CLIENT_ID"], secret=True)
            values["YOUTUBE_CLIENT_SECRET"] = _ask("YOUTUBE_CLIENT_SECRET", values["YOUTUBE_CLIENT_SECRET"], secret=True)
            values["YOUTUBE_REFRESH_TOKEN"] = _ask("YOUTUBE_REFRESH_TOKEN", values["YOUTUBE_REFRESH_TOKEN"], secret=True)
            values["YOUTUBE_CHANNEL_HANDLE"] = _ask("Handle do seu canal (ex: @MeuCanalGTA6)", values["YOUTUBE_CHANNEL_HANDLE"])

        print("\n[5/6] CONTROLE REMOTO PELO CELULAR (Telegram / Discord — Essencial p/ os 4 dias fora!)")
        values["TELEGRAM_BOT_TOKEN"] = _ask(
            "TELEGRAM_BOT_TOKEN (crie no @BotFather p/ usar /status, /pause, /approve)",
            values["TELEGRAM_BOT_TOKEN"],
            secret=True,
        )
        values["TELEGRAM_CHAT_ID"] = _ask("TELEGRAM_CHAT_ID (seu ID numérico do Telegram)", values["TELEGRAM_CHAT_ID"])
        values["DISCORD_WEBHOOK_URL"] = _ask(
            "DISCORD_WEBHOOK_URL (opcional p/ alertas visuais no Discord)",
            values["DISCORD_WEBHOOK_URL"],
            secret=True,
        )

        print("\n[6/6] REDES EXTRAS OPCIONAIS (X / Buffer / Reels / TikTok / ElevenLabs)")
        if _ask_yes_no("Deseja configurar chaves extras (X, Buffer, Instagram, TikTok, ElevenLabs) agora?", default_yes=False):
            x_on = _ask_yes_no("Ativar 6 slots diários no X (Twitter)?", default_yes=(values["X_ENABLED"].lower() == "true"))
            values["X_ENABLED"] = "true" if x_on else "false"
            if x_on:
                values["X_API_KEY"] = _ask("X_API_KEY", values["X_API_KEY"], secret=True)
                values["X_API_SECRET"] = _ask("X_API_SECRET", values["X_API_SECRET"], secret=True)
                values["X_ACCESS_TOKEN"] = _ask("X_ACCESS_TOKEN", values["X_ACCESS_TOKEN"], secret=True)
                values["X_ACCESS_TOKEN_SECRET"] = _ask("X_ACCESS_TOKEN_SECRET", values["X_ACCESS_TOKEN_SECRET"], secret=True)
                values["X_BEARER_TOKEN"] = _ask("X_BEARER_TOKEN", values["X_BEARER_TOKEN"], secret=True)

            buf_on = _ask_yes_no("Ativar Buffer / Webhook p/ Reels & TikTok?", default_yes=(values["BUFFER_ENABLED"].lower() == "true"))
            values["BUFFER_ENABLED"] = "true" if buf_on else "false"
            if buf_on:
                values["BUFFER_ACCESS_TOKEN"] = _ask("BUFFER_ACCESS_TOKEN", values["BUFFER_ACCESS_TOKEN"], secret=True)
                values["BUFFER_PROFILE_IDS"] = _ask("BUFFER_PROFILE_IDS (separados por vírgula)", values["BUFFER_PROFILE_IDS"])
                values["WEBHOOK_PUBLISHER_URL"] = _ask("WEBHOOK_PUBLISHER_URL", values["WEBHOOK_PUBLISHER_URL"])

            values["VOICEOVER_MODE"] = _ask("Modo de locução IA (edge_tts | elevenlabs | none)", values["VOICEOVER_MODE"])
            if values["VOICEOVER_MODE"] == "elevenlabs":
                values["ELEVENLABS_API_KEY"] = _ask("ELEVENLABS_API_KEY", values["ELEVENLABS_API_KEY"], secret=True)
                values["ELEVENLABS_VOICE_ID"] = _ask("ELEVENLABS_VOICE_ID", values["ELEVENLABS_VOICE_ID"])

    env_path.write_text(_format_env_file(values), encoding="utf-8")

    print("\n" + "=" * 74)
    print(f" ✅ Arquivo salvo com sucesso em: {env_path}")
    print("=" * 74)
    print(" 📋 CHECKLIST RÁPIDO ANTES DE SAIR DE CASA:")
    print("  1. Verifique o diagnóstico completo:")
    print("     uv run gta6-agents status")
    print("  2. Rode um ciclo completo de teste gerando Shorts 1080x1920 reais:")
    print("     uv run gta6-agents demo-run")
    print("  3. Quando estiver tudo aprovado para postar de verdade, garanta DRY_RUN=false no .env")
    print("  4. No seu 2º PC, inicie o modo anti-suspensão de 5 dias + painel Mission Control:")
    print("     • Windows (PowerShell): .\\scripts\\start_2pc_windows.ps1")
    print("     • Linux / WSL2:         ./scripts/start_2pc_linux.sh")
    print("=" * 74)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Assistente interativo em Português para preencher o arquivo .env do GTA 6 Autopilot."
    )
    parser.add_argument(
        "--defaults",
        action="store_true",
        help="Gera/atualiza o .env mantendo os valores atuais/padrões sem prompts interativos.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Apenas verifica a leitura do .env sem modificar arquivos.",
    )
    args = parser.parse_args()
    sys.exit(run_wizard(non_interactive=args.defaults, check_only=args.check))


if __name__ == "__main__":
    main()
