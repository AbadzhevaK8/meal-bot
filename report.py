import logging
from datetime import datetime

from pytz import timezone

from sheets import get_today_logs

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


def build_daily_report(user_id: int, tz: str = "Europe/Moscow") -> str | None:
    records = get_today_logs(user_id, tz=tz)
    if not records:
        return None

    total_kcal = sum(float(r.get("kcal", 0) or 0) for r in records)
    total_protein = sum(float(r.get("protein_g", 0) or 0) for r in records)
    total_fat = sum(float(r.get("fat_g", 0) or 0) for r in records)
    total_carbs = sum(float(r.get("carbs_g", 0) or 0) for r in records)

    if total_kcal > 0:
        pct_protein = (total_protein * PROTEIN_KCAL / total_kcal) * 100
        pct_fat = (total_fat * FAT_KCAL / total_kcal) * 100
        pct_carbs = (total_carbs * CARB_KCAL / total_kcal) * 100
    else:
        pct_protein = pct_fat = pct_carbs = 0.0

    now = datetime.now(timezone(tz))
    date_str = f"{now.day} {MONTHS_RU[now.month]}"

    lines = [
        f"📊 <b>Итог дня — {date_str}</b>",
        "",
        "🍽 <b>Приёмы пищи:</b>",
    ]
    for r in records:
        name = r.get("name", "?")
        kcal = r.get("kcal", "?")
        lines.append(f"  • {name} — {kcal} ккал")

    lines += [
        "",
        f"🔥 <b>Итого: {int(total_kcal)} ккал</b>",
        f"  🥩 {int(total_protein)}г  🧈 {int(total_fat)}г  🍞 {int(total_carbs)}г",
        "",
        "📐 <b>БЖУ (% от калорийности):</b>",
        f"🥩 Белки     <code>{_bar(pct_protein)}</code> {pct_protein:.0f}%  {_check(pct_protein, *NORMS['protein'])}  <i>15–30%</i>",
        f"🧈 Жиры      <code>{_bar(pct_fat)}</code> {pct_fat:.0f}%  {_check(pct_fat, *NORMS['fat'])}  <i>20–35%</i>",
        f"🍞 Углеводы  <code>{_bar(pct_carbs)}</code> {pct_carbs:.0f}%  {_check(pct_carbs, *NORMS['carbs'])}  <i>45–65%</i>",
    ]

    return "\n".join(lines)
