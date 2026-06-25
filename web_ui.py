import asyncio
import hashlib
import hmac
import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web
from pytz import timezone

from config import load_config
from garmin_connect import GarminConnectNotConfigured, sync_garmin_cloud_calories_for_date
from google_fitness import GoogleFitnessAuthError, fetch_calories_since_day_start
from health_connect import fetch_health_connect_calories_for_date
from sheets import (
    DAY_START_HOUR,
    MANUAL_EXPENDITURE_PREFIX,
    delete_meal,
    get_cheatmeal_days_for_range,
    get_daily_calorie_summaries_for_range,
    get_current_food_day,
    get_meal_logs_for_range,
    get_saved_fitness_calories_for_range,
    set_cheatmeal_day,
    upsert_fitness_data,
    update_meal,
)

STATIC_DIR = Path(__file__).resolve().parent / "web"
LIVE_TODAY_TIMEOUT_SECONDS = 15.0
API_MEALS_CACHE_TTL_SECONDS = 20.0
GARMIN_PAGE_SYNC_RETRY_SECONDS = 15 * 60
GARMIN_PAGE_SYNC_MAX_DAYS = 10
logger = logging.getLogger(__name__)
_api_meals_cache: dict[tuple[int, str, str], tuple[float, dict]] = {}
_garmin_page_sync_attempts: dict[str, float] = {}


def setup_web_ui(app: web.Application, config) -> None:
    app["web_ui_config"] = config
    app.add_routes([
        web.get("/", redirect_to_ui),
        web.get("/ui", index),
        web.get("/ui/", index),
        web.static("/ui/assets", STATIC_DIR),
        web.get("/api/meals", api_meals),
        web.patch("/api/day-flags/{date}", api_update_day_flag),
        web.patch("/api/day-expenditure/{date}", api_update_day_expenditure),
        web.patch("/api/meals/{row_id}", api_update_meal),
        web.delete("/api/meals/{row_id}", api_delete_meal),
    ])


async def redirect_to_ui(request: web.Request) -> web.Response:
    raise web.HTTPFound("/ui")


async def index(request: web.Request) -> web.Response:
    response = web.FileResponse(STATIC_DIR / "index.html")
    response.headers["Cache-Control"] = "no-store"
    return response


def _expected_token(request: web.Request) -> str | None:
    config = request.app["web_ui_config"]
    return config.WEB_UI_TOKEN or config.ACCESS_PASSWORD


