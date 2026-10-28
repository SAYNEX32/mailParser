"""Клиент Gmail API: получение списка писем и их нормализация."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from gmail_auth import load_credentials
from models import Email
from parsing import parse_gmail_message

DEFAULT_LABELS: tuple[str, ...] = ("INBOX",)
PAGE_SIZE = 500  # максимум, который отдаёт Gmail за один запрос


class HistoryExpiredError(RuntimeError):
    """Gmail хранит историю ограниченное время — нужна полная пересинхронизация."""


class GmailClient:
    """Тонкая обёртка над users().messages() с нормализацией в Email."""

    def __init__(
        self,
        credentials: Credentials | None = None,
        user_id: str = "me",
        retries: int = 3,
    ) -> None:
        self.user_id = user_id
        self.retries = retries
        self._service = build(
            "gmail",
            "v1",
            credentials=credentials or load_credentials(),
            cache_discovery=False,
        )

    # -- внутреннее -----------------------------------------------------
    def _messages(self):
        return self._service.users().messages()

    def _execute(self, request):
        return request.execute(num_retries=self.retries)

    # -- публичное API ---------------------------------------------------
    def list_ids(
        self,
        query: str | None = None,
        label_ids: Iterable[str] | None = DEFAULT_LABELS,
        max_results: int = 10,
    ) -> list[str]:
        """Возвращает id писем (новые сверху), не больше max_results."""
        ids: list[str] = []
        page_token: str | None = None
        while len(ids) < max_results:
            batch = min(PAGE_SIZE, max_results - len(ids))
            params: dict = {
                "userId": self.user_id,
                "maxResults": batch,
                "pageToken": page_token,
            }
            if query:
                params["q"] = query
            if label_ids:
                params["labelIds"] = list(label_ids)

            response = self._execute(self._messages().list(**params))
            ids.extend(m["id"] for m in response.get("messages") or [] if "id" in m)

            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return ids[:max_results]

    def get_message(self, message_id: str) -> Email:
        """Полное письмо (заголовки + тело + метаданные вложений)."""
        raw = self._execute(
            self._messages().get(userId=self.user_id, id=message_id, format="full")
        )
        return parse_gmail_message(raw)

    def get_headers(
        self, message_id: str, names: list[str] | None = None
    ) -> dict[str, str]:
        """Только заголовки (дешёвый запрос без тела)."""
        raw = self._execute(
            self._messages().get(
                userId=self.user_id,
                id=message_id,
                format="metadata",
                metadataHeaders=names or ["Subject", "From", "Date"],
            )
        )
        payload = raw.get("payload") or {}
        return {
            (h.get("name") or "").lower(): h.get("value") or ""
            for h in payload.get("headers") or []
        }

    def iter_messages(
        self,
        query: str | None = None,
        limit: int = 10,
        label_ids: Iterable[str] | None = DEFAULT_LABELS,
    ) -> Iterator[Email]:
        """Ленивая итерация по письмам: список id → полное письмо."""
        for message_id in self.list_ids(
            query=query, label_ids=label_ids, max_results=limit
        ):
            try:
                yield self.get_message(message_id)
            except HttpError as exc:
                print(f"[!] Не удалось получить письмо {message_id}: {exc}")

    # -- инкрементальная синхронизация (этап 5) ---------------------------
    def profile(self) -> dict:
        """Профиль ящика: emailAddress, messagesTotal, historyId."""
        return self._execute(self._service.users().getProfile(userId=self.user_id))

    def history_id(self) -> str:
        """Текущий historyId ящика — точка отсчёта для следующей синхронизации."""
        return str(self.profile().get("historyId") or "")

    def collect_history(
        self, start_history_id: str, max_messages: int = 1000
    ) -> tuple[list[str], str]:
        """Новые письма начиная с start_history_id → (id писем, новый historyId).

        Бросает HistoryExpiredError, если Gmail уже не хранит такую историю
        (тогда вызывающий код должен сделать полную синхронизацию).
        """
        ids: list[str] = []
        page_token: str | None = None
        new_history = start_history_id
        while True:
            params: dict = {
                "userId": self.user_id,
                "startHistoryId": start_history_id,
                "historyTypes": ["messageAdded"],
            }
            if page_token:
                params["pageToken"] = page_token
            try:
                response = self._execute(self._service.users().history().list(**params))
            except HttpError as exc:
                status = getattr(getattr(exc, "resp", None), "status", None)
                if str(status) == "404":
                    raise HistoryExpiredError(
                        f"история Gmail с {start_history_id} больше не доступна"
                    ) from exc
                raise

            for record in response.get("history") or []:
                new_history = str(record.get("id") or new_history)
                for added in record.get("messagesAdded") or []:
                    message_id = (added.get("message") or {}).get("id")
                    if message_id and message_id not in ids:
                        ids.append(message_id)
            page_token = response.get("nextPageToken")
            if not page_token or len(ids) >= max_messages:
                break

        if new_history == start_history_id:
            new_history = self.history_id()
        return ids[:max_messages], new_history
