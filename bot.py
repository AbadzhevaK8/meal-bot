import asyncio
import json
import logging
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from aiohttp import web
from aiogram import Bot, Dispatcher, F, types, exceptions
from aiogram.filters import Command
from aiogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import load_config
from pytz import timezone
from deepseek import analyze_text_food
from google_fitness import (
    build_authorization_url,
    exchange_code_for_tokens,
    fetch_calories_since_midnight,
    fetch_daily_calories,
    fetch_daily_calories_for_date,
    get_tokens_status,
    has_saved_tokens,
    sync_fitness_rows,
)
from groq_vision import analyze_food as analyze_image_food
from health_connect import fetch_health_connect_calories_for_date, ingest_health_connect_calories
from report import build_daily_report
from sheets import (
    delete_last_meal,
    get_logs_for_date,
    get_saved_fitness_calories_for_date,
    get_today_logs,
    log_daily_calories,
    log_meal,
)

log_file = Path(__file__).parent / "bot.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

config = load_config()
bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

AUTH_FILE = Path(__file__).parent / "auth_users.json"
authenticated_users: set[int] = set()
password_prompt_users: set[int] = set()


def load_authenticated_users() -> None:
    """Загружает список авторизованных пользователей из файла."""
    if AUTH_FILE.exists():
        try:
            with open(AUTH_FILE, "r", encoding="utf-8") as f:
                user_ids = json.load(f)
                authenticated_users.update(user_ids)
                logger.info("Loaded %d authenticated users from %s", len(user_ids), AUTH_FILE)
        except Exception as e:
            logger.exception("Failed to load auth file: %s", e)
    else:
        logger.info("Auth file not found, starting with empty user list")


def save_authenticated_user(user_id: int) -> None:
    """Сохраняет авторизованного пользователя в файл."""
    try:
        user_ids = list(authenticated_users)
        with open(AUTH_FILE, "w", encoding="utf-8") as f:
            json.dump(user_ids, f)
        logger.info("Saved user %s to auth file", user_id)
    except Exception as e:
        logger.exception("Failed to save auth file: %s", e)


async def oauth2_callback(request: web.Request) -> web.Response:
    code = request.query.get("code")
    if not code:
        return web.Response(text="Missing code parameter", status=400)

    try:
        tokens = await asyncio.to_thread(exchange_code_for_tokens, code)
        logger.info("Google Fitness authorization completed")
        return web.Response(
            text=(
                "Авторизация Google Fitness завершена.<br>"
                "Токен сохранён. Можно закрыть это окно."
            ),
            content_type="text/html",
        )
    except Exception as e:
        logger.exception("OAuth callback failed: %s", e)
        return web.Response(
            text=f"Ошибка авторизации: {e}",
            status=500,
            content_type="text/html",
        )


async def health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def healthconnect_calories(request: web.Request) -> web.Response:
    expected_token = config.HEALTHCONNECT_INGEST_TOKEN
    if not expected_token:
        return web.json_response({"error": "Health Connect ingest is not configured"}, status=503)

    auth_header = request.headers.get("Authorization", "")
    provided_token = ""
    if auth_header.startswith("Bearer "):
        provided_token = auth_header.removeprefix("Bearer ").strip()
    if not provided_token:
        provided_token = request.query.get("token", "")
    if provided_token != expected_token:
        return web.json_response({"error": "Unauthorized"}, status=401)

    try:
        payload = await request.json()
        parsed = await asyncio.to_thread(ingest_health_connect_calories, payload, config.TIMEZONE)
    except Exception as e:
        logger.exception("Health Connect ingest failed: %s", e)
        return web.json_response({"error": str(e)}, status=400)

    logger.info(
        "Health Connect calories ingested: date=%s total=%s",
        parsed["date"],
        parsed["total_kcal"],
    )
    return web.json_response({
        "ok": True,
        "date": parsed["date"].isoformat(),
        "total_kcal": round(parsed["total_kcal"], 1),
        "note": parsed["note"],
    })


async def start_web_server() -> None:
    app = web.Application()
    app.add_routes([
        web.get("/oauth2callback", oauth2_callback),
        web.post("/healthconnect/calories", healthconnect_calories),
        web.get("/health", health),
    ])
    runner = web.AppRunner(app)
    await runner.setup()
    port = config.WEB_PORT
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Web server started on port %s", port)


