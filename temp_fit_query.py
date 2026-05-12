import json
import sys
from datetime import datetime, date, timedelta
from pytz import timezone
import requests

sys.path.insert(0, '.')
from google_fitness import get_access_token, _aggregate_calories, _parse_calories, _sum_calories_for_types

req_date = date(2026, 5, 11)
tz = timezone('Europe/Moscow')
start = tz.localize(datetime(req_date.year, req_date.month, req_date.day, 0, 0, 0))
end = start + timedelta(days=1)
start_ms = int(start.timestamp() * 1000)
end_ms = int(end.timestamp() * 1000)

print('Date:', req_date)
print('Start:', start.isoformat(), 'End:', end.isoformat())
print('Start_ms:', start_ms)
print('End_ms:', end_ms)
print('---')

for dtype in ['com.google.calories.expended', 'com.google.calories.bmr']:
    try:
        resp = _aggregate_calories(start_ms, end_ms, dtype)
        kcal = _parse_calories(resp)
        print(f'{dtype}: {kcal}')
    except Exception as e:
        print(f'{dtype} request failed:', type(e).__name__, e)
        if hasattr(e, 'response') and e.response is not None:
            try:
                print('Response body:', json.dumps(e.response.json(), indent=2, ensure_ascii=False))
            except Exception:
                print('Response text:', e.response.text)

print('--- direct request for com.google.calories.bmr ---')
access_token = get_access_token()
headers = {'Authorization': 'Bearer ' + access_token, 'Content-Type': 'application/json'}
body = {
    'aggregateBy': [{'dataTypeName': 'com.google.calories.bmr'}],
    'bucketByTime': {'durationMillis': end_ms - start_ms},
    'startTimeMillis': start_ms,
    'endTimeMillis': end_ms,
}
resp = requests.post('https://www.googleapis.com/fitness/v1/users/me/dataset:aggregate', headers=headers, json=body, timeout=30)
print('direct response status:', resp.status_code)
try:
    print('direct response json:', json.dumps(resp.json(), indent=2, ensure_ascii=False))
except Exception:
    print('direct response text:', resp.text)

try:
    combined = _sum_calories_for_types(start_ms, end_ms, ['com.google.calories.expended', 'com.google.calories.bmr'])
    print('sum active+BMR:', combined)
except Exception as e:
    print('sum active+BMR failed:', type(e).__name__, e)
