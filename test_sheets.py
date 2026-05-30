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


if __name__ == "__main__":
    main()
