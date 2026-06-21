from datetime import date
from contextlib import contextmanager
from types import SimpleNamespace
from unittest import TestCase, main
from unittest.mock import patch

import sheets
from aggregate_daily_calories import build_expenditure_by_date
import aggregate_daily_calories


class FakeWorksheet:
    def __init__(self, values):
        self.values = values
        self.updated = None
        self.deleted = None

    def get_all_values(self):
        return self.values

    def update(self, range_name, rows):
        self.updated = (range_name, rows)

    def append_row(self, row):
        self.values.append(row)

    def delete_rows(self, row_id):
        self.deleted = row_id

    def clear(self):
        self.values = [self.values[0]] if self.values else []


class NonPersistingWorksheet(FakeWorksheet):
    def append_row(self, row):
        self.appended = row


class MealWriteVerificationTests(TestCase):
    def test_log_meal_verifies_row_after_append(self):
        worksheet = FakeWorksheet([sheets.HEADERS])

        with (
            patch.object(sheets, "_is_sheets_configured", return_value=True),
            patch.object(sheets, "_get_sheet", return_value=worksheet),
            patch.object(sheets, "_append_meal_audit_event") as audit,
            patch("config.load_config", return_value=SimpleNamespace(GOOGLE_CREDENTIALS_JSON="x", GOOGLE_SHEETS_ID="y")),
        ):
            sheets.log_meal({"name": "Test meal", "kcal": 123}, 42)

        self.assertEqual(len(worksheet.values), 2)
        self.assertEqual(worksheet.values[1][1], "42")
        self.assertEqual(worksheet.values[1][2], "Test meal")
        self.assertEqual(audit.call_args_list[-1].args[0], "verified")

    def test_log_meal_raises_when_appended_row_is_not_visible(self):
        worksheet = NonPersistingWorksheet([sheets.HEADERS])

        with (
            patch.object(sheets, "_is_sheets_configured", return_value=True),
            patch.object(sheets, "_get_sheet", return_value=worksheet),
            patch.object(sheets, "_append_meal_audit_event") as audit,
            patch("config.load_config", return_value=SimpleNamespace(GOOGLE_CREDENTIALS_JSON="x", GOOGLE_SHEETS_ID="y")),
        ):
            with self.assertRaises(RuntimeError):
                sheets.log_meal({"name": "Ghost meal", "kcal": 321}, 42)

        self.assertEqual(audit.call_args_list[-1].args[0], "verify_failed")

    def test_meal_row_match_accepts_equivalent_numeric_formats(self):
        left = ["2026-06-13 01:46", "42", "Meal", "1", "1", "0", "0", "0", "high", ""]
        right = ["2026-06-13 01:46", 42, "Meal", 1.0, 1.0, 0.0, 0, 0, "high", ""]

        self.assertTrue(sheets._meal_row_matches(left, right))


