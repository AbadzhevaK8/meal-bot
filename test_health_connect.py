from datetime import date
from unittest import TestCase, main
from unittest.mock import patch

import health_connect


class HealthConnectIngestTests(TestCase):
    def test_garmin_default_source_is_used_when_payload_has_no_source(self):
        with patch.object(health_connect, "upsert_fitness_data") as upsert:
            parsed = health_connect.ingest_health_connect_calories(
                {"date": "2026-06-04", "total_kcal": 2100.2},
                "Europe/Moscow",
                health_connect.GARMIN_SOURCE,
            )

        self.assertEqual(parsed["date"], date(2026, 6, 4))
        self.assertEqual(parsed["note"], "Garmin Connect via Health Connect")
        upsert.assert_called_once()
        payload = upsert.call_args.args[0]
        self.assertEqual(payload["description"], "Health Connect total calories for 2026-06-04")
        self.assertEqual(payload["kcal"], 2100.2)
        self.assertEqual(payload["note"], "Garmin Connect via Health Connect")

    def test_payload_source_overrides_default_source(self):
        parsed = health_connect.parse_health_connect_payload(
            {
                "date": "2026-06-04",
                "total_kcal": 2100.2,
                "source": "Manual import",
            },
            "Europe/Moscow",
        )

        self.assertEqual(parsed["note"], "Manual import")


if __name__ == "__main__":
    main()
