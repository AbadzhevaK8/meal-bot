import logging
import os
import re
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

FITNESS_DATE_PATTERN = re.compile(r"from (\d{4}-\d{2}-\d{2}) to \d{4}-\d{2}-\d{2}")


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


def _parse_fitness_date_from_description(description: str) -> str | None:
    match = FITNESS_DATE_PATTERN.search(description or "")
    if match:
        return match.group(1)
    return None


def _parse_date_from_timestamp(timestamp: str) -> str | None:
    try:
        return datetime.fromisoformat(timestamp).date().isoformat()
    except ValueError:
        try:
            return datetime.strptime(timestamp, "%Y-%m-%d %H:%M").date().isoformat()
        except ValueError:
            return None


def _parse_kcal(value: str | float | int | None) -> float | None:
    try:
        return float(str(value or 0).replace(",", "."))
    except ValueError:
        return None


def get_health_connect_calories_for_date(date_value: date) -> tuple[float, str] | None:
    """Returns the latest Health Connect total calories row for a date, if present."""
    if not _is_sheets_configured():
        return None

    from config import load_config

    config = load_config()
    worksheet = _get_fitness_sheet(config.GOOGLE_CREDENTIALS_JSON, config.GOOGLE_SHEETS_ID)
    values = worksheet.get_all_values()
    if len(values) < 2:
        return None

    header = values[0]
    required = {"timestamp", "description", "kcal", "note"}
    if not required.issubset(set(header)):
        return None

    timestamp_idx = header.index("timestamp")
    description_idx = header.index("description")
    kcal_idx = header.index("kcal")
    note_idx = header.index("note")
    description = f"Health Connect total calories for {date_value.isoformat()}"

    latest: tuple[str, float, str] | None = None
    for row in values[1:]:
        if len(row) <= max(timestamp_idx, description_idx, kcal_idx, note_idx):
            continue
        if row[description_idx] != description:
            continue
        try:
            kcal = float(str(row[kcal_idx]).replace(",", ".") or 0)
        except ValueError:
            continue
        latest = (row[timestamp_idx], kcal, row[note_idx])

    if latest is None:
        return None
    timestamp, kcal, note = latest
    source_note = note or f"Health Connect данные на {timestamp}"
    return kcal, source_note


def get_saved_fitness_calories_for_date(date_value: date) -> tuple[float, str] | None:
    """Returns the latest saved Google Fit calories row for a date, if present."""
    if not _is_sheets_configured():
        return None

    from config import load_config

    config = load_config()
    worksheet = _get_fitness_sheet(config.GOOGLE_CREDENTIALS_JSON, config.GOOGLE_SHEETS_ID)
    values = worksheet.get_all_values()
    if len(values) < 2:
        return None

    header = values[0]
    required = {"timestamp", "description", "kcal"}
    if not required.issubset(set(header)):
        return None

    timestamp_idx = header.index("timestamp")
    description_idx = header.index("description")
    kcal_idx = header.index("kcal")
    note_idx = header.index("note") if "note" in header else None
    target = date_value.isoformat()
    by_description: dict[str, tuple[str, float, str]] = {}
    by_timestamp: dict[str, tuple[str, float, str]] = {}

    for row in values[1:]:
        if len(row) <= max(timestamp_idx, description_idx, kcal_idx):
            continue
        timestamp = row[timestamp_idx]
        description = row[description_idx]
        if description.startswith("Health Connect total calories"):
            continue
        kcal = _parse_kcal(row[kcal_idx])
        if kcal is None:
            continue
        note = row[note_idx] if note_idx is not None and len(row) > note_idx else ""

        description_date = _parse_fitness_date_from_description(description)
        if description_date:
            if description_date == target:
                by_description[description or timestamp] = (timestamp, kcal, note)
            continue

        timestamp_date = _parse_date_from_timestamp(timestamp)
        if timestamp_date == target:
            by_timestamp[description or timestamp] = (timestamp, kcal, note)

    candidates = by_description or by_timestamp
    if not candidates:
        return None

    total = sum(kcal for _, kcal, _ in candidates.values())
    latest_timestamp = max((timestamp for timestamp, _, _ in candidates.values()), default="")
    note = f"сохранённые данные fitness на {latest_timestamp}" if latest_timestamp else "сохранённые данные fitness"
    return total, note


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
