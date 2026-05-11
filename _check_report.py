import sys
sys.path.insert(0, '/app')
from datetime import date, datetime, timedelta
from pytz import timezone
from report import build_daily_report

tz = timezone('Europe/Moscow')
yesterday = datetime.now(tz).date() - timedelta(days=1)

print(f'Report for yesterday ({yesterday}):')
print('=' * 50)
result = build_daily_report(162187174, tz='Europe/Moscow', target_date=yesterday)
if result:
    print(result)
else:
    print('No records found')