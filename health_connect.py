from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pytz import timezone

from sheets import get_health_connect_calories_for_date, upsert_fitness_data


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
    source = str(payload.get("source") or "Health Connect").strip()
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


def ingest_health_connect_calories(payload: dict[str, Any], tz_name: str) -> dict[str, Any]:
    parsed = parse_health_connect_payload(payload, tz_name)
    date_value = parsed["date"]
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

