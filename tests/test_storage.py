"""Тесты хранилища (этап 5)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from models import Attachment, Classification, Email
from storage import INSERTED, UPDATED, Database


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    database.create_all()
    return database


def make_email(
    *,
    message_id: str = "msg-1",
    subject: str = "Тема письма",
    category: str | None = "important",
    received: datetime | None = None,
    attachments: list[Attachment] | None = None,
) -> Email:
    return Email(
        source="gmail",
        message_id=message_id,
        thread_id="thr-1",
        from_name="Иван",
        from_email="ivan@company.com",
        to=["me@example.com"],
        cc=["copy@example.com"],
        subject=subject,
        date=received or datetime(2026, 10, 28, 10, 41, 31, tzinfo=UTC),
        snippet="сниппет",
        body_text="Тело письма",
        body_html="<p>Тело письма</p>",
        attachments=attachments or [],
        headers_raw={"subject": subject, "from": "ivan@company.com"},
        labels=["INBOX", "UNREAD"],
        classification=(
            Classification(
                category=category,
                confidence=0.87,
                method="rules",
                reasons=["sender_in_whitelist"],
                explanation=["отправитель в белом списке"],
            )
            if category
            else None
        ),
    )


def test_save_is_idempotent(db: Database) -> None:
    assert db.save(make_email()) == INSERTED
    assert db.save(make_email()) == UPDATED
    assert db.count() == 1


def test_save_updates_changed_fields(db: Database) -> None:
    db.save(make_email(subject="Старая тема"))
    db.save(make_email(subject="Новая тема", category="spam"))
    stored = db.get()
    assert len(stored) == 1
    assert stored[0].subject == "Новая тема"
    assert stored[0].classification is not None
    assert stored[0].classification.category == "spam"


def test_roundtrip_keeps_main_fields(db: Database) -> None:
    attachment = Attachment(
        filename="счёт.pdf", mime_type="application/pdf", size=1024, attachment_id="a1"
    )
    db.save(make_email(attachments=[attachment]))
    stored = db.get()[0]
    assert stored.from_name == "Иван"
    assert stored.to == ["me@example.com"]
    assert stored.cc == ["copy@example.com"]
    assert stored.labels == ["INBOX", "UNREAD"]
    assert stored.headers_raw["subject"] == "Тема письма"
    assert stored.attachments[0].filename == "счёт.pdf"
    assert stored.attachments[0].size == 1024
    assert stored.date == datetime(2026, 10, 28, 10, 41, 31, tzinfo=UTC)


def test_classification_is_persisted(db: Database) -> None:
    db.save(make_email())
    result = db.get()[0].classification
    assert result is not None
    assert result.category == "important"
    assert result.confidence == pytest.approx(0.87)
    assert result.method == "rules"
    assert result.reasons == ["sender_in_whitelist"]


def test_email_without_classification(db: Database) -> None:
    db.save(make_email(category=None))
    stored = db.get()[0]
    assert stored.classification is None
    assert db.stats()["unclassified"] == 1


def test_stats_counts_by_category(db: Database) -> None:
    db.save(make_email(message_id="1", category="important"))
    db.save(make_email(message_id="2", category="spam"))
    db.save(make_email(message_id="3", category="spam"))
    stats = db.stats()
    assert stats["total"] == 3
    assert stats["spam"] == 2
    assert stats["important"] == 1


def test_get_filters_and_orders(db: Database) -> None:
    db.save(make_email(message_id="old", received=datetime(2026, 10, 1, tzinfo=UTC)))
    db.save(make_email(message_id="new", received=datetime(2026, 10, 20, tzinfo=UTC)))
    db.save(
        make_email(
            message_id="spam-1",
            category="spam",
            received=datetime(2026, 10, 5, tzinfo=UTC),
        )
    )

    newest = db.get()
    assert next(item.message_id for item in newest) == "new"
    assert [item.message_id for item in db.get(category="spam")] == ["spam-1"]
    assert len(db.get(limit=2)) == 2
    oldest = db.get(newest_first=False)
    assert oldest[0].message_id == "old"


def test_meta_key_value(db: Database) -> None:
    assert db.get_meta("gmail_history_id") is None
    db.set_meta("gmail_history_id", "22820")
    assert db.get_meta("gmail_history_id") == "22820"
    db.set_meta("gmail_history_id", "22821")
    assert db.get_meta("gmail_history_id") == "22821"


def test_import_json_accumulates(db: Database, tmp_path: Path) -> None:
    path = tmp_path / "dump.json"
    path.write_text(
        json.dumps(
            [make_email(message_id="1").to_dict(), make_email(message_id="2").to_dict()]
        ),
        encoding="utf-8",
    )
    first = db.import_json(path)
    assert first[INSERTED] == 2
    assert db.count() == 2

    second = db.import_json(path)
    assert second[UPDATED] == 2
    assert db.count() == 2


def test_import_json_rejects_wrong_shape(db: Database, tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    with pytest.raises(TypeError):
        db.import_json(path)


def test_export_json(db: Database, tmp_path: Path) -> None:
    db.save(make_email())
    out = tmp_path / "out" / "export.json"
    assert db.export_json(out) == 1
    restored = Email.from_dict(json.loads(out.read_text(encoding="utf-8"))[0])
    assert restored.subject == "Тема письма"
    assert restored.classification is not None
    assert restored.classification.category == "important"


def test_delete(db: Database) -> None:
    db.save(make_email())
    assert db.delete("gmail", "msg-1") is True
    assert db.delete("gmail", "msg-1") is False
    assert db.count() == 0


def test_naive_datetime_is_assumed_utc(db: Database) -> None:
    db.save(make_email(received=datetime(2026, 10, 28, 10, 41, 31)))
    stored = db.get()[0].date
    assert stored is not None
    assert stored.tzinfo == UTC
    assert stored.hour == 10
