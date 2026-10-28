"""CLI: выгрузка писем из Gmail в JSON с классификацией (этапы 1 и 4)."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

from classifier import ICONS, classify
from config import DATA_DIR
from gmail_client import GmailClient
from models import CATEGORIES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Выгрузка писем из Gmail в JSON с классификацией"
    )
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
    parser.add_argument(
        "--no-classify",
        action="store_true",
        help="Не классифицировать письма (только сбор)",
    )
    parser.add_argument(
        "--show-rules",
        action="store_true",
        help="Печатать сработавшие правила для каждого письма",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    label_ids: tuple[str, ...] | None = ("INBOX",)
    if args.label:
        label_ids = None if "all" in args.label else tuple(args.label)

    client = GmailClient()

    emails = []
    counts: Counter[str] = Counter()
    for email in client.iter_messages(
        query=args.query, limit=args.limit, label_ids=label_ids
    ):
        if not args.no_classify:
            email.classification = classify(email)
        emails.append(email)

        date = f"{email.date:%Y-%m-%d %H:%M}" if email.date else "—"
        subject = email.subject[:52] + ("…" if len(email.subject) > 52 else "")
        if email.classification:
            result = email.classification
            counts[result.category] += 1
            prefix = f"  {ICONS[result.category]} {result.category:<9} {result.confidence:.2f}"
        else:
            prefix = "  -- ---------- ----"
        print(f"{prefix} {date} | {email.from_email or '—':<32} | {subject}")
        if args.show_rules and email.classification:
            for note in email.classification.explanation:
                print(f"       └─ {note}")

    if not emails:
        print("[i] Писем не найдено")
        return 0

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(
            [email.to_dict() for email in emails], fh, ensure_ascii=False, indent=2
        )

    with_bodies = sum(1 for email in emails if email.body_text)
    attachments = sum(len(email.attachments) for email in emails)
    print(
        f"\n[+] Сохранено {len(emails)} писем → {args.out}\n"
        f"    с текстом: {with_bodies}, вложений: {attachments}"
    )
    if counts:
        summary = ", ".join(f"{cat}: {counts.get(cat, 0)}" for cat in CATEGORIES)
        print(f"    классификация: {summary}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    raise SystemExit(main())