def get_keyboard(is_auth: bool) -> types.ReplyKeyboardMarkup:
    if is_auth:
        keyboard = [
            [types.KeyboardButton(text="/today"), types.KeyboardButton(text="/report")],
            [types.KeyboardButton(text="/delete")],
        ]
    else:
        keyboard = [[types.KeyboardButton(text="/login")]]

    return types.ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def is_authenticated(user_id: int) -> bool:
    return config.ACCESS_PASSWORD is None or user_id in authenticated_users


async def require_auth(message: Message) -> bool:
    if is_authenticated(message.from_user.id):
        return True

    logger.info("Auth required for user_id=%s", message.from_user.id)
    await message.answer(
        "Доступ защищён паролем. Войдите командой:\n"
        "/login <пароль>\n"
        "или отправьте /login, а затем пароль.",
        reply_markup=get_keyboard(False),
    )
    return False


@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Привет! Отправь фото еды — упаковку, блюдо или продукт на весах.\n"
        "Можешь подписать вес или уточнение, например: \"300г\" или \"это домашний борщ\".",
        reply_markup=get_keyboard(is_authenticated(message.from_user.id)),
    )


@dp.message(Command("help"))
async def cmd_help(message: Message) -> None:
    if is_authenticated(message.from_user.id):
        text = (
            "Вот доступные команды:\n"
            "/today — краткий итог за сегодня\n"
            "/report — подробный отчёт БЖУ за сегодня\n"
            "/delete — удалить последнюю запись\n"
            "/fitness_auth — ссылка для авторизации Google Fitness\n"
            "/fitness_status — статус Google Fitness авторизации"
        )
    else:
        text = (
            "Вот доступные команды:\n"
            "/login <пароль> — войти\n"
            "После входа станут доступны /today, /report и /delete"
        )

    await message.answer(text, reply_markup=get_keyboard(is_authenticated(message.from_user.id)))


@dp.message(Command("login"))
async def cmd_login(message: Message) -> None:
    if config.ACCESS_PASSWORD is None:
        await message.answer("Пароль не задан, доступ открыт.")
        return

    text = message.text or ""
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        password_prompt_users.add(message.from_user.id)
        logger.info("Password requested for user_id=%s", message.from_user.id)
        await message.answer(
            "🔐 Введите пароль:",
            reply_markup=get_keyboard(False),
        )
        return

    password = parts[1].strip()
    if password == config.ACCESS_PASSWORD:
        authenticated_users.add(message.from_user.id)
        save_authenticated_user(message.from_user.id)
        logger.info("User %s logged in with inline password", message.from_user.id)
        await message.answer(
            "✅ Вход выполнен. Теперь доступны команды.",
            reply_markup=get_keyboard(True),
        )
    else:
        logger.warning("Invalid inline login attempt for user_id=%s", message.from_user.id)
        await message.answer("❌ Неверный пароль.")


@dp.message(Command("fitness_auth"))
async def cmd_fitness_auth(message: Message) -> None:
    if not await require_auth(message):
        return

    if not config.GOOGLE_OAUTH_CLIENT_ID or not config.GOOGLE_OAUTH_CLIENT_SECRET or not config.GOOGLE_REDIRECT_URI:
        await message.answer(
            "Ошибка настройки Google Fitness. Проверьте GOOGLE_OAUTH_CLIENT_ID, "
            "GOOGLE_OAUTH_CLIENT_SECRET и GOOGLE_REDIRECT_URI в config.py."
        )
        return

    auth_url = build_authorization_url(config.GOOGLE_REDIRECT_URI, config.GOOGLE_OAUTH_CLIENT_ID)
    await message.answer(
        "Перейдите по этой ссылке и завершите авторизацию Google Fitness:\n"
        f"{auth_url}",
        disable_web_page_preview=True,
    )


@dp.message(Command("fitness_status"))
async def cmd_fitness_status(message: Message) -> None:
    if not await require_auth(message):
        return

    status = get_tokens_status()
    if status is None:
        await message.answer(
            "Google Fitness не подключён. Выполните /fitness_auth, чтобы авторизовать доступ."
        )
    else:
        await message.answer(
            "Google Fitness подключён.\n"
            f"Статус: {status}"
        )


