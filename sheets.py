import logging
import os
from datetime import date, datetime
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials
from pytz import timezone

logger = logging.getLogger(__name__)

SHEET_NAME = "log"
HEADERS = [
    "timestamp", "user_id", "name", "weight_g",
    "kcal", "protein_g", "fat_g", "carbs_g",
    "confidence", "note",
]


def _is_sheets_configured() -> bool:
    """Проверяет, настроены ли Google Sheets."""
    from config import load_config

    config = load_config()
    return bool(config.GOOGLE_SHEETS_ID and config.GOOGLE_CREDENTIALS_JSON)


def _resolve_credentials_path(credentials_path: str) -> Path:
    """Превращает относительный путь в абсолютный относительно папки проекта."""
    path = Path(credentials_path)
    if not path.is_absolute():
        path = (Path(__file__).parent / path).resolve()
    return path


def _get_sheet(credentials_path: str, sheet_id: str):
    """Подключается к Google Sheets и возвращает лист `log`."""
    resolved_path = _resolve_credentials_path(credentials_path)
    creds = Credentials.from_service_account_file(
        resolved_path,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(sheet_id)

    try:
        worksheet = spreadsheet.worksheet(SHEET_NAME)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=SHEET_NAME, rows=1000, cols=len(HEADERS))
        worksheet.append_row(HEADERS)

    return worksheet


def _get_fitness_sheet(credentials_path: str, sheet_id: str):
    """Подключается к Google Sheets и возвращает лист `fitness`."""
    resolved_path = _resolve_credentials_path(credentials_path)
    creds = Credentials.from_service_account_file(
        resolved_path,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(sheet_id)

    try:
        worksheet = spreadsheet.worksheet("fitness")
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title="fitness", rows=1000, cols=4)
        worksheet.append_row(["timestamp", "description", "kcal", "note"])

    return worksheet


def _find_fitness_row_index(worksheet, description_value: str) -> int | None:
    values = worksheet.get_all_values()
    if not values or len(values) < 2:
        return None
    header = values[0]
    if "description" not in header:
        return None
    desc_idx = header.index("description")
    for idx, row in enumerate(values[1:], start=2):
        if len(row) > desc_idx and row[desc_idx] == description_value:
            return idx
    return None


def upsert_fitness_data(data: dict, tz: str = "Europe/Moscow") -> None:
    """Записывает или обновляет строку в листе fitness по описанию."""
    if not _is_sheets_configured():
        logger.warning("Google Sheets не настроен, запись фитнес-данных пропущена")
        return

    from config import load_config

    config = load_config()
    worksheet = _get_fitness_sheet(config.GOOGLE_CREDENTIALS_JSON, config.GOOGLE_SHEETS_ID)
    now = datetime.now(timezone(tz)).strftime("%Y-%m-%d %H:%M")

    description = data.get("description", "")
    row = [
        now,
        description,
        round(float(data.get("kcal", 0) or 0), 1),
        data.get("note", ""),
    ]

    row_index = _find_fitness_row_index(worksheet, description)
    if row_index:
        worksheet.update(f"A{row_index}:D{row_index}", [row])
        logger.info("Фитнес-данные обновлены: %s", data)
    else:
        worksheet.append_row(row)
        logger.info("Фитнес-данные добавлены: %s", data)


def _get_daily_calories_sheet(credentials_path: str, sheet_id: str):
    """Подключается к Google Sheets и возвращает лист `daily_calories`."""
    resolved_path = _resolve_credentials_path(credentials_path)
    creds = Credentials.from_service_account_file(
        resolved_path,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(sheet_id)

    try:
        worksheet = spreadsheet.worksheet("daily_calories")
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title="daily_calories", rows=1000, cols=7)
        worksheet.append_row([
            "timestamp",
            "user_id",
            "date",
            "intake_kcal",
            "expenditure_kcal",
            "difference_kcal",
            "note",
        ])

    return worksheet


def log_fitness_data(data: dict, tz: str = "Europe/Moscow") -> None:
    """Записывает данные фитнеса (например, калории) в Google Sheets."""
    if not _is_sheets_configured():
        logger.warning("Google Sheets не настроен, запись фитнес-данных пропущена")
        return

    from config import load_config

    config = load_config()
    worksheet = _get_fitness_sheet(config.GOOGLE_CREDENTIALS_JSON, config.GOOGLE_SHEETS_ID)
    now = datetime.now(timezone(tz)).strftime("%Y-%m-%d %H:%M")

    row = [
        now,
        data.get("description", ""),
        data.get("kcal", 0),
        data.get("note", ""),
    ]

    worksheet.append_row(row)
    logger.info("Фитнес-данные добавлены: %s", data)