def _telegram_user_id(request: web.Request, payload: dict | None = None) -> int | None:
    config = request.app["web_ui_config"]
    init_data = request.headers.get("X-Telegram-Init-Data", "").strip()
    if not init_data and payload:
        init_data = str(payload.get("init_data", "")).strip()
    if not init_data:
        init_data = request.query.get("init_data", "").strip()
    if not init_data:
        return None

    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = parsed.pop("hash", "")
    if not received_hash:
        return None

    data_check_string = "\n".join(f"{key}={parsed[key]}" for key in sorted(parsed))
    secret = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated_hash, received_hash):
        return None

    try:
        user = json.loads(parsed.get("user", "{}"))
        return int(user["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _signed_launch_user_id(request: web.Request, payload: dict | None = None) -> int | None:
    config = request.app["web_ui_config"]
    uid = request.query.get("uid")
    sig = request.query.get("sig")
    if payload:
        uid = payload.get("uid", uid)
        sig = payload.get("sig", sig)
    if not uid or not sig:
        return None

    message = f"mealbot-miniapp:{uid}".encode()
    expected = hmac.new(config.BOT_TOKEN.encode(), message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, str(sig)):
        return None

    try:
        return int(uid)
    except ValueError:
        return None


def _is_token_authorized(request: web.Request) -> bool:
    expected = _expected_token(request)
    if not expected:
        return False

    auth = request.headers.get("Authorization", "")
    token = ""
    if auth.startswith("Bearer "):
        token = auth.removeprefix("Bearer ").strip()
    token = token or request.headers.get("X-Meal-Token", "").strip()
    token = token or request.query.get("token", "").strip()
    return token == expected


def _request_user_id(request: web.Request, payload: dict | None = None) -> int:
    telegram_user_id = _telegram_user_id(request, payload)
    if telegram_user_id is not None:
        return telegram_user_id

    signed_user_id = _signed_launch_user_id(request, payload)
    if signed_user_id is not None:
        return signed_user_id

    if not _is_token_authorized(request):
        raise web.HTTPUnauthorized(text="Unauthorized")

    value = request.query.get("user_id")
    if payload:
        value = payload.get("user_id", value)
    if not value:
        raise web.HTTPBadRequest(text="Missing user_id")
    return int(value)


def _parse_date(value: str | None, fallback: date) -> date:
    if not value:
        return fallback
    return date.fromisoformat(value)


def _truthy_query_flag(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _json_response_no_store(payload: dict) -> web.Response:
    response = web.json_response(payload)
    response.headers["Cache-Control"] = "no-store"
    return response


def _float_value(value) -> float:
    try:
        return float(str(value or 0).replace(",", "."))
    except ValueError:
        return 0.0


def _food_day(timestamp: str, tz: str) -> str:
    parsed = datetime.strptime(timestamp, "%Y-%m-%d %H:%M")
    localized = timezone(tz).localize(parsed)
    if localized.hour < DAY_START_HOUR:
        localized -= timedelta(days=1)
    return localized.date().isoformat()


def _meal_payload(record: dict, tz: str) -> dict:
    timestamp = record.get("timestamp", "")
    food_day = ""
    try:
        food_day = _food_day(timestamp, tz)
    except ValueError:
        pass

    return {
        "row_id": int(record.get("row_id", 0) or 0),
        "timestamp": timestamp,
        "food_day": food_day,
        "user_id": record.get("user_id", ""),
        "name": record.get("name", ""),
        "weight_g": _float_value(record.get("weight_g")),
        "kcal": _float_value(record.get("kcal")),
        "protein_g": _float_value(record.get("protein_g")),
        "fat_g": _float_value(record.get("fat_g")),
        "carbs_g": _float_value(record.get("carbs_g")),
        "confidence": record.get("confidence", ""),
        "note": record.get("note", ""),
    }


def _summary(records: list[dict]) -> dict:
    totals = {
        "kcal": sum(_float_value(r.get("kcal")) for r in records),
        "protein_g": sum(_float_value(r.get("protein_g")) for r in records),
        "fat_g": sum(_float_value(r.get("fat_g")) for r in records),
        "carbs_g": sum(_float_value(r.get("carbs_g")) for r in records),
    }
    return {key: round(value, 1) for key, value in totals.items()}


def _merge_day_balance(day: dict, saved_summary: dict | None, is_cheatmeal: bool = False) -> dict:
    intake = round(day["totals"].get("kcal", 0), 1)
    expenditure = None
    difference = None
    note = ""

    if saved_summary:
        expenditure = saved_summary.get("expenditure_kcal")
        if expenditure is not None:
            expenditure = round(_float_value(expenditure), 1)
            difference = round(intake - expenditure, 1)
        note = saved_summary.get("note", "")

    if is_cheatmeal:
        intake = 0.0
        difference = round(0.0 - expenditure, 1) if expenditure is not None else None
        note = "; ".join(part for part in (note, "читмил: приход не учитывается") if part)

    day["is_cheatmeal"] = is_cheatmeal
    day["balance"] = {
        "intake_kcal": intake,
        "expenditure_kcal": expenditure,
        "difference_kcal": difference,
        "note": note,
    }
    return day


def _merge_days_with_balances(
    by_day: dict[str, dict],
    saved_summaries: dict[str, dict],
    cheatmeal_days: set[str] | None = None,
) -> list[dict]:
    cheatmeal_days = cheatmeal_days or set()
    return [
        _merge_day_balance(day, saved_summaries.get(day_date_key), day_date_key in cheatmeal_days)
        for day_date_key, day in by_day.items()
    ]


def _apply_live_summary(
    saved_summaries: dict[str, dict],
    current_food_day: date,
    live_summary: dict | None,
) -> None:
    if live_summary is not None:
        saved_summaries[current_food_day.isoformat()] = live_summary


def _completed_dates_missing_expenditure(
    by_day: dict[str, dict],
    saved_summaries: dict[str, dict],
    current_food_day: date,
) -> list[date]:
    missing: list[date] = []
    now_monotonic = time.monotonic()
    for date_key in sorted(by_day):
        try:
            day = date.fromisoformat(date_key)
        except ValueError:
            continue
        if day >= current_food_day:
            continue
        if saved_summaries.get(date_key, {}).get("expenditure_kcal") is not None:
            continue
        last_attempt = _garmin_page_sync_attempts.get(date_key, 0)
        if now_monotonic - last_attempt < GARMIN_PAGE_SYNC_RETRY_SECONDS:
            continue
        missing.append(day)
    return missing[:GARMIN_PAGE_SYNC_MAX_DAYS]


def _sync_garmin_for_completed_dates(dates: list[date]) -> dict[str, float]:
    if not dates:
        return {}

    synced: dict[str, float] = {}
    for day in dates:
        date_key = day.isoformat()
        _garmin_page_sync_attempts[date_key] = time.monotonic()
        try:
            total, note = sync_garmin_cloud_calories_for_date(day)
        except GarminConnectNotConfigured as e:
            logger.warning("Garmin Connect cloud is not configured for Mini App sync: %s", e)
            break
        except Exception as e:
            logger.warning("Mini App Garmin cloud sync failed for %s: %s", date_key, e)
            continue
        synced[date_key] = total
        logger.info("Mini App Garmin cloud synced for %s: %.1f (%s)", date_key, total, note)
    return synced


def _manual_expenditure_summary_for_date(food_day: date) -> dict | None:
    date_key = food_day.isoformat()
    fitness_summaries = get_saved_fitness_calories_for_range(food_day, food_day)
    summary = fitness_summaries.get(date_key)
    if not summary:
        return None

    note = str(summary.get("note", ""))
    if not note.startswith("Manual Mini App override"):
        return None

    return {
        "date": date_key,
        "timestamp": summary.get("timestamp", ""),
        "intake_kcal": 0,
        "expenditure_kcal": summary.get("expenditure_kcal"),
        "difference_kcal": 0,
        "note": note,
    }


def _live_current_food_day_summary(food_day: date, tz: str) -> dict | None:
    manual_summary = _manual_expenditure_summary_for_date(food_day)
    if manual_summary is not None:
        return manual_summary

    try:
        expenditure, note = sync_garmin_cloud_calories_for_date(food_day)
        return {
            "date": food_day.isoformat(),
            "timestamp": datetime.now(timezone(tz)).strftime("%Y-%m-%d %H:%M"),
            "intake_kcal": 0,
            "expenditure_kcal": round(float(expenditure or 0), 1),
            "difference_kcal": 0,
            "note": note,
        }
    except GarminConnectNotConfigured as e:
        logger.warning("Garmin Connect cloud is not configured for current day Mini App sync: %s", e)
    except Exception as e:
        logger.warning("Could not fetch current day Garmin Connect cloud calories for %s: %s", food_day, e)

    health_connect_calories = fetch_health_connect_calories_for_date(food_day)
    if health_connect_calories:
        expenditure, note = health_connect_calories
        return {
            "date": food_day.isoformat(),
            "timestamp": datetime.now(timezone(tz)).strftime("%Y-%m-%d %H:%M"),
            "intake_kcal": 0,
            "expenditure_kcal": round(float(expenditure or 0), 1),
            "difference_kcal": 0,
            "note": note,
        }

    if load_config().STRICT_EXPENDITURE_SOURCE:
        return {
            "date": food_day.isoformat(),
            "timestamp": datetime.now(timezone(tz)).strftime("%Y-%m-%d %H:%M"),
            "intake_kcal": 0,
            "expenditure_kcal": None,
            "difference_kcal": None,
            "note": "нет точного расхода Garmin",
        }

    try:
        expenditure = fetch_calories_since_day_start(tz, DAY_START_HOUR)
    except GoogleFitnessAuthError as e:
        logger.warning("Could not fetch live Google Fit calories for %s: %s", food_day, e)
        return {
            "date": food_day.isoformat(),
            "timestamp": datetime.now(timezone(tz)).strftime("%Y-%m-%d %H:%M"),
            "intake_kcal": 0,
            "expenditure_kcal": None,
            "difference_kcal": None,
            "note": "нужна авторизация Google Fit",
        }
    except Exception as e:
        logger.warning("Could not fetch live Google Fit calories for %s: %s", food_day, e)
        return {
            "date": food_day.isoformat(),
            "timestamp": datetime.now(timezone(tz)).strftime("%Y-%m-%d %H:%M"),
            "intake_kcal": 0,
            "expenditure_kcal": None,
            "difference_kcal": None,
            "note": "не удалось получить расход",
        }

    return {
        "date": food_day.isoformat(),
        "timestamp": datetime.now(timezone(tz)).strftime("%Y-%m-%d %H:%M"),
        "intake_kcal": 0,
        "expenditure_kcal": round(float(expenditure or 0), 1),
        "difference_kcal": 0,
        "note": f"Live Google Fit since {DAY_START_HOUR:02d}:00",
    }


async def _live_current_food_day_summary_with_timeout(food_day: date, tz: str) -> dict | None:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_live_current_food_day_summary, food_day, tz),
            timeout=LIVE_TODAY_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.warning(
            "Timed out loading live Google Fit calories for %s after %.1fs",
            food_day,
            LIVE_TODAY_TIMEOUT_SECONDS,
        )
        return None


async def api_meals(request: web.Request) -> web.Response:
    config = request.app["web_ui_config"]
    user_id = _request_user_id(request)
    live_sync_requested = _truthy_query_flag(request.query.get("live"))
    sync_missing_requested = _truthy_query_flag(request.query.get("sync_missing"))
    today = datetime.now(timezone(config.TIMEZONE)).date()
    current_food_day = get_current_food_day(config.TIMEZONE)
    start = _parse_date(request.query.get("from"), today.replace(day=1))
    end = _parse_date(request.query.get("to"), today)
    cache_key = (user_id, start.isoformat(), end.isoformat())
    cached = _api_meals_cache.get(cache_key)
    if not live_sync_requested and cached and time.monotonic() - cached[0] <= API_MEALS_CACHE_TTL_SECONDS:
        return _json_response_no_store(cached[1])

    records_task = asyncio.create_task(asyncio.to_thread(
        get_meal_logs_for_range,
        user_id,
        start,
        end,
        config.TIMEZONE,
    ))
    saved_summaries_task = asyncio.create_task(asyncio.to_thread(
        get_daily_calorie_summaries_for_range,
        user_id,
        start,
        end,
        config.TIMEZONE,
    ))
    cheatmeal_days_task = asyncio.create_task(asyncio.to_thread(
        get_cheatmeal_days_for_range,
        user_id,
        start,
        end,
    ))

    live_today_task = None
    if live_sync_requested and start <= current_food_day <= end:
        live_today_task = asyncio.create_task(
            _live_current_food_day_summary_with_timeout(current_food_day, config.TIMEZONE)
        )

    records, saved_summaries, cheatmeal_days = await asyncio.gather(
        records_task,
        saved_summaries_task,
        cheatmeal_days_task,
    )

    if live_today_task:
        live_today = await live_today_task
        _apply_live_summary(saved_summaries, current_food_day, live_today)

    meals = [_meal_payload(record, config.TIMEZONE) for record in records]
    by_day: dict[str, dict] = {}
    for meal in meals:
        key = meal["food_day"] or meal["timestamp"][:10]
        day = by_day.setdefault(key, {"date": key, "count": 0, "totals": {}})
        day["count"] += 1
        for field in ("kcal", "protein_g", "fat_g", "carbs_g"):
            day["totals"][field] = round(day["totals"].get(field, 0) + meal[field], 1)

    missing_garmin_dates = _completed_dates_missing_expenditure(
        by_day,
        saved_summaries,
        current_food_day,
    )
    garmin_synced = {}
    if sync_missing_requested:
        garmin_synced = await asyncio.to_thread(_sync_garmin_for_completed_dates, missing_garmin_dates)
    if garmin_synced:
        import aggregate_daily_calories

        synced_dates = [date.fromisoformat(value) for value in garmin_synced]
        await asyncio.to_thread(
            aggregate_daily_calories.main,
            min(synced_dates),
            max(synced_dates),
            False,
            str(user_id),
        )
        saved_summaries = await asyncio.to_thread(
            get_daily_calorie_summaries_for_range,
            user_id,
            start,
            end,
            config.TIMEZONE,
        )

    needs_fitness_fallback = any(
        saved_summaries.get(date_key, {}).get("expenditure_kcal") is None
        for date_key in by_day
    )
    if needs_fitness_fallback:
        fitness_summaries = await asyncio.to_thread(
            get_saved_fitness_calories_for_range,
            start,
            end,
            not config.STRICT_EXPENDITURE_SOURCE,
        )
        for date_key, fitness_summary in fitness_summaries.items():
            saved_summary = saved_summaries.get(date_key)
            if saved_summary and saved_summary.get("expenditure_kcal") is not None:
                continue
            saved_summaries[date_key] = {
                "date": date_key,
                "timestamp": saved_summary.get("timestamp", "") if saved_summary else "",
                "intake_kcal": saved_summary.get("intake_kcal", 0) if saved_summary else 0,
                "expenditure_kcal": fitness_summary["expenditure_kcal"],
                "difference_kcal": saved_summary.get("difference_kcal", 0) if saved_summary else 0,
                "note": fitness_summary.get("note", ""),
            }

    for date_key, saved_summary in saved_summaries.items():
        by_day.setdefault(date_key, {"date": date_key, "count": 0, "totals": {}})
    for date_key in cheatmeal_days:
        by_day.setdefault(date_key, {"date": date_key, "count": 0, "totals": {}})

    days = _merge_days_with_balances(by_day, saved_summaries, cheatmeal_days)

    payload = {
        "current_food_day": current_food_day.isoformat(),
        "meals": meals,
        "summary": _summary([meal for meal in meals if meal.get("food_day") not in cheatmeal_days]),
        "days": sorted(days, key=lambda item: item["date"]),
    }
    _api_meals_cache[cache_key] = (time.monotonic(), payload)
    return _json_response_no_store(payload)


async def api_update_day_flag(request: web.Request) -> web.Response:
    config = request.app["web_ui_config"]
    target_date = _parse_date(request.match_info["date"], datetime.now(timezone(config.TIMEZONE)).date())
    payload = json.loads(await request.text())
    user_id = _request_user_id(request, payload)
    is_cheatmeal = bool(payload.get("is_cheatmeal"))

    flag = await asyncio.to_thread(
        set_cheatmeal_day,
        user_id,
        target_date,
        is_cheatmeal,
        config.TIMEZONE,
        "Mini App",
    )
    _api_meals_cache.clear()
    return web.json_response({"day_flag": flag})


async def api_update_day_expenditure(request: web.Request) -> web.Response:
    config = request.app["web_ui_config"]
    target_date = _parse_date(request.match_info["date"], datetime.now(timezone(config.TIMEZONE)).date())
    payload = json.loads(await request.text())
    user_id = _request_user_id(request, payload)
    expenditure = _float_value(payload.get("expenditure_kcal"))
    if expenditure <= 0:
        raise web.HTTPBadRequest(text="expenditure_kcal must be positive")

    await asyncio.to_thread(
        upsert_fitness_data,
        {
            "description": f"{MANUAL_EXPENDITURE_PREFIX}{target_date.isoformat()}",
            "kcal": round(expenditure, 1),
            "note": "Manual Mini App override; recorded_at=manual-miniapp",
        },
        config.TIMEZONE,
    )

    import aggregate_daily_calories

    await asyncio.to_thread(
        aggregate_daily_calories.main,
        target_date,
        target_date,
        False,
        str(user_id),
    )
    _api_meals_cache.clear()
    return web.json_response({
        "ok": True,
        "date": target_date.isoformat(),
        "expenditure_kcal": round(expenditure, 1),
    })


async def api_update_meal(request: web.Request) -> web.Response:
    config = request.app["web_ui_config"]
    row_id = int(request.match_info["row_id"])
    payload = json.loads(await request.text())
    user_id = _request_user_id(request, payload)

    record = await asyncio.to_thread(
        update_meal,
        row_id,
        user_id,
        payload,
        config.TIMEZONE,
    )
    if record is None:
        raise web.HTTPNotFound(text="Meal not found")

    _api_meals_cache.clear()
    return web.json_response({"meal": _meal_payload(record, config.TIMEZONE)})


async def api_delete_meal(request: web.Request) -> web.Response:
    user_id = _request_user_id(request)
    row_id = int(request.match_info["row_id"])
    deleted = await asyncio.to_thread(delete_meal, row_id, user_id)
    if not deleted:
        raise web.HTTPNotFound(text="Meal not found")

    _api_meals_cache.clear()
    return web.json_response({"ok": True})
