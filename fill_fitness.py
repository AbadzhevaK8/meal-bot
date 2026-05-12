from datetime import date, datetime, timedelta

from pytz import timezone

from config import load_config
from google_fitness import fetch_calories_range
from sheets import _get_fitness_sheet

config = load_config()
ws = _get_fitness_sheet(config.GOOGLE_CREDENTIALS_JSON, config.GOOGLE_SHEETS_ID)

# Очистим лист и оставим только заголовок
ws.clear()
ws.append_row(["timestamp", "description", "kcal", "note"])

start_date = date(2026, 5, 2)
end_date = datetime.now(timezone(config.TIMEZONE)).date() - timedelta(days=1)
results = fetch_calories_range(start_date, end_date, config.TIMEZONE)

for day, kcal in results.items():
    day_date = datetime.fromisoformat(day).date()
    description = f"Calories expended (+BMR if available) from {day} to {(day_date + timedelta(days=1)).strftime('%Y-%m-%d')}"
    ws.append_row([
        datetime.now(timezone(config.TIMEZONE)).strftime('%Y-%m-%d %H:%M'),
        description,
        round(kcal, 1),
        f"Auto fill {start_date.isoformat()}..{end_date.isoformat()}",
    ])
    print(day, kcal)

print('Fitness sheet filled from', start_date, 'to', end_date)
