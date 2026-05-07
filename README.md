# 🍽️ Meal Bot — Telegram бот для фото-трекинга питания

Анализирует фото еды через **Groq Vision (Llama-4-Scout)** и сохраняет результаты в **Google Sheets**.

## Возможности

- 📸 Анализ фото еды (упаковка, блюдо, продукт на весах)
- 📊 Просмотр суммарной статистики за день (`/today`)
- ❌ Удаление последней записи (`/delete`)
- 📝 Учёт подписи к фото (вес, уточнения)
- 📄 Подробный отчёт БЖУ (`/report`)

## Быстрый старт

### 1. Получить credentials

1. Создай сервисный аккаунт в [Google Cloud Console](https://console.cloud.google.com/)
2. Включи **Google Sheets API**
3. Скачай JSON-ключ и сохрани как `credentials.json` в корне проекта
4. Создай Google Sheets таблицу и скопируй её ID из URL
5. Получи API ключи:
   - [Groq API Key](https://console.groq.com/keys)
   - [DeepSeek API Key](https://platform.deepseek.com/api_keys) (опционально, для текстового анализа)
   - Создай Telegram-бота через [@BotFather](https://t.me/BotFather)

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
| `/delete` | Удалить последнюю запись |
| `/fitness_auth` | Ссылка для авторизации Google Fitness |
| `/fitness_status` | Проверить статус Google Fitness авторизации |
| `/help` | Справка по командам |

## Структура таблицы

| timestamp | user_id | name | weight_g | kcal | protein_g | fat_g | carbs_g | confidence | note |
|-----------|---------|------|----------|------|-----------|-------|---------|------------|------|

## Архитектура

- **AI Vision**: Groq Llama-4-Scout (через `groq_vision.py`)
- **Текстовый анализ**: DeepSeek (через `deepseek.py`)
- **База данных**: Google Sheets (через `sheets.py`)
- **Фреймворк**: aiogram 3.x
- **Планировщик**: APScheduler для ежедневных отчетов

## Стек

- Python 3.11+
- aiogram 3.x
- Groq API (Llama Vision)
- DeepSeek API (текст)
- gspread + google-auth
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
GOOGLE_OAUTH_CLIENT_ID=client_id_from_google
GOOGLE_OAUTH_CLIENT_SECRET=client_secret_from_google
GOOGLE_REDIRECT_URI=https://medina.garum.tech/oauth2callback
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