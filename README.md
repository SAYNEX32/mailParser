# mailParser

Парсер почты Gmail / Mail.ru с интеллектуальной классификацией писем на четыре
категории: **Важные**, **Полезные**, **Спорные**, **Спам**.

Полная спецификация — [SPEC.md](SPEC.md).

## Текущий статус

Реализован **этап 1**: подключение к Gmail по OAuth 2.0, парсинг MIME-структуры
и выгрузка нормализованных писем в JSON.

| Этап | Статус |
|------|--------|
| 1. Gmail (OAuth 2.0, list/get, парсинг) | ✅ готово |
| 2. Mail.ru (IMAP + пароль приложения) | ⬜ не начато |
| 3. Нормализация в единый формат | ✅ готово (`src/models.py`) |
| 4. Правила классификации (4 категории) | ⬜ не начато |
| 5. Хранение (SQLite/PostgreSQL) | ⬜ не начато |
| 6. ML / гибридный режим (опционально) | ⬜ не начато |

## Структура

```
src/
├── config.py        # конфигурация из .env, пути проекта
├── models.py        # нормализованные модели Email / Attachment
├── gmail_auth.py    # OAuth: загрузка токена и его обновление
├── gmail_client.py  # обёртка над Gmail API (list_ids / get_message)
├── parsing.py       # MIME-парсинг, HTML → plain text
├── main.py          # CLI: выгрузка писем в data/emails.json
└── test_gmail.py    # смоук-тест доступа к Gmail API
```

## Установка

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
cp .env.example .env
```

## Настройка Gmail (OAuth 2.0)

1. Создать проект в Google Cloud Console и включить Gmail API.
2. Создать OAuth 2.0 Client ID типа **Desktop app**.
3. Положить скачанный JSON в `secret/client_secret*.json`.
4. Получить OAuth-токен и сохранить его в файл, указанный в `GMAIL_TOKEN_PATH`
   (по умолчанию — `~/.config/automation/gmail/accounts/<account>/gmail-tokens.json`).

Используемый scope: `https://www.googleapis.com/auth/gmail.readonly`
(для простановки меток потребуется `gmail.modify`).

## Использование

```bash
# проверка доступа к API
python src/test_gmail.py --max 5

# выгрузка 50 последних писем из INBOX в data/emails.json
python src/main.py --limit 50

# произвольный Gmail-запрос, все метки
python src/main.py --limit 20 --query "is:unread" --label all
```

Флаги CLI: `--limit`, `--query`, `--label` (можно несколько раз, `all` — без
фильтра по меткам), `--out`.

## Безопасность

В репозиторий **не попадают**:

- `secret/` — OAuth client secret;
- `.env` — локальные переменные окружения;
- `data/*.json` — выгрузки с реальными письмами (персональные данные);
- `data/*.db`, `data/*.sqlite3` — базы данных.

Токены хранятся вне проекта (`GMAIL_TOKEN_PATH`), не коммитятся.

## Дальше

1. `src/classifier.py` — rule-based правила для четырёх категорий
   (whitelist/blacklist, ключевые слова, `List-Unsubscribe`, спам-паттерны).
2. `src/storage.py` — SQLite через SQLAlchemy (схема из SPEC §7.2)
   и инкрементальная синхронизация по `historyId`.
3. IMAP-клиент для Mail.ru.
4. Сбор обратной связи по «Спорным» и опциональная ML-модель.
