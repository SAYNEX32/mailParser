"""Хранилище писем: SQLite/PostgreSQL через SQLAlchemy 2.x (этап 5, SPEC §7.2).

Схема повторяет SPEC §7.2 с добавлением служебных полей (тело HTML, вложения,
заголовки, причины классификации, timestamps). Уникальность — по паре
``(source, message_id)``, поэтому повторная запись того же письма обновляет
запись, а не создаёт дубль: это и есть накопление выгрузок.

Использование как библиотеки::

    from storage import Database
    db = Database()                  # DATABASE_URL из .env
    db.create_all()
    db.save(email)                   # "inserted" | "updated"

CLI::

    python src/storage.py --init
    python src/storage.py --import data/emails.json
    python src/storage.py --stats
    python src/storage.py --show disputed --limit 10
    python src/storage.py --export data/backup.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from config import DATABASE_URL
from models import CATEGORIES, Attachment, Classification, Email

UPDATED = "updated"
INSERTED = "inserted"


class Base(DeclarativeBase):
    """Базовый класс ORM-моделей."""


def _utcnow() -> datetime:
    return datetime.now(UTC)


class EmailRecord(Base):
    """Письмо в БД (SPEC §7.2)."""

    __tablename__ = "emails"
    __table_args__ = (
        UniqueConstraint("source", "message_id", name="uq_emails_source_message"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(16), index=True, default="")
    message_id: Mapped[str] = mapped_column(String(255), index=True, default="")
    thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    from_email: Mapped[str] = mapped_column(String(320), index=True, default="")
    from_name: Mapped[str] = mapped_column(String(320), default="")
    to: Mapped[str] = mapped_column(Text, default="[]")  # JSON-список
    cc: Mapped[str] = mapped_column(Text, default="[]")  # JSON-список

    subject: Mapped[str] = mapped_column(Text, default="")
    snippet: Mapped[str] = mapped_column(Text, default="")
    body_text: Mapped[str] = mapped_column(Text, default="")
    body_html: Mapped[str] = mapped_column(Text, default="")
    attachments: Mapped[str] = mapped_column(Text, default="[]")  # JSON
    headers_raw: Mapped[str] = mapped_column(Text, default="{}")  # JSON
    labels: Mapped[str] = mapped_column(Text, default="[]")  # JSON

    received_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )

    category: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reasons: Mapped[str] = mapped_column(Text, default="[]")  # JSON

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )


class Meta(Base):
    """Служебное key/value-хранилище (historyId, состояние синхронизации)."""

    __tablename__ = "meta"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )


# ---------------------------------------------------------------------------
# Преобразования Email ↔ EmailRecord
# ---------------------------------------------------------------------------
def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads_list(raw: str | None) -> list[Any]:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _loads_dict(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def record_to_email(record: EmailRecord) -> Email:
    """ORM-запись → нормализованная модель."""
    attachments = [
        Attachment.from_dict(item)
        for item in _loads_list(record.attachments)
        if isinstance(item, dict)
    ]
    classification: Classification | None = None
    if record.category:
        classification = Classification(
            category=record.category,
            confidence=record.confidence or 0.0,
            method=record.method or "rules",
            reasons=[str(item) for item in _loads_list(record.reasons)],
        )
    received_at = record.received_at
    if received_at is not None and received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=UTC)
    return Email(
        source=record.source,
        message_id=record.message_id,
        from_name=record.from_name,
        from_email=record.from_email,
        to=[str(item) for item in _loads_list(record.to)],
        cc=[str(item) for item in _loads_list(record.cc)],
        subject=record.subject,
        date=received_at,
        snippet=record.snippet,
        body_text=record.body_text,
        body_html=record.body_html,
        attachments=attachments,
        headers_raw={
            str(k): str(v) for k, v in _loads_dict(record.headers_raw).items()
        },
        labels=[str(item) for item in _loads_list(record.labels)],
        thread_id=record.thread_id,
        classification=classification,
    )


def apply_email_to_record(email: Email, record: EmailRecord) -> None:
    """Заполняет ORM-запись из нормализованной модели (id/timestamps не трогаем)."""
    record.source = email.source
    record.message_id = email.message_id
    record.thread_id = email.thread_id
    record.from_email = (email.from_email or "")[:320]
    record.from_name = (email.from_name or "")[:320]
    record.to = _dumps(email.to)
    record.cc = _dumps(email.cc)
    record.subject = email.subject
    record.snippet = email.snippet
    record.body_text = email.body_text
    record.body_html = email.body_html
    record.attachments = _dumps([item.to_dict() for item in email.attachments])
    record.headers_raw = _dumps(email.headers_raw)
    record.labels = _dumps(email.labels)

    received_at = email.date
    if received_at is not None:
        received_at = (
            received_at.replace(tzinfo=UTC)
            if received_at.tzinfo is None
            else received_at
        )
        received_at = received_at.astimezone(UTC).replace(tzinfo=None)  # naive UTC
    record.received_at = received_at

    result = email.classification
    if result is not None:
        record.category = result.category
        record.confidence = result.confidence
        record.method = result.method
        record.reasons = _dumps(result.reasons)


# ---------------------------------------------------------------------------
# База данных
# ---------------------------------------------------------------------------
class Database:
    """Тонкая обёртка над SQLAlchemy Engine/Session для работы с письмами."""

    def __init__(self, url: str | None = None, *, echo: bool = False) -> None:
        self.url = url or DATABASE_URL
        self.engine: Engine = create_engine(self.url, echo=echo, future=True)
        self._session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    # -- служебное -------------------------------------------------------
    def create_all(self) -> None:
        """Создаёт таблицы, если их нет (идемпотентно)."""
        Base.metadata.create_all(self.engine)

    def session(self) -> Session:
        return self._session_factory()

    # -- письма ----------------------------------------------------------
    def save(self, email: Email) -> str:
        """Вставляет или обновляет письмо. Возвращает 'inserted' | 'updated'."""
        with self.session() as session:
            record = session.scalar(
                select(EmailRecord).where(
                    EmailRecord.source == email.source,
                    EmailRecord.message_id == email.message_id,
                )
            )
            if record is None:
                record = EmailRecord(source=email.source, message_id=email.message_id)
                apply_email_to_record(email, record)
                session.add(record)
                session.commit()
                return INSERTED
            apply_email_to_record(email, record)
            session.commit()
            return UPDATED

    def save_many(self, emails: Iterable[Email]) -> Counter[str]:
        """Сохраняет пачку писем, возвращает счётчики inserted/updated."""
        stats: Counter[str] = Counter()
        for email in emails:
            stats[self.save(email)] += 1
        return stats

    def count(self) -> int:
        with self.session() as session:
            return int(session.scalar(select(func.count(EmailRecord.id))) or 0)

    def get(
        self,
        *,
        category: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        newest_first: bool = True,
    ) -> list[Email]:
        """Письма из БД с фильтром по категории."""
        stmt = select(EmailRecord)
        if category:
            stmt = stmt.where(EmailRecord.category == category)
        order = (
            EmailRecord.received_at.desc()
            if newest_first
            else EmailRecord.received_at.asc()
        )
        stmt = stmt.order_by(order, EmailRecord.id.desc())
        if limit:
            stmt = stmt.limit(limit).offset(offset)
        with self.session() as session:
            return [record_to_email(row) for row in session.scalars(stmt)]

    def stats(self) -> dict[str, int]:
        """Счётчики по категориям + общее число писем."""
        result: dict[str, int] = {"total": self.count()}
        with self.session() as session:
            rows = session.execute(
                select(EmailRecord.category, func.count(EmailRecord.id)).group_by(
                    EmailRecord.category
                )
            ).all()
        for category, count in rows:
            result[category or "unclassified"] = int(count)
        return result

    def delete(self, source: str, message_id: str) -> bool:
        from sqlalchemy import delete as sql_delete

        with self.session() as session:
            result = session.execute(
                sql_delete(EmailRecord).where(
                    EmailRecord.source == source,
                    EmailRecord.message_id == message_id,
                )
            )
            session.commit()
            return bool(result.rowcount)

    # -- meta (historyId и т.п.) ------------------------------------------
    def get_meta(self, key: str) -> str | None:
        with self.session() as session:
            row = session.get(Meta, key)
            return row.value if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.session() as session:
            row = session.get(Meta, key)
            if row is None:
                session.add(Meta(key=key, value=value))
            else:
                row.value = value
            session.commit()

    # -- импорт/экспорт ---------------------------------------------------
    def import_json(self, path: Path) -> Counter[str]:
        """Догружает письма из JSON-выгрузки (накопление без дублей)."""
        with path.open(encoding="utf-8") as fh:
            raw = json.load(fh)
        if isinstance(raw, dict):
            raw = raw.get("emails")
        if not isinstance(raw, list):
            raise TypeError(f"Ожидался список писем в {path}")
        return self.save_many(Email.from_dict(item) for item in raw)

    def export_json(self, path: Path, emails: Sequence[Email] | None = None) -> int:
        """Выгружает письма из БД в JSON (по умолчанию все)."""
        items = list(emails) if emails is not None else self.get()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            json.dump(
                [item.to_dict() for item in items], fh, ensure_ascii=False, indent=2
            )
        return len(items)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _print_stats(stats: dict[str, int]) -> None:
    total = stats.get("total", 0)
    print(f"\n[i] Всего писем в БД: {total}")
    for category in CATEGORIES:
        count = stats.get(category, 0)
        share = (count / total * 100) if total else 0.0
        print(f"    {category:<10} {count:>5}  {share:>5.1f}%")
    unclassified = stats.get("unclassified", 0)
    if unclassified:
        print(f"    {'без категории':<10} {unclassified:>5}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Хранилище писем (SQLite/SQLAlchemy)")
    parser.add_argument(
        "--db", default=None, help=f"URL базы (по умолчанию {DATABASE_URL})"
    )
    parser.add_argument("--init", action="store_true", help="Создать/обновить схему")
    parser.add_argument(
        "--import",
        dest="import_path",
        action="append",
        default=None,
        help="Импортировать JSON-выгрузку (можно несколько раз)",
    )
    parser.add_argument("--stats", action="store_true", help="Показать статистику")
    parser.add_argument(
        "--show",
        choices=CATEGORIES,
        action="append",
        default=None,
        help="Показать письма выбранных категорий",
    )
    parser.add_argument("--limit", type=int, default=10, help="Лимит вывода (0 — все)")
    parser.add_argument("--export", default=None, help="Экспорт БД в JSON")
    args = parser.parse_args(argv)

    db = Database(args.db)
    db.create_all()

    if args.import_path:
        for item in args.import_path:
            path = Path(item)
            if not path.exists():
                sys.exit(f"[!] Файл не найден: {path}")
            stats = db.import_json(path)
            print(
                f"[+] {path}: новых {stats.get(INSERTED, 0)}, "
                f"обновлено {stats.get(UPDATED, 0)}"
            )

    if args.show:
        limit = args.limit or None
        shown = 0
        for category in args.show:
            emails = db.get(category=category, limit=limit)
            print(f"\n=== {category} ({len(emails)}) ===")
            for email in emails:
                date = f"{email.date:%Y-%m-%d}" if email.date else "----------"
                conf = email.classification.confidence if email.classification else 0.0
                subject = email.subject[:60] + ("…" if len(email.subject) > 60 else "")
                print(f"  {date} {conf:.2f} | {email.from_email[:30]:<30} | {subject}")
            shown += len(emails)
        if not shown:
            print("\n[i] Писем не найдено")

    if args.export:
        count = db.export_json(Path(args.export))
        print(f"[+] Экспортировано {count} писем → {args.export}")

    if args.stats or not (args.import_path or args.show or args.export):
        _print_stats(db.stats())
        print(f"\n[i] База: {db.url}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    raise SystemExit(main())
