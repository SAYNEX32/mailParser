"""CLI: выгрузка писем из Gmail в нормализованный JSON (этап 1)."""

from __future__ import annotations

import argparse
import json
import sys

from config import DATA_DIR
from gmail_client import GmailClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Выгрузка писем из Gmail в JSON")
    parser.add_argument(
        "--limit", type=int, default=10, help="Сколько писем забрать (по умолчанию 10)"
    )
    parser.add_argument(
        "--query", default=None, help="Gmail-запрос, например 'is:unread'"
    )
    parser.add_argument(
        "--label",
        action="append",
        default=None,
        help="Метка (можно несколько раз). 'all' — без фильтра по меткам",
    )
    parser.add_argument(
        "--out",
        default=str(DATA_DIR / "emails.json"),
        help="Файл для сохранения JSON (по умолчанию data/emails.json)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    label_ids: tuple[str, ...] | None = ("INBOX",)
    if args.label:
        label_ids = None if "all" in args.label else tuple(args.label)

    client = GmailClient()

    emails = []
    for email in client.iter_messages(
        query=args.query, limit=args.limit, label_ids=label_ids
    ):
        emails.append(email.to_dict())
        date = f"{email.date:%Y-%m-%d %H:%M}" if email.date else "—"
        subject = email.subject[:60] + ("…" if len(email.subject) > 60 else "")
        print(f"  {date} | {email.from_email or '—':<35} | {subject}")

    if not emails:
        print("[i] Писем не найдено")
        return 0

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(emails, fh, ensure_ascii=False, indent=2)

    with_bodies = sum(1 for e in emails if e["body_text"])
    attachments = sum(len(e["attachments"]) for e in emails)
    print(
        f"\n[+] Сохранено {len(emails)} писем → {args.out}\n"
        f"    с текстом: {with_bodies}, вложений: {attachments}"
    )
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
