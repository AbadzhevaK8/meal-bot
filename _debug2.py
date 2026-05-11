import sys
sys.path.insert(0, '/app')
from google_fitness import _aggregate_calories, _parse_calories
from pytz import timezone
from datetime import datetime, timedelta

tz = timezone('Europe/Moscow')
today = datetime.now(tz).date()
yesterday = today - timedelta(days=1)

# Yesterday full day
s = tz.localize(datetime(yesterday.year, yesterday.month, yesterday.day, 0, 0, 0))
e = s + timedelta(days=1)
r = _aggregate_calories(int(s.timestamp()*1000), int(e.timestamp()*1000))
print(f'Yesterday ({yesterday}): {_parse_calories(r)} kcal')
for b in r.get('bucket', []):
    for d in b.get('dataset', []):
        for p in d.get('point', []):
            st = int(p.get('startTimeNanos', 0)) / 1e9
            et = int(p.get('endTimeNanos', 0)) / 1e9
            vals = [v.get('fpVal') for v in p.get('value', [])]
            print(f'  {datetime.fromtimestamp(st, tz).strftime("%H:%M")} -> {datetime.fromtimestamp(et, tz).strftime("%H:%M")}: {vals}')

# Today so far
print()
s2 = tz.localize(datetime(today.year, today.month, today.day, 0, 0, 0))
e2 = s2 + timedelta(days=1)
r2 = _aggregate_calories(int(s2.timestamp()*1000), int(e2.timestamp()*1000))
print(f'Today ({today}): {_parse_calories(r2)} kcal')
for b in r2.get('bucket', []):
    for d in b.get('dataset', []):
        for p in d.get('point', []):
            st = int(p.get('startTimeNanos', 0)) / 1e9
            et = int(p.get('endTimeNanos', 0)) / 1e9
            vals = [v.get('fpVal') for v in p.get('value', [])]
            print(f'  {datetime.fromtimestamp(st, tz).strftime("%H:%M")} -> {datetime.fromtimestamp(et, tz).strftime("%H:%M")}: {vals}')