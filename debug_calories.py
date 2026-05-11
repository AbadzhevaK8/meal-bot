#!/usr/bin/env python3
"""Debug script to check Google Fitness calorie data."""
import json
import sys
from datetime import date, datetime, timedelta
from pytz import timezone

sys.path.insert(0, '/app')
from config import load_config
from google_fitness import (
    fetch_daily_calories_for_date,
    fetch_daily_calories,
    _aggregate_calories,
    _parse_calories,
    get_access_token,
)

tz = timezone('Europe/Moscow')
today = datetime.now(tz).date()

print(f"Today: {today}")
print(f"Timezone: {tz}")

# Test 1: fetch_daily_calories_for_date
print("\n=== Test 1: fetch_daily_calories_for_date ===")
try:
    result = fetch_daily_calories_for_date(today, 'Europe/Moscow')
    print(f"Result: {result} kcal")
except Exception as e:
    print(f"Error: {e}")

# Test 2: fetch_daily_calories (yesterday's data logged to fitness sheet)
print("\n=== Test 2: fetch_daily_calories (yesterday) ===")
try:
    result = fetch_daily_calories('Europe/Moscow')
    print(f"Result: {result} kcal")
except Exception as e:
    print(f"Error: {e}")

# Test 3: Raw API response for today
print("\n=== Test 3: Raw API response for today ===")
try:
    start = tz.localize(datetime(today.year, today.month, today.day, 0, 0, 0))
    end = start + timedelta(days=1)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    print(f"Start: {start} ({start_ms} ms)")
    print(f"End: {end} ({end_ms} ms)")
    
    response = _aggregate_calories(start_ms, end_ms)
    raw_kcal = _parse_calories(response)
    print(f"Parsed calories: {raw_kcal}")
    print(f"\nFull response:")
    print(json.dumps(response, indent=2))
except Exception as e:
    print(f"Error: {e}")

# Test 4: Check what data type values are
print("\n=== Test 4: Value types in response ===")
try:
    response = _aggregate_calories(start_ms, end_ms)
    for bucket in response.get("bucket", []):
        for dataset in bucket.get("dataset", []):
            for point in dataset.get("point", []):
                for value in point.get("value", []):
                    print(f"  fpVal={value.get('fpVal')} (type: {type(value.get('fpVal')).__name__}), intVal={value.get('intVal')} (type: {type(value.get('intVal')).__name__})")
except Exception as e:
    print(f"Error: {e}")