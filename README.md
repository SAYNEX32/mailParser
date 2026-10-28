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
```

## Конфигурация (`.env`)

Файл `.env` **не хранится в репозитории** и создаётся вручную в корне проекта
(рядом с `README.md`). Всё, что в нём не задано, берётся из значений по
умолчанию в `src/config.py` — для локального запуска достаточно только
`GMAIL_ACCOUNT`.

Windows (PowerShell):

```powershell
New-Item .env
notepad .env
```

Linux / macOS:

```bash
touch .env && $EDITOR .env
```

Шаблон — скопируйте в `.env` и заполните:

```dotenv
# --- Gmail ---
GMAIL_ACCOUNT=you@gmail.com

# Необязательно: путь к сохранённым OAuth-токенам
# GMAIL_TOKEN_PATH=~/.config/automation/gmail/accounts/you-gmail-com/gmail-tokens.json

# Необязательно: путь к OAuth client (Desktop app)
# GMAIL_CLIENT_SECRET_PATH=secret/client_secret_...apps.googleusercontent.com.json

# --- Хранилище (этап 5) ---
# DATABASE_URL=sqlite:///data/emails.db

# --- Mail.ru (этап 2) ---
# MAILRU_EMAIL=
# MAILRU_APP_PASSWORD=
```

| Переменная | Обязательна | По умолчанию | Назначение |
|---|---|---|---|
| `GMAIL_ACCOUNT` | нет | `artemmichin32ru@gmail.com` | Ящик, с которым работаем |
| `GMAIL_TOKEN_PATH` | нет | `~/.config/automation/gmail/accounts/<account>/gmail-tokens.json` | Файл с OAuth-токенами (вне проекта) |
| `GMAIL_CLIENT_SECRET_PATH` | нет | первый `secret/client_secret*.json` | OAuth client типа Desktop app |
| `DATABASE_URL` | нет | `sqlite:///data/emails.db` | Хранилище (пока не используется) |
| `MAILRU_EMAIL` | нет | — | Ящик Mail.ru (этап 2) |
| `MAILRU_APP_PASSWORD` | нет | — | Пароль для внешнего приложения (этап 2) |

## Настройка Gmail (OAuth 2.0)

1. Создать проект в Google Cloud Console и включить Gmail API.
2. Создать OAuth 2.0 Client ID типа **Desktop app**.
3. Положить скачанный JSON в `secret/client_secret*.json` (папка `secret/` в
   `.gitignore`).
4. Получить OAuth-токен и сохранить его в файл, указанный в `GMAIL_TOKEN_PATH`
   (по умолчанию — `~/.config/automation/gmail/accounts/<account>/gmail-tokens.json`).
   Ожидаемый формат — `accessToken` / `refreshToken` / `scope` (поддерживаются
   и snake_case-варианты `access_token` / `refresh_token`):

   ```json
   {
     "accessToken": "ya29...",
     "refreshToken": "1//...",
     "scope": "https://www.googleapis.com/auth/gmail.readonly",
     "token_uri": "https://oauth2.googleapis.com/token"
   }
   ```

   Access-токен обновляется автоматически по `refresh_token` (`src/gmail_auth.py`).

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
- `.env` и `.env.example` — локальные переменные окружения (шаблон — в этом
  README, раздел «Конфигурация (.env)»);
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
