"""Тесты парсинга MIME и вспомогательных функций (этап 3)."""

from __future__ import annotations

import base64
from datetime import UTC, datetime

from parsing import (
    decode_base64url,
    html_to_text,
    parse_address,
    parse_date,
    parse_gmail_message,
    split_addresses,
)


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode().rstrip("=")


# --- HTML → текст ----------------------------------------------------------
def test_html_to_text_strips_tags_and_scripts() -> None:
    html = (
        "<html><head><title>skip</title><style>p{}</style></head>"
        "<body><p>Привет</p><p>Мир</p><script>alert(1)</script></body></html>"
    )
    text = html_to_text(html)
    assert "Привет" in text
    assert "Мир" in text
    assert "alert" not in text
    assert "p{}" not in text
    assert "skip" not in text


def test_html_to_text_handles_entities_and_empty() -> None:
    assert html_to_text("<p>a&nbsp;&amp;b</p>") == "a &b"
    assert html_to_text("") == ""


def test_html_to_text_survives_broken_markup() -> None:
    assert "текст" in html_to_text("<div><b>текст<span></div>")


# --- адреса -----------------------------------------------------------------
def test_parse_address_with_display_name() -> None:
    assert parse_address('"Иван Петров" <ivan@company.com>') == (
        "Иван Петров",
        "ivan@company.com",
    )


def test_parse_address_bare_email() -> None:
    assert parse_address("ivan@company.com") == ("", "ivan@company.com")


def test_parse_address_empty() -> None:
    assert parse_address("") == ("", "")


def test_split_addresses() -> None:
    raw = "Иван <ivan@company.com>, petr@mail.ru, bad"
    assert split_addresses(raw) == ["ivan@company.com", "petr@mail.ru"]


# --- даты -------------------------------------------------------------------
def test_parse_date_rfc2822() -> None:
    parsed = parse_date("Wed, 28 Oct 2026 10:41:31 +0000")
    assert parsed == datetime(2026, 10, 28, 10, 41, 31, tzinfo=UTC)


def test_parse_date_naive_gets_utc() -> None:
    assert parse_date("28 Oct 2026 10:41:31").tzinfo == UTC


def test_parse_date_invalid() -> None:
    assert parse_date("не дата") is None
    assert parse_date(None) is None


# --- base64 ---------------------------------------------------------------
def test_decode_base64url_without_padding() -> None:
    assert decode_base64url(_b64("привет")) == "привет"


def test_decode_base64url_invalid() -> None:
    assert decode_base64url("") == ""


# --- полное письмо из Gmail --------------------------------------------------
def _payload(
    *,
    subject: str = "Тема",
    sender: str = "Иван <ivan@company.com>",
    body: str = "Тело письма",
    attachment: bool = False,
) -> dict:
    parts = [
        {
            "mimeType": "text/plain",
            "headers": [{"name": "Content-Type", "value": "text/plain; charset=utf-8"}],
            "body": {"data": _b64(body), "size": len(body)},
        }
    ]
    if attachment:
        parts.append(
            {
                "mimeType": "application/pdf",
                "filename": "счёт.pdf",
                "body": {"attachmentId": "att-1", "size": 1024},
            }
        )
    return {
        "id": "msg-1",
        "threadId": "thr-1",
        "labelIds": ["INBOX", "UNREAD"],
        "snippet": "сниппет",
        "internalDate": "1793097600000",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
                {"name": "To", "value": "me@example.com"},
                {"name": "Cc", "value": "copy@example.com"},
                {"name": "Date", "value": "Wed, 28 Oct 2026 10:41:31 +0000"},
            ],
            "parts": parts,
        },
    }


def test_parse_gmail_message_basic_fields() -> None:
    email = parse_gmail_message(_payload())
    assert email.source == "gmail"
    assert email.message_id == "msg-1"
    assert email.thread_id == "thr-1"
    assert email.from_name == "Иван"
    assert email.from_email == "ivan@company.com"
    assert email.to == ["me@example.com"]
    assert email.cc == ["copy@example.com"]
    assert email.subject == "Тема"
    assert email.body_text == "Тело письма"
    assert email.labels == ["INBOX", "UNREAD"]
    assert email.date == datetime.fromtimestamp(1793097600, tz=UTC)


def test_parse_gmail_message_attachment_metadata() -> None:
    email = parse_gmail_message(_payload(attachment=True))
    assert len(email.attachments) == 1
    attachment = email.attachments[0]
    assert attachment.filename == "счёт.pdf"
    assert attachment.mime_type == "application/pdf"
    assert attachment.attachment_id == "att-1"
    assert attachment.size == 1024


def test_parse_gmail_message_html_only_body() -> None:
    raw = _payload()
    raw["payload"]["parts"] = [
        {
            "mimeType": "text/html",
            "headers": [{"name": "Content-Type", "value": "text/html; charset=utf-8"}],
            "body": {"data": _b64("<p>Только <b>HTML</b></p>")},
        }
    ]
    email = parse_gmail_message(raw)
    assert email.body_html
    assert "HTML" in email.body_text
    assert "<" not in email.body_text


def test_email_roundtrip_through_dict() -> None:
    from models import Email

    original = parse_gmail_message(_payload(attachment=True))
    restored = Email.from_dict(original.to_dict())
    assert restored.to_dict() == original.to_dict()
