"""Нормализованные модели данных (единый формат для Gmail и Mail.ru)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class Attachment:
    """Метаданные вложения (без содержимого)."""

    filename: str
    mime_type: str
    size: int = 0
    attachment_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Email:
    """Письмо, приведённое к единой структуре (см. SPEC §5.2, §7.1)."""

    source: str
    message_id: str
    from_name: str = ""
    from_email: str = ""
    to: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    subject: str = ""
    date: datetime | None = None
    snippet: str = ""
    body_text: str = ""
    body_html: str = ""
    attachments: list[Attachment] = field(default_factory=list)
    headers_raw: dict[str, str] = field(default_factory=dict)
    labels: list[str] = field(default_factory=list)
    thread_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["date"] = self.date.isoformat() if self.date else None
        return data