@dp.message(Command("today"))
async def cmd_today(message: Message) -> None:
    logger.info("Today requested by user_id=%s", message.from_user.id)
    if not await require_auth(message):
        return

    user_id = message.from_user.id
    records = get_today_logs(user_id, tz=config.TIMEZONE)

    today = datetime.now(timezone(config.TIMEZONE)).date()
    burned = 0.0
    burned_note = ""
    health_connect_calories = fetch_health_connect_calories_for_date(today)
    if health_connect_calories:
        burned, health_connect_note = health_connect_calories
        burned_note = f" ({health_connect_note})"
    else:
        try:
            burned = fetch_calories_since_midnight(config.TIMEZONE)
            burned_note = " (Google Fit API)"
        except Exception as e:
            saved_fitness_calories = get_saved_fitness_calories_for_date(today)
            if saved_fitness_calories:
                burned, saved_fitness_note = saved_fitness_calories
                burned_note = f" ({saved_fitness_note}; Google Fit временно недоступен: {e})"
            else:
                burned_note = f" (не удалось получить текущие данные из Google Fit: {e})"

    total_kcal = sum(float(r.get("kcal", 0) or 0) for r in records)
    total_protein = sum(float(r.get("protein_g", 0) or 0) for r in records)
    total_fat = sum(float(r.get("fat_g", 0) or 0) for r in records)
    total_carbs = sum(float(r.get("carbs_g", 0) or 0) for r in records)

    lines = [
        "📊 За сегодня:",
        f"🔥 Съедено: {int(total_kcal)} ккал",
        f"🔥 Сожжено на момент вызова: {int(burned)} ккал{burned_note}",
        f"⚖️ Разница: {int(total_kcal - burned)} ккал",
        "",
        "Приёмы пищи:",
    ]
    for r in records:
        name = r.get("name", "?")
        kcal = r.get("kcal", "?")
        if kcal != "?":
            try:
                kcal = int(float(kcal))
            except (ValueError, TypeError):
                pass
        lines.append(f"• {name} — {kcal} ккал")

    await message.answer("\n".join(lines))


@dp.message(Command("report"))
async def cmd_report(message: Message) -> None:
    logger.info("Report requested by user_id=%s", message.from_user.id)
    if not await require_auth(message):
        return

    user_id = message.from_user.id
    yesterday = datetime.now(timezone(config.TIMEZONE)).date() - timedelta(days=1)
    text = build_daily_report(user_id, tz=config.TIMEZONE, target_date=yesterday)
    if text:
        await message.answer(text, parse_mode="HTML")
    else:
        await message.answer(f"За {yesterday.day} {MONTHS_RU.get(yesterday.month, '')} записей нет.")


@dp.message(F.text & F.text.func(lambda t: t is not None and not t.startswith("/")))
async def handle_text_message(message: Message) -> None:
    logger.info("Text message received by user_id=%s text=%s", message.from_user.id, message.text)
    
    # Проверка ввода пароля
    if message.from_user.id in password_prompt_users:
        password_prompt_users.discard(message.from_user.id)
        if message.text.strip() == config.ACCESS_PASSWORD:
            authenticated_users.add(message.from_user.id)
            save_authenticated_user(message.from_user.id)
            logger.info("User %s logged in with prompt password", message.from_user.id)
            await message.answer(
                "✅ Вход выполнен. Теперь доступны команды.",
                reply_markup=get_keyboard(True),
            )
        else:
            logger.warning("Invalid prompted login attempt for user_id=%s", message.from_user.id)
            await message.answer(
                "❌ Неверный пароль. Используйте /login, чтобы попробовать снова.",
                reply_markup=get_keyboard(False),
            )
        return

    if not await require_auth(message):
        return

    query = (message.text or "").strip()
    if not query:
        return

    if config.DEEPSEEK_API_KEY:
        await message.answer("🔍 Анализирую текст через DeepSeek...")
        result = await analyze_text_food(query)
        if result is None:
            logger.info("DeepSeek не справился, использую локальный парсер для: %s", query)
            await message.answer(
                "ℹ️ DeepSeek не распознал, использую локальный парсер."
            )
            result = parse_text_meal(query)
    else:
        logger.info(
            "DeepSeek API key is not configured; using local text parser fallback for user_id=%s",
            message.from_user.id,
        )
        await message.answer(
            "⚠️ DeepSeek не настроен. Добавляю запись по простому парсингу текста."
        )
        result = parse_text_meal(query)

    user_id = message.from_user.id
    try:
        log_meal(result, user_id, tz=config.TIMEZONE)
    except Exception as e:
        logger.exception("Ошибка записи текстовой еды user_id=%s: %s", user_id, e)
        await message.answer("❌ Ошибка! Не удалось сохранить запись. Попробуй позже.")
        return

    await message.answer("✅ Запись успешно добавлена.")
    await message.answer(format_meal_response(result))


