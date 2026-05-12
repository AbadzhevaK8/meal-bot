import json
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import requests
from requests.exceptions import HTTPError
from pytz import timezone

from config import load_config
from sheets import log_fitness_data

logger = logging.getLogger(__name__)

TOKEN_FILE = Path(__file__).parent / "google_fitness_tokens.json"
TOKEN_URL = "https://oauth2.googleapis.com/token"
FITNESS_AGGREGATE_URL = "https://www.googleapis.com/fitness/v1/users/me/dataset:aggregate"

SCOPES = [
    "https://www.googleapis.com/auth/fitness.activity.read",
    "https://www.googleapis.com/auth/fitness.body.read",
    "https://www.googleapis.com/auth/fitness.nutrition.read",
]


@dataclass
class FitnessTokens:
    access_token: str
    refresh_token: str
    expires_at: int
    scope: str | None = None
    token_type: str | None = None


def _load_tokens() -> dict[str, Any] | None:
    if not TOKEN_FILE.exists():
        return None
    try:
        with TOKEN_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.exception("Не удалось загрузить Google Fitness токены: %s", e)
        return None


def _save_tokens(tokens: dict[str, Any]) -> None:
    data = dict(tokens)
    if "expires_in" in data:
        expires_in = int(data.pop("expires_in"))
        data["expires_at"] = int(time.time()) + expires_in
    if "refresh_token" in data and not data["refresh_token"]:
        data.pop("refresh_token")
    try:
        with TOKEN_FILE.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            logger.info("Google Fitness токены сохранены в %s", TOKEN_FILE)
    except Exception as e:
        logger.exception("Не удалось сохранить Google Fitness токены: %s", e)


def get_saved_refresh_token() -> str | None:
    tokens = _load_tokens()
    return tokens.get("refresh_token") if tokens else None


def has_saved_tokens() -> bool:
    tokens = _load_tokens()
    return bool(tokens and tokens.get("refresh_token"))


def build_authorization_url(redirect_uri: str, client_id: str) -> str:
    scope_value = requests.utils.quote(" ".join(SCOPES), safe="")
    return (
        "https://accounts.google.com/o/oauth2/v2/auth?"
        f"scope={scope_value}&"
        "access_type=offline&"
        "include_granted_scopes=true&"
        "prompt=consent&"
        "response_type=code&"
        f"client_id={client_id}&"
        f"redirect_uri={redirect_uri}"
    )


def exchange_code_for_tokens(code: str) -> dict[str, Any]:
    config = load_config()
    data = {
        "code": code,
        "client_id": config.GOOGLE_OAUTH_CLIENT_ID,
        "client_secret": config.GOOGLE_OAUTH_CLIENT_SECRET,
        "redirect_uri": config.GOOGLE_REDIRECT_URI,
        "grant_type": "authorization_code",
    }
    resp = requests.post(TOKEN_URL, data=data, timeout=15)
    resp.raise_for_status()
    tokens = resp.json()
    if "refresh_token" not in tokens:
        raise ValueError("Google OAuth did not return a refresh_token. Authorize again.")
    _save_tokens(tokens)
    return tokens


def refresh_access_token() -> dict[str, Any]:
    config = load_config()
    tokens = _load_tokens()
    if not tokens or not tokens.get("refresh_token"):
        raise ValueError("No refresh token available for Google Fitness")

    data = {
        "client_id": config.GOOGLE_OAUTH_CLIENT_ID,
        "client_secret": config.GOOGLE_OAUTH_CLIENT_SECRET,
        "refresh_token": tokens["refresh_token"],
        "grant_type": "refresh_token",
    }
    resp = requests.post(TOKEN_URL, data=data, timeout=15)
    resp.raise_for_status()
    new_tokens = resp.json()
    if "refresh_token" not in new_tokens:
        new_tokens["refresh_token"] = tokens["refresh_token"]
    _save_tokens(new_tokens)
    return new_tokens


def get_access_token() -> str:
    tokens = _load_tokens()
    if not tokens:
        raise ValueError("Google Fitness tokens are not configured")

    expires_at = int(tokens.get("expires_at", 0))
    if time.time() >= expires_at - 60:
        tokens = refresh_access_token()
    return tokens["access_token"]


def _aggregate_calories(start_ms: int, end_ms: int, data_type_name: str = "com.google.calories.expended") -> dict[str, Any]:
    access_token = get_access_token()
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    body = {
        "aggregateBy": [{"dataTypeName": data_type_name}],
        "bucketByTime": {"durationMillis": end_ms - start_ms},
        "startTimeMillis": start_ms,
        "endTimeMillis": end_ms,
    }
    resp = requests.post(FITNESS_AGGREGATE_URL, headers=headers, json=body, timeout=20)
    resp.raise_for_status()
    return resp.json()


