# 🚀 Инструкция по деплою Meal Bot

## Краткое руководство

### 1. Подготовка

1. Создайте сервисный аккаунт в Google Cloud Console
2. Включите Google Sheets API
3. Скачайте JSON-ключ и сохраните как `credentials.json`
4. Создайте Google Sheets таблицу и скопируйте её ID
5. Получите API ключи:
   - [Groq](https://console.groq.com/keys)
   - [DeepSeek](https://platform.deepseek.com/api_keys) (опционально)
6. Создайте Telegram-бота через [@BotFather](https://t.me/BotFather)

### 2. Настройка окружения

```bash
cd meal_bot
cp .env.example .env
```

Отредактируйте `.env`:

```env
BOT_TOKEN=ваш_телеграм_токен
GROQ_API_KEY=ваш_groq_ключ
DEEPSEEK_API_KEY=ваш_deepseek_ключ
DEEPSEEK_API_URL=https://api.deepseek.com
GOOGLE_SHEETS_ID=id_вашей_таблицы
GOOGLE_CREDENTIALS_JSON=credentials.json
TIMEZONE=Europe/Moscow
ACCESS_PASSWORD=ваш_пароль
REPORT_USER_IDS=ваш_telegram_id
```

### 3. Деплой через Docker (рекомендуется)

```bash
docker compose up -d --build
```

### 4. Локальный запуск

```bash
pip install -r requirements.txt
python bot.py
```

## Структура проекта

```
meal_bot/
├── bot.py              # Основной код бота
├── groq_vision.py      # Анализ изображений (Groq)
├── deepseek.py         # Текстовый анализ (DeepSeek)
├── sheets.py           # Google Sheets интеграция
├── report.py           # Генерация отчетов
├── config.py           # Конфигурация
├── requirements.txt    # Зависимости
├── Dockerfile          # Контейнеризация
├── docker-compose.yml  # Orchestration
├── .env                # Переменные окружения
├── .gitignore          # Игнорируемые файлы
└── README.md           # Основная документация
```

## Переменные окружения

| Переменная | Описание | Обязательна |
|------------|----------|-------------|
| `BOT_TOKEN` | Telegram токен | ✅ |
| `GROQ_API_KEY` | Groq API ключ | ✅ |
| `DEEPSEEK_API_KEY` | DeepSeek API ключ | ❌ |
| `GOOGLE_SHEETS_ID` | ID Google Sheets | ✅ |
| `GOOGLE_CREDENTIALS_JSON` | Путь к JSON ключу | ✅ |
| `TIMEZONE` | Часовой пояс | ❌ (по умолчанию: Europe/Moscow) |
| `ACCESS_PASSWORD` | Пароль доступа | ❌ |
| `REPORT_USER_IDS` | ID пользователей для отчетов | ❌ |

## Безопасность

⚠️ **Никогда не коммитьте**:
- `.env` файл
- `credentials.json`
- Любые JSON-файлы сервисных аккаунтов
- Реальные API ключи

Файл `.gitignore` уже настроен для защиты этих файлов.

## Мониторинг

Логи пишутся в `bot.log` и выводятся в stdout.

Для просмотра логов:
```bash
docker compose logs -f
```

## Обновление

```bash
docker compose pull
docker compose up -d --build
```

## Решение проблем

### Ошибка "Invalid API key"
- Проверьте `.env` файл
- Убедитесь, что ключи скопированы полностью

### Ошибка "Worksheet not found"
- Убедитесь, что Google Sheets таблица существует
- Проверьте `GOOGLE_SHEETS_ID`

### Бот не отвечает
- Проверьте статус Telegram бота
- Убедитесь, что порт не занят
- Проверьте логи: `docker compose logs`

## Автоматическое резервное копирование

Для резервного копирования данных из Google Sheets:

1. Используйте Google Takeout
2. Настройте регулярный экспорт в CSV
3. Храните резервные копии отдельно

## Масштабирование

Для горизонтального масштабирования:
1. Используйте внешний Redis для сессий
2. Настройте балансировщик нагрузки
3. Используйте PostgreSQL вместо Google Sheets (опционально)

## Поддержка

При возникновении проблем:
1. Проверьте логи
2. Убедитесь, что все переменные окружения установлены
3. Проверьте доступность API сервисов
4. Создайте issue в репозитории