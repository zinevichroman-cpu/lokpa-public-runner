import unittest
from dispatch_runner import select_runner, completion_marker


class DispatcherTests(unittest.TestCase):
    def test_horoscope_route(self):
        self.assertEqual(select_runner({"title": "LOKPA: horoscope"}), "automation/lokpa_issue_runner.py")

    def test_event_scan_route(self):
        self.assertEqual(select_runner({"title": "LOKPA: event-scan"}), "automation/lokpa_event_scan_runner.py")

    def test_unknown_route_rejected(self):
        with self.assertRaises(ValueError):
            select_runner({"title": "LOKPA: fruitful-dba"})

    def test_completed_horoscope_guard(self):
        self.assertTrue(completion_marker({"title": "LOKPA: horoscope"}, [{"body": "LOKPA_STATUS: COMPLETED"}]))

    def test_completed_event_scan_guard(self):
        self.assertTrue(completion_marker({"title": "LOKPA: event-scan"}, [{"body": "LOKPA_EVENT_SCAN_SUMMARY_V1"}]))

    def test_failed_result_is_not_complete(self):
        self.assertFalse(completion_marker({"title": "LOKPA: horoscope"}, [{"body": "LOKPA_STATUS: FAILED"}]))


if __name__ == "__main__":
    unittest.main()
