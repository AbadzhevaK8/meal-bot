from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from pytz import timezone

from sheets import get_health_connect_calories_for_date, upsert_fitness_data


DEFAULT_SOURCE = "Health Connect"
GARMIN_SOURCE = "Garmin Connect via Health Connect"
MIN_GARMIN_COMPLETED_DAY_KCAL = 1700.0


def _parse_recorded_at(recorded_at: str, tz_name: str) -> datetime | None:
    value = recorded_at.strip()
    if not value:
        return None
    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    tz = timezone(tz_name)
    if parsed.tzinfo is None:
        return tz.localize(parsed)
    return parsed.astimezone(tz)


def _is_completed_day(date_value: date, recorded_at: str, tz_name: str) -> bool:
    parsed_recorded_at = _parse_recorded_at(recorded_at, tz_name)
    if parsed_recorded_at is None:
        return False
    tz = timezone(tz_name)
    day_end = tz.localize(datetime.combine(date_value + timedelta(days=1), time.min))
    return parsed_recorded_at >= day_end


def parse_health_connect_payload(payload: dict[str, Any], tz_name: str) -> dict[str, Any]:
    date_text = str(payload.get("date") or "").strip()
    if date_text:
        date_value = date.fromisoformat(date_text)
    else:
        date_value = datetime.now(timezone(tz_name)).date()

    total_kcal = payload.get("total_kcal", payload.get("kcal"))
    if total_kcal is None:
        raise ValueError("Missing total_kcal")

    total = float(total_kcal)
    active = payload.get("active_kcal")
    basal = payload.get("basal_kcal")
    source = str(payload.get("source") or DEFAULT_SOURCE).strip()
    recorded_at = str(payload.get("recorded_at") or "").strip()

    note_parts = [source]
    if active is not None:
        note_parts.append(f"active={float(active):.1f}")
    if basal is not None:
        note_parts.append(f"basal={float(basal):.1f}")
    if recorded_at:
        note_parts.append(f"recorded_at={recorded_at}")

    return {
        "date": date_value,
        "total_kcal": total,
        "note": "; ".join(note_parts),
    }


def ingest_health_connect_calories(
    payload: dict[str, Any],
    tz_name: str,
    default_source: str = DEFAULT_SOURCE,
) -> dict[str, Any]:
    payload = dict(payload)
    payload.setdefault("source", default_source)
    parsed = parse_health_connect_payload(payload, tz_name)
    date_value = parsed["date"]
    source = str(payload.get("source") or default_source).strip()
    recorded_at = str(payload.get("recorded_at") or "").strip()
    total_kcal = float(parsed["total_kcal"] or 0)
    if (
        source == GARMIN_SOURCE
        and _is_completed_day(date_value, recorded_at, tz_name)
        and 0 < total_kcal < MIN_GARMIN_COMPLETED_DAY_KCAL
    ):
        raise ValueError(
            f"Suspicious Garmin completed-day calories for {date_value.isoformat()}: {total_kcal:.1f}"
        )
    upsert_fitness_data(
        {
            "description": f"Health Connect total calories for {date_value.isoformat()}",
            "kcal": round(parsed["total_kcal"], 1),
            "note": parsed["note"],
        },
        tz=tz_name,
    )
    return parsed


def fetch_health_connect_calories_for_date(date_value: date) -> tuple[float, str] | None:
    return get_health_connect_calories_for_date(date_value)
