import logging
import os
import re
import sqlite3
import json
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

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
DAY_FLAGS_HEADERS = ("timestamp", "user_id", "date", "is_cheatmeal", "note")

FITNESS_DATE_PATTERN = re.compile(r"from (\d{4}-\d{2}-\d{2}) to \d{4}-\d{2}-\d{2}")
HEALTH_CONNECT_RECORDED_AT_PATTERN = re.compile(r"(?:^|;\s*)recorded_at=([^;]+)")
MANUAL_EXPENDITURE_PREFIX = "Manual expenditure override for "
GARMIN_CLOUD_PREFIX = "Garmin Connect cloud daily calories for "
DAY_START_HOUR = 3


def _use_sqlite_storage() -> bool:
    from config import load_config

    return load_config().MEALBOT_STORAGE.strip().lower() == "sqlite"


def _sqlite_path() -> Path:
    from config import load_config

    configured = Path(load_config().MEALBOT_SQLITE_PATH)
    if not configured.is_absolute():
        configured = Path(__file__).parent / configured
    configured.parent.mkdir(parents=True, exist_ok=True)
    return configured


def _meal_audit_path() -> Path:
    configured = _sqlite_path()
    return configured.with_name(f"{configured.stem}_meal_audit.jsonl")


def _append_meal_audit_event(event: str, row: list, tz: str, error: str | None = None) -> None:
    payload = {
        "event": event,
        "recorded_at": datetime.now(timezone(tz)).isoformat(),
        "row": row,
    }
    if error:
        payload["error"] = error

    path = _meal_audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as audit_file:
        audit_file.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _sqlite_connect() -> sqlite3.Connection:
    connection = sqlite3.connect(_sqlite_path())
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS sheet_rows (
            sheet TEXT NOT NULL,
            position INTEGER NOT NULL,
            row_json TEXT NOT NULL,
            PRIMARY KEY (sheet, position)
        )
        """
    )
    return connection


class SQLiteWorksheet:
    def __init__(self, title: str, header: tuple[str, ...]):
        self.title = title
        self.header = [str(value) for value in header]

    def _rows_with_positions(self) -> list[tuple[int, list[str]]]:
        import json

        with _sqlite_connect() as connection:
            rows = connection.execute(
                "SELECT position, row_json FROM sheet_rows WHERE sheet = ? ORDER BY position",
                (self.title,),
            ).fetchall()
        return [(int(position), [str(value) for value in json.loads(row_json)]) for position, row_json in rows]

    def _visible_position(self, visible_row: int) -> int | None:
        if visible_row < 2:
            return None
        rows = self._rows_with_positions()
        index = visible_row - 2
        if index < 0 or index >= len(rows):
            return None
        return rows[index][0]

    def get_all_values(self) -> list[list[str]]:
        return [self.header] + [row for _, row in self._rows_with_positions()]

    def append_row(self, row: list) -> None:
        import json

        normalized = [str(value) for value in row]
        if normalized == self.header:
            return
        with _sqlite_connect() as connection:
            next_position = connection.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 FROM sheet_rows WHERE sheet = ?",
                (self.title,),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO sheet_rows(sheet, position, row_json) VALUES (?, ?, ?)",
                (self.title, int(next_position), json.dumps(normalized, ensure_ascii=False)),
            )

    def update(self, range_name: str, values: list[list]) -> None:
        import json

        match = re.search(r"(\d+)", range_name)
        if not match or not values:
            return
        visible_row = int(match.group(1))
        position = self._visible_position(visible_row)
        if position is None:
            return
        row = [str(value) for value in values[0]]
        with _sqlite_connect() as connection:
            connection.execute(
                "UPDATE sheet_rows SET row_json = ? WHERE sheet = ? AND position = ?",
                (json.dumps(row, ensure_ascii=False), self.title, position),
            )

    def row_values(self, row_number: int) -> list[str]:
        if row_number == 1:
            return list(self.header)
        position = self._visible_position(row_number)
        if position is None:
            return []
        for current_position, row in self._rows_with_positions():
            if current_position == position:
                return row
        return []

    def delete_rows(self, row_number: int) -> None:
        position = self._visible_position(row_number)
        if position is None:
            return
        with _sqlite_connect() as connection:
            connection.execute(
                "DELETE FROM sheet_rows WHERE sheet = ? AND position = ?",
                (self.title, position),
            )

    def clear(self) -> None:
        with _sqlite_connect() as connection:
            connection.execute("DELETE FROM sheet_rows WHERE sheet = ?", (self.title,))

    def findall(self, value: str, in_column: int | None = None):
        result = []
        target = str(value)
        column_index = in_column - 1 if in_column else None
        for visible_row, row in enumerate(self.get_all_values()[1:], start=2):
            columns = [column_index] if column_index is not None else range(len(row))
            for idx in columns:
                if idx is not None and idx < len(row) and row[idx] == target:
                    result.append(SimpleNamespace(row=visible_row, col=idx + 1, value=row[idx]))
        return result


def _is_sheets_configured() -> bool:
    """Проверяет, настроены ли Google Sheets."""
    if _use_sqlite_storage():
        return True

    from config import load_config

    config = load_config()
    return bool(config.GOOGLE_SHEETS_ID and config.GOOGLE_CREDENTIALS_JSON)


def _resolve_credentials_path(credentials_path: str) -> Path:
    """Превращает относительный путь в абсолютный относительно папки проекта."""
    path = Path(credentials_path)
    if not path.is_absolute():
        path = (Path(__file__).parent / path).resolve()
    return path


@lru_cache(maxsize=8)
def _get_spreadsheet(credentials_path: str, sheet_id: str):
    """Подключается к Google Sheets и возвращает объект таблицы."""
    resolved_path = _resolve_credentials_path(credentials_path)
    creds = Credentials.from_service_account_file(
        resolved_path,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    client = gspread.authorize(creds)
    return client.open_by_key(sheet_id)


@lru_cache(maxsize=24)
def _get_worksheet(credentials_path: str, sheet_id: str, title: str, rows: int, cols: int, header: tuple[str, ...]):
    if _use_sqlite_storage():
        return SQLiteWorksheet(title, header)

    spreadsheet = _get_spreadsheet(credentials_path, sheet_id)

    try:
        return spreadsheet.worksheet(title)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=title, rows=rows, cols=cols)
        worksheet.append_row(list(header))
        return worksheet


def _get_sheet(credentials_path: str, sheet_id: str):
    """Подключается к Google Sheets и возвращает лист `log`."""
    return _get_worksheet(credentials_path, sheet_id, SHEET_NAME, 1000, len(HEADERS), tuple(HEADERS))


def _get_fitness_sheet(credentials_path: str, sheet_id: str):
    """Подключается к Google Sheets и возвращает лист `fitness`."""
    return _get_worksheet(
        credentials_path,
        sheet_id,
        "fitness",
        1000,
        4,
        ("timestamp", "description", "kcal", "note"),
    )


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


def _parse_fitness_target_date(description: str) -> str | None:
    if description.startswith(MANUAL_EXPENDITURE_PREFIX):
        return description.removeprefix(MANUAL_EXPENDITURE_PREFIX).strip()
    if description.startswith(GARMIN_CLOUD_PREFIX):
        return description.removeprefix(GARMIN_CLOUD_PREFIX).strip()
    if description.startswith("Health Connect total calories for "):
        return description.removeprefix("Health Connect total calories for ").strip()
    return _parse_fitness_date_from_description(description)


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


def _is_manual_expenditure_note(note: str) -> bool:
    return str(note or "").startswith("Manual Mini App override")


def _normalize_cell(value) -> str:
    text = "" if value is None else str(value).strip()
    try:
        number = float(text.replace(",", "."))
    except ValueError:
        return text
    if number.is_integer():
        return str(int(number))
    return str(number)


def _meal_row_matches(left: list, right: list) -> bool:
    if len(left) != len(right):
        return False
    return all(_normalize_cell(a) == _normalize_cell(b) for a, b in zip(left, right))


def _verify_meal_row_persisted(worksheet, row: list) -> bool:
    try:
        rows = worksheet.get_all_values()
    except Exception as e:
        logger.warning("Не удалось перечитать хранилище после записи еды: %s", e)
        return False
    return any(_meal_row_matches(current, row) for current in rows[1:])


def get_day_bounds(
    target_date: date,
    tz: str = "Europe/Moscow",
    day_start_hour: int = DAY_START_HOUR,
) -> tuple[datetime, datetime]:
    tzinfo = timezone(tz)
    start_naive = datetime.combine(target_date, time(hour=day_start_hour))
    start = tzinfo.localize(start_naive)
    return start, start + timedelta(days=1)


def get_current_food_day(
    tz: str = "Europe/Moscow",
    day_start_hour: int = DAY_START_HOUR,
) -> date:
    now = datetime.now(timezone(tz))
    if now.hour < day_start_hour:
        return now.date() - timedelta(days=1)
    return now.date()


def _parse_meal_timestamp(timestamp: str, tz: str = "Europe/Moscow") -> datetime | None:
    if not timestamp:
        return None

    for fmt in ("%Y-%m-%d %H:%M",):
        try:
            parsed = datetime.strptime(timestamp, fmt)
            return timezone(tz).localize(parsed)
        except ValueError:
            pass

    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return timezone(tz).localize(parsed)
    return parsed.astimezone(timezone(tz))


def _parse_health_connect_recorded_at(note: str, tz: str = "Europe/Moscow") -> datetime | None:
    match = HEALTH_CONNECT_RECORDED_AT_PATTERN.search(note or "")
    if not match:
        return None

    value = match.group(1).strip()
    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return timezone(tz).localize(parsed)
    return parsed.astimezone(timezone(tz))


def is_complete_health_connect_total(date_value: date, note: str, tz: str = "Europe/Moscow") -> bool:
    """Reject Health Connect totals captured before the calendar day ended."""
    recorded_at = _parse_health_connect_recorded_at(note, tz)
    if recorded_at is None:
        return True

    tzinfo = timezone(tz)
    day_end = tzinfo.localize(datetime.combine(date_value + timedelta(days=1), time.min))
    return recorded_at >= day_end


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
        note = row[note_idx]
        if not is_complete_health_connect_total(date_value, note):
            logger.info(
                "Ignoring partial Health Connect calories for %s: note=%s",
                date_value.isoformat(),
                note,
            )
            continue
        try:
            kcal = float(str(row[kcal_idx]).replace(",", ".") or 0)
        except ValueError:
            continue
        latest = (row[timestamp_idx], kcal, note)

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
    manual_description = f"{MANUAL_EXPENDITURE_PREFIX}{target}"
    by_description: dict[str, tuple[str, float, str]] = {}
    by_timestamp: dict[str, tuple[str, float, str]] = {}
    manual: tuple[str, float, str] | None = None

    for row in values[1:]:
        if len(row) <= max(timestamp_idx, description_idx, kcal_idx):
            continue
        timestamp = row[timestamp_idx]
        description = row[description_idx]
        kcal = _parse_kcal(row[kcal_idx])
        if kcal is None:
            continue
        note = row[note_idx] if note_idx is not None and len(row) > note_idx else ""
        if description == manual_description:
            manual = (timestamp, kcal, note)
            continue
        if _is_manual_expenditure_note(note):
            description_date = _parse_fitness_target_date(description)
            if description_date == target:
                manual = (timestamp, kcal, note)
            elif description_date is None and _parse_date_from_timestamp(timestamp) == target:
                manual = (timestamp, kcal, note)
            continue
        if description.startswith("Health Connect total calories") or description.startswith(GARMIN_CLOUD_PREFIX):
            continue

        description_date = _parse_fitness_date_from_description(description)
        if description_date:
            if description_date == target:
                by_description[description or timestamp] = (timestamp, kcal, note)
            continue

        timestamp_date = _parse_date_from_timestamp(timestamp)
        if timestamp_date == target:
            by_timestamp[description or timestamp] = (timestamp, kcal, note)

    if manual is not None:
        _, kcal, note = manual
        return kcal, note or "Manual Mini App override"

    candidates = by_description or by_timestamp
    if not candidates:
        return None

    total = sum(kcal for _, kcal, _ in candidates.values())
    latest_timestamp = max((timestamp for timestamp, _, _ in candidates.values()), default="")
    note = f"сохранённые данные fitness на {latest_timestamp}" if latest_timestamp else "сохранённые данные fitness"
    return total, note


def get_saved_fitness_calories_for_range(
    start_date: date,
    end_date: date,
    include_google_fit_fallback: bool = True,
) -> dict[str, dict]:
    """Returns saved expenditure calories by date from the fitness sheet."""
    if not _is_sheets_configured():
        return {}

    from config import load_config

    if not isinstance(start_date, date):
        start_date = date.fromisoformat(str(start_date))
    if not isinstance(end_date, date):
        end_date = date.fromisoformat(str(end_date))
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    config = load_config()
    worksheet = _get_fitness_sheet(config.GOOGLE_CREDENTIALS_JSON, config.GOOGLE_SHEETS_ID)
    values = worksheet.get_all_values()
    if len(values) < 2:
        return {}

    header = values[0]
    required = {"timestamp", "description", "kcal"}
    if not required.issubset(set(header)):
        return {}

    timestamp_idx = header.index("timestamp")
    description_idx = header.index("description")
    kcal_idx = header.index("kcal")
    note_idx = header.index("note") if "note" in header else None

    health_connect: dict[str, tuple[str, float, str]] = {}
    garmin_cloud: dict[str, tuple[str, float, str]] = {}
    manual: dict[str, tuple[str, float, str]] = {}
    by_description: dict[str, dict[str, tuple[str, float, str]]] = {}
    by_timestamp: dict[str, dict[str, tuple[str, float, str]]] = {}

    for row in values[1:]:
        if len(row) <= max(timestamp_idx, description_idx, kcal_idx):
            continue

        timestamp = row[timestamp_idx]
        description = row[description_idx]
        kcal = _parse_kcal(row[kcal_idx])
        if kcal is None:
            continue
        note = row[note_idx] if note_idx is not None and len(row) > note_idx else ""

        if description.startswith(MANUAL_EXPENDITURE_PREFIX):
            date_text = description.removeprefix(MANUAL_EXPENDITURE_PREFIX).strip()
            try:
                parsed_date = date.fromisoformat(date_text)
            except ValueError:
                continue
            if start_date <= parsed_date <= end_date:
                manual[parsed_date.isoformat()] = (timestamp, kcal, note)
            continue

        if _is_manual_expenditure_note(note):
            manual_date = _parse_fitness_target_date(description) or _parse_date_from_timestamp(timestamp)
            if manual_date:
                try:
                    parsed_date = date.fromisoformat(manual_date)
                except ValueError:
                    continue
                if start_date <= parsed_date <= end_date:
                    manual[parsed_date.isoformat()] = (timestamp, kcal, note)
            continue

        if description.startswith(GARMIN_CLOUD_PREFIX):
            date_text = description.removeprefix(GARMIN_CLOUD_PREFIX).strip()
            try:
                parsed_date = date.fromisoformat(date_text)
            except ValueError:
                continue
            if start_date <= parsed_date <= end_date:
                garmin_cloud[parsed_date.isoformat()] = (timestamp, kcal, note)
            continue

        if description.startswith("Health Connect total calories for "):
            date_text = description.removeprefix("Health Connect total calories for ").strip()
            try:
                parsed_date = date.fromisoformat(date_text)
            except ValueError:
                continue
            if start_date <= parsed_date <= end_date:
                if not is_complete_health_connect_total(parsed_date, note):
                    logger.info(
                        "Ignoring partial Health Connect calories for %s: note=%s",
                        parsed_date.isoformat(),
                        note,
                    )
                    continue
                health_connect[parsed_date.isoformat()] = (timestamp, kcal, note)
            continue

        description_date = _parse_fitness_date_from_description(description)
        if description_date:
            if not include_google_fit_fallback:
                continue
            try:
                parsed_date = date.fromisoformat(description_date)
            except ValueError:
                continue
            if start_date <= parsed_date <= end_date:
                by_description.setdefault(description_date, {})[description or timestamp] = (timestamp, kcal, note)
            continue

        timestamp_date = _parse_date_from_timestamp(timestamp)
        if timestamp_date:
            if not include_google_fit_fallback:
                continue
            try:
                parsed_date = date.fromisoformat(timestamp_date)
            except ValueError:
                continue
            if start_date <= parsed_date <= end_date:
                by_timestamp.setdefault(timestamp_date, {})[description or timestamp] = (timestamp, kcal, note)

    summaries: dict[str, dict] = {}
    for date_key, (_, kcal, note) in garmin_cloud.items():
        summaries[date_key] = {
            "date": date_key,
            "expenditure_kcal": round(kcal, 1),
            "note": note or "Garmin Connect cloud",
        }

    for date_key, (_, kcal, note) in health_connect.items():
        summaries[date_key] = {
            "date": date_key,
            "expenditure_kcal": round(kcal, 1),
            "note": note or "Health Connect",
        }

    for date_key, (_, kcal, note) in manual.items():
        summaries[date_key] = {
            "date": date_key,
            "expenditure_kcal": round(kcal, 1),
            "note": note or "Manual Mini App override",
        }

    for date_key in set(by_description) | set(by_timestamp):
        if date_key in summaries:
            continue
        candidates = by_description.get(date_key) or by_timestamp.get(date_key) or {}
        if not candidates:
            continue
        total = sum(kcal for _, kcal, _ in candidates.values())
        latest_timestamp = max((timestamp for timestamp, _, _ in candidates.values()), default="")
        note = f"сохранённые данные fitness на {latest_timestamp}" if latest_timestamp else "сохранённые данные fitness"
        summaries[date_key] = {
            "date": date_key,
            "expenditure_kcal": round(total, 1),
            "note": note,
        }

    return summaries


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
    return _get_worksheet(
        credentials_path,
        sheet_id,
        "daily_calories",
        1000,
        7,
        (
            "timestamp",
            "user_id",
            "date",
            "intake_kcal",
            "expenditure_kcal",
            "difference_kcal",
            "note",
        ),
    )


def _get_day_flags_sheet(credentials_path: str, sheet_id: str):
    """Подключается к Google Sheets и возвращает лист `day_flags`."""
    return _get_worksheet(
        credentials_path,
        sheet_id,
        "day_flags",
        1000,
        len(DAY_FLAGS_HEADERS),
        DAY_FLAGS_HEADERS,
    )


def _is_truthy_flag(value: str | bool | int | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "да", "читмил"}


def _find_day_flag_row_index(worksheet, user_id: int | str, date_value: date | str) -> int | None:
    values = worksheet.get_all_values()
    if len(values) < 2:
        return None
    header = values[0]
    required = {"user_id", "date"}
    if not required.issubset(set(header)):
        return None

    user_id_idx = header.index("user_id")
    date_idx = header.index("date")
    target_user_id = str(user_id)
    target_date = date_value.isoformat() if isinstance(date_value, date) else str(date_value)

    for row_index, row in enumerate(values[1:], start=2):
        if len(row) <= max(user_id_idx, date_idx):
            continue
        if row[user_id_idx] == target_user_id and row[date_idx] == target_date:
            return row_index
    return None


def set_cheatmeal_day(
    user_id: int,
    date_value: date | str,
    is_cheatmeal: bool,
    tz: str = "Europe/Moscow",
    note: str = "",
) -> dict:
    """Создаёт или обновляет флаг читмила для пищевых суток."""
    if not _is_sheets_configured():
        logger.warning("Google Sheets не настроен, флаг читмила не сохранён")
        return {
            "user_id": str(user_id),
            "date": date_value.isoformat() if isinstance(date_value, date) else str(date_value),
            "is_cheatmeal": bool(is_cheatmeal),
            "note": note,
        }

    from config import load_config

    config = load_config()
    worksheet = _get_day_flags_sheet(config.GOOGLE_CREDENTIALS_JSON, config.GOOGLE_SHEETS_ID)
    now = datetime.now(timezone(tz)).strftime("%Y-%m-%d %H:%M")
    date_text = date_value.isoformat() if isinstance(date_value, date) else str(date_value)
    row = [
        now,
        str(user_id),
        date_text,
        "TRUE" if is_cheatmeal else "FALSE",
        note,
    ]

    row_index = _find_day_flag_row_index(worksheet, user_id, date_text)
    if row_index:
        worksheet.update(f"A{row_index}:E{row_index}", [row])
    else:
        worksheet.append_row(row)

    logger.info("Флаг читмила сохранён: user_id=%s date=%s is_cheatmeal=%s", user_id, date_text, is_cheatmeal)
    return {
        "user_id": str(user_id),
        "date": date_text,
        "is_cheatmeal": bool(is_cheatmeal),
        "note": note,
    }


def get_cheatmeal_days_for_range(
    user_id: int,
    start_date: date,
    end_date: date,
) -> set[str]:
    """Возвращает даты пищевых суток, отмеченные как читмил."""
    if not _is_sheets_configured():
        return set()

    from config import load_config

    if not isinstance(start_date, date):
        start_date = date.fromisoformat(str(start_date))
    if not isinstance(end_date, date):
        end_date = date.fromisoformat(str(end_date))
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    config = load_config()
    worksheet = _get_day_flags_sheet(config.GOOGLE_CREDENTIALS_JSON, config.GOOGLE_SHEETS_ID)
    rows = worksheet.get_all_values()
    if len(rows) < 2:
        return set()

    header = rows[0]
    required = {"user_id", "date", "is_cheatmeal"}
    if not required.issubset(set(header)):
        return set()

    user_id_idx = header.index("user_id")
    date_idx = header.index("date")
    flag_idx = header.index("is_cheatmeal")
    target_user_id = str(user_id)
    result: set[str] = set()

    for row in rows[1:]:
        if len(row) <= max(user_id_idx, date_idx, flag_idx):
            continue
        if row[user_id_idx] != target_user_id:
            continue
        try:
            row_date = date.fromisoformat(row[date_idx])
        except ValueError:
            continue
        if start_date <= row_date <= end_date and _is_truthy_flag(row[flag_idx]):
            result.add(row_date.isoformat())

    return result


def is_cheatmeal_day(user_id: int, date_value: date | str) -> bool:
    date_text = date_value.isoformat() if isinstance(date_value, date) else str(date_value)
    target_date = date.fromisoformat(date_text)
    return date_text in get_cheatmeal_days_for_range(user_id, target_date, target_date)


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
    expenditure_kcal: float | None,
    difference_kcal: float | None,
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
        "" if expenditure_kcal is None else round(float(expenditure_kcal), 1),
        "" if difference_kcal is None else round(float(difference_kcal), 1),
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


def get_daily_calorie_summaries_for_range(
    user_id: int,
    start_date: date,
    end_date: date,
    tz: str = "Europe/Moscow",
) -> dict[str, dict]:
    """Возвращает последние сохранённые суточные балансы калорий по датам."""
    if not _is_sheets_configured():
        return {}

    from config import load_config

    if not isinstance(start_date, date):
        start_date = date.fromisoformat(str(start_date))
    if not isinstance(end_date, date):
        end_date = date.fromisoformat(str(end_date))
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    config = load_config()
    worksheet = _get_daily_calories_sheet(config.GOOGLE_CREDENTIALS_JSON, config.GOOGLE_SHEETS_ID)
    rows = worksheet.get_all_values()
    if len(rows) < 2:
        return {}

    header = rows[0]
    required = {"timestamp", "user_id", "date", "intake_kcal", "expenditure_kcal", "difference_kcal"}
    if not required.issubset(set(header)):
        return {}

    timestamp_idx = header.index("timestamp")
    user_id_idx = header.index("user_id")
    date_idx = header.index("date")
    intake_idx = header.index("intake_kcal")
    expenditure_idx = header.index("expenditure_kcal")
    difference_idx = header.index("difference_kcal")
    note_idx = header.index("note") if "note" in header else None

    summaries: dict[str, dict] = {}
    for row in rows[1:]:
        if len(row) <= max(timestamp_idx, user_id_idx, date_idx, intake_idx, expenditure_idx, difference_idx):
            continue
        if row[user_id_idx] != str(user_id):
            continue
        try:
            row_date = date.fromisoformat(row[date_idx])
        except ValueError:
            continue
        if not start_date <= row_date <= end_date:
            continue

        date_key = row_date.isoformat()
        previous = summaries.get(date_key)
        timestamp = row[timestamp_idx]
        if previous and previous.get("timestamp", "") > timestamp:
            continue

        summaries[date_key] = {
            "date": date_key,
            "timestamp": timestamp,
            "intake_kcal": round(_to_number(row[intake_idx]), 1),
            "expenditure_kcal": None
            if str(row[expenditure_idx]).strip() == ""
            else round(_to_number(row[expenditure_idx]), 1),
            "difference_kcal": None
            if str(row[difference_idx]).strip() == ""
            else round(_to_number(row[difference_idx]), 1),
            "note": row[note_idx] if note_idx is not None and len(row) > note_idx else "",
        }

    return summaries


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

    _append_meal_audit_event("attempt", row, tz)
    try:
        worksheet.append_row(row)
    except Exception as e:
        _append_meal_audit_event("append_failed", row, tz, str(e))
        raise

    if not _verify_meal_row_persisted(worksheet, row):
        _append_meal_audit_event("verify_failed", row, tz)
        raise RuntimeError("Meal row append was not visible after write")

    _append_meal_audit_event("verified", row, tz)
    logger.info("Запись добавлена для user_id=%s: %s", user_id, data.get("name"))


def get_logs_for_date(user_id: int, target_date: date, tz: str = "Europe/Moscow") -> list[dict]:
    """Возвращает все записи пользователя за пищевые сутки 03:00-03:00."""
    if not _is_sheets_configured():
        return []

    from config import load_config

    config = load_config()
    worksheet = _get_sheet(config.GOOGLE_CREDENTIALS_JSON, config.GOOGLE_SHEETS_ID)

    if not isinstance(target_date, date):
        target_date = date.fromisoformat(str(target_date))
    start, end = get_day_bounds(target_date, tz=tz)

    all_rows = worksheet.get_all_values()
    if not all_rows:
        return []

    header = all_rows[0]
    records = []

    for row in all_rows[1:]:
        if len(row) < len(header):
            continue
        record = dict(zip(header, row))
        timestamp = _parse_meal_timestamp(record.get("timestamp", ""), tz=tz)
        if (
            record.get("user_id") == str(user_id)
            and timestamp is not None
            and start <= timestamp < end
        ):
            records.append(record)

    return records


def _normalize_meal_record(row_index: int, header: list[str], row: list[str]) -> dict:
    record = dict(zip(header, row))
    record["row_id"] = row_index
    for key in HEADERS:
        record.setdefault(key, "")
    return record


def get_meal_logs_for_range(
    user_id: int,
    start_date: date,
    end_date: date,
    tz: str = "Europe/Moscow",
) -> list[dict]:
    """Возвращает записи пользователя за диапазон пищевых суток включительно."""
    if not _is_sheets_configured():
        return []

    from config import load_config

    if not isinstance(start_date, date):
        start_date = date.fromisoformat(str(start_date))
    if not isinstance(end_date, date):
        end_date = date.fromisoformat(str(end_date))
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    config = load_config()
    worksheet = _get_sheet(config.GOOGLE_CREDENTIALS_JSON, config.GOOGLE_SHEETS_ID)
    all_rows = worksheet.get_all_values()
    if not all_rows:
        return []

    header = all_rows[0]
    records = []
    range_start, _ = get_day_bounds(start_date, tz=tz)
    _, range_end = get_day_bounds(end_date, tz=tz)

    for row_index, row in enumerate(all_rows[1:], start=2):
        if len(row) < len(header):
            continue
        record = _normalize_meal_record(row_index, header, row)
        timestamp = _parse_meal_timestamp(record.get("timestamp", ""), tz=tz)
        if (
            record.get("user_id") == str(user_id)
            and timestamp is not None
            and range_start <= timestamp < range_end
        ):
            records.append(record)

    return records


def _to_number(value, default: float = 0) -> float:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


def update_meal(row_id: int, user_id: int, data: dict, tz: str = "Europe/Moscow") -> dict | None:
    """Обновляет строку приема пищи, если она принадлежит пользователю."""
    if not _is_sheets_configured():
        return None

    from config import load_config

    row_id = int(row_id)
    if row_id < 2:
        return None

    config = load_config()
    worksheet = _get_sheet(config.GOOGLE_CREDENTIALS_JSON, config.GOOGLE_SHEETS_ID)
    all_rows = worksheet.get_all_values()
    if row_id > len(all_rows):
        return None

    header = all_rows[0]
    current_row = all_rows[row_id - 1]
    if len(current_row) < len(header):
        current_row += [""] * (len(header) - len(current_row))
    current = _normalize_meal_record(row_id, header, current_row)
    if current.get("user_id") != str(user_id):
        return None

    updated = {
        "timestamp": str(data.get("timestamp", current.get("timestamp", ""))).strip(),
        "user_id": str(user_id),
        "name": str(data.get("name", current.get("name", ""))).strip(),
        "weight_g": round(_to_number(data.get("weight_g", current.get("weight_g", 0))), 1),
        "kcal": round(_to_number(data.get("kcal", current.get("kcal", 0))), 1),
        "protein_g": round(_to_number(data.get("protein_g", current.get("protein_g", 0))), 1),
        "fat_g": round(_to_number(data.get("fat_g", current.get("fat_g", 0))), 1),
        "carbs_g": round(_to_number(data.get("carbs_g", current.get("carbs_g", 0))), 1),
        "confidence": str(data.get("confidence", current.get("confidence", ""))).strip(),
        "note": str(data.get("note", current.get("note", ""))).strip(),
    }

    if _parse_meal_timestamp(updated["timestamp"], tz=tz) is None:
        updated["timestamp"] = current.get("timestamp", "")

    row = [updated.get(key, "") for key in HEADERS]
    worksheet.update(f"A{row_id}:J{row_id}", [row])
    updated["row_id"] = row_id
    logger.info("Запись обновлена: row_id=%s user_id=%s", row_id, user_id)
    return updated


def delete_meal(row_id: int, user_id: int) -> bool:
    """Удаляет конкретную строку приема пищи, если она принадлежит пользователю."""
    if not _is_sheets_configured():
        return False

    from config import load_config

    row_id = int(row_id)
    if row_id < 2:
        return False

    config = load_config()
    worksheet = _get_sheet(config.GOOGLE_CREDENTIALS_JSON, config.GOOGLE_SHEETS_ID)
    all_rows = worksheet.get_all_values()
    if row_id > len(all_rows):
        return False

    header = all_rows[0]
    row = all_rows[row_id - 1]
    if len(row) < len(header):
        row += [""] * (len(header) - len(row))
    record = _normalize_meal_record(row_id, header, row)
    if record.get("user_id") != str(user_id):
        return False

    worksheet.delete_rows(row_id)
    logger.info("Удалена строка %d для user_id=%s", row_id, user_id)
    return True


def get_today_logs(user_id: int, tz: str = "Europe/Moscow") -> list[dict]:
    """
    Возвращает все записи пользователя за текущие пищевые сутки.

    Args:
        user_id: Telegram user ID.
        tz: Часовой пояс.

    Returns:
        Список словарей с данными записей.
    """
    return get_logs_for_date(user_id, get_current_food_day(tz=tz), tz=tz)


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
