import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from pytz import timezone

from config import load_config
from sheets import upsert_fitness_data

logger = logging.getLogger(__name__)

GARMIN_CLOUD_PREFIX = "Garmin Connect cloud daily calories for "
GARMIN_CLOUD_SOURCE = "Garmin Connect cloud"


class GarminConnectNotConfigured(RuntimeError):
    pass


class GarminConnectNoCalories(RuntimeError):
    pass


def _resolve_tokenstore(path_text: str) -> str:
    path = Path(path_text)
    if not path.is_absolute():
        path = Path(__file__).parent / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def _debug_dump_summary(date_value: date, summary: dict[str, Any]) -> None:
    config = load_config()
    if not config.GARMIN_CONNECT_DEBUG_DUMP:
        return
    path = Path(__file__).parent / "data" / f"garmin_summary_{date_value.isoformat()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def _summary_number(summary: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = summary.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def extract_total_kilocalories(summary: dict[str, Any]) -> float:
    total = _summary_number(
        summary,
        "totalKilocalories",
        "total_kilocalories",
        "totalCalories",
        "total_calories",
    )
    if total is not None and total > 0:
        return total

    active = _summary_number(summary, "activeKilocalories", "active_kilocalories", "activeCalories")
    bmr = _summary_number(summary, "bmrKilocalories", "bmr_kilocalories", "bmrCalories")
    wellness = _summary_number(summary, "wellnessKilocalories", "wellness_kilocalories")
    if active is not None and bmr is not None and active + bmr > 0:
        return active + bmr
    if wellness is not None and wellness > 0:
        return wellness

    raise GarminConnectNoCalories(
        "Garmin daily summary did not include totalKilocalories/active+bmr calories"
    )


def fetch_garmin_cloud_calories_for_date(date_value: date) -> tuple[float, str]:
    config = load_config()
    if not config.GARMIN_CONNECT_EMAIL or not config.GARMIN_CONNECT_PASSWORD:
        raise GarminConnectNotConfigured("GARMIN_CONNECT_EMAIL/PASSWORD are not configured")

    try:
        from garminconnect import Garmin
    except ImportError as e:
        raise GarminConnectNotConfigured("garminconnect package is not installed") from e

    client = Garmin(config.GARMIN_CONNECT_EMAIL, config.GARMIN_CONNECT_PASSWORD)
    tokenstore = _resolve_tokenstore(config.GARMIN_CONNECT_TOKENSTORE)
    client.login(tokenstore=tokenstore)
    summary = client.get_stats(date_value.isoformat())
    if not isinstance(summary, dict):
        raise GarminConnectNoCalories("Garmin daily summary response is not an object")

    _debug_dump_summary(date_value, summary)
    total = extract_total_kilocalories(summary)
    note = (
        f"{GARMIN_CLOUD_SOURCE}; pulled_at="
        f"{datetime.now(timezone(config.TIMEZONE)).isoformat()}"
    )
    return round(total, 1), note


def sync_garmin_cloud_calories_for_date(date_value: date) -> tuple[float, str]:
    total, note = fetch_garmin_cloud_calories_for_date(date_value)
    upsert_fitness_data(
        {
            "description": f"{GARMIN_CLOUD_PREFIX}{date_value.isoformat()}",
            "kcal": total,
            "note": note,
        }
    )
    return total, note


def sync_garmin_cloud_calories_range(start_date: date, end_date: date) -> dict[str, float]:
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    results: dict[str, float] = {}
    current = start_date
    while current <= end_date:
        total, _ = sync_garmin_cloud_calories_for_date(current)
        results[current.isoformat()] = total
        current += timedelta(days=1)
    return results