class SavedFitnessCaloriesTests(TestCase):
    def get_health(self, values, target):
        with (
            patch.object(sheets, "_is_sheets_configured", return_value=True),
            patch.object(sheets, "_get_fitness_sheet", return_value=FakeWorksheet(values)),
            patch("config.load_config", return_value=SimpleNamespace(GOOGLE_CREDENTIALS_JSON="x", GOOGLE_SHEETS_ID="y")),
        ):
            return sheets.get_health_connect_calories_for_date(target)

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

    def test_saved_for_date_treats_manual_note_as_override(self):
        values = [
            ["timestamp", "description", "kcal", "note"],
            [
                "2026-06-12 12:15",
                "Health Connect total calories for 2026-06-10",
                "1774.0",
                "Manual Mini App override; recorded_at=manual-miniapp",
            ],
        ]

        result = self.get_saved(values, date(2026, 6, 10))

        self.assertEqual(result, (1774.0, "Manual Mini App override; recorded_at=manual-miniapp"))

    def test_saved_for_date_does_not_match_manual_note_by_timestamp_when_description_has_other_date(self):
        values = [
            ["timestamp", "description", "kcal", "note"],
            [
                "2026-06-12 12:15",
                "Health Connect total calories for 2026-06-10",
                "1774.0",
                "Manual Mini App override; recorded_at=manual-miniapp",
            ],
        ]

        result = self.get_saved(values, date(2026, 6, 12))

        self.assertIsNone(result)

    def test_health_connect_for_date_ignores_partial_total(self):
        values = [
            ["timestamp", "description", "kcal", "note"],
            [
                "2026-06-11 01:26",
                "Health Connect total calories for 2026-06-11",
                "94.8",
                "Garmin Connect via Health Connect; recorded_at=2026-06-10T22:26:50Z",
            ],
        ]

        self.assertIsNone(self.get_health(values, date(2026, 6, 11)))

    def test_health_connect_for_date_accepts_completed_total(self):
        values = [
            ["timestamp", "description", "kcal", "note"],
            [
                "2026-06-12 00:05",
                "Health Connect total calories for 2026-06-11",
                "2100.2",
                "Garmin Connect via Health Connect; recorded_at=2026-06-11T21:00:00Z",
            ],
        ]

        result = self.get_health(values, date(2026, 6, 11))

        self.assertEqual(result, (2100.2, "Garmin Connect via Health Connect; recorded_at=2026-06-11T21:00:00Z"))

    def get_saved_range(self, values, start, end):
        with (
            patch.object(sheets, "_is_sheets_configured", return_value=True),
            patch.object(sheets, "_get_fitness_sheet", return_value=FakeWorksheet(values)),
            patch("config.load_config", return_value=SimpleNamespace(GOOGLE_CREDENTIALS_JSON="x", GOOGLE_SHEETS_ID="y")),
        ):
            return sheets.get_saved_fitness_calories_for_range(start, end)

    def test_range_returns_saved_google_fit_by_description_date(self):
        values = [
            ["timestamp", "description", "kcal", "note"],
            [
                "2026-06-05 06:00",
                "Calories expended (+BMR if available) from 2026-06-04 to 2026-06-05",
                "2300.4",
                "Auto sync",
            ],
        ]

        result = self.get_saved_range(values, date(2026, 6, 4), date(2026, 6, 4))

        self.assertEqual(result["2026-06-04"]["expenditure_kcal"], 2300.4)

    def test_range_prefers_health_connect_over_google_fit(self):
        values = [
            ["timestamp", "description", "kcal", "note"],
            [
                "2026-06-05 06:00",
                "Calories expended (+BMR if available) from 2026-06-04 to 2026-06-05",
                "2300.4",
                "Auto sync",
            ],
            [
                "2026-06-05 08:00",
                "Health Connect total calories for 2026-06-04",
                "2100.2",
                "Health Connect",
            ],
        ]

        result = self.get_saved_range(values, date(2026, 6, 4), date(2026, 6, 4))

        self.assertEqual(result["2026-06-04"]["expenditure_kcal"], 2100.2)
        self.assertEqual(result["2026-06-04"]["note"], "Health Connect")

    def test_range_prefers_garmin_cloud_over_google_fit(self):
        values = [
            ["timestamp", "description", "kcal", "note"],
            [
                "2026-06-05 06:00",
                "Calories expended (+BMR if available) from 2026-06-04 to 2026-06-05",
                "2300.4",
                "Auto sync",
            ],
            [
                "2026-06-05 03:01",
                "Garmin Connect cloud daily calories for 2026-06-04",
                "2183.0",
                "Garmin Connect cloud",
            ],
        ]

        result = self.get_saved_range(values, date(2026, 6, 4), date(2026, 6, 4))

        self.assertEqual(result["2026-06-04"]["expenditure_kcal"], 2183.0)
        self.assertEqual(result["2026-06-04"]["note"], "Garmin Connect cloud")

    def test_range_can_exclude_google_fit_fallback(self):
        values = [
            ["timestamp", "description", "kcal", "note"],
            [
                "2026-06-05 06:00",
                "Calories expended (+BMR if available) from 2026-06-04 to 2026-06-05",
                "2300.4",
                "Auto sync",
            ],
        ]

        with (
            patch.object(sheets, "_is_sheets_configured", return_value=True),
            patch.object(sheets, "_get_fitness_sheet", return_value=FakeWorksheet(values)),
            patch("config.load_config", return_value=SimpleNamespace(GOOGLE_CREDENTIALS_JSON="x", GOOGLE_SHEETS_ID="y")),
        ):
            result = sheets.get_saved_fitness_calories_for_range(
                date(2026, 6, 4),
                date(2026, 6, 4),
                include_google_fit_fallback=False,
            )

        self.assertEqual(result, {})


