import asyncio
import hashlib
import hmac
import json
import time
from datetime import date
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, main
from unittest.mock import patch

from aiohttp.test_utils import make_mocked_request

import web_ui
from google_fitness import GoogleFitnessAuthError


class LiveTodaySummaryTests(IsolatedAsyncioTestCase):
    def test_request_user_id_accepts_telegram_init_data_from_payload(self):
        token = "bot-token"
        user = {"id": 123}
        data = {
            "auth_date": "1760000000",
            "query_id": "test",
            "user": json.dumps(user, separators=(",", ":")),
        }
        data_check_string = "\n".join(f"{key}={data[key]}" for key in sorted(data))
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        data["hash"] = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
        init_data = "&".join(f"{key}={value}" for key, value in data.items())
        request = make_mocked_request("PATCH", "/api/day-flags/2026-06-06", app={
            "web_ui_config": SimpleNamespace(BOT_TOKEN=token, WEB_UI_TOKEN="", ACCESS_PASSWORD=""),
        })

        self.assertEqual(web_ui._request_user_id(request, {"init_data": init_data}), 123)

    def test_merge_days_uses_matching_saved_summary_date(self):
        by_day = {
            "2026-06-04": {"date": "2026-06-04", "count": 1, "totals": {"kcal": 1000}},
            "2026-06-05": {"date": "2026-06-05", "count": 1, "totals": {"kcal": 3405.9}},
        }
        saved_summaries = {
            "2026-06-05": {
                "date": "2026-06-05",
                "expenditure_kcal": 2356.2,
                "note": "Live Google Fit since 03:00",
            },
            "2026-06-01": {
                "date": "2026-06-01",
                "expenditure_kcal": 1111.0,
                "note": "unrelated",
            },
        }

        days = web_ui._merge_days_with_balances(by_day, saved_summaries)
        by_date = {day["date"]: day for day in days}

        self.assertIsNone(by_date["2026-06-04"]["balance"]["expenditure_kcal"])
        self.assertEqual(by_date["2026-06-05"]["balance"]["expenditure_kcal"], 2356.2)
        self.assertEqual(by_date["2026-06-05"]["balance"]["difference_kcal"], 1049.7)

    def test_merge_days_zeroes_intake_on_cheatmeal_day(self):
        by_day = {
            "2026-06-05": {
                "date": "2026-06-05",
                "count": 2,
                "totals": {"kcal": 1800, "protein_g": 90, "fat_g": 70, "carbs_g": 160},
            },
        }
        saved_summaries = {
            "2026-06-05": {
                "date": "2026-06-05",
                "expenditure_kcal": 2400,
                "note": "Health Connect",
            },
        }

        days = web_ui._merge_days_with_balances(by_day, saved_summaries, {"2026-06-05"})

        self.assertTrue(days[0]["is_cheatmeal"])
        self.assertEqual(days[0]["balance"]["intake_kcal"], 0)
        self.assertEqual(days[0]["balance"]["difference_kcal"], -2400)
        self.assertIn("читмил", days[0]["balance"]["note"])

    def test_apply_live_summary_uses_food_day_after_midnight(self):
        saved_summaries = {}
        live_summary = {
            "date": "2026-06-05",
            "expenditure_kcal": 2356.2,
            "note": "Live Google Fit since 03:00",
        }

        web_ui._apply_live_summary(saved_summaries, date(2026, 6, 5), live_summary)

        self.assertIn("2026-06-05", saved_summaries)
        self.assertNotIn("2026-06-06", saved_summaries)

    def test_truthy_query_flag_accepts_explicit_true_values(self):
        self.assertTrue(web_ui._truthy_query_flag("1"))
        self.assertTrue(web_ui._truthy_query_flag("true"))
        self.assertTrue(web_ui._truthy_query_flag("YES"))
        self.assertFalse(web_ui._truthy_query_flag(None))
        self.assertFalse(web_ui._truthy_query_flag("0"))

    def test_live_current_food_day_summary_prefers_saved_health_connect(self):
        with (
            patch.object(web_ui, "get_saved_fitness_calories_for_range", return_value={}),
            patch.object(
                web_ui,
                "sync_garmin_cloud_calories_for_date",
                side_effect=web_ui.GarminConnectNotConfigured("missing"),
            ),
            patch.object(
                web_ui,
                "fetch_health_connect_calories_for_date",
                return_value=(1850.5, "Garmin Connect via Health Connect"),
            ),
            patch.object(web_ui, "fetch_calories_since_day_start") as fit_fetch,
        ):
            result = web_ui._live_current_food_day_summary(date(2026, 6, 5), "Europe/Moscow")

        self.assertEqual(result["expenditure_kcal"], 1850.5)
        self.assertEqual(result["note"], "Garmin Connect via Health Connect")
        fit_fetch.assert_not_called()

    def test_live_current_food_day_summary_prefers_garmin_cloud(self):
        with (
            patch.object(web_ui, "get_saved_fitness_calories_for_range", return_value={}),
            patch.object(
                web_ui,
                "sync_garmin_cloud_calories_for_date",
                return_value=(1234.5, "Garmin Connect cloud"),
            ) as garmin_fetch,
            patch.object(web_ui, "fetch_health_connect_calories_for_date") as health_fetch,
            patch.object(web_ui, "fetch_calories_since_day_start") as fit_fetch,
        ):
            result = web_ui._live_current_food_day_summary(date(2026, 6, 5), "Europe/Moscow")

        self.assertEqual(result["date"], "2026-06-05")
        self.assertEqual(result["expenditure_kcal"], 1234.5)
        self.assertEqual(result["note"], "Garmin Connect cloud")
        garmin_fetch.assert_called_once_with(date(2026, 6, 5))
        health_fetch.assert_not_called()
        fit_fetch.assert_not_called()

    def test_live_current_food_day_summary_prefers_manual_override(self):
        manual_summary = {
            "2026-06-05": {
                "date": "2026-06-05",
                "expenditure_kcal": 1992.0,
                "note": "Manual Mini App override; recorded_at=manual-miniapp",
            },
        }

        with (
            patch.object(web_ui, "get_saved_fitness_calories_for_range", return_value=manual_summary),
            patch.object(web_ui, "sync_garmin_cloud_calories_for_date") as garmin_fetch,
            patch.object(web_ui, "fetch_health_connect_calories_for_date") as health_fetch,
            patch.object(web_ui, "fetch_calories_since_day_start") as fit_fetch,
        ):
            result = web_ui._live_current_food_day_summary(date(2026, 6, 5), "Europe/Moscow")

        self.assertEqual(result["date"], "2026-06-05")
        self.assertEqual(result["expenditure_kcal"], 1992.0)
        self.assertEqual(result["note"], "Manual Mini App override; recorded_at=manual-miniapp")
        garmin_fetch.assert_not_called()
        health_fetch.assert_not_called()
        fit_fetch.assert_not_called()

    def test_live_current_food_day_summary_uses_3am_boundary(self):
        with (
            patch.object(web_ui, "get_saved_fitness_calories_for_range", return_value={}),
            patch.object(
                web_ui,
                "sync_garmin_cloud_calories_for_date",
                side_effect=web_ui.GarminConnectNotConfigured("missing"),
            ),
            patch.object(web_ui, "fetch_health_connect_calories_for_date", return_value=None),
            patch.object(
                web_ui,
                "load_config",
                return_value=SimpleNamespace(STRICT_EXPENDITURE_SOURCE=False),
            ),
            patch.object(web_ui, "fetch_calories_since_day_start", return_value=2345.6) as fit_fetch,
        ):
            result = web_ui._live_current_food_day_summary(date(2026, 6, 5), "Europe/Moscow")

        self.assertEqual(result["date"], "2026-06-05")
        self.assertEqual(result["expenditure_kcal"], 2345.6)
        self.assertEqual(result["note"], "Live Google Fit since 03:00")
        fit_fetch.assert_called_once_with("Europe/Moscow", web_ui.DAY_START_HOUR)

    def test_live_current_food_day_summary_reports_google_fit_auth_error(self):
        with (
            patch.object(web_ui, "get_saved_fitness_calories_for_range", return_value={}),
            patch.object(
                web_ui,
                "sync_garmin_cloud_calories_for_date",
                side_effect=web_ui.GarminConnectNotConfigured("missing"),
            ),
            patch.object(web_ui, "fetch_health_connect_calories_for_date", return_value=None),
            patch.object(
                web_ui,
                "load_config",
                return_value=SimpleNamespace(STRICT_EXPENDITURE_SOURCE=False),
            ),
            patch.object(
                web_ui,
                "fetch_calories_since_day_start",
                side_effect=GoogleFitnessAuthError("expired"),
            ),
        ):
            result = web_ui._live_current_food_day_summary(date(2026, 6, 6), "Europe/Moscow")

        self.assertEqual(result["date"], "2026-06-06")
        self.assertIsNone(result["expenditure_kcal"])
        self.assertIsNone(result["difference_kcal"])
        self.assertEqual(result["note"], "нужна авторизация Google Fit")

    def test_live_current_food_day_summary_skips_google_fit_in_strict_mode(self):
        with (
            patch.object(web_ui, "get_saved_fitness_calories_for_range", return_value={}),
            patch.object(
                web_ui,
                "sync_garmin_cloud_calories_for_date",
                side_effect=web_ui.GarminConnectNotConfigured("missing"),
            ),
            patch.object(web_ui, "fetch_health_connect_calories_for_date", return_value=None),
            patch.object(
                web_ui,
                "load_config",
                return_value=SimpleNamespace(STRICT_EXPENDITURE_SOURCE=True),
            ),
            patch.object(web_ui, "fetch_calories_since_day_start") as fit_fetch,
        ):
            result = web_ui._live_current_food_day_summary(date(2026, 6, 6), "Europe/Moscow")

        self.assertIsNone(result["expenditure_kcal"])
        self.assertEqual(result["note"], "нет точного расхода Garmin")
        fit_fetch.assert_not_called()

    async def test_live_current_food_day_summary_times_out(self):
        def slow_summary(food_day, tz):
            time.sleep(0.2)
            return {"date": food_day.isoformat(), "expenditure_kcal": 123}

        with (
            patch.object(web_ui, "LIVE_TODAY_TIMEOUT_SECONDS", 0.01),
            patch.object(web_ui, "fetch_health_connect_calories_for_date", return_value=None),
            patch.object(web_ui, "_live_current_food_day_summary", side_effect=slow_summary),
        ):
            started = time.perf_counter()
            result = await web_ui._live_current_food_day_summary_with_timeout(
                date(2026, 6, 5),
                "Europe/Moscow",
            )
            elapsed = time.perf_counter() - started

        self.assertIsNone(result)
        self.assertLess(elapsed, 0.1)


if __name__ == "__main__":
    main()
