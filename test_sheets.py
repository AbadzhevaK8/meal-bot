from datetime import date
from types import SimpleNamespace
from unittest import TestCase, main
from unittest.mock import patch

import sheets


class FakeWorksheet:
    def __init__(self, values):
        self.values = values

    def get_all_values(self):
        return self.values


class SavedFitnessCaloriesTests(TestCase):
    def get_saved(self, values, target):
        with (
            patch.object(sheets, "_is_sheets_configured", return_value=True),
            patch.object(sheets, "_get_fitness_sheet", return_value=FakeWorksheet(values)),
            patch("config.load_config", return_value=SimpleNamespace(GOOGLE_CREDENTIALS_JSON="x", GOOGLE_SHEETS_ID="y")),
        ):
            return sheets.get_saved_fitness_calories_for_date(target)

    def test_ignores_other_days_updated_on_target_date(self):
        values = [
            ["timestamp", "description", "kcal", "note"],
            [
                "2026-05-29 00:00",
                "Calories expended (+BMR if available) from 2026-05-23 to 2026-05-24",
                "2090.6",
                "Auto sync",
            ],
            [
                "2026-05-29 00:00",
                "Calories expended (+BMR if available) from 2026-05-28 to 2026-05-29",
                "2402.5",
                "Auto sync",
            ],
        ]

        self.assertIsNone(self.get_saved(values, date(2026, 5, 29)))

    def test_prefers_matching_description_date(self):
        values = [
            ["timestamp", "description", "kcal", "note"],
            [
                "2026-05-30 06:00",
                "Calories expended (+BMR if available) from 2026-05-29 to 2026-05-30",
                "2068.9",
                "Auto sync",
            ],
            [
                "2026-05-29 00:00",
                "Legacy fitness entry",
                "500",
                "Legacy",
            ],
        ]

        result = self.get_saved(values, date(2026, 5, 29))

        self.assertEqual(result, (2068.9, "сохранённые данные fitness на 2026-05-30 06:00"))


class MealDayBoundaryTests(TestCase):
    def get_logs(self, values, target):
        with (
            patch.object(sheets, "_is_sheets_configured", return_value=True),
            patch.object(sheets, "_get_sheet", return_value=FakeWorksheet(values)),
            patch("config.load_config", return_value=SimpleNamespace(GOOGLE_CREDENTIALS_JSON="x", GOOGLE_SHEETS_ID="y")),
        ):
            return sheets.get_logs_for_date(123, target, tz="Europe/Moscow")

    def test_food_day_starts_at_3am(self):
        values = [
            ["timestamp", "user_id", "name", "weight_g", "kcal", "protein_g", "fat_g", "carbs_g", "confidence", "note"],
            ["2026-05-29 02:59", "123", "before boundary", "", "100", "", "", "", "", ""],
            ["2026-05-29 03:00", "123", "breakfast", "", "200", "", "", "", "", ""],
            ["2026-05-30 02:59", "123", "late snack", "", "300", "", "", "", "", ""],
            ["2026-05-30 03:00", "123", "next day", "", "400", "", "", "", "", ""],
        ]

        result = self.get_logs(values, date(2026, 5, 29))

        self.assertEqual([row["name"] for row in result], ["breakfast", "late snack"])

    def test_food_day_excludes_other_users(self):
        values = [
            ["timestamp", "user_id", "name", "weight_g", "kcal", "protein_g", "fat_g", "carbs_g", "confidence", "note"],
            ["2026-05-29 12:00", "999", "other user", "", "100", "", "", "", "", ""],
            ["2026-05-29 12:00", "123", "own meal", "", "200", "", "", "", "", ""],
        ]

        result = self.get_logs(values, date(2026, 5, 29))

        self.assertEqual([row["name"] for row in result], ["own meal"])


if __name__ == "__main__":
    main()
