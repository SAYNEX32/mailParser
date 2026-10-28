"""Аутентификация в Gmail API по сохранённым OAuth-токенам."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from config import GMAIL_CLIENT_SECRET_PATH, GMAIL_SCOPES, GMAIL_TOKEN_PATH

TOKEN_URI = "https://oauth2.googleapis.com/token"


def _load_json(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"[!] Файл не найден: {path}")
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        sys.exit(f"[!] Некорректный JSON в {path}: {exc}")


def client_credentials(secret_path: Path | None = None) -> tuple[str, str]:
    """Возвращает client_id/client_secret из файла OAuth client (installed/web)."""
    raw = _load_json(secret_path or GMAIL_CLIENT_SECRET_PATH)
    block = raw.get("installed") or raw.get("web") or raw
    client_id = block.get("client_id")
    client_secret = block.get("client_secret")
    if not client_id or not client_secret:
        sys.exit(
            f"[!] В {secret_path or GMAIL_CLIENT_SECRET_PATH} нет client_id/client_secret"
        )
    return client_id, client_secret


def load_credentials(token_path: Path | None = None) -> Credentials:
    """Собирает Credentials из сохранённого токена и обновляет его при необходимости."""
    token_path = token_path or GMAIL_TOKEN_PATH
    token_data = _load_json(token_path)

    # В файле используются camelCase-ключи (Node.js формат) — поддерживаем оба варианта.
    access_token = token_data.get("accessToken") or token_data.get("access_token")
    refresh_token = token_data.get("refreshToken") or token_data.get("refresh_token")
    scopes = (token_data.get("scope") or "").split() or GMAIL_SCOPES

    if not refresh_token:
        sys.exit("[!] В файле токена отсутствует refresh_token")

    client_id, client_secret = client_credentials()

    creds = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri=token_data.get("token_uri", TOKEN_URI),
        client_id=client_id,
        client_secret=client_secret,
        scopes=scopes,
    )

    if not creds.valid:
        print("[*] Access-токен истёк, обновляем по refresh_token...")
        try:
            creds.refresh(Request())
        except Exception as exc:  # noqa: BLE001 - показываем причину пользователю
            sys.exit(f"[!] Не удалось обновить токен: {exc}")
        print("[+] Токен обновлён")

    return creds
