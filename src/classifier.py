"""Правила классификации писем на 4 категории (этап 4, SPEC §6).

Движок чисто эвристический (rule-based): каждое правило даёт вес одной из
категорий, категория письма — максимум суммы весов. «Спорные» работают как
буфер (SPEC §6.4): при смешанных сигналах письмо не улетает автоматически
в «Спам» или «Важные», а остаётся на ручной разбор.

Использование как библиотеки::

    from classifier import classify
    result = classify(email)      # -> Classification

Использование как CLI (калибровка правил на готовой выгрузке)::

    python src/classifier.py data/emails.json
    python src/classifier.py data/emails.json --show disputed --limit 5
    python src/classifier.py data/emails.json --explain
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from config import DATA_DIR, GMAIL_ACCOUNT
from models import CATEGORIES, Classification, Email

# ---------------------------------------------------------------------------
# Словари и списки признаков
# ---------------------------------------------------------------------------
# Срочность / требование действия
URGENT_KEYWORDS: tuple[str, ...] = (
    "срочно",
    "срочн",
    "немедленно",
    "важно",
    "важн",
    "требуется ответ",
    "прошу ответить",
    "нужен ответ",
    "подтвердите",
    "согласуйте",
    "до конца дня",
    "крайний срок",
    "дедлайн",
    "action required",
    "urgent",
    "asap",
    "immediately",
    "deadline",
    "please respond",
    "reply needed",
)

# Деньги, документы, юридика
MONEY_LEGAL_KEYWORDS: tuple[str, ...] = (
    "счёт",
    "счет на оплату",
    "invoice",
    "платёж",
    "платеж",
    "оплата",
    "оплатить",
    "договор",
    "контракт",
    "акт сверки",
    "штраф",
    "налог",
    "суд",
    "юридическ",
    "претензи",
    "задолженност",
    "кредит",
    "ипотек",
)

# Массовые рассылки и маркетинг
MARKETING_KEYWORDS: tuple[str, ...] = (
    "распродажа",
    "скидка",
    "скидки",
    "промокод",
    "акция",
    "выгодно",
    "купить",
    "заказать",
    "подпишись",
    "не пропусти",
    "только сегодня",
    "sale",
    "discount",
    "offer ends",
    "black friday",
    "limited time",
)

# Явный спам: лотереи, азарт, «лёгкие деньги»
SPAM_KEYWORDS: tuple[str, ...] = (
    "выиграли",
    "выигрыш",
    "лотере",
    "казино",
    "ставки",
    "джекпот",
    "you have won",
    "claim your prize",
    "lottery",
    "casino",
    "viagra",
    "crypto giveaway",
    "заработок без вложений",
    "быстрый заработок",
    "инвестируйте",
    "гарантированный доход",
)

# Фишинговая лексика
PHISHING_KEYWORDS: tuple[str, ...] = (
    "подтвердите данные",
    "подтвердите учетную запись",
    "подтвердите учётную запись",
    "войдите в аккаунт",
    "аккаунт будет заблокирован",
    "аккаунт заблокирован",
    "ваша карта заблокирована",
    "обновить платежные данные",
    "обновить платёжные данные",
    "подозрительная активность",
    "verify your account",
    "confirm your identity",
    "unusual activity",
    "suspended",
    "verify your identity",
)

# Рассылочные слова (дайджесты, новости)
DIGEST_KEYWORDS: tuple[str, ...] = (
    "дайджест",
    "digest",
    "newsletter",
    "рассылка",
    "weekly",
    "еженедельн",
    "новости",
    "подборка",
)

# Домены, которым доверяем по умолчанию (сервисные уведомления)
NOTIFICATION_DOMAINS: frozenset[str] = frozenset(
    {
        "github.com",
        "gitlab.com",
        "bitbucket.org",
        "stackoverflow.com",
        "habr.com",
        "google.com",
        "youtube.com",
        "notion.so",
        "slack.com",
        "atlassian.net",
        "jira.com",
        "figma.com",
        "openrouter.ai",
        "openai.com",
        "anthropic.com",
        "telegram.org",
        "linkedin.com",
        "hh.ru",
        "ozon.ru",
        "wildberries.ru",
        "yandex.ru",
        "cloudflare.com",
        "pypi.org",
        "python.org",
        "npmjs.com",
    }
)

# Бренды: если имя отправителя похоже на бренд, а домен чужой — это подделка
BRANDS: dict[str, tuple[str, ...]] = {
    "сбербанк": ("sberbank.ru", "sberbank.com", "sber.ru"),
    "сбер": ("sberbank.ru", "sber.ru"),
    "тинькофф": ("tinkoff.ru", "tbank.ru"),
    "т-банк": ("tbank.ru", "tinkoff.ru"),
    "альфа-банк": ("alfabank.ru", "alfa-bank.ru"),
    "втб": ("vtb.ru",),
    "газпромбанк": ("gazprombank.ru",),
    "райффайзен": ("raiffeisen.ru",),
    "mail.ru": ("mail.ru",),
    "google": ("google.com", "google.ru"),
    "microsoft": ("microsoft.com", "microsoft.ru"),
    "apple": ("apple.com", "icloud.com"),
    "github": ("github.com",),
    "ozon": ("ozon.ru",),
    "wildberries": ("wildberries.ru",),
    "яндекс": ("yandex.ru", "yandex.com", "ya.ru"),
    "telegram": ("telegram.org", "t.me"),
    "whatsapp": ("whatsapp.com",),
    "netflix": ("netflix.com",),
    "paypal": ("paypal.com",),
    "amazon": ("amazon.com", "amazon.ru"),
}

SUSPICIOUS_TLDS: frozenset[str] = frozenset(
    {
        ".zip",
        ".mov",
        ".top",
        ".xyz",
        ".click",
        ".link",
        ".country",
        ".tk",
        ".ml",
        ".ga",
        ".cf",
        ".gq",
        ".work",
        ".rest",
        ".loan",
        ".review",
        ".win",
        ".bid",
        ".download",
        ".racing",
        ".stream",
        ".quest",
        ".cam",
        ".sbs",
        ".cyou",
        ".support",
        ".fit",
    }
)

URL_SHORTENERS: frozenset[str] = frozenset(
    {
        "bit.ly",
        "tinyurl.com",
        "t.co",
        "goo.gl",
        "is.gd",
        "cutt.ly",
        "rb.gy",
        "clck.ru",
        "vk.cc",
        "ow.ly",
        "buff.ly",
        "shorturl.at",
    }
)

EXECUTABLE_EXT: frozenset[str] = frozenset(
    {
        ".exe",
        ".scr",
        ".js",
        ".vbs",
        ".bat",
        ".cmd",
        ".com",
        ".pif",
        ".jar",
        ".msi",
        ".hta",
        ".ps1",
        ".reg",
        ".lnk",
        ".iso",
        ".img",
    }
)

ARCHIVE_EXT: frozenset[str] = frozenset({".zip", ".rar", ".7z", ".ace", ".cab"})

# Пути к пользовательским спискам (по одному адресу/домену в строке, `#` — комментарий)
WHITELIST_PATH: Path = DATA_DIR / "whitelist.txt"
BLACKLIST_PATH: Path = DATA_DIR / "blacklist.txt"

_URL_RE = re.compile(r"https?://([^/\s\"'<>)]+)", re.IGNORECASE)
_TOKEN_RE = re.compile(r"[a-zа-я0-9@._\-]+", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Загрузка списков отправителей
# ---------------------------------------------------------------------------
def load_address_list(path: Path) -> frozenset[str]:
    """Читает файл со списком адресов/доменов (нижний регистр, без комментариев)."""
    if not path.exists():
        return frozenset()
    items: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip().lower()
        if line:
            items.add(line)
    return frozenset(items)


# Белый список — только контакты пользователя (data/whitelist.txt).
DEFAULT_WHITELIST: frozenset[str] = load_address_list(WHITELIST_PATH)
# Доверенные отправители = контакты + известные сервисы (используется в проверках
# «отправитель известен?», но не для правила sender_in_whitelist).
TRUSTED_DOMAINS: frozenset[str] = DEFAULT_WHITELIST | NOTIFICATION_DOMAINS
DEFAULT_BLACKLIST: frozenset[str] = load_address_list(BLACKLIST_PATH)


# ---------------------------------------------------------------------------
# Признаки письма
# ---------------------------------------------------------------------------
@dataclass(slots=True)
class Features:
    """Всё, что нужно правилам, посчитанное один раз."""

    email: Email
    from_email: str
    from_domain: str
    display_name: str
    subject: str
    text: str  # тема + snippet + тело, нижний регистр, «ё» → «е»
    body_text: str
    subject_exclamations: int
    subject_upper_ratio: float
    list_unsubscribe: bool
    precedence_bulk: bool
    auth_failed: bool
    auth_passed: bool
    reply_to_domain: str
    link_domains: tuple[str, ...]
    exec_attachments: tuple[str, ...]
    archive_attachments: tuple[str, ...]
    labels: frozenset[str]
    direct_recipient: bool
    is_reply: bool
    whitelist: frozenset[str]
    blacklist: frozenset[str]
    trusted: frozenset[str]


def _normalize(text: str) -> str:
    """Нижний регистр, «ё» → «е», схлопнутые пробелы — для поиска ключевых слов."""
    return re.sub(r"\s+", " ", (text or "").lower().replace("ё", "е"))


def _domain_of(address: str) -> str:
    """'Ivan <i@Example.COM>' → 'example.com'."""
    address = (address or "").strip().lower()
    if "<" in address and ">" in address:
        address = address[address.rfind("<") + 1 : address.rfind(">")]
    return address.rpartition("@")[2].strip(" .>")


def _host_of_url(url_domain: str) -> str:
    """'user:pass@Example.com:8080' → 'example.com'."""
    host = url_domain.rpartition("@")[2].split("/")[0]
    if host.count(":") == 1:
        host = host.split(":", 1)[0]
    return host.lower().strip(" .")


def _url_domains(text: str) -> tuple[str, ...]:
    hosts = (_host_of_url(m.group(1)) for m in _URL_RE.finditer(text or ""))
    return tuple(dict.fromkeys(h for h in hosts if h))


def _upper_ratio(text: str) -> float:
    letters = [ch for ch in text or "" if ch.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for ch in letters if ch.isupper()) / len(letters)


def _matches_any(text: str, keywords: Iterable[str]) -> bool:
    return any(k in text for k in keywords)


def _address_matches(addr: str, domain: str, entries: frozenset[str]) -> bool:
    """Точное совпадение адреса либо домена (включая поддомены)."""
    if not entries:
        return False
    if addr and addr in entries:
        return True
    return any(
        domain == entry or (domain.endswith("." + entry) if "." in entry else False)
        for entry in entries
    )


def extract_features(
    email: Email,
    whitelist: frozenset[str] | None = None,
    blacklist: frozenset[str] | None = None,
) -> Features:
    """Считает признаки письма для правил (без побочных эффектов)."""
    headers = {k.lower(): v for k, v in (email.headers_raw or {}).items()}
    from_email = (email.from_email or "").strip().lower()
    from_domain = _domain_of(from_email)
    auth_header = _normalize(
        " ".join(
            headers.get(name, "")
            for name in (
                "authentication-results",
                "arc-authentication-results",
                "received-spf",
            )
        )
    )
    auth_failed = (
        any(f"{marker}=fail" in auth_header for marker in ("spf", "dkim", "dmarc"))
        or "dmarc=fail" in auth_header
    )
    auth_passed = "dmarc=pass" in auth_header or "spf=pass" in auth_header

    unsubscribe = "list-unsubscribe" in headers
    precedence = _normalize(
        " ".join((headers.get("precedence", ""), headers.get("list-id", "")))
    )
    precedence_bulk = (
        any(marker in precedence for marker in ("bulk", "list", "junk"))
        or "list-id" in headers
    )

    account = (GMAIL_ACCOUNT or "").strip().lower()
    recipients = {a.strip().lower() for a in (*email.to, *email.cc) if a}
    direct_recipient = account in recipients if account else not unsubscribe

    filenames = [a.filename.lower() for a in email.attachments if a.filename]
    body = email.body_text or ""
    text = _normalize("\n".join(filter(None, (email.subject, email.snippet, body))))

    return Features(
        email=email,
        from_email=from_email,
        from_domain=from_domain,
        display_name=(email.from_name or "").strip().lower(),
        subject=email.subject or "",
        text=text,
        body_text=body,
        subject_exclamations=(email.subject or "").count("!"),
        subject_upper_ratio=_upper_ratio(email.subject or ""),
        list_unsubscribe=unsubscribe,
        precedence_bulk=precedence_bulk,
        auth_failed=auth_failed,
        auth_passed=auth_passed,
        reply_to_domain=_domain_of(headers.get("reply-to", "")),
        link_domains=_url_domains(body or text),
        exec_attachments=tuple(
            f for f in filenames if any(f.endswith(ext) for ext in EXECUTABLE_EXT)
        ),
        archive_attachments=tuple(
            f for f in filenames if any(f.endswith(ext) for ext in ARCHIVE_EXT)
        ),
        labels=frozenset(email.labels or ()),
        direct_recipient=direct_recipient,
        is_reply="in-reply-to" in headers
        or bool(
            re.match(r"\s*(re|re:|aw|fw|fwd)\s*:", email.subject or "", re.IGNORECASE)
        ),
        whitelist=DEFAULT_WHITELIST if whitelist is None else whitelist,
        blacklist=DEFAULT_BLACKLIST if blacklist is None else blacklist,
        trusted=TRUSTED_DOMAINS | (frozenset() if whitelist is None else whitelist),
    )


# ---------------------------------------------------------------------------
# Правила
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Rule:
    """Одно эвристическое правило: id, категория, вес и предикат."""

    id: str
    category: str
    weight: float
    check: Callable[[Features], bool]
    note: str


def _brand_mismatch(f: Features) -> bool:
    """Имя отправителя содержит бренд, а домен бренду не принадлежит."""
    if not f.display_name or not f.from_domain:
        return False
    if _address_matches(f.from_email, f.from_domain, f.trusted):
        return False
    for brand, allowed in BRANDS.items():
        if brand in f.display_name:
            return not any(
                f.from_domain == d or f.from_domain.endswith("." + d) for d in allowed
            )
    return False


def _financial_lure(f: Features) -> bool:
    """Фишинговая лексика или денежная лексика со ссылкой/вложением от неизвестного."""
    if _address_matches(f.from_email, f.from_domain, f.trusted):
        return False
    if _matches_any(f.text, PHISHING_KEYWORDS):
        return True
    has_payload = bool(f.link_domains or f.exec_attachments or f.archive_attachments)
    return has_payload and _matches_any(f.text, MONEY_LEGAL_KEYWORDS)


def _suspicious_links(f: Features) -> bool:
    return any(any(d.endswith(tld) for tld in SUSPICIOUS_TLDS) for d in f.link_domains)


RULES: tuple[Rule, ...] = (
    # --- чёрный список: сильнейший сигнал -------------------------------
    Rule(
        "sender_in_blacklist",
        "spam",
        5.0,
        lambda f: _address_matches(f.from_email, f.from_domain, f.blacklist),
        "отправитель в чёрном списке",
    ),
    Rule(
        "gmail_spam_label",
        "spam",
        5.0,
        lambda f: bool(f.labels & {"SPAM", "TRASH"}),
        "письмо уже помечено в Gmail как спам/корзина",
    ),
    # --- явный спам -----------------------------------------------------
    Rule(
        "lottery_or_gambling_language",
        "spam",
        2.5,
        lambda f: _matches_any(f.text, SPAM_KEYWORDS),
        "лексика лотерей/азартных игр/«лёгких денег»",
    ),
    Rule(
        "marketing_without_subscription",
        "spam",
        1.2,
        lambda f: _matches_any(f.text, MARKETING_KEYWORDS) and not f.list_unsubscribe,
        "реклама без заголовка List-Unsubscribe",
    ),
    Rule(
        "subject_shouting",
        "spam",
        0.8,
        lambda f: len(f.subject) >= 12 and f.subject_upper_ratio >= 0.7,
        "тема капсом",
    ),
    Rule(
        "many_exclamations",
        "spam",
        0.8,
        lambda f: f.subject_exclamations >= 3,
        "много восклицательных знаков в теме",
    ),
    # --- важные ---------------------------------------------------------
    Rule(
        "sender_in_whitelist",
        "important",
        1.5,
        lambda f: _address_matches(f.from_email, f.from_domain, f.whitelist),
        "отправитель в белом списке",
    ),
    Rule(
        "gmail_important_label",
        "important",
        1.2,
        lambda f: "IMPORTANT" in f.labels,
        "Gmail помечает письмо как важное",
    ),
    Rule(
        "urgent_keyword",
        "important",
        1.5,
        lambda f: _matches_any(f.text, URGENT_KEYWORDS),
        "в тексте есть слова срочности/требования действия",
    ),
    Rule(
        "money_legal_keyword",
        "important",
        1.2,
        lambda f: _matches_any(f.text, MONEY_LEGAL_KEYWORDS),
        "счёт/договор/юридическая лексика",
    ),
    Rule(
        "thread_reply",
        "important",
        0.9,
        lambda f: f.is_reply,
        "ответ в существующей переписке",
    ),
    Rule(
        "direct_recipient_personal",
        "important",
        0.6,
        lambda f: (
            f.direct_recipient and not f.list_unsubscribe and not f.precedence_bulk
        ),
        "письмо адресовано лично, без признаков рассылки",
    ),
    # --- полезные -------------------------------------------------------
    Rule(
        "bulk_newsletter",
        "useful",
        1.5,
        lambda f: f.list_unsubscribe and not _matches_any(f.text, PHISHING_KEYWORDS),
        "легитимная рассылка (есть List-Unsubscribe)",
    ),
    Rule(
        "notification_service",
        "useful",
        1.5,
        lambda f: _address_matches(f.from_email, f.from_domain, NOTIFICATION_DOMAINS),
        "сервисное уведомление известного сервиса",
    ),
    Rule(
        "digest_keyword",
        "useful",
        0.8,
        lambda f: _matches_any(f.text, DIGEST_KEYWORDS),
        "дайджест/новостная подборка",
    ),
    Rule(
        "gmail_update_label",
        "useful",
        0.6,
        lambda f: "CATEGORY_UPDATES" in f.labels,
        "Gmail относит письмо к «Обновлениям»",
    ),
    Rule(
        "gmail_social_label",
        "useful",
        0.4,
        lambda f: "CATEGORY_SOCIAL" in f.labels,
        "Gmail относит письмо к «Соцсетям»",
    ),
    # --- спорные --------------------------------------------------------
    Rule(
        "auth_failed",
        "disputed",
        2.0,
        lambda f: f.auth_failed,
        "не пройдена проверка SPF/DKIM/DMARC",
    ),
    Rule(
        "brand_spoofing",
        "disputed",
        2.5,
        _brand_mismatch,
        "имя отправителя имитирует бренд, домен чужой",
    ),
    Rule(
        "financial_lure_from_unknown_sender",
        "disputed",
        1.5,
        _financial_lure,
        "фишинговая лексика или денежная лексика со ссылкой от неизвестного отправителя",
    ),
    Rule(
        "suspicious_link_domain",
        "disputed",
        1.5,
        _suspicious_links,
        "ссылка на домен с подозрительной зоной",
    ),
    Rule(
        "url_shortener_link",
        "disputed",
        1.2,
        lambda f: any(d in URL_SHORTENERS for d in f.link_domains),
        "ссылка через сокращатель URL",
    ),
    Rule(
        "executable_attachment_unknown_sender",
        "disputed",
        2.5,
        lambda f: (
            bool(f.exec_attachments)
            and not _address_matches(f.from_email, f.from_domain, f.trusted)
        ),
        "исполняемое вложение от неизвестного отправителя",
    ),
    Rule(
        "archive_attachment_unknown_sender",
        "disputed",
        1.0,
        lambda f: (
            bool(f.archive_attachments)
            and not _address_matches(f.from_email, f.from_domain, f.trusted)
        ),
        "архив во вложении от неизвестного отправителя",
    ),
    Rule(
        "punycode_domain",
        "disputed",
        1.5,
        lambda f: "xn--" in f.from_domain or any("xn--" in d for d in f.link_domains),
        "домен в punycode (возможна подмена символов)",
    ),
    Rule(
        "reply_to_domain_mismatch",
        "disputed",
        1.0,
        lambda f: (
            bool(f.reply_to_domain)
            and bool(f.from_domain)
            and f.reply_to_domain != f.from_domain
        ),
        "Reply-To указывает на другой домен",
    ),
)

SPAM_OVERRIDE_THRESHOLD = 4.0  # чёрный список / метка Gmail
CONFIDENT_THRESHOLD = 0.5  # ниже — считаем, что правил не хватило


# ---------------------------------------------------------------------------
# Классификация
# ---------------------------------------------------------------------------
def score_features(features: Features) -> tuple[dict[str, float], list[str], list[str]]:
    """Считает веса категорий и списки сработавших правил."""
    scores: dict[str, float] = defaultdict(float)
    reasons: list[str] = []
    explanation: list[str] = []
    for rule in RULES:
        try:
            matched = rule.check(features)
        except Exception:  # noqa: BLE001 - одно правило не должно ломать классификацию
            matched = False
        if matched:
            scores[rule.category] += rule.weight
            reasons.append(rule.id)
            explanation.append(rule.note)
    return dict(scores), reasons, explanation


def decide(scores: dict[str, float]) -> str:
    """Приоритеты категорий с буфером для «Спорных» (SPEC §6.4)."""
    important = scores.get("important", 0.0)
    useful = scores.get("useful", 0.0)
    disputed = scores.get("disputed", 0.0)
    spam = scores.get("spam", 0.0)

    # Явный спам (чёрный список, метка Gmail, лотерея+маркетинг) — не спорный
    if spam >= SPAM_OVERRIDE_THRESHOLD:
        return "spam"

    # Смешанные сигналы: подозрения + (важное или спам) — на ручной разбор
    if disputed >= 1.5 and (important >= 1.0 or spam >= 1.5):
        return "disputed"
    # «Срочно!!! Распродажа» — не спам и не важное, а именно спорное
    if important >= 1.0 and spam >= 1.5:
        return "disputed"

    best = max(CATEGORIES, key=lambda c: scores.get(c, 0.0))
    if scores.get(best, 0.0) < CONFIDENT_THRESHOLD:
        return "disputed"
    # «Полезные» не должны перебивать более сильные сигналы «Важных»
    if best == "useful" and important > useful:
        return "important"
    return best


def confidence_of(category: str, scores: dict[str, float]) -> float:
    """Уверенность 0.40–0.99: отрыв от ближайшей категории × «насыщенность» весов."""
    score = scores.get(category, 0.0)
    if score <= 0:
        return 0.40
    runner_up = max(
        (value for cat, value in scores.items() if cat != category), default=0.0
    )
    margin = (score - runner_up) / score  # 0..1 — насколько оторвались
    saturation = min(1.0, score / 3.0)  # слабый набор правил не даёт 0.99
    value = 0.5 + 0.49 * margin * saturation
    return round(min(0.99, max(0.40, value)), 2)


def classify(
    email: Email,
    whitelist: frozenset[str] | None = None,
    blacklist: frozenset[str] | None = None,
) -> Classification:
    """Классифицирует письмо и возвращает результат (SPEC §7.1)."""
    features = extract_features(email, whitelist=whitelist, blacklist=blacklist)
    scores, reasons, explanation = score_features(features)
    category = decide(scores)
    if not reasons:
        reasons = ["no_rule_matched"]
        explanation = ["ни одно правило не сработало — письмо на ручной разбор"]
    return Classification(
        category=category,
        confidence=confidence_of(category, scores),
        method="rules",
        reasons=reasons,
        explanation=explanation,
    )


def classify_all(emails: Iterable[Email]) -> list[Email]:
    """Классифицирует пачку писем на месте (мутирует поле classification)."""
    for email in emails:
        email.classification = classify(email)
    return list(emails)


# ---------------------------------------------------------------------------
# CLI для калибровки правил на готовой выгрузке
# ---------------------------------------------------------------------------
ICONS = {"important": "!!", "useful": "..", "disputed": "??", "spam": "XX"}


def _load_emails(path: Path) -> list[Email]:
    with path.open(encoding="utf-8") as fh:
        raw = json.load(fh)
    if isinstance(raw, dict):
        raw = raw.get("emails") or []
    return [Email.from_dict(item) for item in raw]


def _print_distribution(
    categories: Counter[str], total: int, rules: Counter[str] | None = None
) -> None:
    print("\nРаспределение по категориям:")
    for category in CATEGORIES:
        count = categories.get(category, 0)
        share = (count / total * 100) if total else 0.0
        bar = "█" * round(share / 4)
        print(f"  {ICONS[category]} {category:<10} {count:>4}  {share:>5.1f}%  {bar}")
    if rules:
        top_reasons = ", ".join(f"{rid} ({n})" for rid, n in rules.most_common(5))
        print(f"\n  топ правил: {top_reasons}")


def _print_email(email: Email, *, explain: bool) -> None:
    result = email.classification
    assert result is not None
    date = f"{email.date:%Y-%m-%d}" if email.date else "----------"
    subject = email.subject[:58] + ("…" if len(email.subject) > 58 else "")
    print(
        f"  {ICONS[result.category]} {result.category:<9} {result.confidence:.2f} "
        f"{date} | {email.from_email[:32]:<32} | {subject}"
    )
    if explain:
        for note in result.explanation:
            print(f"       └─ {note}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Классификация писем по правилам (калибровка на JSON-выгрузке)"
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=str(DATA_DIR / "emails.json"),
        help="JSON с письмами (по умолчанию data/emails.json)",
    )
    parser.add_argument(
        "--show",
        choices=CATEGORIES,
        action="append",
        default=None,
        help="Показать только выбранные категории (можно несколько раз)",
    )
    parser.add_argument("--limit", type=int, default=0, help="Сколько писем показать")
    parser.add_argument(
        "--explain", action="store_true", help="Печатать сработавшие правила"
    )
    parser.add_argument(
        "--out", default=None, help="Сохранить письма с классификацией в JSON"
    )
    args = parser.parse_args(argv)

    path = Path(args.path)
    if not path.exists():
        sys.exit(f"[!] Файл не найден: {path}")

    emails = _load_emails(path)
    if not emails:
        print("[i] Писем нет")
        return 0

    categories = Counter[str]()
    rules = Counter[str]()
    shown = 0
    for email in emails:
        result = classify(email)
        email.classification = result
        categories[result.category] += 1
        rules.update(result.reasons)
        if args.show and result.category not in args.show:
            continue
        if args.limit and shown >= args.limit:
            continue
        _print_email(email, explain=args.explain)
        shown += 1

    _print_distribution(categories, len(emails), rules)
    print(f"\n[i] Правил сработало: {sum(rules.values())} (уникальных: {len(rules)})")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as fh:
            json.dump(
                [email.to_dict() for email in emails], fh, ensure_ascii=False, indent=2
            )
        print(f"[+] Результат сохранён → {out}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    raise SystemExit(main())
