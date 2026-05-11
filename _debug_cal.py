import json, sys
from datetime import date, datetime, timedelta
from pytz import timezone

sys.path.insert(0, '/app')
from google_fitness import _aggregate_calories, _parse_calories, fetch_daily_calories_for_date, get_access_token
import requests

tz = timezone('Europe/Moscow')
today = datetime.now(tz).date()
now = datetime.now(tz)

print(f'Today: {today}')
print(f'Now: {now}')

# Test 1: fetch_daily_calories_for_date
print('\n=== fetch_daily_calories_for_date(today) ===')
try:
    result = fetch_daily_calories_for_date(today, 'Europe/Moscow')
    print(f'Result: {result} kcal')
except Exception as e:
    print(f'Error: {e}')

# Test 2: Raw API for today
print('\n=== Raw API: today full day ===')
start = tz.localize(datetime(today.year, today.month, today.day, 0, 0, 0))
end = start + timedelta(days=1)
start_ms = int(start.timestamp() * 1000)
end_ms = int(end.timestamp() * 1000)
print(f'Range: {start} -> {end}')
try:
    resp = _aggregate_calories(start_ms, end_ms)
    kcal = _parse_calories(resp)
    print(f'Parsed: {kcal} kcal')
    for bucket in resp.get('bucket', []):
        for dataset in bucket.get('dataset', []):
            ds_id = dataset.get('dataSourceId', '?')
            print(f'  DataSource: {ds_id}')
            for point in dataset.get('point', []):
                st = datetime.fromtimestamp(int(point.get('startTimeNanos', 0)) / 1e9, tz).strftime('%H:%M')
                et = datetime.fromtimestamp(int(point.get('endTimeNanos', 0)) / 1e9, tz).strftime('%H:%M')
                for v in point.get('value', []):
                    print(f'    {st}-{et}: fpVal={v.get("fpVal")}, intVal={v.get("intVal")}')
except Exception as e:
    print(f'Error: {e}')

# Test 3: BMR calories
print('\n=== BMR calories ===')
try:
    token = get_access_token()
    headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
    body = {
        'aggregateBy': [{'dataTypeName': 'com.google.calories.bmr'}],
        'bucketByTime': {'durationMillis': 86400000},
        'startTimeMillis': start_ms,
        'endTimeMillis': end_ms,
    }
    resp_bmr = requests.post('https://www.googleapis.com/fitness/v1/users/me/dataset:aggregate', headers=headers, json=body, timeout=20)
    bmr_data = resp_bmr.json()
    bmr_total = 0.0
    for bucket in bmr_data.get('bucket', []):
        for dataset in bucket.get('dataset', []):
            for point in dataset.get('point', []):
                for v in point.get('value', []):
                    bmr_total += float(v.get('fpVal', v.get('intVal', 0)) or 0)
    print(f'BMR calories today: {bmr_total}')
    print(f'Active (expended): {kcal}')
    print(f'Total (active + BMR): {kcal + bmr_total}')
except Exception as e:
    print(f'Error: {e}')

# Test 4: What Google Fit app shows as "Calories" 
# The app typically shows: active calories (from activities) + BMR
# But the "expended" data type should already include BMR
# Let's check with a session query
print('\n=== Sessions today ===')
try:
    token = get_access_token()
    headers = {'Authorization': 'Bearer ' + token}
    sessions_url = 'https://www.googleapis.com/fitness/v1/users/me/sessions'
    params = {
        'startTime': start.isoformat() + 'T00:00:00.000Z',
        'endTime': end.isoformat() + 'T00:00:00.000Z',
    }
    resp_sessions = requests.get(sessions_url, headers=headers, params=params, timeout=20)
    sessions_data = resp_sessions.json()
    for s in sessions_data.get('session', []):
        name = s.get('name', s.get('activityType', '?'))
        st = s.get('startTimeMillis', '?')
        et = s.get('endTimeMillis', '?')
        print(f'  Session: {name} ({st} -> {et})')
except Exception as e:
    print(f'Error: {e}')