"""Парсинг MIME-структуры письма и нормализация в модель Email."""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any

from models import Attachment, Email

# ---------------------------------------------------------------------------
# HTML → plain text (stdlib, без внешних зависимостей)
# ---------------------------------------------------------------------------
_SKIP_TAGS = {"script", "style", "head", "title"}
_BLOCK_TAGS = {
    "p",
    "div",
    "br",
    "li",
    "tr",
    "hr",
    "section",
    "article",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "blockquote",
    "table",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._chunks.append(data)

    @property
    def text(self) -> str:
        return "".join(self._chunks)


def html_to_text(html: str) -> str:
    """Грубое, но предсказуемое преобразование HTML в читаемый текст."""
    if not html:
        return ""
    extractor = _TextExtractor()
    try:
        extractor.feed(html)
        extractor.close()
    except Exception:  # noqa: BLE001 - битый HTML не должен ломать разбор
        return re.sub(r"<[^>]+>", " ", html)
    text = extractor.text.replace("\xa0", " ").replace("\r", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Низкоуровневые помощники
# ---------------------------------------------------------------------------
def decode_base64url(data: str, charset: str = "utf-8") -> str:
    """Декодирует base64url из Gmail API с учётом charset части (padding может отсутствовать)."""
    if not data:
        return ""
    padding = "=" * (-len(data) % 4)
    try:
        raw = base64.urlsafe_b64decode(data + padding)
    except (binascii.Error, ValueError):
        return ""
    for encoding in (charset, "utf-8", "cp1251", "latin-1"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def part_charset(part: dict[str, Any]) -> str:
    """charset из заголовка Content-Type части MIME."""
    content_type = headers_to_dict(part.get("headers")).get("content-type", "")
    match = re.search(r'charset="?([\w\-:.]+)"?', content_type, re.IGNORECASE)
    return match.group(1) if match else "utf-8"


def headers_to_dict(headers: list[dict[str, str]] | None) -> dict[str, str]:
    """Список заголовков Gmail → dict (lower-case ключи, дубликаты через '\\n')."""
    result: dict[str, str] = {}
    for header in headers or []:
        name = (header.get("name") or "").lower()
        value = header.get("value") or ""
        if not name:
            continue
        result[name] = f"{result[name]}\n{value}" if name in result else value
    return result


def split_addresses(raw: str) -> list[str]:
    """'Имя <a@b>, c@d' → ['a@b', 'c@d']."""
    return [addr for _, addr in getaddresses([raw or ""]) if addr]


def parse_address(raw: str) -> tuple[str, str]:
    """'Иван Петров <ivan@company.com>' → ('Иван Петров', 'ivan@company.com')."""
    pairs = getaddresses([raw or ""])
    if not pairs:
        return "", ""
    name, addr = pairs[0]
    return (name or "").strip().strip('"'), (addr or "").strip()


def parse_date(raw: str | None) -> datetime | None:
    """RFC 2822 дата из заголовка → aware datetime (UTC при отсутствии зоны)."""
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _iter_parts(payload: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield payload
    for part in payload.get("parts") or []:
        yield from _iter_parts(part)


# ---------------------------------------------------------------------------
# Gmail → Email
# ---------------------------------------------------------------------------
def parse_gmail_message(raw: dict[str, Any]) -> Email:
    """Нормализует ответ users.messages.get (format='full') в модель Email."""
    payload = raw.get("payload") or {}
    headers = headers_to_dict(payload.get("headers"))

    body_text = ""
    body_html = ""
    attachments: list[Attachment] = []

    for part in _iter_parts(payload):
        mime_type = (part.get("mimeType") or "").lower()
        filename = part.get("filename") or ""
        body = part.get("body") or {}
        attachment_id = body.get("attachmentId")

        if filename and attachment_id:
            attachments.append(
                Attachment(
                    filename=filename,
                    mime_type=mime_type,
                    size=int(body.get("size") or 0),
                    attachment_id=attachment_id,
                )
            )
            continue

        data = body.get("data")
        if not data:
            continue

        decoded = decode_base64url(data, part_charset(part))
        if mime_type == "text/plain" and not body_text:
            body_text = decoded
        elif mime_type == "text/html" and not body_html:
            body_html = decoded

    if not body_text and body_html:
        body_text = html_to_text(body_html)

    from_name, from_email = parse_address(headers.get("from", ""))

    date: datetime | None = None
    internal_ms = raw.get("internalDate")
    if internal_ms:
        date = datetime.fromtimestamp(int(internal_ms) / 1000, tz=UTC)
    if date is None:
        date = parse_date(headers.get("date"))

    return Email(
        source="gmail",
        message_id=raw.get("id", ""),
        thread_id=raw.get("threadId"),
        from_name=from_name,
        from_email=from_email,
        to=split_addresses(headers.get("to", "")),
        cc=split_addresses(headers.get("cc", "")),
        subject=headers.get("subject", ""),
        date=date,
        snippet=raw.get("snippet", ""),
        body_text=body_text.strip(),
        body_html=body_html,
        attachments=attachments,
        headers_raw=headers,
        labels=list(raw.get("labelIds") or []),
    )