def log_daily_calories(
    user_id: int,
    intake_kcal: float,
    expenditure_kcal: float,
    difference_kcal: float,
    date_value: str,
    tz: str = "Europe/Moscow",
    note: str = "",
) -> None:
    """Записывает суточный баланс калорий в Google Sheets."""
    if not _is_sheets_configured():
        logger.warning("Google Sheets не настроен, запись суточных калорий пропущена")
        return

    from config import load_config

    config = load_config()
    worksheet = _get_daily_calories_sheet(config.GOOGLE_CREDENTIALS_JSON, config.GOOGLE_SHEETS_ID)
    now = datetime.now(timezone(tz)).strftime("%Y-%m-%d %H:%M")

    row = [
        now,
        str(user_id),
        date_value,
        round(float(intake_kcal or 0), 1),
        round(float(expenditure_kcal or 0), 1),
        round(float(difference_kcal or 0), 1),
        note,
    ]

    worksheet.append_row(row)
    logger.info(
        "Суточная сводка добавлена: user_id=%s, date=%s, intake=%s, expenditure=%s, diff=%s",
        user_id,
        date_value,
        intake_kcal,
        expenditure_kcal,
        difference_kcal,
    )


def log_meal(data: dict, user_id: int, tz: str = "Europe/Moscow") -> None:
    """
    Записывает результат анализа еды в Google Sheets.

    Args:
        data: Словарь с данными о еде (name, weight_g, kcal, protein_g, fat_g, carbs_g, confidence, note).
        user_id: Telegram user ID.
        tz: Часовой пояс для временной метки.
    """
    if not _is_sheets_configured():
        logger.warning("Google Sheets не настроен, запись пропущена")
        return

    from config import load_config

    config = load_config()

    worksheet = _get_sheet(config.GOOGLE_CREDENTIALS_JSON, config.GOOGLE_SHEETS_ID)

    now = datetime.now(timezone(tz)).strftime("%Y-%m-%d %H:%M")

    row = [
        now,
        str(user_id),
        data.get("name", ""),
        data.get("weight_g", 0),
        data.get("kcal", 0),
        data.get("protein_g", 0),
        data.get("fat_g", 0),
        data.get("carbs_g", 0),
        data.get("confidence", ""),
        data.get("note", ""),
    ]

    worksheet.append_row(row)
    logger.info("Запись добавлена для user_id=%s: %s", user_id, data.get("name"))


def get_logs_for_date(user_id: int, target_date: date, tz: str = "Europe/Moscow") -> list[dict]:
    """Возвращает все записи пользователя за указанную дату."""
    if not _is_sheets_configured():
        return []

    from config import load_config

    config = load_config()
    worksheet = _get_sheet(config.GOOGLE_CREDENTIALS_JSON, config.GOOGLE_SHEETS_ID)

    if isinstance(target_date, date):
        date_prefix = target_date.strftime("%Y-%m-%d")
    else:
        date_prefix = str(target_date)

    all_rows = worksheet.get_all_values()
    if not all_rows:
        return []

    header = all_rows[0]
    records = []

    for row in all_rows[1:]:
        if len(row) < len(header):
            continue
        record = dict(zip(header, row))
        if record.get("user_id") == str(user_id) and record.get("timestamp", "").startswith(date_prefix):
            records.append(record)

    return records


def get_today_logs(user_id: int, tz: str = "Europe/Moscow") -> list[dict]:
    """
    Возвращает все записи пользователя за сегодня.

    Args:
        user_id: Telegram user ID.
        tz: Часовой пояс.

    Returns:
        Список словарей с данными записей.
    """
    return get_logs_for_date(user_id, datetime.now(timezone(tz)).date(), tz=tz)


def delete_last_meal(user_id: int, tz: str = "Europe/Moscow") -> bool:
    """
    Удаляет последнюю запись пользователя из Sheets.

    Args:
        user_id: Telegram user ID.
        tz: Часовой пояс.

    Returns:
        True если запись удалена, False если записей не найдено.
    """
    if not _is_sheets_configured():
        return False

    from config import load_config

    config = load_config()
    worksheet = _get_sheet(config.GOOGLE_CREDENTIALS_JSON, config.GOOGLE_SHEETS_ID)

    user_cells = worksheet.findall(str(user_id), in_column=2)

    if not user_cells:
        return False

    last_cell = user_cells[-1]
    last_row_idx = last_cell.row

    worksheet.delete_rows(last_row_idx)
    logger.info("Удалена строка %d для user_id=%s", last_row_idx, user_id)
    return True