def _sum_calories_for_types(start_ms: int, end_ms: int, data_types: list[str]) -> float:
    total = 0.0
    for data_type in data_types:
        try:
            response = _aggregate_calories(start_ms, end_ms, data_type)
        except HTTPError as exc:
            if exc.response is not None:
                try:
                    error_json = exc.response.json()
                except Exception:
                    error_json = {}
                message = error_json.get("error", {}).get("message", "")
                if "no default datasource found for" in message:
                    logger.warning(
                        "Нет стандартного источника данных для %s, используем доступные калории expended.",
                        data_type,
                    )
                    continue
            raise
        total += _parse_calories(response)
    return total


def _parse_calories(response: dict[str, Any]) -> float:
    total = 0.0
    for bucket in response.get("bucket", []):
        for dataset in bucket.get("dataset", []):
            for point in dataset.get("point", []):
                for value in point.get("value", []):
                    total += float(value.get("fpVal", value.get("intVal", 0)) or 0)
    return total


def fetch_daily_calories_for_date(date_value: datetime.date, tz_name: str = "Europe/Moscow") -> float:
    try:
        tz = timezone(tz_name)
    except Exception:
        tz = timezone("UTC")
    start = tz.localize(datetime(date_value.year, date_value.month, date_value.day, 0, 0, 0))
    end = start + timedelta(days=1)
    return _sum_calories_for_types(
        int(start.timestamp() * 1000),
        int(end.timestamp() * 1000),
        ["com.google.calories.expended", "com.google.calories.bmr"],
    )


def fetch_calories_since_midnight(tz_name: str = "Europe/Moscow") -> float:
    try:
        tz = timezone(tz_name)
    except Exception:
        tz = timezone("UTC")
    now = datetime.now(tz)
    start = tz.localize(datetime(now.year, now.month, now.day, 0, 0, 0))
    return _sum_calories_for_types(
        int(start.timestamp() * 1000),
        int(now.timestamp() * 1000),
        ["com.google.calories.expended", "com.google.calories.bmr"],
    )


def fetch_calories_range(start_date: datetime.date, end_date: datetime.date, tz_name: str = "Europe/Moscow") -> dict[str, float]:
    """Fetch per-day calories between start_date and end_date inclusive."""
    results: dict[str, float] = {}
    current = start_date
    while current <= end_date:
        results[current.isoformat()] = fetch_daily_calories_for_date(current, tz_name)
        current += timedelta(days=1)
    return results


def sync_fitness_rows(start_date: datetime.date, end_date: datetime.date, tz_name: str = "Europe/Moscow") -> dict[str, float]:
    """Sync fitness sheet rows for each day in the range using upsert logic."""
    from sheets import upsert_fitness_data

    results = fetch_calories_range(start_date, end_date, tz_name)
    for date_str, kcal in results.items():
        start_day = datetime.fromisoformat(date_str)
        description = (
            f"Calories expended (+BMR if available) from {date_str} to {(start_day + timedelta(days=1)).strftime('%Y-%m-%d')}"
        )
        upsert_fitness_data({
            "description": description,
            "kcal": round(kcal, 1),
            "note": f"Auto sync {start_date.isoformat()}..{end_date.isoformat()}",
        }, tz=tz_name)
    return results


def fetch_daily_calories(tz_name: str = "Europe/Moscow") -> float | None:
    config = load_config()
    try:
        tz = timezone(tz_name)
    except Exception:
        tz = timezone("UTC")
    now = datetime.now(tz)
    today_start = tz.localize(datetime(now.year, now.month, now.day, 0, 0, 0))
    yesterday_start = today_start - timedelta(days=1)
    start_ms = int(yesterday_start.timestamp() * 1000)
    end_ms = int(today_start.timestamp() * 1000)

    total_kcal = _sum_calories_for_types(
        start_ms,
        end_ms,
        ["com.google.calories.expended", "com.google.calories.bmr"],
    )
    description = f"Calories expended (+BMR if available) from {yesterday_start.strftime('%Y-%m-%d')} to {today_start.strftime('%Y-%m-%d')}"
    log_fitness_data({
        "timestamp": today_start.strftime("%Y-%m-%d %H:%M"),
        "description": description,
        "kcal": round(total_kcal, 1),
    })
    return total_kcal


def get_tokens_status() -> str:
    tokens = _load_tokens()
    if not tokens:
        return "Google Fitness токены не настроены. Выполните авторизацию."
    if not tokens.get("refresh_token"):
        return "Refresh token отсутствует. Перепроходите авторизацию."
    expires_at = tokens.get("expires_at")
    if not expires_at:
        return "Токен настроен, но информация о сроке действия отсутствует."
    expires = datetime.fromtimestamp(int(expires_at), dt_timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"Авторизация настроена. access_token истекает: {expires}."