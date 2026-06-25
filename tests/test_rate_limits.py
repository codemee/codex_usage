import unittest
from unittest.mock import patch

from codex_usage_widget import parse_rate_limit_snapshots, parse_rate_limits


class RateLimitParsingTests(unittest.TestCase):
    def test_reads_all_buckets_from_multi_bucket_response(self):
        snapshots = parse_rate_limit_snapshots(
            {
                "rateLimitsByLimitId": {
                    "codex_week": {
                        "limitId": "codex_week",
                        "primary": {"usedPercent": 42, "windowDurationMins": 10080, "resetsAt": 1},
                    },
                    "codex": {
                        "limitId": "codex",
                        "primary": {"usedPercent": 25, "windowDurationMins": 15, "resetsAt": 1},
                    },
                },
                "rateLimitResetCredits": {"availableCount": 2},
            }
        )

        self.assertEqual([snapshot.limit_id for snapshot in snapshots], ["codex", "codex_week"])
        snapshot = snapshots[0]
        self.assertEqual(snapshot.limit_id, "codex")
        self.assertEqual(snapshot.used_percent, 25)
        self.assertEqual(snapshot.remaining_percent, 75)
        self.assertEqual(snapshot.window_duration_mins, 15)
        self.assertEqual(snapshot.reset_credits, 2)

    def test_falls_back_to_legacy_rate_limits(self):
        snapshot = parse_rate_limits(
            {
                "rateLimits": {
                    "limitId": "codex",
                    "primary": {"usedPercent": 30, "windowDurationMins": 15, "resetsAt": 1},
                }
            }
        )

        self.assertEqual(snapshot.limit_id, "codex")
        self.assertEqual(snapshot.remaining_percent, 70)

    def test_expands_primary_and_secondary_windows_in_same_bucket(self):
        snapshots = parse_rate_limit_snapshots(
            {
                "rateLimitsByLimitId": {
                    "codex": {
                        "limitId": "codex",
                        "primary": {"usedPercent": 16, "windowDurationMins": 300, "resetsAt": 1},
                        "secondary": {"usedPercent": 40, "windowDurationMins": 10080, "resetsAt": 2},
                    }
                }
            }
        )

        self.assertEqual([snapshot.limit_kind for snapshot in snapshots], ["primary", "secondary"])
        self.assertEqual([snapshot.window_label for snapshot in snapshots], ["5 hr", "7 day"])
        self.assertEqual([snapshot.remaining_percent for snapshot in snapshots], [84, 60])

    def test_remaining_percent_is_clamped(self):
        overused = parse_rate_limits({"rateLimits": {"primary": {"usedPercent": 125}}})
        negative = parse_rate_limits({"rateLimits": {"primary": {"usedPercent": -20}}})

        self.assertEqual(overused.remaining_percent, 0)
        self.assertEqual(overused.used_percent, 100)
        self.assertEqual(negative.remaining_percent, 100)
        self.assertEqual(negative.used_percent, 0)

    def test_short_window_reset_time_omits_date(self):
        with patch("codex_usage_widget.datetime") as fake_datetime:
            fake_datetime.fromtimestamp.return_value.strftime.return_value = "10:00"
            snapshot = parse_rate_limits(
                {"rateLimits": {"primary": {"usedPercent": 1, "windowDurationMins": 300, "resetsAt": 1782352800}}}
            )

        fake_datetime.fromtimestamp.return_value.strftime.assert_called_once_with("%H:%M")
        self.assertEqual(snapshot.resets_at_text, "10:00")
        self.assertEqual(snapshot.window_label, "5 hr")

    def test_long_window_reset_time_omits_year(self):
        with patch("codex_usage_widget.datetime") as fake_datetime:
            fake_datetime.fromtimestamp.return_value.strftime.return_value = "06-25 10:00"
            snapshot = parse_rate_limits(
                {"rateLimits": {"primary": {"usedPercent": 1, "windowDurationMins": 10080, "resetsAt": 1782352800}}}
            )

        fake_datetime.fromtimestamp.return_value.strftime.assert_called_once_with("%m-%d %H:%M")
        self.assertEqual(snapshot.resets_at_text, "06-25 10:00")
        self.assertEqual(snapshot.window_label, "7 day")

    def test_missing_optional_fields_do_not_crash(self):
        snapshot = parse_rate_limits({"rateLimits": {"limitId": "codex"}})

        self.assertEqual(snapshot.used_percent, 0)
        self.assertEqual(snapshot.remaining_percent, 100)
        self.assertIsNone(snapshot.window_duration_mins)
        self.assertEqual(snapshot.resets_at_text, "unknown")


if __name__ == "__main__":
    unittest.main()
