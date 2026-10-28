"""Нормализованные модели данных (единый формат для Gmail и Mail.ru)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

# Категории классификации (см. SPEC §6.1)
CATEGORIES: tuple[str, ...] = ("important", "useful", "disputed", "spam")


@dataclass(slots=True)
class Attachment:
    """Метаданные вложения (без содержимого)."""

    filename: str
    mime_type: str
    size: int = 0
    attachment_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Attachment:
        return cls(
            filename=data.get("filename") or "",
            mime_type=data.get("mime_type") or "",
            size=int(data.get("size") or 0),
            attachment_id=data.get("attachment_id"),
        )


@dataclass(slots=True)
class Classification:
    """Результат классификации письма (см. SPEC §7.1)."""

    category: str
    confidence: float = 0.0
    method: str = "rules"
    reasons: list[str] = field(default_factory=list)
    explanation: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Classification:
        return cls(
            category=data.get("category") or "disputed",
            confidence=float(data.get("confidence") or 0.0),
            method=data.get("method") or "rules",
            reasons=list(data.get("reasons") or []),
            explanation=list(data.get("explanation") or []),
        )


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
    classification: Classification | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["date"] = self.date.isoformat() if self.date else None
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Email:
        """Обратная сборка из JSON (нужна для повторной классификации выгрузок)."""
        raw_date = data.get("date")
        date: datetime | None = None
        if raw_date:
            try:
                date = datetime.fromisoformat(raw_date)
            except ValueError:
                date = None

        raw_classification = data.get("classification")

        return cls(
            source=data.get("source") or "",
            message_id=data.get("message_id") or "",
            from_name=data.get("from_name") or "",
            from_email=data.get("from_email") or "",
            to=list(data.get("to") or []),
            cc=list(data.get("cc") or []),
            subject=data.get("subject") or "",
            date=date,
            snippet=data.get("snippet") or "",
            body_text=data.get("body_text") or "",
            body_html=data.get("body_html") or "",
            attachments=[
                Attachment.from_dict(item) for item in data.get("attachments") or []
            ],
            headers_raw=dict(data.get("headers_raw") or {}),
            labels=list(data.get("labels") or []),
            thread_id=data.get("thread_id"),
            classification=(
                Classification.from_dict(raw_classification)
                if raw_classification
                else None
            ),
        )
