from sheets import _get_fitness_sheet, _load_sheet_rows, build_expenditure_by_date
from config import load_config
import datetime

config = load_config()
fitness = _get_fitness_sheet(config.GOOGLE_CREDENTIALS_JSON, config.GOOGLE_SHEETS_ID)
rows = _load_sheet_rows(fitness)
print('fitness rows count', len(rows))
print(rows[:20])
print('parsed expenditure', build_expenditure_by_date(rows, datetime.date(2026,5,2)))
