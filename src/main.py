"""CLI: сбор писем из Gmail, классификация и сохранение (этапы 1, 4, 5)."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

from classifier import ICONS, classify
from config import DATA_DIR, DATABASE_URL, GMAIL_ACCOUNT
from gmail_client import GmailClient, HistoryExpiredError
from models import CATEGORIES, Email
from storage import Database


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Сбор писем из Gmail: классификация + SQLite/JSON"
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
        "--db",
        nargs="?",
        const=DATABASE_URL,
        default=DATABASE_URL,
        help=f"URL базы (по умолчанию {DATABASE_URL})",
    )
    parser.add_argument(
        "--no-db", action="store_true", help="Не писать в базу, только выгрузка"
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Инкрементальная синхронизация по historyId (только новые письма)",
    )
    parser.add_argument(
        "--out", default=None, help="Дополнительно сохранить письма в JSON"
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


def collect_ids(
    client: GmailClient,
    db: Database | None,
    *,
    limit: int,
    query: str | None,
    label_ids: tuple[str, ...] | None,
    incremental: bool,
) -> tuple[list[str], str | None, bool]:
    """Возвращает (id писем, новый historyId, была ли полная синхронизация)."""
    meta_key = f"gmail_history_id:{GMAIL_ACCOUNT or 'me'}"

    if incremental and db is not None:
        start = db.get_meta(meta_key)
        if not start:
            print("[i] historyId ещё не сохранён — первая синхронизация будет полной")
        else:
            try:
                ids, new_history = client.collect_history(start, max_messages=limit)
                print(f"[*] Инкрементально с historyId {start}: новых писем {len(ids)}")
                return ids, new_history, False
            except HistoryExpiredError as exc:
                print(f"[!] {exc} → выполняем полную синхронизацию")

    # Полная синхронизация: historyId фиксируем ДО запроса, чтобы не потерять письма
    baseline = client.history_id()
    ids = client.list_ids(query=query, label_ids=label_ids, max_results=limit)
    print(f"[*] Полная синхронизация: писем {len(ids)}")
    return ids, baseline, True


def fetch_emails(
    client: GmailClient, ids: list[str], *, do_classify: bool, show_rules: bool
) -> list[Email]:
    """Забирает полные письма, классифицирует и печатает построчно."""
    emails: list[Email] = []
    for message_id in ids:
        try:
            email = client.get_message(message_id)
        except Exception as exc:  # noqa: BLE001 - одно письмо не должно ломать прогон
            print(f"[!] Не удалось получить письмо {message_id}: {exc}")
            continue

        if do_classify:
            email.classification = classify(email)
        emails.append(email)

        date = f"{email.date:%Y-%m-%d %H:%M}" if email.date else "—"
        subject = email.subject[:50] + ("…" if len(email.subject) > 50 else "")
        result = email.classification
        prefix = (
            f"  {ICONS[result.category]} {result.category:<9} {result.confidence:.2f}"
            if result
            else "  -- ---------- ----"
        )
        print(f"{prefix} {date} | {email.from_email or '—':<30} | {subject}")
        if show_rules and result:
            for note in result.explanation:
                print(f"       └─ {note}")
    return emails


def main() -> int:
    args = build_parser().parse_args()

    label_ids: tuple[str, ...] | None = ("INBOX",)
    if args.label:
        label_ids = None if "all" in args.label else tuple(args.label)

    db: Database | None = None
    if not args.no_db:
        db = Database(args.db)
        db.create_all()

    client = GmailClient()
    ids, history_id, full_sync = collect_ids(
        client,
        db,
        limit=args.limit,
        query=args.query,
        label_ids=label_ids,
        incremental=args.sync,
    )

    emails = fetch_emails(
        client,
        ids,
        do_classify=not args.no_classify,
        show_rules=args.show_rules,
    )

    if not emails:
        print("[i] Новых писем нет")
        if db is not None and history_id:
            db.set_meta(f"gmail_history_id:{GMAIL_ACCOUNT or 'me'}", history_id)
        return 0

    # 1) Хранилище
    if db is not None:
        stats = db.save_many(emails)
        print(
            f"\n[+] БД {args.db}\n"
            f"    новых: {stats.get('inserted', 0)}, "
            f"обновлено: {stats.get('updated', 0)}, "
            f"всего в базе: {db.count()}"
        )
        meta_key = f"gmail_history_id:{GMAIL_ACCOUNT or 'me'}"
        if history_id:
            db.set_meta(meta_key, history_id)
            print(f"    historyId: {history_id}")

    # 2) JSON (опционально)
    out = args.out or (str(DATA_DIR / "emails.json") if db is None else None)
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(
                [email.to_dict() for email in emails], fh, ensure_ascii=False, indent=2
            )
        print(f"[+] JSON: {len(emails)} писем → {out}")

    counts: Counter[str] = Counter(
        email.classification.category
        for email in emails
        if email.classification is not None
    )
    if counts:
        summary = ", ".join(f"{cat}: {counts.get(cat, 0)}" for cat in CATEGORIES)
        mode = "полная" if full_sync else "инкрементальная"
        print(f"    классификация ({mode}): {summary}")

    attachments = sum(len(email.attachments) for email in emails)
    with_bodies = sum(1 for email in emails if email.body_text)
    print(f"    с текстом: {with_bodies}, вложений: {attachments}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    raise SystemExit(main())
