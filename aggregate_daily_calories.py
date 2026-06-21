import argparse
import logging
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from config import load_config
from google_fitness import fetch_calories_range, has_saved_tokens
from sheets import (
    GARMIN_CLOUD_PREFIX,
    MANUAL_EXPENDITURE_PREFIX,
    get_cheatmeal_days_for_range,
    is_complete_health_connect_total,
    log_daily_calories,
    _is_sheets_configured,
    _get_daily_calories_sheet,
    _get_fitness_sheet,
    _get_sheet,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

DATE_PATTERN = re.compile(r"from (\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})")


def _parse_date_from_description(description: str) -> str | None:
    match = DATE_PATTERN.search(description or "")
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


def _load_sheet_rows(worksheet):
    values = worksheet.get_all_values()
    if not values or len(values) < 2:
        return []
    header = values[0]
    return [dict(zip(header, row)) for row in values[1:] if row]


def build_intake_by_user_date(meal_rows: list[dict], start_date: date) -> dict[tuple[str, str], float]:
    intake = {}
    for row in meal_rows:
        timestamp = row.get("timestamp", "")
        user_id = row.get("user_id", "")
        kcal = float(row.get("kcal", 0) or 0)
        row_date = _parse_date_from_timestamp(timestamp)
        if not row_date:
            continue
        if date.fromisoformat(row_date) < start_date:
            continue
        key = (user_id, row_date)
        intake[key] = intake.get(key, 0.0) + kcal
    return intake


def build_expenditure_by_date(
    fitness_rows: list[dict],
    start_date: date,
    include_google_fit_fallback: bool = True,
) -> dict[str, float]:
    manual = {}
    garmin_cloud = {}
    health_connect = {}
    fallback = {}
    for row in fitness_rows:
        timestamp = row.get("timestamp", "")
        description = row.get("description", "")
        kcal = float(row.get("kcal", 0) or 0)
        if description.startswith(MANUAL_EXPENDITURE_PREFIX):
            row_date = description.removeprefix(MANUAL_EXPENDITURE_PREFIX).strip()
            target = manual
        elif description.startswith(GARMIN_CLOUD_PREFIX):
            row_date = description.removeprefix(GARMIN_CLOUD_PREFIX).strip()
            target = garmin_cloud
        elif description.startswith("Health Connect total calories for "):
            row_date = description.removeprefix("Health Connect total calories for ").strip()
            try:
                parsed_row_date = date.fromisoformat(row_date)
            except ValueError:
                continue
            if not is_complete_health_connect_total(parsed_row_date, row.get("note", "")):
                continue
            target = health_connect
        else:
            if not include_google_fit_fallback:
                continue
            row_date = _parse_date_from_description(description) or _parse_date_from_timestamp(timestamp)
            target = fallback
        if not row_date:
            continue
        try:
            parsed = date.fromisoformat(row_date)
        except ValueError:
            continue
        if parsed < start_date:
            continue
        target[row_date] = target.get(row_date, 0.0) + kcal
    return {**fallback, **health_connect, **garmin_cloud, **manual}


def get_existing_daily_rows(worksheet) -> dict[tuple[str, str], int]:
    values = worksheet.get_all_values()
    if not values or len(values) < 2:
        return {}
    header = values[0]
    if "user_id" not in header or "date" not in header:
        return {}
    idx_user = header.index("user_id")
    idx_date = header.index("date")
    existing = {}
    for row_index, row in enumerate(values[1:], start=2):
        if len(row) <= max(idx_user, idx_date):
            continue
        existing[(row[idx_user], row[idx_date])] = row_index
    return existing


def main(start_date: date, end_date: date, overwrite: bool = False, user_id: str | None = None) -> None:
    config = load_config()
    if not _is_sheets_configured():
        logger.error("Хранилище MealBot не настроено. Проверьте .env или переменные окружения.")
        raise ValueError("MealBot storage configuration missing")

    meal_sheet = _get_sheet(config.GOOGLE_CREDENTIALS_JSON, config.GOOGLE_SHEETS_ID)
    daily_sheet = _get_daily_calories_sheet(config.GOOGLE_CREDENTIALS_JSON, config.GOOGLE_SHEETS_ID)

    meal_rows = _load_sheet_rows(meal_sheet)
    logger.info("Meal rows loaded: %d", len(meal_rows))

    intake_by_user_date = build_intake_by_user_date(meal_rows, start_date)
    logger.info("Intake entries from %s to %s: %d", start_date, end_date, len(intake_by_user_date))

    fitness_sheet = _get_fitness_sheet(config.GOOGLE_CREDENTIALS_JSON, config.GOOGLE_SHEETS_ID)
    fitness_rows = _load_sheet_rows(fitness_sheet)
    saved_expenditure_by_date = build_expenditure_by_date(
        fitness_rows,
        start_date,
        include_google_fit_fallback=not config.STRICT_EXPENDITURE_SOURCE,
    )
    if saved_expenditure_by_date:
        logger.info("Expenditure days loaded from fitness sheet: %d", len(saved_expenditure_by_date))

    expenditure_by_date = dict(saved_expenditure_by_date)
    if config.STRICT_EXPENDITURE_SOURCE:
        logger.info("STRICT_EXPENDITURE_SOURCE enabled: skipping Google Fit expenditure fallback.")
    elif has_saved_tokens():
        try:
            google_fit_by_date = fetch_calories_range(start_date, end_date, tz_name=config.TIMEZONE)
            missing_google_fit = {
                key: value
                for key, value in google_fit_by_date.items()
                if key not in expenditure_by_date
            }
            expenditure_by_date.update(missing_google_fit)
            logger.info(
                "Expenditure days fetched from Google Fit fallback: %d",
                len(missing_google_fit),
            )
        except Exception as e:
            logger.warning(
                "Не удалось получить расход калорий из Google Fit: %s. Попытка использовать лист fitness.",
                e,
            )
    else:
        logger.warning("Google Fitness tokens не настроены. Использую данные из листа fitness.")

    user_ids = {row.get("user_id") for row in meal_rows if row.get("user_id")}
    user_ids.update(str(uid) for uid in config.REPORT_USER_IDS)
    if user_id:
        user_ids.add(user_id)
    user_ids = {uid for uid in user_ids if uid}
    logger.info("Target user IDs: %s", sorted(user_ids))
    cheatmeal_days_by_user = {
        uid: get_cheatmeal_days_for_range(int(uid), start_date, end_date)
        for uid in user_ids
        if str(uid).isdigit()
    }

    if not user_ids:
        logger.warning("Нет user_id для записи. Укажите REPORT_USER_IDS, --user-id или добавьте записи в таблицу meal_bot.")

    if overwrite:
        daily_sheet.clear()
        daily_sheet.append_row([
            "timestamp",
            "user_id",
            "date",
            "intake_kcal",
            "expenditure_kcal",
            "difference_kcal",
            "note",
        ])
        existing = {}
    else:
        existing = get_existing_daily_rows(daily_sheet)

    expenditure_dates = {date.fromisoformat(d) for d in expenditure_by_date.keys()}
    dates = sorted({date.fromisoformat(d) for (_, d) in intake_by_user_date.keys()} | expenditure_dates)
    dates = [d for d in dates if start_date <= d <= end_date]
    logger.info("Dates to process: %s", [d.isoformat() for d in dates])

    rows_written = 0
    for target_date in dates:
        target_date_str = target_date.isoformat()
        expenditure = expenditure_by_date.get(target_date_str)
        for user_id in sorted(user_ids):
            if not user_id:
                continue
            key = (user_id, target_date_str)
            is_cheatmeal = target_date_str in cheatmeal_days_by_user.get(user_id, set())
            intake = 0.0 if is_cheatmeal else intake_by_user_date.get(key, 0.0)
            if intake == 0 and expenditure is None:
                continue
            if not overwrite and key in existing:
                row_number = existing[key]
                existing_row = daily_sheet.row_values(row_number)
                if len(existing_row) < 7:
                    existing_row += [""] * (7 - len(existing_row))
                current_intake = float(existing_row[3] or 0)
                current_expenditure = float(existing_row[4]) if str(existing_row[4]).strip() else None
                new_intake = intake if intake != 0 or is_cheatmeal else current_intake
                new_expenditure = expenditure if expenditure is not None else current_expenditure
                if new_intake == current_intake and new_expenditure == current_expenditure:
                    continue
                new_difference = None if new_expenditure is None else new_intake - new_expenditure
                now = datetime.now().strftime("%Y-%m-%d %H:%M")
                note = f"API import {start_date.isoformat()}..{end_date.isoformat()}"
                if is_cheatmeal:
                    note += "; читмил: приход не учитывается"
                daily_sheet.update(
                    f"A{row_number}:G{row_number}",
                    [[
                        now,
                        user_id,
                        target_date_str,
                        round(new_intake, 1),
                        "" if new_expenditure is None else round(new_expenditure, 1),
                        "" if new_difference is None else round(new_difference, 1),
                        note,
                    ]],
                )
                rows_written += 1
                continue
            difference = None if expenditure is None else intake - expenditure
            note = f"API import {start_date.isoformat()}..{end_date.isoformat()}"
            if is_cheatmeal:
                note += "; читмил: приход не учитывается"
            log_daily_calories(
                user_id,
                intake,
                expenditure,
                difference,
                target_date_str,
                tz=config.TIMEZONE,
                note=note,
            )
            rows_written += 1

    print(f"Готово. Записано {rows_written} строк в лист daily_calories.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Aggregate meal and fitness data into daily_calories sheet.")
    parser.add_argument("--start-date", default="2026-05-02", help="Дата начала в формате YYYY-MM-DD")
    parser.add_argument("--end-date", default="2026-05-07", help="Дата конца в формате YYYY-MM-DD")
    parser.add_argument("--overwrite", action="store_true", help="Перезаписать существующий лист daily_calories")
    parser.add_argument("--user-id", help="User ID для записи backfill-данных", type=str)
    args = parser.parse_args()
    main(
        date.fromisoformat(args.start_date),
        date.fromisoformat(args.end_date),
        overwrite=args.overwrite,
        user_id=args.user_id,
    )
