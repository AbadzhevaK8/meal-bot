from unittest import TestCase, main

from garmin_connect import GarminConnectNoCalories, extract_total_kilocalories


class GarminConnectCaloriesTests(TestCase):
    def test_extracts_total_kilocalories(self):
        summary = {
            "activeKilocalories": 633,
            "bmrKilocalories": 1550,
            "totalKilocalories": 2183,
        }

        self.assertEqual(extract_total_kilocalories(summary), 2183)

    def test_falls_back_to_active_plus_bmr(self):
        summary = {
            "activeKilocalories": 633,
            "bmrKilocalories": 1550,
        }

        self.assertEqual(extract_total_kilocalories(summary), 2183)

    def test_rejects_missing_calories(self):
        with self.assertRaises(GarminConnectNoCalories):
            extract_total_kilocalories({})


if __name__ == "__main__":
    main()