class DailyAggregationTests(TestCase):
    def get_saved_range(self, values, start, end):
        with (
            patch.object(sheets, "_is_sheets_configured", return_value=True),
            patch.object(sheets, "_get_fitness_sheet", return_value=FakeWorksheet(values)),
            patch("config.load_config", return_value=SimpleNamespace(GOOGLE_CREDENTIALS_JSON="x", GOOGLE_SHEETS_ID="y")),
        ):
            return sheets.get_saved_fitness_calories_for_range(start, end)

    def test_strict_aggregation_writes_blank_expenditure_when_exact_value_missing(self):
        meal_sheet = FakeWorksheet([
            ["timestamp", "user_id", "name", "weight_g", "kcal", "protein_g", "fat_g", "carbs_g", "confidence", "note"],
            ["2026-06-15 12:00", "162187174", "Meal", "", "1870", "", "", "", "", ""],
        ])
        fitness_sheet = FakeWorksheet([
            ["timestamp", "description", "kcal", "note"],
        ])
        daily_sheet = FakeWorksheet([
            ["timestamp", "user_id", "date", "intake_kcal", "expenditure_kcal", "difference_kcal", "note"],
        ])

        def fake_log_daily_calories(user_id, intake, expenditure, difference, date_value, tz, note):
            daily_sheet.append_row([
                "2026-06-16 09:00",
                str(user_id),
                date_value,
                round(float(intake or 0), 1),
                "" if expenditure is None else round(float(expenditure), 1),
                "" if difference is None else round(float(difference), 1),
                note,
            ])

        with (
            patch.object(aggregate_daily_calories, "_is_sheets_configured", return_value=True),
            patch.object(aggregate_daily_calories, "_get_sheet", return_value=meal_sheet),
            patch.object(aggregate_daily_calories, "_get_fitness_sheet", return_value=fitness_sheet),
            patch.object(aggregate_daily_calories, "_get_daily_calories_sheet", return_value=daily_sheet),
            patch.object(aggregate_daily_calories, "log_daily_calories", side_effect=fake_log_daily_calories),
            patch.object(aggregate_daily_calories, "get_cheatmeal_days_for_range", return_value=set()),
            patch.object(aggregate_daily_calories, "has_saved_tokens", return_value=False),
            patch.object(
                aggregate_daily_calories,
                "load_config",
                return_value=SimpleNamespace(
                    GOOGLE_CREDENTIALS_JSON="x",
                    GOOGLE_SHEETS_ID="y",
                    TIMEZONE="Europe/Moscow",
                    REPORT_USER_IDS=[162187174],
                    STRICT_EXPENDITURE_SOURCE=True,
                ),
            ),
        ):
            aggregate_daily_calories.main(date(2026, 6, 15), date(2026, 6, 15))

        self.assertEqual(daily_sheet.values[1][3], 1870.0)
        self.assertEqual(daily_sheet.values[1][4], "")
        self.assertEqual(daily_sheet.values[1][5], "")

    def test_range_uses_garmin_health_connect_note(self):
        values = [
            ["timestamp", "description", "kcal", "note"],
            [
                "2026-06-05 08:00",
                "Health Connect total calories for 2026-06-04",
                "2100.2",
                "Garmin Connect via Health Connect",
            ],
        ]

        result = self.get_saved_range(values, date(2026, 6, 4), date(2026, 6, 4))

        self.assertEqual(result["2026-06-04"]["expenditure_kcal"], 2100.2)
        self.assertEqual(result["2026-06-04"]["note"], "Garmin Connect via Health Connect")

    def test_range_prefers_manual_override_over_health_connect(self):
        values = [
            ["timestamp", "description", "kcal", "note"],
            [
                "2026-06-05 08:00",
                "Health Connect total calories for 2026-06-04",
                "2100.2",
                "Garmin Connect via Health Connect",
            ],
            [
                "2026-06-05 09:00",
                "Manual expenditure override for 2026-06-04",
                "1774",
                "Manual Mini App override",
            ],
        ]

        result = self.get_saved_range(values, date(2026, 6, 4), date(2026, 6, 4))

        self.assertEqual(result["2026-06-04"]["expenditure_kcal"], 1774)
        self.assertEqual(result["2026-06-04"]["note"], "Manual Mini App override")

    def test_range_treats_manual_note_as_override(self):
        values = [
            ["timestamp", "description", "kcal", "note"],
            [
                "2026-06-12 12:15",
                "Health Connect total calories for 2026-06-10",
                "1774.0",
                "Manual Mini App override; recorded_at=manual-miniapp",
            ],
        ]

        result = self.get_saved_range(values, date(2026, 6, 10), date(2026, 6, 10))

        self.assertEqual(result["2026-06-10"]["expenditure_kcal"], 1774.0)
        self.assertEqual(result["2026-06-10"]["note"], "Manual Mini App override; recorded_at=manual-miniapp")

    def test_range_ignores_partial_health_connect_total(self):
        values = [
            ["timestamp", "description", "kcal", "note"],
            [
                "2026-06-12 06:00",
                "Calories expended (+BMR if available) from 2026-06-11 to 2026-06-12",
                "2300.4",
                "Auto sync",
            ],
            [
                "2026-06-11 01:26",
                "Health Connect total calories for 2026-06-11",
                "94.8",
                "Garmin Connect via Health Connect; recorded_at=2026-06-10T22:26:50Z",
            ],
        ]

        result = self.get_saved_range(values, date(2026, 6, 11), date(2026, 6, 11))

        self.assertEqual(result["2026-06-11"]["expenditure_kcal"], 2300.4)

    def test_range_accepts_completed_health_connect_total(self):
        values = [
            ["timestamp", "description", "kcal", "note"],
            [
                "2026-06-12 00:05",
                "Health Connect total calories for 2026-06-11",
                "2100.2",
                "Garmin Connect via Health Connect; recorded_at=2026-06-11T21:00:00Z",
            ],
        ]

        result = self.get_saved_range(values, date(2026, 6, 11), date(2026, 6, 11))

        self.assertEqual(result["2026-06-11"]["expenditure_kcal"], 2100.2)

    def test_aggregate_prefers_health_connect_row_for_same_date(self):
        rows = [
            {
                "timestamp": "2026-06-05 06:00",
                "description": "Calories expended (+BMR if available) from 2026-06-04 to 2026-06-05",
                "kcal": "2300.4",
                "note": "Auto sync",
            },
            {
                "timestamp": "2026-06-05 08:00",
                "description": "Health Connect total calories for 2026-06-04",
                "kcal": "2100.2",
                "note": "Garmin Connect via Health Connect",
            },
        ]

        result = build_expenditure_by_date(rows, date(2026, 6, 4))

        self.assertEqual(result["2026-06-04"], 2100.2)

    def test_aggregate_prefers_garmin_cloud_over_google_fit(self):
        rows = [
            {
                "timestamp": "2026-06-05 06:00",
                "description": "Calories expended (+BMR if available) from 2026-06-04 to 2026-06-05",
                "kcal": "2300.4",
                "note": "Auto sync",
            },
            {
                "timestamp": "2026-06-05 03:01",
                "description": "Garmin Connect cloud daily calories for 2026-06-04",
                "kcal": "2183.0",
                "note": "Garmin Connect cloud",
            },
        ]

        result = build_expenditure_by_date(rows, date(2026, 6, 4))

        self.assertEqual(result["2026-06-04"], 2183.0)

    def test_aggregate_can_exclude_google_fit_fallback(self):
        rows = [
            {
                "timestamp": "2026-06-05 06:00",
                "description": "Calories expended (+BMR if available) from 2026-06-04 to 2026-06-05",
                "kcal": "2300.4",
                "note": "Auto sync",
            },
        ]

        result = build_expenditure_by_date(
            rows,
            date(2026, 6, 4),
            include_google_fit_fallback=False,
        )

        self.assertEqual(result, {})

    def test_aggregate_prefers_manual_override_over_health_connect(self):
        rows = [
            {
                "timestamp": "2026-06-05 08:00",
                "description": "Health Connect total calories for 2026-06-04",
                "kcal": "2100.2",
                "note": "Garmin Connect via Health Connect",
            },
            {
                "timestamp": "2026-06-05 09:00",
                "description": "Manual expenditure override for 2026-06-04",
                "kcal": "1774",
                "note": "Manual Mini App override",
            },
        ]

        result = build_expenditure_by_date(rows, date(2026, 6, 4))

        self.assertEqual(result["2026-06-04"], 1774)

    def test_aggregate_ignores_partial_health_connect_row(self):
        rows = [
            {
                "timestamp": "2026-06-12 06:00",
                "description": "Calories expended (+BMR if available) from 2026-06-11 to 2026-06-12",
                "kcal": "2300.4",
                "note": "Auto sync",
            },
            {
                "timestamp": "2026-06-11 01:26",
                "description": "Health Connect total calories for 2026-06-11",
                "kcal": "94.8",
                "note": "Garmin Connect via Health Connect; recorded_at=2026-06-10T22:26:50Z",
            },
        ]

        result = build_expenditure_by_date(rows, date(2026, 6, 11))

        self.assertEqual(result["2026-06-11"], 2300.4)


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


