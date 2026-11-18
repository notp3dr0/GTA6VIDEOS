"""Helper de 1 comando para gerar o YOUTUBE_REFRESH_TOKEN oficial (YouTube Data API v3).

Uso no seu PC (com navegador):
1. No Google Cloud Console -> APIs e Servicos -> Biblioteca -> Ative 'YouTube Data API v3'.
2. Em Credenciais -> Criar Credenciais -> ID do cliente OAuth -> 'App para computador' (Desktop App).
3. Copie Client ID e Client Secret para o seu .env (YOUTUBE_CLIENT_ID e YOUTUBE_CLIENT_SECRET)
   ou passe ao executar este script.
4. Rode:
   uv run python scripts/generate_youtube_token.py
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    env_file = root / ".env"
    if env_file.exists():
        load_dotenv(env_file)

    client_id = os.getenv("YOUTUBE_CLIENT_ID") or input("Cole seu YOUTUBE_CLIENT_ID: ").strip()
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET") or input("Cole seu YOUTUBE_CLIENT_SECRET: ").strip()

    if not client_id or not client_secret:
        raise SystemExit("Erro: YOUTUBE_CLIENT_ID e YOUTUBE_CLIENT_SECRET sao obrigatorios.")

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")

    print("\n" + "=" * 72)
    print("SUCESSO! Copie as linhas abaixo para o seu arquivo .env:")
    print("=" * 72)
    print(f"YOUTUBE_ENABLED=true")
    print(f"YOUTUBE_CLIENT_ID={client_id}")
    print(f"YOUTUBE_CLIENT_SECRET={client_secret}")
    print(f"YOUTUBE_REFRESH_TOKEN={creds.refresh_token}")
    print("=" * 72)


if __name__ == "__main__":
    main()