def format_meal_response(result: dict) -> str:
    name = result.get("name", "Еда")
    weight = result.get("weight_g", 0)
    kcal = result.get("kcal", 0)
    protein = result.get("protein_g", 0)
    fat = result.get("fat_g", 0)
    carbs = result.get("carbs_g", 0)
    confidence = result.get("confidence", "low")
    note = result.get("note", "")

    lines = [
        f"✅ {name}",
        f"⚖️ Вес: {weight}г",
        f"🔥 Калории: {kcal} ккал",
        f"🥩 Белки: {protein}г",
        f"🧈 Жиры: {fat}г",
        f"🍞 Углеводы: {carbs}г",
    ]
    if note:
        lines.append(f"📝 {note}")
    if confidence == "low":
        lines.append("\n⚠️ Уверенность низкая, проверь данные")

    return "\n".join(lines)


def parse_text_meal(text: str) -> dict:
    weight = 0
    note = ""
    pattern = re.compile(r"(?P<weight>\d{1,4})\s*(г|гр|g|kg)\b", re.I)
    match = pattern.search(text)
    if match:
        try:
            weight = int(match.group("weight"))
        except ValueError:
            weight = 0
        text = (text[: match.start()] + text[match.end() :]).strip()

    name = text or "Еда"
    if weight:
        note = "Текстовая запись с указанным весом"
    else:
        note = "Текстовая запись"

    return {
        "name": name,
        "weight_g": weight,
        "kcal": 0,
        "protein_g": 0,
        "fat_g": 0,
        "carbs_g": 0,
        "confidence": "low",
        "note": note,
    }


@dp.message(Command("delete"))
async def cmd_delete(message: Message) -> None:
    if not await require_auth(message):
        return

    user_id = message.from_user.id
    try:
        success = delete_last_meal(user_id, tz=config.TIMEZONE)
    except Exception as e:
        logger.exception("Ошибка при удалении записи user_id=%s: %s", user_id, e)
        await message.answer("⚠️ Не удалось удалить запись. Попробуй позже.")
        return

    if success:
        await message.answer("✅ Последняя запись успешно удалена из таблицы.")
    else:
        await message.answer("ℹ️ В таблице нет записей для удаления.")


@dp.message(F.photo)
async def handle_photo(message: Message) -> None:
    if not await require_auth(message):
        return

    user_id = message.from_user.id
    caption = message.caption

    # Берём наибольший размер фото
    photo = message.photo[-1]
    photo_bytes = await bot.download(photo)
    image_bytes = photo_bytes.read()

    await process_meal_image(message, image_bytes, caption, user_id)


@dp.message(F.document)
async def handle_document(message: Message) -> None:
    if not await require_auth(message):
        return

    user_id = message.from_user.id
    caption = message.caption

    doc = message.document
    if doc.mime_type and not doc.mime_type.startswith("image/"):
        await message.answer("Пожалуйста, отправь изображение.")
        return

    file_bytes = await bot.download(doc)
    image_bytes = file_bytes.read()

    await process_meal_image(message, image_bytes, caption, user_id)


