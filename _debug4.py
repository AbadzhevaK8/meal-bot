import sys
sys.path.insert(0, '/app')
from datetime import datetime
from pytz import timezone
from report import build_daily_report

tz = timezone('Europe/Moscow')
now = datetime.now(tz)
print(f'Current time: {now.strftime("%H:%M")}')
print(f'Hour: {now.hour}')
print(f'Will show note: {now.hour < 22}')
print()
print(build_daily_report(162187174))