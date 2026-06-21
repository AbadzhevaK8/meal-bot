# 🍽️ Meal Bot — Telegram бот для фото-трекинга питания

Анализирует фото еды через **Groq Vision (Llama-4-Scout)** и сохраняет результаты в локальную **SQLite**-базу.

## Возможности

- 📸 Анализ фото еды (упаковка, блюдо, продукт на весах)
- 📊 Просмотр суммарной статистики за день (`/today`)
- ❌ Удаление последней записи (`/delete`)
- 📝 Учёт подписи к фото (вес, уточнения)
- 📄 Подробный отчёт БЖУ (`/report`)
- 📱 Telegram Mini App: календарь, редактирование записей и графики (`/app`)

## Быстрый старт

### 1. Получить credentials

1. Создай сервисный аккаунт в [Google Cloud Console](https://console.cloud.google.com/)
2. Получи API ключи:
   - [Groq API Key](https://console.groq.com/keys)
   - [DeepSeek API Key](https://platform.deepseek.com/api_keys) (опционально, для текстового анализа)
   - Создай Telegram-бота через [@BotFather](https://t.me/BotFather)

Google Sheets больше не является основным хранилищем. Старые Sheets credentials
нужны только для одноразовой миграции или как внешний архив.

### 2. Настройка

```bash
cp .env.example .env
# Заполни .env своими данными
```

### 3. Запуск через Docker

```bash
docker compose up -d --build
```

При этом внутренняя служба ботa поднимается на порту `8080`, поэтому
`https://medina.garum.tech/oauth2callback` должен быть проксирован на
`http://localhost:8080/oauth2callback` или проброшен через Docker.

Если на том же хосте будет работать `period_bot`, оставьте `meal_bot` на
`/` и `8080`, а `period_bot` проксируйте по другому пути, например
`/period/` на `http://127.0.0.1:8081/`.

### 4. Или локально

```bash
pip install -r requirements.txt
python bot.py
```

## Команды

| Команда | Описание |
|---------|----------|
| `/start` | Приветствие |
| Отправить фото | Анализ еды через Groq Vision |
| `/today` | Статистика за сегодня |
| `/report` | Подробный отчёт БЖУ за сегодня |
| `/app` | Открыть Mini App с календарём и графиками |
| `/delete` | Удалить последнюю запись |
| `/fitness_auth` | Ссылка для авторизации Google Fitness fallback |
| `/fitness_status` | Проверить статус Google Fitness fallback |
| `/help` | Справка по командам |

## Garmin expenditure sources

For completed daily reports, the bot should use exact saved expenditure values:

1. Manual Mini App override.
2. Garmin Connect cloud pull.
3. Garmin via Health Connect Android bridge.

By default `STRICT_EXPENDITURE_SOURCE=true`, so completed-day reports and daily
aggregation do not silently fall back to Google Fit. If none of the exact
sources has a value, the report says that exact expenditure is unavailable
instead of sending a misleading number.

Server-side Garmin Connect pull is configured with:

```env
GARMIN_CONNECT_EMAIL=your_garmin_email
GARMIN_CONNECT_PASSWORD=your_garmin_password
GARMIN_CONNECT_TOKENSTORE=garmin_tokens.json
STRICT_EXPENDITURE_SOURCE=true
```

At 03:00 the bot requests yesterday's daily summary from Garmin Connect cloud,
saves `totalKilocalories` into `fitness`, then recalculates `daily_calories`.

## Garmin / Health Connect ingest

Garmin Connect can write calories into Android Health Connect. Health Connect
runs on Android devices, so the server cannot read it directly. Use the
`../healthconnect_bridge` Android bridge to read `TotalCaloriesBurnedRecord`
locally and send daily totals to:

```http
POST https://medina.garum.tech/garmin/calories
Authorization: Bearer <HEALTHCONNECT_INGEST_TOKEN>
Content-Type: application/json

{
  "date": "2026-05-14",
  "total_kcal": 1829,
  "source": "Garmin Connect via Health Connect"
}
```

The older `/healthconnect/calories` endpoint still works for generic Health
Connect imports. When a Garmin/Health Connect row exists for a date, `/today`,
`/report`, Mini App balances, and daily aggregation prefer it over Google Fit
REST API data. Google Fit is used for completed-day aggregation only when
`STRICT_EXPENDITURE_SOURCE=false`.

## Telegram Mini App

Mini App сервится тем же `aiohttp`-сервером, что OAuth и Health Connect:

- URL интерфейса: `/ui`
- API: `/api/meals`
- В Telegram пользователь определяется по подписанному `Telegram.WebApp.initData`
- Для локального браузера можно использовать `WEB_UI_TOKEN` и ручной `user_id`

Чтобы кнопка `/app` открывала Mini App, укажите публичный HTTPS URL:

```env
MINIAPP_URL=https://medina.garum.tech/ui
WEB_UI_TOKEN=случайный_секрет_для_локального_доступа
```

Для полноценной публикации Mini App также настройте домен приложения в BotFather.

## Хранилище

По умолчанию данные пишутся в `meal_bot.sqlite3`.

```env
MEALBOT_STORAGE=sqlite
MEALBOT_SQLITE_PATH=meal_bot.sqlite3
```

Для миграции старых данных из Google Sheets:

```bash
python migrate_sheets_to_sqlite.py --overwrite
```

## Архитектура

- **AI Vision**: Groq Llama-4-Scout (через `groq_vision.py`)
- **Текстовый анализ**: DeepSeek (через `deepseek.py`)
- **База данных**: SQLite (через совместимый слой `sheets.py`)
- **Фреймворк**: aiogram 3.x
- **Планировщик**: APScheduler для ежедневных отчетов

## Стек

- Python 3.11+
- aiogram 3.x
- Groq API (Llama Vision)
- DeepSeek API (текст)
- sqlite3
- gspread + google-auth (только для миграции/архива Google Sheets)
- Docker

## Переменные окружения

Скопируй `.env.example` в `.env` и заполни:

```env
BOT_TOKEN=токен_телеграм_бота
GROQ_API_KEY=ключ_groq_api
DEEPSEEK_API_KEY=ключ_deepseek_api (опционально)
DEEPSEEK_API_URL=https://api.deepseek.com
GOOGLE_SHEETS_ID=id_таблицы
GOOGLE_CREDENTIALS_JSON=путь_к_json_ключу
MEALBOT_STORAGE=sqlite
MEALBOT_SQLITE_PATH=meal_bot.sqlite3
GOOGLE_OAUTH_CLIENT_ID=client_id_from_google
GOOGLE_OAUTH_CLIENT_SECRET=client_secret_from_google
GOOGLE_REDIRECT_URI=https://medina.garum.tech/oauth2callback
HEALTHCONNECT_INGEST_TOKEN=секрет_для_garmin_android_bridge
WEB_UI_TOKEN=секрет_для_локального_браузера
MINIAPP_URL=https://medina.garum.tech/ui
WEB_PORT=8080
TIMEZONE=Europe/Moscow
ACCESS_PASSWORD=пароль_доступа
REPORT_USER_IDS=id_пользователей_через_запятую
```

> Для работы Google OAuth callback URL должен быть доступен по HTTPS: `https://medina.garum.tech/oauth2callback`.

## Деплой

Если вы запускаете бот через Docker, то контейнер мапится на порт `8080`.
Для внешнего HTTPS-доступа нужен nginx-прокси на `medina.garum.tech`, который будет перенаправлять запросы к боту.

Пример nginx-конфига см. `nginx-proxy-example.conf`.

Смотри `docker-compose.yml` и `Dockerfile`. 

Credentials JSON сервисного аккаунта положи рядом и монтируй в контейнер read-only.

## Безопасность

⚠️ **Важно**: Никогда не коммить реальные ключи API и JSON-файлы сервисных аккаунтов!

Добавь в `.gitignore`:
```
.env
*.json
credentials.json
auth_users.json
bot.log
```

## Лицензия

MIT