class MealEditingTests(TestCase):
    @contextmanager
    def with_sheet(self, worksheet):
        with (
            patch.object(sheets, "_is_sheets_configured", return_value=True),
            patch.object(sheets, "_get_sheet", return_value=worksheet),
            patch("config.load_config", return_value=SimpleNamespace(GOOGLE_CREDENTIALS_JSON="x", GOOGLE_SHEETS_ID="y")),
        ):
            yield

    def test_range_returns_row_ids(self):
        worksheet = FakeWorksheet([
            ["timestamp", "user_id", "name", "weight_g", "kcal", "protein_g", "fat_g", "carbs_g", "confidence", "note"],
            ["2026-05-29 12:00", "123", "own meal", "100", "200", "10", "5", "20", "high", ""],
            ["2026-05-29 13:00", "999", "other", "100", "300", "", "", "", "", ""],
        ])

        with self.with_sheet(worksheet):
            result = sheets.get_meal_logs_for_range(123, date(2026, 5, 29), date(2026, 5, 29))

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["row_id"], 2)

    def test_update_meal_requires_owner(self):
        worksheet = FakeWorksheet([
            ["timestamp", "user_id", "name", "weight_g", "kcal", "protein_g", "fat_g", "carbs_g", "confidence", "note"],
            ["2026-05-29 12:00", "999", "other", "100", "300", "", "", "", "", ""],
        ])

        with self.with_sheet(worksheet):
            result = sheets.update_meal(2, 123, {"name": "edited"})

        self.assertIsNone(result)
        self.assertIsNone(worksheet.updated)

    def test_update_meal_writes_expected_row(self):
        worksheet = FakeWorksheet([
            ["timestamp", "user_id", "name", "weight_g", "kcal", "protein_g", "fat_g", "carbs_g", "confidence", "note"],
            ["2026-05-29 12:00", "123", "old", "100", "300", "1", "2", "3", "low", ""],
        ])

        with self.with_sheet(worksheet):
            result = sheets.update_meal(2, 123, {"name": "new", "kcal": "250,5"})

        self.assertEqual(result["name"], "new")
        self.assertEqual(worksheet.updated[0], "A2:J2")
        self.assertEqual(worksheet.updated[1][0][2], "new")
        self.assertEqual(worksheet.updated[1][0][4], 250.5)

    def test_delete_meal_requires_owner(self):
        worksheet = FakeWorksheet([
            ["timestamp", "user_id", "name", "weight_g", "kcal", "protein_g", "fat_g", "carbs_g", "confidence", "note"],
            ["2026-05-29 12:00", "999", "other", "100", "300", "", "", "", "", ""],
        ])

        with self.with_sheet(worksheet):
            deleted = sheets.delete_meal(2, 123)

        self.assertFalse(deleted)
        self.assertIsNone(worksheet.deleted)

    def test_daily_calorie_summaries_use_latest_row(self):
        worksheet = FakeWorksheet([
            ["timestamp", "user_id", "date", "intake_kcal", "expenditure_kcal", "difference_kcal", "note"],
            ["2026-05-30 01:00", "123", "2026-05-29", "1000", "1800", "-800", "old"],
            ["2026-05-30 03:00", "123", "2026-05-29", "1200", "1900", "-700", "new"],
            ["2026-05-30 03:00", "999", "2026-05-29", "999", "999", "0", "other"],
        ])

        with (
            patch.object(sheets, "_is_sheets_configured", return_value=True),
            patch.object(sheets, "_get_daily_calories_sheet", return_value=worksheet),
            patch("config.load_config", return_value=SimpleNamespace(GOOGLE_CREDENTIALS_JSON="x", GOOGLE_SHEETS_ID="y")),
        ):
            result = sheets.get_daily_calorie_summaries_for_range(
                123,
                date(2026, 5, 29),
                date(2026, 5, 29),
            )

        self.assertEqual(result["2026-05-29"]["intake_kcal"], 1200)
        self.assertEqual(result["2026-05-29"]["expenditure_kcal"], 1900)
        self.assertEqual(result["2026-05-29"]["note"], "new")


