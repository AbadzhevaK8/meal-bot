import argparse
import json
import sqlite3
from pathlib import Path

from config import load_config
from sheets import (
    DAY_FLAGS_HEADERS,
    HEADERS,
    _get_daily_calories_sheet,
    _get_day_flags_sheet,
    _get_fitness_sheet,
    _get_sheet,
    _sqlite_path,
)


SHEETS = (
    ("log", HEADERS, _get_sheet),
    ("fitness", ("timestamp", "description", "kcal", "note"), _get_fitness_sheet),
    (
        "daily_calories",
        ("timestamp", "user_id", "date", "intake_kcal", "expenditure_kcal", "difference_kcal", "note"),
        _get_daily_calories_sheet,
    ),
    ("day_flags", DAY_FLAGS_HEADERS, _get_day_flags_sheet),
)


def migrate(overwrite: bool = False, sqlite_path: Path | None = None) -> None:
    config = load_config()
    if not config.GOOGLE_SHEETS_ID or not config.GOOGLE_CREDENTIALS_JSON:
        raise RuntimeError("GOOGLE_SHEETS_ID and GOOGLE_CREDENTIALS_JSON are required for migration")

    target = sqlite_path or _sqlite_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(target) as connection:
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
        for sheet_name, expected_header, getter in SHEETS:
            worksheet = getter(config.GOOGLE_CREDENTIALS_JSON, config.GOOGLE_SHEETS_ID)
            values = worksheet.get_all_values()
            rows = values[1:] if values else []
            if overwrite:
                connection.execute("DELETE FROM sheet_rows WHERE sheet = ?", (sheet_name,))
            existing = connection.execute(
                "SELECT COUNT(*) FROM sheet_rows WHERE sheet = ?",
                (sheet_name,),
            ).fetchone()[0]
            if existing:
                print(f"{sheet_name}: skipped, {existing} rows already exist")
                continue
            for position, row in enumerate(rows, start=1):
                padded = list(row) + [""] * max(0, len(expected_header) - len(row))
                connection.execute(
                    "INSERT INTO sheet_rows(sheet, position, row_json) VALUES (?, ?, ?)",
                    (sheet_name, position, json.dumps(padded[: len(expected_header)], ensure_ascii=False)),
                )
            print(f"{sheet_name}: migrated {len(rows)} rows")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Copy MealBot Google Sheets data into local SQLite storage.")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing SQLite rows for migrated sheets.")
    parser.add_argument("--sqlite-path", type=Path, help="Override target SQLite path.")
    args = parser.parse_args()
    migrate(overwrite=args.overwrite, sqlite_path=args.sqlite_path)
