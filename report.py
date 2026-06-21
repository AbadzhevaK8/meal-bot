import logging
from datetime import date, datetime, timedelta

from config import load_config
from google_fitness import fetch_daily_calories_for_date
from pytz import timezone

from sheets import (
    get_daily_calorie_summaries_for_range,
    get_logs_for_date,
    get_saved_fitness_calories_for_range,
    is_cheatmeal_day,
)

logger = logging.getLogger(__name__)

PROTEIN_KCAL = 4
FAT_KCAL = 9
CARB_KCAL = 4

NORMS = {
    "protein": (15, 30),
    "fat": (20, 35),
    "carbs": (45, 65),
}

MONTHS_RU = {
    1: "января", 2: "февраля", 3: "марта", 4: "апреля",
    5: "мая", 6: "июня", 7: "июля", 8: "августа",
    9: "сентября", 10: "октября", 11: "ноября", 12: "декабря",
}


def _bar(percent: float, width: int = 10) -> str:
    filled = min(round(percent / 10), width)
    return "█" * filled + "░" * (width - filled)


def _check(pct: float, lo: float, hi: float) -> str:
    return "✅" if lo <= pct <= hi else "❌"


def _get_saved_report_expenditure(user_id: int, target_date: date, tz: str) -> tuple[float, str] | None:
    config = load_config()
    fitness_summaries = get_saved_fitness_calories_for_range(
        target_date,
        target_date,
        include_google_fit_fallback=not config.STRICT_EXPENDITURE_SOURCE,
    )
    fitness_summary = fitness_summaries.get(target_date.isoformat())
    if fitness_summary and fitness_summary.get("expenditure_kcal") is not None:
        return float(fitness_summary["expenditure_kcal"]), str(fitness_summary.get("note", ""))

    if not config.STRICT_EXPENDITURE_SOURCE:
        daily_summaries = get_daily_calorie_summaries_for_range(user_id, target_date, target_date, tz=tz)
        daily_summary = daily_summaries.get(target_date.isoformat())
        if daily_summary and daily_summary.get("expenditure_kcal") is not None:
            return float(daily_summary["expenditure_kcal"]), str(daily_summary.get("note", ""))

    return None


def build_daily_report(
    user_id: int,
    tz: str = "Europe/Moscow",
    target_date: date | None = None,
    day_start_hour: int = 3,
) -> str | None:
    """
    Строит отчёт за указанную дату.
    
    Args:
        user_id: Telegram user ID.
        tz: Часовой пояс.
        target_date: Дата начала пищевых суток. По умолчанию — вчера (для ночного отчёта).
    
    Returns:
        Текст отчёта или None, если записей нет.
    """
    if target_date is None:
        target_date = datetime.now(timezone(tz)).date() - timedelta(days=1)

    records = get_logs_for_date(user_id, target_date, tz=tz)
    if not records:
        return None

    raw_total_kcal = sum(float(r.get("kcal", 0) or 0) for r in records)
    raw_total_protein = sum(float(r.get("protein_g", 0) or 0) for r in records)
    raw_total_fat = sum(float(r.get("fat_g", 0) or 0) for r in records)
    raw_total_carbs = sum(float(r.get("carbs_g", 0) or 0) for r in records)
    is_cheatmeal = is_cheatmeal_day(user_id, target_date)

    if is_cheatmeal:
        total_kcal = total_protein = total_fat = total_carbs = 0.0
    else:
        total_kcal = raw_total_kcal
        total_protein = raw_total_protein
        total_fat = raw_total_fat
        total_carbs = raw_total_carbs

    total_burned: float | None = 0.0
    burned_note = ""
    fit_data_time = ""
    saved_expenditure = _get_saved_report_expenditure(user_id, target_date, tz)
    if saved_expenditure:
        total_burned, burned_note_text = saved_expenditure
        burned_note = f" ({burned_note_text})" if burned_note_text else ""
    else:
        config = load_config()
        if not config.STRICT_EXPENDITURE_SOURCE:
            try:
                total_burned = fetch_daily_calories_for_date(target_date, tz, day_start_hour)
                fit_data_time = datetime.now(timezone(tz)).strftime("%H:%M")
            except Exception as e:
                total_burned = None
                burned_note = f" (не удалось получить из Google Fit: {e})"
        elif not config.GARMIN_CONNECT_EMAIL or not config.GARMIN_CONNECT_PASSWORD:
            total_burned = None
            burned_note = " (Garmin Connect cloud не настроен на сервере; ручного/Health Connect значения тоже нет)"
        else:
            total_burned = None
            burned_note = " (Garmin Connect/manual/Health Connect не дали точное значение)"

    if total_kcal > 0:
        pct_protein = (total_protein * PROTEIN_KCAL / total_kcal) * 100
        pct_fat = (total_fat * FAT_KCAL / total_kcal) * 100
        pct_carbs = (total_carbs * CARB_KCAL / total_kcal) * 100
    else:
        pct_protein = pct_fat = pct_carbs = 0.0

    date_str = f"{target_date.day} {MONTHS_RU[target_date.month]}"
    fit_note = f" (Google Fit данные на {fit_data_time})" if fit_data_time else ""

    lines = [f"📊 <b>Итог дня — {date_str}</b>"]
    if is_cheatmeal:
        lines += [
            "🍕 <b>Читмил-день:</b> приход не учитывается в статистике, расход сохранён.",
        ]
    lines += [
        "",
        "🍽 <b>Приёмы пищи:</b>",
    ]
    for r in records:
        name = r.get("name", "?")
        kcal = r.get("kcal", "?")
        lines.append(f"  • {name} — {kcal} ккал")

    lines += [
        "",
        f"🔥 <b>Итого съедено: {int(total_kcal)} ккал</b>",
        (
            f"🔥 <b>Сожжено: {int(total_burned)} ккал</b>{fit_note}{burned_note}"
            if total_burned is not None
            else f"🔥 <b>Сожжено: нет точных данных</b>{burned_note}"
        ),
        (
            f"⚖️ Разница: {int(total_kcal - total_burned)} ккал"
            if total_burned is not None
            else "⚖️ Разница: нет точных данных"
        ),
        f"  🥩 {int(total_protein)}г  🧈 {int(total_fat)}г  🍞 {int(total_carbs)}г",
        "",
        "📐 <b>БЖУ (% от калорийности):</b>",
        f"🥩 Белки     <code>{_bar(pct_protein)}</code> {pct_protein:.0f}%  {_check(pct_protein, *NORMS['protein'])}  <i>15–30%</i>",
        f"🧈 Жиры      <code>{_bar(pct_fat)}</code> {pct_fat:.0f}%  {_check(pct_fat, *NORMS['fat'])}  <i>20–35%</i>",
        f"🍞 Углеводы  <code>{_bar(pct_carbs)}</code> {pct_carbs:.0f}%  {_check(pct_carbs, *NORMS['carbs'])}  <i>45–65%</i>",
    ]

    return "\n".join(lines)
