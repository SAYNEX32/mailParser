"""Загрузка конфигурации проекта из переменных окружения (.env).

Заготовка: значения по умолчанию покрывают локальную разработку.
Секреты (client secret, токены) НЕ должны попадать в git.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Пути проекта
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "src"
DATA_DIR = BASE_DIR / "data"
SECRET_DIR = BASE_DIR / "secret"

# Загружаем .env из корня проекта (если файла нет — молча пропускаем)
load_dotenv(BASE_DIR / ".env")


def _env_path(name: str, default: Path) -> Path:
    """Читает путь из окружения, иначе возвращает значение по умолчанию."""
    raw = os.getenv(name)
    return Path(os.path.expandvars(raw)).expanduser() if raw else default


# ---------------------------------------------------------------------------
# Gmail
# ---------------------------------------------------------------------------
# Ящик, с которым работаем. Значения по умолчанию нет — задаётся в .env.
GMAIL_ACCOUNT: str = (os.getenv("GMAIL_ACCOUNT") or "").strip()


def account_slug(account: str) -> str:
    """'user@gmail.com' → 'user-gmail-com' (имя папки в хранилище токенов)."""
    return re.sub(r"[^a-z0-9]+", "-", account.lower()).strip("-") or "default"


# Файл с сохранёнными OAuth-токенами (access/refresh).
# По умолчанию путь строится из GMAIL_ACCOUNT, чтобы адрес не был зашит в код.
GMAIL_TOKEN_PATH: Path = _env_path(
    "GMAIL_TOKEN_PATH",
    Path.home()
    / ".config"
    / "automation"
    / "gmail"
    / "accounts"
    / account_slug(GMAIL_ACCOUNT)
    / "gmail-tokens.json",
)


# OAuth client (Desktop app) — нужен для обновления access-токена
def _default_client_secret() -> Path:
    """Ищет файл client_secret*.json в secret/, иначе — в конфиге automation."""
    candidates = sorted(SECRET_DIR.glob("client_secret*.json"))
    if candidates:
        return candidates[0]
    return Path.home() / ".config" / "automation" / "gmail" / "google-oauth-client.json"


GMAIL_CLIENT_SECRET_PATH: Path = _env_path(
    "GMAIL_CLIENT_SECRET_PATH", _default_client_secret()
)

GMAIL_SCOPES: list[str] = [
    "https://www.googleapis.com/auth/gmail.readonly",
]

# ---------------------------------------------------------------------------
# База данных (задел на этап 5)
# ---------------------------------------------------------------------------
DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'emails.db'}")
