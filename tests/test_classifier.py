"""Тесты правил классификации (этап 4)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from classifier import (
    DEFAULT_BLACKLIST,
    DEFAULT_WHITELIST,
    RULES,
    classify,
    decide,
    extract_features,
    load_address_list,
)
from models import CATEGORIES, Attachment, Email

BOSS = "boss@company.ru"
SCAMMER = "promo@super-prize.top"


def make_email(
    *,
    sender: str = "unknown@random-domain.com",
    name: str = "",
    subject: str = "Обычное письмо",
    body: str = "Текст письма без ссылок.",
    headers: dict[str, str] | None = None,
    labels: list[str] | None = None,
    attachments: list[Attachment] | None = None,
    to: list[str] | None = None,
) -> Email:
    local, _, domain = sender.partition("@")
    return Email(
        source="gmail",
        message_id="test-1",
        from_name=name,
        from_email=f"{local}@{domain}",
        to=to or ["me@example.com"],
        subject=subject,
        date=datetime(2026, 10, 28, tzinfo=UTC),
        snippet=body[:80],
        body_text=body,
        attachments=attachments or [],
        headers_raw=headers or {},
        labels=labels or ["INBOX"],
    )


def test_default_lists_files_are_optional(tmp_path) -> None:
    assert load_address_list(tmp_path / "missing.txt") == frozenset()
    path = tmp_path / "list.txt"
    path.write_text("# комментарий\nBOSS@company.ru\n\nexample.com\n", encoding="utf-8")
    assert load_address_list(path) == frozenset({"boss@company.ru", "example.com"})


def test_all_rule_ids_are_unique() -> None:
    ids = [rule.id for rule in RULES]
    assert len(ids) == len(set(ids))
    assert all(rule.category in CATEGORIES for rule in RULES)


# --- важные -----------------------------------------------------------------
def test_whitelisted_sender_is_important() -> None:
    email = make_email(sender=BOSS, name="Начальник", subject="Отчёт за квартал")
    result = classify(email, whitelist=frozenset({BOSS}))
    assert result.category == "important"
    assert "sender_in_whitelist" in result.reasons
    assert result.method == "rules"


def test_urgent_keyword_is_important() -> None:
    email = make_email(subject="Срочно: согласование договора")
    result = classify(email, whitelist=frozenset({BOSS}))
    assert result.category == "important"
    assert "urgent_keyword" in result.reasons


def test_reply_in_thread_is_important() -> None:
    email = make_email(
        subject="Re: бюджет на 2027",
        headers={"in-reply-to": "<abc@mail.ru>"},
    )
    assert classify(email, whitelist=frozenset()).category == "important"


# --- полезные ---------------------------------------------------------------
def test_newsletter_with_unsubscribe_is_useful() -> None:
    email = make_email(
        sender="news@digest.example",
        subject="Дайджест недели: 10 статей",
        body="Подборка материалов недели.",
        headers={"list-unsubscribe": "<https://digest.example/unsub>"},
    )
    result = classify(email)
    assert result.category == "useful"
    assert "bulk_newsletter" in result.reasons


def test_known_service_notification_is_useful() -> None:
    email = make_email(sender="notifications@github.com", subject="[repo] new issue")
    assert classify(email).category == "useful"


# --- спорные -----------------------------------------------------------------
def test_phishing_with_auth_failure_is_disputed() -> None:
    email = make_email(
        sender="security@bank-secure.top",
        name="Сбербанк",
        subject="Ваша карта заблокирована",
        body="Подтвердите данные карты: https://bank-secure.top/login",
        headers={"authentication-results": "spf=fail dkim=fail dmarc=fail"},
    )
    result = classify(email)
    assert result.category == "disputed"
    assert "auth_failed" in result.reasons
    assert "brand_spoofing" in result.reasons


def test_urgent_plus_spam_signals_is_disputed() -> None:
    """Смешанные сигналы не должны улетать в спам (SPEC §6.4)."""
    email = make_email(
        subject="СРОЧНО!!! Только сегодня распродажа",
        body="Купить со скидкой 90 процентов, промокод внутри",
    )
    assert classify(email).category == "disputed"


def test_executable_attachment_from_unknown_sender_is_disputed() -> None:
    email = make_email(
        subject="Ваш счёт во вложении",
        attachments=[
            Attachment(filename="invoice.pdf.exe", mime_type="application/x-msdownload")
        ],
    )
    result = classify(email)
    assert result.category == "disputed"
    assert "executable_attachment_unknown_sender" in result.reasons


def test_url_shortener_is_disputed() -> None:
    email = make_email(body="Заберите бонус: https://bit.ly/abc123")
    result = classify(email)
    assert result.category == "disputed"
    assert "url_shortener_link" in result.reasons


def test_punycode_domain_is_disputed() -> None:
    email = make_email(sender="info@xn--80ak6aa92e.com", subject="Проверка")
    assert classify(email).category == "disputed"


def test_unknown_sender_without_signals_is_disputed() -> None:
    result = classify(make_email(body="Добрый день. Пишу по поводу встречи."))
    assert result.category == "disputed"
    assert result.reasons == ["no_rule_matched"]
    assert result.confidence == pytest.approx(0.40)


def test_disputed_is_never_upgraded_to_spam_by_weak_rules() -> None:
    f = extract_features(make_email(sender=SCAMMER))
    scores = {"important": 0.0, "useful": 0.0, "disputed": 3.0, "spam": 1.7}
    assert decide(scores) == "disputed"
    assert f.from_domain == "super-prize.top"


# --- спам -------------------------------------------------------------------
def test_lottery_language_is_spam() -> None:
    email = make_email(
        sender="winner@prize-center.xyz",
        subject="Вы выиграли миллион",
        body="Поздравляем, вы выиграли! Claim your prize now.",
    )
    result = classify(email)
    assert result.category == "spam"
    assert "lottery_or_gambling_language" in result.reasons


def test_blacklisted_sender_is_spam() -> None:
    email = make_email(sender="spam@bad.example", subject="Скидки")
    result = classify(email, blacklist=frozenset({"bad.example"}))
    assert result.category == "spam"
    assert result.confidence == 0.99


def test_gmail_spam_label_is_spam() -> None:
    email = make_email(labels=["SPAM"], subject="Что-то подозрительное")
    assert classify(email).category == "spam"


def test_marketing_without_unsubscribe_is_spam() -> None:
    email = make_email(
        sender="sales@shop.example",
        subject="Распродажа",
        body="Скидки до 70 процентов, промокод SALE",
    )
    assert classify(email).category == "spam"


# --- общее ------------------------------------------------------------------
def test_confidence_bounds() -> None:
    for sender, subject in (
        (BOSS, "Отчёт"),
        ("x@y.zz", "привет"),
        ("winner@prize.xyz", "Вы выиграли"),
    ):
        result = classify(make_email(sender=sender, subject=subject))
        assert 0.40 <= result.confidence <= 0.99


def test_whitelisted_sender_plus_marketing_stays_decided() -> None:
    email = make_email(
        sender=BOSS,
        subject="Скидка для сотрудников",
        body="Промокод на скидку для сотрудников",
    )
    result = classify(email, whitelist=frozenset({BOSS}))
    assert result.category in {"important", "disputed"}
    assert result.category != "spam"


def test_default_lists_are_sets() -> None:
    assert isinstance(DEFAULT_WHITELIST, frozenset)
    assert isinstance(DEFAULT_BLACKLIST, frozenset)


def test_scores_and_explanation_are_consistent() -> None:
    email = make_email(
        sender="noreply@accounts.google.com",
        subject="Оповещение системы безопасности",
        body="Выполнен вход в ваш аккаунт.",
    )
    result = classify(email)
    assert len(result.reasons) == len(result.explanation)
    assert result.reasons
