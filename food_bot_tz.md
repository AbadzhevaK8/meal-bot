# ТЗ: Телеграм-бот для фото-трекинга питания

---

## Стек

- Python 3.11+
- aiogram 3.x
- **Groq Vision (Llama-4-Scout)** — `groq_vision.py` (ранее ошибочно указано Gemini)
- gspread + google-auth — запись в Google Sheets
- Amvera Cloud — деплой

---

## Структура проекта

```
food_bot/
├── bot.py              # точка входа, регистрация хэндлеров
├── groq_vision.py      # работа с Groq Vision API (ранее gemini.py)
├── deepseek.py         # текстовый анализ через DeepSeek
├── sheets.py           # запись в Google Sheets
├── config.py           # переменные окружения
├── report.py           # генерация отчетов
├── requirements.txt
└── amvera.yml
```

---

## Шаг 1 — Конфиг

`config.py` читает из env:

```
BOT_TOKEN
GROQ_API_KEY
DEEPSEEK_API_KEY (опционально)
DEEPSEEK_API_URL
GOOGLE_SHEETS_ID
GOOGLE_CREDENTIALS_JSON   # путь к файлу сервисного аккаунта
```

---

## Шаг 2 — Модуль Groq Vision

Файл `groq_vision.py`:

**Функция:** `async def analyze_food(image_bytes: bytes, caption: str | None) -> dict`

**Промпт:**

```
Ты нутрициолог-аналитик. Тебе присылают фото еды.
Возможные типы фото:
- упаковка с этикеткой → используй данные с этикетки точно
- блюдо на тарелке → оцени состав и типичный вес порции
- продукт на весах → вес может быть указан в подписи

Если в подписи есть вес или уточнение — используй его.

ВАЖНО: Все значения КБЖУ (kcal, protein_g, fat_g, carbs_g) должны быть
рассчитаны НА УКАЗАННЫЙ ВЕС ПОРЦИИ (weight_g), а не на 100г.
Например, если вес порции 35г, а на 100г продукта 351 ккал,
то kcal должно быть 123 (351 * 35 / 100).

Верни ТОЛЬКО валидный JSON без markdown:
{
  "name": "название блюда/продукта",
  "weight_g": 250,
  "kcal": 380,
  "protein_g": 35,
  "fat_g": 8,
  "carbs_g": 42,
  "confidence": "high|medium|low",
  "note": "опциональный комментарий если что-то неочевидно"
}
```

**Обработка ошибок:**
- Если Groq вернул не JSON → повторить запрос 1 раз с припиской `"Верни только JSON"`
- Если снова ошибка → вернуть `None`, бот сообщит пользователю

---

## Шаг 3 — Google Sheets модуль

Файл `sheets.py`:

**Функция:** `def log_meal(data: dict, user_id: int)`

**Структура таблицы (строка):**

| timestamp | user_id | name | weight_g | kcal | protein_g | fat_g | carbs_g | confidence | note |
|-----------|---------|------|----------|------|-----------|-------|---------|------------|------|

- Лист называется `log`
- Дата в формате `YYYY-MM-DD HH:MM` (московское время)
- Если лист не существует — создать и добавить заголовки

---

## Шаг 4 — Хэндлеры бота

Файл `bot.py`:

### `/start`

```
Привет! Отправь фото еды — упаковку, блюдо или продукт на весах.
Можешь подписать вес или уточнение, например: "300г" или "это домашний борщ".
```

### Хэндлер фото (`photo` или `document` с изображением)

1. Скачать фото (брать наибольший размер из `message.photo[-1]`)
2. Передать в `groq_vision.analyze_food(image_bytes, caption)`
3. Если `None` → ответить: `"Не удалось распознать, попробуй ещё раз или добавь подпись"`
4. Если `confidence == "low"` → добавить к ответу: `"⚠️ Уверенность низкая, проверь данные"`
5. Записать в Sheets через `sheets.log_meal()`
6. Ответить пользователю:

```
✅ {name}

⚖️ Вес: {weight_g}г
🔥 Калории: {kcal} ккал
🥩 Белки: {protein_g}г
🧈 Жиры: {fat_g}г
🍞 Углеводы: {carbs_g}г
```

### `/today`

Достать из Sheets все записи за сегодня для этого `user_id`, просуммировать КБЖУ, вывести:

```
📊 За сегодня:
🔥 {total_kcal} ккал
🥩 Б: {total_protein}г  🧈 Ж: {total_fat}г  🍞 У: {total_carbs}г

Приёмы пищи:
• Гречка с курицей — 380 ккал
• Яблоко — 80 ккал
...
```

### `/report`

Подробный отчёт БЖУ в процентах от калорийности с визуальными индикаторами.

### `/delete`

Удалить последнюю запись пользователя из Sheets (на случай ошибки).

---

## Шаг 5 — requirements.txt

```
aiogram==3.18.0
apscheduler==3.10.4
groq==0.25.0
gspread==6.2.0
google-auth==2.38.0
pytz==2025.2
python-dotenv==1.2.2
Pillow==12.2.0
openai>=1.0.0
```

---

## Шаг 6 — Деплой на VPS (Docker)

`Dockerfile`:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "bot.py"]
```

`docker-compose.yml`:

```yaml
services:
  food_bot:
    build: .
    restart: unless-stopped
    env_file: .env
    volumes:
      - ./n8n-work-mails-243114c65263.json:/app/n8n-work-mails-243114c65263.json:ro
      - ./auth_users.json:/app/auth_users.json
```

`.env`:

```
BOT_TOKEN=...
GROQ_API_KEY=...
DEEPSEEK_API_KEY=...
GOOGLE_SHEETS_ID=...
GOOGLE_CREDENTIALS_JSON=/app/n8n-work-mails-243114c65263.json
```

Запуск:

```bash
docker compose up -d --build
```

Credentials JSON сервисного аккаунта — монтируется в контейнер read-only.

---

## Ограничения и допущения

- Бот рассчитан на одного пользователя или небольшую группу (personal use)
- Groq Free tier: 1500 запросов/день — достаточно для тестов
- Вес блюда без подписи — оценка модели, точность medium/low
- Часовой пояс фиксируется в конфиге (`Europe/Moscow` по умолчанию)

## Важные изменения

- **Исправлено**: замена Gemini на Groq Vision (файл `gemini.py` → `groq_vision.py`)
- **Добавлено**: подробная документация по API и архитектуре
- **Улучшено**: примеры конфигурации и деплоя