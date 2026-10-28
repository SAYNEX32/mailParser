"""Проверка доступа к Gmail API по сохранённым OAuth-токенам.

Выводит Subject и From для N последних писем во входящих.
Запуск:
    python src/test_gmail.py
    python src/test_gmail.py --max 5
"""

from __future__ import annotations

import argparse
import sys

from googleapiclient.errors import HttpError

from config import GMAIL_CLIENT_SECRET_PATH, GMAIL_TOKEN_PATH
from gmail_auth import load_credentials
from gmail_client import GmailClient


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка доступа к Gmail API")
    parser.add_argument(
        "--max",
        type=int,
        default=3,
        help="Сколько последних писем показать (по умолчанию 3)",
    )
    args = parser.parse_args()

    print(f"[*] Аккаунт: {GMAIL_TOKEN_PATH.parent.name}")
    print(f"[*] Токены:  {GMAIL_TOKEN_PATH}")
    print(f"[*] Client:  {GMAIL_CLIENT_SECRET_PATH}")

    try:
        client = GmailClient(load_credentials())
        ids = client.list_ids(label_ids=("INBOX",), max_results=args.max)

        if not ids:
            print("[i] Входящих писем не найдено")
            return 0

        print(f"\n[+] Найдено писем: {len(ids)}\n" + "-" * 70)
        for i, message_id in enumerate(ids, 1):
            headers = client.get_headers(message_id, ["Subject", "From", "Date"])
            print(f"{i}. From:    {headers.get('from', '(нет From)')}")
            print(f"   Subject: {headers.get('subject', '(без темы)')}")
            if headers.get("date"):
                print(f"   Date:    {headers['date']}")
            print("-" * 70)
    except HttpError as exc:
        sys.exit(f"[!] Ошибка Gmail API: {exc}")

    return 0


if __name__ == "__main__":
    # Чтобы кириллица в темах не ломала вывод в Windows-консоли
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