class CheatmealDayTests(TestCase):
    @contextmanager
    def with_day_flags(self, worksheet):
        with (
            patch.object(sheets, "_is_sheets_configured", return_value=True),
            patch.object(sheets, "_get_day_flags_sheet", return_value=worksheet),
            patch("config.load_config", return_value=SimpleNamespace(GOOGLE_CREDENTIALS_JSON="x", GOOGLE_SHEETS_ID="y")),
        ):
            yield

    def test_get_cheatmeal_days_for_range_returns_enabled_days_only(self):
        worksheet = FakeWorksheet([
            ["timestamp", "user_id", "date", "is_cheatmeal", "note"],
            ["2026-06-05 12:00", "123", "2026-06-05", "TRUE", ""],
            ["2026-06-06 12:00", "123", "2026-06-06", "FALSE", ""],
            ["2026-06-05 12:00", "999", "2026-06-05", "TRUE", ""],
        ])

        with self.with_day_flags(worksheet):
            result = sheets.get_cheatmeal_days_for_range(123, date(2026, 6, 5), date(2026, 6, 6))

        self.assertEqual(result, {"2026-06-05"})

    def test_set_cheatmeal_day_updates_existing_row(self):
        worksheet = FakeWorksheet([
            ["timestamp", "user_id", "date", "is_cheatmeal", "note"],
            ["2026-06-05 12:00", "123", "2026-06-05", "FALSE", ""],
        ])

        with self.with_day_flags(worksheet):
            result = sheets.set_cheatmeal_day(123, date(2026, 6, 5), True, note="test")

        self.assertTrue(result["is_cheatmeal"])
        self.assertEqual(worksheet.updated[0], "A2:E2")
        self.assertEqual(worksheet.updated[1][0][3], "TRUE")


class SpreadsheetConnectionCacheTests(TestCase):
    def tearDown(self):
        sheets._get_worksheet.cache_clear()
        sheets._get_spreadsheet.cache_clear()

    def test_reuses_spreadsheet_connection_for_multiple_tabs(self):
        class FakeSpreadsheet:
            def worksheet(self, name):
                return FakeWorksheet([[]])

        class FakeClient:
            def __init__(self):
                self.open_calls = 0

            def open_by_key(self, sheet_id):
                self.open_calls += 1
                return FakeSpreadsheet()

        fake_client = FakeClient()
        sheets._get_worksheet.cache_clear()
        sheets._get_spreadsheet.cache_clear()

        with (
            patch.object(sheets.Credentials, "from_service_account_file", return_value=object()),
            patch.object(sheets.gspread, "authorize", return_value=fake_client) as authorize,
        ):
            sheets._get_sheet("credentials.json", "sheet-id")
            sheets._get_daily_calories_sheet("credentials.json", "sheet-id")

        self.assertEqual(authorize.call_count, 1)
        self.assertEqual(fake_client.open_calls, 1)


if __name__ == "__main__":
    main()