async def process_meal_image(
    message: Message,
    image_bytes: bytes,
    caption: str | None,
    user_id: int,
) -> None:
    """Обрабатывает изображение еды: анализ Gemini + запись в Sheets."""
    await message.answer("🔍 Анализирую фото...")

    result = await analyze_image_food(image_bytes, caption)

    if result is None:
        await message.answer("Не удалось распознать, попробуй ещё раз или добавь подпись.")
        return

    # Запись в Google Sheets
    try:
        log_meal(result, user_id, tz=config.TIMEZONE)
    except Exception as e:
        logger.exception("Ошибка записи в Sheets: %s", e)
        await message.answer("❌ Данные распознаны, но не удалось сохранить в таблицу.")
        return

    await message.answer("✅ Запись успешно добавлена.")

    # Пересчитываем КБЖУ с веса на 100г на фактический вес порции,
    # если значения подозрительно высокие (явно на 100г, а не на порцию)
    name = result.get("name", "?")
    weight = result.get("weight_g")
    kcal = result.get("kcal")
    protein = result.get("protein_g")
    fat = result.get("fat_g")
    carbs = result.get("carbs_g")

    if weight and weight > 0 and kcal and protein and fat and carbs:
        # Эвристика: если калории > 20 * вес, значит КБЖУ на 100г, а не на порцию
        # (например, 351 ккал на 35г — явно на 100г)
        if kcal > weight * 5:
            factor = weight / 100.0
            kcal = round(kcal * factor)
            protein = round(protein * factor, 1)
            fat = round(fat * factor, 1)
            carbs = round(carbs * factor, 1)
            # Обновляем результат для сохранения в Sheets
            result["kcal"] = kcal
            result["protein_g"] = protein
            result["fat_g"] = fat
            result["carbs_g"] = carbs

    response = (
        f"✅ {name}\n\n"
        f"⚖️ Вес: {weight}г\n"
        f"🔥 Калории: {kcal} ккал\n"
        f"🥩 Белки: {protein}г\n"
        f"🧈 Жиры: {fat}г\n"
        f"🍞 Углеводы: {carbs}г"
    )

    if result.get("confidence") == "low":
        response += "\n\n⚠️ Уверенность низкая, проверь данные"

    await message.answer(response)


async def send_daily_reports() -> None:
    """Отправляет суточный отчёт за вчерашний день (запускается в 1:00 ночи)."""
    yesterday = datetime.now(timezone(config.TIMEZONE)).date() - timedelta(days=1)
    for user_id in config.REPORT_USER_IDS:
        try:
            text = build_daily_report(user_id, tz=config.TIMEZONE, target_date=yesterday)
            if text:
                await bot.send_message(user_id, text, parse_mode="HTML")
            else:
                await bot.send_message(user_id, f"За {yesterday.day} {MONTHS_RU.get(yesterday.month, '')} записей нет 🤷")
        except Exception as e:
            logger.exception("Ошибка отправки отчёта user_id=%s: %s", user_id, e)


async def send_daily_fitness_ingestion() -> None:
    try:
        end_date = datetime.now(timezone(config.TIMEZONE)).date() - timedelta(days=1)
        start_date = end_date - timedelta(days=6)

        logger.info("Syncing fitness and daily calories from %s to %s", start_date, end_date)

        await asyncio.to_thread(sync_fitness_rows, start_date, end_date, config.TIMEZONE)

        import aggregate_daily_calories

        await asyncio.to_thread(
            aggregate_daily_calories.main,
            start_date,
            end_date,
            False,
            None,
        )

        logger.info("Daily Google Fitness calories ingested and daily_calories synced: %s..%s", start_date, end_date)
    except Exception as e:
        logger.exception("Failed to sync daily fitness and calories: %s", e)


async def main() -> None:
    load_authenticated_users()

    await bot.set_my_commands([
        types.BotCommand(command="start", description="Начало работы"),
        types.BotCommand(command="login", description="Войти"),
        types.BotCommand(command="today", description="Итог за сегодня"),
        types.BotCommand(command="report", description="Отчёт за сегодня"),
        types.BotCommand(command="delete", description="Удалить последнюю запись"),
        types.BotCommand(command="fitness_auth", description="Авторизовать Google Fitness"),
        types.BotCommand(command="fitness_status", description="Статус Google Fitness"),
        types.BotCommand(command="help", description="Помощь"),
    ])

    await start_web_server()

    scheduler = AsyncIOScheduler(timezone=config.TIMEZONE)
    scheduler.add_job(send_daily_reports, "cron", hour=1, minute=0)
    scheduler.add_job(send_daily_fitness_ingestion, "cron", hour="0,6,12,18", minute=0)
    scheduler.start()

    logger.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
