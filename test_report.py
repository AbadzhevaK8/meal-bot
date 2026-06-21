from datetime import date
from types import SimpleNamespace
from unittest import TestCase, main
from unittest.mock import patch

import report


class DailyReportExpenditureTests(TestCase):
    def test_report_prefers_manual_saved_expenditure_over_live_google_fit(self):
        records = [
            {
                "name": "Meal",
                "kcal": "1710.2",
                "protein_g": "89",
                "fat_g": "42",
                "carbs_g": "234",
            }
        ]
        target = date(2026, 6, 14)

        with (
            patch.object(report, "get_logs_for_date", return_value=records),
            patch.object(report, "is_cheatmeal_day", return_value=False),
            patch.object(
                report,
                "get_saved_fitness_calories_for_range",
                return_value={
                    "2026-06-14": {
                        "expenditure_kcal": 2033.0,
                        "note": "Manual Mini App override; recorded_at=manual-miniapp",
                    }
                },
            ),
            patch.object(report, "get_daily_calorie_summaries_for_range", return_value={}),
            patch.object(
                report,
                "load_config",
                return_value=SimpleNamespace(
                    STRICT_EXPENDITURE_SOURCE=True,
                    GARMIN_CONNECT_EMAIL="test@example.com",
                    GARMIN_CONNECT_PASSWORD="secret",
                ),
            ),
            patch.object(report, "fetch_daily_calories_for_date", return_value=1442.0) as google_fit,
        ):
            text = report.build_daily_report(162187174, target_date=target)

        google_fit.assert_not_called()
        self.assertIn("Сожжено: 2033 ккал", text)
        self.assertIn("Manual Mini App override", text)
        self.assertNotIn("Google Fit данные", text)

    def test_strict_report_does_not_use_google_fit_when_precise_expenditure_missing(self):
        records = [
            {
                "name": "Meal",
                "kcal": "500",
                "protein_g": "20",
                "fat_g": "10",
                "carbs_g": "60",
            }
        ]
        target = date(2026, 6, 14)

        with (
            patch.object(report, "get_logs_for_date", return_value=records),
            patch.object(report, "is_cheatmeal_day", return_value=False),
            patch.object(report, "get_saved_fitness_calories_for_range", return_value={}),
            patch.object(report, "get_daily_calorie_summaries_for_range", return_value={}) as daily,
            patch.object(
                report,
                "load_config",
                return_value=SimpleNamespace(
                    STRICT_EXPENDITURE_SOURCE=True,
                    GARMIN_CONNECT_EMAIL=None,
                    GARMIN_CONNECT_PASSWORD=None,
                ),
            ),
            patch.object(report, "fetch_daily_calories_for_date", return_value=1442.0) as google_fit,
        ):
            text = report.build_daily_report(162187174, target_date=target)

        google_fit.assert_not_called()
        daily.assert_not_called()
        self.assertIn("Сожжено: нет точных данных", text)
        self.assertIn("Разница: нет точных данных", text)


if __name__ == "__main__":
    main()
