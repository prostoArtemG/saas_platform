# saas_platform

Чистый MVP-каркас SaaS-платформы: **FastAPI + aiogram (Telegram bot) + PostgreSQL**.

## Структура

```
saas_platform/
├── app/
│   ├── main.py           # FastAPI + запуск polling бота
│   ├── config.py         # настройки из .env (pydantic-settings)
│   ├── db.py             # async SQLAlchemy + asyncpg
│   ├── bot/              # aiogram-бот (handlers, роутеры)
│   ├── site/             # FastAPI-роуты сайта/API
│   └── services/         # бизнес-логика
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

## Требования

- Python **3.9+**
- PostgreSQL 13+

## Установка

```bash
python3.9 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# отредактируй .env
```

## Переменные окружения

| Переменная     | Описание                                              |
|----------------|-------------------------------------------------------|
| `BOT_TOKEN`    | Токен Telegram-бота                                   |
| `ADMIN_IDS`    | ID админов через запятую: `123456789,987654321`       |
| `DATABASE_URL` | `postgresql+asyncpg://user:pass@host:5432/dbname`     |
| `APP_HOST`     | Хост FastAPI (по умолчанию `0.0.0.0`)                 |
| `APP_PORT`     | Порт FastAPI (по умолчанию `8000`)                    |

## Запуск

```bash
python -m app.main
```

Это поднимет:
- FastAPI на `http://APP_HOST:APP_PORT` (`/`, `/health`)
- Telegram-бот в режиме long polling

## Telegram

- `/start` от админа платформы (`ADMIN_IDS`) → приветствие
- `/start` от остальных → «Доступ ограничен»

## Что НЕ входит в MVP

Намеренно отсутствуют: учёт, продажи, работники — это чистый каркас.
